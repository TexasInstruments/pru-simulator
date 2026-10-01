"""Pin-coupled, standard-library FOC motor plant and current modulators."""
from __future__ import annotations

from pru_io.device_model import DeviceModel, PUSH_PULL


class FocMotorModel(DeviceModel):
    """Three-phase RL motor load driven by PRU PWM pins 0..2.

    The two winding-current measurements are encoded as push-pull PDM on GPI
    pins 3 and 4.  The plant only observes the real GPIO bus; it has no shared
    memory mailbox or firmware feedback shortcut.
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
                 name: str = "foc_motor") -> None:
        for pin_name, pin in (("current_a_pin", current_a_pin),
                              ("current_b_pin", current_b_pin)):
            if (isinstance(pin, bool) or not isinstance(pin, int)
                    or not 0 <= pin < 20):
                raise ValueError(f"{pin_name} must be an integer from 0 to 19")
        if current_a_pin == current_b_pin or {current_a_pin, current_b_pin} & {0, 1, 2}:
            raise ValueError("current output pins must be distinct from PWM pins 0, 1, and 2")
        for field, value in (("resistance_ohm", resistance_ohm),
                             ("inductance_h", inductance_h),
                             ("dc_bus_v", dc_bus_v),
                             ("current_scale_a", current_scale_a),
                             ("current_limit_a", current_limit_a),
                             ("core_clock_hz", core_clock_hz)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be a positive number")
            if value <= 0:
                raise ValueError(f"{field} must be a positive number")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")

        self.current_a_pin = current_a_pin
        self.current_b_pin = current_b_pin
        self.resistance_ohm = float(resistance_ohm)
        self.inductance_h = float(inductance_h)
        self.dc_bus_v = float(dc_bus_v)
        self.current_scale_a = float(current_scale_a)
        self.current_limit_a = float(current_limit_a)
        self.core_clock_hz = float(core_clock_hz)
        self.name = name
        self.nets = {current_a_pin: PUSH_PULL, current_b_pin: PUSH_PULL}
        self._dt = 1.0 / self.core_clock_hz
        self.reset()

    def reset(self) -> None:
        self.phase_currents_a = [0.0, 0.0, 0.0]
        self._pdm_accumulators = [0.0, 0.0]
        self._last_pwm = 0
        self._pwm_periods = 0
        self._cycles = 0
        self._outputs = 0

    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        pwm = bus & 0x7
        if self._last_pwm and not pwm:
            self._pwm_periods += 1
        self._last_pwm = pwm

        common = ((pwm & 1) + ((pwm >> 1) & 1) + ((pwm >> 2) & 1)) / 3.0
        for phase in range(3):
            pole = (pwm >> phase) & 1
            phase_voltage = (pole - common) * self.dc_bus_v
            current = self.phase_currents_a[phase]
            current += (phase_voltage - self.resistance_ohm * current) * (
                self._dt / self.inductance_h)
            self.phase_currents_a[phase] = max(
                -self.current_limit_a, min(self.current_limit_a, current))

        outputs = 0
        for output, phase in enumerate((0, 1)):
            current = self.phase_currents_a[phase]
            normalized = max(-1.0, min(1.0, current / self.current_scale_a))
            accumulator = self._pdm_accumulators[output] + normalized
            bit = int(accumulator >= 0.0)
            self._pdm_accumulators[output] = accumulator + (1.0 if not bit else -1.0)
            pin = (self.current_a_pin, self.current_b_pin)[output]
            if bit:
                outputs |= 1 << pin
        self._outputs = outputs
        self._cycles += 1
        return sum(1 << pin for pin in self.nets), outputs

    def snapshot(self) -> dict:
        return {
            "phase_currents_a": list(self.phase_currents_a),
            "pdm_accumulators": list(self._pdm_accumulators),
            "last_pwm": self._last_pwm,
            "pwm_periods": self._pwm_periods,
            "cycles": self._cycles,
            "outputs": self._outputs,
        }

    def restore(self, snap: dict) -> None:
        self.phase_currents_a = list(snap["phase_currents_a"])
        self._pdm_accumulators = list(snap["pdm_accumulators"])
        self._last_pwm = snap["last_pwm"]
        self._pwm_periods = snap["pwm_periods"]
        self._cycles = snap["cycles"]
        self._outputs = snap["outputs"]

    def get_state(self) -> dict:
        return {
            "name": self.name,
            "model": "three_phase_rl",
            "cycles": self._cycles,
            "pwm_periods": self._pwm_periods,
            "phase_currents_a": list(self.phase_currents_a),
            "outputs": {
                "current_a_pin": (self._outputs >> self.current_a_pin) & 1,
                "current_b_pin": (self._outputs >> self.current_b_pin) & 1,
            },
            "faults": 0,
        }
