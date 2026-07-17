import pytest
import os
from simulator import Simulator

ASM_DIR = os.path.join(os.path.dirname(__file__), "asm")


def run_asm(filename, max_steps=1000):
    sim = Simulator(config_path="nonexistent.cfg")
    filepath = os.path.join(ASM_DIR, filename)
    with open(filepath, 'r') as f:
        source = f.read()
    errors = sim.load("pru0", source)
    assert errors == [], f"Parse errors: {errors}"
    for _ in range(max_steps):
        if sim.cores["pru0"].halted:
            break
        sim.step("pru0")
    assert sim.cores["pru0"].halted, "Program did not halt"
    return sim


class TestArithmeticASM:
    def test_add(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[10] == 30

    def test_sub(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[11] == 10

    def test_underflow(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[12] == 0xFFFFFFFF

    def test_adc_carry(self):
        sim = run_asm("test_arithmetic.asm")
        assert sim.registers("pru0")[13] == 0  # SUB underflow → carry=0, ADC adds 0


class TestLogicASM:
    def test_and(self):
        sim = run_asm("test_logic.asm")
        assert sim.registers("pru0")[10] == 0xF0

    def test_or(self):
        sim = run_asm("test_logic.asm")
        assert sim.registers("pru0")[11] == 0xFFFF

    def test_xor(self):
        sim = run_asm("test_logic.asm")
        assert sim.registers("pru0")[12] == 0x0FF0

    def test_not(self):
        sim = run_asm("test_logic.asm")
        assert sim.registers("pru0")[13] == 0xFFFFFF00

    def test_lsl(self):
        sim = run_asm("test_logic.asm")
        assert sim.registers("pru0")[14] == 16

    def test_lsr(self):
        sim = run_asm("test_logic.asm")
        assert sim.registers("pru0")[15] == 1


class TestBranchesASM:
    def test_qbeq_taken(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[10] == 1

    def test_qbgt_taken(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[11] == 1

    def test_qbbs_taken(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[12] == 1

    def test_loop_count(self):
        sim = run_asm("test_branches.asm")
        assert sim.registers("pru0")[13] == 10


class TestMemoryASM:
    def test_store_load_roundtrip(self):
        sim = run_asm("test_memory.asm")
        assert sim.registers("pru0")[10] == 0xDEAD


class TestXfrASM:
    def test_scratchpad_roundtrip(self):
        sim = run_asm("test_xfr.asm")
        assert sim.registers("pru0")[10] == 0xCAFE
