"""Host-side control plane for the open-loop FOC simulator."""

from __future__ import annotations

import math
import statistics
import struct
import time
import uuid
from collections import deque

from pru_io import foc_abi as abi
from pru_io.foc_motor_model import FocMotorModel


_U32_MASK = 0xFFFF_FFFF


def _q24(value: float, name: str = "reference") -> int:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    scaled = int(round(value * abi.Q_ONE))
    return max(-(1 << 31), min((1 << 31) - 1, scaled))


class FocRuntime:
    """Own the motor model, LUT, and staged control-block writes."""

    _FOC_IEPCLK = 0x1
    _FOC_IEP_GLOBAL_CFG = 0x11

    def __init__(self, sim, core: str = "pru0"):
        self.sim = sim
        self.core = core
        self.model: FocMotorModel = sim.motor_attach()
        self.session_id = uuid.uuid4().hex
        self._wall_started = None
        self._active_wall_time_s = 0.0
        self._time_origin = int(sim.iep.count)
        self._last_pwm = FocMotorModel._zero_pwm()
        self._last_fb = None
        self._publication_intervals = deque(maxlen=32)
        self._paused_reason = None
        self._pending_ui_samples: list[dict] = []
        self._last_ui_state = None
        self._last_ui_timestamp = None
        self._last_ui_wall_time = None
        self.seed_sine_lut()
        self._initialize_control()
        self._last_pwm = self._neutral_pwm()
        self._last_fb = self._read_feedback()

    def _clock_hz(self) -> float:
        iep = self.sim.iep
        if iep.enabled and iep.default_increment:
            return float(iep.active_clock_hz) * iep.default_increment
        # A hardware reset disables IEPCLK/GLOBAL_CFG.  The FOC contract still
        # needs the configured OCP-backed frequency to size the next period
        # before Start restores those registers.
        return float(iep.ocp_clock_hz)

    def _pru_clock_hz(self) -> float:
        return float(self.sim.iep.core_clock_hz(self.core))

    def _restore_iep_clock(self) -> None:
        """Restore the IEP clock mode required by the open-loop firmware."""
        self.sim.iep.write_iepclk(self._FOC_IEPCLK)
        self.sim.iep.write_global_cfg(self._FOC_IEP_GLOBAL_CFG)

    def _control_period_ticks(self) -> int:
        return max(1, round(self._clock_hz() / abi.CONTROL_LOOP_HZ))

    @staticmethod
    def _neutral_pwm(timestamp: int = 0) -> dict:
        return {
            "seq": 0,
            "ta_q24": abi.Q_ONE // 2,
            "tb_q24": abi.Q_ONE // 2,
            "tc_q24": abi.Q_ONE // 2,
            "valpha_q24": 0,
            "vbeta_q24": 0,
            "theta_cmd_u32": 0,
            "loop_counter": 0,
            "timestamp_cycles": int(timestamp) & 0xFFFF_FFFF_FFFF_FFFF,
            "timestamp_iep": int(timestamp) & 0xFFFF_FFFF_FFFF_FFFF,
            "status": abi.STATUS_DISABLED,
            "speed_cmd_q24": 0,
        }

    def _write_neutral_pwm(self, timestamp: int = 0) -> None:
        self.sim.memory.write(
            abi.PWM_OUT_BASE,
            abi.pack_pwm_out(**self._neutral_pwm(timestamp)),
        )

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
                control_period_iep_ticks=self._control_period_ticks(),
            ),
        )
        self._write_neutral_pwm(int(self.sim.iep.count))

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

    @staticmethod
    def _voltage_magnitude(vd: float, vq: float) -> float:
        return math.hypot(vd, vq)

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
            updates["speed_ref_q24"] = _q24(speed, "speed reference")
        if id is not None:
            updates["id_ref_q24"] = _q24(id, "Vd reference")
        if iq is not None:
            updates["iq_ref_q24"] = _q24(iq, "Vq reference")
        if ramp is not None:
            ramp = float(ramp)
            if not math.isfinite(ramp):
                raise ValueError("ramp reference must be finite")
            if ramp < 0:
                raise ValueError("ramp reference must be non-negative")
            updates["ramp_rate_q24"] = _q24(ramp, "ramp reference")
        current = self._control()
        vd = (updates.get("id_ref_q24", current["id_ref_q24"]) / abi.Q_ONE)
        vq = (updates.get("iq_ref_q24", current["iq_ref_q24"]) / abi.Q_ONE)
        if self._voltage_magnitude(vd, vq) > abi.MAX_VOLTAGE_MAGNITUDE_PU:
            raise ValueError(
                "voltage vector magnitude must be at most "
                f"{abi.MAX_VOLTAGE_MAGNITUDE_PU:.9f} pu"
            )
        if updates:
            return self._commit_control(**updates)
        return current

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
        self._restore_iep_clock()
        period = self._control_period_ticks()
        current = self._control()
        if current["control_period_iep_ticks"] != period:
            self.sim.memory.write(
                abi.CONTROL_BASE + abi.CONTROL_PERIOD_IEP_TICKS_OFF,
                struct.pack("<I", period),
            )
        self._set_enable(True)
        self.model.start()
        self._wall_started = time.perf_counter()
        self._active_wall_time_s = 0.0
        self._time_origin = self.model.state()["timestamp"]
        self._paused_reason = None
        return self.state()

    def _foc_wait_pc(self) -> int | None:
        core = self.sim.cores[self.core]
        labels = getattr(core._parser, "labels", {})
        wait_pc = labels.get("l_wait_deadline")
        if wait_pc is None or wait_pc + 3 >= len(core.instructions):
            return None
        if [
            core.instructions[wait_pc + offset].opcode
            for offset in range(4)
        ] != ["LBCO", "SUB", "SUC", "QBBS"]:
            return None
        branch_target = core.instructions[wait_pc + 3].operands[0]
        branch_name = getattr(branch_target, "name", None)
        if branch_name is None or branch_name.lower() != "l_wait_deadline":
            return None
        return int(wait_pc)

    def _fast_forward_foc_wait(self) -> bool:
        """Skip only the proven four-instruction IEP polling loop."""
        core = self.sim.cores[self.core]
        iep = self.sim.iep
        if not iep.enabled or not iep.default_increment:
            return False
        if (
            (core.io_port.perif is not None and core.io_port.perif.enabled)
            or core.io_port.uart_generator is not None
            or core.io_port.ssi_generator is not None
        ):
            return False

        deadline = (
            core.registers.read_full(4)
            | (core.registers.read_full(5) << 32)
        ) & 0xFFFF_FFFF_FFFF_FFFF
        now = int(iep.count) & 0xFFFF_FFFF_FFFF_FFFF
        remaining = (deadline - now) & 0xFFFF_FFFF_FFFF_FFFF
        if remaining == 0 or remaining >= (1 << 63):
            # The ordinary instructions must handle an already-missed deadline.
            return False

        tick_hz = iep.active_clock_hz * iep.default_increment
        core_hz = iep.core_clock_hz(self.core)
        ticks_per_cycle = tick_hz / core_hz
        if ticks_per_cycle <= 0:
            return False

        poll_instructions = 4
        poll_cycles = 7
        polls = max(1, math.ceil(remaining / (ticks_per_cycle * poll_cycles)))
        instruction_budget = getattr(self, "_fast_path_instruction_budget", None)
        if instruction_budget is not None:
            max_polls = instruction_budget // poll_instructions
            if max_polls < 1:
                return False
            polls = min(polls, max_polls)
        deadline_reached = False
        while True:
            cycles = polls * poll_cycles
            instructions = polls * poll_instructions
            if core.io_port.sd_filter is not None:
                core.io_port.sd_filter.advance_cycles(instructions)
            core.counters.cycles += cycles
            core.counters.instruction_count += instructions
            core.counters.stall_cycles += cycles - instructions
            if core.io_port.perif is not None:
                core.io_port.perif.advance_cycles(core.counters.cycles)
            iep.observe_core_cycles(self.core, core.counters.cycles)
            remaining = (deadline - int(iep.count)) & 0xFFFF_FFFF_FFFF_FFFF
            if remaining == 0 or remaining >= (1 << 63):
                deadline_reached = True
                break
            if instruction_budget is not None:
                instruction_budget -= instructions
                if instruction_budget < poll_instructions:
                    break
            polls = 1

        if deadline_reached:
            core.pc += poll_instructions
        return True

    def _configure_timer_wait_fast_path(self) -> bool:
        core = self.sim.cores[self.core]
        if (
            (core.io_port.perif is not None and core.io_port.perif.enabled)
            or core.io_port.uart_generator is not None
            or core.io_port.ssi_generator is not None
        ):
            return False
        wait_pc = self._foc_wait_pc()
        if wait_pc is None:
            return False
        core.configure_foc_timer_wait(wait_pc, self._fast_forward_foc_wait)
        return True

    def run_batch(self, max_steps: int = 4_096, *, fast_path: bool = True) -> dict:
        """Run one bounded PRU batch for the dashboard execution task."""
        if max_steps < 0:
            raise ValueError("FOC execution batch cannot be negative")
        pru = self.sim.cores[self.core]
        fast_enabled = (
            fast_path
            and not pru.breakpoints
            and self._configure_timer_wait_fast_path()
        )
        steps = 0
        try:
            while (
                steps < int(max_steps)
                and not pru.halted
                and pru.pc < len(pru.instructions)
                and pru.pc not in pru.breakpoints
            ):
                self._fast_path_instruction_budget = (
                    int(max_steps) - steps if fast_enabled else None
                )
                before = pru.counters.instruction_count
                pru.step()
                retired = pru.counters.instruction_count - before
                steps += max(1, retired)
        finally:
            self._fast_path_instruction_budget = None
            if fast_enabled:
                pru.clear_foc_timer_wait()
        return {
            "steps": steps,
            "pc": pru.pc,
            "at_breakpoint": pru.pc in pru.breakpoints,
            "cycles": pru.counters.cycles,
            "stall_cycles": pru.counters.stall_cycles,
            "halted": pru.halted,
            "fault": pru.fault,
        }

    def stop(self) -> dict:
        """Disable the control block and stop the plant observer."""
        self._set_enable(False)
        core = self.sim.cores.get(self.core)
        before = self._read_pwm()
        if (
            core is not None
            and core.instructions
            and not core.halted
            and not (before and before.get("status", 0) & abi.STATUS_DISABLED)
        ):
            target_loop = int(before.get("loop_counter", 0)) if before else 0
            max_steps = max(2_000, self._control_period_ticks() * 8)
            for _ in range(max_steps):
                self.sim.step(self.core, 1)
                candidate = self._read_pwm()
                if (
                    candidate["loop_counter"] != target_loop
                    and candidate["status"] & abi.STATUS_DISABLED
                ):
                    break
        self.model.stop()
        self._pause_wall_time()
        self._paused_reason = "stopped"
        self.model.publish_feedback()
        self._read_pwm()
        return self.state()

    def pause(self, reason: str = "paused") -> dict:
        """Pause the observer without fabricating a neutral PWM publication."""
        self.model.stop()
        self._pause_wall_time()
        self._paused_reason = str(reason)
        return self.state()

    def _pause_wall_time(self) -> None:
        if self._wall_started is None:
            return
        self._active_wall_time_s += max(0.0, time.perf_counter() - self._wall_started)
        self._wall_started = None

    def reset(self) -> dict:
        """Reset firmware, shared FOC memory, plant state, and session history."""
        self.sim.hard_reset()
        self.model.reset()
        self.seed_sine_lut()
        self._initialize_control()
        self.session_id = uuid.uuid4().hex
        self._wall_started = None
        self._active_wall_time_s = 0.0
        self._time_origin = int(self.sim.iep.count)
        self._last_pwm = self._neutral_pwm(self._time_origin)
        self._last_fb = self._read_feedback()
        self._publication_intervals.clear()
        self._paused_reason = None
        self._pending_ui_samples.clear()
        self._last_ui_state = None
        self._last_ui_timestamp = None
        self._last_ui_wall_time = None
        return self.state()

    def step(self, ticks: int = 1) -> dict:
        """Manually advance the plant and return the shared-memory snapshot."""
        self.model.step(ticks)
        return self.state()

    def _read_pwm(self) -> dict | None:
        base = abi.PWM_OUT_BASE
        block = abi.read_coherent_pwm_out(
            lambda: int.from_bytes(self.sim.memory_read(base, 4), "little"),
            lambda: self.sim.memory_read(base + 4, abi.PWM_OUT_SIZE - 4),
        )
        if block is not None:
            previous = self._last_pwm
            if previous is not None:
                delta_loop = (
                    int(block["loop_counter"]) - int(previous["loop_counter"])
                ) & _U32_MASK
                delta_timestamp = (
                    int(block["timestamp_cycles"])
                    - int(previous.get("timestamp_cycles", 0))
                ) & 0xFFFF_FFFF_FFFF_FFFF
                if delta_loop and delta_timestamp:
                    self._publication_intervals.append(delta_timestamp / delta_loop)
            self._last_pwm = block
        return self._last_pwm

    def _read_feedback(self) -> dict | None:
        base = abi.MOTOR_FB_BASE
        block = abi.read_coherent_motor_fb(
            lambda: int.from_bytes(self.sim.memory_read(base, 4), "little"),
            lambda: self.sim.memory_read(base + 4, abi.MOTOR_FB_SIZE - 4),
        )
        if block is not None:
            self._last_fb = block
        return self._last_fb

    def state(self) -> dict:
        """Return control, PWM, feedback, and model snapshots."""
        control = self._control()
        pwm = self._read_pwm()
        fb = self._read_feedback()
        if self.model.running:
            self.model.advance_to(int(self.sim.iep.count))
        model = self.model.state()
        samples = self.model.drain_samples()
        clock_hz = self._clock_hz()
        period = max(1, int(control.get("control_period_iep_ticks", 0)))
        sim_time_s = ((model["timestamp"] - self._time_origin) & 0xFFFF_FFFF_FFFF_FFFF) / clock_hz
        active_wall_time_s = self._active_wall_time_s
        if self._wall_started is not None:
            active_wall_time_s += max(0.0, time.perf_counter() - self._wall_started)
        configured_loop_hz = clock_hz / period
        if len(self._publication_intervals) >= 2:
            observed_period = statistics.median(self._publication_intervals)
            observed_loop_hz = clock_hz / observed_period
        else:
            observed_loop_hz = configured_loop_hz
        sim_wall_ratio = sim_time_s / active_wall_time_s if active_wall_time_s > 0 else 0.0
        requested_speed_rpm = control["speed_ref_q24"] / abi.Q_ONE * abi.SPEED_BASE_RPM
        ramped_speed_rpm = pwm["speed_cmd_q24"] / abi.Q_ONE * abi.SPEED_BASE_RPM
        return {
            "control": control,
            "pwm": pwm,
            "fb": fb,
            "model": model,
            "samples": samples,
            "session_id": self.session_id,
            "clock": {
                "pru_clock_hz": self._pru_clock_hz(),
                "iep_clock_hz": clock_hz,
                "iep_hz": clock_hz,
                "control_period_iep_ticks": period,
                "configured_control_loop_frequency_hz": configured_loop_hz,
                "control_loop_frequency_hz": observed_loop_hz,
                "loop_frequency_hz": configured_loop_hz,
                "sim_time_s": sim_time_s,
                "active_wall_time_s": active_wall_time_s,
                "wall_time_s": active_wall_time_s,
                "execution_active": self._wall_started is not None,
                "sim_wall_ratio": sim_wall_ratio,
                "simulated_ms_per_wall_second": sim_wall_ratio * 1_000.0,
            },
            "telemetry": {
                "requested_speed_rpm": requested_speed_rpm,
                "ramped_speed_rpm": ramped_speed_rpm,
                "measured_speed_rpm": model["speed_rpm"],
                "angle_error_deg": self._angle_error_deg(pwm, fb),
            },
            "fault": self._fault(pwm),
            "paused_reason": self._paused_reason,
        }

    def ui_state(self, *, force: bool = False) -> dict | None:
        """Return a state update no faster than the dashboard publish cadence."""
        fresh = self.state()
        self._pending_ui_samples.extend(fresh.pop("samples", []))
        timestamp = int(fresh["model"]["timestamp"])
        period = max(1, round(self._clock_hz() / 30.0))
        wall_time = time.perf_counter()
        due = (
            force
            or self._last_ui_state is None
            or self._last_ui_timestamp is None
            or ((timestamp - self._last_ui_timestamp) & 0xFFFF_FFFF_FFFF_FFFF) >= period
            or self._last_ui_wall_time is None
            or wall_time - self._last_ui_wall_time >= (1.0 / 30.0)
            or not fresh["model"]["running"]
        )
        if not due:
            return None
        fresh["samples"] = self._pending_ui_samples
        self._pending_ui_samples = []
        self._last_ui_state = fresh
        self._last_ui_timestamp = timestamp
        self._last_ui_wall_time = wall_time
        return fresh

    @staticmethod
    def _angle_error_deg(pwm: dict, fb: dict) -> float:
        command = int(pwm.get("theta_cmd_u32", 0)) & 0xFFFF_FFFF
        rotor = int(fb.get("rotor_theta_u32", 0)) & 0xFFFF_FFFF
        delta = ((command - rotor + 0x8000_0000) & 0xFFFF_FFFF) - 0x8000_0000
        return delta * 360.0 / (1 << 32)

    def _fault(self, pwm: dict) -> dict | None:
        core_fault = self.sim.cores[self.core].fault
        if core_fault is not None:
            return core_fault
        status = int(pwm.get("status", 0))
        fault_flags = status & (abi.STATUS_DEADLINE_MISS | abi.STATUS_INVALID_CONFIG)
        if fault_flags:
            return {"type": "foc_status", "status": status}
        return None
