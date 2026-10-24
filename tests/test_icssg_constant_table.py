"""AM243x / AM64x (ICSSG) PRU constant table C0-C31.

Expected values are literals from SPRUIM2J Table 6-65 (pp. 581-582). The
fixed entries were read with LBCO on an LP-AM243 (ICSSG0, PRU0 and PRU1):
the entry window equals the window read directly at the listed local address
(c1-c9, c16, c21, c26, c27). Entries listed as "zero" read zero on silicon, as
does the TRM-listed window, so their value is the TRM value, consistent but not
identified by a read (c12-c15, c17-c20, c23).

C10 and C22 are per-core; PRU0 and PRU1 cannot be told apart by reads (the
candidate windows hold identical data at reset), so only the TRM values are
pinned there.
"""
import pytest
from simulator import Simulator

# entry -> value; the cfg must not hold an AM335x-style SoC address here.
FIXED = {
    0: 0x00020000, 1: 0x0002F000, 2: 0x0002F100, 3: 0x00030000,
    4: 0x00026000, 5: 0x00026100, 6: 0x00020200, 7: 0x00028000,
    8: 0x0002E100, 9: 0x00033000,
    12: 0x00027000, 13: 0x0002C000, 14: 0x00024800,
    15: 0x60000000, 16: 0x70000000, 17: 0x80000000, 18: 0x90000000,
    19: 0xA0000000, 20: 0xB0000000, 21: 0x00032400, 23: 0xC0000000,
    26: 0x0002E000, 27: 0x00032000,
}
# Programmable entries as found / unchanged by this table: pinned so a later
# edit of the fixed block cannot move them unnoticed.
PROGRAMMABLE = {24: 0, 25: 0x2000, 28: 0x10000, 29: 0, 30: 0, 31: 0}
CORES = ["pru0", "rtu0", "pru1", "rtu1"]


@pytest.mark.parametrize("target", ["AM243x", "AM64x"])
@pytest.mark.parametrize("core", CORES)
def test_fixed_entries_are_icssg_local_addresses(sim_config, target, core):
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in FIXED.items():
        assert table.resolve(n) == expected, f"c{n}"


@pytest.mark.parametrize("core", CORES)
def test_programmable_entries_unchanged(sim_config, core):
    table = Simulator(sim_config(target="AM243x")).cores[core].constant_table
    for n, expected in PROGRAMMABLE.items():
        assert table.resolve(n) == expected, f"c{n}"
