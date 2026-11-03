"""Pin-driven SD input: PdmLink, per-channel source modes, CLK_SEL/CLK_INV.

PRU1 runs tests/asm/pdm_pattern_producer.asm (clock on GPO0, data on GPO1);
PRU0's SD channels sample those pins through a PdmLink.

The pattern helpers at module level (PDM_PATTERN, PDM_PERIOD_CYCLES,
rising_bits(), falling_bits(), load_pattern_producer(), cic3()) are meant to
be imported by later PDM tests.
"""

import os

import pytest

from pru_io.pdm_link import PdmLink, PdmLinkError
from pru_io.sd_filter import SigmaDeltaFilter
from simulator import Simulator

_ASM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asm")
PDM_PRODUCER_ASM = os.path.join(_ASM_DIR, "pdm_pattern_producer.asm")

#: Pattern word shifted out by pdm_pattern_producer.asm, LSB first.
PDM_PATTERN = 0xC3A5_9E17
#: Producer cycles per PDM clock period (7 high, 7 low).
PDM_PERIOD_CYCLES = 14
#: Instructions before label LOOP (ldi32 = 2, ldi, ldi).
PDM_PREAMBLE_INSTRUCTIONS = 4

_CLK_SEL_BASE = 0x26048
_SAMPLE_SIZE_BASE = 0x2604C


def rising_bits(n: int) -> list[int]:
    """Data bits seen by the first *n* rising clock edges."""
    return [(PDM_PATTERN >> ((2 * k) % 32)) & 1 for k in range(n)]


def falling_bits(n: int) -> list[int]:
    """Data bits seen by the first *n* falling clock edges."""
    return [(PDM_PATTERN >> ((2 * k + 1) % 32)) & 1 for k in range(n)]


def cic3(bits: list[int]) -> tuple[int, int, int]:
    """Triple integration of *bits*, as the SD channel accumulators do it."""
    acc1 = acc2 = acc3 = 0
    for bit in bits:
        acc1 += bit
        acc2 += acc1
        acc3 += acc2
    return acc1, acc2, acc3


def load_pattern_producer(sim: Simulator, core: str = "pru1") -> None:
    with open(PDM_PRODUCER_ASM) as f:
        source = f.read()
    assert f"0x{PDM_PATTERN >> 16:04X}_{PDM_PATTERN & 0xFFFF:04X}" in source
    assert sim.load(core, source) == []


_IDLE_LOOP = "idle:\n    jmp idle\n"


def write_reg(sim: Simulator, addr: int, value: int) -> None:
    sim.memory.write(addr, value.to_bytes(4, "little"))


def configure_channel(sim: Simulator, ch: int, clk_sel: int, clk_inv: int = 0,
                      osr: int = 96) -> None:
    write_reg(sim, _CLK_SEL_BASE + 8 * ch, (clk_inv << 2) | clk_sel)
    write_reg(sim, _SAMPLE_SIZE_BASE + 8 * ch, osr - 1)


def make_linked_sim(nominal_config, keep_history: bool = True):
    sim = Simulator(config_path=nominal_config)
    load_pattern_producer(sim)
    assert sim.load("pru0", _IDLE_LOOP) == []
    link = sim.attach_pdm_link(keep_history=keep_history)
    return sim, link


def run_periods(sim: Simulator, periods: int) -> None:
    """Run the producer through *periods* full clock periods, then catch the
    consumer up so it has applied every edge the producer made."""
    sim.step("pru1", PDM_PREAMBLE_INSTRUCTIONS + periods * PDM_PERIOD_CYCLES)
    p, c = sim.cores["pru1"], sim.cores["pru0"]
    target = sim.iep.core_time_units("pru1", p.counters.cycles)
    while sim.iep.core_time_units("pru0", c.counters.cycles) <= target:
        c.step()


def test_default_source_is_modulator(nominal_config):
    sim = Simulator(config_path=nominal_config)
    sd = sim.cores["pru0"].io_port.sd_filter
    assert SigmaDeltaFilter.SOURCES == ("modulator", "pin", "off")
    assert sd.sources == ["modulator"] * 3
    assert sd.pin_link is None


def test_set_source_rejects_bad_arguments(nominal_config):
    sim = Simulator(config_path=nominal_config)
    with pytest.raises(ValueError):
        sim.set_sd_source("pru0", 3, "pin")
    with pytest.raises(ValueError):
        sim.set_sd_source("pru0", 0, "gpio")


def test_pattern_differs_between_halves():
    assert rising_bits(16) != falling_bits(16)


def test_pin_mode_rising_edge_matches_cic(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1, clk_inv=0, osr=96)
    sim.set_sd_source("pru0", 0, "pin")
    periods = 250
    run_periods(sim, periods)
    ch0 = sim.cores["pru0"].io_port.sd_filter.channels[0]
    expected = rising_bits(periods)
    assert link.sampled_bits[0] == expected
    assert (ch0.acc1, ch0.acc2, ch0.acc3) == cic3(expected)
    assert ch0.valid   # 250 ticks > OSR 96


def test_shared_clock_inverted_channel_samples_falling_edge(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1, clk_inv=0)
    configure_channel(sim, 1, clk_sel=2, clk_inv=1)
    sim.set_sd_source("pru0", 0, "pin")
    sim.set_sd_source("pru0", 1, "pin")
    periods = 100
    run_periods(sim, periods)
    sd = sim.cores["pru0"].io_port.sd_filter
    ch0, ch1 = sd.channels[0], sd.channels[1]
    assert link.sampled_bits[1] == falling_bits(periods)
    assert (ch1.acc1, ch1.acc2, ch1.acc3) == cic3(falling_bits(periods))
    assert (ch0.acc1, ch0.acc2, ch0.acc3) == cic3(rising_bits(periods))
    assert ch0.acc3 != ch1.acc3


def test_unselected_clock_gets_no_ticks(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1)
    configure_channel(sim, 1, clk_sel=1)     # own SD1_CLK: not wired
    sim.set_sd_source("pru0", 0, "pin")
    sim.set_sd_source("pru0", 1, "pin")
    run_periods(sim, 50)
    sd = sim.cores["pru0"].io_port.sd_filter
    assert sd.channels[0].acc1 > 0
    assert sd.channels[1].acc1 == 0
    assert link.sampled_bits[1] == []


def test_clk_sel_zero_gets_no_ticks(nominal_config):
    sim, _ = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=0)     # GPI R31[16]: not driven by the link
    sim.set_sd_source("pru0", 0, "pin")
    run_periods(sim, 50)
    assert sim.cores["pru0"].io_port.sd_filter.channels[0].acc1 == 0


def test_off_mode_never_ticks(nominal_config):
    sim = Simulator(config_path=nominal_config)
    assert sim.load("pru0", _IDLE_LOOP) == []
    sd = sim.cores["pru0"].io_port.sd_filter
    sim.set_sd_source("pru0", 2, "off")
    sim.step("pru0", 10_000)
    ch2 = sd.channels[2]
    assert (ch2.acc1, ch2.acc2, ch2.acc3) == (0, 0, 0)
    assert sd.channels[0].acc1 > 0   # modulator channels still run


def test_tick_n_equals_repeated_tick():
    a, b = SigmaDeltaFilter(200.0), SigmaDeltaFilter(200.0)
    for sd in (a, b):
        for mod in sd.modulators:
            mod.signal = "sine"
            mod.sd_clock_mhz = 3.072
    for _ in range(1000):
        a.tick()
    b.tick_n(1000)
    assert a.snapshot() == b.snapshot()


def test_tick_n_skips_non_modulator_channels():
    sd = SigmaDeltaFilter(200.0)
    for ch in range(3):
        sd.set_source(ch, "pin" if ch < 2 else "off")
    before = sd.snapshot()
    sd.tick_n(10**12)          # O(1): would never finish if it looped
    assert sd.snapshot()["channels"] == before["channels"]


def test_consumer_ahead_of_producer_raises(nominal_config):
    sim, _ = make_linked_sim(nominal_config)
    sim.step("pru0", 1)        # time 0 == producer horizon: allowed
    with pytest.raises(PdmLinkError,
                       match="PDM link: consumer pru0 is ahead of producer pru1"):
        sim.step("pru0", 1)


def test_halted_producer_has_infinite_horizon(nominal_config):
    sim = Simulator(config_path=nominal_config)
    assert sim.load("pru1", "halt\n") == []
    assert sim.load("pru0", _IDLE_LOOP) == []
    link = sim.attach_pdm_link()
    sim.step("pru1", 1)
    assert link.horizon() == float("inf")
    sim.step("pru0", 100)      # no error


def test_edge_stamp_is_retire_time(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    sim.step("pru1", PDM_PREAMBLE_INSTRUCTIONS + PDM_PERIOD_CYCLES)
    # First pass: data <- 1 at LOOP+3, rising edge at LOOP+5 (LOOP = cycle 4).
    t = lambda cycles: sim.iep.core_time_units("pru1", cycles)
    assert link.edge_history[:2] == [(t(4 + 3 + 1), 0b10), (t(4 + 5 + 1), 0b11)]


def test_next_valid_time_predicts_valid_rise(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1, osr=32)
    sim.set_sd_source("pru0", 0, "pin")
    sim.step("pru1", PDM_PREAMBLE_INSTRUCTIONS + 40 * PDM_PERIOD_CYCLES)
    predicted = link.next_valid_time(0)
    assert predicted is not None
    assert link.next_valid_time(2) is None          # ch2 not in pin mode
    c = sim.cores["pru0"]
    ch0 = c.io_port.sd_filter.channels[0]
    seen = None
    while seen is None:
        now = sim.iep.core_time_units("pru0", c.counters.cycles)
        c.step()
        if ch0.valid:
            seen = now
    assert seen == predicted


def test_snapshot_restore_roundtrip(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1, osr=16)
    configure_channel(sim, 1, clk_sel=2, clk_inv=1, osr=16)
    sim.set_sd_source("pru0", 0, "pin")
    sim.set_sd_source("pru0", 1, "pin")
    run_periods(sim, 10)
    sim.step("pru1", 30 * PDM_PERIOD_CYCLES)        # queue edges ahead
    sd = sim.cores["pru0"].io_port.sd_filter
    sd_snap, link_snap = sd.snapshot(), link.snapshot()
    consumer_cycles = sim.cores["pru0"].counters.cycles

    def consume():
        link.advance_consumer(consumer_cycles + 20 * PDM_PERIOD_CYCLES)
        return ([(ch.acc1, ch.acc2, ch.acc3, ch.valid) for ch in sd.channels],
                {k: list(v) for k, v in link.sampled_bits.items()})

    first = consume()
    sd.restore(sd_snap)
    link.restore(link_snap)
    assert consume() == first
    assert sd.snapshot()["sources"] == ["pin", "pin", "modulator"]


def test_detach_pdm_link(nominal_config):
    sim, link = make_linked_sim(nominal_config)
    sim.detach_pdm_link()
    assert sim.cores["pru0"].io_port.sd_filter.pin_link is None
    assert link.on_producer_gpo not in sim.cores["pru1"].io_port.gpo_listeners
    sim.step("pru0", 10)       # no horizon check once detached


def test_gpo_listener_called_only_on_change():
    from pru_io.io_port import IOPort
    port = IOPort()
    calls = []
    port.gpo_listeners.append(lambda old, new: calls.append((old, new)))
    port.write_r30(0x3)
    port.write_r30(0x3)
    port.write_r30(0x3 | (1 << 25))   # bit 25 is outside the 20-bit GPO word
    port.write_r30(0x1)
    assert calls == [(0, 3), (3, 1)]


def test_pdm_link_standalone_construction(nominal_config):
    sim = Simulator(config_path=nominal_config)
    link = PdmLink(sim.cores["pru1"], sim.cores["pru0"], sim.iep.core_time_units,
                   clock_pin=0, data_pins={0: 1})
    assert link.producer is sim.cores["pru1"]
    assert link.consumer is sim.cores["pru0"]
    assert link.edge_history == []


def _catch_up_consumer(sim: Simulator) -> None:
    p, c = sim.cores["pru1"], sim.cores["pru0"]
    target = sim.iep.core_time_units("pru1", p.counters.cycles)
    while sim.iep.core_time_units("pru0", c.counters.cycles) <= target:
        c.step()


def test_producer_reset_is_a_real_falling_edge(nominal_config):
    """A reset drives the clock low at the reset instant: one real falling
    edge, sampled with the reset data level (0). The first write after the
    reset must not be decoded against the stale pre-reset word."""
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1, clk_inv=0)
    configure_channel(sim, 1, clk_sel=2, clk_inv=1)
    sim.set_sd_source("pru0", 0, "pin")
    sim.set_sd_source("pru0", 1, "pin")
    # 3 full periods, then LOOP+0..+5: stopped just after a rising edge.
    sim.step("pru1", PDM_PREAMBLE_INSTRUCTIONS + 3 * PDM_PERIOD_CYCLES + 6)
    assert sim.cores["pru1"].io_port.gpo & 1 == 1          # clock high
    _catch_up_consumer(sim)
    assert link.sampled_bits[0] == rising_bits(4)
    assert link.sampled_bits[1] == falling_bits(3)

    sim.reset("pru1")
    reset_time = sim.iep.core_time_units("pru1", 0)
    assert link.edge_history[-1] == (reset_time, 0)
    periods = 5
    sim.step("pru1", PDM_PREAMBLE_INSTRUCTIONS + periods * PDM_PERIOD_CYCLES)
    _catch_up_consumer(sim)
    assert link.sampled_bits[0] == rising_bits(4) + rising_bits(periods)
    assert link.sampled_bits[1] == falling_bits(3) + [0] + falling_bits(periods)


def test_stamp_uses_instruction_start_even_if_stall_precedes_write(nominal_config):
    """The stamp rule must not depend on where an opcode counts its stalls."""
    sim = Simulator(config_path=nominal_config)
    assert sim.load("pru1", "ldi r1, 1\nmov r30, r1\nhalt\n") == []
    link = sim.attach_pdm_link(keep_history=True)
    p = sim.cores["pru1"]
    real_write = p.io_port.write_r30

    def stalled_write(value, wstrb=0xF):
        p.counters.stall(5)           # stall counted before the writeback
        real_write(value, wstrb)

    p.io_port.write_r30 = stalled_write
    sim.step("pru1", 2)
    assert link.edge_history == [(sim.iep.core_time_units("pru1", 1 + 1), 1)]


def test_stamp_for_stalling_lbbo_into_r30(nominal_config):
    sim = Simulator(config_path=nominal_config)
    p = sim.cores["pru1"]
    sim.memory.write(p._map_data_addr(0x100), (0x3).to_bytes(4, "little"))
    assert sim.load("pru1", "ldi r1, 0x100\nlbbo &r30, r1, 0, 4\nhalt\n") == []
    link = sim.attach_pdm_link(keep_history=True)
    sim.step("pru1", 2)
    assert p.counters.stall_cycles > 0             # the LBBO really stalled
    assert link.edge_history == [(sim.iep.core_time_units("pru1", 1 + 1), 3)]


def test_queue_cap_raises_from_the_producer_without_losing_edges(nominal_config):
    """PRU1 running alone (PRU0 never steps) must not grow the queue forever."""
    sim, link = make_linked_sim(nominal_config, keep_history=False)
    link.max_queue = 20
    with pytest.raises(PdmLinkError,
                       match=r"PDM link: consumer pru0 is not advancing \(queue > 20 edges\)"):
        sim.step("pru1", 10_000)
    assert len(link._queue) == 21          # the edge that crossed the cap is queued
    # Every further producer edge raises again; none is dropped.
    with pytest.raises(PdmLinkError):
        sim.step("pru1", 10_000)
    assert len(link._queue) == 22


def test_queue_cap_default_and_constructor_kwarg(nominal_config):
    sim = Simulator(config_path=nominal_config)
    p, c = sim.cores["pru1"], sim.cores["pru0"]
    assert PdmLink.MAX_QUEUE == 1_000_000
    assert PdmLink(p, c, sim.iep.core_time_units, 0, {0: 1}).max_queue == 1_000_000
    assert PdmLink(p, c, sim.iep.core_time_units, 0, {0: 1}, max_queue=7).max_queue == 7


def test_producer_reset_at_the_cap_still_completes(nominal_config):
    sim, link = make_linked_sim(nominal_config, keep_history=False)
    link.max_queue = 1
    with pytest.raises(PdmLinkError):
        sim.step("pru1", 10_000)
    sim.reset("pru1")                       # its pin drop is queued, no raise
    assert sim.cores["pru1"].pc == 0 and sim.cores["pru1"].io_port.gpo == 0


def test_consumer_reset_drops_the_stale_edges(nominal_config):
    """A reset of the consumer rebases it onto the shared time; the edges
    stamped before its new start must not reach it as one burst."""
    sim, link = make_linked_sim(nominal_config)
    configure_channel(sim, 0, clk_sel=1, osr=96)
    sim.set_sd_source("pru0", 0, "pin")
    sim.step("pru1", PDM_PREAMBLE_INSTRUCTIONS + 40 * PDM_PERIOD_CYCLES)
    assert len(link._queue) > 40                       # PRU0 never ran
    gpo = sim.cores["pru1"].io_port.gpo
    sim.reset("pru0")
    start = sim.iep.core_time_units("pru0", 0)
    assert start == sim.iep.core_time_units("pru1", sim.cores["pru1"].counters.cycles)
    assert all(stamp >= start for stamp, _ in link._queue)
    assert link._last_word == gpo                      # the pin levels as they stand
    channel = sim.cores["pru0"].io_port.sd_filter.channels[0]
    sim.step("pru0", 1)                                # no burst of 40 clock edges
    assert channel._sample_count == 0 and link.sampled_bits[0] == []
    # The pair then runs on normally: the next producer edges are sampled.
    run_periods(sim, 5)
    assert len(link.sampled_bits[0]) == 5
