"""FastAPI + WebSocket backend for the PRU Simulator Dashboard."""

import asyncio
import json
import math
import os
import pathlib
import sys

# Ensure project root is on path so simulator can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from simulator import Simulator
from core.branch import LoopState
from perif.gpcfg import MUX_SD
from xfr.xfr_bus import SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, IPC_SPAD

app = FastAPI(title="PRU Simulator Dashboard")

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
_config_lock = asyncio.Lock()

# ---- Step history (for step-back) ----------------------------------------
_MAX_HISTORY = 500
_history: dict[str, list] = {"pru0": [], "rtu0": []}


def _snapshot(core: str) -> dict:
    """Capture full PRU core + memory state before a step."""
    c = sim.cores[core]
    ls = c.loop_state
    sd = c.io_port.sd_filter
    perif = c.io_port.perif
    return {
        "pc": c.pc,
        "halted": c.halted,
        "loop_state": {"count": ls.count, "start_address": ls.start_address,
                       "end_address": ls.end_address} if ls else None,
        "regs": list(c.registers.regs),
        "carry": c.registers.carry,
        "gpo": c.io_port.gpo,
        "gpi": c.io_port.gpi,
        "cycles": c.counters.cycles,
        "stall_cycles": c.counters.stall_cycles,
        "instruction_count": c.counters.instruction_count,
        "mem": [bytes(r._data) for r in sim.memory.regions],
        "sd": sd.snapshot() if sd is not None else None,
        "perif": perif.snapshot() if perif is not None else None,
    }


def _restore(core: str, snap: dict) -> None:
    """Restore PRU core + memory state from a snapshot."""
    c = sim.cores[core]
    c.pc = snap["pc"]
    c.halted = snap["halted"]
    c.loop_state = LoopState(**snap["loop_state"]) if snap["loop_state"] else None
    c.registers.regs = list(snap["regs"])
    c.registers.carry = snap["carry"]
    c.io_port.gpo = snap["gpo"]
    c.io_port.gpi = snap["gpi"]
    c.counters.cycles = snap["cycles"]
    c.counters.stall_cycles = snap["stall_cycles"]
    c.counters.instruction_count = snap["instruction_count"]
    for i, region_data in enumerate(snap["mem"]):
        if i < len(sim.memory.regions):
            sim.memory.regions[i]._data[:] = region_data
    if snap.get("sd") is not None and c.io_port.sd_filter is not None:
        c.io_port.sd_filter.restore(snap["sd"])
    if snap.get("perif") is not None and c.io_port.perif is not None:
        c.io_port.perif.restore(snap["perif"])

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
            _history["pru0"].clear()
            _history["rtu0"].clear()
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
                _history[core].clear()
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
                _history[core].clear()
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
                _history[core].append(_snapshot(core))
                if len(_history[core]) > _MAX_HISTORY:
                    _history[core].pop(0)
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
                _history[core].clear()
                sim.reset(core)
                await _send_state(websocket, core)
            elif action == "hard_reset":
                for k in _history:
                    _history[k].clear()
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
                if _history[core]:
                    _restore(core, _history[core].pop())
                await _send_state(websocket, core)
            elif action == "toggle_breakpoint":
                addr = int(msg.get("addr", 0))
                pru = sim.cores[core]
                if addr in pru.breakpoints:
                    pru.breakpoints.discard(addr)
                elif len(pru.breakpoints) < 10:
                    pru.breakpoints.add(addr)
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
                pru = sim.cores[core]
                at_breakpoint = False
                try:
                    steps = 0
                    while steps < max_steps and not pru.halted and pru.pc < len(pru.instructions):
                        pru.step()
                        steps += 1
                        if pru.pc in pru.breakpoints:
                            at_breakpoint = True
                            break
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                await _send_state(websocket, core, at_breakpoint=at_breakpoint)
            elif action == "set_sd_modulator":
                ch = int(msg.get("channel", 0))
                params = msg.get("params", {})
                sim.set_sd_modulator(core, ch, **params)
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


async def _send_state(ws, core, at_breakpoint=False):
    c = sim.cores[core]
    # R31 display reflects live GPI state (registers.regs[31] is never updated by set_gpi_pin)
    regs = list(c.registers.regs)
    regs[31] = c.io_port.read_r31()
    # GPO pins derived from R30 register value (avoids drift after reset)
    r30 = c.registers.read_full(30)
    gpo_pins = [(r30 >> i) & 1 for i in range(20)]
    sd_data = sim.sd_state(core)
    perif_data = sim.perif_state(core)
    mux_sel = sim.gpcfg_state(core)["mux_sel"]
    perif_on = bool(perif_data and perif_data.get("enabled"))
    # SD view switches on the GPCFG mux select (same as Peripheral mode) so the
    # IO window shows Sigma-Delta as soon as the mode is configured, not only
    # once firmware asserts sd_en (R30 bit 25).
    sd_on = mux_sel == MUX_SD or bool(sd_data and sd_data.get("sd_en"))
    if perif_on:
        mode = "perif"
    elif sd_on:
        mode = "sd"
    else:
        mode = "gpio"
    io_section = {
        "mode": mode,
        "mux_sel": mux_sel,
        "gpo_pins": gpo_pins,
        "gpi_pins": c.io_port.get_gpi_pins(),
    }
    if sd_data is not None:
        io_section["sd"] = sd_data
    if perif_data is not None:
        io_section["perif"] = perif_data
        io_section["loopback"] = sim.loopback_state()
    state = {
        "type": "state",
        "core": core,
        "pc": c.pc,
        "halted": c.halted,
        "at_breakpoint": at_breakpoint,
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


def start_dashboard(host="127.0.0.1", port=8080):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_dashboard()
