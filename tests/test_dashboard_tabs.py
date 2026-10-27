"""Two browser connections observe one simulator without polling or re-running it."""
import asyncio
import json
from types import SimpleNamespace

import anyio
import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from ui import server as srv


def test_missing_crc_accelerator_is_not_presented_as_a_real_zero_result():
    assert srv._read_crc(SimpleNamespace(accelerators={})) == {"mode": "—", "value": "—"}


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    fresh = Simulator(srv.config_path)
    cfg = tmp_path / "memory.cfg"
    cfg.write_bytes(open(srv.config_path, "rb").read())
    monkeypatch.setattr(srv, "config_path", str(cfg))
    monkeypatch.setattr(srv, "sim", fresh)
    monkeypatch.setattr(srv, "_iep_clock_override", None)
    monkeypatch.setattr(srv, "_execution_cores", ["pru0"])
    monkeypatch.setattr(srv, "_execution_owner", None)
    monkeypatch.setattr(srv, "_execution_mode", None)
    srv._clear_history()
    with TestClient(srv.app) as client:
        yield client


def receive(ws):
    async def bounded():
        with anyio.fail_after(3):
            return await ws._send_rx.receive()
    message = ws.portal.call(bounded)
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])


def until(ws, predicate):
    for _ in range(40):
        message = receive(ws)
        if predicate(message):
            return message
    pytest.fail("expected live update was not received")


def state(ws, core="pru0"):
    return until(ws, lambda msg: msg["type"] == "state" and msg["core"] == core)


def test_load_step_input_and_reset_are_visible_in_an_idle_second_tab(dashboard):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "load", "source": "ldi r0, 42\nhalt"})
        loaded = state(control)
        assert state(view)["instructions"] == loaded["instructions"]
        control.send_json({"action": "step"})
        stepped = state(control)
        live = state(view)
        assert live["pc"] == stepped["pc"] == 1
        assert live["cycles"] == stepped["cycles"]
        assert live["registers"][0] == "0x0000002A"
        control.send_json({"action": "set_input", "pin": 16, "value": 1})
        state(control)
        assert state(view)["io"]["gpi_pins"][16] == 1
        # Either tab may change the shared simulator.
        view.send_json({"action": "reset"})
        reset = state(view)
        assert state(control)["pc"] == reset["pc"] == 0


@pytest.mark.parametrize("name,partners", [
    ("SSI encoder (single core)", []),
    ("SSI multi-core (emulator + reader)", ["pru1"]),
])
def test_ssi_signal_capture_and_mailbox_reach_a_recording_observer(dashboard, name, partners):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        view.send_json({"action": "subscribe_capture", "enabled": True})
        assert receive(view)["type"] == "capture_subscription"
        control.send_json({"action": "scenario_load", "name": name})
        until(control, lambda msg: msg["type"] == "scenario_loaded")
        for core in ["pru0", *partners]:
            state(control, core)
        state(view)
        control.send_json({"action": "run_multicore" if partners else "run",
                           "partners": partners, "max_steps": 20_000, "stride": None,
                           "capture": False, "run_id": 77})
        capture = until(view, lambda msg: msg["type"] == "capture")
        assert {sample[1] & 1 for sample in capture["samples"]} == {0, 1}, (len(capture["samples"]), capture["samples"][:5])
        steps = [sample[0] for sample in capture["samples"]]
        assert all(b - a <= 2 for a, b in zip(steps, steps[1:])), "SSI capture must retain the example sampling rate across tabs"
        assert capture.get("partners", []) == partners
        live = state(view)
        assert "run_id" not in live, "another tab's reply must not release a local pending command"
        assert live["captured"] is True
        assert live["io"]["ssi_demo"]["position"] == 2748
        assert live["io"]["ssi_demo"]["frame_count"] >= 2
        current = state(control)
        assert current["cycles"] == live["cycles"]
        assert current["registers"] == live["registers"]


def test_clock_config_change_reaches_open_tabs_without_reload(dashboard):
    with dashboard.websocket_connect("/ws") as view:
        response = dashboard.put("/config/clock_speed", json={"mhz": 250, "group": 0})
        assert response.status_code == 200
        assert state(view)["clocks"]["groups"][0]["mhz"] == 250


def test_memory_updates_use_each_tabs_own_address(dashboard):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        view.send_json({"action": "read_memory", "addr": 16, "length": 4, "tag": "mem2"})
        assert receive(view)["data"] == [0, 0, 0, 0]
        control.send_json({"action": "write_memory", "addr": 16, "data": [1, 2, 3, 4]})
        assert receive(control)["type"] == "memory_written"
        live = until(view, lambda msg: msg["type"] == "memory")
        assert (live["tag"], live["addr"], live["data"]) == ("mem2", 16, [1, 2, 3, 4])


def test_run_status_and_stop_are_shared_and_second_tab_cannot_double_run(dashboard):
    assert srv.sim.load("pru0", "add r0, r0, 1\nqba 0") == []
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "start_execution", "mode": "run"})
        assert receive(control) == {"type": "execution", "running": True, "owner": True, "mode": "run",
                                    "cores": ["pru0"], "scenario": None}
        shared = until(view, lambda msg: msg["type"] == "execution")
        assert shared["running"] and not shared["owner"]
        view.send_json({"action": "run", "max_steps": 10, "run_id": 1})
        until(view, lambda msg: msg["type"] == "error")
        reply = state(view)
        assert reply["cycles"] == 0 and reply["run_id"] == 1
        control.send_json({"action": "run", "managed": True, "max_steps": 10, "run_id": 1})
        progressed = state(control)
        assert progressed["instruction_count"] == 10
        assert state(view)["instruction_count"] == 10
        with dashboard.websocket_connect("/ws") as late:
            late.send_json({"action": "get_state"})
            joined = state(late)
            assert joined["instruction_count"] == 10
            assert joined["registers"] == progressed["registers"]
            assert srv.sim.cores["pru0"].counters.instruction_count == 10
        view.send_json({"action": "stop_execution"})
        assert until(control, lambda msg: msg["type"] == "execution")["running"] is False
        assert until(view, lambda msg: msg["type"] == "execution")["running"] is False
        # A chunk queued by the previous timer must not restart a stopped run.
        control.send_json({"action": "run", "managed": True, "max_steps": 10, "run_id": 2})
        assert state(control)["instruction_count"] == 10


def test_breakpoint_status_is_the_same_in_both_views(dashboard):
    assert srv.sim.load("pru0", "nop\nhalt") == []
    srv.sim.cores["pru0"].breakpoints.add(1)
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "run"})
        stopped = state(control)
        observed = state(view)
        assert stopped["at_breakpoint"] is observed["at_breakpoint"] is True
        assert stopped["pc"] == observed["pc"] == 1


def test_new_tab_sees_running_status_and_owner_disconnect_stops_it(dashboard):
    with dashboard.websocket_connect("/ws") as view:
        with dashboard.websocket_connect("/ws") as control:
            control.send_json({"action": "start_execution", "mode": "sim"})
            assert receive(control)["running"] is True
            until(view, lambda msg: msg["type"] == "execution")
            with dashboard.websocket_connect("/ws") as late:
                late.send_json({"action": "get_state"})
                assert state(late)["execution"] == {"type": "execution", "running": True, "owner": False, "mode": "sim",
                                                    "cores": ["pru0"], "scenario": None}
        assert until(view, lambda msg: msg["type"] == "execution")["running"] is False


@pytest.mark.parametrize("cores", [
    ["pru0", "pru1"], ["pru0", "pru1", "rtu0"],
    ["pru0", "pru1", "rtu0", "rtu1"],
])
def test_manual_multicore_steps_each_have_one_shared_undo(dashboard, cores):
    # Unequal rates make a follower execute more than one instruction per click.
    assert dashboard.put("/config/clock_speed", json={"mhz": 200, "group": 0}).status_code == 200
    assert dashboard.put("/config/clock_speed", json={"mhz": 300, "group": 1}).status_code == 200
    for index, core in enumerate(cores):
        assert srv.sim.load(core, "add r0, r0, 1\nsbbo &r0, r2, 0, 4\nqba 0") == []
        srv.sim.cores[core].registers.write_full(2, 0x10000 + index * 4)
    with dashboard.websocket_connect("/ws") as ws:
        # This older follower-only event must not be undone by the same keypress.
        ws.send_json({"action": "step", "core": "pru1"})
        state(ws, "pru1")
        checkpoints = []
        for run_id in range(3):
            checkpoints.append(srv._snapshot("pru0"))
            ws.send_json({"action": "step_multicore", "core": cores[0],
                          "partners": cores[1:], "run_id": run_id})
            for core in cores:
                result = state(ws, core)
            assert result["run_id"] == run_id
        assert srv.sim.cores["pru0"].counters.instruction_count == 3
        for checkpoint in reversed(checkpoints):
            ws.send_json({"action": "step_back", "core": cores[0]})
            for core in srv.sim.cores:
                state(ws, core)
            assert srv._snapshot("pru0") == checkpoint
        assert srv.sim.cores["pru1"].counters.instruction_count == 1


def test_manual_multicore_step_captures_once_for_a_recording_observer(dashboard):
    for core in ("pru0", "pru1"):
        assert srv.sim.load(core, "xor r30, r30, 1\nqba 0") == []
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        view.send_json({"action": "subscribe_capture", "enabled": True})
        assert receive(view)["type"] == "capture_subscription"
        for run_id in range(3):
            control.send_json({"action": "step_multicore", "core": "pru0",
                               "partners": ["pru1"], "run_id": run_id})
            # A single manual step must not be lost to the normal stride of 100.
            capture = receive(view)
            assert capture["type"] == "capture"
            assert capture["partners"] == ["pru1"]
            assert len(capture["samples"]) == 1
            assert capture["samples"][0][0] == run_id + 1
            assert capture["samples"][0][1] == capture["samples"][0][6]
            for core in srv.sim.cores:
                live = state(view, core)
                assert live["captured"] is (core in {"pru0", "pru1"})
            state(control)
            assert state(control, "pru1")["run_id"] == run_id


def test_manual_multicore_step_respects_the_shared_run_owner_and_stop(dashboard):
    for core in ("pru0", "pru1"):
        assert srv.sim.load(core, "nop\nqba 0") == []
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "set_execution_cores", "cores": ["pru0", "pru1"]})
        assert receive(control)["cores"] == ["pru0", "pru1"]
        until(view, lambda msg: msg["type"] == "execution")
        control.send_json({"action": "start_execution", "mode": "sim"})
        assert receive(control)["owner"] is True
        until(view, lambda msg: msg["type"] == "execution")
        view.send_json({"action": "step_multicore", "partners": ["pru1"], "run_id": 1})
        assert receive(view)["type"] == "error"
        assert state(view)["run_id"] == 1
        assert srv.sim.cores["pru0"].counters.instruction_count == 0
        control.send_json({"action": "step_multicore", "partners": ["pru1"],
                           "managed": True, "run_id": 2})
        assert state(control)["instruction_count"] == 1
        assert state(control, "pru1")["run_id"] == 2
        for core in srv.sim.cores:
            state(view, core)
        view.send_json({"action": "stop_execution"})
        assert until(control, lambda msg: msg["type"] == "execution")["running"] is False
        control.send_json({"action": "step_multicore", "partners": ["pru1"],
                           "managed": True, "run_id": 3})
        assert receive(control)["type"] == "error"
        stopped = state(control)
        assert (stopped["instruction_count"], stopped["run_id"]) == (1, 3)


def test_manual_step_captures_a_pending_followers_progress_even_without_a_new_lead_step(dashboard):
    for core in ("pru0", "pru1"):
        assert srv.sim.load(core, "xor r30, r30, 1\nqba 0") == []
    lead = srv.sim.cores["pru0"]
    lead.counters.cycles = lead.counters.instruction_count = 5000
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        view.send_json({"action": "subscribe_capture", "enabled": True})
        assert receive(view)["type"] == "capture_subscription"
        for run_id in (1, 2):
            control.send_json({"action": "step_multicore", "partners": ["pru1"], "run_id": run_id})
            capture = receive(view)
            assert capture["type"] == "capture"
            assert len(capture["samples"]) == 1
            assert capture["samples"][0][0] == 5001
            for core in srv.sim.cores:
                live = state(view, core)
                assert live["captured"] is (core in {"pru0", "pru1"})
            state(control)
            assert state(control, "pru1")["run_id"] == run_id
        assert srv.sim.cores["pru1"].counters.instruction_count == 2000


def test_explicit_state_refresh_is_marked_as_a_view_without_advancing_the_simulator(dashboard):
    assert srv.sim.load("pru0", "nop\nhalt") == []
    with dashboard.websocket_connect("/ws") as ws:
        ws.send_json({"action": "get_state"})
        refreshed = state(ws)
        assert refreshed["view_only"] is True
        assert refreshed["instruction_count"] == 0
        ws.send_json({"action": "step"})
        stepped = state(ws)
        assert not stepped.get("view_only", False)
        assert stepped["instruction_count"] == 1


@pytest.mark.parametrize("mutation", ["reset", "load"])
@pytest.mark.parametrize("incomplete", [False, True])
def test_slow_controller_cannot_publish_old_capture_after_another_tabs_mutation(
        dashboard, monkeypatch, mutation, incomplete):
    from ui.live_sessions import DashboardConnection
    assert srv.sim.load("pru0", "xor r30, r30, 1\nqba 0") == []
    connections = []
    original_init = DashboardConnection.__init__
    events = {}
    if incomplete:
        assert srv.sim.load("pru1", "xor r30, r30, 1\nqba 0") == []
        # A late follower cannot catch up within one bounded pacing call.
        lead = srv.sim.cores["pru0"]
        lead.counters.cycles = lead.counters.instruction_count = 5000

    def delayed_connection(self, websocket):
        original_init(self, websocket)
        connections.append(self)
        if len(connections) != 1:
            return
        original_send = websocket.send_json

        async def delayed_send(message, *args, **kwargs):
            if message.get("type") == ("error" if incomplete else "capture"):
                events["entered"].set()
                await events["release"].wait()
            await original_send(message, *args, **kwargs)
        websocket.send_json = delayed_send

    monkeypatch.setattr(DashboardConnection, "__init__", delayed_connection)
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        async def prepare():
            events.update(entered=asyncio.Event(), release=asyncio.Event())
        control.portal.call(prepare)
        view.send_json({"action": "subscribe_capture", "enabled": True})
        assert receive(view)["type"] == "capture_subscription"
        control.send_json({"action": "run_multicore" if incomplete else "run",
                           "partners": ["pru1"], "max_steps": 100, "stride": 1})

        async def wait_entered():
            with anyio.fail_after(1):
                await events["entered"].wait()
        control.portal.call(wait_entered)
        try:
            view.send_json({"action": mutation, "source": "ldi r0, 42\nhalt"})
            messages = []
            while True:
                message = receive(view)
                messages.append(message)
                if message["type"] == "state" and message["core"] == "pru0":
                    break
            if mutation == "reset":
                assert messages[-1]["pc"] == 0
            else:
                assert messages[-1]["instructions"][0]["text"] == "ldi r0, 42"
            expected_count = 0 if mutation == "reset" else 5001 if incomplete else 100
            assert messages[-1]["instruction_count"] == expected_count
            assert [msg["type"] for msg in messages] == ["capture", "state"]
        finally:
            control.portal.call(events["release"].set)
        # Once the controlling socket catches up it must publish only fresh state.
        assert state(control)["instruction_count"] == expected_count
        assert receive(view)["type"] == "state"


MULTI_SSI = "SSI multi-core (emulator + reader)"


def test_scenario_selection_is_shared_with_observers_and_late_joiners(dashboard):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "scenario_load", "name": MULTI_SSI})
        loaded = until(control, lambda msg: msg["type"] == "scenario_loaded")
        current = state(control)
        observed = receive(view)
        assert observed["type"] == "state", "scenario UI navigation belongs to its requester"
        for message in (current, observed):
            assert message["execution"]["cores"] == ["pru0", "pru1"]
            assert message["execution"]["scenario"] == {key: value for key, value in loaded.items() if key != "type"}
        counts = {name: pru.counters.instruction_count for name, pru in srv.sim.cores.items()}
        with dashboard.websocket_connect("/ws") as late:
            late.send_json({"action": "get_state", "core": "rtu0"})
            joined = state(late, "rtu0")
            assert joined["execution"]["cores"] == ["pru0", "pru1"]
            assert joined["execution"]["scenario"]["name"] == MULTI_SSI
            assert {name: pru.counters.instruction_count for name, pru in srv.sim.cores.items()} == counts


def test_other_tab_runs_loaded_ssi_selection_despite_stale_single_core_request(dashboard):
    with dashboard.websocket_connect("/ws") as loader, dashboard.websocket_connect("/ws") as runner:
        loader.send_json({"action": "scenario_load", "name": MULTI_SSI})
        until(loader, lambda msg: msg["type"] == "scenario_loaded")
        state(loader)
        state(loader, "pru1")
        for name in srv.sim.cores:
            state(runner, name)
        runner.send_json({"action": "start_execution", "mode": "run"})
        started = receive(runner)
        assert started["running"] and started["owner"]
        runner.send_json({"action": "run", "managed": True, "core": "pru0",
                          "partners": [], "max_steps": 20_000, "run_id": 91})
        completed = until(runner, lambda msg: msg.get("run_id") == 91)
        assert completed["core"] == "pru1"
        assert completed["instruction_count"] > 0
        loader.send_json({"action": "get_state"})
        observed = until(loader, lambda msg: msg["type"] == "state" and msg["core"] == "pru0"
                         and msg["io"]["ssi_demo"]["position"] == 2748)
        assert observed["io"]["ssi_demo"]["frame_count"] >= 2
        loader.send_json({"action": "stop_execution"})
        assert until(runner, lambda msg: msg["type"] == "execution" and not msg["running"])["cores"] == ["pru0", "pru1"]


def test_execution_selection_is_canonical_shared_and_stops_an_active_run(dashboard):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        control.send_json({"action": "start_execution", "mode": "sim"})
        assert receive(control)["running"]
        until(view, lambda msg: msg["type"] == "execution")
        view.send_json({"action": "set_execution_cores", "cores": ["rtu1", "pru1", "pru0"]})
        changed = until(view, lambda msg: msg["type"] == "execution")
        assert changed["cores"] == ["pru0", "pru1", "rtu1"]
        assert changed["running"] is False
        assert changed["scenario"] is None
        assert until(control, lambda msg: msg["type"] == "execution")["cores"] == changed["cores"]


@pytest.mark.parametrize("cores", [None, [], "pru0", ["pru0", "pru0"], ["missing"], [1], [True], {}])
def test_invalid_execution_selection_preserves_the_running_session(dashboard, cores):
    with dashboard.websocket_connect("/ws") as ws:
        ws.send_json({"action": "start_execution", "mode": "run"})
        assert receive(ws)["running"]
        ws.send_json({"action": "set_execution_cores", "cores": cores})
        assert receive(ws)["type"] == "error"
        ws.send_json({"action": "get_state"})
        execution = state(ws)["execution"]
        assert execution["running"] and execution["owner"]
        assert execution["cores"] == ["pru0"]


@pytest.mark.parametrize("selected,expected", [(["pru1", "rtu1"], ["pru1"]), (["rtu1"], ["pru0"])])
def test_target_replacement_prunes_execution_selection_and_keeps_it_nonempty(dashboard, selected, expected):
    with dashboard.websocket_connect("/ws") as ws:
        ws.send_json({"action": "set_execution_cores", "cores": selected})
        assert receive(ws)["type"] == "execution"
        text = open(srv.config_path).read().replace("target = AM243x", "target = AM261x")
        assert dashboard.put("/config", content=text).status_code == 200
        changed = until(ws, lambda msg: msg["type"] == "execution")
        assert changed["cores"] == expected
        assert changed["scenario"] is None
        ws.send_json({"action": "get_state"})
        assert state(ws)["execution"]["cores"] == expected


@pytest.mark.parametrize("action,selected", [("step", ["pru0", "pru1"]), ("step_multicore", ["pru1"]), ("run_multicore", ["pru1"])])
def test_managed_chunks_execute_shared_cores_instead_of_request_core_and_partners(dashboard, action, selected):
    for name in ("pru0", "pru1"):
        assert srv.sim.load(name, "add r0, r0, 1\nqba 0") == []
    with dashboard.websocket_connect("/ws") as ws:
        ws.send_json({"action": "set_execution_cores", "cores": selected})
        assert receive(ws)["type"] == "execution"
        ws.send_json({"action": "start_execution", "mode": "sim"})
        assert receive(ws)["owner"]
        ws.send_json({"action": action, "managed": True, "core": "rtu1", "partners": ["rtu0"],
                      "max_steps": 1, "run_id": 41})
        completed = until(ws, lambda msg: msg.get("run_id") == 41)
        assert completed["core"] == "pru1"
        assert srv.sim.cores["pru1"].counters.instruction_count == 1
        assert srv.sim.cores["pru0"].counters.instruction_count == (1 if "pru0" in selected else 0)
        assert srv.sim.cores["rtu0"].counters.instruction_count == 0
        assert srv.sim.cores["rtu1"].counters.instruction_count == 0


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(srv, "_monotonic", lambda: now[0])
    monkeypatch.setattr(srv, "_last_observer_publish", float("-inf"))
    monkeypatch.setattr(srv, "_observer_publish_skipped", False)
    monkeypatch.setattr(srv, "_skipped_captured_cores", set())
    return now


def start_loop(control, view):
    assert srv.sim.load("pru0", "add r0, r0, 1\nqba 0") == []
    control.send_json({"action": "start_execution", "mode": "run"})
    assert receive(control)["running"] is True
    until(view, lambda msg: msg["type"] == "execution")


def run_chunk(control, run_id):
    control.send_json({"action": "run", "managed": True, "max_steps": 10, "run_id": run_id})
    return state(control)


def observed_until_stopped(view):
    """State messages the observer queued up to the execution-stopped message."""
    states = []
    while True:
        message = receive(view)
        if message["type"] == "execution" and not message["running"]:
            return states
        if message["type"] == "state" and message["core"] == "pru0":
            states.append(message)


def test_back_to_back_run_chunks_within_interval_are_throttled_for_observers(dashboard, clock):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        start_loop(control, view)
        for run_id in range(1, 6):
            run_chunk(control, run_id)
            clock[0] += 0.01
        assert srv.sim.cores["pru0"].counters.instruction_count == 50
        control.send_json({"action": "stop_execution"})
        states = observed_until_stopped(view)
        assert [s["instruction_count"] for s in states] == [10]


def test_run_chunk_after_interval_is_published(dashboard, clock):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        start_loop(control, view)
        run_chunk(control, 1)
        clock[0] += 0.01
        run_chunk(control, 2)
        clock[0] += srv.OBSERVER_STATE_INTERVAL_S
        run_chunk(control, 3)
        control.send_json({"action": "stop_execution"})
        states = observed_until_stopped(view)
        assert [s["instruction_count"] for s in states] == [10, 30]


def test_stop_gives_observers_the_final_state_even_if_last_chunk_was_throttled(dashboard, clock):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        start_loop(control, view)
        run_chunk(control, 1)
        run_chunk(control, 2)
        view.send_json({"action": "stop_execution"})
        assert until(view, lambda msg: msg["type"] == "execution")["running"] is False
        assert state(view)["instruction_count"] == 20


def test_stop_flush_keeps_the_captured_flag_of_a_throttled_capture_chunk(dashboard, clock):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        start_loop(control, view)
        for run_id in (1, 2):
            control.send_json({"action": "run", "managed": True, "max_steps": 10,
                               "run_id": run_id, "capture": True})
            state(control)
        assert state(view)["captured"] is True
        view.send_json({"action": "stop_execution"})
        until(view, lambda msg: msg["type"] == "execution" and not msg["running"])
        flushed = state(view)
        assert flushed["instruction_count"] == 20 and flushed["captured"] is True


def test_breakpoint_and_halt_are_published_immediately_inside_the_interval(dashboard, clock):
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        assert srv.sim.load("pru0", "nop\nnop\nnop\nhalt") == []
        srv.sim.cores["pru0"].breakpoints.add(2)
        control.send_json({"action": "start_execution", "mode": "run"})
        receive(control)
        until(view, lambda msg: msg["type"] == "execution")
        srv._last_observer_publish = clock[0]  # a publish just happened
        control.send_json({"action": "run", "managed": True, "max_steps": 1000, "run_id": 1})
        hit = state(view)
        assert hit["pc"] == 2 and hit["at_breakpoint"] is True
        srv.sim.cores["pru0"].breakpoints.clear()
        control.send_json({"action": "start_execution", "mode": "run"})
        until(view, lambda msg: msg["type"] == "execution" and msg["running"])
        srv._last_observer_publish = clock[0]
        control.send_json({"action": "run", "managed": True, "max_steps": 1000, "run_id": 2})
        halted = until(view, lambda msg: msg["type"] == "state" and msg["core"] == "pru0" and msg["halted"])
        assert halted["pc"] == srv.sim.cores["pru0"].pc


def test_step_is_never_throttled(dashboard, clock):
    assert srv.sim.load("pru0", "nop\nnop\nnop\nhalt") == []
    with dashboard.websocket_connect("/ws") as control, dashboard.websocket_connect("/ws") as view:
        for expected in (1, 2, 3):
            control.send_json({"action": "step"})
            state(control)
            assert state(view)["pc"] == expected
