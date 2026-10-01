"""Contract tests for the actual three-core SSI realtime dashboard panel."""

import asyncio
import json
from pathlib import Path

from ui import server as srv


STATIC_DIR = Path(__file__).parents[1] / "ui" / "static"


def test_simple_ssi_profile_state_is_read_only_and_reports_build_inputs(tmp_path, monkeypatch):
    profile_path = tmp_path / "ssi_build_config.json"
    profile_path.write_text(
        json.dumps({
            "SSI_CORE_HZ": 300_000_000,
            "SSI_IEP_HZ": 300_000_000,
            "SSI_PERIOD_TICKS": 288,
            "SSI_PERIOD_NS": 960,
            "SSI_FRAME_BITS": 12,
            "SSI_CLOCK_HZ": 4_000_000,
            "SSI_POSITION_BITS": 12,
            "SSI_POSITION_STEP": 1,
            "SSI_ITERATIONS": 100_000,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "SSI_SIMPLE_PROFILE_PATH", profile_path)
    monkeypatch.setattr(srv, "_ssi_simple_error", None)

    state = srv._ssi_simple_state()

    assert state["loaded"] is False
    assert state["running"] is False
    assert "load the three firmware images" in state["status"]
    assert state["profile"]["SSI_PERIOD_TICKS"] == 288
    assert state["profile"]["SSI_ITERATIONS"] == 100_000
    assert state["config_path"].endswith("ssi_hardware_config.h")


def test_simple_ssi_defaults_to_the_bundled_firmware_workspace():
    bundled_root = Path(srv.PROJECT_ROOT) / "firmware" / "ssi_test"

    assert srv.SSI_SIMPLE_ROOT == bundled_root
    for relative_path in (
        "tools/simulate_ssi.py",
        "tools/simulator_adapter.py",
        "tools/generate_config.py",
        "ssi_test/ssi_hardware_config.h",
        "include/ssi_build_config.json",
        "include/ssi_build_config.inc",
        "include/ssi_test_abi.h",
        "include/ssi_test_abi.inc",
        "pru0_ssi_emulator/pru0_main.asm",
        "pru0_ssi_emulator/AM243_AM64_PRU_pinmux.inc",
        "pru1_ssi_reader/pru1_main.asm",
        "pru1_ssi_reader/AM243_AM64_PRU_pinmux.inc",
        "rtu1_tick/rtu_pru1_main.asm",
    ):
        assert (bundled_root / relative_path).is_file(), relative_path


def test_simple_ssi_load_puts_all_three_images_in_the_shared_simulator():
    original_sim = srv.sim
    try:
        srv._load_ssi_simple_firmware()

        assert srv._ssi_simple_session is not None
        assert srv._ssi_simple_host is not None
        assert srv.sim is srv._ssi_simple_session.sim
        assert all(
            srv.sim.cores[core].instructions
            for core in ("pru0", "pru1", "rtu1")
        )
        assert {
            (wire["src_core"], wire["src_pin"], wire["dst_core"], wire["dst_pin"])
            for wire in srv.sim.list_gpio_wires()
        } == {
            ("pru1", 0, "pru0", 8),
            ("pru0", 0, "pru1", 16),
        }
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim


def test_simple_ssi_load_regenerates_profile_before_reading_it(monkeypatch):
    original_sim = srv.sim
    events = []
    profile = srv._read_ssi_simple_profile()

    monkeypatch.setattr(
        srv,
        "_regenerate_ssi_simple_profile",
        lambda: events.append("generate"),
        raising=False,
    )
    monkeypatch.setattr(
        srv,
        "_read_ssi_simple_profile",
        lambda: events.append("read") or profile,
    )
    try:
        srv._load_ssi_simple_firmware()
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim

    assert events == ["generate", "read"]


def test_simple_ssi_normal_scheduler_advances_firmware_and_host_model():
    original_sim = srv.sim
    try:
        srv._load_ssi_simple_firmware()
        session = srv._ssi_simple_session

        srv._ssi_simple_advance(20_000)

        assert session.wall_cycle == 20_000
        assert srv._ssi_simple_armed is True
        assert all(
            session.sim.cores[core].counters.instruction_count > 0
            for core in ("pru0", "pru1", "rtu1")
        )
        progress = srv._ssi_simple_progress()
        assert progress["opportunities"] > 0
        assert progress["published"] == progress["opportunities"]
        assert progress["frames"] > 0
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim


def test_simple_ssi_panel_uses_actual_run_actions_and_read_only_fields():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    for element_id in (
        "ssi-simple-load",
        "ssi-simple-reset",
        "ssi-simple-refresh",
        "ssi-simple-status",
        "ssi-simple-profile",
        "ssi-simple-results",
    ):
        assert f'id="{element_id}"' in html, element_id

    for action in ("ssi_simple_load", "ssi_simple_read", "ssi_simple_reset"):
        assert action in js, action

    assert "ssi_simple_run" not in js
    assert 'id="btn-run"' in html
    assert 'id="btn-step"' in html
    assert 'id="btn-sim"' in html
    assert "ssi_simple_state" in js
    assert "ssi_simple_error" in js
    assert "Run, Step, or SIM advances all three images" in html
    assert "use the top Run, Step, or SIM controls" in html
    assert '<div class="io-section-title">Simple SSI Realtime</div>' in html
    assert '<details id="ssi-runtime-legacy" hidden>' in html


def test_signal_graph_renderer_cache_is_busted_after_edge_timestamp_fix():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'src="/static/app.js?v=20260914-5"' in html


def test_simple_ssi_reload_clears_previous_waveform_capture():
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    panel_start = js.index("// ---- Simple SSI realtime panel")
    load_start = js.index('loadBtn.addEventListener("click"', panel_start)
    load_end = js.index('resetBtn.addEventListener("click"', load_start)
    reset_start = load_end
    reset_end = js.index('refreshBtn.addEventListener("click"', reset_start)

    assert "graphClear();" in js[load_start:load_end]
    assert "graphClear();" in js[reset_start:reset_end]


def test_multicore_partner_selector_can_show_rtu_pru1():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    partner_start = html.index('id="mc-partner-select"')
    partner_end = html.index("</select>", partner_start)
    partner_markup = html[partner_start:partner_end]

    assert '<option value="rtu1">RTU_PRU1</option>' in partner_markup
    assert 'if (core === "rtu1") return "RTU_PRU1";' in js


def test_simple_execution_publishes_progress_to_the_browser():
    server_source = Path(srv.__file__).read_text(encoding="utf-8")
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert '"type": "ssi_simple_progress"' in server_source
    assert 'msg.type === "ssi_simple_progress"' in js
    assert "renderSsiSimpleProgress" in js


def test_simple_multicore_run_sends_both_state_packets_without_capture():
    server_source = Path(srv.__file__).read_text(encoding="utf-8")

    assert "for selected_core in selected_cores" in server_source
    assert "await _send_state(websocket, selected_core, captured=capture)" in server_source
    assert "await _send_ssi_simple_progress(websocket)" in server_source


def test_simple_ssi_capture_is_transition_only_and_has_initial_terminal_samples(
    monkeypatch,
):
    original_sim = srv.sim
    try:
        srv._load_ssi_simple_firmware()
        selected = ["pru0", "pru1", "rtu1"]
        selected_prus = {core: srv.sim.cores[core] for core in selected}
        core_by_id = {id(pru): core for core, pru in selected_prus.items()}
        observed = {core: [] for core in selected}
        previous = {}
        original_signature = srv._capture_signature

        def observe_signature(pru):
            signature = original_signature(pru)
            core = core_by_id[id(pru)]
            if previous.get(core) != signature:
                observed[core].append(signature)
                previous[core] = signature
            return signature

        monkeypatch.setattr(srv, "_capture_signature", observe_signature)
        samples_by_core, times_by_core = asyncio.run(
            srv._ssi_simple_capture_run(selected_prus, selected[0], 1000)
        )
        emitted = [core for core in selected if samples_by_core[core]]
        captures = [
            {
                "core": core,
                "samples": samples_by_core[core],
                "captured_at_ms": times_by_core[core],
                "capture_group": f"{','.join(emitted)}:{selected_prus[selected[0]].counters.instruction_count}",
                "capture_cores": emitted,
                "edge_timestamps": True,
            }
            for core in emitted
        ]
        assert {message["core"] for message in captures} == set(selected)
        assert {tuple(message["capture_cores"]) for message in captures} == {
            tuple(selected)
        }
        assert all(message["edge_timestamps"] is True for message in captures)
        assert sum(len(message["samples"]) for message in captures) < 150
        assert len(json.dumps(captures, separators=(",", ":"))) < 25_000
        for message in captures:
            samples = message["samples"]
            assert samples
            assert samples[0][-1] == 0
            signatures = [
                (sample[1], sample[2], 0, 0, 0) for sample in samples
            ]
            assert signatures[0] == observed[message["core"]][0]
            assert signatures[1:-1] == observed[message["core"]][1:]
        terminal_steps = {
            message["samples"][-1][-1] for message in captures
        }
        assert len(terminal_steps) == 1
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim


def test_simple_ssi_ui_mode_keeps_diagnostics_empty_over_a_long_batch():
    original_sim = srv.sim
    try:
        srv._load_ssi_simple_firmware()
        asyncio.run(srv._ssi_simple_advance_async(288_000))

        assert srv._ssi_simple_session.events() == []
        assert srv._ssi_simple_host.position_stores == []
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim


def test_simple_ssi_ui_batches_yield_after_each_128_slot_chunk(monkeypatch):
    original_sim = srv.sim
    yields = []

    async def record_yield(delay):
        yields.append(delay)

    try:
        srv._load_ssi_simple_firmware()
        monkeypatch.setattr(srv.asyncio, "sleep", record_yield)
        asyncio.run(srv._ssi_simple_advance_async(257))
        assert len(yields) == 2
        assert yields == [0, 0]
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim


def test_simple_ssi_scheduler_stops_polling_timer_ready_after_arm(monkeypatch):
    original_sim = srv.sim
    ready_reads_after_arm = []

    try:
        srv._load_ssi_simple_firmware()
        session = srv._ssi_simple_session
        original_read32 = session.read32
        timer_ready_offset = int(
            session.abi["SSI_PRU1_TIMER_READY_OFF"]
        )

        def counted_read32(region, offset):
            if offset == timer_ready_offset and srv._ssi_simple_armed:
                ready_reads_after_arm.append((region, offset))
            return original_read32(region, offset)

        monkeypatch.setattr(session, "read32", counted_read32)
        srv._ssi_simple_advance(20_000)

        assert srv._ssi_simple_armed is True
        assert ready_reads_after_arm == []
    finally:
        srv._drop_ssi_simple_firmware()
        srv.sim = original_sim
