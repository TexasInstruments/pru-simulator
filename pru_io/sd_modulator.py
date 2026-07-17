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
        # NOTE: sd_clock_mhz is metadata for the consumer (e.g., UI, logging).
        # It does not control internal rate; internal rate is determined by next_bit() call frequency.
        self.sd_clock_mhz = sd_clock_mhz

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
        else:
            raise ValueError(f"Unknown signal type: {self.signal!r}. Expected 'dc' or 'sine'")

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
