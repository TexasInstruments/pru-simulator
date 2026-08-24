"""FastAPI + WebSocket backend for the PRU Simulator Dashboard."""

import asyncio
import json
import math
import os
import pathlib
import re
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
from pru_io import ssi_config_abi as ssi_abi
from pru_io.ssi_runtime import CLOCK_LOOP_OVERHEAD_CYCLES, PROFILES, SSIRuntime

app = FastAPI(title="PRU Simulator Dashboard")

MAX_BREAKPOINTS = 64

# Use project root for config resolution
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config_path = os.path.join(PROJECT_ROOT, "memory.cfg")
SOURCE_DIR = pathlib.Path(PROJECT_ROOT) / "source"

SSI_RUNTIME_READER = SOURCE_DIR / "ssi_generic_reader" / "ssi_generic_reader.asm"
SSI_RUNTIME_EMULATOR = SOURCE_DIR / "ssi_generic_emulator" / "ssi_generic_emulator.asm"
SSI_RUNTIME_DEFAULT_FRAMES = [0xABC, 0xAAA, 0xBCA, 0x12A, 0xCC2]
SSI_RUNTIME_READER_CLK_PIN = 0
SSI_RUNTIME_READER_DATA_PIN = 16
SSI_RUNTIME_EMULATOR_CLK_PIN = 8
SSI_RUNTIME_EMULATOR_DATA_PIN = 0
_ssi_runtime: SSIRuntime | None = None


def _ssi_profile_catalog() -> list[dict]:
    """Return JSON-safe named profile defaults for the dashboard controls."""
    catalog = []
    for name, profile in PROFILES.items():
        item = profile.as_dict()
        item.update({
            "name": name,
            "clock_hz": int(
                300_000_000 / (
                    profile.clock_high_cycles
                    + profile.clock_low_cycles
                    + CLOCK_LOOP_OVERHEAD_CYCLES
                )
            ),
            "max_clock_hz": profile.max_clock_hz,
        })
        catalog.append(item)
    return catalog


def _parse_ssi_frame_values(values) -> list[int]:
    """Parse UI raw-frame values, accepting hex strings with or without 0x."""
    if not values:
        raise ValueError("SSI frame sequence needs at least one value")
    if not isinstance(values, list):
        raise ValueError("SSI frame sequence must be a list")

    parsed = []
    for raw in values:
        try:
            if isinstance(raw, bool):
                raise ValueError
            if isinstance(raw, int):
                value = raw
            elif isinstance(raw, str):
                token = raw.strip()
                if token.lower().startswith("0x"):
                    value = int(token, 16)
                else:
                    value = int(token, 16)
            else:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(f"invalid SSI frame value: {raw!r}") from None
        if value < 0:
            raise ValueError(f"invalid SSI frame value: {raw!r}")
        parsed.append(value)
    return parsed


def _parse_ssi_position_values(values) -> list[int]:
    """Parse natural positions for semantic frame packing."""
    return _parse_ssi_frame_values(values)


def _effective_ssi_clock_hz(fields: dict) -> int:
    high = int(fields.get("clock_high_cycles", 0))
    low = int(fields.get("clock_low_cycles", 0))
    if high <= 0 or low <= 0:
        return 0
    return int(300_000_000 / (high + low + CLOCK_LOOP_OVERHEAD_CYCLES))


def _ssi_mailbox_layout() -> dict:
    """Return absolute Shared RAM addresses for the live SSI mailbox."""
    base = ssi_abi.MAILBOX_BASE
    return {
        "base": base,
        "fields": {
            "sequence": base + ssi_abi.MAILBOX_SEQ_OFF,
            "raw_frame": base + ssi_abi.MAILBOX_RAW_FRAME_OFF,
            # The wire-format position is decoded in place by the host.
            "raw_position": base + ssi_abi.MAILBOX_POSITION_VALUE_OFF,
            "position": base + ssi_abi.MAILBOX_POSITION_VALUE_OFF,
            "status": base + ssi_abi.MAILBOX_STATUS_BITS_OFF,
            "frame_counter": base + ssi_abi.MAILBOX_FRAME_COUNTER_OFF,
            "timestamp": base + ssi_abi.MAILBOX_TIMESTAMP_CYCLES_OFF,
        },
    }


def _ssi_trace_layout() -> dict:
    """Return absolute Shared RAM addresses for trace ring counters."""
    base = ssi_abi.CAPTURE_BASE
    return {
        "base": base,
        "fields": {
            "write_index": base + ssi_abi.CAPTURE_TRACE_WRITE_INDEX_OFF,
            "overrun_count": base + ssi_abi.CAPTURE_TRACE_OVERRUN_COUNT_OFF,
        },
        "records_base": ssi_abi.TRACE_BASE,
        "record_size": ssi_abi.TRACE_RECORD_SIZE,
        "record_count": 1024,
    }


def _ssi_hex(value, width: int) -> str | None:
    """Format a mailbox integer without losing 64-bit precision in JSON UI code."""
    if value is None:
        return None
    mask = (1 << (width * 4)) - 1
    return f"0x{int(value) & mask:0{width}X}"


def _ssi_mailbox_display(mailbox: dict) -> dict:
    """Return fixed-width display strings for the address-labeled UI view."""
    return {
        "sequence": _ssi_hex(mailbox.get("seq"), 8),
        "raw_frame": _ssi_hex(mailbox.get("raw_frame"), 16),
        "raw_position": _ssi_hex(mailbox.get("raw_position_value"), 8),
        "position": _ssi_hex(mailbox.get("position_value"), 8),
        "status": _ssi_hex(mailbox.get("status_bits"), 8),
        "frame_counter": _ssi_hex(mailbox.get("frame_counter"), 8),
        "timestamp": _ssi_hex(mailbox.get("timestamp_cycles"), 16),
        "frame_counter_decimal": int(mailbox.get("frame_counter", 0)),
        "position_decode_error": mailbox.get("position_decode_error"),
    }


def _ssi_trace_display(trace: dict) -> dict:
    """Return fixed-width display strings for trace ring counters."""
    return {
        "write_index": _ssi_hex(trace.get("write_index"), 8),
        "overrun_count": _ssi_hex(trace.get("overrun_count"), 8),
        "write_index_decimal": int(trace.get("write_index", 0)),
        "overrun_count_decimal": int(trace.get("overrun_count", 0)),
    }


def _ssi_runtime_state() -> dict:
    """Return the current generic SSI state in a dashboard-safe shape."""
    if _ssi_runtime is None:
        return {
            "loaded": False,
            "profiles": _ssi_profile_catalog(),
            "mailbox_layout": _ssi_mailbox_layout(),
            "trace_layout": _ssi_trace_layout(),
            "status": "Load the generic SSI PRU pair first",
        }

    config = ssi_abi.unpack_config(
        sim.memory_read(ssi_abi.CONFIG_BASE, 256)
    )
    write_index = int.from_bytes(
        sim.memory_read(
            ssi_abi.CAPTURE_BASE + ssi_abi.CAPTURE_TRACE_WRITE_INDEX_OFF, 4
        ),
        "little",
    )
    overrun_count = int.from_bytes(
        sim.memory_read(
            ssi_abi.CAPTURE_BASE + ssi_abi.CAPTURE_TRACE_OVERRUN_COUNT_OFF, 4
        ),
        "little",
    )
    active = config
    active["effective_clock_hz"] = _effective_ssi_clock_hz(active)
    staged = dict(_ssi_runtime._staged or {})
    staged["effective_clock_hz"] = _effective_ssi_clock_hz(staged)
    mailbox = _ssi_runtime.read_mailbox()
    trace = {"write_index": write_index, "overrun_count": overrun_count}
    return {
        "loaded": True,
        "profiles": _ssi_profile_catalog(),
        "selected_profile": (
            _ssi_runtime._active_profile.name
            if _ssi_runtime._active_profile is not None
            else ""
        ),
        "staged_profile": (
            _ssi_runtime._staged_profile.name
            if _ssi_runtime._staged_profile is not None
            else ""
        ),
        "active": active,
        "staged": staged,
        "effective_clock_hz": active["effective_clock_hz"],
        "requested_generation": active["requested_generation"],
        "pru0_ack_generation": active["pru0_ack_generation"],
        "pru1_ack_generation": active["pru1_ack_generation"],
        "frames": _ssi_runtime.read_raw_frames(),
        "mailbox": mailbox,
        "mailbox_layout": _ssi_mailbox_layout(),
        "mailbox_display": _ssi_mailbox_display(mailbox),
        "trace": trace,
        "trace_layout": _ssi_trace_layout(),
        "trace_display": _ssi_trace_display(trace),
        "wires": sim.list_gpio_wires(),
        "status": "Generic PRU0 emulator / PRU1 reader loaded",
    }


def _load_ssi_runtime_pair() -> dict:
    """Load, wire and initialize the generic PRU0/PRU1 SSI pair."""
    global _ssi_runtime
    reader_source = SSI_RUNTIME_READER.read_text(encoding="utf-8")
    emulator_source = SSI_RUNTIME_EMULATOR.read_text(encoding="utf-8")

    # Loading the generic pair establishes its complete topology.  Remove
    # stale user-created wires first; leaving one connected to an SSI input
    # makes the result depend on whatever project was loaded previously.
    for wire in sim.list_gpio_wires():
        sim.remove_gpio_wire(
            wire["src_core"], wire["src_pin"],
            wire["dst_core"], wire["dst_pin"],
        )

    errors = sim.load("pru1", reader_source, [str(SOURCE_DIR)])
    if errors:
        raise RuntimeError("PRU1 generic reader failed to load: " + "; ".join(errors))
    errors = sim.load("pru0", emulator_source, [str(SOURCE_DIR)])
    if errors:
        raise RuntimeError("PRU0 generic emulator failed to load: " + "; ".join(errors))

    sim.remove_gpio_wire(
        "pru1", SSI_RUNTIME_READER_CLK_PIN,
        "pru0", SSI_RUNTIME_EMULATOR_CLK_PIN,
    )
    sim.remove_gpio_wire(
        "pru0", SSI_RUNTIME_EMULATOR_DATA_PIN,
        "pru1", SSI_RUNTIME_READER_DATA_PIN,
    )
    sim.add_gpio_wire(
        "pru1", SSI_RUNTIME_READER_CLK_PIN,
        "pru0", SSI_RUNTIME_EMULATOR_CLK_PIN,
    )
    sim.add_gpio_wire(
        "pru0", SSI_RUNTIME_EMULATOR_DATA_PIN,
        "pru1", SSI_RUNTIME_READER_DATA_PIN,
    )
    sim.hard_reset()

    _ssi_runtime = SSIRuntime(sim)
    _ssi_runtime.set_raw_frames(SSI_RUNTIME_DEFAULT_FRAMES)
    _ssi_runtime.apply()
    return _ssi_runtime_state()


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
_history: dict[str, list] = {"pru0": [], "rtu0": [], "pru1": []}


def _snapshot(core: str) -> dict:
    """Capture full PRU core + memory state before a step."""
    c = sim.cores[core]
    ls = c.loop_state
    sd = c.io_port.sd_filter
    perif = c.io_port.perif
    i2c = c.io_port.i2c_device
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
        "i2c": i2c.snapshot() if i2c is not None else None,
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
    if snap.get("i2c") is not None and c.io_port.i2c_device is not None:
        c.io_port.i2c_device.restore(snap["i2c"])

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
            _history["pru1"].clear()
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
    global sim, _ssi_runtime
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
            _ssi_runtime = None
            _history["pru0"].clear()
            _history["rtu0"].clear()
            _history["pru1"].clear()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _ssi_runtime
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            core = msg.get("core", "pru0")

            if action == "ssi_runtime_profiles":
                await websocket.send_json({
                    "type": "ssi_runtime_state",
                    **_ssi_runtime_state(),
                })
            elif action == "ssi_runtime_load":
                try:
                    state = _load_ssi_runtime_pair()
                    await websocket.send_json({
                        "type": "ssi_runtime_state",
                        **state,
                    })
                    # Loading the pair changes both source listings.  Publish
                    # normal core-state messages immediately so multi-core
                    # panels do not depend on a later mode switch or manual
                    # get_state request to populate their source tabs.
                    await _send_state(websocket, "pru0")
                    await _send_state(websocket, "pru1")
                except Exception as exc:
                    _ssi_runtime = None
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": str(exc),
                    })
            elif action == "ssi_runtime_stage":
                if _ssi_runtime is None:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": "Load the generic SSI PRU pair first",
                    })
                    continue
                try:
                    profile = msg.get("profile") or None
                    overrides = msg.get("overrides", {})
                    if not isinstance(overrides, dict):
                        raise ValueError("SSI runtime overrides must be an object")
                    _ssi_runtime.stage(profile, **overrides)
                    await websocket.send_json({
                        "type": "ssi_runtime_state",
                        **_ssi_runtime_state(),
                    })
                except (TypeError, ValueError) as exc:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": str(exc),
                    })
            elif action == "ssi_runtime_frames":
                if _ssi_runtime is None:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": "Load the generic SSI PRU pair first",
                    })
                    continue
                try:
                    values = _parse_ssi_frame_values(msg.get("frames", []))
                    _ssi_runtime.set_raw_frames(values)
                    await websocket.send_json({
                        "type": "ssi_runtime_state",
                        **_ssi_runtime_state(),
                    })
                except (TypeError, ValueError) as exc:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": str(exc),
                    })
            elif action == "ssi_runtime_positions":
                if _ssi_runtime is None:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": "Load the generic SSI PRU pair first",
                    })
                    continue
                try:
                    positions = _parse_ssi_position_values(msg.get("positions", []))
                    statuses = msg.get("statuses")
                    if statuses is not None:
                        statuses = _parse_ssi_position_values(statuses)
                    position_count = msg.get("position_count")
                    if position_count is not None:
                        position_count = int(position_count)
                    gray_excess_offset = msg.get("gray_excess_offset")
                    if gray_excess_offset is not None:
                        gray_excess_offset = int(gray_excess_offset)
                    _ssi_runtime.set_positions(
                        positions,
                        statuses,
                        position_count=position_count,
                        gray_excess_offset=gray_excess_offset,
                    )
                    await websocket.send_json({
                        "type": "ssi_runtime_state",
                        **_ssi_runtime_state(),
                    })
                except (TypeError, ValueError) as exc:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": str(exc),
                    })
            elif action == "ssi_runtime_apply":
                if _ssi_runtime is None:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": "Load the generic SSI PRU pair first",
                    })
                    continue
                try:
                    transaction_keys = {"profile", "overrides", "frames"}
                    if transaction_keys.intersection(msg):
                        profile = msg.get("profile") or None
                        overrides = msg.get("overrides", {})
                        if not isinstance(overrides, dict):
                            raise ValueError("SSI runtime overrides must be an object")
                        frame_values = None
                        if "frames" in msg:
                            frame_values = _parse_ssi_frame_values(msg["frames"])
                        _ssi_runtime.stage_and_apply(
                            profile,
                            frame_values=frame_values,
                            timeout_steps=int(msg.get("timeout_steps", 200_000)),
                            **overrides,
                        )
                    else:
                        _ssi_runtime.apply(int(msg.get("timeout_steps", 200_000)))
                    await websocket.send_json({
                        "type": "ssi_runtime_state",
                        **_ssi_runtime_state(),
                    })
                except (TimeoutError, TypeError, ValueError) as exc:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": str(exc),
                    })
            elif action == "ssi_runtime_read":
                if _ssi_runtime is None:
                    await websocket.send_json({
                        "type": "ssi_runtime_error",
                        "error": "Load the generic SSI PRU pair first",
                    })
                    continue
                await websocket.send_json({
                    "type": "ssi_runtime_state",
                    **_ssi_runtime_state(),
                })
            elif action == "load":
                _ssi_runtime = None
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
                _ssi_runtime = None
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
                                "request_id": getattr(
                                    websocket,
                                    "_mem_request_id2" if tag == "mem2" else "_mem_request_id",
                                    None,
                                ),
                                "data": list(data),
                            })
                        except ValueError:
                            pass
            elif action == "reset":
                _history[core].clear()
                sim.reset(core)
                await _send_state(websocket, core)
            elif action == "hard_reset":
                _ssi_runtime = None
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
                    websocket._mem_request_id2 = msg.get("request_id")
                elif tag == "mem1":
                    websocket._mem_addr = addr
                    websocket._mem_len = length
                    websocket._mem_request_id = msg.get("request_id")
                try:
                    data = sim.memory_read(addr, length)
                    await websocket.send_json({
                        "type": "memory",
                        "tag": tag,
                        "addr": addr,
                        "length": length,
                        "request_id": msg.get("request_id"),
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
                                    "request_id": getattr(
                                        websocket,
                                        "_mem_request_id2" if tag == "mem2" else "_mem_request_id",
                                        None,
                                    ),
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
                request_id = msg.get("request_id")
                pru = sim.cores[core]
                at_breakpoint = False
                samples = []
                try:
                    steps = 0
                    while steps < max_steps and not pru.halted and pru.pc < len(pru.instructions):
                        pru.step()
                        steps += 1
                        if capture and _capture_due(pru, steps):
                            samples.append(_capture_sample(pru, run_step=pru.counters.instruction_count))
                        if pru.pc in pru.breakpoints:
                            at_breakpoint = True
                            break
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                if samples:
                    await _send_capture(websocket, core, samples)
                await _send_state(websocket, core, at_breakpoint=at_breakpoint,
                                  captured=capture)
                if request_id is not None:
                    await websocket.send_json({"type": "run_done", "request_id": request_id})
            elif action == "run_multicore":
                max_steps = int(msg.get("max_steps", 1000))
                capture = bool(msg.get("capture", False))
                request_id = msg.get("request_id")
                partner = msg.get("partner", "pru1")
                lead_pru = sim.cores[core]
                partner_pru = sim.cores[partner]
                lead_bp = partner_bp = False
                samples = []
                partner_samples = []
                sync_error = _multicore_sync_error(core, partner)
                if sync_error:
                    error = {
                        "type": "error",
                        "code": "multicore_sync",
                        "errors": [sync_error],
                    }
                    if request_id is not None:
                        error["request_id"] = request_id
                    await websocket.send_json(error)
                    if request_id is not None:
                        await websocket.send_json({"type": "run_done", "request_id": request_id})
                    continue
                try:
                    steps = 0
                    while (steps < max_steps and not lead_pru.halted
                           and lead_pru.pc < len(lead_pru.instructions)):
                        sim.step_paced(core, partner, 1)
                        steps += 1
                        sync_error = _multicore_step_sync_error(core, partner)
                        if sync_error:
                            error = {
                                "type": "error",
                                "code": "multicore_sync",
                                "errors": [sync_error],
                            }
                            if request_id is not None:
                                error["request_id"] = request_id
                            await websocket.send_json(error)
                            break
                        # ``steps`` is local to this websocket request.  Use the
                        # lead's absolute instruction count so the browser's
                        # horizontal axis remains monotonic across Run chunks.
                        run_step = lead_pru.counters.instruction_count
                        if capture and _capture_due(lead_pru, steps):
                            samples.append(_capture_sample(lead_pru, run_step=run_step))
                        if capture and _capture_due(partner_pru, steps):
                            partner_samples.append(_capture_sample(partner_pru, run_step=run_step))
                        if lead_pru.pc in lead_pru.breakpoints:
                            lead_bp = True
                            break
                        if partner_pru.pc in partner_pru.breakpoints:
                            partner_bp = True
                            break
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "errors": [str(ve)]})
                if sync_error:
                    if request_id is not None:
                        await websocket.send_json({"type": "run_done", "request_id": request_id})
                    continue
                # The two capture messages belong to one shared instruction
                # timeline.  Give the browser a stable group key so it can
                # interleave them before writing to its single graph buffer;
                # sending one complete core batch after the other would evict
                # the first core from that buffer.
                capture_group = None
                if samples and partner_samples:
                    capture_group = (
                        f"{core}:{partner}:{lead_pru.counters.instruction_count}"
                    )
                if samples:
                    await _send_capture(
                        websocket, core, samples, capture_group=capture_group
                    )
                if partner_samples:
                    await _send_capture(
                        websocket, partner, partner_samples,
                        capture_group=capture_group,
                    )
                await _send_state(websocket, core, at_breakpoint=lead_bp,
                                  captured=capture)
                await _send_state(websocket, partner, at_breakpoint=partner_bp,
                                  captured=capture)
                if request_id is not None:
                    await websocket.send_json({"type": "run_done", "request_id": request_id})
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
            elif action == "add_wire":
                sim.add_gpio_wire(msg["src_core"], int(msg["src_pin"]),
                                  msg["dst_core"], int(msg["dst_pin"]))
                await websocket.send_text(json.dumps({"type": "wires", "wires": sim.list_gpio_wires()}))
            elif action == "remove_wire":
                sim.remove_gpio_wire(msg["src_core"], int(msg["src_pin"]),
                                     msg["dst_core"], int(msg["dst_pin"]))
                await websocket.send_text(json.dumps({"type": "wires", "wires": sim.list_gpio_wires()}))
            elif action == "get_wires":
                await websocket.send_text(json.dumps({"type": "wires", "wires": sim.list_gpio_wires()}))
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
            elif action == "ssi_inject":
                clk_pin = int(msg.get("clk_pin", 0))
                data_pin = int(msg.get("data_pin", 8))
                value = int(msg.get("value", 0), 16) if isinstance(msg.get("value", 0), str) else int(msg.get("value", 0))
                bits = int(msg.get("bits", 12))
                sim.ssi_inject(
                    core=core,
                    clk_pin=clk_pin,
                    data_pin=data_pin,
                    value=value,
                    bits=bits,
                )
                await websocket.send_text(json.dumps({
                    "type": "ssi_inject_ok",
                    "value": value,
                    "value_hex": f"0x{value:03x}",
                    "bits": bits,
                    "clk_pin": clk_pin,
                    "data_pin": data_pin,
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


# ``step_paced`` permits a small peripheral-clock lead/lag while it catches the
# follower up.  Larger differences mean one core can remain frozen while the
# other continues, which produces a misleading multi-core graph.
MULTICORE_PERIF_SYNC_TOLERANCE_NS = 20.0


def _multicore_sync_error(core: str, partner: str) -> str | None:
    """Return a clear preflight error when the two run timelines are unsafe."""
    lead = sim.cores[core]
    follow = sim.cores[partner]
    if lead.halted != follow.halted:
        return f"Multi-core run stopped: {core} and {partner} have different halt states; reset both cores."

    lead_mode = _io_mode(core)
    follow_mode = _io_mode(partner)
    if lead_mode == "perif" or follow_mode == "perif":
        if lead_mode != follow_mode:
            return f"Multi-core run stopped: {core}={lead_mode} and {partner}={follow_mode}; both cores must use the same peripheral mode."
        lead_time = sim._perif[core]._now_ns
        follow_time = sim._perif[partner]._now_ns
        if abs(lead_time - follow_time) > MULTICORE_PERIF_SYNC_TOLERANCE_NS:
            return f"Multi-core run stopped: peripheral clocks differ by {abs(lead_time - follow_time):.1f} ns; reset both cores."
        return None

    if lead.counters.instruction_count != follow.counters.instruction_count:
        return f"Multi-core run stopped: instruction counts differ ({core}={lead.counters.instruction_count}, {partner}={follow.counters.instruction_count}); reset both cores."
    return None


def _multicore_step_sync_error(core: str, partner: str) -> str | None:
    """Check synchronization using only state that can change during a step.

    The full preflight helper serializes SD/peripheral state and is useful at
    request boundaries, but doing that for every instruction makes a run
    unnecessarily expensive.  Peripheral enablement and virtual time are the
    only mode-specific values needed while stepping; GPIO/SSI only needs the
    cheap counter check.
    """
    lead = sim.cores[core]
    follow = sim.cores[partner]
    if lead.halted != follow.halted:
        return f"Multi-core run stopped: {core} and {partner} have different halt states; reset both cores."

    lead_perif = sim._perif.get(core)
    follow_perif = sim._perif.get(partner)
    lead_enabled = bool(lead_perif and lead_perif.enabled)
    follow_enabled = bool(follow_perif and follow_perif.enabled)
    if lead_enabled or follow_enabled:
        if lead_enabled != follow_enabled:
            return f"Multi-core run stopped: {core} and {partner} have different peripheral enable states; both cores must use the same peripheral mode."
        delta = abs(lead_perif._now_ns - follow_perif._now_ns)
        if delta > MULTICORE_PERIF_SYNC_TOLERANCE_NS:
            return f"Multi-core run stopped: peripheral clocks differ by {delta:.1f} ns; reset both cores."
        return None

    if lead.counters.instruction_count != follow.counters.instruction_count:
        return f"Multi-core run stopped: instruction counts differ ({core}={lead.counters.instruction_count}, {partner}={follow.counters.instruction_count}); reset both cores."
    return None


# One Signal Graph sample per this many instructions, outside peripheral mode.
# SSI at 4 MHz / 300 MHz PRU = 75 cycles per half-bit.  With stride 10 we get
# ~7-8 samples per half-bit — enough to see rising/falling edges clearly.
# UART at 4 Mb/s = 75 cycles/bit so the bit period is still resolvable (7+ pts).
# The previous value of 100 missed most SSI data transitions entirely.
CAPTURE_STRIDE_GP = 10


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


def _capture_sample(c, run_step: int = 0) -> list[int]:
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
            gpi_bits, out_bits, oe_bits, clk_bits, run_step]


async def _send_capture(ws, core, samples, capture_group=None):
    """Ship a run loop's per-instruction Signal Graph samples in one message."""
    message = {
        "type": "capture",
        "core": core,
        "mode": _io_mode(core),
        "samples": samples,
    }
    if capture_group is not None:
        message["capture_group"] = capture_group
    await ws.send_json(message)


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
    }
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
        "wires": sim.list_gpio_wires(),
    }
    await ws.send_json(state)


def start_dashboard(host="127.0.0.1", port=8080):
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    start_dashboard()
