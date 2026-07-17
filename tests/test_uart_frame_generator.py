# tests/test_uart_frame_generator.py
import pytest
from pru_io.uart_frame_generator import UARTFrameGenerator
from pru_io.io_port import IOPort


class TestUARTFrameGeneratorTimeline:
    """Test that the generator computes the correct bit timeline."""

    def test_single_byte_start_bit(self):
        """Start bit should be LOW for exactly one bit period."""
        gen = UARTFrameGenerator(
            pin=0,
            payload=[0x41],
            baudrate=4_000_000,
            pru_clock_mhz=200.0,
        )
        # Bit period = 200MHz / 4Mbaud = 50 cycles
        # At cycle 0, generator should start with idle HIGH
        # The first event is the start bit going LOW at cycle 0
        # Start bit lasts 50 cycles (cycles 0-49)
        gen.start(trigger_cycle=0)
        assert gen.get_pin_value(0) == 0   # start bit LOW
        assert gen.get_pin_value(49) == 0  # still in start bit
        assert gen.get_pin_value(50) == 1  # first data bit of 0x41 (bit0=1)

    def test_single_byte_all_data_bits(self):
        """Verify all 8 data bits of 0x41 ('A') are correct. 0x41 = 0b01000001."""
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=0)
        bp = 50  # bit period
        # Bit layout: START | b0 b1 b2 b3 b4 b5 b6 b7 | STOP
        # 0x41 = 0b01000001 → LSB first: 1,0,0,0,0,0,1,0
        expected_bits = [0, 1, 0, 0, 0, 0, 0, 1, 0, 1]  # start, d0-d7, stop
        for i, expected in enumerate(expected_bits):
            # Sample at middle of each bit period
            sample_cycle = i * bp + bp // 2
            actual = gen.get_pin_value(sample_cycle)
            assert actual == expected, f"Bit {i}: expected {expected}, got {actual} at cycle {sample_cycle}"

    def test_two_bytes_no_idle_between(self):
        """Two bytes back-to-back: stop bit of byte 0 immediately followed by start bit of byte 1."""
        gen = UARTFrameGenerator(pin=0, payload=[0xFF, 0x00], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=0)
        bp = 50
        # Byte 0 (0xFF): start=0, d0-d7=all 1, stop=1 → 10 bits = 500 cycles
        # Byte 1 (0x00): start=0, d0-d7=all 0, stop=1
        # At cycle 500: byte 1 start bit begins (LOW)
        assert gen.get_pin_value(499) == 1  # byte 0 stop bit still HIGH
        assert gen.get_pin_value(500) == 0  # byte 1 start bit (LOW, no idle gap)

    def test_idle_before_trigger(self):
        """Pin should be HIGH (idle) before trigger cycle."""
        gen = UARTFrameGenerator(pin=0, payload=[0x00], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=100)
        assert gen.get_pin_value(0) == 1
        assert gen.get_pin_value(99) == 1
        assert gen.get_pin_value(100) == 0  # start bit

    def test_idle_after_frame(self):
        """Pin should return to HIGH (idle) after last stop bit."""
        gen = UARTFrameGenerator(pin=0, payload=[0x55], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.start(trigger_cycle=0)
        bp = 50
        frame_end = 10 * bp  # 10 bits total (start + 8 data + stop)
        assert gen.get_pin_value(frame_end - 1) == 1  # last cycle of stop bit
        assert gen.get_pin_value(frame_end) == 1      # idle after frame
        assert gen.get_pin_value(frame_end + 100) == 1


class TestUARTFrameGeneratorTick:
    """Test tick() method that updates IOPort GPI state."""

    def test_tick_sets_gpi_pin_low_on_start_bit(self):
        """tick() should set GPI pin LOW when start bit is active."""
        io = IOPort()
        io.gpi = 0xFFFFF  # all pins HIGH
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(io)
        gen.start(trigger_cycle=0)

        gen.tick(0)  # cycle 0: start bit
        assert (io.gpi & 1) == 0  # pin 0 should be LOW

    def test_tick_preserves_other_pins(self):
        """tick() should only affect the configured pin, not others."""
        io = IOPort()
        io.gpi = 0b1111_1111_1111_1111_1111  # all HIGH
        gen = UARTFrameGenerator(pin=3, payload=[0x00], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(io)
        gen.start(trigger_cycle=0)

        gen.tick(0)  # start bit on pin 3
        assert (io.gpi >> 3) & 1 == 0  # pin 3 LOW
        assert (io.gpi >> 0) & 1 == 1  # pin 0 unchanged
        assert (io.gpi >> 4) & 1 == 1  # pin 4 unchanged

    def test_tick_idle_keeps_pin_high(self):
        """Before trigger, tick should set pin HIGH (idle)."""
        io = IOPort()
        io.gpi = 0
        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(io)
        gen.start(trigger_cycle=100)

        gen.tick(50)  # before trigger
        assert (io.gpi & 1) == 1  # idle HIGH


from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from xfr.xfr_bus import XFRBus


class TestUARTFrameGeneratorPRUIntegration:
    """Test that generator updates are visible to PRU instructions via R31."""

    def _make_core(self, asm: str) -> PRUCore:
        """Create a PRUCore with DRAM0 and IOPort."""
        mem = MemoryBus()
        mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
        xfr = XFRBus()
        io = IOPort()
        core = PRUCore("PRU0", mem, xfr, io)
        errors = core.load_asm(asm)
        assert errors == [], f"Assembly errors: {errors}"
        return core

    def test_r31_reads_uart_start_bit(self):
        """PRU reading R31 should see LOW on pin 0 when start bit is active."""
        asm = "and r5, r31, 1\nhalt\n"
        core = self._make_core(asm)

        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=0)
        core.io_port.uart_generator = gen

        # Step one instruction (cycle 0: start bit LOW)
        core.step()

        # R5 should contain 0 (pin 0 was LOW during the AND instruction)
        assert core.registers.read_full(5) == 0

    def test_r31_reads_uart_data_bit_high(self):
        """PRU reading R31 should see HIGH when data bit is 1."""
        # 0x41 bit 0 = 1, which starts at cycle 50 (after 50-cycle start bit)
        asm = "and r5, r31, 1\n" * 60 + "halt\n"
        core = self._make_core(asm)

        gen = UARTFrameGenerator(pin=0, payload=[0x41], baudrate=4_000_000, pru_clock_mhz=200.0)
        gen.attach(core.io_port)
        gen.start(trigger_cycle=0)
        core.io_port.uart_generator = gen

        # Step 55 instructions (cycle 55 is in data bit 0 period [50..99] which is HIGH for 0x41)
        for _ in range(55):
            core.step()

        # R5 should contain 1 (pin 0 was HIGH at cycle 54)
        assert core.registers.read_full(5) == 1
