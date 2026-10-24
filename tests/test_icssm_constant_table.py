"""AM261x / AM263x (ICSS-M) PRU constant table C0-C31.

Expected values are literals: SPRUJB6B Table 7-40 (pp. 370-371), confirmed by
reading each entry with LBCO on an LP-AM261 (ICSS-M0, PRU0 and PRU1). AM263x
and AM261x share the ICSS-M constant table, so both targets are checked against
the same numbers.

Silicon as found (ICTE debug block and CCS register view, PRU0 and PRU1): c10
and c12-c14 hold non-zero pointers (c10 0x2A000 on PRU0 / 0x2A200 on PRU1, c12
0x27000, c13 0x2C000, c14 0x24800) while the window behind each reads zero. The
simulator has no "pointer present, window reads zero" model, so these are left
absent (0); an LBCO through an absent entry reads the core's own DRAM, not
zeros. The tests pin the absent value and the as-found pointers separately, so
the difference stays visible. c29-c31 are static as-found values.
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
# Modelled absent (0), but silicon holds a pointer here (see SILICON_POINTERS).
ABSENT = (10, 12, 13, 14)
# C24 (own DRAM, local 0x0) is absent: it reads as 0 either way.
ABSENT_SAME_AS_SILICON = (24,)
# As found on silicon: pointer present, window behind it reads zero. The
# simulator does not model that, so the cfg keeps these absent. C10 is per core.
SILICON_POINTERS = {"pru0": {10: 0x2A000, 12: 0x27000, 13: 0x2C000, 14: 0x24800},
                    "pru1": {10: 0x2A200, 12: 0x27000, 13: 0x2C000, 14: 0x24800}}
# C29-C31 as found: 0xD0000000 / 0xE0000000 / 0xF0000000 | ptr << 8 (ptr = 0).
AS_FOUND_C29_C31 = {29: 0xD0000000, 30: 0xE0000000, 31: 0xF0000000}
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
def test_absent_entries_are_zero(sim_config, target, core):
    # C10 must not carry the ICSSG value (0x2A000 on PRU0, 0x2A200 on PRU1) nor
    # the old 0x48318000 fallback: the cfg holds 0, not the as-found pointer.
    # This is a known difference from silicon (pointer present, window reads
    # zero); see SILICON_POINTERS.
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n in ABSENT + ABSENT_SAME_AS_SILICON:
        assert table.resolve(n) == 0, f"c{n}"


def test_silicon_pointers_exist_for_exactly_the_absent_entries():
    # Documents the discrepancy: silicon pointers are non-zero for the entries
    # the simulator leaves absent.
    for pointers in SILICON_POINTERS.values():
        assert sorted(pointers) == sorted(ABSENT)
        assert all(v != 0 for v in pointers.values())


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_c29_c31_are_the_as_found_values(sim_config, target, core):
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    for n, expected in AS_FOUND_C29_C31.items():
        assert table.resolve(n) == expected, f"c{n}"


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_c28_stays_absent(sim_config, target, core):
    # As found 0x10000 on an LP-AM261 only because CTPPR0 = 0x100 survived from
    # earlier use; power-on is unresolved, so the cfg keeps C28 absent (0).
    table = Simulator(sim_config(target=target)).cores[core].constant_table
    assert table.resolve(28) == 0


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
