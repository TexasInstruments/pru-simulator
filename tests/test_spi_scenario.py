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


def _use_config(monkeypatch, path):
    """Point the server at a throw-away config (``_configured_clocks`` and
    ``_build_session_sim`` read ``srv.config_path``) and a fresh simulator
    built from it. The repo memory.cfg is never used: its clock is whatever
    the user last picked in the dashboard."""
    import ui.server as srv
    monkeypatch.setattr(srv, "config_path", path)
    fresh = Simulator(config_path=path)
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_device_api", srv.PRUSimulatorMCP(
        config_path=path, simulator=fresh))
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    monkeypatch.setattr(srv, "_execution_owner", None)
    monkeypatch.setattr(srv, "_execution_cores", ["pru0"])
    srv._clear_history()
    return fresh


@pytest.fixture
def fresh_sim(monkeypatch, sim_config):
    """Server simulator at a configured 200 MHz: the default SPI rate (25 MHz
    mode 1) needs no rebuild. Assert on ``srv.sim`` after any load that may
    rebuild the simulator; this one is then discarded."""
    return _use_config(monkeypatch, sim_config(pru_clock_mhz=200, pru1_clock_mhz=200))


@pytest.fixture
def sim_250(monkeypatch, sim_config):
    """Server simulator at a configured 250 MHz (the committed memory.cfg's
    clock): loading the SPI default rate must rebuild at 200 MHz."""
    return _use_config(monkeypatch, sim_config(pru_clock_mhz=250, pru1_clock_mhz=250))


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
    assert "supported" not in st          # Ruling R14a: sent once, not in every push
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


_LEAVE_EXTRA = {"load": {"source": "nop\n halt\n"}, "scenario_load": {"name": SSI_MULTI},
                "set_execution_cores": {"cores": ["pru0"]}}


@pytest.mark.parametrize("action", ["load", "scenario_load", "set_execution_cores"])
def test_ws_paths_leaving_spi_close_runtime(fresh_sim, action):
    import ui.server as srv
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        assert srv.sim.get_gpi_delay("pru1") == 5
        ws.send_json({"action": action, **_LEAVE_EXTRA.get(action, {})})
        while True:
            msg = _receive(ws)
            if msg["type"] == "state":
                break
        for core in ("pru0", "pru1"):
            assert srv.sim.get_gpi_delay(core) == 0
        assert not any(isinstance(d, SpiBusMonitor) for d in srv.sim.device_bus.devices)
        assert srv.sim.list_gpio_wires() == [] or action == "scenario_load"
        assert "spi_demo" not in msg["io"]
        assert spi_demo_state(srv.sim) is None


def test_set_execution_cores_keeping_both_prus_keeps_spi(fresh_sim):
    import ui.server as srv
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        ws.send_json({"action": "set_execution_cores", "cores": ["pru1", "pru0"]})
        _drain_until(ws, "execution")
        assert spi_demo_state(srv.sim) is not None
        assert srv.sim.get_gpi_delay("pru0") == 5


@pytest.mark.parametrize("action", ["reset", "hard_reset"])
def test_reset_keeps_spi_demo(fresh_sim, action):
    """Ruling R21: Reset / HW Reset re-apply the SPI scenario at the session
    config (same clock, fresh time base) instead of leaving it."""
    import ui.server as srv
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        err, msg = _state_after(ws, "spi_setup", mode=1, spi_mhz=25.0, miso_delay=18)
        assert err is None
        _state_after(ws, "spi_send", cmd=3, addr=2, pattern="count")
        session = srv.sim
        err, msg = _state_after(ws, action)
        assert err is None
        st = msg["io"]["spi_demo"]
        assert st["config"]["miso_delay"] == 18 and st["config"]["spi_mhz"] == 25.0
        assert st["frame"]["edges"] == [] and st["status"]["frames"] == 0
        assert st["last"] is None
        assert srv.sim is session and srv.sim.cores["pru0"].counters.cycles == 0
        for core in ("pru0", "pru1"):
            assert srv.sim.get_gpi_delay(core) == 5
        err, msg = _state_after(ws, "spi_send", cmd=3, addr=4, pattern="a5_5a")
        assert msg["io"]["spi_demo"]["match"] == {"mosi": 16, "miso": 16}


def test_ssi_state_has_no_spi_demo(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "scenario_load", "name": SSI_MULTI})
        _receive(ws)
        msg = _receive(ws)
        assert "spi_demo" not in msg["io"] and "ssi_demo" in msg["io"]


def test_supported_table_sent_once_not_in_state(fresh_sim):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "scenario_load", "name": SPI})
        loaded = _receive(ws)
        assert set(loaded["spi_supported"]) == {"0", "1"}
        assert loaded["spi_supported"]["1"][0]["mode"] == 1
        states = [_receive(ws), _receive(ws)]
        assert all("supported" not in m["io"]["spi_demo"] for m in states if m["core"] == "pru0")
        err, msg = _state_after(ws, "spi_setup", mode=0, spi_mhz=10.0)
        assert "supported" not in msg["io"]["spi_demo"]
        ws.send_json({"action": "spi_supported"})
        reply = _drain_until(ws, "spi_supported")
        assert reply["supported"] == loaded["spi_supported"]


@pytest.mark.parametrize("leave", ["scenario_load", "load", "set_execution_cores"])
def test_leaving_after_setup_restores_configured_clock(fresh_sim, leave):
    import ui.server as srv
    before = hashlib.sha256(MEMORY_CFG.read_bytes()).hexdigest()
    configured = srv._configured_clocks()
    fast = next(r for r in load_supported()[1] if r["core_mhz"] != configured[0])
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        _state_after(ws, "spi_setup", mode=1, spi_mhz=fast["spi_mhz"])
        assert srv.sim.cores["pru0"].clock_mhz == fast["core_mhz"]
        ws.send_json({"action": leave, **_LEAVE_EXTRA.get(leave, {})})
        while _receive(ws)["type"] != "state":
            pass
        assert (srv.sim.cores["pru0"].clock_mhz, srv.sim.cores["pru1"].clock_mhz) == configured
        assert srv.sim.get_gpi_delay("pru0") == 0
    assert hashlib.sha256(MEMORY_CFG.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("action", ["reset", "hard_reset"])
def test_reset_after_setup_keeps_session_clock(fresh_sim, action):
    import ui.server as srv
    fast = next(r for r in load_supported()[1] if r["core_mhz"] != 200)
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        _state_after(ws, "spi_setup", mode=1, spi_mhz=fast["spi_mhz"])
        err, msg = _state_after(ws, action)
        assert err is None
        assert srv.sim.cores["pru0"].clock_mhz == fast["core_mhz"]
        assert msg["io"]["spi_demo"]["config"]["spi_mhz"] == pytest.approx(fast["spi_mhz"])


def test_load_at_250_rebuilds_then_restores(sim_250):
    """Configured 250 MHz: the default 25 MHz rate needs 200 MHz, so loading
    the scenario rebuilds the simulator (session only), and leaving restores
    the configured 250 MHz. The configured simulator itself refuses the rate."""
    import ui.server as srv
    with pytest.raises(ValueError, match="200 MHz core"):
        apply_scenario(sim_250, SPI, srv._device_api)
    cfg_before = Path(srv.config_path).read_bytes()
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        assert srv.sim is not sim_250
        assert srv.sim.cores["pru0"].clock_mhz == 200 and srv.sim.cores["pru1"].clock_mhz == 200
        err, msg = _state_after(ws, "spi_send", cmd=3, addr=6, pattern="toggle")
        assert err is None and msg["io"]["spi_demo"]["match"] == {"mosi": 16, "miso": 16}
        assert msg["io"]["spi_demo"]["core_mhz"] == 200
        err, msg = _state_after(ws, "reset")
        assert "spi_demo" in msg["io"] and srv.sim.cores["pru0"].clock_mhz == 200
        ws.send_json({"action": "load", "source": "nop\n halt\n"})
        while _receive(ws)["type"] != "state":
            pass
        assert (srv.sim.cores["pru0"].clock_mhz, srv.sim.cores["pru1"].clock_mhz) == (250, 250)
    assert Path(srv.config_path).read_bytes() == cfg_before


def test_spi_setup_miso_delay(fresh_sim):
    """Ruling R17: spi_setup takes an optional realisable miso_delay."""
    import ui.server as srv
    row = next(r for r in load_supported()[1] if r["spi_mhz"] == 25.0)
    other = next(d for d in row["miso_delay_pass"] if d != row["miso_delay"])
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)
        err, msg = _state_after(ws, "spi_setup", mode=1, spi_mhz=25.0, miso_delay=other)
        assert err is None
        st = msg["io"]["spi_demo"]
        assert st["config"]["miso_delay"] == other
        err, msg = _state_after(ws, "spi_send", cmd=3, addr=1, pattern="count")
        assert msg["io"]["spi_demo"]["match"] == {"mosi": 16, "miso": 16}
        session = srv.sim
        k = row["half_k"]
        for bad in (0, k, 2 * k, 2 * k * 7 + 1, -1, "x", True, 2.5):
            err, msg = _state_after(ws, "spi_setup", mode=1, spi_mhz=25.0, miso_delay=bad)
            assert err and err["tag"] == "spi", bad
            assert srv.sim is session                      # nothing changed
            assert msg["io"]["spi_demo"]["config"]["miso_delay"] == other
        # without miso_delay a setup goes back to the row's chosen value
        err, msg = _state_after(ws, "spi_setup", mode=1, spi_mhz=25.0)
        assert msg["io"]["spi_demo"]["config"]["miso_delay"] == row["miso_delay"]


def test_apply_accepts_miso_delay(fresh_sim):
    import ui.server as srv
    apply_scenario(fresh_sim, SPI, srv._device_api,
                   config={"mode": 1, "spi_mhz": 25.0, "miso_delay": 15})
    assert spi_demo_state(fresh_sim)["config"]["miso_delay"] == 15
    with pytest.raises(ValueError, match="miso_delay"):
        apply_scenario(fresh_sim, SPI, srv._device_api,
                       config={"mode": 1, "spi_mhz": 25.0, "miso_delay": 4})


def test_spi_view_reports_measured_fetch(fresh_sim):
    """The tIDLE row shows PRU1's single-stepped fetch, not the derived budget."""
    import ui.server as srv
    from pru_io.scenarios import spi_send
    apply_scenario(fresh_sim, SPI, srv._device_api)
    spi_send(fresh_sim, 3, 2, "count")
    idle = next(r for r in spi_demo_state(fresh_sim)["timing"] if r["symbol"] == "tIDLE")
    assert idle["derived"] is False and idle["cycles"] == 12


def test_clock_speed_group0_pins_configured_not_session_clock(monkeypatch, tmp_path):
    """put_clock_speed (group 0) pins an inherited pru1 clock at the configured
    rate, never at an SPI session's clock."""
    import configparser
    import shutil
    import ui.server as srv
    cfg = configparser.ConfigParser()
    cfg.read(MEMORY_CFG)
    cfg["device"]["pru_clock_mhz"] = "250"
    cfg.remove_option("device", "pru1_clock_mhz")
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    shutil.copytree(MEMORY_CFG.parent / "config", tmp_path / "config")
    _use_config(monkeypatch, str(path))
    with client.websocket_connect("/ws") as ws:
        _load_spi(ws)                      # session sim at 200 MHz
        assert srv.sim._pru1_clock_mhz == 200
    res = client.put("/config/clock_speed", json={"mhz": 300, "group": 0})
    assert res.status_code == 200, res.text
    out = configparser.ConfigParser()
    out.read(path)
    assert out["device"]["pru_clock_mhz"] == "300"
    assert out["device"]["pru1_clock_mhz"] == "250"


def test_monitor_edges_bounded_and_timing_survives(fresh_sim):
    import ui.server as srv
    from pru_io.scenarios import spi_send
    apply_scenario(fresh_sim, SPI, srv._device_api)
    rt = fresh_sim._scenario_runtime
    counts = []
    for i in range(20):
        spi_send(fresh_sim, 3, i, "count")
        counts.append(len(rt.monitor.edges))
    assert counts[-1] == counts[-2] or max(counts[10:]) <= max(counts[:10]) + 2
    assert len(rt.monitor.frames()) == rt.monitor.KEEP_FRAMES
    assert rt.peripheral_status()["frames"] == 20
    st = spi_demo_state(fresh_sim)
    assert st["match"] == {"mosi": 16, "miso": 16}
    from pru_io.spi_link import measure_timing
    rows = {r["symbol"]: r for r in measure_timing(
        rt.monitor.edges, rt.cfg, frame_index=-2, hop_delay=rt.hop_delay)}
    assert rows["tCSH"]["sim_ns"] is not None
