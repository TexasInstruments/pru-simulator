"""Timing and configuration regressions for the optimized open-loop FOC path."""

import math
import random

from pru_io import foc_abi as abi
import pytest

from tools.profile_foc import STAGE_LABELS, _run_case
from tests.test_foc_firmware import (
    make_sim,
    read_u32,
    read_i32,
    seed_sine_lut,
    to_q24,
    write_control,
)


U32_MASK = 0xFFFF_FFFF
Q24 = 1 << abi.Q_FRACTION_BITS


def _signed32(value):
    value &= U32_MASK
    return value - (1 << 32) if value & 0x8000_0000 else value


def _wrap32(value):
    return value & U32_MASK


def _mul_q24_reference(a, b):
    """Independent signed-Q24 reference with per-product truncation."""
    a = _signed32(a)
    b = _signed32(b)
    magnitude = (abs(a) * abs(b)) >> abi.Q_FRACTION_BITS
    result = -magnitude if (a < 0) != (b < 0) else magnitude
    return _wrap32(result)


def _run_stage(sim, start, end, max_steps=1_000):
    """Run one real assembled stage from its labelled entry to its exit."""
    core = sim.cores["pru0"]
    core.pc = start
    for _ in range(max_steps):
        if core.pc == end:
            return
        sim.step("pru0", 1)
    raise AssertionError(f"stage {start}->{end} did not finish")


def _cached_voltage_signs(id_raw, iq_raw):
    id_negative = _signed32(id_raw) < 0
    iq_negative = _signed32(iq_raw) < 0
    return int(id_negative) | (int(id_negative != iq_negative) << 8)


def _seed_inverse_park_stage(sim, id_raw, iq_raw, sin_raw, cos_raw):
    core = sim.cores["pru0"]
    core.registers.write_full(7, id_raw)       # VD_REF
    core.registers.write_full(8, iq_raw)       # VQ_REF
    core.registers.write_full(17, sin_raw)     # SIN_Q24
    core.registers.write_full(29, cos_raw)     # COS_Q24 / MAC operand B
    core.registers.write_full(18, _cached_voltage_signs(id_raw, iq_raw))
    core.registers.write_full(20, abs(_signed32(id_raw)) << 8)
    core.registers.write_full(21, abs(_signed32(iq_raw)) << 8)


def _run_inverse_park_stage(sim, id_raw, iq_raw, sin_raw, cos_raw):
    core = sim.cores["pru0"]
    labels = core._parser.labels
    _seed_inverse_park_stage(sim, id_raw, iq_raw, sin_raw, cos_raw)
    _run_stage(sim, labels["l_inverse_park_start"],
               labels["l_inverse_park_end"])
    return core.registers.read_full(19), core.registers.read_full(22)


def _reference_common_mode(values):
    minimum = min(_signed32(value) for value in values)
    maximum = max(_signed32(value) for value in values)
    wrapped_sum = _wrap32(minimum + maximum)
    half = (wrapped_sum >> 1) | (wrapped_sum & 0x8000_0000)
    return half & U32_MASK


def _reference_clamped_duties(values, common_mode):
    offset = ((abi.Q_ONE // 2) - common_mode) & U32_MASK
    duties = []
    for value in values:
        shifted = (_wrap32(value) + offset) & U32_MASK
        if shifted & 0x8000_0000:
            shifted = 0
        elif shifted > abi.Q_ONE:
            shifted = abi.Q_ONE
        duties.append(_wrap32(shifted))
    return tuple(duties)


def _run_common_mode_case(nominal_config, values):
    sim = make_sim(nominal_config)
    core = sim.cores["pru0"]
    labels = core._parser.labels
    for register, value in zip((23, 24, 0), values):
        core.registers.write_full(register, _wrap32(value))

    start_cycles = core.counters.cycles
    _run_stage(sim, labels["l_svpwm_common_mode_start"],
               labels["l_svpwm_common_mode_end"])
    cycles = core.counters.cycles - start_cycles
    common_mode = core.registers.read_full(27)
    _run_stage(sim, labels["l_svpwm_duty_offset_start"],
               labels["l_svpwm_duty_offset_end"])
    _run_stage(sim, labels["l_duty_clamp_status_start"],
               labels["l_computation_end"])
    duties = tuple(core.registers.read_full(register) for register in (23, 24, 0))
    return cycles, common_mode, duties


def test_profiler_reports_real_foc_stage_cycles_and_iep_ticks(tmp_path):
    """Profile the assembled FOC stages and cross-check them against IEP time."""
    result = _run_case(
        200.0,
        speed_pu=0.4,
        iq_pu=0.2,
        updates=3,
        directory=tmp_path,
    )

    assert tuple(result["stages"]) == tuple(STAGE_LABELS)
    assert set(result["stages"]) == {
        "entry_validation",
        "ramp",
        "phase_accumulator",
        "sine_lookup",
        "cosine_lookup",
        "inverse_park",
        "inverse_clarke",
        "svpwm_common_mode",
        "svpwm_duty_offset",
        "duty_clamp_status",
    }

    stage_cycles = [result["stages"][name]["cycles"]["min"] for name in STAGE_LABELS]
    assert all(cycles > 0 for cycles in stage_cycles)
    assert sum(stage_cycles) == result["regions"]["computation"]["cycles"]["min"]

    for name in STAGE_LABELS:
        stage = result["stages"][name]
        assert stage["iep_ticks"]["min"] == stage["cycles"]["min"]
        assert stage["iep_ticks"]["max"] == stage["cycles"]["max"]
        assert stage["microseconds_at_clock"]["min"] > 0


@pytest.mark.parametrize(
    ("clock_mhz", "period_ticks"),
    ((200.0, 2_000), (250.0, 2_500), (300.0, 3_000)),
)
def test_profiler_cross_checks_stage_time_with_iep_ticks(
    tmp_path, clock_mhz, period_ticks
):
    """IEP ticks and PRU cycles describe the same stage at each clock."""
    result = _run_case(
        clock_mhz,
        speed_pu=0.4,
        iq_pu=0.2,
        updates=2,
        directory=tmp_path,
    )

    assert result["period_ticks"] == period_ticks
    assert result["iep_tick_hz"] == clock_mhz * 1_000_000
    computation = result["regions"]["computation"]
    assert computation["iep_ticks"] == computation["cycles"]
    for name in STAGE_LABELS:
        stage = result["stages"][name]
        assert stage["microseconds_at_iep"]["min"] == pytest.approx(
            stage["microseconds_at_clock"]["min"]
        )


def test_optimized_foc_stages_meet_the_measured_budgets(tmp_path):
    """The real assembled valid path must hit each optimization budget."""
    result = _run_case(
        200.0,
        speed_pu=0.4,
        iq_pu=0.2,
        updates=3,
        directory=tmp_path,
    )

    assert result["stages"]["inverse_park"]["cycles"]["max"] <= 34
    assert result["stages"]["phase_accumulator"]["cycles"]["max"] <= 11
    assert result["stages"]["entry_validation"]["cycles"]["max"] <= 5
    assert result["stages"]["svpwm_common_mode"]["cycles"]["max"] <= 13
    assert result["stages"]["cosine_lookup"]["cycles"]["max"] <= 6
    assert result["stages"]["inverse_clarke"]["cycles"]["max"] <= 18
    assert result["regions"]["computation"]["cycles"]["max"] <= 199


def test_final_valid_path_matches_projected_stage_costs(tmp_path):
    """The final schedule reaches the projected 117-cycle valid path."""
    result = _run_case(
        200.0,
        speed_pu=0.4,
        iq_pu=0.2,
        updates=3,
        directory=tmp_path,
    )

    expected_cycles = {
        "entry_validation": 5,
        "ramp": 10,
        "phase_accumulator": 11,
        "sine_lookup": 5,
        "cosine_lookup": 6,
        "inverse_park": 34,
        "inverse_clarke": 17,
        "svpwm_common_mode": 13,
        "svpwm_duty_offset": 6,
        "duty_clamp_status": 10,
    }
    for name, cycles in expected_cycles.items():
        assert result["stages"][name]["cycles"]["min"] == cycles
    assert result["regions"]["computation"]["cycles"]["min"] == 117
    assert result["conservative_computation_cycles"] == 123


def test_profiler_reports_disabled_branch_without_fake_foc_stages(tmp_path):
    """The disabled path is timed separately and exposes no valid stages."""
    result = _run_case(
        200.0,
        speed_pu=0.4,
        iq_pu=0.2,
        updates=3,
        directory=tmp_path,
        enable=False,
        case_name="disabled",
    )
    assert result["case"] == "disabled"
    assert result["stages"] == {}
    assert result["regions"]["computation"]["cycles"]["max"] > 0


def test_profiler_reports_valid_boundary_and_invalid_branch_cases(tmp_path):
    """Profiler keeps valid stage data and reports invalid-path timing separately."""
    boundary = _run_case(
        200.0,
        speed_pu=0.4,
        iq_pu=0.5773,
        id_pu=0.0,
        ramp_pu=0.01,
        updates=2,
        directory=tmp_path,
        case_name="boundary",
    )
    assert boundary["case"] == "boundary"
    assert boundary["stages"]["inverse_park"]["cycles"]["max"] <= 41

    invalid = _run_case(
        200.0,
        speed_pu=0.4,
        iq_pu=0.5,
        id_pu=0.5,
        ramp_pu=0.01,
        updates=2,
        directory=tmp_path,
        case_name="invalid",
    )
    assert invalid["case"] == "invalid"
    assert invalid["stages"] == {}
    assert invalid["regions"]["computation"]["cycles"]["max"] > 0


def test_q24_xin_reads_have_a_mac_settling_instruction(nominal_config):
    """Every multiply-only read waits after its final operand write."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    labels = core._parser.labels
    phase = core.instructions[
        labels["l_phase_accumulator_start"]:labels["l_phase_accumulator_end"]
    ]
    phase_xins = [
        index for index, instruction in enumerate(phase)
        if instruction.opcode == "XIN"
        and instruction.source_text.lower().endswith(", 4")
    ]
    assert len(phase_xins) == 1
    assert phase[phase_xins[0] - 1].opcode == "NOP"

    for index, instruction in enumerate(core.instructions):
        if (
            instruction.opcode == "XIN"
            and instruction.source_text.lower().endswith(", 4")
        ):
            assert core.instructions[index - 1].opcode in {"NOP", "XOR"}


@pytest.mark.parametrize(
    "speed_raw",
    (
        0,
        to_q24(0.25),
        to_q24(-0.25),
        to_q24(1.0),
        to_q24(-1.0),
        1_234_567,
        -1_234_567,
    ),
)
def test_phase_accumulator_matches_exact_q24_product(nominal_config, speed_raw):
    """The settling fix preserves signed, fractional phase increments."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    labels = core._parser.labels
    core.registers.write_full(11, _wrap32(speed_raw))
    core.registers.write_full(12, 0)
    _run_stage(sim, labels["l_phase_accumulator_start"],
               labels["l_phase_accumulator_end"])
    assert core.registers.read_full(12) == _mul_q24_reference(
        speed_raw, abi.SPEED_SCALE
    )


def test_common_mode_removal_preserves_wrapped_result_and_duties(nominal_config):
    """Removing extrema unbiasing preserves common mode and clamped duties."""
    values = [
        (0, 0, 0),
        (Q24, -Q24, 0),
        (Q24, Q24, Q24),
        (-(1 << 31), (1 << 31) - 1, 0),
        (-(1 << 31), -1, 123),
        ((1 << 31) - 1, (1 << 31) - 1, (1 << 31) - 1),
        (-2 * Q24, Q24, 3 * Q24),
    ]
    rng = random.Random(0xC0FFEE)
    values.extend(
        tuple(rng.randint(-(1 << 31), (1 << 31) - 1) for _ in range(3))
        for _ in range(1_000)
    )

    for phases in values:
        cycles, common_mode, duties = _run_common_mode_case(
            nominal_config, phases
        )
        expected_common_mode = _reference_common_mode(phases)
        assert cycles == 13
        assert common_mode == expected_common_mode
        assert duties == _reference_clamped_duties(
            phases, expected_common_mode
        )


def _wait_for_ack(sim, generation, max_steps=20_000):
    ack_addr = abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF
    for _ in range(max_steps):
        sim.step("pru0", 1)
        if read_u32(sim, ack_addr) == generation:
            return
    raise AssertionError(f"generation {generation} was not acknowledged")


@pytest.mark.parametrize(
    ("id_ref", "iq_ref", "expected_signs"),
    (
        (0.2, 0.3, 0),
        (-0.2, 0.3, 0x101),
        (0.2, -0.3, 0x100),
        (-0.2, -0.3, 1),
        (0.0, -0.3, 0x100),
        (-0.2, 0.0, 0x101),
    ),
)
def test_configuration_adoption_caches_all_voltage_sign_combinations(
    nominal_config, id_ref, iq_ref, expected_signs
):
    """Each new generation refreshes the persistent inverse-Park signs."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.1,
        iq_ref=0.1,
        ramp_rate=0.0,
    )
    write_control(
        sim,
        abi_version=1,
        struct_size=abi.CONTROL_SIZE,
        enable=1,
        requested_generation=2,
        speed_ref_q24=0,
        id_ref_q24=to_q24(id_ref),
        iq_ref_q24=to_q24(iq_ref),
        ramp_rate_q24=0,
    )
    _wait_for_ack(sim, 2)
    assert sim.cores["pru0"].registers.read_full(18) == expected_signs


def test_invalid_to_valid_update_refreshes_cached_voltage_signs(nominal_config):
    """A rejected vector cannot leave stale signs after a valid replacement."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.5,
        iq_ref=0.5,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    assert core.registers.read_full(18) == 0

    write_control(
        sim,
        abi_version=1,
        struct_size=abi.CONTROL_SIZE,
        enable=1,
        requested_generation=2,
        speed_ref_q24=0,
        id_ref_q24=to_q24(-0.2),
        iq_ref_q24=to_q24(0.2),
        ramp_rate_q24=0,
    )
    _wait_for_ack(sim, 2)
    assert core.registers.read_full(18) == 0x101


def test_inverse_park_preserves_cached_voltage_signs(nominal_config):
    """Inverse Park consumes but does not overwrite the cached sign state."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    labels = core._parser.labels
    _seed_inverse_park_stage(
        sim, to_q24(-0.2), to_q24(0.3), to_q24(0.4), to_q24(-0.6)
    )
    before = core.registers.read_full(18)
    _run_stage(sim, labels["l_inverse_park_start"],
               labels["l_inverse_park_end"])
    assert core.registers.read_full(18) == before


def test_inverse_park_preserves_integer_product_truncation(nominal_config):
    """Random real-assembly inverse-Park outputs match independent Q24 math."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    rng = random.Random(0xF0C)

    cases = [
        (to_q24(0.125), to_q24(-0.375), to_q24(0.2), to_q24(-0.7)),
        (1234567, -7654321, to_q24(-0.5), to_q24(0.25)),
    ]
    for _ in range(100):
        cases.append(
            (
                rng.randrange(-Q24 * 45 // 100, Q24 * 45 // 100),
                rng.randrange(-Q24 * 45 // 100, Q24 * 45 // 100),
                rng.randrange(-Q24, Q24 + 1),
                rng.randrange(-Q24, Q24 + 1),
            )
        )

    for id_raw, iq_raw, sin_raw, cos_raw in cases:
        valpha, vbeta = _run_inverse_park_stage(
            sim, id_raw, iq_raw, sin_raw, cos_raw
        )
        expected_alpha = _wrap32(
            _signed32(_mul_q24_reference(id_raw, cos_raw))
            - _signed32(_mul_q24_reference(iq_raw, sin_raw))
        )
        expected_beta = _wrap32(
            _signed32(_mul_q24_reference(id_raw, sin_raw))
            + _signed32(_mul_q24_reference(iq_raw, cos_raw))
        )
        assert valpha == expected_alpha
        assert vbeta == expected_beta


def test_sine_and_cosine_lookup_cover_every_table_index(nominal_config):
    """The optimized byte-index path preserves all 2048 LUT entries."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    labels = core._parser.labels

    for index in range(abi.SINE_LUT_COUNT):
        expected_sine = to_q24(
            math.sin(2 * math.pi * index / abi.SINE_LUT_COUNT)
        ) & U32_MASK
        expected_cosine = to_q24(
            math.sin(
                2 * math.pi * ((index + abi.SINE_LUT_COUNT // 4)
                                % abi.SINE_LUT_COUNT)
                / abi.SINE_LUT_COUNT
            )
        ) & U32_MASK

        core.registers.write_full(12, index << 21)
        _run_stage(sim, labels["l_sine_lookup_start"],
                   labels["l_sine_lookup_end"])
        assert core.registers.read_full(17) == expected_sine

        core.registers.write_full(16, index)
        _run_stage(sim, labels["l_cosine_lookup_start"],
                   labels["l_cosine_lookup_end"])
        assert core.registers.read_full(29) == expected_cosine


def test_inverse_clarke_preserves_arithmetic_half_shift_edges(nominal_config):
    """Odd/even signed alpha values retain the old arithmetic-right-shift rule."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    labels = core._parser.labels

    for alpha in (-7, -3, -2, -1, 0, 1, 2, 3, 7):
        core.registers.write_full(19, alpha)
        core.registers.write_full(22, 0)
        _run_stage(sim, labels["l_inverse_clarke_start"],
                   labels["l_inverse_clarke_end"])
        expected_half = -(_signed32(alpha) >> 1)
        assert _signed32(core.registers.read_full(23)) == _signed32(alpha)
        assert _signed32(core.registers.read_full(24)) == expected_half
        assert _signed32(core.registers.read_full(0)) == expected_half


def test_inverse_park_does_not_modify_reserved_io_registers(nominal_config):
    """The optimized transform keeps R30/R31 and non-temporary state intact."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    sentinels = {
        register: 0xA5000000 + register
        for register in (*range(1, 7), 9, *range(11, 16), 30, 31)
    }
    for register, value in sentinels.items():
        core.registers.write_full(register, value)

    _run_inverse_park_stage(
        sim, to_q24(0.2), to_q24(-0.3), to_q24(0.4), to_q24(-0.6)
    )

    for register, value in sentinels.items():
        assert core.registers.read_full(register) == value


def test_duty_clamp_covers_each_direct_branch_input(nominal_config):
    """Every phase's negative and upper-bound clamp branch remains explicit."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.0,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    labels = core._parser.labels
    one = abi.Q_ONE
    cases = (
        ((-1, one // 2, one // 2), (0, one // 2, one // 2)),
        ((one + 1, one // 2, one // 2), (one, one // 2, one // 2)),
        ((one // 2, -1, one // 2), (one // 2, 0, one // 2)),
        ((one // 2, one + 1, one // 2), (one // 2, one, one // 2)),
        ((one // 2, one // 2, -1), (one // 2, one // 2, 0)),
        ((one // 2, one // 2, one + 1), (one // 2, one // 2, one)),
    )
    for (va, vb, vc), expected in cases:
        core.registers.write_full(23, va)
        core.registers.write_full(24, vb)
        core.registers.write_full(0, vc)
        _run_stage(sim, labels["l_duty_clamp_status_start"],
                   labels["l_computation_end"])
        assert tuple(core.registers.read_full(reg) for reg in (23, 24, 0)) == expected
        assert core.registers.read_full(16) & abi.STATUS_SATURATED


def test_valid_generation_refreshes_cached_voltage_magnitudes(nominal_config):
    """A valid-to-valid update refreshes the prescaled private cache."""
    sim = _configured_sim(
        nominal_config,
        speed_ref=0.0,
        id_ref=0.1,
        iq_ref=0.0,
        ramp_rate=0.0,
    )
    core = sim.cores["pru0"]
    core.registers.write_full(12, 0)
    loop_addr = abi.PWM_OUT_BASE + abi.PWM_OUT_LOOP_COUNTER_OFF
    valpha_addr = abi.PWM_OUT_BASE + abi.PWM_OUT_VALPHA_Q24_OFF

    previous_loop = read_u32(sim, loop_addr)
    for _ in range(20_000):
        sim.step("pru0", 1)
        if read_u32(sim, loop_addr) != previous_loop:
            break
    first = read_i32(sim, valpha_addr)
    previous_loop = read_u32(sim, loop_addr)

    write_control(
        sim,
        abi_version=1,
        struct_size=abi.CONTROL_SIZE,
        enable=1,
        requested_generation=2,
        speed_ref_q24=0,
        id_ref_q24=to_q24(0.2),
        iq_ref_q24=0,
        ramp_rate_q24=0,
    )
    for _ in range(20_000):
        sim.step("pru0", 1)
        if (
            read_u32(sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF)
            == 2
            and read_u32(sim, loop_addr) != previous_loop
        ):
            break
    second = read_i32(sim, valpha_addr)

    assert abs(first - to_q24(0.1)) <= 2
    assert abs(second - to_q24(0.2)) <= 2



def _configured_sim(nominal_config, *, generation=1, speed_ref=0.4,
                    id_ref=0.1, iq_ref=0.2, ramp_rate=0.01):
    sim = make_sim(nominal_config)
    seed_sine_lut(sim)
    write_control(
        sim,
        abi_version=1,
        struct_size=abi.CONTROL_SIZE,
        enable=1,
        requested_generation=generation,
        speed_ref_q24=to_q24(speed_ref),
        id_ref_q24=to_q24(id_ref),
        iq_ref_q24=to_q24(iq_ref),
        ramp_rate_q24=to_q24(ramp_rate),
    )
    for _ in range(10_000):
        sim.step("pru0", 1)
        if read_u32(sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF) == generation:
            return sim
    raise AssertionError("FOC configuration was not acknowledged")


def _step_to_region(sim, start_pc, end_pc):
    core = sim.cores["pru0"]
    for _ in range(100_000):
        if core.pc == start_pc:
            start_cycles = core.counters.cycles
            while core.pc != end_pc:
                sim.step("pru0", 1)
            return core.counters.cycles - start_cycles
        sim.step("pru0", 1)
    raise AssertionError("FOC computation region was not reached")


def test_foc_kernel_has_zero_cost_boundaries_and_fits_under_200_cycles(
    nominal_config,
):
    """The real assembled open-loop kernel must expose and meet its budget."""
    sim = _configured_sim(nominal_config)
    labels = sim.cores["pru0"]._parser.labels

    start = labels["l_computation_start"]
    end = labels["l_computation_end"]
    deadline = labels["l_deadline_start"]
    publication = labels["l_publication_start"]

    assert start < end <= deadline < publication
    assert _step_to_region(sim, start, end) <= 199


def test_foc_kernel_branch_paths_fit_under_200_cycles(nominal_config):
    """Exercise signed, ramp, quadrant, and near-boundary valid paths."""
    cases = (
        (0.4, 0.1, 0.2, 0.01),
        (0.4, -0.1, 0.2, 0.01),
        (0.0, 0.4, -0.3, 0.0),
        (0.0, -0.4, -0.3, 0.0),
        (0.0, 0.4, 0.4, 0.0),
        (0.0, -0.4, 0.4, 0.0),
    )
    cycles = []
    for speed_ref, id_ref, iq_ref, ramp_rate in cases:
        sim = _configured_sim(
            nominal_config,
            speed_ref=speed_ref,
            id_ref=id_ref,
            iq_ref=iq_ref,
            ramp_rate=ramp_rate,
        )
        labels = sim.cores["pru0"]._parser.labels
        cycles.append(
            _step_to_region(
                sim,
                labels["l_computation_start"],
                labels["l_computation_end"],
            )
        )

    assert max(cycles) <= 199


def test_foc_uses_one_persistent_multiply_mode_and_wide_result_reads(
    nominal_config,
):
    """The running firmware uses wide reads only for validation products."""
    sim = _configured_sim(nominal_config)
    instructions = sim.cores["pru0"].instructions

    assert sum(instr.opcode == "XOUT" for instr in instructions) == 1
    assert sum(
        instr.opcode == "XIN" and instr.source_text.lower().endswith(", 8")
        for instr in instructions
    ) == 2
    assert sum(
        instr.opcode == "XIN" and instr.source_text.lower().endswith(", 4")
        for instr in instructions
    ) == 6


def test_inverse_park_reuses_fixed_mac_operands_without_extra_moves(
    nominal_config,
):
    """The real inverse-Park sequence has only four operand-register moves."""
    sim = _configured_sim(nominal_config)
    core = sim.cores["pru0"]
    labels = core._parser.labels
    inverse_park = core.instructions[
        labels["l_inverse_park_start"]:labels["l_inverse_park_end"]
    ]

    assert sum(instr.opcode == "MOV" for instr in inverse_park) == 4


def test_foc_revalidates_voltage_when_a_new_generation_is_adopted(
    nominal_config,
):
    """Invalid and then valid voltage references are both handled coherently."""
    sim = _configured_sim(nominal_config, id_ref=0.5, iq_ref=0.5)
    pwm_status = abi.PWM_OUT_BASE + abi.PWM_OUT_STATUS_OFF
    theta_addr = abi.PWM_OUT_BASE + abi.PWM_OUT_THETA_CMD_U32_OFF

    for _ in range(100_000):
        sim.step("pru0", 1)
        if read_u32(sim, pwm_status) & abi.STATUS_INVALID_CONFIG:
            break
    else:
        raise AssertionError("invalid voltage reference was not reported")
    invalid_theta = read_u32(sim, theta_addr)
    invalid_loop = read_u32(
        sim, abi.PWM_OUT_BASE + abi.PWM_OUT_LOOP_COUNTER_OFF
    )

    write_control(
        sim,
        abi_version=1,
        struct_size=abi.CONTROL_SIZE,
        enable=1,
        requested_generation=2,
        speed_ref_q24=to_q24(0.4),
        id_ref_q24=to_q24(0.1),
        iq_ref_q24=to_q24(0.2),
        ramp_rate_q24=to_q24(0.01),
    )
    for _ in range(100_000):
        sim.step("pru0", 1)
        if (
            read_u32(sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF)
            == 2
            and read_u32(
                sim, abi.PWM_OUT_BASE + abi.PWM_OUT_LOOP_COUNTER_OFF
            )
            > invalid_loop
            and read_u32(sim, theta_addr) != invalid_theta
            and not (read_u32(sim, pwm_status) & abi.STATUS_INVALID_CONFIG)
        ):
            break
    else:
        raise AssertionError("valid replacement reference was not adopted")

    assert not (read_u32(sim, pwm_status) & abi.STATUS_INVALID_CONFIG)
