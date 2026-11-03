"""Run-ahead scheduling for one-way producer/consumer cores (spec §10.3).

PRU1 (producer) drives the PDM pins, PRU0 (consumer) samples them through
the pin-driven SD. Data flows only PRU1 -> PRU0, so ``Simulator.run_ahead``
lets PRU1 run a time slice ahead and PRU0 catch up through the time-stamped
link edges. The equivalence gate: the result is identical to lockstep
(``step_paced_many("pru1", ["pru0"], ...)``) at the same PRU0 cycle count.

Run-ahead leaves the producer up to a slice ahead of the consumer, while
lockstep leaves it next to the consumer. The producer's state does not
depend on the consumer (that is the one-way precondition), so the tests
bring the producer that is behind up to the other one by stepping it alone,
and then compare everything: registers, counters, SD/IEP/link state, DRAM0,
shared RAM and each core's GPO edge history.
"""

from pathlib import Path

import pytest

from simulator import RunAheadRefused
from tests.test_idle_skip import (PDM_DELAY_PRODUCER_ASM, SD_CONSUMER,
                                  SD_IEP_CONSUMER, _build, _link_consumer,
                                  _state)

_TESTS = Path(__file__).parent
PATTERN_PRODUCER_ASM = _TESTS / "asm" / "pdm_pattern_producer.asm"


def _pair(config, producer_src=None):
    """Task 2's producer/consumer pair: pattern producer -> SD_CONSUMER."""
    if producer_src is None:
        producer_src = PATTERN_PRODUCER_ASM.read_text()
    return _build(config, SD_CONSUMER, producer_src, _link_consumer)


def _per_core(pins):
    return {name: [p for p in pins if p[0] == name] for name in ("pru0", "pru1")}


def _step_alone_to(core, cycles):
    while core.counters.cycles < cycles:
        core.step()
    assert core.counters.cycles == cycles


def _assert_same(a_sim, a_pins, b_sim, b_pins):
    """Align the producers, then require identical state and pin histories."""
    pa, pb = a_sim.cores["pru1"], b_sim.cores["pru1"]
    end = max(pa.counters.cycles, pb.counters.cycles)
    _step_alone_to(pa, end)
    _step_alone_to(pb, end)
    sa, sb = _state(a_sim), _state(b_sim)
    for key in sb:
        assert sa[key] == sb[key], key
    assert _per_core(a_pins) == _per_core(b_pins)
    assert a_sim._pdm_link.edge_history == b_sim._pdm_link.edge_history
    assert a_sim._pdm_link.sampled_bits == b_sim._pdm_link.sampled_bits


def _lockstep_to(sim, pru0_cycles):
    pru0 = sim.cores["pru0"]
    while pru0.counters.cycles < pru0_cycles:
        result = sim.step_paced_many("pru1", ["pru0"], 1, idle_skip=sim.idle_skip)
        assert result["complete"], result
    assert pru0.counters.cycles == pru0_cycles


def _equivalence(config, cycles, skip, producer_src=None):
    a_sim, a_pins = _pair(config, producer_src)
    a_sim.idle_skip = skip
    out = a_sim.run_ahead("pru1", "pru0", cycles,
                          producer_reads=[], consumer_writes=[])
    assert out["breakpoint"] is None
    pru0_a = a_sim.cores["pru0"].counters.cycles
    assert out["consumer_cycles"] == pru0_a >= cycles
    assert out["producer_cycles"] == a_sim.cores["pru1"].counters.cycles

    b_sim, b_pins = _pair(config, producer_src)
    b_sim.idle_skip = skip
    _lockstep_to(b_sim, pru0_a)
    skipped = {name: c.skipped_cycles for name, c in a_sim.cores.items()}
    assert out["skipped"] == {"pru1": skipped["pru1"], "pru0": skipped["pru0"]}
    _assert_same(a_sim, a_pins, b_sim, b_pins)
    return a_sim, out


@pytest.mark.parametrize("skip", [True, False], ids=["skip_on", "skip_off"])
def test_run_ahead_equals_lockstep(nominal_config, skip):
    sim, out = _equivalence(nominal_config, 400_000, skip)
    assert any(sim.memory_read(0, 0x100))          # samples were stored
    assert len(sim._pdm_link.sampled_bits[0]) > 20_000
    pru0 = sim.cores["pru0"]
    if skip:
        assert pru0.skipped_cycles > 0.5 * pru0.counters.cycles
    else:
        assert out["skipped"] == {"pru1": 0, "pru0": 0}


def test_run_ahead_equals_lockstep_delay_producer(nominal_config):
    """Both cores idle: PRU1 delay loops collapse, PRU0 polls jump."""
    sim, out = _equivalence(nominal_config, 200_000, True,
                            PDM_DELAY_PRODUCER_ASM.read_text())
    assert out["skipped"]["pru1"] > 0.5 * out["producer_cycles"]
    assert out["skipped"]["pru0"] > 0.5 * out["consumer_cycles"]


def test_run_ahead_equals_lockstep_unequal_clocks(sim_config):
    config = sim_config(pru_clock_mhz=200.0, pru1_clock_mhz=250.0)
    _equivalence(config, 100_000, True)


@pytest.mark.parametrize("slice_cycles", [1, 7, 100_000])
def test_slice_size_does_not_change_the_result(nominal_config, slice_cycles):
    a_sim, a_pins = _pair(nominal_config)
    a_sim.run_ahead("pru1", "pru0", 30_000, producer_reads=[], consumer_writes=[],
                    slice_cycles=slice_cycles)
    b_sim, b_pins = _pair(nominal_config)
    _lockstep_to(b_sim, a_sim.cores["pru0"].counters.cycles)
    _assert_same(a_sim, a_pins, b_sim, b_pins)


def test_repeated_calls_continue_where_the_last_stopped(nominal_config):
    a_sim, a_pins = _pair(nominal_config)
    total = 0
    for chunk in (5_000, 1, 12_345, 20_000):
        out = a_sim.run_ahead("pru1", "pru0", chunk,
                              producer_reads=[], consumer_writes=[])
        assert out["consumer_cycles"] >= chunk
        total += out["consumer_cycles"]
    assert a_sim.cores["pru0"].counters.cycles == total
    b_sim, b_pins = _pair(nominal_config)
    _lockstep_to(b_sim, total)
    _assert_same(a_sim, a_pins, b_sim, b_pins)


def test_halted_producer_lets_the_consumer_finish(nominal_config):
    halting = PDM_DELAY_PRODUCER_ASM.read_text().replace(
        "    qba   LOOP\n", "    add r20, r20, 1\n    qbgt LOOP, r20, 250\n    halt\n")
    sim, _ = _pair(nominal_config, halting)
    out = sim.run_ahead("pru1", "pru0", 60_000, producer_reads=[], consumer_writes=[])
    assert sim.cores["pru1"].halted
    assert out["consumer_cycles"] >= 60_000
    assert out["consumer_cycles"] > out["producer_cycles"]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def _refused(sim, **kw):
    kw.setdefault("producer_reads", [])
    kw.setdefault("consumer_writes", [])
    before = {name: c.counters.cycles for name, c in sim.cores.items()}
    with pytest.raises(RunAheadRefused) as err:
        sim.run_ahead("pru1", "pru0", 10_000, **kw)
    assert {name: c.counters.cycles for name, c in sim.cores.items()} == before
    assert err.value.reasons
    assert err.value.reasons == sim.one_way_violations(
        "pru1", "pru0", kw["producer_reads"], kw["consumer_writes"],
        **{k: v for k, v in kw.items()
           if k in ("producer_writes", "consumer_reads")})
    return " | ".join(err.value.reasons)


def test_demo_pair_has_no_violations(nominal_config):
    sim, _ = _pair(nominal_config)
    assert sim.one_way_violations("pru1", "pru0", [], []) == []


def test_refuses_on_gpio_wire(nominal_config):
    sim, _ = _pair(nominal_config)
    sim.add_gpio_wire("pru0", 5, "pru1", 5)
    text = _refused(sim)
    assert "wire" in text and "pru0" in text and "pru1" in text


def test_refuses_on_range_overlap(nominal_config):
    sim, _ = _pair(nominal_config)
    text = _refused(sim, producer_reads=[(0x10100, 4)],
                    consumer_writes=[(0x10100, 0x400)])
    assert "0x10100" in text.lower()


def test_adjacent_ranges_do_not_overlap(nominal_config):
    sim, _ = _pair(nominal_config)
    assert sim.one_way_violations("pru1", "pru0", [(0x10000, 0x100)],
                                  [(0x10100, 0x400)]) == []


def test_refuses_on_producer_write_consumer_read_overlap(nominal_config):
    """PRU0 would see PRU1's writes up to a slice early."""
    sim, _ = _pair(nominal_config)
    text = _refused(sim, producer_writes=[(0x10000, 0x40)],
                    consumer_reads=[(0x1003C, 8)])
    assert "0x10000" in text.lower()


def test_refuses_when_perif_enabled(nominal_config):
    sim, _ = _pair(nominal_config)
    sim.gpcfg_write("pru0", 1)
    assert "PERIF" in _refused(sim)


def test_refuses_on_perif_loopback(nominal_config):
    sim, _ = _pair(nominal_config)
    sim.perif_loopback(0, True)
    assert "loopback" in _refused(sim).lower()


@pytest.mark.parametrize("program", ["producer", "consumer"])
@pytest.mark.parametrize("xfr", ["xout  10, &r2, 4", "xin   11, &r2, 8",
                                 "xchg  12, &r2, 4", "xout  15, &r2, 4",
                                 "xin   r1.b0, &r2, 4"])
def test_refuses_on_scratchpad_xfr(nominal_config, program, xfr):
    extra = f"    {xfr}\n"
    producer = PATTERN_PRODUCER_ASM.read_text()
    consumer = SD_CONSUMER
    if program == "producer":
        producer = producer.replace("start:\n", "start:\n" + extra)
    else:
        consumer = extra + consumer
    sim, _ = _build(nominal_config, consumer, producer, _link_consumer)
    text = _refused(sim)
    assert ("pru1" if program == "producer" else "pru0") in text
    assert "XFR" in text or "scratchpad" in text


def test_mac_xfr_is_allowed(nominal_config):
    """MAC (ID 0) is a per-core accelerator, not shared."""
    producer = PATTERN_PRODUCER_ASM.read_text().replace(
        "start:\n", "start:\n    xin   0, &r26, 4\n")
    sim, _ = _build(nominal_config, SD_CONSUMER, producer, _link_consumer)
    assert sim.one_way_violations("pru1", "pru0", [], []) == []


@pytest.mark.parametrize("program", ["producer", "consumer"])
@pytest.mark.parametrize("access", [
    "lbco  &r1, c26, 0x10, 4",
    "sbco  &r1, c26, 0, 4",
    "ldi32 r10, 0x0002E010\n    lbbo  &r1, r10, 0, 4",
    "ldi32 r10, 0x00026030\n    sbbo  &r1, r10, 0, 4",   # IEPCLK
])
def test_refuses_on_iep_access(nominal_config, program, access):
    extra = f"    {access}\n"
    producer = PATTERN_PRODUCER_ASM.read_text()
    consumer = SD_CONSUMER
    if program == "producer":
        producer = producer.replace("start:\n", "start:\n" + extra)
    else:
        consumer = extra + consumer
    sim, _ = _build(nominal_config, consumer, producer, _link_consumer)
    text = _refused(sim)
    assert "IEP" in text
    assert ("pru1" if program == "producer" else "pru0") in text


def test_refuses_iep_reading_consumer(nominal_config):
    """Task 2's IEP-reading consumer: the IEP follows the furthest core."""
    sim, _ = _build(nominal_config, SD_IEP_CONSUMER, PATTERN_PRODUCER_ASM.read_text(),
                    _link_consumer)
    assert "IEP" in _refused(sim)


@pytest.mark.parametrize("field,value", [("cmp_cfg", 0x2), ("cmp_cfg", 0x1),
                                          ("cap_cfg", 0x1)])
def test_refuses_on_iep_event_config(nominal_config, field, value):
    sim, _ = _pair(nominal_config)
    setattr(sim.iep, field, value)
    assert "IEP" in _refused(sim)


def test_enabled_iep_counter_alone_is_allowed(nominal_config):
    """A counting IEP nobody reads ends at the same count: no violation."""
    sim, _ = _pair(nominal_config)
    sim.iep.global_cfg = 0x551
    assert sim.one_way_violations("pru1", "pru0", [], []) == []
    a_sim, a_pins = _pair(nominal_config)
    a_sim.iep.global_cfg = 0x551
    a_sim.run_ahead("pru1", "pru0", 20_000, producer_reads=[], consumer_writes=[])
    b_sim, b_pins = _pair(nominal_config)
    b_sim.iep.global_cfg = 0x551
    _lockstep_to(b_sim, a_sim.cores["pru0"].counters.cycles)
    _assert_same(a_sim, a_pins, b_sim, b_pins)
    assert a_sim.iep.count > 0


def test_refuses_on_reversed_pdm_link(nominal_config):
    sim, _ = _pair(nominal_config)
    sim.attach_pdm_link(producer="pru0", consumer="pru1")
    assert "link" in _refused(sim).lower()


def test_refuses_same_core(nominal_config):
    sim, _ = _pair(nominal_config)
    with pytest.raises(ValueError):
        sim.one_way_violations("pru1", "pru1", [], [])


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------

def _first_pc(core, opcode, operand_text=""):
    for instr in core.instructions:
        if instr.opcode == opcode and operand_text in instr.source_text:
            return instr.address
    raise AssertionError(opcode)


def _lockstep_to_breakpoint(sim):
    """The dashboard's run_multicore loop: one paced step, then check."""
    lead, partner = sim.cores["pru1"], sim.cores["pru0"]
    for _ in range(1_000_000):
        assert sim.step_paced_many("pru1", ["pru0"], 1,
                                   idle_skip=sim.idle_skip)["complete"]
        if lead.pc in lead.breakpoints:
            return "pru1"
        if partner.pc in partner.breakpoints:
            return "pru0"
    raise AssertionError("no breakpoint hit")


@pytest.mark.parametrize("skip", [True, False], ids=["skip_on", "skip_off"])
def test_stops_at_breakpoint(nominal_config, skip):
    sim, _ = _pair(nominal_config)
    sim.idle_skip = skip
    pru0 = sim.cores["pru0"]
    store = _first_pc(pru0, "SBBO")
    pru0.breakpoints.add(store)
    out = sim.run_ahead("pru1", "pru0", 400_000, producer_reads=[], consumer_writes=[])
    assert out["breakpoint"] == "pru0"
    assert pru0.pc == store
    assert out["consumer_cycles"] < 400_000

    # PRU0 stops exactly where lockstep stops; PRU1 may be ahead by a slice.
    ref, _ = _pair(nominal_config)
    ref.idle_skip = skip
    ref.cores["pru0"].breakpoints.add(store)
    assert _lockstep_to_breakpoint(ref) == "pru0"
    a, b = _state(sim), _state(ref)
    assert a["pru0"] == b["pru0"]
    assert a["mem"] == b["mem"]

    # Resuming from the breakpoint runs on to the next store.
    out = sim.run_ahead("pru1", "pru0", 400_000, producer_reads=[], consumer_writes=[])
    assert out["breakpoint"] == "pru0" and pru0.pc == store
    assert out["consumer_cycles"] > 0


@pytest.mark.parametrize("skip", [True, False], ids=["skip_on", "skip_off"])
def test_stops_at_producer_breakpoint_like_lockstep(nominal_config, skip):
    """A producer breakpoint stops both cores where lockstep stops them."""
    def setup(sim):
        sim.idle_skip = skip
        pru1 = sim.cores["pru1"]
        rise = _first_pc(pru1, "OR", "r30")
        pru1.breakpoints.add(rise)
        return rise

    sim, pins = _pair(nominal_config)
    rise = setup(sim)
    # Run past the first hit so the consumer has real work behind it.
    for _ in range(50):
        out = sim.run_ahead("pru1", "pru0", 400_000,
                            producer_reads=[], consumer_writes=[])
        assert out["breakpoint"] == "pru1"
        assert sim.cores["pru1"].pc == rise

    ref, ref_pins = _pair(nominal_config)
    setup(ref)
    for _ in range(50):
        assert _lockstep_to_breakpoint(ref) == "pru1"
    a, b = _state(sim), _state(ref)
    for key in b:
        assert a[key] == b[key], key
    assert _per_core(pins) == _per_core(ref_pins)


def test_consumer_breakpoint_during_producer_catch_up_wins(nominal_config):
    """PRU0 reaches its breakpoint before PRU1's: PRU0 is reported."""
    sim, _ = _pair(nominal_config)
    pru0, pru1 = sim.cores["pru0"], sim.cores["pru1"]
    store = _first_pc(pru0, "SBBO")
    pru0.breakpoints.add(store)
    # PRU1 already 3000 cycles ahead hits its breakpoint within one period;
    # PRU0's first store (~1400 cycles in) comes first in time.
    sim.step("pru1", 3000)
    pru1.breakpoints.add(_first_pc(pru1, "JMP"))
    out = sim.run_ahead("pru1", "pru0", 400_000, producer_reads=[],
                        consumer_writes=[], slice_cycles=4096)
    assert out["breakpoint"] == "pru0"
    assert pru0.pc == store
