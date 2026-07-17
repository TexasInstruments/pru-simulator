# Sigma-Delta Filter Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a cycle-accurate sigma-delta filter peripheral to the PRU simulator with memory-mapped configuration registers, 2nd-order pattern generator, and a dedicated SD interface UI panel.

**Architecture:** SigmaDeltaFilter peripheral module with 3 channels per core, SDModulator pattern generator per channel, memory-mapped config registers at ICSS-G addresses. IOPort delegates R30/R31 to the SD filter when sd_en=1. Async clock model uses fractional accumulator to advance SD ticks per PRU step.

**Tech Stack:** Python 3.11+, pytest, FastAPI/WebSocket, vanilla JS frontend

---

## Task 1: SD Modulator (Pattern Generator)

**Files:**
- Create: `pru_io/sd_modulator.py`
- Create: `tests/test_sd_modulator.py`

- [ ] **Step 1: Write failing test for DC signal modulator**

```python
# tests/test_sd_modulator.py
"""Tests for the 2nd-order sigma-delta modulator pattern generator."""
import pytest
from pru_io.sd_modulator import SDModulator


class TestSDModulatorDC:
    """DC input should produce a bitstream with density proportional to level."""

    def test_dc_zero_produces_roughly_half_ones(self):
        """DC level 0.0 maps to mid-scale: ~50% ones in the bitstream."""
        mod = SDModulator(signal="dc", dc_level=0.0)
        bits = [mod.next_bit() for _ in range(1000)]
        ones = sum(bits)
        # 2nd order modulator at DC=0 should produce ~50% ones
        assert 400 < ones < 600

    def test_dc_positive_produces_more_ones(self):
        """DC level +0.8 should produce ~90% ones."""
        mod = SDModulator(signal="dc", dc_level=0.8)
        bits = [mod.next_bit() for _ in range(2000)]
        ones = sum(bits)
        assert ones > 1500  # > 75%

    def test_dc_negative_produces_fewer_ones(self):
        """DC level -0.8 should produce ~10% ones."""
        mod = SDModulator(signal="dc", dc_level=-0.8)
        bits = [mod.next_bit() for _ in range(2000)]
        ones = sum(bits)
        assert ones < 500  # < 25%

    def test_output_is_binary(self):
        """Every bit must be 0 or 1."""
        mod = SDModulator(signal="dc", dc_level=0.5)
        bits = [mod.next_bit() for _ in range(100)]
        assert all(b in (0, 1) for b in bits)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_modulator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pru_io.sd_modulator'`

- [ ] **Step 3: Implement SDModulator**

```python
# pru_io/sd_modulator.py
"""2nd-order sigma-delta modulator pattern generator.

Produces a 1-bit PDM bitstream encoding a selectable input signal (DC or Sine).
Used to simulate an external sigma-delta ADC modulator feeding the PRU SD filter.
"""

import math


class SDModulator:
    """2nd-order sigma-delta modulator producing a 1-bit PDM stream."""

    def __init__(
        self,
        signal: str = "dc",
        dc_level: float = 0.0,
        amplitude: float = 0.8,
        period: int = 1024,
        phase_deg: float = 0.0,
        sd_clock_mhz: float = 20.0,
    ):
        self.signal = signal            # "dc" or "sine"
        self.dc_level = dc_level        # -1.0 to +1.0
        self.amplitude = amplitude      # 0.0 to 1.0
        self.period = period            # samples per sine cycle
        self.phase_deg = phase_deg      # phase offset in degrees
        self.sd_clock_mhz = sd_clock_mhz

        # Internal modulator state
        self._integrator1: float = 0.0
        self._integrator2: float = 0.0
        self._sample_index: int = 0

    def _get_input(self) -> float:
        """Return the current input signal value in range [-1.0, +1.0]."""
        if self.signal == "dc":
            return self.dc_level
        # Sine wave
        phase_rad = self.phase_deg * math.pi / 180.0
        angle = 2.0 * math.pi * self._sample_index / self.period + phase_rad
        return self.amplitude * math.sin(angle)

    def next_bit(self) -> int:
        """Advance the modulator by one clock tick and return the output bit (0 or 1)."""
        x = self._get_input()

        # 2nd order sigma-delta: two integrators with feedback
        # Quantizer output from previous step (feedback)
        q_out = 1.0 if self._integrator2 >= 0.0 else -1.0

        # Update integrators
        self._integrator1 += x - q_out
        self._integrator2 += self._integrator1 - q_out

        self._sample_index += 1

        # Return binary: 1 if quantizer output is +1, else 0
        return 1 if q_out >= 0.0 else 0

    def reset(self) -> None:
        """Reset modulator state to initial conditions."""
        self._integrator1 = 0.0
        self._integrator2 = 0.0
        self._sample_index = 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_modulator.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Write failing tests for sine signal**

```python
# Append to tests/test_sd_modulator.py

class TestSDModulatorSine:
    """Sine input should produce periodic variation in bit density."""

    def test_sine_full_period_average_near_half(self):
        """Over a full period, average bit density should be ~0.5 (zero-mean sine)."""
        mod = SDModulator(signal="sine", amplitude=0.8, period=1024, phase_deg=0.0)
        bits = [mod.next_bit() for _ in range(1024)]
        ones = sum(bits)
        assert 400 < ones < 624  # roughly 50% +/- tolerance

    def test_sine_phase_offset(self):
        """Two modulators with 180-degree phase offset should be anti-correlated."""
        mod_a = SDModulator(signal="sine", amplitude=0.8, period=1024, phase_deg=0.0)
        mod_b = SDModulator(signal="sine", amplitude=0.8, period=1024, phase_deg=180.0)
        bits_a = [mod_a.next_bit() for _ in range(1024)]
        bits_b = [mod_b.next_bit() for _ in range(1024)]
        # First half of mod_a (positive sine) should have more ones than mod_b
        first_half_a = sum(bits_a[:512])
        first_half_b = sum(bits_b[:512])
        assert first_half_a > first_half_b

    def test_period_parameter(self):
        """Shorter period should complete cycle faster."""
        mod = SDModulator(signal="sine", amplitude=0.8, period=64, phase_deg=0.0)
        # Run 64 samples = 1 full cycle, should be similar density to DC=0
        bits = [mod.next_bit() for _ in range(640)]  # 10 full cycles
        ones = sum(bits)
        assert 250 < ones < 390  # roughly 50%
```

- [ ] **Step 6: Run sine tests to verify they pass (implementation already handles sine)**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_modulator.py -v`
Expected: All 7 tests PASS

- [ ] **Step 7: Write test for live parameter changes**

```python
# Append to tests/test_sd_modulator.py

class TestSDModulatorLiveConfig:
    """Parameters can be changed at runtime without reset."""

    def test_switch_dc_level(self):
        """Changing dc_level mid-stream affects subsequent output."""
        mod = SDModulator(signal="dc", dc_level=0.8)
        [mod.next_bit() for _ in range(100)]  # warm up
        mod.dc_level = -0.8
        bits = [mod.next_bit() for _ in range(1000)]
        ones = sum(bits)
        assert ones < 400  # should now produce mostly zeros

    def test_switch_signal_type(self):
        """Switching from dc to sine changes output pattern."""
        mod = SDModulator(signal="dc", dc_level=0.0)
        [mod.next_bit() for _ in range(100)]
        mod.signal = "sine"
        mod.amplitude = 0.8
        mod.period = 64
        bits = [mod.next_bit() for _ in range(640)]
        ones = sum(bits)
        # Should still be roughly 50% (sine averages to 0)
        assert 250 < ones < 390

    def test_reset_clears_state(self):
        """Reset zeroes integrators and sample index."""
        mod = SDModulator(signal="dc", dc_level=0.9)
        [mod.next_bit() for _ in range(500)]
        mod.reset()
        assert mod._integrator1 == 0.0
        assert mod._integrator2 == 0.0
        assert mod._sample_index == 0
```

- [ ] **Step 8: Run all modulator tests**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_modulator.py -v`
Expected: All 10 tests PASS

- [ ] **Step 9: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add pru_io/sd_modulator.py tests/test_sd_modulator.py
git commit -m "feat(sd): add 2nd-order sigma-delta modulator pattern generator"
```

---

## Task 2: SD Channel (Single Channel Accumulator)

**Files:**
- Create: `pru_io/sd_channel.py`
- Create: `tests/test_sd_channel.py`

- [ ] **Step 1: Write failing tests for accumulator logic**

```python
# tests/test_sd_channel.py
"""Tests for a single SD filter channel accumulator."""
import pytest
from pru_io.sd_channel import SDChannel


class TestAccumulation:
    """Accumulators run continuously, accumulating every SD tick."""

    def test_acc1_counts_ones(self):
        """acc1 is a running sum of input bits."""
        ch = SDChannel(osr=8)
        for _ in range(8):
            ch.tick(1)  # feed all ones
        assert ch.acc1 == 8

    def test_acc2_sums_acc1(self):
        """acc2 is running sum of acc1 values."""
        ch = SDChannel(osr=8)
        # Feed: 1,1,1,0,0,0,0,0
        for bit in [1, 1, 1, 0, 0, 0, 0, 0]:
            ch.tick(bit)
        # acc1 after each tick: 1,2,3,3,3,3,3,3
        # acc2 = sum of acc1 series: 1+2+3+3+3+3+3+3 = 21
        assert ch.acc1 == 3
        assert ch.acc2 == 21

    def test_acc3_sums_acc2(self):
        """acc3 is running sum of acc2 values."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # acc1: 1,2,3,4 → acc1=4
        # acc2: 1,3,6,10 → acc2=10
        # acc3: 1,4,10,20 → acc3=20
        assert ch.acc1 == 4
        assert ch.acc2 == 10
        assert ch.acc3 == 20


class TestShadowLatch:
    """Shadow registers latch every OSR ticks; valid flag set."""

    def test_valid_set_after_osr_ticks(self):
        """valid flag asserts after exactly OSR ticks."""
        ch = SDChannel(osr=4)
        for _ in range(3):
            ch.tick(1)
            assert ch.valid is False
        ch.tick(1)
        assert ch.valid is True

    def test_shadow_captures_accumulator_values(self):
        """Shadow registers capture acc1/2/3 at OSR boundary."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        assert ch.shadow_acc1 == 4
        assert ch.shadow_acc2 == 10
        assert ch.shadow_acc3 == 20

    def test_accumulators_keep_running_after_latch(self):
        """Accumulators do NOT reset after shadow latch."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        # After latch, feed 4 more ones
        for _ in range(4):
            ch.tick(1)
        # acc1 keeps accumulating: was 4, now 4+4=8
        assert ch.acc1 == 8

    def test_valid_cleared_on_read(self):
        """Reading clears the valid flag."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        assert ch.valid is True
        ch.read_and_clear_valid()
        assert ch.valid is False

    def test_second_latch_updates_shadow(self):
        """Next OSR boundary updates shadow with new accumulator state."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        ch.read_and_clear_valid()
        for _ in range(4):
            ch.tick(0)
        # acc1 was 4, now +0+0+0+0 = still 4
        assert ch.shadow_acc1 == 4
        # acc2 was 10, now +4+4+4+4 = 26
        assert ch.shadow_acc2 == 26


class TestReinit:
    """reinit command resets accumulators to zero."""

    def test_reinit_zeros_accumulators(self):
        """reinit clears acc1, acc2, acc3, and sample counter."""
        ch = SDChannel(osr=8)
        for _ in range(5):
            ch.tick(1)
        ch.reinit()
        assert ch.acc1 == 0
        assert ch.acc2 == 0
        assert ch.acc3 == 0

    def test_reinit_does_not_clear_shadow(self):
        """reinit preserves the last latched shadow values."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        shadow_before = ch.shadow_acc3
        ch.reinit()
        assert ch.shadow_acc3 == shadow_before

    def test_valid_after_reinit_requires_full_osr(self):
        """After reinit, need full OSR ticks for next valid."""
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        ch.read_and_clear_valid()
        ch.reinit()
        for _ in range(3):
            ch.tick(1)
            assert ch.valid is False
        ch.tick(1)
        assert ch.valid is True


class TestOverflow:
    """Overflow when accumulator exceeds 28-bit range."""

    def test_overflow_flag_on_28bit_exceed(self):
        """ovf flag set when acc3 exceeds 0x0FFFFFFF."""
        ch = SDChannel(osr=256)
        ch.acc3 = 0x0FFFFFFF
        ch.tick(1)  # acc1+=1, acc2+=acc1, acc3+=acc2 → acc3 overflows
        # After tick, acc3 > 0x0FFFFFFF → overflow
        assert ch.ovf is True

    def test_overflow_sticky(self):
        """ovf remains set until explicitly cleared."""
        ch = SDChannel(osr=256)
        ch.acc3 = 0x0FFFFFFF
        ch.tick(1)
        ch.tick(0)  # another tick
        assert ch.ovf is True  # still set

    def test_clear_overflow(self):
        """clr_ovf resets the overflow flag."""
        ch = SDChannel(osr=256)
        ch.acc3 = 0x0FFFFFFF
        ch.tick(1)
        assert ch.ovf is True
        ch.clear_ovf()
        assert ch.ovf is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_channel.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pru_io.sd_channel'`

- [ ] **Step 3: Implement SDChannel**

```python
# pru_io/sd_channel.py
"""Single sigma-delta filter channel with 3 cascaded accumulators.

Models the hardware accumulator stages of the ICSS-G SCU SD filter.
Accumulators run continuously; shadow registers latch every OSR ticks.
"""

_ACC_MAX = 0x0FFFFFFF  # 28-bit max


class SDChannel:
    """One SD filter channel: 3 cascaded accumulators + shadow latch + overflow."""

    def __init__(self, osr: int = 64):
        self.osr = osr

        # Live accumulators (run continuously)
        self.acc1: int = 0
        self.acc2: int = 0
        self.acc3: int = 0

        # Shadow registers (latched every OSR ticks)
        self.shadow_acc1: int = 0
        self.shadow_acc2: int = 0
        self.shadow_acc3: int = 0

        # Status flags
        self.valid: bool = False
        self.ovf: bool = False

        # Internal counter
        self._sample_count: int = 0

    def tick(self, bit: int) -> None:
        """Process one SD clock tick with input *bit* (0 or 1).

        Advances all three accumulators and checks for OSR boundary latch.
        """
        self.acc1 += bit
        self.acc2 += self.acc1
        self.acc3 += self.acc2

        # Overflow detection (28-bit)
        if self.acc3 > _ACC_MAX:
            self.ovf = True

        self._sample_count += 1
        if self._sample_count >= self.osr:
            # Latch to shadow
            self.shadow_acc1 = self.acc1
            self.shadow_acc2 = self.acc2
            self.shadow_acc3 = self.acc3
            self.valid = True
            self._sample_count = 0

    def read_and_clear_valid(self) -> None:
        """Clear the valid flag (called when PRU reads R31)."""
        self.valid = False

    def reinit(self) -> None:
        """Reset accumulators and sample counter to zero. Shadow preserved."""
        self.acc1 = 0
        self.acc2 = 0
        self.acc3 = 0
        self._sample_count = 0

    def clear_ovf(self) -> None:
        """Clear the overflow flag."""
        self.ovf = False

    def get_data(self, acc_sel: int) -> int:
        """Return the shadow accumulator value selected by *acc_sel*.

        acc_sel: 0=acc3 (sinc3), 1=acc2 (sinc2), 2=acc1 (sinc1)
        """
        if acc_sel == 0:
            return self.shadow_acc3 & _ACC_MAX
        elif acc_sel == 1:
            return self.shadow_acc2 & _ACC_MAX
        else:
            return self.shadow_acc1 & _ACC_MAX
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_channel.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add pru_io/sd_channel.py tests/test_sd_channel.py
git commit -m "feat(sd): add SD channel accumulator with shadow latch and overflow"
```

---

## Task 3: SD Configuration Registers

**Files:**
- Create: `pru_io/sd_registers.py`
- Create: `tests/test_sd_registers.py`
- Modify: `memory.cfg` (add SD_CFG memory region)

- [ ] **Step 1: Write failing tests for register read/write**

```python
# tests/test_sd_registers.py
"""Tests for SD configuration register memory-mapped interface."""
import pytest
from pru_io.sd_registers import SDRegisters


class TestSDCfgReg0:
    """Global SD_CFG_REG0 at offset 0x26044."""

    def test_initial_value_zero(self):
        regs = SDRegisters()
        assert regs.read(0x26044, 4) == b'\x00\x00\x00\x00'

    def test_write_share_en(self):
        """Write SHARE_EN bit [8]."""
        regs = SDRegisters()
        regs.write(0x26044, (1 << 8).to_bytes(4, 'little'))
        val = int.from_bytes(regs.read(0x26044, 4), 'little')
        assert (val >> 8) & 1 == 1

    def test_read_share_en_field(self):
        regs = SDRegisters()
        regs.write(0x26044, (1 << 8).to_bytes(4, 'little'))
        assert regs.get_share_en() is True


class TestSDClkSelReg:
    """Per-channel SD_CLK_SEL_REGn at offset 0x26048 + n*8."""

    def test_channel0_offset(self):
        regs = SDRegisters()
        # Write ACC_SEL=1 (bits [5:4]) for channel 0
        val = 1 << 4
        regs.write(0x26048, val.to_bytes(4, 'little'))
        assert regs.get_acc_sel(0) == 1

    def test_channel1_offset(self):
        regs = SDRegisters()
        # Channel 1 at offset 0x26050
        val = 2 << 4  # ACC_SEL=2 (sinc1)
        regs.write(0x26050, val.to_bytes(4, 'little'))
        assert regs.get_acc_sel(1) == 2

    def test_channel2_offset(self):
        regs = SDRegisters()
        # Channel 2 at offset 0x26058
        val = 0 << 4  # ACC_SEL=0 (sinc3, default)
        regs.write(0x26058, val.to_bytes(4, 'little'))
        assert regs.get_acc_sel(2) == 0

    def test_clk_sel_field(self):
        regs = SDRegisters()
        val = 2  # CLK_SEL=2 (shared)
        regs.write(0x26048, val.to_bytes(4, 'little'))
        assert regs.get_clk_sel(0) == 2

    def test_clk_inv_field(self):
        regs = SDRegisters()
        val = 1 << 2  # CLK_INV=1
        regs.write(0x26048, val.to_bytes(4, 'little'))
        assert regs.get_clk_inv(0) is True


class TestSDSampleSizeReg:
    """Per-channel SD_SAMPLE_SIZE_REGn at offset 0x2604C + n*8."""

    def test_sample_size_field(self):
        regs = SDRegisters()
        # Write SAMPLE_SIZE=63 (OSR=64)
        regs.write(0x2604C, (63).to_bytes(4, 'little'))
        assert regs.get_sample_size(0) == 63
        assert regs.get_osr(0) == 64  # OSR = sample_size + 1

    def test_channel1_sample_size(self):
        regs = SDRegisters()
        # Channel 1 at offset 0x26054
        regs.write(0x26054, (127).to_bytes(4, 'little'))
        assert regs.get_osr(1) == 128

    def test_fd_en_field(self):
        regs = SDRegisters()
        val = 1 << 23  # FD_EN bit
        regs.write(0x2604C, val.to_bytes(4, 'little'))
        assert regs.get_fd_en(0) is True

    def test_fd_window_size_field(self):
        regs = SDRegisters()
        val = 3 << 8  # FD_WINDOW_SIZE=3 → 16 samples
        regs.write(0x2604C, val.to_bytes(4, 'little'))
        assert regs.get_fd_window_size(0) == 3


class TestWriteCallbacks:
    """Write callbacks notify the SD filter of config changes."""

    def test_callback_on_osr_change(self):
        received = []
        regs = SDRegisters()
        regs.on_config_change = lambda ch, field, val: received.append((ch, field, val))
        regs.write(0x2604C, (63).to_bytes(4, 'little'))
        assert ("osr", 0) in [(f, c) for c, f, v in received]

    def test_callback_on_acc_sel_change(self):
        received = []
        regs = SDRegisters()
        regs.on_config_change = lambda ch, field, val: received.append((ch, field, val))
        regs.write(0x26048, (1 << 4).to_bytes(4, 'little'))
        assert ("acc_sel", 0) in [(f, c) for c, f, v in received]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_registers.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement SDRegisters**

```python
# pru_io/sd_registers.py
"""Memory-mapped SD configuration registers for ICSS-G sigma-delta filter.

Register layout matches real hardware:
  SD_CFG_REG0:        0x26044 (global)
  SD_CLK_SEL_REGn:    0x26048 + n*8 (per channel, n=0..2)
  SD_SAMPLE_SIZE_REGn: 0x2604C + n*8 (per channel, n=0..2)
"""

from typing import Callable, Optional

_BASE = 0x26044
_CLK_SEL_BASE = 0x26048
_SAMPLE_SIZE_BASE = 0x2604C
_CHANNEL_STRIDE = 8
_NUM_CHANNELS = 3

# Total register space: 0x26044 to 0x2605F (28 bytes)
_REG_SIZE = 0x20  # 32 bytes covers all registers with margin


class SDRegisters:
    """SD filter configuration registers with field accessors and write callbacks."""

    def __init__(self):
        self._data = bytearray(_REG_SIZE)
        self.on_config_change: Optional[Callable[[int, str, int], None]] = None

    def _offset(self, addr: int) -> int:
        """Convert absolute address to internal offset."""
        return addr - _BASE

    def read(self, addr: int, length: int) -> bytes:
        """Read *length* bytes from register address *addr*."""
        off = self._offset(addr)
        return bytes(self._data[off:off + length])

    def write(self, addr: int, data: bytes) -> None:
        """Write *data* to register address *addr* and fire callbacks."""
        off = self._offset(addr)
        old = bytes(self._data[off:off + len(data)])
        self._data[off:off + len(data)] = data
        self._fire_callbacks(addr, old)

    def _read_u32(self, addr: int) -> int:
        """Read a 32-bit register value."""
        off = self._offset(addr)
        return int.from_bytes(self._data[off:off + 4], 'little')

    def _fire_callbacks(self, addr: int, old: bytes) -> None:
        """Detect which fields changed and notify via on_config_change."""
        if self.on_config_change is None:
            return
        # Determine which register was written
        if addr == _BASE:
            new_val = self._read_u32(_BASE)
            old_val = int.from_bytes(old[:4], 'little') if len(old) >= 4 else 0
            if ((new_val >> 8) & 1) != ((old_val >> 8) & 1):
                self.on_config_change(-1, "share_en", (new_val >> 8) & 1)
        else:
            for ch in range(_NUM_CHANNELS):
                clk_addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
                ss_addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
                if addr == clk_addr:
                    self.on_config_change(ch, "acc_sel", self.get_acc_sel(ch))
                    self.on_config_change(ch, "clk_sel", self.get_clk_sel(ch))
                    self.on_config_change(ch, "clk_inv", int(self.get_clk_inv(ch)))
                elif addr == ss_addr:
                    self.on_config_change(ch, "osr", self.get_osr(ch))
                    self.on_config_change(ch, "fd_en", int(self.get_fd_en(ch)))
                    self.on_config_change(ch, "fd_window", self.get_fd_window_size(ch))

    # --- Field accessors: SD_CFG_REG0 ---

    def get_share_en(self) -> bool:
        return bool((self._read_u32(_BASE) >> 8) & 1)

    # --- Field accessors: SD_CLK_SEL_REGn ---

    def get_acc_sel(self, ch: int) -> int:
        """Return ACC_SEL[5:4] for channel *ch*: 0=acc3, 1=acc2, 2=acc1."""
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 4) & 0x3

    def get_clk_sel(self, ch: int) -> int:
        """Return CLK_SEL[1:0] for channel *ch*."""
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return self._read_u32(addr) & 0x3

    def get_clk_inv(self, ch: int) -> bool:
        """Return CLK_INV[2] for channel *ch*."""
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return bool((self._read_u32(addr) >> 2) & 1)

    def get_fd_zero_max_limit(self, ch: int) -> int:
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 17) & 0x1F

    def get_fd_zero_min_limit(self, ch: int) -> int:
        addr = _CLK_SEL_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 11) & 0x1F

    # --- Field accessors: SD_SAMPLE_SIZE_REGn ---

    def get_sample_size(self, ch: int) -> int:
        """Return raw SAMPLE_SIZE[7:0] for channel *ch*."""
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return self._read_u32(addr) & 0xFF

    def get_osr(self, ch: int) -> int:
        """Return effective OSR (sample_size + 1)."""
        return self.get_sample_size(ch) + 1

    def get_fd_en(self, ch: int) -> bool:
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return bool((self._read_u32(addr) >> 23) & 1)

    def get_fd_window_size(self, ch: int) -> int:
        """Return FD_WINDOW_SIZE[10:8]: 0=4, 1=8, ..., 7=32 samples."""
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 8) & 0x7

    def get_fd_one_max_limit(self, ch: int) -> int:
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 17) & 0x1F

    def get_fd_one_min_limit(self, ch: int) -> int:
        addr = _SAMPLE_SIZE_BASE + ch * _CHANNEL_STRIDE
        return (self._read_u32(addr) >> 11) & 0x1F

    # --- Bulk config snapshot (for UI) ---

    def get_channel_config(self, ch: int) -> dict:
        """Return full config snapshot for channel *ch* as a dict."""
        return {
            "osr": self.get_osr(ch),
            "acc_sel": self.get_acc_sel(ch),
            "clk_sel": self.get_clk_sel(ch),
            "clk_inv": self.get_clk_inv(ch),
            "fd_en": self.get_fd_en(ch),
            "fd_window_size": self.get_fd_window_size(ch),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_registers.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Add SD_CFG memory region to memory.cfg**

Append to `memory.cfg`:
```ini
[ICSS_SD_CFG]
base = 0x00026044
size = 0x20
read_latency = 2
write_latency = 1
jitter = 0
```

- [ ] **Step 6: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add pru_io/sd_registers.py tests/test_sd_registers.py memory.cfg
git commit -m "feat(sd): add memory-mapped SD configuration registers"
```

---

## Task 4: SD Filter (Top-Level Peripheral Wiring Channels + Registers)

**Files:**
- Create: `pru_io/sd_filter.py`
- Create: `tests/test_sd_filter.py`

- [ ] **Step 1: Write failing tests for R30/R31 interface**

```python
# tests/test_sd_filter.py
"""Tests for the top-level SigmaDeltaFilter peripheral."""
import pytest
from pru_io.sd_filter import SigmaDeltaFilter


class TestR30Decode:
    """R30 write decodes control fields."""

    def test_ch_sel_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0b0001 << 26 | 1 << 25)  # ch_sel=1, sd_en=1
        assert filt.ch_sel == 1

    def test_sd_en_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25)
        assert filt.sd_en is True
        filt.process_r30(0)
        assert filt.sd_en is False

    def test_snoop_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25 | 1 << 24)
        assert filt.snoop is True

    def test_data_sel_from_r30(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(1 << 25 | 1 << 23)
        assert filt.data_sel is True


class TestR31Status:
    """R31 read returns packed status for selected channel."""

    def test_r31_format_with_valid(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)  # select ch0, enable
        # Manually set channel state for testing
        filt.channels[0].shadow_acc3 = 0x0ABC123
        filt.channels[0].valid = True
        filt.channels[0].ovf = False
        r31 = filt.get_r31_status()
        # [31:30]=00, [29]=ovf=0, [28]=valid=1, [27:0]=0x0ABC123
        assert (r31 >> 28) & 1 == 1  # valid
        assert (r31 >> 29) & 1 == 0  # ovf
        assert r31 & 0x0FFFFFFF == 0x0ABC123

    def test_r31_read_clears_valid(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].valid = True
        filt.get_r31_status()
        assert filt.channels[0].valid is False

    def test_r31_overflow_bit(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].ovf = True
        filt.channels[0].shadow_acc3 = 0
        r31 = filt.get_r31_status()
        assert (r31 >> 29) & 1 == 1


class TestR31Commands:
    """R31 write issues commands (clr_ovf, reinit)."""

    def test_clr_ovf_command(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].ovf = True
        filt.process_r31_command(1 << 29)  # clr_ovf
        assert filt.channels[0].ovf is False

    def test_reinit_command(self):
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.process_r30(0 << 26 | 1 << 25)
        filt.channels[0].acc1 = 100
        filt.channels[0].acc2 = 200
        filt.channels[0].acc3 = 300
        filt.process_r31_command(1 << 28)  # reinit
        assert filt.channels[0].acc1 == 0
        assert filt.channels[0].acc2 == 0
        assert filt.channels[0].acc3 == 0


class TestTickAdvancement:
    """tick() advances SD channels via async clock model."""

    def test_tick_advances_channel(self):
        """With sd_clock=200MHz and pru_clock=200MHz, 1 tick per step."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.modulators[0].sd_clock_mhz = 200.0  # 1:1 ratio for easy testing
        filt.process_r30(1 << 25)  # enable SD
        filt.channels[0].osr = 4
        # 4 ticks should produce a valid
        for _ in range(4):
            filt.tick()
        assert filt.channels[0].valid is True

    def test_tick_ratio_div10(self):
        """With sd_clock=20MHz and pru_clock=200MHz, 1 tick per 10 steps."""
        filt = SigmaDeltaFilter(pru_clock_mhz=200.0)
        filt.modulators[0].sd_clock_mhz = 20.0
        filt.process_r30(1 << 25)
        filt.channels[0].osr = 4
        # Need 4 SD ticks = 40 PRU steps at 10:1 ratio
        for _ in range(39):
            filt.tick()
        assert filt.channels[0].valid is False
        filt.tick()  # 40th step → 4th SD tick
        assert filt.channels[0].valid is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_filter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement SigmaDeltaFilter**

```python
# pru_io/sd_filter.py
"""Top-level sigma-delta filter peripheral for the PRU simulator.

Owns 3 SD channels and 3 pattern generator modulators.
Handles R30/R31 interface and async clock tick advancement.
"""

from pru_io.sd_channel import SDChannel
from pru_io.sd_modulator import SDModulator

_NUM_CHANNELS = 3


class SigmaDeltaFilter:
    """SD filter peripheral: 3 channels with pattern generators and R30/R31 interface."""

    def __init__(self, pru_clock_mhz: float = 200.0):
        self.pru_clock_mhz = pru_clock_mhz

        # Channels and modulators
        self.channels: list[SDChannel] = [SDChannel(osr=64) for _ in range(_NUM_CHANNELS)]
        self.modulators: list[SDModulator] = [SDModulator() for _ in range(_NUM_CHANNELS)]

        # R30 decoded control fields
        self.ch_sel: int = 0
        self.sd_en: bool = False
        self.snoop: bool = False
        self.data_sel: bool = False

        # Async clock accumulators (one per channel)
        self._clock_acc: list[float] = [0.0] * _NUM_CHANNELS

    def process_r30(self, value: int) -> None:
        """Decode R30 control bits."""
        self.ch_sel = (value >> 26) & 0xF
        self.sd_en = bool((value >> 25) & 1)
        self.snoop = bool((value >> 24) & 1)
        self.data_sel = bool((value >> 23) & 1)

    def get_r31_status(self) -> int:
        """Return packed R31 status word for the selected channel.

        Format: [31:30]=00, [29]=ovf, [28]=valid, [27:0]=data
        Reading clears the valid flag.
        """
        ch_idx = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0
        ch = self.channels[ch_idx]
        acc_sel = 0  # default sinc3; will be wired to registers later
        data = ch.get_data(acc_sel)
        status = (int(ch.ovf) << 29) | (int(ch.valid) << 28) | (data & 0x0FFFFFFF)
        ch.read_and_clear_valid()
        return status

    def get_r31_status_with_acc_sel(self, acc_sel: int) -> int:
        """Return packed R31 status using explicit acc_sel (from register config)."""
        ch_idx = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0
        ch = self.channels[ch_idx]
        data = ch.get_data(acc_sel)
        status = (int(ch.ovf) << 29) | (int(ch.valid) << 28) | (data & 0x0FFFFFFF)
        ch.read_and_clear_valid()
        return status

    def process_r31_command(self, value: int) -> None:
        """Process R31 write as command (clr_ovf, reinit) for selected channel."""
        ch_idx = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0
        ch = self.channels[ch_idx]
        if (value >> 29) & 1:
            ch.clear_ovf()
        if (value >> 28) & 1:
            ch.reinit()

    def tick(self) -> None:
        """Advance all channels by one PRU clock tick (async clock model).

        Each channel advances 0 or more SD ticks based on its modulator's clock rate
        relative to the PRU clock.
        """
        for i in range(_NUM_CHANNELS):
            mod = self.modulators[i]
            ratio = mod.sd_clock_mhz / self.pru_clock_mhz
            self._clock_acc[i] += ratio
            while self._clock_acc[i] >= 1.0:
                self._clock_acc[i] -= 1.0
                bit = mod.next_bit()
                self.channels[i].tick(bit)

    def get_state(self) -> dict:
        """Return full state for UI broadcast."""
        return {
            "sd_en": self.sd_en,
            "ch_sel": self.ch_sel,
            "snoop": self.snoop,
            "data_sel": self.data_sel,
            "channels": [
                {
                    "id": i,
                    "acc1": ch.acc1,
                    "acc2": ch.acc2,
                    "acc3": ch.acc3,
                    "shadow_acc1": ch.shadow_acc1,
                    "shadow_acc2": ch.shadow_acc2,
                    "shadow_acc3": ch.shadow_acc3,
                    "valid": ch.valid,
                    "ovf": ch.ovf,
                    "selected": i == self.ch_sel,
                    "osr": ch.osr,
                }
                for i, ch in enumerate(self.channels)
            ],
            "modulators": [
                {
                    "signal": mod.signal,
                    "sd_clock_mhz": mod.sd_clock_mhz,
                    "dc_level": mod.dc_level,
                    "amplitude": mod.amplitude,
                    "period": mod.period,
                    "phase_deg": mod.phase_deg,
                }
                for mod in self.modulators
            ],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_filter.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add pru_io/sd_filter.py tests/test_sd_filter.py
git commit -m "feat(sd): add top-level SigmaDeltaFilter with R30/R31 interface and async clock"
```

---

## Task 5: Wire SD Filter into IOPort and Simulator

**Files:**
- Modify: `pru_io/io_port.py`
- Modify: `simulator.py`
- Modify: `core/pru_core.py`
- Create: `tests/test_sd_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/test_sd_integration.py
"""Integration tests: PRU core ↔ SD filter via IOPort."""
import pytest
from simulator import Simulator


class TestSDModeSwitch:
    """IOPort delegates to SD filter when sd_en is set via R30."""

    def test_r30_write_enables_sd(self):
        sim = Simulator()
        # Write R30 with sd_en=1 via assembly
        source = "MOV r30, 0x02000000"  # bit 25 = sd_en
        sim.load("pru0", source)
        sim.step("pru0")
        assert sim.cores["pru0"].io_port.sd_filter.sd_en is True

    def test_r31_read_returns_sd_status_when_enabled(self):
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        # Enable SD mode
        sd.process_r30(1 << 25)
        # Manually force a known state
        sd.channels[0].shadow_acc3 = 0x12345
        sd.channels[0].valid = True
        # Read R31 through IOPort
        r31 = sim.cores["pru0"].io_port.read_r31()
        assert r31 & 0x0FFFFFFF == 0x12345
        assert (r31 >> 28) & 1 == 1  # valid

    def test_r31_returns_gpi_when_sd_disabled(self):
        sim = Simulator()
        sim.cores["pru0"].io_port.set_gpi_pin(5, True)
        r31 = sim.cores["pru0"].io_port.read_r31()
        assert (r31 >> 5) & 1 == 1

    def test_sd_tick_called_on_step(self):
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        sd.modulators[0].sd_clock_mhz = 200.0  # 1:1 for easy test
        sd.process_r30(1 << 25)
        sd.channels[0].osr = 4
        # Load NOP instructions
        source = "NOP\\nNOP\\nNOP\\nNOP"
        sim.load("pru0", source)
        for _ in range(4):
            sim.step("pru0")
        assert sd.channels[0].valid is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_integration.py -v`
Expected: FAIL (IOPort has no sd_filter attribute)

- [ ] **Step 3: Modify IOPort to support SD mode delegation**

Replace the contents of `pru_io/io_port.py` with:

```python
# pru_io/io_port.py
"""IOPort — models the PRU's R30 (GPO) and R31 (GPI) 20-bit I/O registers.

When the SD filter is attached and sd_en is active, R30/R31 are routed
through the sigma-delta filter interface instead of raw GPIO.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pru_io.sd_filter import SigmaDeltaFilter

_MASK_20 = 0x000FFFFF


class IOPort:
    """20-bit general-purpose output (GPO) and input (GPI) port with SD mode."""

    def __init__(self) -> None:
        self.gpo: int = 0   # driven by writes to R30
        self.gpi: int = 0   # sampled by reads from R31
        self.sd_filter: SigmaDeltaFilter | None = None

    # ------------------------------------------------------------------
    # R30 / GPO
    # ------------------------------------------------------------------

    def write_r30(self, value: int) -> None:
        """Set GPO state from *value*. If SD filter attached, also process R30."""
        self.gpo = value & _MASK_20
        if self.sd_filter is not None:
            self.sd_filter.process_r30(value)

    # ------------------------------------------------------------------
    # R31 / GPI
    # ------------------------------------------------------------------

    def read_r31(self) -> int:
        """Return R31 value: SD status if sd_en active, else GPI masked to 20 bits."""
        if self.sd_filter is not None and self.sd_filter.sd_en:
            return self.sd_filter.get_r31_status()
        return self.gpi & _MASK_20

    def write_r31(self, value: int) -> None:
        """Process R31 write as SD command (if SD mode active)."""
        if self.sd_filter is not None and self.sd_filter.sd_en:
            self.sd_filter.process_r31_command(value)

    def set_gpi_pin(self, pin: int, value: bool) -> None:
        """Set or clear a single GPI pin (0–19)."""
        if pin < 0 or pin > 19:
            raise ValueError(f"GPI pin index {pin} out of range 0-19")
        if value:
            self.gpi |= 1 << pin
        else:
            self.gpi &= ~(1 << pin)
        self.gpi &= _MASK_20

    def set_gpi_word(self, value: int) -> None:
        """Set all GPI bits at once, masked to 20 bits."""
        self.gpi = value & _MASK_20

    # ------------------------------------------------------------------
    # Pin-list helpers
    # ------------------------------------------------------------------

    def get_gpo_pins(self) -> list[int]:
        """Return a list of 20 integers (0 or 1) representing each GPO pin."""
        return [(self.gpo >> i) & 1 for i in range(20)]

    def get_gpi_pins(self) -> list[int]:
        """Return a list of 20 integers (0 or 1) representing each GPI pin."""
        return [(self.gpi >> i) & 1 for i in range(20)]
```

- [ ] **Step 4: Modify simulator.py to create and wire SD filters**

Add to `simulator.py` — import and wiring in `__init__`:

```python
# Add import at top:
from pru_io.sd_filter import SigmaDeltaFilter

# In __init__, after creating cores, add:
        # Wire SD filters to each core's IOPort
        pru_clock_mhz = float(self._get_device_config(config_path).get("pru_clock_mhz", "200"))
        for name, core in self.cores.items():
            sd = SigmaDeltaFilter(pru_clock_mhz=pru_clock_mhz)
            core.io_port.sd_filter = sd
```

Add helper method `_get_device_config`:

```python
    def _get_device_config(self, config_path: str) -> dict:
        """Read [device] section from config file."""
        if not os.path.exists(config_path):
            return {}
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        if "device" in cfg:
            return dict(cfg["device"])
        return {}
```

- [ ] **Step 5: Modify pru_core.py to call sd_filter.tick() on each step**

In `core/pru_core.py`, inside the `step()` method, add after instruction execution:

```python
        # Advance SD filter clock (if attached)
        if self.io_port.sd_filter is not None:
            self.io_port.sd_filter.tick()
```

Also modify `_write_operand` to handle R31 writes for SD commands:

```python
        if isinstance(op, Register):
            self.registers.write(op.index, op.offset, op.width, value)
            if op.index == 30:
                self.io_port.write_r30(self.registers.read_full(30))
            elif op.index == 31:
                self.io_port.write_r31(self.registers.read_full(31))
```

- [ ] **Step 6: Add pru_clock_mhz to memory.cfg [device] section**

Add `pru_clock_mhz = 200` to the `[device]` section in `memory.cfg`:

```ini
[device]
target = AM243x
core_version = V4
pru_clock_mhz = 200
```

- [ ] **Step 7: Run integration tests**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_integration.py -v`
Expected: All 4 tests PASS

- [ ] **Step 8: Run full test suite to verify no regressions**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/ -v`
Expected: All existing tests still PASS

- [ ] **Step 9: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add pru_io/io_port.py simulator.py core/pru_core.py memory.cfg tests/test_sd_integration.py
git commit -m "feat(sd): wire SD filter into IOPort and simulator step cycle"
```

---

## Task 6: Wire SD Registers to Memory Bus

**Files:**
- Modify: `simulator.py`
- Modify: `pru_io/sd_filter.py`
- Create: `tests/test_sd_memmap.py`

- [ ] **Step 1: Write failing test for memory-mapped register access**

```python
# tests/test_sd_memmap.py
"""Tests for SD register access through the memory bus."""
import pytest
from simulator import Simulator


class TestSDRegisterMemoryMap:
    """SD config registers accessible via memory read/write."""

    def test_write_osr_via_memory(self):
        """Writing SAMPLE_SIZE register updates channel OSR."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        # Write OSR=64-1=63 to SD_SAMPLE_SIZE_REG0 (0x2604C)
        sim.memory.write(0x2604C, (63).to_bytes(4, 'little'))
        assert sd.channels[0].osr == 64

    def test_write_acc_sel_via_memory(self):
        """Writing CLK_SEL register updates acc_sel."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter
        # Write ACC_SEL=1 (sinc2) to SD_CLK_SEL_REG0 (0x26048)
        sim.memory.write(0x26048, (1 << 4).to_bytes(4, 'little'))
        assert sd.registers.get_acc_sel(0) == 1

    def test_read_register_via_memory(self):
        """Reading SD register address returns current value."""
        sim = Simulator()
        sim.memory.write(0x2604C, (127).to_bytes(4, 'little'))
        data = sim.memory_read(0x2604C, 4)
        val = int.from_bytes(data, 'little')
        assert val & 0xFF == 127
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_memmap.py -v`
Expected: FAIL (address 0x2604C not in memory map or no callback wiring)

- [ ] **Step 3: Add SD register region to MemoryBus with write callbacks**

Modify `simulator.py` to create an SD register-backed memory region. Add a new class `SDRegisterRegion` that extends `MemoryRegion` to intercept writes:

```python
# Add to simulator.py (or create mem/sd_region.py if preferred)
from pru_io.sd_registers import SDRegisters
from mem.regions import MemoryRegion


class SDRegisterRegion(MemoryRegion):
    """Memory region backed by SD configuration registers with write callbacks."""

    def __init__(self, sd_registers: SDRegisters):
        super().__init__("ICSS_SD_CFG", 0x00026044, 0x20, 2, 1, 0)
        self._sd_regs = sd_registers

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        return self._sd_regs.read(addr, length)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, len(data))
        self._sd_regs.write(addr, data)
```

In `Simulator.__init__`, after creating SD filters, wire the register region:

```python
        # Wire SD registers into memory bus
        for name, core in self.cores.items():
            sd = core.io_port.sd_filter
            sd.registers = SDRegisters()
            sd.registers.on_config_change = sd._on_config_change
            if name == "pru0":  # Only one copy in memory map
                sd_region = SDRegisterRegion(sd.registers)
                self.memory.add_region(sd_region)
```

Add `_on_config_change` method to `SigmaDeltaFilter`:

```python
    def _on_config_change(self, ch: int, field: str, value: int) -> None:
        """Handle config register changes."""
        if ch < 0:
            return  # global fields (share_en) handled elsewhere
        if field == "osr":
            self.channels[ch].osr = value
        elif field == "acc_sel":
            pass  # stored in registers, read at R31 time
```

- [ ] **Step 4: Update get_r31_status to use register acc_sel**

In `pru_io/sd_filter.py`, modify `get_r31_status`:

```python
    def get_r31_status(self) -> int:
        """Return packed R31 status word for the selected channel."""
        ch_idx = self.ch_sel if self.ch_sel < _NUM_CHANNELS else 0
        ch = self.channels[ch_idx]
        acc_sel = self.registers.get_acc_sel(ch_idx) if self.registers else 0
        data = ch.get_data(acc_sel)
        status = (int(ch.ovf) << 29) | (int(ch.valid) << 28) | (data & 0x0FFFFFFF)
        ch.read_and_clear_valid()
        return status
```

- [ ] **Step 5: Remove the hardcoded ICSS_SD_CFG entry from memory.cfg**

Remove the `[ICSS_SD_CFG]` section added in Task 3 Step 5 from `memory.cfg` — the region is now created programmatically via `SDRegisterRegion`.

- [ ] **Step 6: Run tests**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_memmap.py tests/test_sd_filter.py tests/test_sd_integration.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add simulator.py pru_io/sd_filter.py pru_io/sd_registers.py tests/test_sd_memmap.py memory.cfg
git commit -m "feat(sd): wire SD registers into memory bus with write callbacks"
```

---

## Task 7: Fast Detect Logic

**Files:**
- Modify: `pru_io/sd_channel.py`
- Create: `tests/test_sd_fast_detect.py`

- [ ] **Step 1: Write failing tests for Fast Detect**

```python
# tests/test_sd_fast_detect.py
"""Tests for the Fast Detect sliding-window monitor."""
import pytest
from pru_io.sd_channel import SDChannel


class TestFastDetect:
    """Sliding window counts zeros/ones and compares against thresholds."""

    def test_fd_disabled_by_default(self):
        ch = SDChannel(osr=64)
        ch.fd_en = False
        for _ in range(100):
            ch.tick(1)
        assert ch.fd_one_max is False
        assert ch.fd_zero_max is False

    def test_fd_one_max_triggers(self):
        """When ones in window exceed threshold, fd_one_max asserts."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_one_max_limit = 2  # threshold = limit+1 = 3
        # Feed 4 ones → 4 ones in window (4 >= 3)
        for _ in range(4):
            ch.tick(1)
        assert ch.fd_one_max is True

    def test_fd_zero_max_triggers(self):
        """When zeros in window exceed threshold, fd_zero_max asserts."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_zero_max_limit = 2  # threshold = limit+1 = 3
        # Feed 4 zeros → 4 zeros in window
        for _ in range(4):
            ch.tick(0)
        assert ch.fd_zero_max is True

    def test_fd_sliding_window(self):
        """Window slides: old bits drop out."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0  # 4 samples
        ch.fd_one_max_limit = 2  # trigger at 3 ones
        # Feed: 1,1,1,0 → 3 ones → triggers
        for bit in [1, 1, 1, 0]:
            ch.tick(bit)
        assert ch.fd_one_max is True
        # Clear and continue: feed 0,0,0 → window becomes [0,1,0,0]... shifts
        ch.fd_one_max = False
        for _ in range(3):
            ch.tick(0)
        # Window is now [0,0,0,0] → no trigger
        assert ch.fd_one_max is False

    def test_fd_clear_on_write_1(self):
        """Status flags are cleared by writing 1."""
        ch = SDChannel(osr=256)
        ch.fd_en = True
        ch.fd_window_size = 0
        ch.fd_one_max_limit = 0  # threshold = 1
        ch.tick(1)
        assert ch.fd_one_max is True
        ch.fd_one_max = False  # simulate W1C
        assert ch.fd_one_max is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_fast_detect.py -v`
Expected: FAIL (SDChannel has no fd_ attributes)

- [ ] **Step 3: Add Fast Detect logic to SDChannel**

Add to `pru_io/sd_channel.py`:

```python
# Add these attributes to __init__:
        # Fast Detect
        self.fd_en: bool = False
        self.fd_window_size: int = 0  # 0=4, 1=8, ..., 7=32 samples
        self.fd_one_max_limit: int = 0
        self.fd_one_min_limit: int = 0
        self.fd_zero_max_limit: int = 0
        self.fd_zero_min_limit: int = 0
        self.fd_one_max: bool = False
        self.fd_one_min: bool = False
        self.fd_zero_max: bool = False
        self.fd_zero_min: bool = False
        self._fd_shift_reg: int = 0  # 32-bit shift register
        self._fd_sample_count: int = 0
```

Add to the `tick()` method, after accumulator logic:

```python
        # Fast Detect
        if self.fd_en:
            window = 4 << self.fd_window_size  # 4, 8, 16, 32
            self._fd_shift_reg = ((self._fd_shift_reg << 1) | bit) & ((1 << window) - 1)
            self._fd_sample_count = min(self._fd_sample_count + 1, window)
            if self._fd_sample_count >= window:
                ones = bin(self._fd_shift_reg).count('1')
                zeros = window - ones
                threshold_one_max = self.fd_one_max_limit + 1
                threshold_one_min = self.fd_one_min_limit + 1
                threshold_zero_max = self.fd_zero_max_limit + 1
                threshold_zero_min = self.fd_zero_min_limit + 1
                if ones >= threshold_one_max:
                    self.fd_one_max = True
                if ones <= threshold_one_min:
                    self.fd_one_min = True
                if zeros >= threshold_zero_max:
                    self.fd_zero_max = True
                if zeros <= threshold_zero_min:
                    self.fd_zero_min = True
```

- [ ] **Step 4: Run tests**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_fast_detect.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add pru_io/sd_channel.py tests/test_sd_fast_detect.py
git commit -m "feat(sd): add Fast Detect sliding-window logic to SD channel"
```

---

## Task 8: Backend — WebSocket SD State Broadcast & Actions

**Files:**
- Modify: `ui/server.py`
- Modify: `simulator.py` (add SD-related public API methods)

- [ ] **Step 1: Add SD state to Simulator public API**

Add to `simulator.py`:

```python
    def sd_state(self, core: str) -> dict | None:
        """Return SD filter state for *core*, or None if no SD filter."""
        pru = self._get_core(core)
        if pru.io_port.sd_filter is None:
            return None
        sd = pru.io_port.sd_filter
        state = sd.get_state()
        # Add register config per channel
        if sd.registers:
            for i, ch_state in enumerate(state["channels"]):
                ch_state["config"] = sd.registers.get_channel_config(i)
        return state

    def set_sd_modulator(self, core: str, channel: int, **params) -> None:
        """Update pattern generator parameters for a channel."""
        sd = self._get_core(core).io_port.sd_filter
        if sd is None:
            return
        mod = sd.modulators[channel]
        for key, val in params.items():
            if hasattr(mod, key):
                setattr(mod, key, val)
```

- [ ] **Step 2: Add SD state to `_send_state` in server.py**

In `ui/server.py`, modify `_send_state` to include SD data in the `io` dict:

```python
    # Inside _send_state, replace the "io" section:
        sd_state = sim.sd_state(core)
        io_data = {
            "mode": "sd" if (sd_state and sd_state.get("sd_en")) else "gpio",
            "gpo_pins": gpo_pins,
            "gpi_pins": c.io_port.get_gpi_pins(),
        }
        if sd_state:
            io_data["sd"] = sd_state
```

- [ ] **Step 3: Add WebSocket action handlers for SD**

Add to the WebSocket message handler in `server.py`:

```python
            elif action == "set_sd_modulator":
                ch = int(msg.get("channel", 0))
                params = msg.get("params", {})
                sim.set_sd_modulator(core, ch, **params)
                await _send_state(websocket, core)
            elif action == "write_sd_register":
                addr = int(msg.get("addr", 0))
                value = int(msg.get("value", 0))
                sim.memory.write(addr, value.to_bytes(4, 'little'))
                await _send_state(websocket, core)
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add ui/server.py simulator.py
git commit -m "feat(sd): add SD state broadcast and WebSocket actions to server"
```

---

## Task 9: Frontend — SD Panel UI

**Files:**
- Modify: `ui/static/index.html`
- Modify: `ui/static/app.js`

- [ ] **Step 1: Add SD panel HTML structure to index.html**

Add inside the `#io-panel .panel-body`, after the GPI section and before the UART decoder divider:

```html
    <!-- SD Interface (hidden when GPIO mode) -->
    <div class="io-section sd-interface" id="sd-interface" style="display:none;">
      <div class="sd-mode-bar">
        <span class="sd-mode-badge">SD MODE</span>
        <span class="sd-mode-info" id="sd-mode-info">PRU0 owns Ch 0–2</span>
        <a class="sd-mode-switch" id="sd-switch-gpio" href="#">Switch to GPIO →</a>
      </div>

      <!-- R30 Control decode -->
      <div class="sd-r30-decode" id="sd-r30-decode"></div>

      <!-- Channel cards -->
      <div class="sd-channels" id="sd-channels"></div>

      <!-- Pattern Generator -->
      <div class="sd-section-title">Pattern Generator</div>
      <div class="sd-pattern-gen" id="sd-pattern-gen"></div>

      <!-- Config register shortcuts -->
      <div class="sd-config-bar" id="sd-config-bar"></div>
    </div>
```

- [ ] **Step 2: Add SD panel CSS to index.html**

Add CSS styles in the `<style>` section:

```css
.sd-interface { font-family: monospace; font-size: 11px; }
.sd-mode-bar { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid #333; margin-bottom: 8px; }
.sd-mode-badge { background: #1a4a1a; color: #66bb6a; padding: 2px 8px; border-radius: 3px; font-size: 10px; }
.sd-mode-info { color: #888; font-size: 10px; }
.sd-mode-switch { color: #4fc3f7; font-size: 10px; text-decoration: underline; margin-left: auto; cursor: pointer; }
.sd-r30-decode { background: #1a1a2e; padding: 6px 8px; border-radius: 4px; margin-bottom: 8px; font-size: 10px; }
.sd-r30-field { background: #2d2d44; padding: 1px 5px; border-radius: 2px; margin-right: 4px; display: inline-block; }
.sd-channels { display: flex; gap: 6px; margin-bottom: 8px; }
.sd-channel-card { border: 1px solid #333; padding: 8px; border-radius: 4px; flex: 1; background: #0d1117; }
.sd-channel-card.selected { border-color: #4fc3f7; background: #0a1628; }
.sd-channel-title { font-weight: bold; margin-bottom: 4px; }
.sd-channel-config { color: #666; font-size: 9px; margin-bottom: 4px; }
.sd-acc-row { font-size: 10px; }
.sd-acc-val { color: #fff; }
.sd-status { margin-top: 4px; padding-top: 4px; border-top: 1px solid #333; font-size: 10px; }
.sd-valid { color: #66bb6a; }
.sd-section-title { color: #ce93d8; font-size: 11px; font-weight: bold; margin: 8px 0 4px; }
.sd-pattern-gen { display: flex; gap: 6px; margin-bottom: 8px; }
.sd-mod-card { border: 1px solid #444; padding: 6px; border-radius: 4px; flex: 1; background: #1a1a2e; font-size: 10px; }
.sd-mod-row { display: flex; justify-content: space-between; margin-bottom: 2px; }
.sd-mod-label { color: #888; }
.sd-mod-value { color: #fff; }
.sd-mod-value select, .sd-mod-value input { background: #2d2d44; border: 1px solid #555; color: #fff; font-size: 10px; padding: 0 4px; border-radius: 2px; width: 70px; }
.sd-config-bar { border-top: 1px solid #333; padding-top: 6px; }
.sd-config-bar span { font-size: 9px; color: #888; display: block; margin-bottom: 4px; }
.sd-config-chips { display: flex; gap: 4px; flex-wrap: wrap; }
.sd-config-chip { background: #2d2d44; padding: 2px 6px; border-radius: 3px; font-size: 10px; cursor: pointer; color: #ccc; }
```

- [ ] **Step 3: Add SD panel rendering logic to app.js**

Add the following functions to `app.js`:

```javascript
// ---- SD Interface rendering ----

function updateSDPanel(io) {
  const sdSection = document.getElementById('sd-interface');
  const gpioSections = document.querySelectorAll('#io-panel .io-section:not(.sd-interface):not(#uart-decoder)');

  if (!io || io.mode !== 'sd') {
    // Show GPIO, hide SD
    sdSection.style.display = 'none';
    gpioSections.forEach(s => s.style.display = '');
    return;
  }

  // Show SD, hide GPIO
  sdSection.style.display = '';
  gpioSections.forEach(s => s.style.display = 'none');

  const sd = io.sd;
  if (!sd) return;

  // R30 decode
  const r30 = document.getElementById('sd-r30-decode');
  r30.innerHTML = `<span style="color:#ce93d8;">R30 Control:</span> ` +
    `<span class="sd-r30-field">[29:26] ch_sel=<b>${sd.ch_sel}</b></span>` +
    `<span class="sd-r30-field">[25] sd_en=<b style="color:#66bb6a;">${sd.sd_en?1:0}</b></span>` +
    `<span class="sd-r30-field">[24] snoop=<b>${sd.snoop?1:0}</b></span>` +
    `<span class="sd-r30-field">[23] data_sel=<b>${sd.data_sel?1:0}</b></span>`;

  // Channel cards
  const chContainer = document.getElementById('sd-channels');
  chContainer.innerHTML = '';
  (sd.channels || []).forEach(ch => {
    const card = document.createElement('div');
    card.className = 'sd-channel-card' + (ch.selected ? ' selected' : '');
    const accSelName = ['sinc3','sinc2','sinc1'][ch.config?.acc_sel || 0];
    const clkName = ['r31[16]','own','shared','rsvd'][ch.config?.clk_sel || 0];
    card.innerHTML = `
      <div class="sd-channel-title" style="color:${ch.selected?'#4fc3f7':'#888'};">
        CH ${ch.id} <span style="color:${ch.valid?'#66bb6a':'#666'};">●</span>
        ${ch.selected ? '<span style="font-size:9px;color:#4fc3f7;"> ★</span>' : ''}
      </div>
      <div class="sd-channel-config">CLK:${clkName} | OSR:${ch.osr} | ACC:${accSelName}</div>
      <div class="sd-acc-row">acc1: <span class="sd-acc-val">0x${(ch.acc1||0).toString(16).toUpperCase().padStart(4,'0')}</span></div>
      <div class="sd-acc-row">acc2: <span class="sd-acc-val">0x${(ch.acc2||0).toString(16).toUpperCase().padStart(4,'0')}</span></div>
      <div class="sd-acc-row">acc3: <span class="sd-acc-val">0x${(ch.acc3||0).toString(16).toUpperCase().padStart(6,'0')}</span></div>
      <div class="sd-status">
        <div style="color:${ch.valid?'#ffb74d':'#666'};">R31: ovf=${ch.ovf?1:0} valid=<b${ch.valid?' class="sd-valid"':''}>${ch.valid?1:0}</b></div>
        <div style="color:#fff;">data=0x${(ch['shadow_acc3']||0).toString(16).toUpperCase().padStart(7,'0')}</div>
      </div>
    `;
    chContainer.appendChild(card);
  });

  // Pattern generator
  const pgContainer = document.getElementById('sd-pattern-gen');
  pgContainer.innerHTML = '';
  (sd.modulators || []).forEach((mod, i) => {
    const card = document.createElement('div');
    card.className = 'sd-mod-card';
    let rows = `
      <div class="sd-mod-row"><span class="sd-mod-label">Signal:</span>
        <span class="sd-mod-value"><select data-ch="${i}" data-param="signal">
          <option value="dc"${mod.signal==='dc'?' selected':''}>DC</option>
          <option value="sine"${mod.signal==='sine'?' selected':''}>Sine</option>
        </select></span></div>
      <div class="sd-mod-row"><span class="sd-mod-label">SD Clock:</span>
        <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="sd_clock_mhz" value="${mod.sd_clock_mhz}" min="10" max="40" step="1"> MHz</span></div>
    `;
    if (mod.signal === 'dc') {
      rows += `<div class="sd-mod-row"><span class="sd-mod-label">DC Level:</span>
        <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="dc_level" value="${mod.dc_level}" min="-1" max="1" step="0.1"></span></div>`;
    } else {
      rows += `
        <div class="sd-mod-row"><span class="sd-mod-label">Amplitude:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="amplitude" value="${mod.amplitude}" min="0" max="1" step="0.1"></span></div>
        <div class="sd-mod-row"><span class="sd-mod-label">Period:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="period" value="${mod.period}" min="64" max="65536" step="64"></span></div>
        <div class="sd-mod-row"><span class="sd-mod-label">Phase:</span>
          <span class="sd-mod-value"><input type="number" data-ch="${i}" data-param="phase_deg" value="${mod.phase_deg}" min="0" max="360" step="1">°</span></div>
      `;
    }
    card.innerHTML = `<div style="color:#4fc3f7;font-size:9px;margin-bottom:4px;">CH ${i} Modulator</div>` + rows;
    chContainer.appendChild(card);
    // Intentional: append to pgContainer
    pgContainer.appendChild(card);
  });

  // Attach event listeners for pattern gen controls
  pgContainer.querySelectorAll('select, input').forEach(el => {
    el.addEventListener('change', (e) => {
      const ch = parseInt(e.target.dataset.ch);
      const param = e.target.dataset.param;
      let value = e.target.value;
      if (e.target.type === 'number') value = parseFloat(value);
      sendAction({ action: 'set_sd_modulator', core: targetCore, channel: ch, params: { [param]: value }});
    });
  });

  // GPIO switch link
  document.getElementById('sd-switch-gpio').onclick = (e) => {
    e.preventDefault();
    document.getElementById('sd-interface').style.display = 'none';
    gpioSections.forEach(s => s.style.display = '');
  };
}
```

- [ ] **Step 4: Call updateSDPanel from the state update handler**

In the WebSocket `onmessage` handler in `app.js`, after `updatePins(state.io)`:

```javascript
      updateSDPanel(state.io);
```

- [ ] **Step 5: Manual test — start simulator, load code that enables SD, verify UI switches**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m ui.server`
Open http://localhost:8080, load assembly: `MOV r30, 0x02000000` (sets sd_en=1).
Step → IO panel should switch from GPIO grid to SD interface.

- [ ] **Step 6: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add ui/static/index.html ui/static/app.js
git commit -m "feat(sd): add SD interface panel to web UI with live channel display"
```

---

## Task 10: End-to-End Validation

**Files:**
- Create: `tests/test_sd_e2e.py`

- [ ] **Step 1: Write end-to-end test simulating PRU SD workflow**

```python
# tests/test_sd_e2e.py
"""End-to-end test: PRU assembly configures SD, reads filtered data."""
import pytest
from simulator import Simulator


class TestSDEndToEnd:
    """Full workflow: configure registers, enable SD, run, read result."""

    def test_dc_input_produces_stable_output(self):
        """DC input through 2nd-order modulator → stable accumulator after OSR samples."""
        sim = Simulator()
        sd = sim.cores["pru0"].io_port.sd_filter

        # Configure: OSR=64, ACC_SEL=0 (sinc3), clock ratio 1:1 for speed
        sd.channels[0].osr = 64
        sd.modulators[0].signal = "dc"
        sd.modulators[0].dc_level = 0.5
        sd.modulators[0].sd_clock_mhz = 200.0  # 1:1 with PRU

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
        sd.process_r31_command(1 << 28)
        assert sd.channels[0].acc1 == 0

        # Run 3*OSR=48 more ticks
        for _ in range(48):
            sim.step("pru0")

        # Should have 3 valid assertions (at tick 16, 32, 48)
        # After 3rd OSR window, acc3 should have meaningful data
        r31 = sim.cores["pru0"].io_port.read_r31()
        data = r31 & 0x0FFFFFFF
        # DC=0.0 → ~50% ones → acc1≈8 per OSR, values accumulate
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
        sd.modulators[0].dc_level = 0.8
        sd.modulators[1].signal = "dc"
        sd.modulators[1].dc_level = -0.8

        sd.process_r30(0 << 26 | 1 << 25)  # ch0

        source = "\n".join(["NOP"] * 32)
        sim.load("pru0", source)
        for _ in range(16):
            sim.step("pru0")

        r31_ch0 = sim.cores["pru0"].io_port.read_r31()

        # Switch to ch1
        sd.process_r30(1 << 26 | 1 << 25)
        for _ in range(16):
            sim.step("pru0")

        r31_ch1 = sim.cores["pru0"].io_port.read_r31()

        # ch0 (DC=+0.8) should have higher accumulator than ch1 (DC=-0.8)
        data_ch0 = r31_ch0 & 0x0FFFFFFF
        data_ch1 = r31_ch1 & 0x0FFFFFFF
        assert data_ch0 > data_ch1
```

- [ ] **Step 2: Run end-to-end tests**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/test_sd_e2e.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Run full test suite**

Run: `cd C:/ti/industrial-automation-lab/Projects/pru_simulator && python -m pytest tests/ -v`
Expected: All tests PASS (no regressions)

- [ ] **Step 4: Commit**

```bash
cd C:/ti/industrial-automation-lab/Projects/pru_simulator
git add tests/test_sd_e2e.py
git commit -m "test(sd): add end-to-end validation tests for SD filter workflow"
```

---

## Summary

| Task | Component | Files | Tests |
|------|-----------|-------|-------|
| 1 | SD Modulator (pattern gen) | `pru_io/sd_modulator.py` | 10 |
| 2 | SD Channel (accumulators) | `pru_io/sd_channel.py` | 14 |
| 3 | SD Config Registers | `pru_io/sd_registers.py` | 14 |
| 4 | SD Filter (top-level) | `pru_io/sd_filter.py` | 12 |
| 5 | IOPort + Simulator wiring | `io_port.py`, `simulator.py`, `pru_core.py` | 4 |
| 6 | Memory bus register region | `simulator.py` | 3 |
| 7 | Fast Detect | `sd_channel.py` (modify) | 5 |
| 8 | WebSocket server | `ui/server.py`, `simulator.py` | regression |
| 9 | Frontend SD panel | `index.html`, `app.js` | manual |
| 10 | End-to-end validation | `tests/test_sd_e2e.py` | 3 |
