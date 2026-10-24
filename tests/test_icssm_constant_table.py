"""AM261x / AM263x (ICSS-M) PRU constant table C0-C31.

Expected values are literals: SPRUJB6B Table 7-40 (pp. 370-371), confirmed by
reading each entry with LBCO on an LP-AM261 (ICSS-M0, PRU0 and PRU1). AM263x
and AM261x share the ICSS-M constant table, so both targets are checked against
the same numbers.

Entries that read zero on silicon are either reserved (c10, c12-c14, c29-c31:
no hang, no aliasing to any other window) or are not distinguishable from zero
by a read; the simulator models them as absent (0).
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
# Reserved: read zero on silicon, no hang, no aliasing.
RESERVED = (10, 12, 13, 14)
# C24 (own DRAM, local 0x0) is absent: it reads as 0 either way.
# C29-C31 (CTPPR0[31:16], CTPPR1) read zero on silicon.
ZERO_PROGRAMMABLE = (24, 29, 30, 31)
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
def test_reserved_entries_read_zero(sim_config, target, core):
    # C10 is reserved on ICSS-M: it must not carry the ICSSG value (0x2A000 on
    # PRU0, 0x2A200 on PRU1) nor the old 0x48318000 fallback.
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n in RESERVED + ZERO_PROGRAMMABLE:
        assert table.resolve(n) == 0, f"c{n}"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_per_core_entries(sim_config, target, core):
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in PER_CORE[core].items():
        assert table.resolve(n) == expected, f"c{n}"


# C25-C27: the TRM gives the forms 2n00h / 2En00h / 32n00h; with the CTBIR
# fields at 0 an LP-AM261 read through each entry equals the window at
# 0x2000 (other core's DRAM), 0x2E000 and 0x32000, on PRU0 and PRU1.
@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_ctbir_entries_c25_c26_c27(sim_config, target, core):
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    assert [table.resolve(n) for n in (25, 26, 27)] == [0x2000, 0x2E000, 0x32000]
    # C24 stays absent (own DRAM at local 0 reads the same as 0).
    assert table.resolve(24) == 0
