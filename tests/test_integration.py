"""Integration tests for the top-level Simulator orchestrator."""

import sys
import os

# Ensure the project root is on sys.path so "simulator" can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from simulator import Simulator
from core.pru_core import PRUCore


class TestSimulatorBasic:
    def test_create_default(self):
        """Simulator initialises with two cores even when no config file exists."""
        sim = Simulator(config_path="nonexistent.cfg")
        assert "pru0" in sim.cores
        assert "rtu0" in sim.cores

    def test_load_and_step(self):
        """Loading assembly and stepping produces the correct register state."""
        sim = Simulator(config_path="nonexistent.cfg")
        errors = sim.load("pru0", "ldi r0, 42\nhalt")
        assert errors == []
        sim.step("pru0")
        sim.step("pru0")
        assert sim.registers("pru0")[0] == 42

    def test_both_cores_independent(self):
        """Each core executes independently without affecting the other."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.load("pru0", "ldi r0, 1\nhalt")
        sim.load("rtu0", "ldi r0, 2\nhalt")
        sim.step("pru0")
        sim.step("rtu0")
        assert sim.registers("pru0")[0] == 1
        assert sim.registers("rtu0")[0] == 2

    def test_xfr_ipc_between_cores(self):
        """Data written via XOUT on one core can be read via XIN on another."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.load("pru0", "ldi r2, 0xCAFE\nxout 10, &r2, 4\nhalt")
        sim.step("pru0", 2)
        sim.load("rtu0", "xin 10, &r2, 4\nhalt")
        sim.step("rtu0")
        assert sim.registers("rtu0")[2] == 0xCAFE

    def test_status(self):
        """status() returns per-core pc and cycle counts after execution."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.load("pru0", "ldi r0, 1\nhalt")
        sim.step("pru0")
        status = sim.status()
        assert status["pru0"]["pc"] == 1
        assert status["pru0"]["cycles"] == 1

    def test_reset(self):
        """reset() clears register state back to zero."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.load("pru0", "ldi r0, 1\nhalt")
        sim.step("pru0")
        sim.reset("pru0")
        assert sim.registers("pru0")[0] == 0

    def test_io(self):
        """Writing to R30 via LDI exposes correct GPO pin values."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.load("pru0", "ldi r30, 5\nhalt")
        sim.step("pru0")
        io = sim.io("pru0")
        # 5 == 0b101 → pins 0 and 2 set, pin 1 clear
        assert io["gpo_pins"][0] == 1
        assert io["gpo_pins"][2] == 1
        assert io["gpo_pins"][1] == 0

    def test_set_input(self):
        """set_input() sets a GPI pin that is then readable via R31/MOV."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.set_input("pru0", 5, True)
        sim.load("pru0", "mov r0, r31\nhalt")
        sim.step("pru0")
        assert sim.registers("pru0")[0] == (1 << 5)

    def test_load_from_real_config(self):
        """Simulator can be constructed from the real AM243x config file."""
        cfg = os.path.join(os.path.dirname(__file__), "..", "config", "memory_am243x.cfg")
        sim = Simulator(config_path=cfg)
        assert "pru0" in sim.cores

    def test_memory_read_write(self):
        """SBBO writes to memory; memory_read() returns the correct bytes."""
        sim = Simulator(config_path="nonexistent.cfg")
        sim.load("pru0", "ldi r1, 0x100\nldi r2, 0xBEEF\nsbbo &r2, r1, 0, 4\nhalt")
        sim.step("pru0", 3)
        data = sim.memory_read(0x100, 4)
        assert int.from_bytes(data, "little") == 0xBEEF


# ===========================================================================
# MAC accelerator integration tests
# ===========================================================================

class TestMACIntegration:
    """Run assembly programs through PRUCore using the MAC accelerator."""

    def _make_core(self):
        from mem.memory_bus import MemoryBus
        from mem.regions import MemoryRegion
        from xfr.xfr_bus import XFRBus
        from pru_io.io_port import IOPort
        from mem.constant_table import ConstantTable
        bus = MemoryBus()
        bus.add_region(MemoryRegion("DRAM0", 0x00000000, 0x2000, 2, 1, 0))
        return PRUCore("test", bus, XFRBus(), IOPort(), ConstantTable())

    def test_multiply_only_50_times_25(self):
        """Academy mac_multiply example: 50*25=1250 in R26, R27=0."""
        core = self._make_core()
        src = """
            zero  &r0, 120
            LDI   R25, 0
            XOUT  0, &R25, 1
            LDI   R28, 50
            LDI   R29, 25
            NOP
            XIN   0, &R25, 1
            XIN   0, &R26, 4
            XIN   0, &R27, 4
            HALT
        """
        errors = core.load_asm(src)
        assert errors == []
        core.run()
        assert core.registers.read_full(26) == 1250
        assert core.registers.read_full(27) == 0

    def test_mac_dot_product_1_2_3_dot_4_5_6(self):
        """Academy mac example: (1,2,3)·(4,5,6) = 32 in R26."""
        core = self._make_core()
        src = """
            zero  &r0, 120
            LDI   R10, 1
            LDI   R11, 2
            LDI   R12, 3
            LDI   R13, 4
            LDI   R14, 5
            LDI   R15, 6

            LDI   R25, 1
            XOUT  0, &R25, 1
            LDI   R25, 3
            XOUT  0, &R25, 1
            LDI   R25, 1

            MOV   R28, R10
            MOV   R29, R13
            XOUT  0, &R25, 1

            MOV   R28, R11
            MOV   R29, R14
            XOUT  0, &R25, 1

            MOV   R28, R12
            MOV   R29, R15
            XOUT  0, &R25, 1

            XIN   0, &R25, 1
            XIN   0, &R26, 4
            XIN   0, &R27, 4
            HALT
        """
        errors = core.load_asm(src)
        assert errors == []
        core.run()
        assert core.registers.read_full(26) == 32
        assert core.registers.read_full(27) == 0

    def test_mac_reset_clears_accelerator(self):
        """After core.reset(), MAC accumulator and mode are cleared."""
        core = self._make_core()
        src = """
            LDI   R25, 1
            XOUT  0, &R25, 1
            LDI   R28, 100
            LDI   R29, 100
            XOUT  0, &R25, 1
            HALT
        """
        core.load_asm(src)
        core.run()
        assert core.accelerators[0].mac_mode is True
        assert core.accelerators[0]._accumulator > 0
        core.reset()
        assert core.accelerators[0].mac_mode is False
        assert core.accelerators[0]._accumulator == 0
