"""FastAPI + WebSocket backend for the PRU Simulator Dashboard."""

import asyncio
import copy
import json
import math
import os
import pathlib
import re
import sys

# Ensure project root is on path so simulator can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from simulator import Simulator
from mcp_server.server import PRUSimulatorMCP
from core.branch import LoopState
from perif.gpcfg import MUX_SD
from pru_io import foc_control_abi
from pru_io.ssi_encoder_model import SSIEncoderModel
from xfr.xfr_bus import SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, IPC_SPAD

app = FastAPI(title="PRU Simulator Dashboard")

MAX_BREAKPOINTS = 64

# Use project root for config resolution
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(PROJECT_ROOT, "memory.cfg")
SOURCE_DIR = pathlib.Path(PROJECT_ROOT) / "source"


def _safe_source_subpath(path: str) -> pathlib.Path | None:
    """Return resolved path within SOURCE_DIR, allowing subdirectories. Returns None if unsafe."""
    if not path or len(path) > 300:
        return None
    if '\\' in path:
        return None
    try:
        candidate = (SOURCE_DIR / path).resolve()
        candidate.relative_to(SOURCE_DIR.resolve())  # raises ValueError if outside SOURCE_DIR
    except (ValueError, OSError):
        return None
    if candidate.suffix.lower() not in ('.asm', '.s', '.inc', '.json'):
        return None
    return candidate
sim = Simulator(config_path=config_path)
_device_api = PRUSimulatorMCP(config_path=config_path, simulator=sim)
_config_lock = asyncio.Lock()


def _device_api_for_current_sim() -> PRUSimulatorMCP:
    global _device_api
    if _device_api.sim is not sim:
        _device_api = PRUSimulatorMCP(config_path=config_path, simulator=sim)
    return _device_api

# ---- Step history (for step-back) ----------------------------------------
_MAX_HISTORY = 500
_history: dict[str, list] = {"pru0": [], "rtu0": [], "pru1": [], "rtu1": []}
_history_order: list[tuple[str, dict]] = []


def _clear_history() -> None:
    for history in _history.values():
        history.clear()
    _history_order.clear()


def _record_step(core: str) -> None:
    snapshot = _snapshot(core)
    _history[core].append(snapshot)
    _history_order.append((core, snapshot))
    if len(_history_order) > _MAX_HISTORY:
        oldest_core, oldest_snapshot = _history_order.pop(0)
        _history[oldest_core][:] = [
            item for item in _history[oldest_core] if item is not oldest_snapshot
        ]


def _step_back(core: str) -> bool:
    for index in range(len(_history_order) - 1, -1, -1):
        event_core, snapshot = _history_order[index]
        if event_core != core:
            continue
        _restore(core, snapshot)
        discarded = {id(item) for _, item in _history_order[index:]}
        del _history_order[index:]
        for history in _history.values():
            history[:] = [item for item in history if id(item) not in discarded]
        return True
    return False


def _snapshot_core(c) -> dict:
    """Capture one core's mutable state for the shared simulator history."""
    ls = c.loop_state
    sd = c.io_port.sd_filter
    perif = c.io_port.perif
    i2c = c.io_port.i2c_device
    return {
        "pc": c.pc,
        "halted": c.halted,
        "fault": dict(c.fault) if c.fault is not None else None,
        "loop_state": {"count": ls.count, "start_address": ls.start_address,
                       "end_address": ls.end_address} if ls else None,
        "regs": list(c.registers.regs),
        "carry": c.registers.carry,
        "gpo": c.io_port.gpo,
        "gpi": c.io_port.gpi,
        "loopback_mask": c.io_port.loopback_mask,
        "gpo_drive_mask": c.io_port.gpo_drive_mask,
        "cycles": c.counters.cycles,
        "stall_cycles": c.counters.stall_cycles,
        "instruction_count": c.counters.instruction_count,
        "sd": sd.snapshot() if sd is not None else None,
        "perif": perif.snapshot() if perif is not None else None,
        "i2c_device": i2c,
        "i2c": i2c.snapshot() if i2c is not None else None,
        "uart_generator": c.io_port.uart_generator,
        "accelerators": {
            device_id: accelerator.snapshot()
            for device_id, accelerator in c.accelerators.items()
            if callable(getattr(accelerator, "snapshot", None))
        },
        "unsupported_xfr": copy.deepcopy(c.unsupported_xfr),
    }


def _snapshot(core: str) -> dict:
    """Capture every core plus shared memory and devices before a step."""
    return {
        "cores": {name: _snapshot_core(c) for name, c in sim.cores.items()},
        "device_bus": sim.device_bus.snapshot(),
        "xfr": sim.xfr.snapshot(),
        "gpcfg": sim._gpcfg.snapshot(),
        "loopback": sim._loopback.snapshot(),
        "mem": [bytes(r._data) for r in sim.memory.regions],
        "iep": sim.iep.snapshot(),
    }


def _restore_core(c, snap: dict) -> None:
    c.pc = snap["pc"]
    c.halted = snap["halted"]
    c.fault = dict(snap["fault"]) if snap["fault"] is not None else None
    c.loop_state = LoopState(**snap["loop_state"]) if snap["loop_state"] else None
    c.registers.regs = list(snap["regs"])
    c.registers.carry = snap["carry"]
    c.io_port.gpo = snap["gpo"]
    c.io_port.gpi = snap["gpi"]
    c.io_port.loopback_mask = snap.get("loopback_mask", c.io_port.loopback_mask)
    c.io_port.gpo_drive_mask = snap.get("gpo_drive_mask", c.io_port.gpo_drive_mask)
    c.counters.cycles = snap["cycles"]
    c.counters.stall_cycles = snap["stall_cycles"]
    c.counters.instruction_count = snap["instruction_count"]
    if snap.get("sd") is not None and c.io_port.sd_filter is not None:
        c.io_port.sd_filter.restore(snap["sd"])
    if snap.get("perif") is not None and c.io_port.perif is not None:
        c.io_port.perif.restore(snap["perif"])
    if "i2c_device" in snap:
        c.io_port.i2c_device = snap["i2c_device"]
    if snap.get("i2c") is not None and c.io_port.i2c_device is not None:
        c.io_port.i2c_device.restore(snap["i2c"])
    c.io_port.uart_generator = snap.get("uart_generator")
    for device_id, accelerator_snap in snap.get("accelerators", {}).items():
        accelerator = c.accelerators.get(device_id)
        if accelerator is not None:
            accelerator.restore(accelerator_snap)
    c.unsupported_xfr = copy.deepcopy(snap.get("unsupported_xfr", {}))


def _restore(core: str, snap: dict) -> None:
    """Restore every core and shared device to the same saved point in time."""
    for core_name, core_snap in snap.get("cores", {core: snap}).items():
        if core_name in sim.cores:
            _restore_core(sim.cores[core_name], core_snap)
    for i, region_data in enumerate(snap["mem"]):
        if i < len(sim.memory.regions):
            sim.memory.regions[i]._data[:] = region_data
    if snap.get("iep") is not None:
        sim.iep.restore(snap["iep"])
    if snap.get("device_bus") is not None:
        sim.device_bus.restore(snap["device_bus"])
    if snap.get("xfr") is not None:
        sim.xfr.restore(snap["xfr"])
    if snap.get("gpcfg") is not None:
        sim._gpcfg.restore(snap["gpcfg"])
    if snap.get("loopback") is not None:
        sim._loopback.restore(snap["loopback"])
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/regions")
async def get_regions():
    return [{"name": r.name, "base": r.base_addr, "size": r.size} for r in sim.memory.regions]


@app.get("/config")
async def get_config():
    try:
        with open(config_path, "r") as f:
            return PlainTextResponse(f.read())
    except FileNotFoundError:
        return PlainTextResponse("")


@app.get("/source")
async def list_source():
    SOURCE_DIR.mkdir(exist_ok=True)
    files = sorted(
        p.name for p in SOURCE_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ('.asm', '.s', '.inc')
    )
    lib_dir = SOURCE_DIR / "lib"
    lib = sorted(
        p.name for p in lib_dir.iterdir()
        if p.is_file() and p.suffix.lower() in ('.asm', '.s', '.inc')
    ) if lib_dir.exists() else []
    projects = {}
    for subdir in sorted(SOURCE_DIR.iterdir()):
        if subdir.is_dir() and subdir.name != "lib":
            proj_files = sorted(
                p.name for p in subdir.iterdir()
                if p.is_file() and p.suffix.lower() in ('.asm', '.s', '.inc', '.json')
            )
            if any(f.endswith(('.asm', '.s')) for f in proj_files):
                projects[subdir.name] = proj_files
    return {"files": files, "projects": projects, "lib": lib}


@app.get("/source/{path:path}")
async def get_source_file(path: str):
    resolved = _safe_source_subpath(path)
    if resolved is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not resolved.exists():
        return JSONResponse({"error": "Not found"}, status_code=404)
    return PlainTextResponse(resolved.read_text(encoding="utf-8"))


@app.put("/source/{path:path}")
async def put_source_file(path: str, request: Request):
    resolved = _safe_source_subpath(path)
    if resolved is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    text = (await request.body()).decode("utf-8")
    resolved.write_text(text, encoding="utf-8")
    return {"ok": True}


@app.put("/config")
async def put_config(request: Request):
    global sim
    text = (await request.body()).decode("utf-8")
    async with _config_lock:
        try:
            with open(config_path, "w") as f:
                f.write(text)
            sim = Simulator(config_path=config_path)
            _clear_history()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)


ALLOWED_CLOCK_MHZ = {200, 225, 250, 300, 333}


def _set_ini_value(text: str, section: str, key: str, value: str) -> str:
    """Set key = value inside [section] of an ini-format string, preserving
    all other formatting/comments. Replaces the key if present, otherwise
    inserts it as the last line of the section. Section must already exist."""
    section_re = re.compile(
        r"(\[" + re.escape(section) + r"\]\n)(.*?)(?=\n\[|\Z)", re.DOTALL
    )
    match = section_re.search(text)
    if match is None:
        raise ValueError(f"[{section}] section not found in config")
    header, body = match.group(1), match.group(2)

    key_re = re.compile(r"^" + re.escape(key) + r"\s*=.*$", re.MULTILINE)
    if key_re.search(body):
        new_body = key_re.sub(f"{key} = {value}", body)
    else:
        if body and not body.endswith("\n"):
            body += "\n"
        new_body = body + f"{key} = {value}\n"

    return text[:match.start()] + header + new_body + text[match.end():]


@app.get("/config/clock_speed")
async def get_clock_speed():
    return {"mhz": sim._pru_clock_mhz}


@app.put("/config/clock_speed")
async def put_clock_speed(request: Request):
    global sim
    body = await request.json()
    mhz = body.get("mhz")
    if mhz not in ALLOWED_CLOCK_MHZ:
        return JSONResponse(
            {"error": f"mhz must be one of {sorted(ALLOWED_CLOCK_MHZ)}"},
            status_code=400,
        )
    async with _config_lock:
        try:
            with open(config_path, "r") as f:
                text = f.read()
            text = _set_ini_value(text, "device", "pru_clock_mhz", str(mhz))
            text = _set_ini_value(text, "device", "pru1_clock_mhz", str(mhz))
            with open(config_path, "w") as f:
                f.write(text)
            sim = Simulator(config_path=config_path)
            _clear_history()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            core = msg.get("core", "pru0")

            if action == "load":
                _clear_history()
                filename = msg.get("filename")
                include_paths = None
                if filename and isinstance(filename, str) and '\\' not in filename and len(filename) <= 300:
                    try:
                        candidate_dir = (SOURCE_DIR / pathlib.Path(filename).parent).resolve()
                        candidate_dir.relative_to(SOURCE_DIR.resolve())
                        lib_dir = SOURCE_DIR / "lib"
                        include_paths = [str(candidate_dir)]
                        if lib_dir.exists():
                            include_paths.append(str(lib_dir))
                    except (ValueError, OSError):
                        pass  # Unsafe path — ignore filename, use default include_paths
                errors = sim.load(core, msg["source"], include_paths)
                if errors:
                    await websocket.send_json({"type": "error", "errors": errors})
                await _send_state(websocket, core)
            elif action == "load_elf":
                _clear_history()
                # ELF binary sent as base64-encoded string
                elf_b64 = msg.get("data", "")
                try:
                    elf_data = base64.b64decode(elf_b64)
                except Exception:
                    await websocket.send_json({"type": "error", "errors": ["Invalid base64 data"]})
                    continue
                errors = sim.load_elf(core, elf_data)
                if errors:
                    await websocket.send_json({"type": "error", "errors": errors})
                await _send_state(websocket, core)
            elif action == "step":
                _record_step(core)
                at_breakpoint = False
                try:
                    sim.step(core, msg.get("count", 1))
                    pru = sim.cores[core]
                    if pru.pc in pru.breakpoints:
                        at_breakpoint = True
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                await _send_state(websocket, core, at_breakpoint=at_breakpoint)
                # Auto-refresh both memory panels if client has set addresses
                for tag, addr_attr, len_attr in [
                    ("mem1", "_mem_addr",  "_mem_len"),
                    ("mem2", "_mem_addr2", "_mem_len2"),
                ]:
                    if hasattr(websocket, addr_attr):
                        try:
                            data = sim.memory_read(
                                getattr(websocket, addr_attr),
                                getattr(websocket, len_attr),
                            )
                            await websocket.send_json({
                                "type": "memory",
                                "tag": tag,
                                "addr": getattr(websocket, addr_attr),
                                "length": getattr(websocket, len_attr),
                                "data": list(data),
                            })
                        except ValueError:
                            pass
            elif action == "reset":
                _clear_history()
                sim.reset(core)
                await _send_state(websocket, core)
            elif action == "hard_reset":
                _clear_history()
                sim.hard_reset()
                await _send_state(websocket, core)
            elif action == "set_input":
                sim.set_input(core, msg["pin"], bool(msg["value"]))
                await _send_state(websocket, core)
            elif action == "get_state":
                await _send_state(websocket, core)
            elif action == "read_memory":
                addr = int(msg.get("addr", 0))
                length = int(msg.get("length", 128))
                tag = msg.get("tag", "mem1")
                if tag == "mem2":
                    websocket._mem_addr2 = addr
                    websocket._mem_len2 = length
                elif tag == "mem1":
                    websocket._mem_addr = addr
                    websocket._mem_len = length
                try:
                    data = sim.memory_read(addr, length)
                    await websocket.send_json({
                        "type": "memory",
                        "tag": tag,
                        "addr": addr,
                        "length": length,
                        "data": list(data),
                    })
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)], "tag": tag})
            elif action == "write_memory":
                addr = int(msg.get("addr", 0))
                data_bytes = bytes(msg.get("data", []))
                try:
                    sim.memory.write(addr, data_bytes)
                    await websocket.send_json({"type": "memory_written", "addr": addr, "length": len(data_bytes)})
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
            elif action == "set_register":
                idx = int(msg.get("index", 0)) & 0x1F  # clamp to 0-31
                val = int(msg.get("value", 0)) & 0xFFFFFFFF
                c = sim.cores[core]
                c.registers.write_full(idx, val)
                if idx == 30:
                    c.io_port.write_r30(val)
                elif idx == 31:
                    c.io_port.set_gpi_word(val)
                await _send_state(websocket, core)
            elif action == "step_back":
                if _step_back(core):
                    for state_core in sim.cores:
                        await _send_state(websocket, state_core)
                else:
                    await _send_state(websocket, core)
            elif action == "toggle_breakpoint":
                addr = int(msg.get("addr", 0))
                pru = sim.cores[core]
                if addr in pru.breakpoints:
                    pru.breakpoints.discard(addr)
                elif len(pru.breakpoints) < MAX_BREAKPOINTS:
                    pru.breakpoints.add(addr)
                await _send_state(websocket, core)
            elif action == "clear_breakpoints":
                sim.cores[core].breakpoints.clear()
                await _send_state(websocket, core)
            elif action == "fill_memory":
                addr      = int(msg.get("addr", 0))
                length    = int(msg.get("length", 256))
                mode      = msg.get("mode", "pattern")
                fmt       = msg.get("fmt", "byte")
                elem_size = {"byte": 1, "16b": 2, "32b": 4}.get(fmt, 1)
                n_elems   = length // elem_size
                mask      = (1 << (elem_size * 8)) - 1
                try:
                    if mode == "pattern":
                        pval  = int(msg.get("value", "0"), 0) & mask
                        chunk = pval.to_bytes(elem_size, "little")
                        data  = (chunk * (n_elems + 1))[:length]
                    elif mode == "sequence":
                        start = int(msg.get("start", "0"), 0)
                        step  = int(msg.get("step",  "1"), 0)
                        buf   = bytearray()
                        for i in range(n_elems):
                            buf += ((start + i * step) & mask).to_bytes(elem_size, "little")
                        data = bytes(buf[:length])
                    elif mode == "waveform":
                        wtype  = msg.get("wtype", "sine")
                        amp    = int(msg.get("amplitude", "0x7FFF"), 0)
                        cycles = float(msg.get("cycles", 1))
                        dc     = int(msg.get("dc_offset", "0"), 0)
                        signed = bool(msg.get("signed", True))
                        bits   = elem_size * 8
                        buf    = bytearray()
                        for i in range(n_elems):
                            t = (i / max(n_elems, 1)) * cycles * 2 * math.pi
                            if   wtype == "sine":     y = math.sin(t)
                            elif wtype == "square":   y = 1.0 if math.sin(t) >= 0 else -1.0
                            elif wtype == "triangle": y = 2/math.pi * math.asin(math.sin(t))
                            else:                     y = 2*((t/(2*math.pi)) % 1.0) - 1.0
                            if signed:
                                # y in [-1,1] → centered around dc
                                raw = int(round(dc + amp * y))
                                raw = max(-(1<<(bits-1)), min((1<<(bits-1))-1, raw))
                                if raw < 0: raw += 1 << bits
                            else:
                                # y in [-1,1] → scaled to [0,1] so waveform spans [dc, dc+amp]
                                raw = int(round(dc + amp * (y + 1) / 2))
                                raw = max(0, min(mask, raw))
                            buf += raw.to_bytes(elem_size, "little")
                        data = bytes(buf[:length])
                    else:
                        data = bytes(length)
                    sim.memory.write(addr, data)
                    await websocket.send_json({"type": "memory_written", "addr": addr, "length": len(data)})
                    for tag, addr_attr, len_attr in [
                        ("mem1", "_mem_addr",  "_mem_len"),
                        ("mem2", "_mem_addr2", "_mem_len2"),
                    ]:
                        if hasattr(websocket, addr_attr):
                            try:
                                pdata = sim.memory_read(
                                    getattr(websocket, addr_attr),
                                    getattr(websocket, len_attr),
                                )
                                await websocket.send_json({
                                    "type": "memory", "tag": tag,
                                    "addr": getattr(websocket, addr_attr),
                                    "length": getattr(websocket, len_attr),
                                    "data": list(pdata),
                                })
                            except ValueError:
                                pass
                except Exception as e:
                    await websocket.send_json({"type": "error", "errors": [str(e)]})
            elif action == "set_xfr_shift":
                sim.xfr.xfr_shift_en = bool(msg.get("enabled", False))
                await _send_state(websocket, core)
            elif action == "run":
                max_steps = int(msg.get("max_steps", 1000))
                capture = bool(msg.get("capture", False))
                pru = sim.cores[core]
                at_breakpoint = False
                samples = []
                try:
                    steps = 0
                    while steps < max_steps and not pru.halted and pru.pc < len(pru.instructions):
                        pru.step()
                        steps += 1
                        if capture and _capture_due(pru, steps):
                            samples.append(_capture_sample(pru))
                        if pru.pc in pru.breakpoints:
                            at_breakpoint = True
                            break
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                if samples:
                    await _send_capture(websocket, core, samples)
                await _send_state(websocket, core, at_breakpoint=at_breakpoint,
                                  captured=capture)
            elif action == "run_multicore":
                max_steps = int(msg.get("max_steps", 1000))
                capture = bool(msg.get("capture", False))
                partner = msg.get("partner", "pru1")
                lead_pru = sim.cores[core]
                partner_pru = sim.cores[partner]
                lead_bp = partner_bp = False
                samples = []
                try:
                    steps = 0
                    while (steps < max_steps and not lead_pru.halted
                           and lead_pru.pc < len(lead_pru.instructions)):
                        sim.step_paced(core, partner, 1)
                        steps += 1
                        if capture and _capture_due(lead_pru, steps):
                            samples.append(_capture_sample(lead_pru))
                        if lead_pru.pc in lead_pru.breakpoints:
                            lead_bp = True
                            break
                        if partner_pru.pc in partner_pru.breakpoints:
                            partner_bp = True
                            break
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                if samples:
                    await _send_capture(websocket, core, samples)
                await _send_state(websocket, core, at_breakpoint=lead_bp,
                                  captured=capture)
                await _send_state(websocket, partner, at_breakpoint=partner_bp,
                                  captured=capture)
            elif action == "set_sd_modulator":
                ch = int(msg.get("channel", 0))
                params = msg.get("params", {})
                try:
                    sim.set_sd_modulator(core, ch, **params)
                except (TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "device",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action == "write_sd_register":
                addr = int(msg.get("addr", 0))
                value = int(msg.get("value", 0))
                sim.memory.write(addr, value.to_bytes(4, 'little'))
                await _send_state(websocket, core)
            elif action == "set_loopback":
                sim.set_loopback(core, int(msg["group"]), bool(msg["enabled"]))
            elif action == "gpcfg_write":
                sim.gpcfg_write(core, int(msg.get("mux_sel", 0)))
                await _send_state(websocket, core)
            elif action == "write_perif_register":
                addr = int(msg.get("addr", 0))
                value = int(msg.get("value", 0))
                sim.write_perif_register(core, addr, value)
                await _send_state(websocket, core)
            elif action == "perif_loopback":
                sim.perif_loopback(
                    int(msg.get("channel", 0)),
                    bool(msg.get("enabled", False)),
                    latency_ns=float(msg.get("latency_ns", 0.0)),
                    jitter_ns=float(msg.get("jitter_ns", 0.0)),
                    drift_ppm=float(msg.get("drift_ppm", 0.0)),
                )
                await websocket.send_text(json.dumps({
                    "type": "perif_ok",
                    "channel": int(msg.get("channel", 0)),
                    "enabled": bool(msg.get("enabled", False)),
                }))
                await _send_state(websocket, core)
            elif action == "i2c_attach":
                sim.i2c_attach(core, bool(msg.get("enabled", False)), int(msg.get("address", 0x23)))
                await _send_state(websocket, core)
            elif action == "device_attach":
                try:
                    _device_api_for_current_sim().pru_device_attach(
                        profile=msg.get("profile", ""), core=core,
                        config=msg.get("config"),
                    )
                    _clear_history()
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "device",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action == "device_detach":
                try:
                    name = msg.get("name", "")
                    device = sim.device_bus.get_device(name)
                    attached_core = sim.device_bus.core_for_device(device)
                    if attached_core != core:
                        raise ValueError(f"device {name!r} is attached to {attached_core}")
                    _device_api_for_current_sim().pru_device_detach(name)
                    _clear_history()
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "device",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action == "ssi_set_position":
                try:
                    name = msg.get("name", "")
                    device = sim.device_bus.get_device(name)
                    attached_core = sim.device_bus.core_for_device(device)
                    if attached_core != core:
                        raise ValueError(f"device {name!r} is attached to {attached_core}")
                    if not isinstance(device, SSIEncoderModel):
                        raise ValueError(f"device {name!r} is not an SSI encoder")
                    device.set_position(msg.get("position"))
                    _clear_history()
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "device",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action == "foc_apply":
                try:
                    if core != "pru0":
                        raise ValueError("FOC controls are available on PRU0 only")
                    motor = next(
                        (device for device in sim.device_state()["devices"]
                         if device.get("model") == "three_phase_rl"
                         and device.get("core") == "pru0"),
                        None,
                    )
                    if motor is None:
                        raise ValueError("Attach a FOC motor on PRU0 before applying controls")
                    config = msg.get("config")
                    if not isinstance(config, dict):
                        raise ValueError("FOC config must be an object")
                    config_bytes = foc_control_abi.pack_config(**config)
                    routes = msg.get("routes")
                    if (not isinstance(routes, list) or len(routes) != 2
                            or any(isinstance(pin, bool) or not isinstance(pin, int)
                                   or not -1 <= pin < 20 for pin in routes)):
                        raise ValueError("FOC SD routes must contain two pins from -1 to 19")
                    sd = sim.cores["pru0"].io_port.sd_filter
                    sd.route_inputs([None if pin == -1 else pin for pin in routes]
                                    + sd.input_routes[2:])
                    sim.memory.write(foc_control_abi.CONTROL_ADDRESS, config_bytes)
                    _clear_history()
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "device",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action == "uart_inject":
                pin = int(msg.get("pin", 0))
                payload = msg.get("payload", [])
                baudrate = int(msg.get("baudrate", 4_000_000))
                frames = int(msg.get("frames", 1))
                pru = sim._get_core(core)
                trigger_cycle = pru.counters.cycles + 5
                sim.uart_inject(
                    core=core,
                    pin=pin,
                    payload=payload,
                    baudrate=baudrate,
                    trigger_cycle=trigger_cycle,
                    frames=frames,
                )
                await websocket.send_text(json.dumps({
                    "type": "uart_inject_ok",
                    "trigger_cycle": trigger_cycle,
                    "payload_len": len(payload),
                    "frames": frames,
                }))
    except WebSocketDisconnect:
        return
    except Exception as e:
        import traceback
        traceback.print_exc()


def _read_mac(core) -> dict:
    """Return MAC accelerator status for the given PRUCore."""
    acc = core.accelerators.get(0)
    if acc is None:
        return {"mode": False, "acc_carry": False}
    return {"mode": acc.mac_mode, "acc_carry": acc.acc_carry}


def _read_spad(sim_obj) -> dict:
    """Return scratchpad contents as hex words per bank.

    Banks 0/1/2 hold R0–R29 (30 words); IPC holds R2–R9 (8 words).
    """
    result = {}
    for device_id, key, count in [
        (SPAD_BANK0, "bank0", 30),
        (SPAD_BANK1, "bank1", 30),
        (SPAD_BANK2, "bank2", 30),
        (IPC_SPAD,   "ipc",   8),
    ]:
        raw = sim_obj.xfr._pads[device_id].data
        result[key] = [
            f"0x{int.from_bytes(raw[i*4:i*4+4], 'little'):08X}"
            for i in range(count)
        ]
    return result


def _io_mode(core, sd_data=None, perif_data=None, mux_sel=None) -> str:
    """Which IO view owns the pads: "perif", "sd" or "gpio".

    The SD view switches on the GPCFG mux select (same as Peripheral mode) so
    the IO window shows Sigma-Delta as soon as the mode is configured, not only
    once firmware asserts sd_en (R30 bit 25).
    """
    if sd_data is None:
        sd_data = sim.sd_state(core)
    if perif_data is None:
        perif_data = sim.perif_state(core)
    if mux_sel is None:
        mux_sel = sim.gpcfg_state(core)["mux_sel"]
    if bool(perif_data and perif_data.get("enabled")):
        return "perif"
    if mux_sel == MUX_SD or bool(sd_data and sd_data.get("sd_en")):
        return "sd"
    return "gpio"


# One Signal Graph sample per this many instructions, outside peripheral mode.
# GP-mode traces are firmware-paced — a bit-banged 115200-baud UART bit is ~1736
# core cycles — so sampling every instruction would buy nothing and shrink the
# window's time span 100x, which is what the UART decoder's auto-detected bit
# period needs. In peripheral mode the signals are hardware-paced instead (a
# channel-0 bit at the N=2 divider is 2 core cycles), so that mode samples every
# instruction; see the stride decision in the run loop.
CAPTURE_STRIDE_GP = 100


def _capture_due(c, steps: int) -> bool:
    """Whether to take a graph sample after instruction `steps` of this chunk.

    Decided per instruction rather than once per chunk because firmware enables
    peripheral mode from inside the run — `perif_duty_cycle_sweep.asm` writes
    GPCFG about 11 instructions in and is only ~160 instructions long, so a
    stride fixed before the loop would decimate away the whole transmission.
    """
    perif = c.io_port.perif
    if perif is not None and perif.enabled:
        return True
    return steps % CAPTURE_STRIDE_GP == 0


def _capture_sample(c) -> list[int]:
    """One Signal Graph sample, taken inside the run loop.

    Run executes up to `max_steps` instructions per websocket round-trip, so a
    graph fed only by the state push at the end of that loop samples once per
    chunk. For perif signals that is far too coarse and the waveform aliases
    away entirely — the reported symptom being a Run capture of
    `perif_duty_cycle_sweep.asm` that draws nothing while SIM (one instruction
    per push) traces it fine.

    Packed into ints (bitmask per lane group) to keep the batch small.
    """
    perif = c.io_port.perif
    gpi = c.io_port.get_gpi_pins()
    gpi_bits = 0
    for i, v in enumerate(gpi[:20]):
        if v:
            gpi_bits |= 1 << i
    out_bits = oe_bits = clk_bits = 0
    if perif is not None:
        for i, ch in enumerate(perif.channels[:3]):
            if ch.tx_line_value():
                out_bits |= 1 << i
            if ch.tx_out_en:
                oe_bits |= 1 << i
            if ch.tx_clk_pin:
                clk_bits |= 1 << i
    return [c.counters.instruction_count, c.registers.read_full(30) & 0xFFFFF,
            gpi_bits, out_bits, oe_bits, clk_bits]


async def _send_capture(ws, core, samples):
    """Ship a run loop's per-instruction Signal Graph samples in one message."""
    await ws.send_json({
        "type": "capture",
        "core": core,
        "mode": _io_mode(core),
        "samples": samples,
    })


async def _send_state(ws, core, at_breakpoint=False, captured=False):
    c = sim.cores[core]
    # R31 display reflects live GPI state (registers.regs[31] is never updated by set_gpi_pin)
    regs = list(c.registers.regs)
    regs[31] = c.io_port.read_r31()
    # GPO pins derived from R30 register value (avoids drift after reset)
    r30 = c.registers.read_full(30)
    gpo_pins = [(r30 >> i) & 1 for i in range(20)]
    sd_data = sim.sd_state(core)
    perif_data = sim.perif_state(core)
    i2c_data = sim.i2c_state(core)
    mux_sel = sim.gpcfg_state(core)["mux_sel"]
    mode = _io_mode(core, sd_data=sd_data, perif_data=perif_data, mux_sel=mux_sel)
    io_section = {
        "mode": mode,
        "mux_sel": mux_sel,
        "gpo_pins": gpo_pins,
        "gpi_pins": c.io_port.get_gpi_pins(),
        "gpo_drive_mask": c.io_port.gpo_drive_mask,
    }
    if sim.device_bus.active:
        device_state = sim.device_state()
        io_section["device_bus"] = device_state
        foc_device = next(
            (device for device in device_state["devices"]
             if device.get("model") == "three_phase_rl"
             and device.get("core") == "pru0"),
            None,
        )
        if core == "pru0" and foc_device is not None:
            foc_config = foc_control_abi.unpack_config(
                sim.memory_read(foc_control_abi.CONTROL_ADDRESS,
                                foc_control_abi.CONFIG_SIZE)
            )
            if foc_config["abi_version"] != foc_control_abi.ABI_VERSION:
                foc_config = foc_control_abi.unpack_config(
                    foc_control_abi.pack_config()
                )
            io_section["foc_config"] = foc_config
    if sd_data is not None:
        io_section["sd"] = sd_data
    if perif_data is not None:
        io_section["perif"] = perif_data
        io_section["loopback"] = sim.loopback_state()
    if i2c_data is not None:
        io_section["i2c"] = i2c_data
    state = {
        "type": "state",
        "core": core,
        "pc": c.pc,
        "halted": c.halted,
        "fault": c.fault,
        "core_faults": {name: dict(pru.fault) for name, pru in sim.cores.items()
                        if pru.fault is not None},
        "at_breakpoint": at_breakpoint,
        # True when a "capture" message already carried this chunk's graph
        # samples, so the client must not sample this state push as well.
        "captured": captured,
        "breakpoints": sorted(c.breakpoints),
        "registers": [f"0x{r:08X}" for r in regs],
        "carry": c.registers.carry,
        "cycles": c.counters.cycles,
        "stall_cycles": c.counters.stall_cycles,
        "instruction_count": c.counters.instruction_count,
        "ipc": round(c.counters.ipc, 3),
        "io": io_section,
        "instructions": [{"addr": i.address, "text": i.source_text} for i in c.instructions],
        "labels": dict(c._parser.labels),   # name -> word address
        "spad": _read_spad(sim),
        "xfr_shift_en": sim.xfr.xfr_shift_en,
        "mac": _read_mac(c),
    }
    await ws.send_json(state)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def _port_is_free(host, port):
    """Return True when a listening socket can be bound to host:port.

    uvicorn's own bind failure surfaces as a bare OSError traceback that never
    names the port, so probe first to report something actionable. The probe
    must make the same bind decision uvicorn will: asyncio sets SO_REUSEADDR on
    POSIX, but deliberately does not on Windows (where it would permit
    hijacking an active listener). Mirroring that avoids a false "in use" for a
    POSIX port sitting in TIME_WAIT.
    """
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
        return True
    finally:
        probe.close()


def start_dashboard(host=DEFAULT_HOST, port=DEFAULT_PORT):
    import uvicorn

    if not _port_is_free(host, port):
        raise SystemExit(
            f"Port {port} on {host} is already in use.\n"
            f"Start the dashboard on another port, e.g.:\n"
            f"    python ui/server.py --port {port + 1}\n"
            f"    PRU_SIM_UI_PORT={port + 1} python ui/server.py"
        )

    uvicorn.run(app, host=host, port=port)


def _parse_args(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="PRU Simulator Dashboard")
    parser.add_argument(
        "--host",
        default=os.environ.get("PRU_SIM_UI_HOST", DEFAULT_HOST),
        help=f"bind address (default {DEFAULT_HOST}, env PRU_SIM_UI_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PRU_SIM_UI_PORT", DEFAULT_PORT)),
        help=f"bind port (default {DEFAULT_PORT}, env PRU_SIM_UI_PORT)",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    start_dashboard(host=args.host, port=args.port)
