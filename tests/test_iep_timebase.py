"""Deterministic AM243x ICSSG IEP counter regressions.

Hardware assumptions are taken from the checked-in TI/OpenPRU definitions:
IEP0 is local through c26 at 0x0002E000, GLOBAL_CFG is +0x00,
COUNT_REG0/COUNT_LO is +0x10, and COUNT_REG1/COUNT_HI is +0x14.
Reading COUNT_LO latches COUNT_HI for a later separate read.
"""

from pathlib import Path

import pytest

import core.iep as iep_module
from simulator import Simulator


IEP_BASE = 0x0002E000
IEP_GLOBAL_CFG = IEP_BASE + 0x00
IEP_COUNT_LO = IEP_BASE + 0x10
IEP_COUNT_HI = IEP_BASE + 0x14
ICSS_CFG_IEPCLK = 0x00026030
SHARED_RAM = 0x00010000

GLOBAL_CFG_RESET = 0x00000550
IEPCLK_OCP_EN = 0x1


def _write32(sim: Simulator, addr: int, value: int) -> None:
    sim.memory.write(addr, (value & 0xFFFF_FFFF).to_bytes(4, "little"))


def _read32(sim: Simulator, addr: int) -> int:
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def _read_count(sim: Simulator) -> int:
    return int.from_bytes(sim.memory_read(IEP_COUNT_LO, 8), "little")


def _write_count(sim: Simulator, value: int) -> None:
    sim.memory.write(IEP_COUNT_LO, (value & 0xFFFF_FFFF_FFFF_FFFF).to_bytes(8, "little"))


def _configure_counter(
    sim: Simulator,
    *,
    ocp_enabled: bool = True,
    default_inc: int = 1,
    enabled: bool = True,
) -> None:
    _write32(sim, ICSS_CFG_IEPCLK, IEPCLK_OCP_EN if ocp_enabled else 0)
    _write32(sim, IEP_GLOBAL_CFG, (default_inc << 4) | int(enabled))


def _load_loop(sim: Simulator, core: str) -> None:
    assert sim.load(core, "nop\njmp 0\n") == []


def test_cycle_observer_hot_path_uses_no_fraction_or_timeline_scan(monkeypatch):
    class NoTimelineScan(dict):
        def __iter__(self):
            raise AssertionError("cycle observer iterated every core timeline")

        def items(self):
            raise AssertionError("cycle observer scanned every core timeline")

        def keys(self):
            raise AssertionError("cycle observer scanned every core timeline")

        def values(self):
            raise AssertionError("cycle observer scanned every core timeline")

    def reject_fraction(*args, **kwargs):
        raise AssertionError("cycle observer constructed a Fraction")

    sim = Simulator()
    sim.iep._core_timelines = NoTimelineScan(sim.iep._core_timelines)
    monkeypatch.setattr(iep_module, "Fraction", reject_fraction)

    for cycle in range(1, 257):
        sim.iep.observe_core_cycles("pru0", cycle)
    assert sim.iep.count == 0

    sim.iep.write_iepclk(IEPCLK_OCP_EN)
    sim.iep.write_global_cfg(0x11)
    sim.iep.write_count(low=0, high=0)
    assert sim.iep._ticks_per_time_unit == 1
    for cycle in range(257, 513):
        sim.iep.observe_core_cycles("pru0", cycle)
    assert sim.iep.count == 256


def test_am243x_only_maps_local_iep_and_preserves_c28_shared(sim_config):
    am243x = Simulator(sim_config(target="AM243x", pru_clock_mhz=300, pru1_clock_mhz=300))

    assert am243x.constant_table.resolve(26) == IEP_BASE
    assert am243x.constant_table.resolve(28) == SHARED_RAM
    assert am243x._pru_clock_mhz == 300.0
    assert am243x._pru1_clock_mhz == 300.0
    assert hasattr(am243x, "iep")

    other = Simulator(sim_config(target="AM335x", pru_clock_mhz=200, pru1_clock_mhz=200))
    assert other.constant_table.resolve(26) == SHARED_RAM
    assert other.constant_table.resolve(28) == SHARED_RAM
    assert not hasattr(other, "iep")
    with pytest.raises(ValueError, match="No memory region mapped"):
        other.memory_read(IEP_BASE, 4)


def test_am243x_config_without_clock_override_defaults_pru_and_iep_to_300_mhz():
    config = Path(__file__).parent.parent / "config" / "memory_am243x.cfg"
    sim = Simulator(config_path=str(config))

    assert sim._pru_clock_mhz == 300.0
    assert sim._pru1_clock_mhz == 300.0
    assert sim.iep.ocp_clock_hz == 300_000_000
    assert sim.constant_table.resolve(26) == IEP_BASE
    assert sim.constant_table.resolve(28) == SHARED_RAM


def test_pru_startup_writes_iepclk_and_global_cfg_through_c4_and_c26():
    sim = Simulator()
    assert _read32(sim, ICSS_CFG_IEPCLK) == 0
    assert _read32(sim, IEP_GLOBAL_CFG) == GLOBAL_CFG_RESET
    assert _read_count(sim) == 0

    source = (
        "ldi r2, 1\n"
        "sbco &r2, c4, 0x30, 4\n"
        "ldi r2, 0x11\n"
        "sbco &r2, c26, 0, 4\n"
        "halt\n"
    )
    assert sim.load("pru0", source) == []
    sim.step("pru0", 5)

    assert _read32(sim, ICSS_CFG_IEPCLK) == IEPCLK_OCP_EN
    assert _read32(sim, IEP_GLOBAL_CFG) == 0x11
    assert sim.iep.enabled
    assert sim.iep.default_increment == 1
    # The repository's local memory.cfg is intentionally a 200 MHz profile;
    # startup must select the configured OCP clock rather than a hard-coded
    # 300 MHz assumption.
    assert sim.iep.active_clock_hz == sim.iep.ocp_clock_hz


def test_counter_enable_and_default_increment_control_progression():
    sim = Simulator()
    _load_loop(sim, "pru0")

    sim.step("pru0", 5)
    assert _read_count(sim) == 0

    _configure_counter(sim, default_inc=1)
    _write_count(sim, 0)
    sim.step("pru0", 4)
    assert _read_count(sim) == 4

    _write32(sim, IEP_GLOBAL_CFG, 0x31)
    _write_count(sim, 0)
    sim.step("pru0", 2)
    assert _read_count(sim) == 6

    _write32(sim, IEP_GLOBAL_CFG, 0x30)
    sim.step("pru0", 3)
    assert _read_count(sim) == 6


def test_external_iep_clock_uses_exact_rational_remainder_and_ocp_switch(sim_config):
    sim = Simulator(
        sim_config(
            target="AM243x",
            pru_clock_mhz=300,
            pru1_clock_mhz=300,
            iep_clock_mhz=200,
        )
    )
    _load_loop(sim, "pru0")
    _configure_counter(sim, ocp_enabled=False, default_inc=1)
    _write_count(sim, 0)

    sim.step("pru0", 1)
    assert _read_count(sim) == 0
    sim.step("pru0", 2)
    assert _read_count(sim) == 2
    sim.step("pru0", 3)
    assert _read_count(sim) == 4

    _write_count(sim, 0)
    _write32(sim, ICSS_CFG_IEPCLK, IEPCLK_OCP_EN)
    sim.step("pru0", 1)
    assert _read_count(sim) == 1


def test_pair_step_counts_global_time_once_and_single_core_continues():
    sim = Simulator()
    _load_loop(sim, "pru0")
    _load_loop(sim, "pru1")
    _configure_counter(sim)
    _write_count(sim, 0)

    sim.step_paced("pru0", "pru1", 10)
    assert sim.cores["pru0"].counters.cycles == 10
    assert sim.cores["pru1"].counters.cycles == 10
    assert _read_count(sim) == 10

    sim.step("pru0", 3)
    assert _read_count(sim) == 13


def test_memory_stall_cycles_advance_iep():
    sim = Simulator()
    _configure_counter(sim)
    source = "ldi r0, 0\nlbbo &r1, r0, 0, 4\nhalt\n"
    assert sim.load("pru0", source) == []

    sim.step("pru0")
    _write_count(sim, 0)
    sim.step("pru0")

    assert sim.cores["pru0"].counters.stall_cycles == 2
    assert _read_count(sim) == 3


def test_separate_count_reads_return_high_latched_by_low_across_rollover():
    sim = Simulator()
    _configure_counter(sim)
    _write_count(sim, 0x0000_0000_FFFF_FFFF)
    source = (
        "lbco &r2, c26, 0x10, 4\n"
        "nop\n"
        "lbco &r3, c26, 0x14, 4\n"
        "lbco &r4, c26, 0x10, 4\n"
        "lbco &r5, c26, 0x14, 4\n"
        "halt\n"
    )
    assert sim.load("pru0", source) == []
    sim.step("pru0", 5)

    registers = sim.registers("pru0")
    assert registers[2] == 0xFFFF_FFFF
    assert registers[3] == 0
    assert registers[5] == 1
    assert sim.iep.count > 0x0000_0001_0000_0000


def test_eight_byte_lbco_returns_one_coherent_snapshot_at_low_word_wrap():
    sim = Simulator()
    _configure_counter(sim)
    _write_count(sim, 0x0000_0000_FFFF_FFFF)
    assert sim.load("pru0", "lbco &r2, c26, 0x10, 8\nhalt\n") == []

    sim.step("pru0")

    registers = sim.registers("pru0")
    assert registers[2] == 0xFFFF_FFFF
    assert registers[3] == 0
    assert sim.iep.count >= 0x0000_0001_0000_0000


def test_counter_register_writes_update_full_count_and_clear_fraction():
    sim = Simulator()
    _write32(sim, IEP_GLOBAL_CFG, 0x10)

    _write32(sim, IEP_COUNT_HI, 0x0123_4567)
    _write32(sim, IEP_COUNT_LO, 0x89AB_CDEF)
    assert _read_count(sim) == 0x0123_4567_89AB_CDEF

    sim.memory.write(IEP_COUNT_LO, (0xA5A5_5A5A_1234_5678).to_bytes(8, "little"))
    assert _read_count(sim) == 0xA5A5_5A5A_1234_5678


def test_core_resets_rebase_observers_without_freeze_or_jump():
    sim = Simulator()
    _load_loop(sim, "pru0")
    _load_loop(sim, "pru1")
    _configure_counter(sim)
    _write_count(sim, 0)
    sim.step_paced("pru0", "pru1", 5)
    assert _read_count(sim) == 5

    sim.reset("pru0")
    sim.reset("pru1")
    assert _read_count(sim) == 5
    sim.step_paced("pru0", "pru1", 1)
    assert _read_count(sim) == 6

    sim.reset("pru0")
    sim.step("pru0")
    assert _read_count(sim) == 7


def test_hard_reset_restores_hardware_register_state_and_64_bit_wrap():
    sim = Simulator()
    _load_loop(sim, "pru0")
    _configure_counter(sim)
    _write_count(sim, 0xFFFF_FFFF_FFFF_FFFF)
    sim.step("pru0")
    assert _read_count(sim) == 0

    sim.hard_reset()
    assert _read_count(sim) == 0
    assert _read32(sim, IEP_GLOBAL_CFG) == GLOBAL_CFG_RESET
    assert _read32(sim, ICSS_CFG_IEPCLK) == 0
    sim.step("pru0", 3)
    assert _read_count(sim) == 0
