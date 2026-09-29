# pru_io/sd_modulator.py
"""2nd-order sigma-delta modulator pattern generator.

Produces a 1-bit PDM bitstream encoding a selectable input signal (DC, Sine,
or an audio Sweep).
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
        f_start: float = 20.0,
        f_stop: float = 20000.0,
        duration_s: float = 0.5,
        sweep_type: str = "log",
    ):
        self.signal = signal            # "dc", "sine" or "sweep"
        self.dc_level = dc_level        # -1.0 to +1.0
        self.amplitude = amplitude      # 0.0 to 1.0
        self.period = period            # samples per sine cycle
        self.phase_deg = phase_deg      # phase offset in degrees
        # Bit rate: the SD filter calls next_bit() sd_clock_mhz / pru_clock_mhz
        # times per PRU instruction. The sweep uses it as its time base.
        self.sd_clock_mhz = sd_clock_mhz
        # Sweep: one sweep f_start -> f_stop over duration_s, then silence.
        self.f_start = f_start          # Hz (> 0 for a log sweep)
        self.f_stop = f_stop            # Hz
        self.duration_s = duration_s    # s
        self.sweep_type = sweep_type    # "log" or "linear"

        # Internal modulator state
        self._integrator1: float = 0.0
        self._integrator2: float = 0.0
        self._sample_index: int = 0

    def _get_input(self) -> float:
        """Return the current input signal value in range [-1.0, +1.0]."""
        if self.period <= 0:
            raise ValueError("period must be > 0")

        if self.signal == "dc":
            return self.dc_level
        elif self.signal == "sine":
            # Sine wave
            phase_rad = self.phase_deg * math.pi / 180.0
            angle = 2.0 * math.pi * self._sample_index / self.period + phase_rad
            return self.amplitude * math.sin(angle)
        elif self.signal == "sweep":
            self._check_sweep()
            t = self.time_s()
            if t >= self.duration_s:
                return 0.0
            return self.amplitude * math.sin(self.sweep_phase(t))
        else:
            raise ValueError(f"Unknown signal type: {self.signal!r}. Expected 'dc', 'sine' or 'sweep'")

    def time_s(self) -> float:
        """Modulator time: bits produced so far divided by the bit rate."""
        return self._sample_index / (self.sd_clock_mhz * 1e6)

    def _check_sweep(self) -> None:
        if self.sweep_type not in ("log", "linear"):
            raise ValueError(f"sweep_type must be 'log' or 'linear', got {self.sweep_type!r}")
        if self.duration_s <= 0:
            raise ValueError(f"duration_s must be > 0, got {self.duration_s}")
        if self.f_start < 0 or (self.sweep_type == "log" and self.f_start <= 0):
            raise ValueError(f"f_start must be > 0 for a log sweep (>= 0 for linear), got {self.f_start}")
        if self.f_stop <= self.f_start:
            raise ValueError(f"f_stop must be > f_start, got {self.f_stop} <= {self.f_start}")

    def sweep_phase(self, t: float) -> float:
        """Sweep phase in radians at time t (closed form, phase-continuous)."""
        f1, f2, T = self.f_start, self.f_stop, self.duration_s
        if self.sweep_type == "linear":
            return 2.0 * math.pi * (f1 * t + (f2 - f1) * t * t / (2.0 * T))
        k = math.log(f2 / f1) / T
        return 2.0 * math.pi * f1 * math.expm1(k * t) / k

    def sweep_frequency(self, t: float) -> float:
        """Instantaneous sweep frequency in Hz at time t."""
        f1, f2, T = self.f_start, self.f_stop, self.duration_s
        if self.sweep_type == "linear":
            return f1 + (f2 - f1) * t / T
        return f1 * math.exp(math.log(f2 / f1) * t / T)

    def next_bit(self) -> int:
        """Advance the modulator by one clock tick and return the output bit (0 or 1)."""
        # Validate dc_level is in range
        if not -1.0 <= self.dc_level <= 1.0:
            raise ValueError(f"dc_level must be in range [-1.0, 1.0], got {self.dc_level}")
        # Validate amplitude is in range
        if not 0.0 <= self.amplitude <= 1.0:
            raise ValueError(f"amplitude must be in range [0.0, 1.0], got {self.amplitude}")

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

    def snapshot(self) -> dict:
        """Return a copy of all mutable execution state for step-back."""
        return {
            "_integrator1": self._integrator1,
            "_integrator2": self._integrator2,
            "_sample_index": self._sample_index,
        }

    def restore(self, snap: dict) -> None:
        """Restore execution state from a snapshot."""
        self._integrator1 = snap["_integrator1"]
        self._integrator2 = snap["_integrator2"]
        self._sample_index = snap["_sample_index"]
