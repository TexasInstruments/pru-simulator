"""Deterministic surface-PMSM plant for the open-loop FOC simulator."""

from __future__ import annotations

import math
import struct

from references.gan_shunt__current import (
    KE,
    LS,
    POLE_PAIRS,
    RS,
    V_DC,
)
from pru_io import foc_abi as abi


# Mechanical parameters are intentionally modest so a 48 V, 4-pole-pair motor
# reaches the commanded open-loop speed within a short simulator run.
B = 0.0005
J = 0.0001
Tload = 0.0
TLOAD = Tload

_MAX_INTEGRATION_DT = 5e-6
_U32_MASK = 0xFFFF_FFFF
_U64_MASK = 0xFFFF_FFFF_FFFF_FFFF


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clarke_from_phase(va: float, vb: float, vc: float) -> tuple[float, float]:
    common = (va + vb + vc) / 3.0
    va -= common
    vb -= common
    vc -= common
    return va, (vb - vc) / math.sqrt(3.0)


def _q24(value: float) -> int:
    scaled = int(round(value * abi.Q_ONE))
    return max(-(1 << 31), min((1 << 31) - 1, scaled))


class FocMotorModel:
    """Integrate a three-phase PMSM from the firmware's PWM output block."""

    def __init__(self, sim):
        self.sim = sim
        self._running = False
        self._observer_registered = False
        self._last_timestamp = self._now()
        self._ialpha = 0.0
        self._ibeta = 0.0
        self._theta_mech = 0.0
        self._omega_mech = 0.0
        self._last_pwm = self._zero_pwm()
        self.sim.add_hard_reset_hook(self.reset)
        self._publish_feedback(self._last_timestamp, reset=True)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def observer_registered(self) -> bool:
        return self._observer_registered

    def _iep(self):
        return self.sim.iep

    def _now(self) -> int:
        return int(self._iep().count) & _U64_MASK

    def _tick_hz(self) -> float:
        iep = self._iep()
        if iep.enabled and iep.default_increment:
            return float(iep.active_clock_hz) * iep.default_increment
        return float(abi.IEP_TICK_HZ)

    @staticmethod
    def _zero_pwm() -> dict[str, int]:
        return {
            "seq": 0,
            "ta_q24": abi.Q_ONE // 2,
            "tb_q24": abi.Q_ONE // 2,
            "tc_q24": abi.Q_ONE // 2,
            "valpha_q24": 0,
            "vbeta_q24": 0,
            "theta_cmd_u32": 0,
            "loop_counter": 0,
            "timestamp_cycles": 0,
        }

    def reset(self) -> None:
        """Stop the plant and restore its feedback snapshot to zero."""
        self.stop()
        self._last_timestamp = self._now()
        self._ialpha = 0.0
        self._ibeta = 0.0
        self._theta_mech = 0.0
        self._omega_mech = 0.0
        self._last_pwm = self._zero_pwm()
        self._publish_feedback(self._last_timestamp, reset=True)

    def start(self) -> bool:
        """Start IEP-clocked integration and publish an initial sample."""
        if self._running:
            return True
        self._last_timestamp = self._now()
        self._running = True
        self._iep().add_counter_observer(self._on_iep_advanced)
        self._observer_registered = True
        self._publish_feedback(self._last_timestamp)
        return True

    def stop(self) -> None:
        """Stop integration while leaving the last feedback sample readable."""
        if self._observer_registered:
            self._iep().remove_counter_observer(self._on_iep_advanced)
            self._observer_registered = False
        self._running = False

    def _read_pwm(self) -> dict[str, int]:
        base = abi.PWM_OUT_BASE
        block = abi.read_coherent_pwm_out(
            lambda: int.from_bytes(self.sim.memory_read(base, 4), "little"),
            lambda: self.sim.memory_read(base + 4, abi.PWM_OUT_SIZE - 4),
        )
        if block is None:
            return self._last_pwm
        self._last_pwm = block
        return block

    @staticmethod
    def _phase_voltage(pwm: dict[str, int]) -> tuple[float, float, float]:
        duties = (
            pwm["ta_q24"],
            pwm["tb_q24"],
            pwm["tc_q24"],
        )
        return tuple(
            (_clamp(duty / abi.Q_ONE, 0.0, 1.0) - 0.5) * V_DC
            for duty in duties
        )

    def _integrate(self, dt: float, pwm: dict[str, int]) -> None:
        va, vb, vc = self._phase_voltage(pwm)
        valpha, vbeta = _clarke_from_phase(va, vb, vc)
        theta_e = POLE_PAIRS * self._theta_mech
        omega_e = POLE_PAIRS * self._omega_mech
        ealpha = -KE * omega_e * math.sin(theta_e)
        ebeta = KE * omega_e * math.cos(theta_e)

        self._ialpha += dt * (valpha - RS * self._ialpha - ealpha) / LS
        self._ibeta += dt * (vbeta - RS * self._ibeta - ebeta) / LS

        cos_theta = math.cos(theta_e)
        sin_theta = math.sin(theta_e)
        iq = -self._ialpha * sin_theta + self._ibeta * cos_theta
        torque = 1.5 * POLE_PAIRS * KE * iq
        self._omega_mech += dt * (torque - B * self._omega_mech - Tload) / J
        self._theta_mech += dt * self._omega_mech

    def _elapsed_ticks(self, timestamp: int) -> int:
        now = int(timestamp) & _U64_MASK
        elapsed = (now - self._last_timestamp) & _U64_MASK
        # A large backwards jump is a manually reset/rebased clock, not a
        # multi-century simulation interval.
        if elapsed > (1 << 63):
            elapsed = 0
        self._last_timestamp = now
        return elapsed

    def advance_to(self, timestamp: int) -> int:
        """Integrate through an absolute IEP timestamp and return tick delta."""
        if not self._running:
            raise RuntimeError("motor model is not running")
        elapsed_ticks = self._elapsed_ticks(timestamp)
        if not elapsed_ticks:
            return 0

        total_dt = elapsed_ticks / self._tick_hz()
        steps = max(1, math.ceil(total_dt / _MAX_INTEGRATION_DT))
        dt = total_dt / steps
        pwm = self._read_pwm()
        for _ in range(steps):
            self._integrate(dt, pwm)
        self._publish_feedback(self._last_timestamp)
        return elapsed_ticks

    def step(self, ticks: int = 1) -> dict:
        """Advance by *ticks* from the last observed timestamp."""
        if ticks < 0:
            raise ValueError("motor model ticks cannot be negative")
        target = (self._last_timestamp + int(ticks)) & _U64_MASK
        self.advance_to(target)
        return self.state()

    def _measured_currents(self) -> tuple[float, float, float, float, float]:
        theta_e = POLE_PAIRS * self._theta_mech
        cos_theta = math.cos(theta_e)
        sin_theta = math.sin(theta_e)
        id_meas = self._ialpha * cos_theta + self._ibeta * sin_theta
        iq_meas = -self._ialpha * sin_theta + self._ibeta * cos_theta
        ia = self._ialpha
        ib = -0.5 * self._ialpha + (math.sqrt(3.0) / 2.0) * self._ibeta
        ic = -0.5 * self._ialpha - (math.sqrt(3.0) / 2.0) * self._ibeta
        return ia, ib, ic, id_meas, iq_meas

    def _rotor_theta_u32(self) -> int:
        phase = (self._theta_mech / (2.0 * math.pi)) % 1.0
        return int(round(phase * (1 << 32))) & _U32_MASK

    def _publish_feedback(self, timestamp: int, *, reset: bool = False) -> None:
        ia, ib, ic, id_meas, iq_meas = self._measured_currents()
        speed_rpm = self._omega_mech * 60.0 / (2.0 * math.pi)
        block = abi.pack_motor_fb(
            seq=0,
            ia_q24=_q24(ia / abi.CURRENT_BASE_A),
            ib_q24=_q24(ib / abi.CURRENT_BASE_A),
            ic_q24=_q24(ic / abi.CURRENT_BASE_A),
            id_meas_q24=_q24(id_meas / abi.CURRENT_BASE_A),
            iq_meas_q24=_q24(iq_meas / abi.CURRENT_BASE_A),
            rotor_theta_u32=self._rotor_theta_u32(),
            speed_rpm_q24=_q24(speed_rpm / abi.SPEED_BASE_RPM),
            timestamp=int(timestamp) & _U64_MASK,
        )
        base = abi.MOTOR_FB_BASE
        if reset:
            self.sim.memory.write(base, block)
            return

        current = int.from_bytes(self.sim.memory_read(base, 4), "little")
        stable = current if not (current & 1) else current + 1
        odd = (stable + 1) & _U32_MASK
        even = (stable + 2) & _U32_MASK
        self.sim.memory.write(base, struct.pack("<I", odd))
        self.sim.memory.write(base + 4, block[4:])
        self.sim.memory.write(base, struct.pack("<I", even))

    def _on_iep_advanced(self, timestamp: int) -> None:
        if self._running:
            self._on_tick(timestamp)

    def _on_tick(self, timestamp: int | None = None) -> int:
        """Advance one observer callback, using the current IEP time by default."""
        if not self._running:
            return 0
        return self.advance_to(self._now() if timestamp is None else timestamp)

    def state(self) -> dict:
        """Return a JSON-serializable plant and last-command snapshot."""
        ia, ib, ic, id_meas, iq_meas = self._measured_currents()
        return {
            "running": self._running,
            "observer_registered": self._observer_registered,
            "timestamp": self._last_timestamp,
            "rotor_theta_u32": self._rotor_theta_u32(),
            "rotor_theta": self._theta_mech % (2.0 * math.pi),
            "electrical_theta": (self._theta_mech * POLE_PAIRS) % (2.0 * math.pi),
            "speed_rpm": self._omega_mech * 60.0 / (2.0 * math.pi),
            "omega_mech": self._omega_mech,
            "ia": ia,
            "ib": ib,
            "ic": ic,
            "id": id_meas,
            "iq": iq_meas,
            "pwm": dict(self._last_pwm),
        }
