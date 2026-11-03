"""Idle skip in run modes: NOP delay-loop collapse and R31 poll fast-forward.

Every test runs the same firmware twice, once with plain ``step()`` and once
through the skipping run paths (``PRUCore.step_run`` or
``Simulator.step_paced_many(..., idle_skip=True)``), and requires the two
runs to end bit- and cycle-identical: registers, pc, counters, loop state,
memory, SD/PERIF/IEP/link state and every GPO edge with its time stamp.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from simulator import Simulator
from tests.test_sd_pin_input import configure_channel, load_pattern_producer

_TESTS = Path(__file__).parent
_SRC = _TESTS.parent / "source"
PDM_CLK_ASM = _TESTS / "asm" / "pdm_clk_ref.asm"
PDM_DELAY_PRODUCER_ASM = _TESTS / "asm" / "pdm_delay_producer.asm"

# PRU0 minimal SD consumer: SD mode on ch0, spin on the valid bit, read the
# shadow value, clear valid, store it to a 64-word DRAM0 ring, repeat.
SD_CONSUMER = """\
    ldi32 r30, 0x02000000       ; SD_EN (R30[25]), CH_SEL = 0
    ldi   r6, 0
wait:
    qbbc  wait, r31, 28         ; spin until ch0 valid
    mov   r5, r31               ; status + shadow data
    set   r31, r31, 24          ; clear valid
    sbbo  &r5, r6, 0, 4
    add   r6, r6, 4
    and   r6, r6, 0xFF          ; wrap the ring
    qba   wait
"""


def _probe_pins(sim):
    """Record every GPO change of every core with its time stamp."""
    history = []
    for name, core in sim.cores.items():
        def listener(old, new, _name=name, _core=core):
            history.append((_name, _core.step_start_cycle, _core.counters.cycles,
                            _core.counters.instruction_count, old, new))
        core.io_port.gpo_listeners.append(listener)
    return history


def _state(sim):
    out = {}
    for name, c in sim.cores.items():
        ls = c.loop_state
        perif = c.io_port.perif
        out[name] = {
            "regs": sim.registers(name),
            "carry": c.registers.carry,
            "pc": c.pc,
            "halted": c.halted,
            "counters": (c.counters.cycles, c.counters.stall_cycles,
                         c.counters.instruction_count),
            "loop": None if ls is None else (ls.count, ls.start_address, ls.end_address),
            "step_start_cycle": c.step_start_cycle,
            "gpo": c.io_port.gpo,
            "device_cycle": c.io_port._device_cycle,
            "sd": c.io_port.sd_filter.snapshot(),
            "perif": perif.snapshot() if perif is not None else None,
        }
    out["mem"] = (sim.memory_read(0, 0x2000), sim.memory_read(0x10000, 0x8000))
    out["iep"] = sim.iep.snapshot()
    link = sim._pdm_link
    out["link"] = link.snapshot() if link is not None else None
    return out


def _build(config, source_pru0, source_pru1, setup=None, include_paths=None):
    sim = Simulator(config_path=config)
    pins = _probe_pins(sim)
    for name, source in (("pru0", source_pru0), ("pru1", source_pru1)):
        if source is not None:
            assert sim.load(name, source, include_paths) == []
    if setup is not None:
        setup(sim)
    return sim, pins


def run_both(config, source_pru0, source_pru1, cycles, skip, setup=None,
             include_paths=None):
    """Run to *cycles* with or without idle skip; return (state, pins, sim).

    One source: that core runs alone (``step_run(cycles_left)`` vs
    ``step()``). Two sources: PRU1 leads, PRU0 follows through
    ``step_paced_many``. The run stops at the first lead instruction boundary
    at or past *cycles*, which is the same boundary in both modes.
    """
    sim, pins = _build(config, source_pru0, source_pru1, setup, include_paths)
    if source_pru0 is not None and source_pru1 is not None:
        lead = sim.cores["pru1"]
        while lead.counters.cycles < cycles:
            result = sim.step_paced_many("pru1", ["pru0"], 1, idle_skip=skip)
            assert result["complete"], result
    else:
        name = "pru0" if source_pru0 is not None else "pru1"
        core = sim.cores[name]
        while core.counters.cycles < cycles and not core.halted:
            if skip:
                core.step_run(cycles - core.counters.cycles)
            else:
                core.step()
    return _state(sim), pins, sim


def assert_skip_exact(config, source_pru0, source_pru1, cycles, **kw):
    """Run with skip, then without to the same end point; require equality."""
    on_state, on_pins, on_sim = run_both(config, source_pru0, source_pru1,
                                         cycles, True, **kw)
    end = on_sim.cores["pru1" if source_pru1 is not None else "pru0"].counters.cycles
    off_state, off_pins, off_sim = run_both(config, source_pru0, source_pru1,
                                            end, False, **kw)
    for key in off_state:
        assert on_state[key] == off_state[key], key
    assert on_pins == off_pins
    for core in off_sim.cores.values():
        assert core.skipped_cycles == 0
    return on_sim, on_pins


def _pdm_clk():
    return PDM_CLK_ASM.read_text()


# ---------------------------------------------------------------------------
# Delay-loop collapse
# ---------------------------------------------------------------------------

def test_delay_loop_collapse_is_exact(nominal_config):
    sim, pins = assert_skip_exact(nominal_config, None, _pdm_clk(), 200_000)
    pru1 = sim.cores["pru1"]
    assert pru1.skipped_cycles > 0
    assert pru1.skipped_cycles > 0.8 * pru1.counters.cycles
    # 3.072 MHz from 200 MHz: ~65 cycles per period, two edges each.
    assert len(pins) > 2 * (200_000 // 66)


def test_delay_loop_collapse_ticks_an_enabled_iep(nominal_config):
    def enable_iep(sim):
        sim.iep.global_cfg = 0x551          # CNT_ENABLE, DEFAULT_INC 5
    sim, _ = assert_skip_exact(nominal_config, None, _pdm_clk(), 20_000,
                               setup=enable_iep)
    assert sim.iep.count > 0
    assert sim.cores["pru1"].skipped_cycles > 0


def test_step_run_with_small_budget_is_step(nominal_config):
    sim, _ = _build(nominal_config, None, _pdm_clk())
    pru1 = sim.cores["pru1"]
    while pru1.pc != 6:                     # first NOP of the high delay loop
        pru1.step()
    before = pru1.counters.instruction_count
    assert pru1.step_run(1) == 1
    assert pru1.counters.instruction_count == before + 1
    assert pru1.skipped_cycles == 0
    retired = pru1.step_run(10)
    assert retired == 10 and pru1.skipped_cycles == 10
    assert pru1.loop_state is not None and pru1.pc == 6


def test_single_step_never_skips(nominal_config):
    sim, _ = _build(nominal_config, None, _pdm_clk())
    pru1 = sim.cores["pru1"]
    while pru1.pc != 6:
        pru1.step()
    assert pru1.loop_state is not None and pru1.instructions[6].opcode == "NOP"
    before = pru1.counters.instruction_count
    sim.step("pru1", 1)
    assert pru1.counters.instruction_count == before + 1
    assert pru1.skipped_cycles == 0


def test_breakpoint_inside_delay_loop_still_hits(nominal_config):
    def run_to_breakpoint(skip):
        sim, _ = _build(nominal_config, None, _pdm_clk())
        pru1 = sim.cores["pru1"]
        pru1.breakpoints.add(11)            # the NOP of the short low delay
        steps = 0
        while steps < 10_000:
            if skip:
                pru1.step_run(1 << 16)
            else:
                pru1.step()
            steps += 1
            if pru1.pc in pru1.breakpoints:
                break
        return pru1

    on, off = run_to_breakpoint(True), run_to_breakpoint(False)
    assert on.pc == off.pc == 11
    assert on.counters.cycles == off.counters.cycles
    assert on.counters.instruction_count == off.counters.instruction_count
    assert on.loop_state.count == off.loop_state.count
    assert on.skipped_cycles > 0        # the high delay before it collapsed


def test_breakpoint_after_delay_loop_stops_at_loop_exit(nominal_config):
    def run_to_breakpoint(skip):
        sim, _ = _build(nominal_config, None, _pdm_clk())
        pru1 = sim.cores["pru1"]
        pru1.breakpoints.add(7)             # PDM_HIGH_DLY: first pc after the loop
        while True:
            pru1.step_run(1 << 16) if skip else pru1.step()
            if pru1.pc in pru1.breakpoints:
                return pru1

    on, off = run_to_breakpoint(True), run_to_breakpoint(False)
    assert on.pc == off.pc == 7
    assert on.counters.cycles == off.counters.cycles
    assert on.loop_state is None and off.loop_state is None
    assert on.skipped_cycles > 0


# ---------------------------------------------------------------------------
# Existing SD and PERIF firmware
# ---------------------------------------------------------------------------

def test_modulator_mode_sd_unaffected(nominal_config):
    def sine(sim):
        sim.set_sd_modulator("pru0", 2, signal="sine", amplitude=0.5,
                             dc_level=0.0, period=1024, phase_deg=0.0,
                             sd_clock_mhz=20.0)
    source = (_SRC / "sdfm_sinc3_demo" / "sdfm_sinc3_demo.asm").read_text()
    sim, _ = assert_skip_exact(nominal_config, source, None, 300_000, setup=sine,
                               include_paths=[str(_SRC / "sdfm_sinc3_demo")])
    # The poll on the modulator channel must not fast-forward.
    pru0 = sim.cores["pru0"]
    assert pru0.skipped_cycles < 0.01 * pru0.counters.cycles
    assert any(sim.memory_read(0, 0x400))      # it did store filtered samples


def test_perif_firmware_unaffected(nominal_config):
    def perif(sim):
        sim.gpcfg_write("pru0", 1)
        sim.write_perif_register("pru0", 0x260E4, 0x00070010)
    source = (_SRC / "perif_tx_pattern.asm").read_text()
    sim, _ = assert_skip_exact(nominal_config, source, None, 50_000, setup=perif)
    assert sim.cores["pru0"].io_port.perif.enabled
    assert sim.cores["pru0"].skipped_cycles == 0


# ---------------------------------------------------------------------------
# Poll fast-forward
# ---------------------------------------------------------------------------

def _link_consumer(sim):
    configure_channel(sim, 0, clk_sel=1, clk_inv=0, osr=96)
    sim.set_sd_source("pru0", 0, "pin")
    sim.attach_pdm_link(keep_history=True)


def test_poll_fast_forward_is_exact(nominal_config):
    """Task 1's pattern producer runs ahead; PRU0 then consumes its edges.

    With the producer already ahead, PRU0's spin may jump straight to the
    next valid rise. (Lockstep with this producer cannot skip: see the
    paced test below.)
    """
    cycles = 60_000

    def run(skip, end=None):
        sim, pins = _build(nominal_config, None, None)
        assert sim.load("pru0", SD_CONSUMER) == []
        load_pattern_producer(sim)
        _link_consumer(sim)
        producer, consumer = sim.cores["pru1"], sim.cores["pru0"]
        while producer.counters.cycles < cycles + 2_000:
            producer.step()
        stop = cycles if end is None else end
        while consumer.counters.cycles < stop:
            if skip:
                consumer.step_run(stop - consumer.counters.cycles)
            else:
                consumer.step()
        return sim, pins

    on_sim, on_pins = run(True)
    off_sim, off_pins = run(False, on_sim.cores["pru0"].counters.cycles)
    on, off = _state(on_sim), _state(off_sim)
    for key in off:
        assert on[key] == off[key], key
    assert on_pins == off_pins
    assert on_sim._pdm_link.sampled_bits == off_sim._pdm_link.sampled_bits
    pru0 = on_sim.cores["pru0"]
    assert pru0.skipped_cycles > 0.5 * cycles
    assert off_sim.cores["pru0"].skipped_cycles == 0
    assert any(on_sim.memory_read(0, 0x100))   # samples were stored


def test_poll_fast_forward_paced_pattern_producer_is_exact(nominal_config):
    with open(_TESTS / "asm" / "pdm_pattern_producer.asm") as f:
        producer = f.read()
    sim, pins = assert_skip_exact(nominal_config, SD_CONSUMER, producer, 30_000,
                                  setup=_link_consumer)
    assert any(sim.memory_read(0, 0x100))
    assert len(pins) > 1000


def test_poll_fast_forward_paced_delay_producer_is_exact(nominal_config):
    """Lockstep: PRU1's delay loops collapse and PRU0's spin follows them."""
    cycles = 100_000
    sim, pins = assert_skip_exact(nominal_config, SD_CONSUMER,
                                  PDM_DELAY_PRODUCER_ASM.read_text(), cycles,
                                  setup=_link_consumer)
    assert sim.cores["pru1"].skipped_cycles > 0.5 * cycles
    assert sim.cores["pru0"].skipped_cycles > 0.5 * cycles
    assert any(sim.memory_read(0, 0x100))
    assert len(sim._pdm_link.sampled_bits[0]) > 1000


def test_poll_fast_forward_paced_pdm_clk_is_exact(nominal_config):
    cycles = 100_000
    sim, _ = assert_skip_exact(nominal_config, SD_CONSUMER, _pdm_clk(), cycles,
                               setup=_link_consumer)
    assert sim.cores["pru0"].skipped_cycles > 0.5 * cycles


# PRU0: like SD_CONSUMER, but on each valid rise it stores the shared IEP
# count. The IEP follows the furthest core, so this is where a lead that ran
# ahead of lockstep would show: the follower would read a later count.
SD_IEP_CONSUMER = """\
    ldi32 r30, 0x02000000
    ldi32 r10, 0x0002E010       ; IEP COUNT_REG0
    ldi   r6, 0
wait:
    qbbc  wait, r31, 28
    add   r7, r7, 1             ; busy work: the read below then lands
    add   r7, r7, 1             ; while PRU1 sits in its next delay loop
    add   r7, r7, 1
    add   r7, r7, 1
    add   r7, r7, 1
    add   r7, r7, 1
    add   r7, r7, 1
    add   r7, r7, 1
    lbbo  &r5, r10, 0, 4        ; IEP count
    set   r31, r31, 24
    sbbo  &r5, r6, 0, 4
    add   r6, r6, 4
    and   r6, r6, 0xFF
    qba   wait
"""

# PRU0: never idle, samples the IEP count in a tight loop.
IEP_READER = """\
    ldi32 r10, 0x0002E010
    ldi   r6, 0
again:
    lbbo  &r5, r10, 0, 4
    sbbo  &r5, r6, 0, 4
    add   r6, r6, 4
    and   r6, r6, 0xFF
    qba   again
"""


def _enable_iep(sim):
    sim.iep.global_cfg = 0x551


def test_paced_skip_keeps_follower_iep_reads_exact(nominal_config):
    def setup(sim):
        _link_consumer(sim)
        _enable_iep(sim)
    sim, _ = assert_skip_exact(nominal_config, SD_IEP_CONSUMER,
                               PDM_DELAY_PRODUCER_ASM.read_text(), 60_000,
                               setup=setup)
    assert sim.cores["pru1"].skipped_cycles > 0.3 * 60_000
    assert any(sim.memory_read(0, 0x100))


def test_paced_skip_with_busy_follower_is_exact(nominal_config):
    sim, _ = assert_skip_exact(nominal_config, IEP_READER, _pdm_clk(), 30_000,
                               setup=_enable_iep)
    assert any(sim.memory_read(0, 0x100))


def test_paced_skip_with_follower_as_lead_producer_order_is_exact(nominal_config):
    """Consumer leads, producer follows (halted producer: infinite horizon)."""
    halting = PDM_DELAY_PRODUCER_ASM.read_text().replace(
        "    qba   LOOP\n", "    add r20, r20, 1\n    qbgt LOOP, r20, 40\n    halt\n")

    def run(skip, end=None):
        sim, pins = _build(nominal_config, SD_CONSUMER, halting, _link_consumer)
        producer = sim.cores["pru1"]
        while not producer.halted:          # producer finishes first
            producer.step()
        lead = sim.cores["pru0"]
        stop = 40_000 if end is None else end
        while lead.counters.cycles < stop:
            assert sim.step_paced_many("pru0", ["pru1"], 1, idle_skip=skip)["complete"]
        return sim, pins

    on_sim, on_pins = run(True)
    off_sim, off_pins = run(False, on_sim.cores["pru0"].counters.cycles)
    on, off = _state(on_sim), _state(off_sim)
    for key in off:
        assert on[key] == off[key], key
    assert on_pins == off_pins
    assert on_sim.cores["pru0"].skipped_cycles > 0


def test_poll_with_breakpoint_on_spin_never_skips(nominal_config):
    sim, _ = _build(nominal_config, SD_CONSUMER, None)
    load_pattern_producer(sim)
    _link_consumer(sim)
    producer, consumer = sim.cores["pru1"], sim.cores["pru0"]
    while producer.counters.cycles < 5_000:
        producer.step()
    consumer.step_run(100)
    consumer.step_run(100)                  # ldi32 = 2 instructions
    consumer.step_run(100)
    assert consumer.pc == 3                 # at the spin
    consumer.breakpoints.add(3)
    before = consumer.counters.cycles
    assert consumer.step_run(1_000) == 1
    assert consumer.counters.cycles == before + 1
    assert consumer.skipped_cycles == 0


def test_idle_skip_is_on_by_default_and_step_paced_many_defaults_off(nominal_config):
    sim, _ = _build(nominal_config, SD_CONSUMER, PDM_DELAY_PRODUCER_ASM.read_text(),
                    setup=_link_consumer)
    assert sim.idle_skip is True
    while sim.cores["pru1"].counters.cycles < 5_000:
        assert sim.step_paced_many("pru1", ["pru0"], 1)["complete"]
    assert all(c.skipped_cycles == 0 for c in sim.cores.values())


def test_reset_clears_skipped_cycles(nominal_config):
    sim = run_both(nominal_config, None, _pdm_clk(), 2_000, True)[2]
    assert sim.cores["pru1"].skipped_cycles > 0
    sim.reset("pru1")
    assert sim.cores["pru1"].skipped_cycles == 0


# ---------------------------------------------------------------------------
# Dashboard server: Run uses the skip, capture over a skipped span
# ---------------------------------------------------------------------------

@pytest.fixture
def server_sim(monkeypatch, nominal_config):
    import ui.server as srv
    fresh = Simulator(config_path=nominal_config)
    monkeypatch.setattr(srv, "sim", fresh)
    srv._clear_history()
    return fresh


def _receive_until_state(ws):
    messages = []
    while True:
        msg = ws.receive_json()
        messages.append(msg)
        if msg.get("type") == "state":
            return messages


def _ws_samples(monkeypatch, config, load, msg, idle_skip=True):
    """Fresh server sim, one websocket request; return (samples, sim)."""
    import ui.server as srv
    from ui.server import app
    fresh = Simulator(config_path=config)
    fresh.idle_skip = idle_skip
    monkeypatch.setattr(srv, "sim", fresh)
    srv._clear_history()
    load(fresh)
    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json(msg)
        messages = _receive_until_state(ws)
        if msg["action"] == "run_multicore":
            messages += _receive_until_state(ws)
    samples = [s for m in messages if m.get("type") == "capture" for s in m["samples"]]
    return samples, fresh


def _load_pdm_clk(sim):
    assert sim.load("pru1", _pdm_clk()) == []


def _load_paced_pair(sim):
    assert sim.load("pru1", PDM_DELAY_PRODUCER_ASM.read_text()) == []
    assert sim.load("pru0", SD_CONSUMER) == []
    _link_consumer(sim)


def test_capture_over_skipped_span(monkeypatch, nominal_config):
    """Run with the Signal Graph capturing never skips (samples are spaced by
    index): same samples as with idle skip off; without capture it skips."""
    msg = {"action": "run", "core": "pru1", "max_steps": 400,
           "capture": True, "stride": 1}
    on, on_sim = _ws_samples(monkeypatch, nominal_config, _load_pdm_clk, msg)
    off, off_sim = _ws_samples(monkeypatch, nominal_config, _load_pdm_clk, msg,
                               idle_skip=False)
    assert len(on) == 400 and on == off
    assert on_sim.cores["pru1"].skipped_cycles == 0
    assert on_sim.cores["pru1"].counters.instruction_count == 400

    _, plain = _ws_samples(monkeypatch, nominal_config, _load_pdm_clk,
                           {"action": "run", "core": "pru1", "max_steps": 400})
    assert plain.cores["pru1"].skipped_cycles > 0
    assert plain.cores["pru1"].counters.instruction_count > 400


def test_run_multicore_capture_never_skips(monkeypatch, nominal_config):
    msg = {"action": "run_multicore", "core": "pru1", "partners": ["pru0"],
           "max_steps": 300, "capture": True, "stride": 1}
    on, on_sim = _ws_samples(monkeypatch, nominal_config, _load_paced_pair, msg)
    off, off_sim = _ws_samples(monkeypatch, nominal_config, _load_paced_pair, msg,
                               idle_skip=False)
    assert len(on) == len(off) == 300
    assert on == off                          # same stamps and pin words
    assert all(c.skipped_cycles == 0 for c in on_sim.cores.values())
    for name in ("pru0", "pru1"):
        assert _state(on_sim)[name] == _state(off_sim)[name]

    no_capture = dict(msg, capture=False)
    _, plain = _ws_samples(monkeypatch, nominal_config, _load_paced_pair, no_capture)
    assert plain.cores["pru1"].skipped_cycles > 0
    assert plain.cores["pru0"].skipped_cycles > 0


def test_set_idle_skip_turns_run_skip_off(server_sim):
    from ui.server import app
    assert server_sim.load("pru1", _pdm_clk()) == []
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "set_idle_skip", "enabled": False, "core": "pru1"})
        state = _receive_until_state(ws)[-1]
        assert state["idle_skip"] is False
        assert server_sim.idle_skip is False
        ws.send_json({"action": "run", "core": "pru1", "max_steps": 400})
        state = _receive_until_state(ws)[-1]
    pru1 = server_sim.cores["pru1"]
    assert pru1.skipped_cycles == 0
    assert pru1.counters.instruction_count == 400
    assert state["skipped_cycles"] == 0


def test_run_multicore_uses_skip_but_step_multicore_does_not(server_sim):
    from ui.server import app
    assert server_sim.load("pru1", PDM_DELAY_PRODUCER_ASM.read_text()) == []
    assert server_sim.load("pru0", SD_CONSUMER) == []
    _link_consumer(server_sim)
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        for _ in range(5):
            ws.send_json({"action": "step_multicore", "core": "pru1",
                          "partners": ["pru0"]})
            _receive_until_state(ws)
            _receive_until_state(ws)
        assert all(c.skipped_cycles == 0 for c in server_sim.cores.values())
        ws.send_json({"action": "run_multicore", "core": "pru1",
                      "partners": ["pru0"], "max_steps": 200})
        _receive_until_state(ws)
        _receive_until_state(ws)
    assert server_sim.cores["pru1"].skipped_cycles > 0
    assert server_sim.cores["pru0"].skipped_cycles > 0


# ---------------------------------------------------------------------------
# MCP run_until
# ---------------------------------------------------------------------------

def _mcp(nominal_config):
    from mcp_server.server import PRUSimulatorMCP
    return PRUSimulatorMCP(simulator=Simulator(config_path=nominal_config))


@pytest.mark.parametrize("condition", ["cycles>12345", "reg:r9==0x2d"])
def test_run_until_skips_and_matches_stepping(nominal_config, condition):
    results = {}
    for skip in (True, False):
        api = _mcp(nominal_config)
        api.sim.idle_skip = skip
        assert api.sim.load("pru1", _pdm_clk()) == []
        out = api.pru_run_until("pru1", condition, max_steps=100_000)
        core = api.sim.cores["pru1"]
        results[skip] = (out, core.counters.cycles, core.counters.instruction_count,
                         core.pc, api.sim.registers("pru1"), core.skipped_cycles)
    on, off = results[True], results[False]
    assert on[:5] == off[:5]
    assert on[0]["condition_met"]
    assert on[5] > 0 and off[5] == 0


def test_run_until_cycle_budget_is_exact_with_skip(nominal_config):
    results = {}
    for skip in (True, False):
        api = _mcp(nominal_config)
        api.sim.idle_skip = skip
        assert api.sim.load("pru1", _pdm_clk()) == []
        out = api.pru_run_until("pru1", "halt", max_steps=100_000, max_cycles=777)
        core = api.sim.cores["pru1"]
        results[skip] = (out, core.counters.cycles, core.pc, core.skipped_cycles)
    assert results[True][:3] == results[False][:3]
    assert results[True][1] == 777
    assert results[True][3] > 0


def test_mcp_step_never_skips(nominal_config):
    api = _mcp(nominal_config)
    assert api.sim.load("pru1", _pdm_clk()) == []
    api.pru_step("pru1", 500)
    core = api.sim.cores["pru1"]
    assert core.counters.instruction_count == 500
    assert core.skipped_cycles == 0


# ---------------------------------------------------------------------------
# Fix round 1: delay loop on the PdmLink consumer, run_until max_steps
# ---------------------------------------------------------------------------

# PRU0: a 200-NOP delay loop before each valid poll. The core is the link's
# consumer, so stepping applies producer edges before every NOP and raises
# once a NOP starts past the producer's horizon.
CONSUMER_DELAY_LOOP = """\
    ldi32 r30, 0x02000000
top:
    loop  dly, 200
    nop
dly:
    qbbc  top, r31, 28
    mov   r5, r31
    set   r31, r31, 24
    qba   top
"""


def _consumer_delay_sim(config):
    sim, pins = _build(config, CONSUMER_DELAY_LOOP, None)
    load_pattern_producer(sim)
    _link_consumer(sim)
    return sim, pins


def test_consumer_delay_loop_collapse_is_exact(nominal_config):
    def run(skip, stop):
        sim, pins = _consumer_delay_sim(nominal_config)
        producer, consumer = sim.cores["pru1"], sim.cores["pru0"]
        while producer.counters.cycles < 20_000:
            producer.step()
        while consumer.counters.cycles < stop:
            if skip:
                consumer.step_run(stop - consumer.counters.cycles)
            else:
                consumer.step()
        return sim, pins

    # 150 cycles ends inside the first delay loop; 15 000 crosses many.
    for stop in (150, 15_000):
        on_sim, on_pins = run(True, stop)
        off_sim, off_pins = run(False, on_sim.cores["pru0"].counters.cycles)
        on, off = _state(on_sim), _state(off_sim)
        for key in off:
            assert on[key] == off[key], (stop, key)
        assert on_pins == off_pins
        assert on_sim._pdm_link.sampled_bits == off_sim._pdm_link.sampled_bits
        assert on_sim.cores["pru0"].skipped_cycles > 0.5 * stop


def test_consumer_ahead_error_point_matches_stepping_with_skip(nominal_config):
    from pru_io.pdm_link import PdmLinkError
    out = {}
    for skip in (True, False):
        sim, _ = _consumer_delay_sim(nominal_config)
        producer, consumer = sim.cores["pru1"], sim.cores["pru0"]
        while producer.counters.cycles < 500:
            producer.step()
        with pytest.raises(PdmLinkError) as err:
            for _ in range(100_000):
                consumer.step_run(1 << 16) if skip else consumer.step()
        out[skip] = (consumer.counters.cycles, consumer.pc, str(err.value),
                     _state(sim), consumer.skipped_cycles > 0)
    assert out[True][:4] == out[False][:4]
    assert out[True][4], "the delay loop did not collapse at all"


def test_run_until_max_steps_stop_matches_stepping(nominal_config):
    results = {}
    for skip in (True, False):
        api = _mcp(nominal_config)
        api.sim.idle_skip = skip
        assert api.sim.load("pru1", _pdm_clk()) == []
        out = api.pru_run_until("pru1", "halt", max_steps=200)
        core = api.sim.cores["pru1"]
        results[skip] = (out, core.counters.cycles, core.counters.instruction_count,
                         core.pc, core.skipped_cycles)
    on, off = results[True], results[False]
    assert on[:4] == off[:4]
    assert on[0]["reason"] == "max_steps" and on[2] == 200
    assert on[4] > 0 and off[4] == 0
