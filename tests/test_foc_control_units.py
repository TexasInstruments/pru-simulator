"""Host units and control-block staging for the FOC firmware."""
import math

import pytest

from pru_io import foc_control as control
from pru_io import foc_control_abi as abi
from simulator import Simulator


def test_speed_units_use_pole_pairs_and_the_one_kilohertz_base():
    # 1500 rpm * 4 pole pairs / 60 = 100 Hz electrical = 0.1 pu.
    assert control.speed_rpm_to_q28(1500, 4) == round(0.1 * 2**28)
    assert control.speed_q28_to_rpm(round(0.1 * 2**28), 4) == pytest.approx(1500)
    assert control.speed_rpm_to_q28(-1500, 2) == -round(0.05 * 2**28)
    # 1000 rpm/s with 4 pole pairs: 0.0667 pu/s / 16 kHz per update.
    assert control.ramp_rpm_s_to_q28(1000, 4) == round(0.1 / 1.5 / 16000 * 2**28)
    assert control.voltage_pu_to_q15(0.25) == 8192


@pytest.mark.parametrize("call", [
    lambda: control.speed_rpm_to_q28(float("nan"), 4),
    lambda: control.speed_rpm_to_q28("100", 4),
    lambda: control.speed_rpm_to_q28(100, 0),
    lambda: control.speed_rpm_to_q28(100, True),
    lambda: control.ramp_rpm_s_to_q28(-1, 4),
    lambda: control.voltage_pu_to_q15(math.inf),
])
def test_unit_conversions_reject_invalid_values(call):
    with pytest.raises(ValueError):
        call()


def test_stage_control_bumps_generation_and_keeps_pru_owned_words():
    sim = Simulator()
    first = control.stage_control(sim.memory, enable=1, vq_ref_q15=1000)
    assert (first["requested_generation"], first["enable"], first["vq_ref_q15"]) == (1, 1, 1000)
    sim.memory.write(abi.CONTROL_ADDRESS + abi.ACK_GENERATION_OFFSET,
                     (1).to_bytes(4, "little") + (abi.STATUS_RUNNING).to_bytes(4, "little"))
    second = control.stage_control(sim.memory, vd_ref_q15=-5)
    assert second["requested_generation"] == 2
    assert (second["enable"], second["vq_ref_q15"], second["vd_ref_q15"]) == (1, 1000, -5)
    assert (second["ack_generation"], second["status"]) == (1, abi.STATUS_RUNNING)


def test_rejected_stage_control_leaves_the_block_untouched():
    sim = Simulator()
    control.stage_control(sim.memory, enable=1)
    before = sim.memory.read(abi.CONTROL_ADDRESS, abi.CONFIG_SIZE)
    with pytest.raises(ValueError):
        control.stage_control(sim.memory, vd_ref_q15=40000)
    with pytest.raises(ValueError, match="unknown"):
        control.stage_control(sim.memory, bogus=1)
    assert sim.memory.read(abi.CONTROL_ADDRESS, abi.CONFIG_SIZE) == before
