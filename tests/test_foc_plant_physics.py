"""PMSM plant physics against independent numerical references.

The plant is driven through its pins only, with synthetic PWM from a scaled
core clock (100 cycles per 16 kHz period) so that a few hundred milliseconds
of motor time stays fast. Firmware-in-the-loop coverage is in
``test_foc_firmware.py`` and ``test_foc_motor_model.py``.
"""
import math

import pytest

from pru_io.foc_motor_model import FocMotorModel, SAMPLE_CAPACITY, SAMPLE_FIELDS
from tests import foc_reference as ref

PWM_HZ = 16_000
CYCLES = 100
CORE_HZ = PWM_HZ * CYCLES
PERIOD_S = 1.0 / PWM_HZ
DEFAULTS = {"r": 0.5, "l": 1e-3, "flux": 0.05, "p": 4, "j": 5e-4, "b": 3e-3,
            "load": 0.0, "vdc": 48.0}


def _model(**options):
    return FocMotorModel(core_clock_hz=CORE_HZ, current_a_clock_hz=CORE_HZ / 5,
                         current_b_clock_hz=CORE_HZ / 5, **options)


class Drive:
    """Open-loop V/f generator: ideal Park + SVGEN duties as PWM pin waveforms."""

    def __init__(self, model, speed_rpm, ramp_rpm_s, vq, vd=0.0, pole_pairs=4):
        self.model, self.target, self.ramp = model, speed_rpm, ramp_rpm_s
        self.vd, self.vq, self.p = vd, vq, pole_pairs
        self.speed = 0.0
        self.angle = 0.0   # electrical turns
        self.cycle = 0
        self.duties = []   # quantized duties actually applied, per period

    def periods(self, count):
        for _ in range(count):
            step = self.ramp * PERIOD_S
            self.speed = min(self.target, self.speed + step) if self.target >= 0 else \
                max(self.target, self.speed - step)
            self.angle = (self.angle + self.speed * self.p / 60.0 * PERIOD_S) % 1.0
            duties = ref.svgen_duties(*ref.inverse_park(self.angle, self.vd, self.vq))
            on = [round(duty * CYCLES) for duty in duties]
            self.duties.append(tuple(count / CYCLES for count in on))
            for cycle in range(CYCLES):
                bus = sum(1 << pin for pin in range(3) if cycle < on[pin])
                self.model.tick(self.cycle, bus)
                self.cycle += 1


def _reference(drive, params=DEFAULTS):
    return ref.pmsm_rk4(drive.duties, params, PERIOD_S)


def test_pmsm_open_loop_vf_matches_independent_rk4_reference():
    model = _model()
    drive = Drive(model, speed_rpm=300, ramp_rpm_s=1500, vq=0.15)
    drive.periods(int(0.4 * PWM_HZ))
    speed, theta, _, _ = _reference(drive)[-1]
    state = model.get_state()
    assert state["rotor_speed_rpm"] == pytest.approx(speed, abs=3.0)
    error = math.remainder(state["rotor_angle_rad"] - theta, 2 * math.pi)
    assert error == pytest.approx(0.0, abs=0.1)
    # Open-loop V/f locks the rotor to the commanded synchronous speed.
    assert state["rotor_speed_rpm"] == pytest.approx(300, rel=0.05)
    # Duties are quantized to 1% on this scaled clock, so the pin-derived
    # speed is only good to a few percent here.
    assert state["commanded_speed_rpm"] == pytest.approx(300, rel=0.1)
    assert abs(state["angle_error_rad"]) < math.radians(60)


def test_commanded_angle_and_duties_are_measured_from_the_pins():
    model = _model()
    drive = Drive(model, speed_rpm=1500, ramp_rpm_s=10_000_000, vq=0.2, vd=0.0)
    drive.periods(300)
    state = model.get_state()
    # Measured duties are the on-time fractions of the last completed period.
    assert state["duty_cycles"] == pytest.approx(drive.duties[-2], abs=1e-9)
    mean = sum(drive.duties[-2]) / 3
    assert state["alpha_beta_v"][0] == pytest.approx((drive.duties[-2][0] - mean) * 48.0)
    # Reference angle defaults to the Vq axis: commanded angle = Park angle.
    expected = (2 * math.pi * _angle_after(drive, 298)) % (2 * math.pi)
    assert math.remainder(state["commanded_angle_rad"] - expected, 2 * math.pi) == \
        pytest.approx(0.0, abs=0.03)
    assert state["pwm_frequency_hz"] == pytest.approx(PWM_HZ)
    assert state["commanded_speed_rpm"] == pytest.approx(1500, rel=0.1)


def _angle_after(drive, period_index):
    """Park angle (turns) used for period ``period_index`` of a ``Drive`` run."""
    angle, speed = 0.0, 0.0
    for _ in range(period_index + 1):
        speed = min(drive.target, speed + drive.ramp * PERIOD_S)
        angle = (angle + speed * drive.p / 60.0 * PERIOD_S) % 1.0
    return angle


def test_load_torque_shifts_angle_error_and_torque_current():
    model = _model()
    drive = Drive(model, speed_rpm=300, ramp_rpm_s=3000, vq=0.15)
    drive.periods(int(0.3 * PWM_HZ))
    baseline = model.get_state()
    model.configure({"load_torque_nm": 0.3})
    drive.periods(int(0.3 * PWM_HZ))
    loaded = model.get_state()

    # Steady synchronous torque: Te = 1.5 p flux iq = load + B*omega.
    delta_iq = loaded["iq_a"] - baseline["iq_a"]
    assert delta_iq == pytest.approx(0.3 / (1.5 * 4 * 0.05), rel=0.15)
    assert loaded["rotor_speed_rpm"] == pytest.approx(300, rel=0.05)
    # The rotor falls back by the load angle: more lag behind the command.
    assert loaded["angle_error_rad"] > baseline["angle_error_rad"] + 0.05


def test_runtime_parameter_change_applies_from_the_next_step():
    a, b = _model(), _model()
    drive_a = Drive(a, speed_rpm=600, ramp_rpm_s=60_000, vq=0.2)
    drive_b = Drive(b, speed_rpm=600, ramp_rpm_s=60_000, vq=0.2)
    drive_a.periods(40)
    drive_b.periods(40)
    assert a.get_state() == b.get_state()
    speed_before = a.get_state()["rotor_speed_rpm"]
    b.configure({"inertia_kg_m2": 0.001})
    assert b.get_state()["parameters"]["inertia_kg_m2"] == 0.001
    drive_a.periods(20)
    drive_b.periods(20)
    gain_a = a.get_state()["rotor_speed_rpm"] - speed_before
    gain_b = b.get_state()["rotor_speed_rpm"] - speed_before
    assert gain_a > 0 and gain_b > 0
    assert gain_b < 0.7 * gain_a   # doubled inertia: clearly slower response


@pytest.mark.parametrize("field,value,check", [
    ("pole_pairs", 2, lambda s: s["parameters"]["pole_pairs"] == 2),
    ("dc_bus_v", 24.0, lambda s: s["parameters"]["dc_bus_v"] == 24.0),
    ("flux_linkage_vs", 0.1, lambda s: s["parameters"]["flux_linkage_vs"] == 0.1),
    ("resistance_ohm", 1.0, lambda s: s["parameters"]["resistance_ohm"] == 1.0),
    ("inductance_h", 0.002, lambda s: s["parameters"]["inductance_h"] == 0.002),
    ("damping_nm_s", 0.01, lambda s: s["parameters"]["damping_nm_s"] == 0.01),
])
def test_every_physics_parameter_is_changeable_at_runtime(field, value, check):
    model = _model()
    assert model.configure({field: value})[field] == value
    assert check(model.get_state())


def test_dc_bus_change_scales_the_applied_voltage():
    model = _model()
    model.configure({"flux_linkage_vs": 0.0, "dc_bus_v": 24.0})
    drive = Drive(model, speed_rpm=0, ramp_rpm_s=0, vq=0.0, vd=0.3)
    drive.periods(5)
    assert model.get_state()["alpha_beta_v"][0] == pytest.approx(
        (drive.duties[-2][0] - sum(drive.duties[-2]) / 3) * 24.0)


def test_zero_flux_linkage_is_the_previous_pure_rl_load():
    model = FocMotorModel(core_clock_hz=1_000_000, flux_linkage_vs=0.0,
                          current_limit_a=500.0)
    # Phase A pole high, B/C low: v_a = 2/3 Vdc, v_b = v_c = -1/3 Vdc.
    for cycle in range(3000):
        model.tick(cycle, 0b001)
    tau = 1e-3 / 0.5
    expected = (2 / 3 * 48.0) / 0.5 * (1 - math.exp(-3000e-6 / tau))
    ia, ib, ic = model.phase_currents_a
    assert ia == pytest.approx(expected, rel=0.02)
    assert ib == pytest.approx(-expected / 2, rel=0.02)
    assert ic == pytest.approx(-expected / 2, rel=0.02)
    assert model.get_state()["torque_nm"] == 0.0
    # Rotor never moves without flux: the load is purely electrical.
    assert model.get_state()["rotor_speed_rpm"] == 0.0


@pytest.mark.parametrize("changes", [
    {"resistance_ohm": 0}, {"resistance_ohm": -1.0}, {"inductance_h": 0.0},
    {"flux_linkage_vs": -0.01}, {"pole_pairs": 0}, {"pole_pairs": 1.5},
    {"pole_pairs": 65}, {"inertia_kg_m2": 0}, {"damping_nm_s": -1},
    {"dc_bus_v": 0}, {"load_torque_nm": float("nan")},
    {"dc_bus_v": float("inf")}, {"resistance_ohm": True}, {"dc_bus_v": "48"},
    {"bogus": 1}, {"resistance_ohm": 2.0, "inductance_h": -1.0}, {},
])
def test_invalid_parameters_are_rejected_without_mutating_the_plant(changes):
    model = _model()
    Drive(model, speed_rpm=100, ramp_rpm_s=5000, vq=0.1).periods(30)
    before = model.snapshot()
    with pytest.raises(ValueError):
        model.configure(changes)
    assert model.snapshot() == before


def test_physics_options_are_validated_at_construction():
    with pytest.raises(ValueError, match="pole_pairs"):
        FocMotorModel(pole_pairs=0)
    with pytest.raises(ValueError, match="inertia_kg_m2"):
        FocMotorModel(inertia_kg_m2=-1.0)
    assert FocMotorModel(flux_linkage_vs=0.0).parameters()["flux_linkage_vs"] == 0.0


def test_snapshot_restore_round_trip_is_exact_including_samples_and_parameters():
    model = _model()
    drive = Drive(model, speed_rpm=200, ramp_rpm_s=4000, vq=0.15)
    drive.periods(120)
    snapshot = model.snapshot()
    model.configure({"load_torque_nm": 0.1, "pole_pairs": 3})
    drive.periods(40)
    state_after = model.get_state()
    samples_after = model.samples_since(0)

    other = _model()
    other.restore(snapshot)
    assert other.snapshot() == snapshot
    assert other.parameters()["pole_pairs"] == 4
    other.configure({"load_torque_nm": 0.1, "pole_pairs": 3})
    # Replay the same last 40 periods from the restored plant.
    clone = Drive(other, speed_rpm=200, ramp_rpm_s=4000, vq=0.15)
    clone.speed, clone.angle = _speed_angle_after(drive, 120)
    clone.periods(40)
    assert other.get_state() == state_after
    assert other.samples_since(0) == samples_after


def _speed_angle_after(drive, periods):
    angle, speed = 0.0, 0.0
    for _ in range(periods):
        speed = min(drive.target, speed + drive.ramp * PERIOD_S)
        angle = (angle + speed * drive.p / 60.0 * PERIOD_S) % 1.0
    return speed, angle


def test_sample_ring_is_bounded_monotonic_and_incremental():
    model = _model()
    drive = Drive(model, speed_rpm=100, ramp_rpm_s=1000, vq=0.1)
    drive.periods(30)
    first = model.samples_since(0)
    assert first["fields"] == list(SAMPLE_FIELDS)
    indexes = [sample[0] for sample in first["samples"]]
    assert indexes == list(range(len(indexes))) and first["dropped"] == 0
    assert first["next_index"] == len(indexes) >= 28
    drive.periods(10)
    later = model.samples_since(first["next_index"])
    assert [sample[0] for sample in later["samples"]] == list(
        range(first["next_index"], later["next_index"]))
    assert model.samples_since(later["next_index"])["samples"] == []

    drive.periods(SAMPLE_CAPACITY + 200)
    full = model.samples_since(0)
    assert len(full["samples"]) == SAMPLE_CAPACITY
    assert full["dropped"] == full["next_index"] - SAMPLE_CAPACITY > 0
    assert full["samples"][0][0] == full["dropped"]
    with pytest.raises(ValueError):
        model.samples_since(-1)


def test_pdm_outputs_track_phase_a_and_b_currents():
    model = FocMotorModel(core_clock_hz=250_000_000)
    model.phase_currents_a = [8.0, -3.0, -5.0]
    bits = [0, 0]
    ticks = 5000
    for cycle in range(ticks):
        _, values = model.tick(cycle, 0)
        bits[0] += (values >> 3) & 1
        bits[1] += (values >> 4) & 1
    for count, current in zip(bits, (8.0, -3.0)):
        # The held PDM level's duty encodes i = (2 * density - 1) * 20 A.
        assert (2 * count / ticks - 1) * 20.0 == pytest.approx(current, abs=0.5)
