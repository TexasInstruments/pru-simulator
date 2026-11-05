"""Per-core GPI input delay (Simulator.set_gpi_delay).

Silicon (AM243, 333 MHz) shows 18 ns from an input edge to the output edge of
a `wbs` + `set` pair; the simulator without a delay answers in 1-2 cycles.
`set_gpi_delay(core, N)` makes R31 reads at core cycle c return the GPI word
as it was at cycle c - N. The native PRU0 -> wire -> PRU1 `wbs`/`set` latency
is 1 cycle, so a delay of 5 reproduces the silicon 6 cycles (18 ns).

Edge cycles below are each core's `counters.cycles` right after the step
in which the pin changed, i.e. the first cycle at which the new level is
visible.
"""

import pytest

from simulator import Simulator
from ui import server


def _first_change(sim, core, bit, steps, stimulus=None):
    """Step *core* one instruction at a time until R30[bit] goes high.

    *stimulus* maps a cycle number to a callable run just before the step
    whose instruction executes at that cycle.
    """
    pru = sim.cores[core]
    for _ in range(steps):
        if stimulus and pru.counters.cycles in stimulus:
            stimulus[pru.counters.cycles]()
        sim.step(core)
        if (pru.registers.read_full(30) >> bit) & 1:
            return pru.counters.cycles
    raise AssertionError(f"{core} R30[{bit}] never went high")


def _sample_r31_log(sim, core, steps, raise_at, pin):
    """Run back-to-back `mov r1, r31`; return the cycle at which r1[pin] first reads 1."""
    pru = sim.cores[core]
    for _ in range(steps):
        cycle = pru.counters.cycles
        if cycle == raise_at:
            sim.set_input(core, pin, True)
        sim.step(core)
        if (pru.registers.read_full(1) >> pin) & 1:
            return cycle
    raise AssertionError("pin change never observed")


# Unrolled so R31 is sampled on every cycle (a loop's branch skips cycles).
_SAMPLE_LOOP = "\n".join(["mov r1, r31"] * 80 + ["halt"])


def test_default_delay_is_zero_and_unchanged(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    assert sim.get_gpi_delay("pru0") == 0
    assert sim.load("pru0", "wbs r31, 14\nset r30, r30, 16\nhalt") == []
    raise_at = 5
    out = _first_change(sim, "pru0", 16, 50,
                        {raise_at: lambda: sim.set_input("pru0", 14, True)})
    assert 1 <= out - raise_at <= 2


def test_delay_shifts_r31_view(sim_config):
    seen = {}
    for delay in (0, 3):
        sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
        sim.set_gpi_delay("pru1", delay)
        assert sim.load("pru1", _SAMPLE_LOOP) == []
        seen[delay] = _sample_r31_log(sim, "pru1", 100, raise_at=10, pin=5)
    assert seen[3] - seen[0] == 3


def test_delay_is_per_core(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    sim.set_gpi_delay("pru1", 4)
    assert sim.get_gpi_delay("pru1") == 4
    assert sim.get_gpi_delay("pru0") == 0
    assert sim.cores["pru0"].io_port.gpi_delay_cycles == 0
    assert sim.cores["pru1"].io_port.gpi_delay_cycles == 4


@pytest.mark.parametrize("bad", [-1, 17, 2.0, True])
def test_rejects_bad_values(sim_config, bad):
    sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    with pytest.raises(ValueError):
        sim.set_gpi_delay("pru0", bad)
    assert sim.get_gpi_delay("pru0") == 0


def test_delay_survives_hard_reset(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    sim.set_gpi_delay("pru0", 4)
    sim.hard_reset()
    assert sim.get_gpi_delay("pru0") == 4
    fresh = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    assert fresh.get_gpi_delay("pru0") == 0


def test_gpi_delay_survives_snapshot_restore(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    sim.set_gpi_delay("pru0", 4)
    assert sim.load("pru0", _SAMPLE_LOOP) == []
    pru = sim.cores["pru0"]
    # Input edges at cycles 6 and 18; snapshot 10 cycles past the first.
    edges = {6: True, 18: False}
    while pru.counters.cycles < 16:
        if pru.counters.cycles in edges:
            sim.set_input("pru0", 3, edges.pop(pru.counters.cycles))
        sim.step("pru0")

    old_sim = server.sim
    server.sim = sim
    try:
        snap = server._snapshot("pru0")

        def run():
            pending = {18: False}
            log = []
            while pru.counters.cycles < 40:
                if pru.counters.cycles in pending:
                    sim.set_input("pru0", 3, pending.pop(pru.counters.cycles))
                cycle = pru.counters.cycles
                sim.step("pru0")
                log.append((cycle, pru.registers.read_full(1) >> 3 & 1))
            return log

        first = run()
        # Scramble GPI and the delay setting; restore must bring both back.
        sim.set_input("pru0", 3, False)
        sim.set_gpi_delay("pru0", 0)
        server._restore("pru0", snap)
        assert sim.get_gpi_delay("pru0") == 4
        second = run()
    finally:
        server.sim = old_sim
    assert first == second
    # The falling edge at 18 must still be seen 4 cycles late after restore:
    # cycles 18..21 read the old level (1), from 22 on the new level (0).
    assert [v for c, v in second if 16 <= c < 22] == [1] * 6
    assert all(v == 0 for c, v in second if c >= 22)


def _calibration_edges(sim_config, delay):
    sim = Simulator(sim_config(pru_clock_mhz=333, pru1_clock_mhz=333))
    sim.add_gpio_wire("pru0", 16, "pru1", 1)
    sim.set_gpio_drive_mask("pru0", 1 << 16)
    sim.set_gpi_delay("pru1", delay)
    # PRU0 raises GPO16 after a fixed run of NOPs; PRU1 waits for it.
    lead = "\n".join(["ldi r30, 0"] + ["nop"] * 20 + ["set r30, r30, 16", "halt"])
    assert sim.load("pru0", lead) == []
    assert sim.load("pru1", "ldi r30, 0\nwbs r31, 1\nset r30, r30, 11\nhalt") == []
    p0, p1 = sim.cores["pru0"], sim.cores["pru1"]
    edge0 = edge1 = None
    for _ in range(200):
        result = sim.step_paced_many("pru0", ["pru1"])
        assert result["complete"]
        if edge0 is None and (p0.registers.read_full(30) >> 16) & 1:
            edge0 = p0.counters.cycles
        if edge1 is None and (p1.registers.read_full(30) >> 11) & 1:
            edge1 = p1.counters.cycles
        if edge0 is not None and edge1 is not None:
            break
        # Lead halted: keep the follower stepping on its own.
        if p0.halted and edge1 is None:
            sim.step("pru1")
            if (p1.registers.read_full(30) >> 11) & 1:
                edge1 = p1.counters.cycles
                break
    assert edge0 is not None and edge1 is not None
    return edge1 - edge0


def test_calibration_matches_silicon_18ns_at_333mhz(sim_config):
    undelayed = _calibration_edges(sim_config, 0)
    delayed = _calibration_edges(sim_config, 5)
    print(f"PRU0 GPO16 -> PRU1 GPO11 edge-to-edge: delay0={undelayed} cycles, "
          f"delay5={delayed} cycles ({delayed * 1000 / 333:.1f} ns at 333 MHz)")
    assert undelayed == 1             # native simulator wire -> wbs -> set
    assert delayed == 6               # 18 ns at 333 MHz (AM243 silicon)
