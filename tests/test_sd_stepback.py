"""Tests for SD filter snapshot/restore (step-back support).

Covers SDChannel, SDModulator, and SigmaDeltaFilter snapshot/restore methods
that enable the server's step-back feature to correctly preserve SD state.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pru_io.sd_channel import SDChannel
from pru_io.sd_modulator import SDModulator
from pru_io.sd_filter import SigmaDeltaFilter
from pru_io.sd_registers import SDRegisters


# ── SDChannel ─────────────────────────────────────────────────────────────────

class TestSDChannelSnapshot:
    def test_snapshot_captures_live_accumulators(self):
        ch = SDChannel(osr=8)
        for _ in range(5):
            ch.tick(1)
        snap = ch.snapshot()
        assert snap["acc1"] == ch.acc1
        assert snap["acc2"] == ch.acc2
        assert snap["acc3"] == ch.acc3

    def test_snapshot_captures_shadow_registers(self):
        ch = SDChannel(osr=4)
        for _ in range(4):   # complete one OSR window → shadow latches
            ch.tick(1)
        snap = ch.snapshot()
        assert snap["shadow_acc1"] == ch.shadow_acc1
        assert snap["shadow_acc2"] == ch.shadow_acc2
        assert snap["shadow_acc3"] == ch.shadow_acc3

    def test_snapshot_captures_status_flags(self):
        ch = SDChannel(osr=4)
        for _ in range(4):
            ch.tick(1)
        snap = ch.snapshot()
        assert snap["valid"] == ch.valid
        assert snap["ovf"] == ch.ovf

    def test_snapshot_captures_sample_counter(self):
        ch = SDChannel(osr=8)
        for _ in range(3):
            ch.tick(1)
        snap = ch.snapshot()
        assert snap["_sample_count"] == 3

    def test_snapshot_captures_fd_state(self):
        ch = SDChannel(osr=8)
        ch.fd_en = True
        ch.fd_window_size = 1   # window = 8
        ch.fd_one_max_limit = 6
        ch.fd_zero_max_limit = 6
        for _ in range(8):
            ch.tick(1)
        snap = ch.snapshot()
        assert snap["fd_en"] == ch.fd_en
        assert snap["fd_window_size"] == ch.fd_window_size
        assert snap["_fd_shift_reg"] == ch._fd_shift_reg
        assert snap["_fd_sample_count"] == ch._fd_sample_count
        assert snap["fd_one_max"] == ch.fd_one_max

    def test_snapshot_is_independent_copy(self):
        """Mutating channel after snapshot must not affect the snap dict."""
        ch = SDChannel(osr=4)
        ch.tick(1)
        snap = ch.snapshot()
        original_acc1 = snap["acc1"]
        ch.tick(1)   # mutate
        assert snap["acc1"] == original_acc1

    def test_restore_reverts_accumulators(self):
        ch = SDChannel(osr=8)
        for _ in range(5):
            ch.tick(1)
        snap = ch.snapshot()
        acc1_before = ch.acc1

        for _ in range(10):  # advance further
            ch.tick(1)
        assert ch.acc1 != acc1_before   # verify state changed

        ch.restore(snap)
        assert ch.acc1 == acc1_before
        assert ch.acc2 == snap["acc2"]
        assert ch.acc3 == snap["acc3"]

    def test_restore_reverts_shadow_registers(self):
        ch = SDChannel(osr=4)
        for _ in range(4):  # complete first window
            ch.tick(1)
        snap = ch.snapshot()

        for _ in range(4):  # complete second window — shadows change
            ch.tick(0)

        ch.restore(snap)
        assert ch.shadow_acc1 == snap["shadow_acc1"]
        assert ch.shadow_acc3 == snap["shadow_acc3"]

    def test_restore_reverts_valid_flag(self):
        ch = SDChannel(osr=4)
        for _ in range(4):  # valid goes True
            ch.tick(1)
        snap = ch.snapshot()
        assert snap["valid"] is True

        ch.read_and_clear_valid()  # clear valid
        assert ch.valid is False

        ch.restore(snap)
        assert ch.valid is True

    def test_restore_reverts_sample_counter(self):
        ch = SDChannel(osr=8)
        for _ in range(3):
            ch.tick(1)
        snap = ch.snapshot()

        for _ in range(3):
            ch.tick(1)

        ch.restore(snap)
        assert ch._sample_count == snap["_sample_count"]

    def test_restore_reverts_osr(self):
        ch = SDChannel(osr=64)
        ch.osr = 128   # change OSR
        snap = ch.snapshot()
        ch.osr = 32
        ch.restore(snap)
        assert ch.osr == 128

    def test_restore_reverts_fd_state(self):
        ch = SDChannel(osr=8)
        ch.fd_en = True
        ch.fd_window_size = 1   # window=8
        ch.fd_one_max_limit = 6
        ch.fd_zero_max_limit = 6
        for _ in range(8):
            ch.tick(1)
        snap = ch.snapshot()

        for _ in range(8):
            ch.tick(0)

        ch.restore(snap)
        assert ch._fd_shift_reg == snap["_fd_shift_reg"]
        assert ch._fd_sample_count == snap["_fd_sample_count"]
        assert ch.fd_one_max == snap["fd_one_max"]


# ── SDModulator ───────────────────────────────────────────────────────────────

class TestSDModulatorSnapshot:
    def test_snapshot_captures_integrator_state(self):
        mod = SDModulator(dc_level=0.5)
        for _ in range(10):
            mod.next_bit()
        snap = mod.snapshot()
        assert snap["_integrator1"] == mod._integrator1
        assert snap["_integrator2"] == mod._integrator2
        assert snap["_sample_index"] == mod._sample_index

    def test_snapshot_is_independent_copy(self):
        mod = SDModulator(dc_level=0.3)
        mod.next_bit()
        snap = mod.snapshot()
        original_int1 = snap["_integrator1"]
        mod.next_bit()   # mutate
        assert snap["_integrator1"] == original_int1

    def test_restore_reverts_integrator_state(self):
        mod = SDModulator(dc_level=0.5)
        for _ in range(10):
            mod.next_bit()
        snap = mod.snapshot()
        int1_before = mod._integrator1

        for _ in range(5):
            mod.next_bit()
        assert mod._integrator1 != int1_before

        mod.restore(snap)
        assert mod._integrator1 == int1_before
        assert mod._integrator2 == snap["_integrator2"]
        assert mod._sample_index == snap["_sample_index"]

    def test_restore_makes_output_deterministic(self):
        """After restore, next_bit() must produce the same sequence as before."""
        mod = SDModulator(dc_level=0.6)
        for _ in range(20):
            mod.next_bit()
        snap = mod.snapshot()

        bits_original = [mod.next_bit() for _ in range(8)]

        mod.restore(snap)
        bits_after_restore = [mod.next_bit() for _ in range(8)]

        assert bits_original == bits_after_restore


# ── SigmaDeltaFilter ──────────────────────────────────────────────────────────

class TestSigmaDeltaFilterSnapshot:
    def _make_filter(self):
        sd = SigmaDeltaFilter(pru_clock_mhz=200.0)
        sd.registers = SDRegisters()
        return sd

    def test_snapshot_captures_r30_control_fields(self):
        sd = self._make_filter()
        sd.ch_sel = 2
        sd.sd_en = True
        sd.snoop = False
        sd.data_sel = True
        snap = sd.snapshot()
        assert snap["ch_sel"] == 2
        assert snap["sd_en"] is True
        assert snap["data_sel"] is True

    def test_snapshot_captures_clock_accumulators(self):
        sd = self._make_filter()
        for _ in range(3):   # partial tick advances fractional accumulator
            sd.tick()
        snap = sd.snapshot()
        assert len(snap["clock_acc"]) == 3
        assert snap["clock_acc"] == list(sd._clock_acc)

    def test_snapshot_captures_channel_state(self):
        sd = self._make_filter()
        for _ in range(100):
            sd.tick()
        snap = sd.snapshot()
        assert len(snap["channels"]) == 3
        assert snap["channels"][0]["acc3"] == sd.channels[0].acc3

    def test_snapshot_captures_modulator_state(self):
        sd = self._make_filter()
        for _ in range(50):
            sd.tick()
        snap = sd.snapshot()
        assert len(snap["modulators"]) == 3
        assert snap["modulators"][0]["_sample_index"] == sd.modulators[0]._sample_index

    def test_snapshot_captures_registers(self):
        sd = self._make_filter()
        # Write OSR=64 to channel 0 sample-size register
        sd.registers.write(0x2604C, (63).to_bytes(4, 'little'))
        snap = sd.snapshot()
        assert snap["regs"] == bytes(sd.registers._data)
        assert len(snap["regs"]) == 0x20   # 32 bytes

    def test_snapshot_none_registers_is_handled(self):
        sd = SigmaDeltaFilter(pru_clock_mhz=200.0)
        # registers not wired
        snap = sd.snapshot()
        assert snap["regs"] is None

    def test_restore_reverts_channel_accumulators(self):
        sd = self._make_filter()
        for _ in range(200):
            sd.tick()
        snap = sd.snapshot()
        acc3_before = sd.channels[0].acc3

        for _ in range(100):
            sd.tick()
        assert sd.channels[0].acc3 != acc3_before

        sd.restore(snap)
        assert sd.channels[0].acc3 == acc3_before

    def test_restore_reverts_r30_fields(self):
        sd = self._make_filter()
        sd.ch_sel = 1
        sd.sd_en = True
        snap = sd.snapshot()

        sd.ch_sel = 2
        sd.sd_en = False
        sd.restore(snap)

        assert sd.ch_sel == 1
        assert sd.sd_en is True

    def test_restore_reverts_registers(self):
        sd = self._make_filter()
        # Write OSR=64 to channel 0
        sd.registers.write(0x2604C, (63).to_bytes(4, 'little'))
        snap = sd.snapshot()

        # Change to OSR=32
        sd.registers.write(0x2604C, (31).to_bytes(4, 'little'))
        assert sd.registers.get_osr(0) == 32

        sd.restore(snap)
        assert sd.registers.get_osr(0) == 64

    def test_restore_reverts_clock_accumulators(self):
        sd = self._make_filter()
        for _ in range(7):
            sd.tick()
        snap = sd.snapshot()
        clock_acc_before = list(sd._clock_acc)

        for _ in range(3):
            sd.tick()

        sd.restore(snap)
        assert sd._clock_acc == clock_acc_before

    def test_restore_makes_tick_deterministic(self):
        """After restore, the same sequence of ticks produces identical acc3 values."""
        sd = self._make_filter()
        for _ in range(640):   # run a while to reach steady state
            sd.tick()
        snap = sd.snapshot()

        # Record next 64 channel-0 acc3 values
        acc3_original = []
        for _ in range(640):
            sd.tick()
            acc3_original.append(sd.channels[0].acc3)

        sd.restore(snap)

        acc3_after_restore = []
        for _ in range(640):
            sd.tick()
            acc3_after_restore.append(sd.channels[0].acc3)

        assert acc3_original == acc3_after_restore
