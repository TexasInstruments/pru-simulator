"""Pin-coupled, standard-library PMSM plant and current modulators."""
from __future__ import annotations

import math
from collections import deque
from fractions import Fraction

from pru_io.device_model import DeviceModel, PUSH_PULL

_TWO_PI = 2.0 * math.pi
_SQRT3 = math.sqrt(3.0)
_MAX_INTEGRATION_DT = 5e-6
_HISTORY_PERIODS = 32
# Voltage vectors shorter than this fraction of the bus are treated as zero
# (disabled/neutral PWM) when deriving the commanded angle from the pins.
_MIN_VECTOR_FRACTION = 1e-3

#: Physics parameters, their defaults and whether a value must be positive
#: (True) or only non-negative (False).
PHYSICS_DEFAULTS = {
    "resistance_ohm": 0.5,
    "inductance_h": 0.001,
    "flux_linkage_vs": 0.05,
    "pole_pairs": 4,
    "inertia_kg_m2": 0.0005,
    "damping_nm_s": 0.003,
    "load_torque_nm": 0.0,
    "dc_bus_v": 48.0,
}
_PHYSICS_POSITIVE = {"resistance_ohm", "inductance_h", "inertia_kg_m2", "dc_bus_v"}
_PHYSICS_NON_NEGATIVE = {"flux_linkage_vs", "damping_nm_s"}

#: Field order of one per-PWM-period sample (see ``samples_since``).
SAMPLE_FIELDS = (
    "index", "time_s", "duty_a", "duty_b", "duty_c", "valpha_v", "vbeta_v",
    "commanded_angle_rad", "rotor_angle_rad", "rotor_speed_rpm",
    "commanded_speed_rpm", "ia", "ib", "ic", "id", "iq",
)
SAMPLE_CAPACITY = 1024


def _wrap_pi(angle: float) -> float:
    return (angle + math.pi) % _TWO_PI - math.pi


def _validate_physics(values: dict) -> dict:
    """Return normalized physics values or raise ValueError without side effects."""
    unknown = set(values) - PHYSICS_DEFAULTS.keys() - {"reference_angle_rad"}
    if unknown:
        raise ValueError(f"unknown foc_motor parameters: {sorted(unknown)}")
    result = {}
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number")
        if field == "pole_pairs":
            if not isinstance(value, int) and float(value) != int(value):
                raise ValueError("pole_pairs must be an integer from 1 to 64")
            if not 1 <= int(value) <= 64:
                raise ValueError("pole_pairs must be an integer from 1 to 64")
            result[field] = int(value)
            continue
        if field in _PHYSICS_POSITIVE and value <= 0:
            raise ValueError(f"{field} must be a positive number")
        if field in _PHYSICS_NON_NEGATIVE and value < 0:
            raise ValueError(f"{field} must be zero or positive")
        result[field] = float(value)
    return result


class FocMotorModel(DeviceModel):
    """Surface-mounted PMSM (Ld = Lq) driven by PRU PWM pins 0..2.

    Phase voltages come from the half-bridge pole states on the real GPIO
    bus. The model integrates the stator currents with back-EMF, the
    electromagnetic torque and the rotor mechanics, and encodes the phase-A/B
    currents as push-pull PDM on GPI pins 3 and 4. It never reads shared
    memory and the firmware never reads it: the PWM pins and the PDM pins are
    the only coupling. Everything else (duties, alpha/beta, commanded angle)
    is measured from the pins for the host to display.
    """

    name = "foc_motor"
    time_driven = True
    pru_output_mask = 0x7
    supported_cores = ("pru0",)

    def __init__(self, current_a_pin: int = 3, current_b_pin: int = 4,
                 resistance_ohm: float = 0.5, inductance_h: float = 0.001,
                 dc_bus_v: float = 48.0, current_scale_a: float = 20.0,
                 current_limit_a: float = 40.0,
                 core_clock_hz: float = 250_000_000,
                 current_a_clock_hz: float = 20_000_000,
                 current_b_clock_hz: float = 20_000_000,
                 name: str = "foc_motor",
                 flux_linkage_vs: float = 0.05, pole_pairs: int = 4,
                 inertia_kg_m2: float = 0.0005, damping_nm_s: float = 0.003,
                 load_torque_nm: float = 0.0) -> None:
        for pin_name, pin in (("current_a_pin", current_a_pin),
                              ("current_b_pin", current_b_pin)):
            if (isinstance(pin, bool) or not isinstance(pin, int)
                    or not 0 <= pin < 20):
                raise ValueError(f"{pin_name} must be an integer from 0 to 19")
        if current_a_pin == current_b_pin or {current_a_pin, current_b_pin} & {0, 1, 2}:
            raise ValueError("current output pins must be distinct from PWM pins 0, 1, and 2")
        for field, value in (("current_scale_a", current_scale_a),
                             ("current_limit_a", current_limit_a),
                             ("core_clock_hz", core_clock_hz),
                             ("current_a_clock_hz", current_a_clock_hz),
                             ("current_b_clock_hz", current_b_clock_hz)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be a positive number")
            if value <= 0:
                raise ValueError(f"{field} must be a positive number")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        physics = _validate_physics({
            "resistance_ohm": resistance_ohm, "inductance_h": inductance_h,
            "dc_bus_v": dc_bus_v, "flux_linkage_vs": flux_linkage_vs,
            "pole_pairs": pole_pairs, "inertia_kg_m2": inertia_kg_m2,
            "damping_nm_s": damping_nm_s, "load_torque_nm": load_torque_nm,
        })

        self.current_a_pin = current_a_pin
        self.current_b_pin = current_b_pin
        self.current_scale_a = float(current_scale_a)
        self.current_limit_a = float(current_limit_a)
        self.core_clock_hz = float(core_clock_hz)
        self._current_modulator_clock_hz = [
            Fraction(str(current_a_clock_hz)), Fraction(str(current_b_clock_hz))]
        self.current_a_clock_hz = float(self._current_modulator_clock_hz[0])
        self.current_b_clock_hz = float(self._current_modulator_clock_hz[1])
        self.name = name
        self.nets = {current_a_pin: PUSH_PULL, current_b_pin: PUSH_PULL}
        self._drive_mask = (1 << current_a_pin) | (1 << current_b_pin)
        self._max_pending_cycles = max(
            1, int(_MAX_INTEGRATION_DT * self.core_clock_hz))
        self._pdm_clock_ratios = [
            clock_hz / Fraction(str(core_clock_hz))
            for clock_hz in self._current_modulator_clock_hz
        ]
        self._sample_count = 0
        self._apply_physics(physics)
        self._reference_angle_rad = math.pi / 2.0
        self.reset()

    # -- physics parameters -------------------------------------------------

    def _apply_physics(self, values: dict) -> None:
        for field, value in values.items():
            if field != "reference_angle_rad":
                setattr(self, field, value)
        if "reference_angle_rad" in values:
            self._reference_angle_rad = values["reference_angle_rad"]
        self._refresh_pole_voltages()

    def _refresh_pole_voltages(self) -> None:
        """(alpha, beta) volts for each of the eight pole patterns."""
        table = []
        for pwm in range(8):
            poles = [(pwm >> phase) & 1 for phase in range(3)]
            common = sum(poles) / 3.0
            v = [(pole - common) * self.dc_bus_v for pole in poles]
            table.append((v[0], (v[1] - v[2]) / _SQRT3))
        self._pole_voltages = table

    def parameters(self) -> dict:
        """Current physics parameters (and the display-only reference angle)."""
        return {**{field: getattr(self, field) for field in PHYSICS_DEFAULTS},
                "reference_angle_rad": self._reference_angle_rad}

    def configure(self, parameters: dict) -> dict:
        """Change physics parameters live; applies from the next plant step.

        ``reference_angle_rad`` is display-only: the angle of the (Vd, Vq)
        reference vector, subtracted from the pin-measured voltage angle to
        report the commanded Park angle. It never enters the plant equations.
        """
        if not isinstance(parameters, dict) or not parameters:
            raise ValueError("parameters must be a non-empty object")
        if any(not isinstance(key, str) for key in parameters):
            raise ValueError("parameter names must be strings")
        values = _validate_physics(parameters)
        self._flush()
        self._apply_physics(values)
        return self.parameters()

    # -- current modulator clocks -------------------------------------------

    def reset(self) -> None:
        self.phase_currents_a = [0.0, 0.0, 0.0]
        self._theta_e = 0.0
        self._omega_m = 0.0
        self._torque_nm = 0.0
        self._pdm_accumulators = [0.0, 0.0]
        self._pdm_clock_accumulators = [0, 0]
        self._last_pwm = 0
        self._pwm_periods = 0
        self._cycles = 0
        self._pending = 0
        self._outputs = 0
        self._pole_rise = [0, 0, 0]
        self._on_cycles = [0, 0, 0]
        self._period_start = None
        self._period_cycles = 0
        self._duties = [0.0, 0.0, 0.0]
        self._alpha_beta_v = [0.0, 0.0]
        self._voltage_angle = self._reference_angle_rad
        self._cmd_unwrapped = 0.0
        self._cmd_history = deque(maxlen=_HISTORY_PERIODS + 1)
        self._samples = deque(maxlen=SAMPLE_CAPACITY)

    def set_current_modulator_clock_hz(self, output: int, clock_hz: float) -> None:
        """Set output 0/1's sampling clock to match its physical SD channel."""
        if isinstance(output, bool) or not isinstance(output, int) or output not in (0, 1):
            raise ValueError("current modulator output must be 0 or 1")
        if isinstance(clock_hz, bool) or not isinstance(clock_hz, (int, float)) or clock_hz < 0:
            raise ValueError("current modulator clock must be a non-negative number")
        rate = Fraction(str(clock_hz))
        self._set_current_modulator_clock(output, rate)

    def _set_current_modulator_clock(self, output: int, rate: Fraction) -> None:
        if self._current_modulator_clock_hz[output] == rate:
            return
        ratio = rate / Fraction(str(self.core_clock_hz))
        self._current_modulator_clock_hz[output] = rate
        if output == 0:
            self.current_a_clock_hz = float(rate)
        else:
            self.current_b_clock_hz = float(rate)
        self._pdm_clock_ratios[output] = ratio
        self._pdm_clock_accumulators[output] = 0

    def set_pin_clock_hz(self, pin: int, clock_hz: float) -> None:
        """Match a physical current output to the SD channel sampling its pin."""
        output = (self.current_a_pin, self.current_b_pin).index(pin)
        self.set_current_modulator_clock_hz(output, clock_hz)

    # -- plant ----------------------------------------------------------------

    def _flush(self) -> None:
        """Integrate the cycles accumulated under the current pole pattern."""
        cycles = self._pending
        if not cycles:
            return
        self._pending = 0
        self._integrate(cycles / self.core_clock_hz, self._last_pwm)

    def _integrate(self, dt: float, pwm: int) -> None:
        valpha, vbeta = self._pole_voltages[pwm]
        ia, ib, ic = self.phase_currents_a
        ialpha = (2.0 * ia - ib - ic) / 3.0
        ibeta = (ib - ic) / _SQRT3
        resistance, inductance = self.resistance_ohm, self.inductance_h
        flux, pole_pairs = self.flux_linkage_vs, self.pole_pairs
        sin_e, cos_e = math.sin(self._theta_e), math.cos(self._theta_e)
        emf = flux * pole_pairs * self._omega_m
        iq = -ialpha * sin_e + ibeta * cos_e
        self._torque_nm = 1.5 * pole_pairs * flux * iq
        limit = self.current_limit_a
        ialpha += (valpha - resistance * ialpha + emf * sin_e) * (dt / inductance)
        ibeta += (vbeta - resistance * ibeta - emf * cos_e) * (dt / inductance)
        ialpha = max(-limit, min(limit, ialpha))
        ibeta = max(-limit, min(limit, ibeta))
        self._omega_m += (self._torque_nm - self.damping_nm_s * self._omega_m
                          - self.load_torque_nm) * (dt / self.inertia_kg_m2)
        self._theta_e = (self._theta_e
                         + pole_pairs * self._omega_m * dt) % _TWO_PI
        half = -0.5 * ialpha
        self.phase_currents_a = [ialpha, half + 0.5 * _SQRT3 * ibeta,
                                 half - 0.5 * _SQRT3 * ibeta]

    def _pwm_edge(self, pwm: int) -> None:
        """Track pin transitions: on-times per phase and the PWM period."""
        now = self._cycles
        last = self._last_pwm
        for phase in range(3):
            bit = 1 << phase
            if pwm & bit and not last & bit:
                self._pole_rise[phase] = now
            elif last & bit and not pwm & bit:
                self._on_cycles[phase] += now - self._pole_rise[phase]
        if last and not pwm:
            self._pwm_periods += 1
        if pwm and not last:
            if self._period_start is not None:
                self._period_cycles = now - self._period_start
                self._finish_period(now)
            self._period_start = now
            self._on_cycles = [0, 0, 0]
        self._last_pwm = pwm

    def _finish_period(self, now: int) -> None:
        length = self._period_cycles
        duties = [on / length for on in self._on_cycles]
        mean = sum(duties) / 3.0
        valpha = (duties[0] - mean) * self.dc_bus_v
        vbeta = (duties[1] - duties[2]) * self.dc_bus_v / _SQRT3
        self._duties = duties
        self._alpha_beta_v = [valpha, vbeta]
        if math.hypot(valpha, vbeta) > _MIN_VECTOR_FRACTION * self.dc_bus_v:
            self._voltage_angle = math.atan2(vbeta, valpha) % _TWO_PI
        commanded = (self._voltage_angle - self._reference_angle_rad) % _TWO_PI
        if self._cmd_history:
            self._cmd_unwrapped += _wrap_pi(
                commanded - self._cmd_history[-1][2])
        else:
            self._cmd_unwrapped = commanded
        time_s = now / self.core_clock_hz
        self._cmd_history.append((time_s, self._cmd_unwrapped, commanded))
        ia, ib, ic = self.phase_currents_a
        id_a, iq_a = self._dq_currents()
        self._samples.append((
            self._sample_count, time_s, *duties, valpha, vbeta, commanded,
            self._theta_e, self._speed_rpm(), self._commanded_speed_rpm(),
            ia, ib, ic, id_a, iq_a))
        self._sample_count += 1

    def _dq_currents(self) -> tuple[float, float]:
        ia, ib, ic = self.phase_currents_a
        ialpha = (2.0 * ia - ib - ic) / 3.0
        ibeta = (ib - ic) / _SQRT3
        sin_e, cos_e = math.sin(self._theta_e), math.cos(self._theta_e)
        return (ialpha * cos_e + ibeta * sin_e,
                -ialpha * sin_e + ibeta * cos_e)

    def _speed_rpm(self) -> float:
        return self._omega_m * 60.0 / _TWO_PI

    def _commanded_speed_rpm(self) -> float:
        """Mechanical rpm from the commanded angle's rate over recent periods."""
        if len(self._cmd_history) < 2:
            return 0.0
        first, last = self._cmd_history[0], self._cmd_history[-1]
        elapsed = last[0] - first[0]
        if elapsed <= 0.0:
            return 0.0
        return (last[1] - first[1]) / elapsed / self.pole_pairs * 60.0 / _TWO_PI

    def samples_since(self, index: int = 0) -> dict:
        """Per-PWM-period samples with ``index >= index`` still in the ring.

        ``dropped`` counts requested samples that already left the bounded
        ring; ``next_index`` is the value to ask for next.
        """
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("index must be a non-negative integer")
        first = self._sample_count - len(self._samples)
        start = max(index, first)
        samples = [list(sample) for sample in
                   list(self._samples)[max(0, start - first):]]
        return {"fields": list(SAMPLE_FIELDS), "samples": samples,
                "next_index": self._sample_count,
                "dropped": max(0, first - index)}

    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        pwm = bus & 0x7
        if pwm != self._last_pwm:
            self._flush()
            self._pwm_edge(pwm)
        self._pending += 1
        if self._pending >= self._max_pending_cycles:
            self._flush()

        for output, phase in enumerate((0, 1)):
            ratio = self._pdm_clock_ratios[output]
            self._pdm_clock_accumulators[output] += ratio.numerator
            while self._pdm_clock_accumulators[output] >= ratio.denominator:
                self._pdm_clock_accumulators[output] -= ratio.denominator
                pin = (self.current_a_pin, self.current_b_pin)[output]
                current = self.phase_currents_a[phase]
                normalized = max(-1.0, min(1.0, current / self.current_scale_a))
                accumulator = self._pdm_accumulators[output] + normalized
                bit = int(accumulator >= 0.0)
                self._pdm_accumulators[output] = accumulator + (1.0 if not bit else -1.0)
                if bit:
                    self._outputs |= 1 << pin
                else:
                    self._outputs &= ~(1 << pin)
        self._cycles += 1
        return self._drive_mask, self._outputs

    def snapshot(self) -> dict:
        return {
            "phase_currents_a": list(self.phase_currents_a),
            "theta_e": self._theta_e,
            "omega_m": self._omega_m,
            "torque_nm": self._torque_nm,
            "parameters": self.parameters(),
            "pdm_accumulators": list(self._pdm_accumulators),
            "pdm_clock_accumulators": list(self._pdm_clock_accumulators),
            "current_modulator_clock_hz": [
                [clock_hz.numerator, clock_hz.denominator]
                for clock_hz in self._current_modulator_clock_hz],
            "last_pwm": self._last_pwm,
            "pwm_periods": self._pwm_periods,
            "cycles": self._cycles,
            "pending": self._pending,
            "outputs": self._outputs,
            "pole_rise": list(self._pole_rise),
            "on_cycles": list(self._on_cycles),
            "period_start": self._period_start,
            "period_cycles": self._period_cycles,
            "duties": list(self._duties),
            "alpha_beta_v": list(self._alpha_beta_v),
            "voltage_angle": self._voltage_angle,
            "cmd_unwrapped": self._cmd_unwrapped,
            "cmd_history": list(self._cmd_history),
            "samples": list(self._samples),
            "sample_count": self._sample_count,
        }

    def restore(self, snap: dict) -> None:
        self.phase_currents_a = list(snap["phase_currents_a"])
        self._theta_e = snap["theta_e"]
        self._omega_m = snap["omega_m"]
        self._torque_nm = snap["torque_nm"]
        self._apply_physics(snap["parameters"])
        self._pdm_accumulators = list(snap["pdm_accumulators"])
        saved_clocks = snap.get("current_modulator_clock_hz", [20_000_000] * 2)
        for output, clock in enumerate(saved_clocks):
            rate = Fraction(*clock) if isinstance(clock, (list, tuple)) else Fraction(str(clock))
            self._set_current_modulator_clock(output, rate)
        self._pdm_clock_accumulators = list(snap.get("pdm_clock_accumulators", [0, 0]))
        self._last_pwm = snap["last_pwm"]
        self._pwm_periods = snap["pwm_periods"]
        self._cycles = snap["cycles"]
        self._pending = snap["pending"]
        self._outputs = snap["outputs"]
        self._pole_rise = list(snap["pole_rise"])
        self._on_cycles = list(snap["on_cycles"])
        self._period_start = snap["period_start"]
        self._period_cycles = snap["period_cycles"]
        self._duties = list(snap["duties"])
        self._alpha_beta_v = list(snap["alpha_beta_v"])
        self._voltage_angle = snap["voltage_angle"]
        self._cmd_unwrapped = snap["cmd_unwrapped"]
        self._cmd_history = deque(snap["cmd_history"], maxlen=_HISTORY_PERIODS + 1)
        self._samples = deque(snap["samples"], maxlen=SAMPLE_CAPACITY)
        self._sample_count = snap["sample_count"]

    def get_state(self) -> dict:
        ia, ib, ic = self.phase_currents_a
        id_a, iq_a = self._dq_currents()
        commanded = (self._voltage_angle - self._reference_angle_rad) % _TWO_PI
        frequency = (self.core_clock_hz / self._period_cycles
                     if self._period_cycles else 0.0)
        return {
            "name": self.name,
            "model": "pmsm",
            "cycles": self._cycles,
            "time_s": self._cycles / self.core_clock_hz,
            "pwm_periods": self._pwm_periods,
            "pwm_frequency_hz": frequency,
            "phase_currents_a": list(self.phase_currents_a),
            "current_modulator_clock_hz": [self.current_a_clock_hz, self.current_b_clock_hz],
            "outputs": {
                "current_a_pin": (self._outputs >> self.current_a_pin) & 1,
                "current_b_pin": (self._outputs >> self.current_b_pin) & 1,
            },
            "parameters": self.parameters(),
            "rotor_angle_rad": self._theta_e,
            "rotor_speed_rpm": self._speed_rpm(),
            "voltage_angle_rad": self._voltage_angle,
            "commanded_angle_rad": commanded,
            "angle_error_rad": _wrap_pi(commanded - self._theta_e),
            "commanded_speed_rpm": self._commanded_speed_rpm(),
            "duty_cycles": list(self._duties),
            "alpha_beta_v": list(self._alpha_beta_v),
            "id_a": id_a,
            "iq_a": iq_a,
            "torque_nm": self._torque_nm,
            "sample_index": self._sample_count,
            "faults": 0,
        }
