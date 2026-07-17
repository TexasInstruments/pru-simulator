"""Tests for UART RX framing error detection and recovery."""
import pytest
from pathlib import Path

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from mem.constant_table import ConstantTable
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort
from pru_io.uart_frame_generator import UARTFrameGenerator


def make_uart_core(asm_path: str | Path) -> PRUCore:
    """Create PRUCore with DRAM0 (8KB) and constant table c24=0x00000000."""
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    xfr = XFRBus()
    io = IOPort()
    ct = ConstantTable()
    ct.entries[24] = 0x00000000
    core = PRUCore("PRU0", mem, xfr, io, ct)
    source = Path(asm_path).read_text()
    errors = core.load_asm(source)
    assert errors == [], f"Assembly errors: {errors}"
    return core


class TestUARTRXFramingErrors:
    """Test framing error detection via stop bit validation."""

    def test_framing_error_sets_error_flag(self):
        """If stop bit is LOW, error flag at DRAM0+0x0FFE should be set."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x41] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        gen.inject_framing_error(byte_index=2)  # Corrupt byte 2's stop bit
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        # Error flag should be set
        err_data, _ = core.memory.read(0x0FFE, 1)
        assert err_data[0] == 1, f"Expected error flag=1, got {err_data[0]}"

    def test_framing_error_aborts_frame_storage(self):
        """Frame with framing error should NOT be stored to DRAM0."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        payload = [0x41] * 11
        gen = UARTFrameGenerator(pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        gen.inject_framing_error(byte_index=0)  # Error on very first byte
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=8000)

        # Frame should NOT be stored (DRAM0[0:11] should be zeros)
        data, _ = core.memory.read(0x0000, 11)
        assert list(data) == [0] * 11

    def test_recovery_after_framing_error(self):
        """After a framing error, PRU should recover and receive the next valid frame."""
        asm_path = Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm"
        core = make_uart_core(asm_path)

        # Two frames: first has error on byte 3, second is clean
        payload = [0x42] * 11
        gen = UARTFrameGenerator(
            pin=0, payload=payload, baudrate=4_000_000, pru_clock_mhz=200.0,
            frames=2, idle_gap_bits=4,
        )
        gen.attach(core.io_port)
        gen.start(trigger_cycle=5)
        gen.inject_framing_error(byte_index=3)  # Error in byte 3 of frame 1
        core.io_port.uart_generator = gen
        core.io_port.set_gpi_pin(0, True)

        core.run(max_steps=16000)

        # Error flag should be set (from frame 1)
        err_data, _ = core.memory.read(0x0FFE, 1)
        assert err_data[0] == 1

        # Frame 2 should be stored at DRAM0[0] (frame 1 was aborted, offset not advanced)
        data, _ = core.memory.read(0x0000, 11)
        assert list(data) == payload
