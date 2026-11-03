#!/usr/bin/env python3
"""Reproducible check of the Audio view's server API and, optionally, the page.

``ui/server.py`` rewrites ``memory.cfg`` (it forces both PRU clocks to 200 MHz),
and it looks the file up in the project root, not in the working directory. This
tool therefore never starts the repo's own server: it copies the working tree
(without .git, tests, docs and caches) into a temporary directory, starts the
server there on a free port, and stops it afterwards. The repo's ``memory.cfg``
is only read to report that it was left byte-identical.

    python3 source/pdm/tools/ui_check.py ws                 # websocket flow only
    python3 source/pdm/tools/ui_check.py browser --shots out/   # headless Chromium

``ws``: pdm_setup with speech_librivox.wav / chirp_lin_100_7000.wav, several
pdm_run chunks, a disconnect, a reconnect with pdm_history (must equal what the
first connection received and be contiguous), more chunks on the new connection
and a pdm_reset. Needs the ``websockets`` package (in requirements.txt).

``browser``: the same through the real page. A Chromium/Chrome with
``--remote-debugging-port`` is driven over the DevTools protocol: open the Audio
tab, pick the clips, Run, Booth in and out, reload mid-run, Run again, then a
second tab as read-only observer (Run and Reset disabled, Stop stops the run of
the owner tab). Screenshots go to ``--shots``. Needs no extra dependency.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import websockets

REPO = Path(__file__).resolve().parents[3]
LEFT, RIGHT = "speech_librivox.wav", "chirp_lin_100_7000.wav"
IGNORE = shutil.ignore_patterns(".git", ".venv", "tests", "docs", "references",
                                "__pycache__", ".superpowers", "node_modules", ".pytest_cache")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


@contextmanager
def temp_server():
    """Start ui/server.py from a copy of the tree; yield its port."""
    with tempfile.TemporaryDirectory(prefix="pdm-ui-check-") as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(REPO, tree, ignore=IGNORE)
        port = free_port()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.Popen([sys.executable, str(tree / "ui" / "server.py"),
                                 "--port", str(port)], cwd=tree, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 60
            while True:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).close()
                    break
                except OSError:
                    if proc.poll() is not None or time.time() > deadline:
                        raise RuntimeError("the UI server did not start")
                    time.sleep(0.3)
            yield port
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------- websocket


async def _until(ws, pred, timeout=120):
    msgs = []
    end = time.time() + timeout
    while True:
        msgs.append(json.loads(await asyncio.wait_for(ws.recv(), end - time.time())))
        if pred(msgs[-1]):
            return msgs


def _is_reply(m):
    return m["type"] == "pdm_status" or (m["type"] == "error" and m.get("tag") == "pdm")


def _collect(history, msgs):
    for m in msgs:
        if m["type"] != "pdm_pcm":
            continue
        have = history.setdefault(m["ch"], [])
        if m["start"] != len(have):
            raise AssertionError(f"ch{m['ch']}: chunk starts at {m['start']}, have {len(have)}")
        have.extend(m["samples"])


async def _send(ws, **msg):
    await ws.send(json.dumps(msg))
    return await _until(ws, _is_reply)


async def ws_flow(port: int, chunks: int) -> None:
    url = f"ws://127.0.0.1:{port}/ws"
    live = {}
    async with websockets.connect(url, max_size=None) as ws:
        msgs = await _send(ws, action="pdm_setup", clip_left=LEFT, clip_right=RIGHT, swap=False)
        assert msgs[-1]["type"] == "pdm_status", msgs[-1]
        print("setup ok, mode", msgs[-1]["mode"])
        for i in range(chunks):
            t = time.time()
            msgs = await _send(ws, action="pdm_run", run_id=f"a{i}")
            _collect(live, msgs)
            st = msgs[-1]
            assert st["type"] == "pdm_status", st
            print(f"chunk {i}: audio {st['audio_ms']:.1f} ms, {time.time() - t:.2f} s wall, "
                  f"{st['speed_ms_per_s']:.2f} ms audio/s, samples {[len(v) for v in live.values()]}")
    print("disconnected")
    async with websockets.connect(url, max_size=None) as ws:
        rebuilt = {}
        msgs = await _send(ws, action="pdm_history")
        _collect(rebuilt, msgs)
        assert rebuilt == live, "history after reconnect differs from what was streamed"
        print("history after reconnect equals the streamed one:", {k: len(v) for k, v in rebuilt.items()})
        for i in range(chunks, chunks + 2):
            msgs = await _send(ws, action="pdm_run", run_id=f"b{i}")
            _collect(rebuilt, msgs)
        assert all(len(rebuilt[k]) > len(live[k]) for k in live), "Run did not continue"
        print("run continued, samples", {k: len(v) for k, v in rebuilt.items()})
    async with websockets.connect(url, max_size=None) as ws:
        again = {}
        _collect(again, await _send(ws, action="pdm_history"))
        assert again == rebuilt, "history is not the concatenation of what was streamed"
        msgs = await _send(ws, action="pdm_reset")
        assert msgs[-1]["type"] == "pdm_status" and msgs[-1]["audio_ms"] == 0, msgs[-1]
        print("reset ok; history contiguous")


# ------------------------------------------------------------------ browser


class Page:
    """One DevTools page target."""

    def __init__(self, ws, shots):
        self.ws, self.shots, self.n = ws, shots, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    async def shot(self, name):
        data = (await self.call("Page.captureScreenshot", format="png"))["data"]
        if self.shots:
            (self.shots / f"{name}.png").write_bytes(base64.b64decode(data))

    async def state(self):
        return json.loads(await self.js("""JSON.stringify({
          runDisabled: document.getElementById('audio-run').disabled,
          stopDisabled: document.getElementById('audio-stop').disabled,
          resetDisabled: document.getElementById('audio-reset').disabled,
          progress: document.getElementById('audio-progress-text').textContent,
          speed: document.getElementById('audio-speed').textContent,
          observer: document.getElementById('audio-observer').hidden ? ''
                    : document.getElementById('audio-observer').textContent,
          error: document.getElementById('audio-error').textContent})"""))

    async def open_audio(self, url, theme="light"):
        await self.call("Page.enable")
        await self.call("Runtime.enable")
        await self.call("Emulation.setDeviceMetricsOverride", width=1440, height=900,
                        deviceScaleFactor=1, mobile=False)
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(3)
        await self.js(f"localStorage.setItem('pru-workspace-theme', '{theme}')")
        await self.call("Page.reload")
        await asyncio.sleep(3)
        await self.js("document.getElementById('audio-nav-button').click()")
        await asyncio.sleep(2)


def progress_ms(state) -> int:
    return int(state["progress"].split(" ms")[0])


async def browser_flow(port: int, chrome: str, shots: Path | None, seconds: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    debug = free_port()
    with tempfile.TemporaryDirectory(prefix="pdm-ui-chrome-") as profile:
        proc = subprocess.Popen([chrome, "--headless=new", f"--remote-debugging-port={debug}",
                                 f"--user-data-dir={profile}", "--no-first-run", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    json.load(urllib.request.urlopen(f"http://127.0.0.1:{debug}/json/version"))
                    break
                except OSError:
                    await asyncio.sleep(0.2)
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{debug}/json"))
            first = next(t for t in tabs if t["type"] == "page")
            if shots:
                shots.mkdir(parents=True, exist_ok=True)
            async with websockets.connect(first["webSocketDebuggerUrl"], max_size=None) as ws_a:
                a = Page(ws_a, shots)
                await a.open_audio(url)
                await a.shot("01-idle")
                await a.js(f"""(() => {{
                  for (const [id, value] of [['audio-left-clip', '{LEFT}'], ['audio-right-clip', '{RIGHT}']]) {{
                    const s = document.getElementById(id);
                    s.value = value; s.dispatchEvent(new Event('change', {{bubbles: true}}));
                  }}
                  document.getElementById('audio-run').click(); }})()""")
                await asyncio.sleep(seconds)
                running = await a.state()
                print("running:", running)
                assert running["runDisabled"] and not running["error"], running
                await a.shot("02-running")
                await a.js("document.getElementById('audio-booth').click()")
                await asyncio.sleep(2)
                inbooth = await a.js("document.getElementById('audio-view').classList.contains('audio-booth')")
                await a.shot("03-booth")
                await a.js("document.getElementById('audio-booth').click()")
                await asyncio.sleep(1)
                outbooth = not await a.js("document.getElementById('audio-view').classList.contains('audio-booth')")
                print("booth in/out:", inbooth, outbooth)
                assert inbooth and outbooth

                # Second tab while the first one runs: read-only observer.
                new = urllib.request.Request(f"http://127.0.0.1:{debug}/json/new?about:blank", method="PUT")
                second = json.load(urllib.request.urlopen(new))
                async with websockets.connect(second["webSocketDebuggerUrl"], max_size=None) as ws_b:
                    b = Page(ws_b, shots)
                    await b.open_audio(url)
                    await asyncio.sleep(6)
                    observer = await b.state()
                    print("observer tab:", observer)
                    assert observer["runDisabled"] and observer["resetDisabled"], observer
                    assert "another tab" in observer["observer"], observer
                    await b.shot("04-observer")
                    stop_enabled = not observer["stopDisabled"]
                    before = progress_ms(await a.state())
                    if stop_enabled:
                        await b.js("document.getElementById('audio-stop').click()")
                    await asyncio.sleep(8)
                    after = await a.state()
                    print(f"observer Stop enabled: {stop_enabled}; owner tab {before} -> "
                          f"{progress_ms(after)} ms, Run disabled: {after['runDisabled']}")
                    await b.call("Page.close")

                await a.call("Page.reload")
                await asyncio.sleep(3)
                await a.js("document.getElementById('audio-nav-button').click()")
                await asyncio.sleep(3)
                reloaded = await a.state()
                print("after reload:", reloaded)
                await a.shot("05-after-reload")
                if reloaded["runDisabled"]:
                    await a.js("document.getElementById('audio-stop').click()")
                    await asyncio.sleep(2)
                before = progress_ms(await a.state())
                await a.js("document.getElementById('audio-run').click()")
                await asyncio.sleep(seconds)
                after = progress_ms(await a.state())
                print(f"Run after reload: {before} -> {after} ms")
                assert after > before >= 1
                await a.shot("06-run-continues")
                await a.js("document.getElementById('audio-stop').click()")
        finally:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    w = sub.add_parser("ws", help="websocket flow only")
    w.add_argument("--chunks", type=int, default=4, help="pdm_run chunks before the disconnect")
    b = sub.add_parser("browser", help="drive headless Chromium over the DevTools protocol")
    b.add_argument("--chrome", default=shutil.which("chromium") or shutil.which("google-chrome")
                   or "/snap/bin/chromium")
    b.add_argument("--shots", type=Path, help="directory for screenshots")
    b.add_argument("--seconds", type=int, default=15, help="how long each Run is left going")
    args = parser.parse_args(argv)

    cfg = REPO / "memory.cfg"
    before = md5(cfg) if cfg.exists() else None
    with temp_server() as port:
        if args.mode == "ws":
            asyncio.run(ws_flow(port, args.chunks))
        else:
            asyncio.run(browser_flow(port, args.chrome, args.shots, args.seconds))
    after = md5(cfg) if cfg.exists() else None
    print("repo memory.cfg unchanged:", before == after)
    return 0 if before == after else 1


if __name__ == "__main__":
    sys.exit(main())
