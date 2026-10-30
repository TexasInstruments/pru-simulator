"""FastAPI + WebSocket backend for the PRU Simulator Dashboard."""

import asyncio
import configparser
import copy
import json
import math
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time

# Ensure project root is on path so simulator can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base64

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from simulator import Simulator
from mcp_server.server import PRUSimulatorMCP
from core.branch import LoopState
from perif.gpcfg import MUX_SD
from pru_io.device_profiles import discover_device_profiles
from pru_io.scenarios import SCENARIOS, apply_scenario, ssi_demo_state, set_ssi_demo_position
from pru_io.ssi_encoder_model import SSIEncoderModel
from ui.live_sessions import DashboardConnection
from xfr.crc_accelerator import CRCAccelerator
from xfr.xfr_bus import SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, IPC_SPAD

app = FastAPI(title="PRU Simulator Dashboard")

MAX_BREAKPOINTS = 64
# While a shared run is active, observers get state/memory at most this often.
OBSERVER_STATE_INTERVAL_S = 0.1
_monotonic = time.monotonic
_last_observer_publish = float('-inf')
_observer_publish_skipped = False
_skipped_captured_cores = set()  # their samples already went out as capture messages
_trailing_publish = None  # timer that publishes a withheld state if the owner goes quiet

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
_connections: set[DashboardConnection] = set()
_execution_owner = None
_execution_mode = None
_execution_cores = ["pru0"]


def _selected_execution_cores():
    global _execution_cores
    available = [name for name in ("pru0", "pru1", "rtu0", "rtu1") if name in sim.cores]
    _execution_cores = [name for name in available if name in _execution_cores] or available[:1]
    return list(_execution_cores)


def _execution_state(connection=None):
    scenario = getattr(sim, "_scenario", None)
    return {"type": "execution", "running": _execution_owner is not None,
            "owner": _execution_owner is not None and _execution_owner is connection,
            "mode": _execution_mode, "cores": _selected_execution_cores(),
            "scenario": scenario.describe() if scenario else None}


def _stop_execution():
    global _execution_owner, _execution_mode
    _execution_owner = _execution_mode = None
    for connection in _connections:
        connection.publish(_execution_state(connection))


def _reject_legacy_i2c_conflict(core: str) -> None:
    """The legacy I2C slot owns R30/R31 bits 0/1; refuse it next to a device on them."""
    for device in sim.device_bus.devices:
        if (sim.device_bus.core_for_device(device) == core
                and {0, 1} & set(device.nets)):
            raise ValueError(
                f"legacy I2C attach uses {core} pins 0 and 1, which device "
                f"{device.name!r} already uses; detach it first")


def _reject_i2c_pin_conflict(core: str, profile: object, config: object) -> None:
    """Refuse a device that drives pins the legacy I2C attach already owns."""
    if sim.cores[core].io_port.i2c_device is None:
        return
    spec = discover_device_profiles().get(profile)
    if not isinstance(spec, dict):
        return  # unknown profile: pru_device_attach reports it
    values = {**spec["defaults"], **(config if isinstance(config, dict) else {})}
    if {values.get(key) for key in spec.get("outputs", {})} & {0, 1}:
        # Spelled-out acronyms (ssi, sd) take "an" like vowels do.
        article = "an" if profile.startswith(("a", "e", "i", "o", "u", "ssi", "sd")) else "a"
        raise ValueError(
            f"the legacy I2C attach already owns {core} pins 0 and 1; "
            f"detach it before attaching {article} {profile} device on those pins")


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
        "gpio_loopback_mask": c.io_port.loopback_mask,
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
        "ct_ctrl": c.constant_table.snapshot(),
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
        "paced_pending": copy.deepcopy(sim._paced_pending),
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
    if "ct_ctrl" in snap:
        c.constant_table.restore(snap["ct_ctrl"])


def _restore(core: str, snap: dict) -> None:
    """Restore every core and shared device to the same saved point in time."""
    for core_name, core_snap in snap.get("cores", {core: snap}).items():
        if core_name in sim.cores:
            _restore_core(sim.cores[core_name], core_snap)
    for i, region_data in enumerate(snap["mem"]):
        if i < len(sim.memory.regions):
            sim.memory.regions[i]._data[:] = region_data
    sim._paced_pending = copy.deepcopy(snap.get("paced_pending", {}))
    if snap.get("iep") is not None:
        sim.iep.restore(snap["iep"])
        _apply_iep_override()
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


@app.get("/constant_table/{core}")
async def get_constant_table(core: str):
    """C0-C31 as *core* sees them right now (C24-C31 follow CTBIR/CTPPR)."""
    if core not in sim.cores:
        raise HTTPException(status_code=404, detail=f"Unknown core {core!r}")
    return sim.constant_table_view(core)


@app.get("/cores")
async def get_cores():
    return list(sim.cores)


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


def _replace_config(text: str) -> Simulator:
    """Build a Simulator from *text*, then make *text* the content of config_path.

    The text is validated through a temp file beside config_path (so lookups
    relative to the config directory resolve the same way) and only replaces
    config_path once the Simulator built; a bad config leaves the file as it was."""
    fd, tmp_path = tempfile.mkstemp(
        prefix=".memory-", suffix=".cfg.tmp",
        dir=os.path.dirname(os.path.abspath(config_path)),
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        if os.path.exists(config_path):
            shutil.copymode(config_path, tmp_path)
        try:
            new_sim = Simulator(config_path=tmp_path)
        except Exception as e:
            # Parser errors quote the file name; report config_path, not the temp file.
            msg = str(e).replace(repr(tmp_path)[1:-1], repr(config_path)[1:-1])
            raise ValueError(msg.replace(tmp_path, config_path)) from e
        os.replace(tmp_path, config_path)
        return new_sim
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.put("/config")
async def put_config(request: Request):
    global sim, _iep_clock_override
    text = (await request.body()).decode("utf-8")
    async with _config_lock:
        try:
            sim = _replace_config(text)
            _iep_clock_override = None   # a saved config speaks for itself
            _clear_history()
            _stop_execution()
            _publish_updates()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)


ALLOWED_CLOCK_MHZ = {200, 225, 250, 300, 333}

# IEP counter clock chosen in the dashboard. Runtime only: memory.cfg's
# iep_clock_mhz is never written. None means "the configured default".
IEP_CLOCK_CHOICES_MHZ = (200, 225, 250, 300, 333)
_iep_clock_override = None


def _configured_iep_mhz() -> float:
    """Keep the backend's configured external rate before applying an override."""
    return getattr(sim, "_ui_configured_iep_mhz", float(sim.iep.external_clock_hz / 1_000_000))


def _set_iep_clock(mhz) -> None:
    """Switch only the external IEP clock; None restores the configured rate."""
    global _iep_clock_override
    if mhz is not None and (isinstance(mhz, bool) or not isinstance(mhz, (int, float))
                            or not math.isfinite(mhz) or mhz <= 0):
        raise ValueError("IEP clock must be a finite positive number in MHz")
    configured = _configured_iep_mhz()
    sim.iep.set_clock_mhz(configured if mhz is None else mhz)
    sim._ui_configured_iep_mhz = configured
    _iep_clock_override = mhz


def _apply_iep_override() -> None:
    """Preserve the session choice across rebuilds and history restoration."""
    configured = _configured_iep_mhz()
    sim._ui_configured_iep_mhz = configured
    sim.iep.set_clock_mhz(configured if _iep_clock_override is None else _iep_clock_override)


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


def _clock_speed_state() -> dict:
    return {
        "mhz": sim._pru_clock_mhz,
        "groups": [
            {"id": group, "mhz": mhz,
             "cores": [name for name in members if name in sim.cores]}
            for group, mhz, members in (
                (0, sim._pru_clock_mhz, ("pru0", "rtu0")),
                (1, sim._pru1_clock_mhz, ("pru1", "rtu1")),
            )
        ],
        "choices_mhz": sorted(ALLOWED_CLOCK_MHZ),
    }


@app.get("/config/clock_speed")
async def get_clock_speed():
    return _clock_speed_state()


@app.put("/config/clock_speed")
async def put_clock_speed(request: Request):
    global sim
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    mhz = body.get("mhz")
    if (isinstance(mhz, bool) or not isinstance(mhz, (int, float))
            or mhz not in ALLOWED_CLOCK_MHZ):
        return JSONResponse(
            {"error": f"mhz must be one of {sorted(ALLOWED_CLOCK_MHZ)}"},
            status_code=400,
        )
    group = body.get("group")
    if "group" in body and (isinstance(group, bool) or not isinstance(group, (int, float))
                            or group not in (0, 1)):
        return JSONResponse({"error": "group must be 0 or 1"}, status_code=400)
    async with _config_lock:
        try:
            with open(config_path, "r") as f:
                text = f.read()
            if group == 0:
                cfg = configparser.ConfigParser()
                cfg.read_string(text, source=config_path)
                # An omitted second clock inherits the first. Pin its old rate
                # before changing the first so the other cores keep their rate.
                if not cfg.has_option("device", "pru1_clock_mhz") and mhz != sim._pru1_clock_mhz:
                    text = _set_ini_value(text, "device", "pru1_clock_mhz", str(sim._pru1_clock_mhz))
            if group is None or group == 0:
                text = _set_ini_value(text, "device", "pru_clock_mhz", str(mhz))
            if group is None or group == 1:
                text = _set_ini_value(text, "device", "pru1_clock_mhz", str(mhz))
            sim = _replace_config(text)
            _apply_iep_override()
            _clear_history()
            _stop_execution()
            _publish_updates()
            return {"ok": True}
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    global _execution_owner, _execution_mode, _execution_cores, _last_observer_publish
    await websocket.accept()
    websocket = DashboardConnection(websocket)
    _connections.add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("action")
            core = msg.get("core", "pru0")

            if action == "set_execution_cores":
                cores = msg.get("cores")
                if (not isinstance(cores, list) or not cores
                        or not all(isinstance(name, str) and name in sim.cores for name in cores)
                        or len(set(cores)) != len(cores)):
                    await websocket.send_json({"type": "error", "errors": [
                        f"cores must be a nonempty list of unique cores of {list(sim.cores)}"]})
                    continue
                previous = _selected_execution_cores()
                _execution_cores = cores
                if _selected_execution_cores() != previous:
                    _stop_execution()
                    _flush_skipped_publish()
                else:
                    await websocket.send_json(_execution_state(websocket))
                continue

            if action == "start_execution":
                if msg.get("mode") not in {"run", "sim"}:
                    await websocket.send_json({"type": "error", "errors": ["mode must be run or sim"]})
                    continue
                if _execution_owner in (None, websocket):
                    _execution_owner, _execution_mode = websocket, msg["mode"]
                    _last_observer_publish = float('-inf')  # a new run's first chunk always publishes
                reply = _execution_state(websocket)
                if "request_id" in msg:
                    reply["request_id"] = msg["request_id"]
                await websocket.send_json(reply)
                for connection in _connections - {websocket}:
                    connection.publish(_execution_state(connection))
                continue
            if action == "stop_execution":
                _stop_execution()
                _flush_skipped_publish()
                continue

            if msg.get("managed") and action in {"step", "run", "run_multicore", "step_multicore"}:
                selected = _selected_execution_cores()
                core = selected[0]
                action = ("step" if action in {"step", "step_multicore"} else "run")
                if len(selected) > 1:
                    action += "_multicore"
                msg["partners"] = selected[1:]

            if not isinstance(core, str) or core not in sim.cores:
                await websocket.send_json({"type": "error", "tag": "core",
                    "errors": [f"core must be one of {list(sim.cores)}, got {core!r}"],
                    "available_cores": list(sim.cores)})
                await _send_state(websocket, next(iter(sim.cores)),
                                  run_id=msg.get("run_id") if action in ("run", "run_multicore", "step_multicore") else None)
                continue

            if action in {"step", "run", "run_multicore", "step_multicore"}:
                if ((_execution_owner is not None and _execution_owner is not websocket)
                        or (msg.get("managed") and _execution_owner is not websocket)):
                    await websocket.send_json({"type": "error", "errors": ["Execution stopped or controlled by another tab"]})
                    await _send_state(websocket, core, run_id=msg.get("run_id"))
                    continue
            elif action in {"load", "load_elf", "reset", "hard_reset", "step_back", "scenario_load"}:
                if _execution_owner is not None:
                    _stop_execution()

            if action == "subscribe_capture":
                websocket.capture = bool(msg.get("enabled", False))
                await websocket.send_json({"type": "capture_subscription", "enabled": websocket.capture})
            elif action == "load":
                _clear_history()
                sim._scenario = None
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
                        if SOURCE_DIR.resolve() != candidate_dir:
                            # generated ABI headers live in source/, not in project folders
                            include_paths.append(str(SOURCE_DIR))
                    except (ValueError, OSError):
                        pass  # Unsafe path — ignore filename, use default include_paths
                errors = sim.load(core, msg["source"], include_paths)
                if errors:
                    await websocket.send_json({"type": "error", "errors": errors})
                await _send_state(websocket, core)
            elif action == "load_elf":
                _clear_history()
                sim._scenario = None
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
                await _send_state(websocket, core, at_breakpoint=at_breakpoint,
                                  run_id=msg.get("run_id"))
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
                await _send_state(websocket, core, view_only=True)
            elif action == "set_gpio_drive_mask":
                try:
                    # Full-mask clients replace all directions; dashboard pin
                    # intents toggle one pin against the current authoritative mask.
                    if "pin" in msg:
                        if "mask" in msg:
                            raise ValueError("provide either GPIO pin or drive mask, not both")
                        pin = msg["pin"]
                        if (isinstance(pin, bool) or not isinstance(pin, int)
                                or not 0 <= pin < 20):
                            raise ValueError("GPIO pin must be an integer from 0 to 19")
                        mask = sim.cores[core].io_port.gpo_drive_mask ^ (1 << pin)
                    else:
                        mask = msg.get("mask")
                    if (isinstance(mask, bool) or not isinstance(mask, int)
                            or not 0 <= mask < (1 << 20)):
                        raise ValueError("GPIO drive mask must be a 20-bit integer")
                    for (owner_core, pin), lease in sim.device_bus.snapshot()["output_leases"].items():
                        if owner_core == core and bool(mask & (1 << pin)) != lease["drive_output"]:
                            raise ValueError(f"GPIO {pin} direction is owned; detach its device before changing it")
                    sim.set_gpio_drive_mask(core, mask)
                    _clear_history()
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "tag": "gpio", "errors": [str(ve)]})
                await _send_state(websocket, core)
            elif action == "set_iep_clock":
                try:
                    _set_iep_clock(msg.get("mhz"))
                except ValueError as ve:
                    await websocket.send_json({"type": "error", "tag": "iep", "errors": [str(ve)]})
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
                capture = bool(msg.get("capture", False)) or any(c.capture for c in _connections)
                stride = _capture_stride(msg)
                pru = sim.cores[core]
                at_breakpoint = False
                samples = []
                error = None
                try:
                    steps = 0
                    while steps < max_steps and not pru.halted and pru.pc < len(pru.instructions):
                        pru.step()
                        steps += 1
                        if capture and _capture_due(pru, steps, stride):
                            samples.append(_capture_sample(pru))
                        if pru.pc in pru.breakpoints:
                            at_breakpoint = True
                            break
                except ValueError as ve:
                    error = {"type": "error", "errors": [str(ve)]}
                if samples:
                    await _send_capture(websocket, core, samples)
                if error:
                    await websocket.send_json(error)
                await _send_state(websocket, core, at_breakpoint=at_breakpoint,
                                  captured=capture, run_id=msg.get("run_id"))
            elif action in {"run_multicore", "step_multicore"}:
                manual_step = action == "step_multicore"
                max_steps = 1 if manual_step else int(msg.get("max_steps", 1000))
                capture = bool(msg.get("capture", False)) or any(c.capture for c in _connections)
                stride = _capture_stride(msg)
                # "partners" runs three or four cores together; "partner" is the
                # original single follower.
                partners = msg.get("partners") or [msg.get("partner", "pru1")]
                if (not isinstance(partners, list)
                        or not all(isinstance(name, str) for name in partners)
                        or len(set(partners)) != len(partners) or core in partners
                        or any(name not in sim.cores for name in partners)):
                    await websocket.send_json({"type": "error", "errors": [
                        f"partners must be other cores of {list(sim.cores)}, got {partners!r}"]})
                    await _send_state(websocket, core, run_id=msg.get("run_id"))
                    continue
                lead_pru = sim.cores[core]
                partner_prus = [sim.cores[name] for name in partners]
                lead_bp = False
                partner_bp = [False] * len(partners)
                samples = []
                error = None
                manual_counts = {name: sim.cores[name].counters.instruction_count
                                 for name in [core, *partners]} if manual_step and capture else {}
                try:
                    if manual_step:
                        # One click advances the whole selected set atomically.
                        _record_step(core)
                    steps = 0
                    while steps < max_steps:
                        pacing = sim.step_paced_many(core, partners, 1)
                        steps += pacing["lead_steps"]
                        progressed = pacing["lead_steps"] or any(
                            sim.cores[name].counters.instruction_count != before
                            for name, before in manual_counts.items())
                        if capture and progressed and (manual_step or _capture_due(lead_pru, steps, stride)):
                            samples.append(_capture_sample(lead_pru, partner_prus))
                        if not pacing["complete"]:
                            error = {
                                "type": "error", "reason": pacing["reason"],
                                "errors": ["Follower catch-up is incomplete; run again to continue"],
                            }
                            break
                        if lead_pru.pc in lead_pru.breakpoints:
                            lead_bp = True
                            break
                        partner_bp = [p.pc in p.breakpoints for p in partner_prus]
                        if any(partner_bp):
                            break
                        if lead_pru.halted or lead_pru.pc >= len(lead_pru.instructions):
                            break
                except ValueError as ve:
                    error = {"type": "error", "errors": [str(ve)]}
                if samples:
                    await _send_capture(websocket, core, samples, partners)
                if error:
                    await websocket.send_json(error)
                await _send_state(websocket, core, at_breakpoint=lead_bp,
                                  captured=bool(samples))
                for name, at_bp in zip(partners, partner_bp):
                    await _send_state(websocket, name, at_breakpoint=at_bp,
                                      captured=bool(samples),
                                      run_id=msg.get("run_id") if name == partners[-1] else None)
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
                # Deprecated: the dashboard attaches a TCA9538 through the
                # generic device_attach (profile "tca9538"). Kept for old clients.
                enabled = bool(msg.get("enabled", False))
                try:
                    if enabled:
                        _reject_legacy_i2c_conflict(core)
                    sim.i2c_attach(core, enabled, int(msg.get("address", 0x23)))
                except (TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "device",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action == "device_attach":
                try:
                    _reject_i2c_pin_conflict(core, msg.get("profile", ""), msg.get("config"))
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
            elif action == "device_discover":
                await websocket.send_json({
                    "type": "device_profiles",
                    "profiles": discover_device_profiles(),
                })
            elif action == "scenario_list":
                await websocket.send_json({
                    "type": "scenarios",
                    "scenarios": [scenario.describe() for scenario in SCENARIOS.values()],
                })
            elif action == "scenario_load":
                try:
                    scenario = apply_scenario(sim, msg.get("name"),
                                              _device_api_for_current_sim(),
                                              config=msg.get("config"))
                    _execution_cores = list(scenario.cores)
                    _clear_history()
                    await websocket.send_json({"type": "scenario_loaded", **scenario.describe()})
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "scenario",
                                               "errors": [str(exc)]})
                    scenario = None
                for scenario_core in (scenario.cores if scenario else (core,)):
                    await _send_state(websocket, scenario_core)
            elif action == "ssi_demo_position":
                try:
                    set_ssi_demo_position(sim, msg.get("position"))
                    _clear_history()
                except (KeyError, TypeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "tag": "ssi_demo",
                                               "errors": [str(exc)]})
                await _send_state(websocket, core)
            elif action in ("ssi_set_position", "ssi_set_error"):
                try:
                    name = msg.get("name", "")
                    device = sim.device_bus.get_device(name)
                    attached_core = sim.device_bus.core_for_device(device)
                    if attached_core != core:
                        raise ValueError(f"device {name!r} is attached to {attached_core}")
                    if not isinstance(device, SSIEncoderModel):
                        raise ValueError(f"device {name!r} is not an SSI encoder")
                    if action == "ssi_set_position":
                        device.set_position(msg.get("position"))
                    else:
                        device.set_error(msg.get("error"))
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
            else:
                await websocket.send_json({"type": "error", "tag": "ws",
                                           "errors": [f"unknown action {action!r}"]})
            if action not in {"get_state", "read_memory", "device_discover", "scenario_list", "subscribe_capture"}:
                captured_cores = {core, *partners} if action in {"run_multicore", "step_multicore"} and samples else (
                    {core} if action == "run" and capture else set())
                breakpoint_cores = set()
                if action in {"step", "run", "run_multicore", "step_multicore"}:
                    stepped = [core, *partners] if action in {"run_multicore", "step_multicore"} else [core]
                    breakpoint_cores = {name for name in stepped
                                        if sim.cores[name].pc in sim.cores[name].breakpoints}
                    if _execution_owner is websocket and (breakpoint_cores or any(
                            sim.cores[name].halted or sim.cores[name].pc >= len(sim.cores[name].instructions)
                            for name in stepped)):
                        _stop_execution()
                _publish_updates(websocket, captured_cores, breakpoint_cores,
                                 throttle=action in {"run", "run_multicore"} and _execution_owner is websocket)
    except WebSocketDisconnect:
        return
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        _connections.discard(websocket)
        if _execution_owner is websocket:
            _stop_execution()
            _flush_skipped_publish()
        await websocket.close()


def _read_mac(core) -> dict:
    """Return MAC accelerator status for the given PRUCore."""
    acc = core.accelerators.get(0)
    if acc is None:
        return {"mode": False, "acc_carry": False}
    return {"mode": acc.mac_mode, "acc_carry": acc.acc_carry}


def _read_crc(core) -> dict:
    """Return CRC accelerator status for the given PRUCore."""
    acc = core.accelerators.get(CRCAccelerator.DEVICE_ID)
    if acc is None:
        return {"mode": "—", "value": "—"}
    if acc.crc32_mode:
        mode = "CRC32"
        value = f"{acc.crc_reg:08X}"
    else:
        mode = "CRC16-MOD" if acc.mod_en else "CRC16"
        value = f"{acc.crc_reg:04X}"
    return {"mode": mode, "value": value}


def _read_spad(sim_obj) -> dict:
    """Return scratchpad contents as hex words per bank.

    Banks 0/1/2 hold R0–R29 (30 words); IPC holds R2–R9 (8 words).  A bank
    whose device id is not a broadside id on the target (IPC on ICSS-M) is
    left out, since firmware cannot reach it.
    """
    result = {}
    for device_id, key, count in [
        (SPAD_BANK0, "bank0", 30),
        (SPAD_BANK1, "bank1", 30),
        (SPAD_BANK2, "bank2", 30),
        (IPC_SPAD,   "ipc",   8),
    ]:
        if device_id not in sim_obj.capabilities.broadside_ids:
            continue
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


def _capture_stride(msg: dict) -> int:
    """Instructions per Signal Graph sample outside peripheral mode (client may ask finer)."""
    stride = msg.get("stride", CAPTURE_STRIDE_GP)
    if isinstance(stride, bool) or not isinstance(stride, int):
        stride = CAPTURE_STRIDE_GP
    scenario = getattr(sim, "_scenario", None)
    if scenario is not None:
        stride = min(stride, scenario.ui.get("capture_stride", CAPTURE_STRIDE_GP))
    return max(1, min(stride, CAPTURE_STRIDE_GP))


def _capture_due(c, steps: int, stride: int = CAPTURE_STRIDE_GP) -> bool:
    """Whether to take a graph sample after instruction `steps` of this chunk.

    Decided per instruction rather than once per chunk because firmware enables
    peripheral mode from inside the run — `perif_duty_cycle_sweep.asm` writes
    GPCFG about 11 instructions in and is only ~160 instructions long, so a
    stride fixed before the loop would decimate away the whole transmission.
    """
    perif = c.io_port.perif
    if perif is not None and perif.enabled:
        return True
    return steps % stride == 0


def _capture_sample(c, partners=()) -> list[int]:
    """One Signal Graph sample, taken inside the run loop.

    Run executes up to `max_steps` instructions per websocket round-trip, so a
    graph fed only by the state push at the end of that loop samples once per
    chunk. For perif signals that is far too coarse and the waveform aliases
    away entirely — the reported symptom being a Run capture of
    `perif_duty_cycle_sweep.asm` that draws nothing while SIM (one instruction
    per push) traces it fine.

    Packed into ints (bitmask per lane group) to keep the batch small. A
    multi-core run appends [r30, gpi bits] for each partner core, so both ends
    of every wire are plotted.
    """
    perif = c.io_port.perif
    gpi_bits = _gpi_bits(c)
    out_bits = oe_bits = clk_bits = 0
    if perif is not None:
        for i, ch in enumerate(perif.channels[:3]):
            if ch.tx_line_value():
                out_bits |= 1 << i
            if ch.tx_out_en:
                oe_bits |= 1 << i
            if ch.tx_clk_pin:
                clk_bits |= 1 << i
    sample = [c.counters.instruction_count, c.registers.read_full(30) & 0xFFFFF,
              gpi_bits, out_bits, oe_bits, clk_bits]
    for p in partners:
        sample += [p.registers.read_full(30) & 0xFFFFF, _gpi_bits(p)]
    return sample


def _gpi_bits(c) -> int:
    bits = 0
    for i, v in enumerate(c.io_port.get_gpi_pins()[:20]):
        if v:
            bits |= 1 << i
    return bits


async def _send_capture(ws, core, samples, partners=None):
    """Ship a run loop's per-instruction Signal Graph samples in one message."""
    msg = {
        "type": "capture",
        "core": core,
        "mode": _io_mode(core),
        "samples": samples,
    }
    if partners:
        msg["partners"] = partners   # names for the trailing [r30, gpi] pairs
    # Queue the shared event before yielding to a slow requester. Otherwise a
    # second tab can reset/load and observe this older capture after its new state.
    for connection in _connections - {ws}:
        if connection.capture:
            connection.publish(msg)
    await ws.send_json(msg)


async def _send_state(ws, core, at_breakpoint=False, captured=False, run_id=None, view_only=False):
    state = _state(core, at_breakpoint, captured)
    state["execution"] = _execution_state(ws)
    if run_id is not None:
        state["run_id"] = run_id
    if view_only:
        state["view_only"] = True
    await ws.send_json(state)


def _publish_updates(origin=None, captured_cores=(), breakpoint_cores=(), throttle=False):
    """Publish shared data; request acknowledgements and view choices stay local.

    throttle is set only for a run chunk that left execution active: those arrive
    back to back and each would queue a full state per core per observer.
    Capture samples travel separately (_send_capture) and are never throttled.
    """
    global _last_observer_publish, _observer_publish_skipped, _trailing_publish
    observers = _connections - {origin}
    if not observers:
        return
    now = _monotonic()
    if throttle and not breakpoint_cores and now - _last_observer_publish < OBSERVER_STATE_INTERVAL_S:
        _observer_publish_skipped = True
        _skipped_captured_cores.update(captured_cores)
        if _trailing_publish is None:  # one timer per window, even if the owner then stops sending chunks
            delay = max(0.0, _last_observer_publish + OBSERVER_STATE_INTERVAL_S - now)
            _trailing_publish = asyncio.get_running_loop().call_later(delay, _publish_trailing)
        return
    if _trailing_publish is not None:
        _trailing_publish.cancel()
        _trailing_publish = None
    _last_observer_publish = now
    _observer_publish_skipped = False
    _skipped_captured_cores.clear()
    states = [_state(core, at_breakpoint=core in breakpoint_cores, captured=core in captured_cores)
              for core in sim.cores]
    for connection in observers:
        for state in states:
            connection.publish({**state, "execution": _execution_state(connection)})
        for tag, addr_attr, len_attr in (("mem1", "_mem_addr", "_mem_len"),
                                         ("mem2", "_mem_addr2", "_mem_len2")):
            if hasattr(connection, addr_attr):
                addr, length = getattr(connection, addr_attr), getattr(connection, len_attr)
                try:
                    connection.publish({"type": "memory", "tag": tag, "addr": addr,
                                        "length": length, "data": list(sim.memory_read(addr, length))})
                except ValueError:
                    pass  # a new configuration may remove this tab's memory range


def _flush_skipped_publish(origin=None):
    """After a run ends, give observers the state a throttled chunk withheld."""
    if _observer_publish_skipped:
        # Keep the captured flag so recording tabs do not append a duplicate sample.
        _publish_updates(origin, captured_cores=set(_skipped_captured_cores))


def _publish_trailing():
    """Timer: the owner stopped sending chunks, so observers get the newest state anyway."""
    global _trailing_publish
    _trailing_publish = None
    # The owner already has this state from its last chunk reply; a second copy
    # would add a duplicate Signal Graph sample there.
    _flush_skipped_publish(_execution_owner)


def _state(core, at_breakpoint=False, captured=False):
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
        "gpio_loopback_mask": c.io_port.loopback_mask,
    }
    demo = ssi_demo_state(sim)
    if demo is not None:
        io_section["ssi_demo"] = demo
    if sim.device_bus.active:
        device_state = sim.device_state()
        io_section["device_bus"] = device_state
    if sd_data is not None:
        io_section["sd"] = sd_data
    if perif_data is not None:
        io_section["perif"] = perif_data
        io_section["loopback"] = sim.loopback_state()
    if i2c_data is not None:
        io_section["i2c"] = i2c_data
    state = {
        "type": "state",
        "available_cores": list(sim.cores),
        "clocks": _clock_speed_state(),
        "core": core,
        "pc": c.pc,
        "halted": c.halted,
        "fault": dict(c.fault) if c.fault is not None else None,
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
        "iep": {
            "clock_mhz": float(sim.iep.active_clock_mhz),
            "external_mhz": float(sim.iep.external_clock_hz / 1_000_000),
            "core_clock": bool(sim.iep.iepclk & sim.iep.IEPCLK_OCP_EN),
            "choices_mhz": list(IEP_CLOCK_CHOICES_MHZ),
            "configured_mhz": _configured_iep_mhz(),
            "override_mhz": _iep_clock_override,
        },
        "instructions": [{"addr": i.address, "text": i.source_text} for i in c.instructions],
        "labels": dict(c._parser.labels),   # name -> word address
        "spad": _read_spad(sim),
        "xfr_shift_en": sim.xfr.xfr_shift_en,
        "mac": _read_mac(c),
        "crc": _read_crc(c),
    }
    return state


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


def start_dashboard(host=DEFAULT_HOST, port=DEFAULT_PORT, reload=False):
    import uvicorn

    if not _port_is_free(host, port):
        raise SystemExit(
            f"Port {port} on {host} is already in use.\n"
            f"Start the dashboard on another port, e.g.:\n"
            f"    python ui/server.py --port {port + 1}\n"
            f"    PRU_SIM_UI_PORT={port + 1} python ui/server.py"
        )

    if reload:
        # Dev only: restart the backend on Python edits so it cannot go stale
        # behind static assets that are re-read from disk on every request.
        root = str(pathlib.Path(__file__).resolve().parents[1])
        uvicorn.run("ui.server:app", host=host, port=port, reload=True,
                    reload_dirs=[root], app_dir=root)
        return
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
    parser.add_argument(
        "--reload",
        action="store_true",
        help="dev only: restart the server when Python sources change",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    start_dashboard(host=args.host, port=args.port, reload=args.reload)
