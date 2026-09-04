"""Host-side control plane for the open-loop FOC simulator."""

from __future__ import annotations

import math
import struct

from pru_io import foc_abi as abi
from pru_io.foc_motor_model import FocMotorModel


_U32_MASK = 0xFFFF_FFFF


def _q24(value: float) -> int:
    scaled = int(round(float(value) * abi.Q_ONE))
    return max(-(1 << 31), min((1 << 31) - 1, scaled))


class FocRuntime:
    """Own the motor model, LUT, and staged control-block writes."""

    def __init__(self, sim):
        self.sim = sim
        self.model: FocMotorModel = sim.motor_attach()
        self.seed_sine_lut()
        self._initialize_control()

    def _initialize_control(self) -> None:
        # The simulator's full reset does not erase shared RAM. Reinitialize
        # this host-owned control plane so a reload cannot inherit enable=1 or
        # stale references from the previous firmware instance.
        self.sim.memory.write(
            abi.CONTROL_BASE,
            abi.pack_control(
                enable=0,
                requested_generation=0,
                pru_ack_generation=0,
                speed_ref_q24=0,
                id_ref_q24=0,
                iq_ref_q24=0,
                ramp_rate_q24=0,
            ),
        )

    def seed_sine_lut(self) -> None:
        """Seed the shared Q24 sine table used by the PRU LUT stage."""
        values = [
            int(round(math.sin(2.0 * math.pi * i / abi.SINE_LUT_ENTRIES) * abi.Q_ONE))
            & _U32_MASK
            for i in range(abi.SINE_LUT_ENTRIES)
        ]
        payload = struct.pack("<" + "I" * len(values), *values)
        self.sim.memory.write(abi.SINE_LUT_BASE, payload)

    def _control(self) -> dict:
        return abi.unpack_control(
            self.sim.memory_read(abi.CONTROL_BASE, abi.CONTROL_SIZE)
        )

    def _commit_control(self, **updates) -> dict:
        current = self._control()
        generation = current["requested_generation"] & _U32_MASK
        staged = dict(current)
        staged.update(updates)
        staged["requested_generation"] = generation
        self.sim.memory.write(abi.CONTROL_BASE, abi.pack_control(**staged))
        next_generation = (generation + 1) & _U32_MASK
        self.sim.memory.write(
            abi.CONTROL_BASE + abi.CONTROL_REQUESTED_GENERATION_OFF,
            struct.pack("<I", next_generation),
        )
        return self._control()

    def set_reference(
        self,
        speed: float | None = None,
        id: float | None = None,
        iq: float | None = None,
        ramp: float | None = None,
    ) -> dict:
        """Stage per-unit speed, d-current, q-current, and ramp references."""
        updates = {}
        if speed is not None:
            updates["speed_ref_q24"] = _q24(speed)
        if id is not None:
            updates["id_ref_q24"] = _q24(id)
        if iq is not None:
            updates["iq_ref_q24"] = _q24(iq)
        if ramp is not None:
            updates["ramp_rate_q24"] = _q24(max(0.0, ramp))
        if updates:
            return self._commit_control(**updates)
        return self._control()

    def _set_enable(self, enabled: bool) -> dict:
        current = self._control()
        if current["enable"] == int(enabled):
            return current
        self.sim.memory.write(
            abi.CONTROL_BASE + abi.CONTROL_ENABLE_OFF,
            struct.pack("<I", int(enabled)),
        )
        return self._control()

    def start(self) -> dict:
        """Enable the control block and start the IEP-clocked plant."""
        self._set_enable(True)
        self.model.start()
        return self.state()

    def stop(self) -> dict:
        """Disable the control block and stop the plant observer."""
        self._set_enable(False)
        self.model.stop()
        return self.state()

    def step(self, ticks: int = 1) -> dict:
        """Manually advance the plant and return the shared-memory snapshot."""
        self.model.step(ticks)
        return self.state()

    def _read_pwm(self) -> dict | None:
        base = abi.PWM_OUT_BASE
        return abi.read_coherent_pwm_out(
            lambda: int.from_bytes(self.sim.memory_read(base, 4), "little"),
            lambda: self.sim.memory_read(base + 4, abi.PWM_OUT_SIZE - 4),
        )

    def _read_feedback(self) -> dict | None:
        base = abi.MOTOR_FB_BASE
        return abi.read_coherent_motor_fb(
            lambda: int.from_bytes(self.sim.memory_read(base, 4), "little"),
            lambda: self.sim.memory_read(base + 4, abi.MOTOR_FB_SIZE - 4),
        )

    def state(self) -> dict:
        """Return control, PWM, feedback, and model snapshots."""
        return {
            "control": self._control(),
            "pwm": self._read_pwm(),
            "fb": self._read_feedback(),
            "model": self.model.state(),
        }
