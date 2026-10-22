"""Core-local constant addresses from TI SPRUIM2J Table 6-65 (pp.581–582).

AM263x PRU control/RAT values are also listed in TI's OpenPRU
source/linker_cmd/c_code/am263x/AM263x_PRU{0,1}.cmd. Register behavior is not
under test: explicit probe regions show where LBCO/SBCO route their accesses.
"""
import pytest
from mem.regions import MemoryRegion
from simulator import Simulator


ICSSG_CORES = [
    ("pru0", 0x2A000, 0x22000, 0x8000),
    ("rtu0", 0x2A100, 0x23000, 0x8000),
    ("pru1", 0x2A200, 0x24000, 0x9000),
    ("rtu1", 0x2A300, 0x23800, 0x9000),
]


@pytest.mark.parametrize("target", ["AM243x", "AM64x"])
@pytest.mark.parametrize("core,c10,c11,c22", ICSSG_CORES)
def test_icssg_core_constants_follow_the_selected_core(sim_config, target, core, c10, c11, c22):
    sim = Simulator(sim_config(target=target))
    table = sim.cores[core].constant_table
    assert [table.resolve(n) for n in (10, 11, 22)] == [c10, c11, c22]


@pytest.mark.parametrize("core,c11,c22", [("pru0", 0x22000, 0x8000),
                                           ("pru1", 0x24000, 0x9000)])
def test_am263x_pru_control_and_rat_constants(sim_config, core, c11, c22):
    sim = Simulator(sim_config(target="AM263x"))
    table = sim.cores[core].constant_table
    assert table.resolve(11) == c11
    assert table.resolve(22) == c22


@pytest.mark.parametrize("core,c10,c11,c22", ICSSG_CORES)
def test_constant_loads_and_stores_use_the_core_bank(sim_config, core, c10, c11, c22):
    sim = Simulator(sim_config(target="AM243x"))
    addresses = sorted({value for row in ICSSG_CORES for value in row[1:]})
    for index, address in enumerate(addresses):
        # The shipped config already has a generic region at PRU0 control.
        if not any(region.contains(address) for region in sim.memory.regions):
            sim.memory.add_region(MemoryRegion("probe", address, 0x30, 2, 1))
        sim.memory.write(address + 4, (0xA000 + index).to_bytes(4, "little"))
    expected = [int.from_bytes(sim.memory_read(address + 4, 4), "little")
                for address in (c10, c11, c22)]
    assert sim.load(core, "lbco &r1, c10, 4, 4\nlbco &r2, c11, 4, 4\n"
                          "lbco &r3, c22, 4, 4\nldi r4, 0xBEEF\n"
                          "sbco &r4, c11, 8, 4\nsbco &r4, c22, 8, 4\nhalt") == []
    sim.step(core, 10)
    assert sim.cores[core].fault is None
    assert sim.registers(core)[1:4] == expected
    for address in addresses:
        value = int.from_bytes(sim.memory_read(address + 8, 4), "little")
        assert value == (0xBEEF if address in (c11, c22) else 0)


def test_custom_constant_entries_stay_intact_and_tables_remain_independent(sim_config):
    path = sim_config(target="AM243x")
    from pathlib import Path
    constants = Path(path).parent / "config" / "constants_am243x.cfg"
    constants.write_text("[constants]\nc3 = 0x12340000\nc28 = 0x10000\n")
    sim = Simulator(path)
    assert all(core.constant_table.resolve(3) == 0x12340000 for core in sim.cores.values())
    sim.cores["pru0"].constant_table.set(11, 0xDEADBEEF)
    assert sim.cores["pru1"].constant_table.resolve(11) == 0x24000
