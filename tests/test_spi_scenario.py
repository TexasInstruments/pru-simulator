"""SPI scenario: listing, state payload, session-only rate rebuild, websocket actions."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pru_io.scenarios import SCENARIOS, apply_scenario, spi_demo_state
from pru_io.spi_link import PATTERNS, SpiBusMonitor, load_supported
from simulator import Simulator
from tests.test_scenarios_ws import _receive
from ui.server import app

client = TestClient(app)
SPI = "SPI multi-core (controller + peripheral)"
SSI_MULTI = "SSI multi-core (emulator + reader)"
MEMORY_CFG = Path(__file__).parents[1] / "memory.cfg"


@pytest.fixture
def fresh_sim(monkeypatch):
    import ui.server as srv
    fresh = Simulator(config_path=srv.config_path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_device_api", srv.PRUSimulatorMCP(
        config_path=srv.config_path, simulator=fresh))
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    monkeypatch.setattr(srv, "_execution_owner", None)
    srv._clear_history()
    return fresh


def _drain_until(ws, kind):
    for _ in range(20):
        msg = _receive(ws)
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"no {kind} message")


def _state_after(ws, action, **kw):
    """Send *action*, return (error or None, the final pru0 state)."""
    ws.send_json({"action": action, **kw})
    error = None
    while True:
        msg = _receive(ws)
        if msg["type"] == "error":
            error = msg
        if msg["type"] == "state" and msg.get("core") == "pru0" and "io" in msg:
            return error, msg


def _load_spi(ws):
    ws.send_json({"action": "scenario_load", "name": SPI})
    loaded = _receive(ws)
    assert loaded["type"] == "scenario_loaded", loaded
    _receive(ws), _receive(ws)
    return loaded


def _spi(sim):
    return spi_demo_state(sim)


def test_scenario_listed_and_applies(fresh_sim):
    import ui.server as srv
    scenario = SCENARIOS[SPI]
    assert scenario.cores == ("pru0", "pru1") and scenario.lead == "pru0"
    assert scenario.multicore
    assert scenario.firmware == {"pru0": "spi/spi_controller.asm",
                                 "pru1": "spi/spi_peripheral.asm"}
    assert scenario.ui["io_tab"] == "spi" and scenario.ui["capture_stride"] == 1
    assert scenario.ui["view"] == "io"
    apply_scenario(fresh_sim, SPI, srv._device_api)
    st = _spi(fresh_sim)
    assert st["config"]["mode"] == 1 and st["config"]["spi_mhz"] == 25.0
    assert st["core_mhz"] == 200 and st["hop_delay"] == 5
    assert st["config"]["miso_delay"] > 0
    assert set(st["supported"]) == {0, 1} or set(st["supported"]) == {"0", "1"}
    assert st["frame"]["edges"] == []
    assert st["status"]["frames"] == 0
    # patterns cycle per block
    names = list(PATTERNS)
    for blk in (0, 1, 7, 0x7F):
        got = bytes(fresh_sim.memory_read(0x2800 + 16 * blk, 16))
        assert got == PATTERNS[names[blk % len(names)]][1]
    for core in ("pru0", "pru1"):
        assert fresh_sim.get_gpi_delay(core) == 5
    assert any(isinstance(d, SpiBusMonitor) for d in fresh_sim.device_bus.devices)


def test_apply_rejects_bad_config(fresh_sim):
    import ui.server as srv
    for cfg in ({"mode": 2, "spi_mhz": 25}, {"mode": 1, "spi_mhz": 7},
                {"mode": 1}, {"mode": 1, "spi_mhz": 25, "x": 1}, "no",
                {"mode": True, "spi_mhz": 25}):
        with pytest.raises(ValueError):
            apply_scenario(fresh_sim, SPI, srv._device_api, config=cfg)
    # ssi settings are not valid SPI settings
    with pytest.raises(ValueError):
        apply_scenario(fresh_sim, SPI, srv._device_api, config={"preset": "RM08_12BIT_4MHZ"})
    # a rate that needs another core clock than the simulator has
    with pytest.raises(ValueError, match="core"):
        apply_scenario(fresh_sim, SPI, srv._device_api, config={"mode": 1, "spi_mhz": 50})


def test_spi_send_round_trip_via_state(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        err, msg = _state_after(ws, "spi_send", cmd=3, addr=2, pattern="count")
        assert err is None
        st = msg["io"]["spi_demo"]
        assert st["match"] == {"mosi": 16, "miso": 16}
        c_tx, p_tx = PATTERNS["count"]
        assert bytes.fromhex("".join(st["memory"]["c_tx"])) == c_tx
        assert bytes.fromhex("".join(st["memory"]["p_rx"])) == c_tx
        assert bytes.fromhex("".join(st["memory"]["p_tx"])) == p_tx
        assert bytes.fromhex("".join(st["memory"]["c_rx"])) == p_tx
        fr = st["frame"]
        assert fr["edges"] and not fr["aborted"] and fr["bits"] == 152
        assert all(fr["start"] <= c <= fr["end"] for c, _ in fr["edges"][1:])
        assert st["timing"] and st["status"]["frames"] == 1
        assert st["last"] == {"cmd": 3, "addr": 2, "pattern": "count"}
        json.dumps(st)


def test_spi_send_errors_use_spi_tag(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        for kw in ({"cmd": 300, "addr": 0, "pattern": "count"},
                   {"cmd": 3, "addr": 0, "pattern": "nope"},
                   {"cmd": 3, "addr": "x", "pattern": "count"}):
            err, _ = _state_after(ws, "spi_send", **kw)
            assert err and err["tag"] == "spi"      # no SPI scenario loaded either
        _load_spi(ws)
        err, _ = _state_after(ws, "spi_send", cmd=3, addr=0, pattern="nope")
        assert err and err["tag"] == "spi"
        err, _ = _state_after(ws, "spi_setup", mode=1, spi_mhz=7)
        assert err and err["tag"] == "spi"


def test_spi_sequence_via_state(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        err, msg = _state_after(ws, "spi_sequence")
        assert err is None
        seq = msg["io"]["spi_demo"]["sequence"]
        assert seq["ok"] is True and seq["byte_errors"] == 0 and seq["steps"]


def test_spi_setup_session_only(fresh_sim):
    import ui.server as srv
    before = hashlib.sha256(MEMORY_CFG.read_bytes()).hexdigest()
    rows = load_supported()[1]
    fast = next((r for r in rows if r["spi_mhz"] == 50.0), rows[-1])
    assert fast["core_mhz"] != 200
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        old = srv.sim
        err, msg = _state_after(ws, "spi_setup", mode=1, spi_mhz=fast["spi_mhz"])
        assert err is None
        assert srv.sim is not old
        assert srv.sim.cores["pru0"].clock_mhz == fast["core_mhz"]
        assert srv.sim.cores["pru1"].clock_mhz == fast["core_mhz"]
        st = msg["io"]["spi_demo"]
        assert st["core_mhz"] == fast["core_mhz"]
        assert st["config"]["spi_mhz"] == pytest.approx(fast["spi_mhz"])
        assert st["config"]["miso_delay"] == fast["miso_delay"]
        err, msg = _state_after(ws, "spi_send", cmd=3, addr=5, pattern="a5_5a")
        assert msg["io"]["spi_demo"]["match"] == {"mosi": 16, "miso": 16}
    assert hashlib.sha256(MEMORY_CFG.read_bytes()).hexdigest() == before


def test_spi_setup_mode0(fresh_sim):
    import ui.server as srv
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        err, msg = _state_after(ws, "spi_setup", mode=0, spi_mhz=10.0)
        assert err is None and msg["io"]["spi_demo"]["config"]["mode"] == 0
        err, msg = _state_after(ws, "spi_send", cmd=3, addr=1, pattern="walking1")
        assert msg["io"]["spi_demo"]["match"] == {"mosi": 16, "miso": 16}


def test_spi_setup_while_running_stops_and_clears(fresh_sim):
    import ui.server as srv
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        _state_after(ws, "spi_send", cmd=3, addr=2, pattern="count")
        srv._execution_owner = object()
        srv._execution_mode = "run"
        err, msg = _state_after(ws, "spi_setup", mode=1, spi_mhz=25.0)
        assert err is None
        assert srv._execution_owner is None
        assert msg["execution"]["running"] is False
        assert msg["io"]["spi_demo"]["frame"]["edges"] == []


def test_leaving_spi_scenario_restores_defaults(fresh_sim):
    import ui.server as srv
    apply_scenario(fresh_sim, SPI, srv._device_api)
    apply_scenario(fresh_sim, SSI_MULTI, srv._device_api)
    for core in ("pru0", "pru1"):
        assert fresh_sim.get_gpi_delay(core) == 0
    assert not any(isinstance(d, SpiBusMonitor) for d in fresh_sim.device_bus.devices)
    pins = {frozenset({(w["core_a"], w["pin_a"]), (w["core_b"], w["pin_b"])})
            for w in fresh_sim.list_gpio_wires()}
    assert pins == {frozenset({("pru0", 0), ("pru1", 0)}),
                    frozenset({("pru1", 16), ("pru0", 16)})}   # only the SSI wires remain
    assert spi_demo_state(fresh_sim) is None


@pytest.mark.parametrize("action", ["load", "reset", "hard_reset", "scenario_load"])
def test_ws_paths_leaving_spi_close_runtime(fresh_sim, action):
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        assert fresh_sim.get_gpi_delay("pru1") == 5
        extra = {"load": {"source": "nop\n halt\n"}, "scenario_load": {"name": SSI_MULTI}}.get(action, {})
        ws.send_json({"action": action, **extra})
        while True:
            msg = _receive(ws)
            if msg["type"] == "state":
                break
        for core in ("pru0", "pru1"):
            assert fresh_sim.get_gpi_delay(core) == 0
        assert not any(isinstance(d, SpiBusMonitor) for d in fresh_sim.device_bus.devices)
        assert "spi_demo" not in msg["io"]


def test_ssi_state_has_no_spi_demo(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "scenario_load", "name": SSI_MULTI})
        _receive(ws)
        msg = _receive(ws)
        assert "spi_demo" not in msg["io"] and "ssi_demo" in msg["io"]
