# tests/test_i2c_tca9538_firmware.py
"""Integration test: i2c_tca9538_running_led.asm against TCA9538Device.

Uses the `nominal_config` fixture (tests/conftest.py) to pin the core
clock to 200 MHz — this firmware's DELAY_COUNT is derived against that
specific clock, and a bare `Simulator()` would inherit whatever core
speed the dashboard's UI last wrote into the repo's memory.cfg.
"""
import pathlib
import pytest
from simulator import Simulator

SOURCE = pathlib.Path("source/i2c_tca9538_running_led.asm").read_text()


def _make_sim(nominal_config):
    sim = Simulator(nominal_config)
    errors = sim.load("pru0", SOURCE)
    assert errors == [], errors
    sim.i2c_attach("pru0", True, address=0x23)
    return sim


class TestInitSequence:
    def test_config_register_becomes_all_outputs(self, nominal_config):
        sim = _make_sim(nominal_config)
        # Generous step budget for one full init transaction at ~200-300
        # cycles/bit including subroutine-call overhead; the run loop
        # never reaches HALT so an upper bound is safe here.
        sim.step("pru0", count=20_000)
        state = sim.i2c_state("pru0")
        assert state["config_reg"] == 0x00


class TestRunLoop:
    # Step budgets below are measured, not guessed: single-stepping this
    # firmware against nominal_config (200 MHz) shows a steady-state
    # run_loop iteration (i2c_start + 3x i2c_write_byte + i2c_stop) takes
    # exactly 5843 instruction-steps (5845 on the pattern-wrap iteration,
    # two extra instructions for the 0x80->0x01 reset), and the firmware
    # first reaches the top of run_loop (having completed init) at step
    # 5842. The OUT_REG data byte for a given iteration is applied to the
    # device mid-iteration, before that iteration's STOP.
    def test_output_register_cycles_through_walking_bit_pattern(self, nominal_config):
        sim = _make_sim(nominal_config)
        sim.step("pru0", count=7_000)          # clear init; land at top of the
                                                # first steady-state run_loop
                                                # iteration (entry measured at
                                                # step 5842), well before that
                                                # iteration's OUT_REG write
                                                # lands (measured step 11182)
        seen = []
        for _ in range(10):
            sim.step("pru0", count=6_000)      # one output-port transaction
                                                # (measured 5843-5845 steps),
                                                # with ~150c headroom
            seen.append(sim.i2c_state("pru0")["output_reg"])
        assert seen[:8] == [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80]
        assert seen[8] == 0x01                   # wrapped

    def test_wrong_address_sets_dram0_error_flag(self, nominal_config):
        # The brief's original draft attached the mismatched-address
        # device *before* the very first byte of `start:`'s init
        # sequence. Empirically (single-stepped), that makes the ADDR_W
        # write in `start:` NACK immediately, and `start:` treats any
        # init-time NACK as fatal (`qbne init_fail, r20, 0` -> `halt`,
        # by design: "nothing useful can happen without CONFIG set").
        # The firmware halts at init_fail (~2017 steps) and never
        # reaches `run_loop`/`run_nack`, so DRAM0[0x0FFE] is never
        # written -- that scenario cannot exercise the soft-error path
        # at all, regardless of step budget.
        #
        # To exercise `run_nack` (the actual soft-error+continue path
        # this test is meant to cover) we instead: let init complete
        # against the correct address (0x23) so the firmware reaches
        # steady-state `run_loop`, then swap the attached device for one
        # at a mismatched address (0x24) -- simulating the slave going
        # unresponsive -- at a clean run_loop iteration boundary (step
        # 11685, the measured 2nd run_loop entry) so the swap doesn't
        # land mid-byte against the old device's protocol state.
        sim = _make_sim(nominal_config)             # address 0x23: init succeeds
        sim.step("pru0", count=11_685)               # clear init + 1 full run_loop
                                                       # iteration -> clean boundary
        sim.i2c_attach("pru0", True, address=0x24)   # device now mismatches -> NACK
        sim.step("pru0", count=8_000)                 # comfortably more than one
                                                       # NACKed iteration (measured
                                                       # ~2200-2500 steps to run_nack)
        err_byte = sim.memory_read(0x0FFE, 1)
        assert err_byte[0] == 1
