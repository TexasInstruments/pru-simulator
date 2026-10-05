"""The SSI reader and encoder model at the 300 MHz core clock."""
from fractions import Fraction

import pytest

from pru_io.ssi_runtime import SSIRuntime
from simulator import Simulator

# (preset, position, error, clock_delay_loops). The reader's bit period is about
# four cycles per delay loop plus eight, so 300 MHz needs at least 75 cycles for
# the 4 MHz RM08 limit and 150 cycles for the 2 MHz SICK limit.
CASES = [
    ("RM08_12BIT_4MHZ", 0xABC, 0, 20),
    ("AFS_AFM60_MULTITURN_30BIT", 0x2ABCDEF1, 5, 40),
    ("AHS_AHM36_SINGLETURN", 0x1555, 1, 40),
]


@pytest.fixture
def config_300mhz(sim_config):
    return sim_config(pru_clock_mhz=300, pru1_clock_mhz=300)


@pytest.mark.parametrize(("preset", "position", "error", "delay"), CASES)
def test_reader_reads_the_encoder_model_at_300mhz(
        config_300mhz, preset, position, error, delay):
    sim = Simulator(config_300mhz)

    with SSIRuntime(sim, preset=preset, position=position,
                    error_value=error) as runtime:
        assert runtime.encoder.core_clock_hz == Fraction(300_000_000)
        runtime.load(clock_delay_loops=delay)
        result = runtime.run_until_frames(1, max_steps=60_000)
        runtime.step(10_000)  # let the encoder's Tm guard expire
        mailbox = runtime.mailbox()
        faults = runtime.encoder.faults()

    assert result["reached"] is True
    assert (mailbox["position"], mailbox["error"]) == (position, error)
    assert mailbox["status"] == 0
    assert faults == []


def test_standard_preset_clock_limit_is_enforced_at_300mhz(config_300mhz):
    sim = Simulator(config_300mhz)

    with SSIRuntime(sim, preset="RM08_12BIT_4MHZ", position=0xABC) as runtime:
        runtime.load(clock_delay_loops=0)  # about 8 cycles per bit, far above 4 MHz
        runtime.run_until_frames(1, max_steps=60_000)
        faults = runtime.encoder.faults()

    assert any("f_max" in fault for fault in faults)
