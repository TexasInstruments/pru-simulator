# tests/test_sd_e2e.py
"""End-to-end tests: PRU assembly configures SD, reads filtered data."""
import pytest
from simulator import Simulator


class TestSDEndToEnd:
    """Full workflow: configure registers, enable SD, run, read result."""

    def test_dc_input_produces_stable_output(self):
        """DC input through 2nd-order modulator → stable accumulator after OSR samples."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter

        # Configure: OSR=64, clock ratio 1:1 for speed
        sd.channels[0].osr = 64
        sd.modulators[0].signal = "dc"
        sd.modulators[0].dc_level = 0.5
        sd.modulators[0].sd_clock_mhz = 333.0  # 1:1 with PRU (AM243x = 333 MHz)

        # Enable SD via R30
        sd.process_r30(0 << 26 | 1 << 25)  # ch0, sd_en=1

        # Run enough steps for one full OSR window
        source = "\n".join(["NOP"] * 64)
        sim.load("pru0", source)
        for _ in range(64):
            sim.step("pru0")

        # Valid should be set, data should be non-zero
        assert sd.channels[0].valid is True
        r31 = sim.cores["pru0"].io_port.read_r31()
        assert (r31 >> 28) & 1 == 1  # valid
        assert r31 & 0x0FFFFFFF > 0  # non-zero data for DC=0.5

    def test_reinit_then_read_after_3_osr(self):
        """Reset-and-measure mode: reinit, wait 3*OSR, read stable value."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter

        sd.channels[0].osr = 16
        sd.modulators[0].signal = "dc"
        sd.modulators[0].dc_level = 0.0
        sd.modulators[0].sd_clock_mhz = 200.0

        sd.process_r30(0 << 26 | 1 << 25)

        # Run some ticks to get dirty state
        source = "\n".join(["NOP"] * 100)
        sim.load("pru0", source)
        for _ in range(50):
            sim.step("pru0")

        # Reinit
        sd.process_r31_command(1 << 23)  # reinit = bit 23 per Verilog
        assert sd.channels[0].acc1 == 0

        # Run 3*OSR=48 more ticks
        for _ in range(48):
            sim.step("pru0")

        # After 3 full OSR windows, should have valid data
        r31 = sim.cores["pru0"].io_port.read_r31()
        data = r31 & 0x0FFFFFFF
        # DC=0.0 → ~50% ones → accumulators have meaningful values
        assert data > 0

    def test_channel_switching(self):
        """Switching ch_sel changes which channel R31 reports."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter

        # Setup different DC levels on ch0 and ch1
        for i in range(2):
            sd.channels[i].osr = 16
            sd.modulators[i].sd_clock_mhz = 200.0
        sd.modulators[0].signal = "dc"
        sd.modulators[0].dc_level = 0.9  # very high
        sd.modulators[1].signal = "dc"
        sd.modulators[1].dc_level = -0.9  # very low

        # Run with ch0 selected
        sd.process_r30(0 << 26 | 1 << 25)

        source = "\n".join(["NOP"] * 64)
        sim.load("pru0", source)
        for _ in range(32):
            sim.step("pru0")

        r31_ch0 = sim.cores["pru0"].io_port.read_r31()

        # Switch to ch1
        sd.process_r30(1 << 26 | 1 << 25)
        for _ in range(32):
            sim.step("pru0")

        r31_ch1 = sim.cores["pru0"].io_port.read_r31()

        # ch0 (DC=+0.9) should have higher accumulator than ch1 (DC=-0.9)
        data_ch0 = r31_ch0 & 0x0FFFFFFF
        data_ch1 = r31_ch1 & 0x0FFFFFFF
        assert data_ch0 > data_ch1

    def test_osr_config_via_memory(self):
        """Changing OSR via memory-mapped register takes effect on next accumulation."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        sd.modulators[0].sd_clock_mhz = 333.0  # 1:1 with PRU (AM243x = 333 MHz)
        sd.modulators[0].signal = "dc"
        sd.modulators[0].dc_level = 0.5
        sd.process_r30(0 << 26 | 1 << 25)

        # Set OSR=8 via memory register
        sim.memory.write(0x2604C, (7).to_bytes(4, 'little'))  # SAMPLE_SIZE=7 → OSR=8
        assert sd.channels[0].osr == 8

        # Run 8 steps, should get valid
        source = "\n".join(["NOP"] * 8)
        sim.load("pru0", source)
        for _ in range(8):
            sim.step("pru0")
        assert sd.channels[0].valid is True

    def test_sd_state_api(self):
        """sd_state() returns correct mode and channel data."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        sd.process_r30(0 << 26 | 1 << 25)  # enable SD

        state = sim.sd_state("pru0")
        assert state is not None
        assert state["sd_en"] is True
        assert state["ch_sel"] == 0
        assert len(state["channels"]) == 3
        assert len(state["modulators"]) == 3
        # Channels should have config from registers
        assert "config" in state["channels"][0]
        assert "osr" in state["channels"][0]["config"]
