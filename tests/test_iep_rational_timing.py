from fractions import Fraction

from perif.iep import (
    CAP_CFG,
    CAPR0_REG0,
    CMP0_REG0,
    CMP_CFG,
    COUNT_REG0,
    COUNT_REG1,
    IepTimer,
)
from simulator import Simulator


IEP_BASE = 0x0002E000
IEP_CLOCK_SELECT = 0x00026030
GLOBAL_CFG = 0x00
CMP_STATUS = 0x74


def _write32(sim, addr, value):
    sim.memory.write(addr, (value & 0xFFFFFFFF).to_bytes(4, "little"))


def _read32(sim, addr):
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def _clocked_sim(tmp_path, *, pru="200", pru1="250", iep="300"):
    config = tmp_path / "clock.cfg"
    config.write_text(
        "[device]\n"
        f"pru_clock_mhz = {pru}\n"
        f"pru1_clock_mhz = {pru1}\n"
        f"iep_clock_mhz = {iep}\n"
        "\n[DRAM0]\nbase = 0x00000000\nsize = 0x2000\n"
        "read_latency = 2\nwrite_latency = 1\njitter = 0\n"
        "\n[DRAM1]\nbase = 0x00002000\nsize = 0x2000\n"
        "read_latency = 2\nwrite_latency = 1\njitter = 0\n",
        encoding="utf-8",
    )
    return Simulator(str(config))


def _load_loop(sim, core):
    assert sim.load(core, "nop\njmp 0\n") == []


def test_cmp0_resets_counter_through_the_shared_simulator_timer():
    sim = Simulator()
    _load_loop(sim, "pru0")
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)
    _write32(sim, IEP_BASE + CMP0_REG0, 50)
    _write32(sim, IEP_BASE + CMP_CFG, 0x3)

    sim.step("pru0", 300)

    # memory.cfg runs PRU0 at 250 MHz and the external IEP at 200 MHz:
    # 300 core cycles produce 240 IEP ticks; CMP0 reset at tick 200.
    assert sim.iep.count == 40
    assert _read32(sim, IEP_BASE + CMP_STATUS) & 1


def test_capture_and_64_bit_register_pairs_stay_memory_mapped():
    sim = Simulator()
    sim.iep.write32(COUNT_REG0, 0x89ABCDEF)
    sim.iep.write32(COUNT_REG1, 0x01234567)
    sim.iep.write32(CMP0_REG0 + 8, 0x76543210)
    sim.iep.write32(CMP0_REG0 + 12, 0xFEDCBA98)
    sim.iep.write32(CAP_CFG, 1)

    assert sim.iep.capture_event(0)

    assert sim.memory_read(IEP_BASE + COUNT_REG0, 8) == bytes.fromhex(
        "efcdab8967452301"
    )
    assert sim.memory_read(IEP_BASE + CMP0_REG0 + 8, 8) == bytes.fromhex(
        "1032547698badcfe"
    )
    assert sim.memory_read(IEP_BASE + CAPR0_REG0, 8) == bytes.fromhex(
        "efcdab8967452301"
    )
    assert {"global_cfg", "count", "cmp_cfg", "cmp_status", "compare"} <= (
        sim.iep.snapshot().keys()
    )


def test_peer_cores_advance_one_exact_shared_timeline_and_clock_can_switch(tmp_path):
    sim = _clocked_sim(tmp_path, pru="200", pru1="250", iep="300")
    for core in ("pru0", "pru1", "rtu0"):
        _load_loop(sim, core)
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)

    sim.step("pru0", 100)   # 500 ns -> 150 external-clock ticks
    sim.step("pru1", 125)   # same 500 ns; peer time is not counted twice
    assert sim.iep.count == 150
    assert sim.iep.now_ns == Fraction(500)

    sim.step("rtu0", 102)   # reaches 510 ns -> 3 more ticks at 300 MHz
    assert sim.iep.count == 153
    assert sim.iep.now_ns == Fraction(510)

    _write32(sim, IEP_CLOCK_SELECT, 1)  # select the 200 MHz OCP/core clock
    sim.iep.write32(COUNT_REG0, 0)
    sim.step("rtu0", 2)
    assert sim.iep.active_clock_hz == 200_000_000
    assert sim.iep.count == 2


def test_rational_core_clock_preserves_exact_iep_cadence(tmp_path):
    sim = _clocked_sim(tmp_path, pru="250", pru1="200.1", iep="250")
    _load_loop(sim, "pru1")
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)

    sim.step("pru1", 2001)  # exactly 10 us at 200.1 MHz

    assert sim.iep.now_ns == Fraction(10_000)
    assert sim.iep.count == 2500


def test_repeated_global_cfg_firmware_writes_preserve_rational_tick_phase():
    sim = Simulator("memory.cfg")
    sim.iep.global_cfg = 0x11
    assert sim.load(
        "pru0",
        "ldi32 r0, 0x2E000\n"
        "ldi r1, 0x11\n"
        "loop: sbbo &r1, r0, 0, 4\n"
        "qba loop\n",
    ) == []

    result = sim.step("pru0", 302)

    # The 302 instruction steps take 452 core cycles because the IEP writes
    # incur memory stalls. At 200 MHz IEP / 250 MHz PRU, that is 361 ticks.
    assert result["cycles"] == 452
    assert sim.iep.count == 361


def test_global_cfg_noop_and_non_timing_writes_preserve_tick_phase():
    iep = IepTimer()
    iep.write32(GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 1)
    assert iep._tick_remainder == 4

    iep.write32(GLOBAL_CFG, 0x11)  # exact same configuration
    iep.write32(GLOBAL_CFG, 0x111)  # bit 8 does not affect modeled timing
    assert iep._tick_remainder == 4

    iep.observe_core_cycles("pru0", 2)
    assert iep.count == 1
    assert iep._tick_remainder == 3


def test_global_cfg_enable_and_increment_transitions_reset_tick_phase():
    iep = IepTimer()
    iep.write32(GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 1)
    assert iep._tick_remainder == 4

    iep.write32(GLOBAL_CFG, 0x21)  # DEFAULT_INC changes from 1 to 2
    assert iep._tick_remainder == 0
    iep.observe_core_cycles("pru0", 2)
    assert iep.count == 0
    assert iep._tick_remainder == 4

    iep.write32(GLOBAL_CFG, 0x20)  # disable the counter
    assert iep._tick_remainder == 0
    iep.observe_core_cycles("pru0", 3)
    assert iep.count == 0
    assert iep._tick_remainder == 0

    iep.write32(GLOBAL_CFG, 0x21)  # enable it again
    iep.observe_core_cycles("pru0", 4)
    assert iep.count == 0
    assert iep._tick_remainder == 4
    iep.observe_core_cycles("pru0", 5)
    assert iep.count == 2
    assert iep._tick_remainder == 3


def test_iep_clock_source_and_rate_transitions_reset_tick_phase():
    iep = IepTimer()
    iep.write32(GLOBAL_CFG, 0x11)
    iep.observe_core_cycles("pru0", 1)
    assert iep._tick_remainder == 4

    iep.write_iepclk(1)  # select the 250 MHz OCP/core clock
    assert iep._tick_remainder == 0
    iep.observe_core_cycles("pru0", 2)
    assert iep.count == 1

    iep.write_iepclk(0)  # return to the 200 MHz external clock
    iep.observe_core_cycles("pru0", 3)
    assert iep._tick_remainder == 4
    iep.set_clock_mhz("100")
    assert iep._tick_remainder == 0
    iep.observe_core_cycles("pru0", 4)
    assert iep.count == 1
    assert iep._tick_remainder == 2


def test_repeated_iepclk_firmware_writes_preserve_tick_phase():
    sim = Simulator("memory.cfg")
    sim.iep.global_cfg = 0x11
    assert sim.load("pru0", "ldi32 r0, 0x26030\nldi r1, 0\n"
                    "loop: sbbo &r1, r0, 0, 4\nqba loop\n") == []
    result = sim.step("pru0", 302)
    assert result["cycles"] == 452
    assert sim.iep.count == 361


def test_unchanged_effective_clock_preserves_tick_phase():
    iep = IepTimer()
    iep.global_cfg = 0x11
    iep.observe_core_cycles("pru0", 1)
    iep.write_iepclk(0)
    iep.write_iepclk(2)  # unmodeled bit, still the external clock
    iep.set_clock_mhz("200")
    assert iep._tick_remainder == 4
    iep.observe_core_cycles("pru0", 2)
    assert iep.count == 1


def test_inactive_external_clock_change_preserves_ocp_phase():
    iep = IepTimer(ocp_clock_mhz="200", core_clocks_mhz={"pru0": "250"})
    iep.global_cfg = 0x11
    iep.write_iepclk(1)
    iep.observe_core_cycles("pru0", 1)
    iep.set_clock_mhz("100")
    assert iep._tick_remainder == 4
    iep.observe_core_cycles("pru0", 2)
    assert iep.count == 1


def test_lbbo_and_wait_stalls_reach_the_cycle_observer():
    sim = Simulator()
    assert sim.load(
        "pru0",
        "ldi r0, 0\n"
        "lbbo &r1, r0, 0, 4\n"
        "wbs 0\n"
        "halt\n",
    ) == []
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)

    core = sim.cores["pru0"]
    observe = core.cycle_observer
    elapsed = []
    core.cycle_observer = lambda cycles: (elapsed.append(cycles), observe(cycles))
    sim.step("pru0", 3)
    assert core.pc == 2
    assert core.counters.stall_cycles == 3  # two DRAM stalls and one WBS cycle
    sim.set_input("pru0", 0, True)
    sim.step("pru0", 2)

    assert elapsed == [1, 3, 1, 1, 1]
    assert sim.iep.count == 5


def test_rtu1_uses_slice_one_local_dram_mapping():
    sim = Simulator()
    sim.memory.write(0x00000000, b"\x11\x22\x33\x44")
    sim.memory.write(0x00002000, b"\xAA\xBB\xCC\xDD")
    assert sim.load("rtu1", "ldi r0, 0\nlbbo &r1, r0, 0, 4\nhalt\n") == []

    sim.step("rtu1", 3)

    assert sim.registers("rtu1")[1] == 0xDDCCBBAA


def test_step_paced_many_tracks_time_and_applies_perif_guard_only_when_enabled(
    tmp_path,
):
    sim = _clocked_sim(tmp_path, pru="250", pru1="200", iep="300")
    for core in ("pru0", "pru1", "rtu1"):
        _load_loop(sim, core)
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)

    sim.step_paced_many("pru0", ["pru1", "rtu1"], 10)

    assert sim.cores["pru0"].counters.cycles == 10
    assert sim.cores["pru1"].counters.cycles == 8
    assert sim.cores["rtu1"].counters.cycles == 8
    assert sim.iep.count == 12
    assert sim._perif["pru0"]._now_ns > 0  # clock runs with mux disabled

    guarded = _clocked_sim(tmp_path, pru="250", pru1="200", iep="300")
    for core in ("pru0", "pru1"):
        _load_loop(guarded, core)
        guarded.gpcfg_write(core, 1)
    guarded.step_paced_many("pru0", ["pru1"], 10, guard_ns=20)

    assert guarded.iep.core_time_units("pru0", 10) - guarded.iep.core_time_units(
        "pru1", guarded.cores["pru1"].counters.cycles
    ) == guarded.iep.nanoseconds_to_units(20)


def test_step_paced_many_catches_up_after_lead_memory_stalls(tmp_path):
    sim = _clocked_sim(tmp_path, pru="250", pru1="200", iep="300")
    assert sim.load("pru0", "ldi r0, 0\nlbbo &r1, r0, 0, 4\nnop\njmp 1\n") == []
    _load_loop(sim, "pru1")

    sim.step_paced_many("pru0", ["pru1"], 2)

    lead = sim.iep.time_units_to_ns(
        sim.iep.core_time_units("pru0", sim.cores["pru0"].counters.cycles)
    )
    follow = sim.iep.time_units_to_ns(
        sim.iep.core_time_units("pru1", sim.cores["pru1"].counters.cycles)
    )
    assert sim.cores["pru0"].counters.stall_cycles == 2
    assert lead == Fraction(16)
    assert follow == Fraction(20)
    assert lead <= follow <= lead + Fraction(5)  # at most one 200 MHz cycle


def test_step_paced_many_counts_follower_wbs_stalls_as_elapsed_time(tmp_path):
    sim = _clocked_sim(tmp_path, pru="250", pru1="200", iep="300")
    _load_loop(sim, "pru0")
    assert sim.load("pru1", "wbs 0\nnop\njmp 1\n") == []
    sim.set_input("pru1", 0, False)

    sim.step_paced_many("pru0", ["pru1"], 10)

    lead = sim.iep.time_units_to_ns(
        sim.iep.core_time_units("pru0", sim.cores["pru0"].counters.cycles)
    )
    follow = sim.iep.time_units_to_ns(
        sim.iep.core_time_units("pru1", sim.cores["pru1"].counters.cycles)
    )
    assert lead == follow == Fraction(40)
    assert sim.cores["pru1"].pc == 0
    assert sim.cores["pru1"].counters.stall_cycles == 8

    sim.set_input("pru1", 0, True)
    sim.step_paced_many("pru0", ["pru1"], 1)

    lead = sim.iep.time_units_to_ns(
        sim.iep.core_time_units("pru0", sim.cores["pru0"].counters.cycles)
    )
    follow = sim.iep.time_units_to_ns(
        sim.iep.core_time_units("pru1", sim.cores["pru1"].counters.cycles)
    )
    assert sim.cores["pru1"].pc == 1
    assert lead == Fraction(44)
    assert lead <= follow <= lead + Fraction(5)


def test_single_core_reset_rejoins_global_time_and_hardware_reset_restarts_it():
    sim = Simulator()
    _load_loop(sim, "pru0")
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)
    sim.step("pru0", 10)
    assert sim.iep.now_ns == Fraction(40)

    sim.reset("pru0")
    assert sim.iep.now_ns == Fraction(40)
    snapshot = sim.iep.snapshot()
    cycles = sim.cores["pru0"].counters.cycles
    sim.step("pru0", 2)
    assert sim.iep.now_ns == Fraction(48)
    assert sim.iep.count == 9

    sim.cores["pru0"].counters.cycles = cycles
    sim.iep.restore(snapshot)
    sim.step("pru0")
    assert sim.iep.now_ns == Fraction(44)
    assert sim.iep.count == 8

    sim.hard_reset()
    assert sim.iep.now_ns == 0
    assert sim.iep.count == 0
    assert sim.iep.read32(GLOBAL_CFG) == 0
    assert sim.iep.iepclk == 0


def test_ui_step_back_restores_the_shared_timer_timeline(monkeypatch):
    import ui.server as server

    sim = Simulator()
    monkeypatch.setattr(server, "sim", sim)
    _load_loop(sim, "pru0")
    _write32(sim, IEP_BASE + GLOBAL_CFG, 0x11)
    sim.step("pru0", 10)
    snapshot = server._snapshot("pru0")
    saved_time = sim.iep.now_ns
    saved_count = sim.iep.count

    sim.step("pru0", 10)
    server._restore("pru0", snapshot)

    assert sim.iep.now_ns == saved_time
    assert sim.iep.count == saved_count
    assert sim.cores["pru0"].counters.cycles == 10
