"""AM261x / AM263x (ICSS-M) PRU constant table C0-C31.

Expected values are literals: SPRUJB6B Table 7-40 (pp. 370-371), confirmed by
reading each entry with LBCO on an LP-AM261 (ICSS-M0, PRU0 and PRU1). AM263x
and AM261x share the ICSS-M constant table, so both targets are checked against
the same numbers.

Silicon as found (ICTE debug block and CCS register view, PRU0 and PRU1): c10
and c12-c14 hold non-zero pointers (c10 0x2A000 on PRU0 / 0x2A200 on PRU1, c12
0x27000, c13 0x2C000, c14 0x24800) while the window behind each reads zero. The
simulator keeps those pointers in the table and maps the reserved ranges as
read-as-zero windows (test_icssm_reserved_windows.py). C24-C31 follow
CTBIR0/1 and CTPPR0/1 (test_constant_table_registers.py).
"""
import pytest
from simulator import Simulator

TARGETS = ["AM261x", "AM263x"]

# Fixed entries common to both PRUs.
COMMON = {
    0: 0x00020000, 1: 0x0002F000, 2: 0x0002F100, 3: 0x00030000,
    4: 0x00026000, 5: 0x00026100, 6: 0x00020200, 7: 0x00028000,
    8: 0x0002E100, 9: 0x00033000,
    15: 0x60000000, 16: 0x70000000, 17: 0x80000000, 18: 0x90000000,
    19: 0xA0000000, 20: 0xB0000000, 21: 0x00032400, 23: 0xC0000000,
}
# Pointer present, window behind it reads zero. C10 is per core.
SILICON_POINTERS = {"pru0": {10: 0x2A000, 12: 0x27000, 13: 0x2C000, 14: 0x24800},
                    "pru1": {10: 0x2A200, 12: 0x27000, 13: 0x2C000, 14: 0x24800}}
# With CTBIR0/1 = CTPPR0/1 = 0: C24-C31 are the formula bases.
REGISTER_RESET = {24: 0, 25: 0x2000, 26: 0x2E000, 27: 0x32000, 28: 0,
                  29: 0xD0000000, 30: 0xE0000000, 31: 0xF0000000}
PER_CORE = {"pru0": {11: 0x00022000, 22: 0x00008000},
            "pru1": {11: 0x00024000, 22: 0x00009000}}


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_fixed_entries_match_silicon_and_trm(sim_config, target, core):
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in COMMON.items():
        assert table.resolve(n) == expected, f"c{n}"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_reserved_pointers_are_the_silicon_values(sim_config, target, core):
    # C10 must not carry the old 0x48318000 fallback.
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in SILICON_POINTERS[core].items():
        assert table.resolve(n) == expected, f"c{n}"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_register_driven_entries_at_reset(sim_config, target, core):
    # C28 is 0 until firmware programs CTPPR0 (an LP-AM261 read 0x10000 only
    # because CTPPR0 = 0x100 was left over from earlier use).
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in REGISTER_RESET.items():
        assert table.resolve(n) == expected, f"c{n}"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_per_core_entries(sim_config, target, core):
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in PER_CORE[core].items():
        assert table.resolve(n) == expected, f"c{n}"
