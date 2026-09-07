"""Regression contracts for the open-loop FOC validation repair."""

import math
from pathlib import Path

import pytest

from pru_io import foc_abi as abi
from pru_io.foc_motor_model import FocMotorModel, _clarke_from_phase
from pru_io.foc_runtime import FocRuntime
from simulator import Simulator


ROOT = Path(__file__).parents[1]
FOC_SOURCE = (ROOT / "source" / "foc_open_loop" / "foc_open_loop.asm").read_text()


def _load_firmware(nominal_config):
    sim = Simulator(nominal_config)
    assert sim.load("pru0", FOC_SOURCE, include_paths=[str(ROOT / "source")]) == []
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    return sim


def _write_control(sim, **values):
    values.setdefault(
        "control_period_iep_ticks",
        round(float(sim.iep.active_clock_hz) / abi.CONTROL_LOOP_HZ),
    )
    sim.memory.write(abi.CONTROL_BASE, abi.pack_control(**values))


def _seed_lut(sim):
    for index in range(abi.SINE_LUT_ENTRIES):
        value = round(math.sin(2.0 * math.pi * index / abi.SINE_LUT_ENTRIES) * abi.Q_ONE)
        sim.memory.write(
            abi.SINE_LUT_BASE + index * abi.SINE_LUT_SIZE,
            (value & abi.U32_MASK).to_bytes(4, "little"),
        )


def _read_pwm(sim):
    return abi.read_coherent_pwm_out(
        lambda: int.from_bytes(sim.memory_read(abi.PWM_OUT_BASE, 4), "little"),
        lambda: sim.memory_read(abi.PWM_OUT_BASE + 4, abi.PWM_OUT_SIZE - 4),
    )


def _write_pwm(sim, **updates):
    values = {
        "seq": 2,
        "ta_q24": abi.Q_ONE // 2,
        "tb_q24": abi.Q_ONE // 2,
        "tc_q24": abi.Q_ONE // 2,
        "valpha_q24": 0,
        "vbeta_q24": 0,
        "theta_cmd_u32": 0,
        "loop_counter": 1,
        "timestamp_cycles": 0,
        "status": 0,
        "speed_cmd_q24": 0,
    }
    values.update(updates)
    sim.memory.write(abi.PWM_OUT_BASE, abi.pack_pwm_out(**values))


def _run_publications(sim, count, max_steps=200_000):
    publications = []
    current = _read_pwm(sim)
    previous_loop = current["loop_counter"] if current is not None else 0
    for _ in range(max_steps):
        sim.step("pru0", 1)
        pwm = _read_pwm(sim)
        if pwm is not None and pwm["loop_counter"] != previous_loop:
            publications.append(pwm)
            previous_loop = pwm["loop_counter"]
            if len(publications) >= count:
                return publications
    raise AssertionError("firmware did not publish the requested PWM samples")


def test_foc_abi_exposes_clock_status_and_ramped_command_fields():
    assert abi.ABI_VERSION >= 2
    assert abi.CONTROL_PERIOD_IEP_TICKS_OFF == 0x24
    assert abi.CONTROL_SIZE == 40
    assert abi.PWM_OUT_TIMESTAMP_IEP_OFF == 0x20
    assert abi.PWM_OUT_STATUS_OFF == 0x28
    assert abi.PWM_OUT_SPEED_CMD_Q24_OFF == 0x2C
    assert abi.PWM_OUT_SIZE == 48
    assert abi.STATUS_DISABLED
    assert abi.STATUS_SATURATED
    assert abi.STATUS_DEADLINE_MISS


def test_foc_abi_round_trips_true_timestamp_status_and_command():
    block = abi.pack_pwm_out(
        seq=4,
        ta_q24=abi.Q_ONE // 2,
        tb_q24=abi.Q_ONE // 2,
        tc_q24=abi.Q_ONE // 2,
        valpha_q24=0,
        vbeta_q24=0,
        theta_cmd_u32=0x12345678,
        loop_counter=7,
        timestamp_cycles=2000,
        status=abi.STATUS_DISABLED,
        speed_cmd_q24=round(0.4 * abi.Q_ONE),
    )
    decoded = abi.unpack_pwm_out(block)
    assert decoded["timestamp_cycles"] == 2000
    assert decoded["status"] == abi.STATUS_DISABLED
    assert decoded["speed_cmd_q24"] == round(0.4 * abi.Q_ONE)


def test_voltage_vector_limit_is_representable_in_the_generated_abi():
    assert math.isclose(
        abi.MAX_VOLTAGE_MAGNITUDE_PU,
        1.0 / math.sqrt(3.0),
        rel_tol=0,
        abs_tol=2.0 / abi.Q_ONE,
    )


def test_motor_model_uses_true_iep_elapsed_time_and_held_pwm(nominal_config):
    sim = Simulator(nominal_config)
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    model = FocMotorModel(sim)
    model.start()

    _write_pwm(sim, ta_q24=round(0.7 * abi.Q_ONE), loop_counter=1)
    model.advance_to(0)
    assert model.state()["pwm"]["loop_counter"] == 1

    elapsed = []
    model._integrate = lambda dt, pwm: elapsed.append((dt, pwm["ta_q24"]))
    _write_pwm(sim, ta_q24=round(0.8 * abi.Q_ONE), loop_counter=100)
    model.advance_to(2_000)
    _write_pwm(sim, ta_q24=round(0.9 * abi.Q_ONE), loop_counter=200)
    model.advance_to(4_000)

    expected_dt = 2_000 / float(sim.iep.active_clock_hz)
    assert math.isclose(sum(item[0] for item in elapsed[:2]), expected_dt)
    assert math.isclose(sum(item[0] for item in elapsed[2:]), expected_dt)
    assert {item[1] for item in elapsed[:2]} == {round(0.7 * abi.Q_ONE)}
    assert {item[1] for item in elapsed[2:]} == {round(0.8 * abi.Q_ONE)}


def test_motor_feedback_is_published_at_sample_cadence(nominal_config, monkeypatch):
    sim = Simulator(nominal_config)
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    model = FocMotorModel(sim)
    model.start()

    calls = 0
    publish = model._publish_feedback

    def counted_publish(*args, **kwargs):
        nonlocal calls
        calls += 1
        return publish(*args, **kwargs)

    monkeypatch.setattr(model, "_publish_feedback", counted_publish)
    for timestamp in range(1_000, 20_001, 1_000):
        model.advance_to(timestamp)

    assert calls == 1


def test_motor_observer_coalesces_callbacks_at_the_control_period(nominal_config):
    sim = Simulator(nominal_config)
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    model = FocMotorModel(sim)
    model.start()
    elapsed = []
    model._integrate = lambda dt, pwm: elapsed.append(dt)

    for timestamp in range(1, 2_001):
        model._on_tick(timestamp)

    assert len(elapsed) == 2
    assert math.isclose(sum(elapsed), 2_000 / float(sim.iep.active_clock_hz))


def test_motor_sample_history_survives_iep_rollover(nominal_config):
    sim = Simulator(nominal_config)
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    mask = (1 << 64) - 1
    origin = mask - 10_000
    sim.iep.write_count(low=origin & 0xFFFF_FFFF, high=origin >> 32)
    model = FocMotorModel(sim)
    model.start()

    model.advance_to((origin + 30_000) & mask)
    samples = model.drain_samples()

    assert [sample["timestamp"] for sample in samples] == [
        (origin + 20_000) & mask
    ]


def test_motor_model_uses_standard_inverse_clarke_convention():
    alpha, beta = _clarke_from_phase(0.2, -0.1, -0.1)
    assert math.isclose(alpha, 0.2)
    assert math.isclose(beta, 0.0)


def test_runtime_rejects_nonfinite_and_overvoltage_references(nominal_config):
    runtime = FocRuntime(Simulator(nominal_config))

    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            runtime.set_reference(speed=value)

    with pytest.raises(ValueError):
        runtime.set_reference(ramp=-0.01)

    with pytest.raises(ValueError):
        runtime.set_reference(id=abi.MAX_VOLTAGE_MAGNITUDE_PU, iq=0.01)


def test_runtime_reports_clock_and_requested_ramped_measured_speed(nominal_config):
    sim = Simulator(nominal_config)
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)

    state = runtime.state()
    assert state["control"]["control_period_iep_ticks"] == 2_000
    assert state["clock"]["loop_frequency_hz"] == 100_000
    assert math.isclose(state["telemetry"]["requested_speed_rpm"], 400, abs_tol=1e-3)
    assert state["telemetry"]["ramped_speed_rpm"] == 0
    assert state["telemetry"]["measured_speed_rpm"] == 0


def test_ui_state_publishes_on_wall_time_when_simulation_is_slow(
    nominal_config, monkeypatch
):
    wall_time = [10.0]
    monkeypatch.setattr(
        "pru_io.foc_runtime.time.perf_counter",
        lambda: wall_time[0],
    )
    runtime = FocRuntime(Simulator(nominal_config))
    runtime.start()

    assert runtime.ui_state(force=True) is not None
    assert runtime.ui_state() is None

    wall_time[0] += (1.0 / 30.0) + 0.001
    assert runtime.ui_state() is not None


@pytest.mark.parametrize("fast_path", [False, True])
def test_runtime_batch_preserves_foc_state_contract(nominal_config, fast_path):
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)
    runtime.start()

    result = runtime.run_batch(4_096, fast_path=fast_path)
    state = runtime.state()

    assert result["fault"] is None
    assert state["pwm"]["seq"] % 2 == 0
    assert state["pwm"]["timestamp_cycles"] <= sim.iep.count
    assert state["model"]["timestamp"] == sim.iep.count


def test_foc_timer_wait_fast_path_matches_instruction_timeline(nominal_config):
    snapshots = []
    for fast_path in (False, True):
        sim = _load_firmware(nominal_config)
        runtime = FocRuntime(sim)
        runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)
        runtime.start()
        runtime.run_batch(4_096, fast_path=fast_path)
        state = runtime.state()
        snapshots.append((
            sim.iep.count,
            state["pwm"]["loop_counter"],
            state["pwm"]["timestamp_cycles"],
            state["pwm"]["theta_cmd_u32"],
            state["model"]["speed_rpm"],
        ))

    reference = snapshots[0]
    fast = snapshots[1]
    assert fast[0] == reference[0]
    assert fast[1] == reference[1]
    assert abs(fast[2] - reference[2]) <= 7
    assert fast[3] == reference[3]
    assert math.isclose(fast[4], reference[4], abs_tol=0.05)


def test_runtime_recognizes_the_foc_timer_wait_loop(nominal_config):
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    core = sim.cores["pru0"]

    wait_pc = core._parser.labels["l_wait_deadline"]

    assert runtime._foc_wait_pc() == wait_pc
    assert runtime._configure_timer_wait_fast_path() is True
    assert core._foc_timer_wait_pc == wait_pc


def test_runtime_stop_waits_for_neutral_firmware_boundary(nominal_config):
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)
    runtime.start()
    _run_publications(sim, 2)

    runtime.stop()
    stopped = runtime.state()
    assert stopped["model"]["running"] is False
    assert [stopped["pwm"][name] for name in ("ta_q24", "tb_q24", "tc_q24")] == [
        abi.Q_ONE // 2
    ] * 3
    assert stopped["pwm"]["status"] & abi.STATUS_DISABLED

    frozen_speed = stopped["model"]["speed_rpm"]
    sim.step("pru0", 5_000)
    assert runtime.state()["model"]["speed_rpm"] == frozen_speed


def test_runtime_reset_restores_control_shared_memory_and_plant(nominal_config):
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)
    runtime.start()
    sim.step("pru0", 5_000)

    runtime.reset()
    state = runtime.state()
    assert state["control"]["enable"] == 0
    assert state["control"]["requested_generation"] == 0
    assert state["model"]["running"] is False
    assert state["model"]["rotor_theta_u32"] == 0
    assert state["pwm"]["status"] & abi.STATUS_DISABLED
    assert state["pwm"]["ta_q24"] == abi.Q_ONE // 2
    assert state["fb"]["ia_q24"] == 0


def test_real_firmware_reset_then_start_restores_iep_and_advances(nominal_config):
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)
    runtime.start()
    runtime.run_batch(4_096)

    runtime.reset()
    assert sim.iep.enabled is False
    assert runtime.state()["pwm"]["loop_counter"] == 0

    runtime.start()
    runtime.run_batch(4_096)
    state = runtime.state()

    assert sim.iep.enabled is True
    assert state["control"]["control_period_iep_ticks"] == 2_000
    assert sim.iep.count > 0
    assert state["pwm"]["loop_counter"] > 0
    assert state["model"]["running"] is True


def test_runtime_runs_to_a_real_breakpoint_without_timer_fast_path(nominal_config):
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    wait_pc = sim.cores["pru0"]._parser.labels["l_wait_deadline"]
    sim.cores["pru0"].breakpoints.add(wait_pc)

    result = runtime.run_batch(1_000)

    assert result["steps"] > 0
    assert result["pc"] == wait_pc
    assert result["at_breakpoint"] is True


def test_runtime_reports_separate_clock_cadence_and_active_throughput(
    nominal_config, monkeypatch
):
    wall_time = [100.0]
    monkeypatch.setattr("pru_io.foc_runtime.time.perf_counter", lambda: wall_time[0])
    sim = _load_firmware(nominal_config)
    runtime = FocRuntime(sim)
    runtime.start()
    runtime.run_batch(4_096)
    wall_time[0] += 1.0
    active = runtime.state()
    wall_time[0] += 10.0
    paused = runtime.pause("breakpoint")
    wall_time[0] += 10.0
    after_pause = runtime.state()

    assert active["clock"]["pru_clock_hz"] == 200_000_000
    assert active["clock"]["iep_clock_hz"] == 200_000_000
    assert active["clock"]["control_loop_frequency_hz"] == 100_000
    assert active["clock"]["simulated_ms_per_wall_second"] > 0
    assert paused["clock"]["active_wall_time_s"] == after_pause["clock"]["active_wall_time_s"]


def test_firmware_latches_generation_zero_configuration(nominal_config):
    sim = _load_firmware(nominal_config)
    _seed_lut(sim)
    _write_control(
        sim,
        enable=1,
        requested_generation=0,
        speed_ref_q24=round(0.2 * abi.Q_ONE),
        id_ref_q24=round(0.1 * abi.Q_ONE),
        iq_ref_q24=round(0.2 * abi.Q_ONE),
        ramp_rate_q24=round(0.01 * abi.Q_ONE),
    )

    for _ in range(500):
        sim.step("pru0", 1)

    assert sim.registers("pru0")[6] == round(0.2 * abi.Q_ONE)
    assert sim.registers("pru0")[7] == round(0.1 * abi.Q_ONE)
    assert sim.registers("pru0")[8] == round(0.2 * abi.Q_ONE)


def test_firmware_disable_is_independent_of_reference_generation(nominal_config):
    sim = _load_firmware(nominal_config)
    _seed_lut(sim)
    _write_control(
        sim,
        enable=1,
        requested_generation=1,
        speed_ref_q24=round(0.4 * abi.Q_ONE),
        iq_ref_q24=round(0.25 * abi.Q_ONE),
        ramp_rate_q24=round(0.01 * abi.Q_ONE),
    )
    _run_publications(sim, 3)
    before = _read_pwm(sim)
    sim.memory.write(
        abi.CONTROL_BASE + abi.CONTROL_ENABLE_OFF,
        (0).to_bytes(4, "little"),
    )
    after = _run_publications(sim, 1)[0]

    assert [after[name] for name in ("ta_q24", "tb_q24", "tc_q24")] == [
        abi.Q_ONE // 2
    ] * 3
    assert after["theta_cmd_u32"] == before["theta_cmd_u32"]
    assert after["status"] & abi.STATUS_DISABLED


def test_firmware_uses_standard_inverse_clarke_cardinal_mapping(nominal_config):
    sim = _load_firmware(nominal_config)
    _seed_lut(sim)
    _write_control(
        sim,
        enable=1,
        requested_generation=1,
        id_ref_q24=round(0.5 * abi.Q_ONE),
        iq_ref_q24=0,
        ramp_rate_q24=0,
    )
    pwm = _run_publications(sim, 1)[0]

    assert abs(pwm["valpha_q24"] / abi.Q_ONE - 0.5) < 5e-5
    assert abs(pwm["vbeta_q24"]) <= 2
    assert abs(pwm["ta_q24"] / abi.Q_ONE - 0.875) < 5e-5
    assert abs(pwm["tb_q24"] / abi.Q_ONE - 0.125) < 5e-5
    assert abs(pwm["tc_q24"] / abi.Q_ONE - 0.125) < 5e-5


def test_firmware_publishes_absolute_iep_timestamps_at_control_cadence(nominal_config):
    sim = _load_firmware(nominal_config)
    _seed_lut(sim)
    _write_control(sim, enable=1, requested_generation=1, ramp_rate_q24=0)
    samples = _run_publications(sim, 5)
    deltas = [
        (samples[index]["timestamp_cycles"] - samples[index - 1]["timestamp_cycles"])
        & ((1 << 64) - 1)
        for index in range(1, len(samples))
    ]

    assert all(1_900 <= delta <= 2_200 for delta in deltas), deltas
    assert all(sample["timestamp_cycles"] > sample["loop_counter"] for sample in samples)


@pytest.mark.parametrize("clock_mhz", [200.0, 250.0, 300.0])
def test_firmware_control_period_tracks_configured_iep_clock(sim_config, clock_mhz):
    sim = _load_firmware(sim_config(pru_clock_mhz=clock_mhz, pru1_clock_mhz=clock_mhz))
    _seed_lut(sim)
    _write_control(sim, enable=1, requested_generation=1, ramp_rate_q24=0)
    samples = _run_publications(sim, 4)
    deltas = [
        (samples[index]["timestamp_cycles"] - samples[index - 1]["timestamp_cycles"])
        & ((1 << 64) - 1)
        for index in range(1, len(samples))
    ]

    expected = round(clock_mhz * 1_000_000 / abi.CONTROL_LOOP_HZ)
    # The firmware publishes on the first instruction after the absolute
    # deadline; the polling loop contributes a small, deterministic bound.
    assert all(abs(delta - expected) <= 16 for delta in deltas), (expected, deltas)


@pytest.mark.parametrize("clock_mhz", [200.0, 250.0, 300.0])
def test_runtime_clock_metadata_and_real_assembly_cadence(sim_config, clock_mhz):
    sim = _load_firmware(sim_config(pru_clock_mhz=clock_mhz, pru1_clock_mhz=clock_mhz))
    runtime = FocRuntime(sim)
    runtime.set_reference(speed=0.4, iq=0.25, ramp=0.01)
    runtime.start()
    runtime.run_batch(100_000)
    state = runtime.state()

    expected_ticks = round(clock_mhz * 1_000_000 / abi.CONTROL_LOOP_HZ)
    assert state["clock"]["pru_clock_hz"] == clock_mhz * 1_000_000
    assert state["clock"]["iep_clock_hz"] == clock_mhz * 1_000_000
    assert state["clock"]["control_period_iep_ticks"] == expected_ticks
    assert math.isclose(
        state["clock"]["control_loop_frequency_hz"],
        abi.CONTROL_LOOP_HZ,
        rel_tol=0.01,
    )
    assert state["pwm"]["loop_counter"] > 0


def test_firmware_clamps_invalid_vector_duties_and_reports_saturation(nominal_config):
    sim = _load_firmware(nominal_config)
    _seed_lut(sim)
    _write_control(
        sim,
        enable=1,
        requested_generation=1,
        id_ref_q24=abi.Q_ONE,
        iq_ref_q24=abi.Q_ONE,
        ramp_rate_q24=0,
    )
    pwm = _run_publications(sim, 1)[0]

    assert all(0 <= pwm[name] <= abi.Q_ONE for name in ("ta_q24", "tb_q24", "tc_q24"))
    assert pwm["status"] & abi.STATUS_SATURATED
    assert pwm["status"] & abi.STATUS_INVALID_CONFIG


def test_firmware_reports_deadline_miss_and_falls_back_to_neutral(nominal_config):
    sim = _load_firmware(nominal_config)
    _seed_lut(sim)
    _write_control(
        sim,
        enable=1,
        requested_generation=1,
        iq_ref_q24=round(0.25 * abi.Q_ONE),
        ramp_rate_q24=0,
        control_period_iep_ticks=1,
    )
    pwm = _run_publications(sim, 1)[0]

    assert pwm["status"] & abi.STATUS_DEADLINE_MISS
    assert [pwm[name] for name in ("ta_q24", "tb_q24", "tc_q24")] == [
        abi.Q_ONE // 2
    ] * 3
