"""Host-side units and control-block staging for ``foc_open_loop.asm``.

The firmware works in per-unit fixed point. This module converts engineering
units and writes the host-owned words of the generated control ABI, bumping
``requested_generation`` last so the firmware adopts the whole block at once.
"""
import math

from pru_io import foc_control_abi as abi

CONTROL_UPDATE_HZ = 16_000
_HOST_BYTES = abi.ACK_GENERATION_OFFSET   # words below this are host-owned


def _finite(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _positive_pole_pairs(pole_pairs) -> int:
    if isinstance(pole_pairs, bool) or not isinstance(pole_pairs, int) or pole_pairs < 1:
        raise ValueError("pole_pairs must be a positive integer")
    return pole_pairs


def _update_rate(update_hz: float) -> float:
    rate = _finite("update rate", update_hz)
    if rate <= 0:
        raise ValueError("update rate must be positive")
    return rate


def update_frequency_hz(iep) -> float:
    """Actual update rate of the firmware's fixed 12,500-tick period."""
    return float(iep.active_clock_mhz) * 1_000_000 / 12_500


def speed_rpm_to_q28(rpm: float, pole_pairs: int,
                     *, update_hz: float = CONTROL_UPDATE_HZ) -> int:
    """Mechanical rpm -> phase increment; omitted rate means the nominal 16 kHz."""
    electrical_hz = _finite("speed", rpm) * _positive_pole_pairs(pole_pairs) / 60.0
    return round(electrical_hz / _update_rate(update_hz) * 2**32)


def speed_q28_to_rpm(value: int, pole_pairs: int,
                     *, update_hz: float = CONTROL_UPDATE_HZ) -> float:
    return (value / 2**32 * _update_rate(update_hz) * 60.0
            / _positive_pole_pairs(pole_pairs))


def ramp_rpm_s_to_q28(rpm_per_s: float, pole_pairs: int,
                      *, update_hz: float = CONTROL_UPDATE_HZ) -> int:
    """Acceleration in rpm/s -> phase-increment change per control update."""
    if _finite("acceleration", rpm_per_s) < 0:
        raise ValueError("acceleration must be zero or positive")
    rate = _update_rate(update_hz)
    return min(abi.SPEED_Q28_ONE, round(
        rpm_per_s * _positive_pole_pairs(pole_pairs) / 60.0 / rate**2 * 2**32))


def voltage_pu_to_q15(value: float) -> int:
    """Per-unit voltage (1 pu = the DC bus voltage as a phase amplitude)."""
    return round(_finite("voltage", value) * abi.VOLTAGE_Q15_ONE)


def read_control(memory) -> dict:
    return abi.unpack_config(memory.read(abi.CONTROL_ADDRESS, abi.CONFIG_SIZE)[0])


def build_control(memory, **updates) -> bytes:
    """Validate ``updates`` and return the host-owned words of the next block.

    Nothing is written. PRU-owned words (ack, status) are not part of the
    result; ``requested_generation`` is the current one plus one.
    """
    current = read_control(memory)
    fields = {name: current[name] for name in (
        "enable", "speed_ref_q28", "ramp_rate_q28", "vd_ref_q15", "vq_ref_q15",
        "initial_phase_q32")}
    unknown = set(updates) - fields.keys()
    if unknown:
        raise ValueError(f"unknown FOC control fields: {sorted(unknown)}")
    fields.update(updates)
    fields["requested_generation"] = (current["requested_generation"] + 1) & 0xFFFFFFFF
    return abi.pack_config(**fields)[:_HOST_BYTES]


def stage_control(memory, **updates) -> dict:
    """Apply ``updates`` to the host-owned ABI fields and publish a new generation.

    A rejected update leaves the block untouched; the firmware adopts the
    whole block at its next control update.
    """
    memory.write(abi.CONTROL_ADDRESS, build_control(memory, **updates))
    return read_control(memory)
