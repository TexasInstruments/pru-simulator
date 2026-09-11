"""Firmware<->Python cross-check tests for the open-loop FOC firmware
(source/foc_open_loop/foc_open_loop.asm), built and verified one macro at a
time per docs/superpowers/plans/2026-09-04-foc-pru-firmware.md.

Runs on pru0 only (single-PRU, no pairing). `nominal_config` pins the core
clock to 200 MHz per the plan.

numpy is not available in this environment (offline), so the reference
math (inverse Park, SVGEN, sin/cos) is written in pure Python instead of
reusing references/gan_shunt__current.py's numpy-based helpers directly --
the formulas are copied/matched by hand.
"""
import math
import pathlib

from simulator import Simulator
from pru_io import foc_abi as abi

ROOT = pathlib.Path(__file__).parent.parent
SOURCE = (ROOT / "source" / "foc_open_loop" / "foc_open_loop.asm").read_text()
INCLUDE_DIR = ROOT / "source"

Q24 = 1 << 24


def to_q24(x: float) -> int:
    """Python float -> signed Q24 int (struct-'i'-compatible, -2^31..2^31-1)."""
    return round(x * Q24)


def from_q24(v: int) -> float:
    """Signed Q24 int (raw 32-bit register value) -> Python float."""
    if v & 0x80000000:
        v -= 1 << 32
    return v / Q24


def make_sim(nominal_config):
    sim = Simulator(nominal_config)
    errors = sim.load("pru0", SOURCE, include_paths=[str(INCLUDE_DIR)])
    assert errors == [], errors
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    return sim


def write_control(sim, **fields):
    sim.memory.write(abi.CONTROL_BASE, abi.pack_control(**fields))


def seed_sine_lut(sim):
    """Host helper: seed the 2048-entry Q24 sine LUT (pure Python, no
    numpy). lut[i] = sin(2*pi*i/2048); cosine is read by the firmware as a
    512-entry (quarter-circle) offset into this same table."""
    words = bytearray()
    for i in range(abi.SINE_LUT_COUNT):
        angle = 2 * math.pi * i / abi.SINE_LUT_COUNT
        words += to_q24(math.sin(angle)).to_bytes(4, "little", signed=True)
    sim.memory.write(abi.SINE_LUT_BASE, bytes(words))


def read_u32(sim, addr):
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def read_i32(sim, addr):
    return int.from_bytes(sim.memory_read(addr, 4), "little", signed=True)


# ---------------------------------------------------------------------------
# A0 - config handshake
# ---------------------------------------------------------------------------

def test_config_handshake(nominal_config):
    sim = make_sim(nominal_config)
    write_control(
        sim,
        abi_version=1,
        struct_size=36,
        enable=1,
        requested_generation=1,
        speed_ref_q24=to_q24(0.5),
        id_ref_q24=to_q24(0.1),
        iq_ref_q24=to_q24(0.2),
        ramp_rate_q24=to_q24(0.01),
    )

    for _ in range(2000):
        sim.step("pru0", 1)
        ack = read_u32(sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF)
        if ack == 1:
            break
    else:
        raise AssertionError("pru_ack_generation never reached 1")

    regs = sim.registers("pru0")
    assert regs[6] == to_q24(0.5)     # SPEED_REF
    assert regs[7] == to_q24(0.1)     # ID_REF
    assert regs[8] == to_q24(0.2)     # IQ_REF
    assert regs[9] == to_q24(0.01)    # RAMP_RATE
    assert regs[10] == 1              # ENABLE


# ---------------------------------------------------------------------------
# A1 - MUL_Q24 broadside-MAC multiply helper
#
# Isolated via IPARK's Valpha = Id*cos - Iq*sin (added in task A4): with
# IqRef pinned to 0 and theta_acc pinned to 0 (SpeedRef=RampRate=0), Valpha
# reduces to exactly MUL_Q24(IdRef, cos(0)) -- and cos(0) is just
# sine_lut[512] (the quarter-table cosine sample for index 0), which this
# helper pokes directly to an arbitrary test operand instead of seeding the
# whole table. This lets test_mul_q24 feed arbitrary (a, b) pairs -- incl.
# negative operands -- straight into the real MUL_Q24 call sites.
# ---------------------------------------------------------------------------

def _run_mul_q24_case(nominal_config, a: float, b: float) -> int:
    """Drive MUL_Q24(a, b) through the real IPARK call site and return the
    raw pwm_out.valpha_q24 word."""
    sim = make_sim(nominal_config)
    write_control(
        sim,
        abi_version=1,
        struct_size=36,
        enable=1,
        requested_generation=1,
        speed_ref_q24=0,
        id_ref_q24=to_q24(a),
        iq_ref_q24=0,
        ramp_rate_q24=0,
    )
    # sine_lut[512] is theta_acc=0's cosine sample (index 0 + quarter-table
    # offset 512); sine_lut[0] (the sine sample) is irrelevant since IqRef=0.
    sim.memory.write(abi.SINE_LUT_BASE + 512 * abi.SINE_LUT_SIZE,
                      to_q24(b).to_bytes(4, "little", signed=True))
    for _ in range(2000):
        sim.step("pru0", 1)
        if read_u32(sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF) == 1:
            break
    else:
        raise AssertionError("pru_ack_generation never reached 1")
    addr = abi.PWM_OUT_BASE + abi.PWM_OUT_VALPHA_Q24_OFF
    for _ in range(5000):
        sim.step("pru0", 1)
        if read_u32(sim, addr) != 0:
            break
    return read_i32(sim, addr)


def test_mul_q24_positive_operands(nominal_config):
    result = _run_mul_q24_case(nominal_config, 0.5, 0.25)
    expected = to_q24(0.5 * 0.25)
    assert abs(result - expected) <= 1


def test_mul_q24_negative_operand(nominal_config):
    """Guards the signed-product path: one operand negative."""
    result = _run_mul_q24_case(nominal_config, 0.5, -0.25)
    expected = to_q24(-0.5 * 0.25)
    assert abs(result - expected) <= 1


def test_mul_q24_both_negative_operands(nominal_config):
    result = _run_mul_q24_case(nominal_config, -0.5, -0.25)
    expected = to_q24(0.5 * 0.25)
    assert abs(result - expected) <= 1


# ---------------------------------------------------------------------------
# A2 - RC (ramp control) + RG (ramp generator) -> theta_cmd
# ---------------------------------------------------------------------------

THETA_CMD_ADDR = abi.PWM_OUT_BASE + abi.PWM_OUT_THETA_CMD_U32_OFF


def _make_configured_sim(nominal_config, speed_ref, ramp_rate, id_ref=0.0, iq_ref=0.0):
    sim = make_sim(nominal_config)
    write_control(
        sim,
        abi_version=1,
        struct_size=36,
        enable=1,
        requested_generation=1,
        speed_ref_q24=to_q24(speed_ref),
        id_ref_q24=to_q24(id_ref),
        iq_ref_q24=to_q24(iq_ref),
        ramp_rate_q24=to_q24(ramp_rate),
    )
    for _ in range(2000):
        sim.step("pru0", 1)
        if read_u32(sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF) == 1:
            break
    else:
        raise AssertionError("pru_ack_generation never reached 1")
    return sim


def _run_iterations(sim, count, max_steps_per_iter=5000):
    """Step through `count` completed l_core_loop passes (each pass writes
    theta_cmd_u32 exactly once), returning the theta_cmd value observed at
    the end of each pass."""
    results = []
    prev = read_u32(sim, THETA_CMD_ADDR)
    for _ in range(count):
        for _ in range(max_steps_per_iter):
            sim.step("pru0", 1)
            cur = read_u32(sim, THETA_CMD_ADDR)
            if cur != prev:
                break
        else:
            raise AssertionError("theta_cmd_u32 never changed within one pass")
        results.append(cur)
        prev = cur
    return results


def test_ramp_generates_rotating_theta(nominal_config):
    # ---- monotonic advance + slew (no instantaneous jump to target) ----
    sim = _make_configured_sim(nominal_config, speed_ref=0.5, ramp_rate=0.01)
    thetas = _run_iterations(sim, 20)
    deltas = [(thetas[i] - thetas[i - 1]) & 0xFFFFFFFF for i in range(1, len(thetas))]
    assert all(d > 0 for d in deltas), deltas          # monotonic (no wrap yet)
    # RC is still slewing setpoint upward over these 20 iterations (ramp_rate
    # is small relative to speed_ref), so successive step sizes must grow --
    # a firmware that jumped straight to the target on iteration 1 would
    # instead show a constant delta from the very first sample.
    assert deltas[-1] > deltas[0], deltas

    # ---- step size scales with SpeedRef (RAMP_RATE >> both SpeedRefs, so
    # RC clamps to the target on iteration 1 -- isolates RG's scaling from
    # RC's slew) ----
    sim_slow = _make_configured_sim(nominal_config, speed_ref=0.3, ramp_rate=10.0)
    sim_fast = _make_configured_sim(nominal_config, speed_ref=0.9, ramp_rate=10.0)
    slow_thetas = _run_iterations(sim_slow, 2)
    fast_thetas = _run_iterations(sim_fast, 2)
    slow_delta = (slow_thetas[1] - slow_thetas[0]) & 0xFFFFFFFF
    fast_delta = (fast_thetas[1] - fast_thetas[0]) & 0xFFFFFFFF
    assert slow_delta > 0
    assert abs(fast_delta / slow_delta - 3.0) < 0.05, (slow_delta, fast_delta)

    # ---- 32-bit wrap: preset THETA_ACC near the boundary and confirm the
    # next iteration wraps around (new theta_cmd < the preset value) ----
    sim_wrap = _make_configured_sim(nominal_config, speed_ref=0.5, ramp_rate=10.0)
    sim_wrap.cores["pru0"].registers.write_full(12, 0xFFFFFFF0)  # THETA_ACC
    wrapped = _run_iterations(sim_wrap, 1)[0]
    assert wrapped < 0xFFFFFFF0


# ---------------------------------------------------------------------------
# A3 - sin/cos from the 2048-entry Q24 LUT
# ---------------------------------------------------------------------------

def _sincos_at_theta(nominal_config, theta_acc: int):
    """Preset THETA_ACC (r12) directly and freeze it there (SpeedRef=0,
    RampRate=0 -> RG's freq is always 0), then run the real lookup stages
    directly so the raw SIN_Q24/COS_Q24 values are captured before inverse
    Park reuses those registers for magnitudes."""
    sim = _make_configured_sim(nominal_config, speed_ref=0.0, ramp_rate=0.0)
    seed_sine_lut(sim)
    core = sim.cores["pru0"]
    labels = core._parser.labels
    core.registers.write_full(12, theta_acc)  # THETA_ACC
    core.pc = labels["l_sine_lookup_start"]
    for _ in range(100):
        if core.pc == labels["l_cosine_lookup_end"]:
            break
        sim.step("pru0", 1)
    else:
        raise AssertionError("sin/cos lookup did not finish")
    regs = sim.registers("pru0")
    return regs[17], regs[29]  # SIN_Q24, COS_Q24 / MAC operand B


def test_sincos_lut(nominal_config):
    LUT_SHIFT = 21
    for i in range(16):
        theta_acc = (i * (1 << 32)) // 16
        index = theta_acc >> LUT_SHIFT
        expected_angle = 2 * math.pi * index / abi.SINE_LUT_COUNT

        sin_raw, cos_raw = _sincos_at_theta(nominal_config, theta_acc)
        sin_val = from_q24(sin_raw)
        cos_val = from_q24(cos_raw)

        # Nearest-neighbor LUT: exact vs. the table's own sample at `index`
        # (quantization tolerance is the LUT-fill's own Q24 rounding, not
        # the continuous angle -- theta_acc is an exact multiple of the
        # LUT bucket width here, so there is no bucket-boundary slop).
        assert abs(sin_val - math.sin(expected_angle)) < 1e-6
        assert abs(cos_val - math.cos(expected_angle)) < 1e-6
        assert abs(sin_val * sin_val + cos_val * cos_val - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# A4 - IPARK (inverse Park): Valpha, Vbeta
# ---------------------------------------------------------------------------

def _run_ipark_case(nominal_config, id_ref: float, iq_ref: float, theta_acc: int):
    sim = _make_configured_sim(nominal_config, speed_ref=0.0, ramp_rate=0.0,
                                id_ref=id_ref, iq_ref=iq_ref)
    seed_sine_lut(sim)
    sim.cores["pru0"].registers.write_full(12, theta_acc)  # THETA_ACC
    sim.step("pru0", 5000)
    valpha = from_q24(read_u32(sim, abi.PWM_OUT_BASE + abi.PWM_OUT_VALPHA_Q24_OFF))
    vbeta = from_q24(read_u32(sim, abi.PWM_OUT_BASE + abi.PWM_OUT_VBETA_Q24_OFF))
    return valpha, vbeta


def test_ipark(nominal_config):
    LUT_SHIFT = 21
    id_iq_grid = [(0.5, 0.0), (0.0, 0.5), (0.3, -0.2), (-0.4, 0.1), (0.4, 0.4)]
    theta_fracs = [0.0, 1 / 8, 1 / 4, 3 / 8, 1 / 2, 5 / 8, 3 / 4, 7 / 8]

    for id_ref, iq_ref in id_iq_grid:
        for frac in theta_fracs:
            theta_acc = int(frac * (1 << 32)) & 0xFFFFFFFF
            index = theta_acc >> LUT_SHIFT
            # The exact discrete angle the firmware's nearest-neighbor LUT
            # sampled at this theta_acc -- matching it exactly isolates the
            # IPARK math from the LUT's own quantization error.
            angle = 2 * math.pi * index / abi.SINE_LUT_COUNT

            valpha, vbeta = _run_ipark_case(nominal_config, id_ref, iq_ref, theta_acc)
            expected_valpha = id_ref * math.cos(angle) - iq_ref * math.sin(angle)
            expected_vbeta = id_ref * math.sin(angle) + iq_ref * math.cos(angle)

            assert abs(valpha - expected_valpha) < 5e-5, (id_ref, iq_ref, frac)
            assert abs(vbeta - expected_vbeta) < 5e-5, (id_ref, iq_ref, frac)


# ---------------------------------------------------------------------------
# A5 - SVGEN (SVPWM): Ta, Tb, Tc
# ---------------------------------------------------------------------------

LUT_SHIFT = 21


def svgen_reference(valpha: float, vbeta: float):
    """Pure-Python SVGEN matching the conventional phase mapping."""
    va = valpha
    vb = -0.5 * valpha + (math.sqrt(3) / 2) * vbeta
    vc = -0.5 * valpha - (math.sqrt(3) / 2) * vbeta
    vcom = 0.5 * (max(va, vb, vc) + min(va, vb, vc))
    return va - vcom + 0.5, vb - vcom + 0.5, vc - vcom + 0.5, vcom


def _run_svgen_case(nominal_config, id_ref: float, iq_ref: float, theta_acc: int):
    sim = _make_configured_sim(nominal_config, speed_ref=0.0, ramp_rate=0.0,
                                id_ref=id_ref, iq_ref=iq_ref)
    seed_sine_lut(sim)
    sim.cores["pru0"].registers.write_full(12, theta_acc)  # THETA_ACC
    sim.step("pru0", 5000)
    ta = from_q24(read_u32(sim, abi.PWM_OUT_BASE + abi.PWM_OUT_TA_Q24_OFF))
    tb = from_q24(read_u32(sim, abi.PWM_OUT_BASE + abi.PWM_OUT_TB_Q24_OFF))
    tc = from_q24(read_u32(sim, abi.PWM_OUT_BASE + abi.PWM_OUT_TC_Q24_OFF))
    return ta, tb, tc


def test_svgen(nominal_config):
    id_iq_grid = [(0.3, 0.2), (0.5, 0.0), (0.0, 0.4), (-0.2, 0.3), (0.4, -0.3)]
    theta_fracs = [0.0, 1 / 8, 1 / 4, 3 / 8, 1 / 2, 5 / 8, 3 / 4, 7 / 8]

    for id_ref, iq_ref in id_iq_grid:
        for frac in theta_fracs:
            theta_acc = int(frac * (1 << 32)) & 0xFFFFFFFF
            index = theta_acc >> LUT_SHIFT
            angle = 2 * math.pi * index / abi.SINE_LUT_COUNT
            valpha = id_ref * math.cos(angle) - iq_ref * math.sin(angle)
            vbeta = id_ref * math.sin(angle) + iq_ref * math.cos(angle)
            ta_ref, tb_ref, tc_ref, vcom = svgen_reference(valpha, vbeta)

            ta, tb, tc = _run_svgen_case(nominal_config, id_ref, iq_ref, theta_acc)

            assert abs(ta - ta_ref) < 5e-5, (id_ref, iq_ref, frac)
            assert abs(tb - tb_ref) < 5e-5, (id_ref, iq_ref, frac)
            assert abs(tc - tc_ref) < 5e-5, (id_ref, iq_ref, frac)
            for duty in (ta, tb, tc):
                assert 0.0 <= duty <= 1.0, (id_ref, iq_ref, frac, ta, tb, tc)
            # Common-mode identity: Va+Vb+Vc==0 always (Clarke identity), so
            # Ta+Tb+Tc == 1.5 - 3*Vcom.
            assert abs((ta + tb + tc) - (1.5 - 3 * vcom)) < 1e-4, (id_ref, iq_ref, frac)


def test_full_loop_pwm(nominal_config):
    """A full electrical revolution -> three duties tracing the classic
    SVPWM saddle waveforms, phase-shifted 120 degrees apart."""
    id_ref, iq_ref = 0.3, 0.2
    n_samples = 48
    ta_samples, tb_samples, tc_samples = [], [], []

    for i in range(n_samples):
        theta_acc = (i * (1 << 32)) // n_samples
        index = theta_acc >> LUT_SHIFT
        angle = 2 * math.pi * index / abi.SINE_LUT_COUNT
        valpha = id_ref * math.cos(angle) - iq_ref * math.sin(angle)
        vbeta = id_ref * math.sin(angle) + iq_ref * math.cos(angle)
        ta_ref, tb_ref, tc_ref, _ = svgen_reference(valpha, vbeta)

        ta, tb, tc = _run_svgen_case(nominal_config, id_ref, iq_ref, theta_acc)
        assert abs(ta - ta_ref) < 5e-5
        assert abs(tb - tb_ref) < 5e-5
        assert abs(tc - tc_ref) < 5e-5
        assert 0.0 <= ta <= 1.0 and 0.0 <= tb <= 1.0 and 0.0 <= tc <= 1.0

        ta_samples.append(ta)
        tb_samples.append(tb)
        tc_samples.append(tc)

    # All three duties are not identical (genuinely phase-different, not a
    # bug that collapsed them to the same waveform)...
    assert ta_samples != tb_samples
    assert ta_samples != tc_samples
    # ...but trace the same saddle-waveform shape (same min/max/mean),
    # consistent with a 120-degree phase shift of one common waveform.
    assert abs(max(ta_samples) - max(tb_samples)) < 1e-3
    assert abs(max(ta_samples) - max(tc_samples)) < 1e-3
    assert abs(min(ta_samples) - min(tb_samples)) < 1e-3
    assert abs(min(ta_samples) - min(tc_samples)) < 1e-3
    mean_ta = sum(ta_samples) / n_samples
    mean_tb = sum(tb_samples) / n_samples
    mean_tc = sum(tc_samples) / n_samples
    assert abs(mean_ta - mean_tb) < 1e-3
    assert abs(mean_ta - mean_tc) < 1e-3
