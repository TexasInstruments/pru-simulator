"""Issue #58: the MAC does not see an R28/R29 write made on the
instruction immediately before the XIN that reads the product.

Silicon evidence (LP-AM261, 2026-10-11/12): `mov r28,a / mov r29,b / xin 0`
back to back returned a x (previous r29); one NOP between fixed it, and a write
two instructions before the XIN was seen. Both operands behave the same.
"""

import pytest

from simulator import Simulator

# Establish stale operands (r28=2, r29=3), separate them from the test with a
# NOP, then overwrite per the case under test.
PRELUDE = "ldi r28, 2\nldi r29, 3\nnop\n"


def _run(sim_config, body: str) -> int:
    sim = Simulator(sim_config(target="AM243x"))
    assert sim.load("pru0", PRELUDE + body + "xin 0, &r26, 4\nhalt\n") == []
    for _ in range(32):
        sim.step("pru0")
        if sim.cores["pru0"].halted:
            break
    return sim.registers("pru0")[26]


def test_r29_written_immediately_before_xin_is_not_seen(sim_config):
    # r28=7 two instructions back is seen; r29=11 right before the XIN is not.
    assert _run(sim_config, "ldi r28, 7\nldi r29, 11\n") == 7 * 3


def test_r28_written_immediately_before_xin_is_not_seen(sim_config):
    assert _run(sim_config, "ldi r29, 11\nldi r28, 7\n") == 2 * 11


def test_both_written_immediately_before_xin_one_gap_instruction_fixes_it(sim_config):
    assert _run(sim_config, "ldi r28, 7\nldi r29, 11\nnop\n") == 7 * 11


def test_xout_accumulate_sees_operands_as_of_one_instruction_ago(sim_config):
    """XOUT-accumulate (ctrl bit 0) lags like XIN.

    ASSUMPTION, not measured on silicon: only the XIN product path was measured
    (LP-AM261, ICSSM0 PRU0). The XOUT lag is extrapolated from it.

    Rule: the MAC sees R28/R29 as latched at the start of the previous instruction.
    Program: r28=2, r29=3, nop, r25=1 (MAC_MODE), r28=7, r29=11, [gap], XOUT.
      0-instruction gap: the previous instruction is `ldi r29, 11`, so the latch
        holds r28=7, r29=3 -> accumulator = 7*3 = 21.
      1-instruction gap: the previous instruction is the nop, so the latch holds
        r28=7, r29=11 -> accumulator = 7*11 = 77.
    """
    def run(gap: str) -> int:
        sim = Simulator(sim_config(target="AM243x"))
        src = (PRELUDE + "ldi r25, 1\nldi r28, 7\nldi r29, 11\n" + gap
               + "xout 0, &r25, 1\nnop\nxin 0, &r26, 4\nhalt\n")
        assert sim.load("pru0", src) == []
        for _ in range(32):
            sim.step("pru0")
            if sim.cores["pru0"].halted:
                break
        return sim.registers("pru0")[26]

    assert run("") == 7 * 3
    assert run("nop\n") == 7 * 11


@pytest.mark.parametrize("target", ["AM243x", "AM261x"])
def test_gap_of_zero_vs_one_instruction(sim_config, target):
    def run(gap: str) -> int:
        sim = Simulator(sim_config(target=target))
        src = PRELUDE + "ldi r28, 7\nldi r29, 11\n" + gap + "xin 0, &r26, 4\nhalt\n"
        assert sim.load("pru0", src) == []
        for _ in range(32):
            sim.step("pru0")
            if sim.cores["pru0"].halted:
                break
        return sim.registers("pru0")[26]

    assert run("") == 7 * 3       # 0-instruction gap: stale r29
    assert run("nop\n") == 7 * 11  # 1-instruction gap: fresh
