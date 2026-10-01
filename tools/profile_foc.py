"""Profile the real assembled open-loop FOC kernel.

The command deliberately does not install the simulator's IEP timer shortcut.
It reports instruction, modeled-stall, total-cycle, and IEP-tick bounds for each
region and each valid-reference FOC stage using zero-cost labels in the firmware.
It also measures the disabled branch and derives a conservative valid-path bound
by taking the longest modeled clamp branch independently for each phase.
"""

from __future__ import annotations

import argparse
import configparser
import json
import math
import pathlib
import shutil
import tempfile

from pru_io import foc_abi as abi
from simulator import Simulator


ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "source" / "foc_open_loop" / "foc_open_loop.asm").read_text()
REGIONS = ("configuration", "input", "computation", "deadline", "publication")
STAGE_LABELS = (
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
)
STAGE_BOUNDARY_LABELS = {
    "entry_validation": ("l_computation_start", "l_entry_validation_end"),
    "ramp": ("l_ramp_start", "l_ramp_end"),
    "phase_accumulator": (
        "l_phase_accumulator_start",
        "l_phase_accumulator_end",
    ),
    "sine_lookup": ("l_sine_lookup_start", "l_sine_lookup_end"),
    "cosine_lookup": ("l_cosine_lookup_start", "l_cosine_lookup_end"),
    "inverse_park": ("l_inverse_park_start", "l_inverse_park_end"),
    "inverse_clarke": ("l_inverse_clarke_start", "l_inverse_clarke_end"),
    "svpwm_common_mode": (
        "l_svpwm_common_mode_start",
        "l_svpwm_common_mode_end",
    ),
    "svpwm_duty_offset": (
        "l_svpwm_duty_offset_start",
        "l_svpwm_duty_offset_end",
    ),
    "duty_clamp_status": (
        "l_duty_clamp_status_start",
        "l_computation_end",
    ),
}
_U64_MASK = (1 << 64) - 1


def _to_q24(value: float) -> int:
    return round(value * (1 << abi.Q_FRACTION_BITS))


def _read_u32(sim: Simulator, address: int) -> int:
    return int.from_bytes(sim.memory_read(address, 4), "little")


def _config_path(clock_mhz: float, directory: pathlib.Path) -> str:
    config = configparser.ConfigParser()
    config.read(ROOT / "memory.cfg")
    config.setdefault("device", {})
    config["device"]["pru_clock_mhz"] = str(clock_mhz)
    config["device"]["pru1_clock_mhz"] = str(clock_mhz)
    path = directory / "memory.cfg"
    with path.open("w") as stream:
        config.write(stream)
    shutil.copytree(ROOT / "config", directory / "config", dirs_exist_ok=True)
    return str(path)


def _seed_sine_lut(sim: Simulator) -> None:
    words = bytearray()
    for index in range(abi.SINE_LUT_COUNT):
        sample = _to_q24( math.sin(2 * math.pi * index / abi.SINE_LUT_COUNT))
        words += sample.to_bytes(4, "little", signed=True)
    sim.memory.write(abi.SINE_LUT_BASE, words)


def _snapshot(sim: Simulator, core) -> dict[str, int]:
    return {
        "cycles": core.counters.cycles,
        "instructions": core.counters.instruction_count,
        "stalls": core.counters.stall_cycles,
        "iep_ticks": int(sim.iep.count),
    }


def _delta(start: dict[str, int], end: dict[str, int]) -> dict[str, int]:
    delta = {
        field: end[field] - start[field]
        for field in ("cycles", "instructions", "stalls")
    }
    delta["iep_ticks"] = (end["iep_ticks"] - start["iep_ticks"]) & _U64_MASK
    return delta


def _run_label_range(sim: Simulator, core, start: int, end: int) -> int:
    """Run a real assembled label range and return its modeled cycles."""
    core.pc = start
    start_cycles = core.counters.cycles
    for _ in range(1_000):
        if core.pc == end:
            return core.counters.cycles - start_cycles
        sim.step("pru0", 1)
    raise RuntimeError(f"label range {start}->{end} did not finish")


def _measure_conservative_clamp_bound(sim: Simulator, core, labels: dict) -> dict:
    """Measure independent longest clamp branches from the expanded firmware."""
    start = labels["l_duty_clamp_status_start"]
    end = labels["l_computation_end"]
    one = abi.Q_ONE
    in_range = one // 2

    def measure(values: tuple[int, int, int]) -> int:
        for register, value in zip((23, 24, 0), values):
            core.registers.write_full(register, value)
        return _run_label_range(sim, core, start, end)

    baseline = measure((in_range, in_range, in_range))
    phase_maxima = []
    for phase in range(3):
        candidates = []
        for branch_value in (-1, one + 1):
            values = [in_range, in_range, in_range]
            values[phase] = branch_value
            candidates.append(measure(tuple(values)))
        phase_maxima.append(max(candidates))

    conservative = baseline + sum(
        phase_cycles - baseline for phase_cycles in phase_maxima
    )
    return {
        "baseline_cycles": baseline,
        "per_phase_max_cycles": phase_maxima,
        "conservative_cycles": conservative,
    }


def _run_case(clock_mhz: float, speed_pu: float, iq_pu: float,
              updates: int, directory: pathlib.Path, *, id_pu: float = 0.1,
              ramp_pu: float = 0.01, case_name: str | None = None,
              enable: bool = True) -> dict:
    config_path = _config_path(clock_mhz, directory)
    sim = Simulator(config_path)
    errors = sim.load("pru0", SOURCE, include_paths=[str(ROOT / "source")])
    if errors:
        raise RuntimeError("FOC assembly failed to load: " + "; ".join(errors))
    sim.iep.write_iepclk(1)
    sim.iep.write_global_cfg(0x11)
    _seed_sine_lut(sim)

    period_ticks = round(clock_mhz * 1_000_000 / abi.CONTROL_LOOP_HZ)
    sim.memory.write(
        abi.CONTROL_BASE,
        abi.pack_control(
            abi_version=abi.ABI_VERSION,
            struct_size=abi.CONTROL_SIZE,
            enable=int(enable),
            requested_generation=1,
            speed_ref_q24=_to_q24(speed_pu),
            id_ref_q24=_to_q24(id_pu),
            iq_ref_q24=_to_q24(iq_pu),
            ramp_rate_q24=_to_q24(ramp_pu),
            control_period_iep_ticks=period_ticks,
        ),
    )

    core = sim.cores["pru0"]
    labels = core._parser.labels
    config_start = labels["l_config_start"]
    config_end = labels["l_config_end"]
    input_start = labels["l_input_load_start"]
    computation_start = labels["l_computation_start"]
    disabled_start = labels["l_disabled_output"]
    computation_end = labels["l_computation_end"]
    publication_start = labels["l_publication_start"]
    stage_boundaries = {
        name: (labels[start], labels[end])
        for name, (start, end) in STAGE_BOUNDARY_LABELS.items()
    }
    current: dict[str, dict[str, int]] = {}
    current_stages: dict[str, dict[str, int]] = {}
    stage_samples: dict[str, dict[str, int]] = {}
    config_pending = False
    config_entry: dict[str, int] | None = None
    samples: list[dict[str, dict[str, int]]] = []
    config_samples: list[dict[str, int]] = []

    def finalize(end_snapshot: dict[str, int]) -> None:
        if len(samples) >= updates:
            return
        required = ("input", "computation", "end", "publication")
        if not all(name in current for name in required):
            return
        sample = {
            "input": _delta(current["input"], current["computation"]),
            "computation": _delta(current["computation"], current["end"]),
            "deadline": _delta(current["end"], current["publication"]),
            "publication": _delta(current["publication"], end_snapshot),
        }
        sample["active"] = _delta(current["input"], end_snapshot)
        sample["stages"] = dict(stage_samples)
        samples.append(sample)

    for _ in range(2_000_000):
        pc = core.pc
        snapshot = _snapshot(sim, core)

        if pc == config_start:
            finalize(snapshot)
            current = {}
            current_stages = {}
            stage_samples = {}
            requested = _read_u32(
                sim, abi.CONTROL_BASE + abi.CONTROL_REQUESTED_GENERATION_OFF
            )
            acknowledged = _read_u32(
                sim, abi.CONTROL_BASE + abi.CONTROL_PRU_ACK_GENERATION_OFF
            )
            config_pending = requested != acknowledged
            config_entry = snapshot if config_pending else None

        if pc == config_end and config_pending and config_entry is not None:
            config_samples.append(_delta(config_entry, snapshot))
            config_pending = False
            config_entry = None

        if pc == input_start:
            current["input"] = snapshot
        elif pc == computation_start:
            current["computation"] = snapshot
        elif pc == disabled_start:
            current["computation"] = snapshot
        elif pc == computation_end:
            current["end"] = snapshot
        elif pc == publication_start:
            current["publication"] = snapshot

        for name in STAGE_LABELS:
            start_pc, end_pc = stage_boundaries[name]
            if pc == start_pc:
                current_stages[name] = snapshot
            if pc == end_pc and name in current_stages:
                stage_samples[name] = _delta(current_stages[name], snapshot)

        sim.step("pru0", 1)
        if len(samples) >= updates:
            break
    else:
        raise RuntimeError(f"did not collect {updates} FOC updates")

    if len(samples) < updates:
        raise RuntimeError(f"collected only {len(samples)} FOC updates")

    result = {
        "clock_mhz": clock_mhz,
        "iep_clock_hz": float(sim.iep.active_clock_hz),
        "iep_tick_hz": float(sim.iep.active_clock_hz * sim.iep.default_increment),
        "configured_control_hz": abi.CONTROL_LOOP_HZ,
        "period_ticks": period_ticks,
        "case": case_name or ("reverse" if speed_pu < 0 else "nominal"),
        "updates": updates,
        "configuration": config_samples[0] if config_samples else {},
        "regions": {},
        "stages": {},
        "conservative_computation_cycles": None,
        "clamp_branch_bound": None,
    }
    if result["configuration"]:
        result["configuration"]["microseconds_at_clock"] = {
            "min": result["configuration"]["cycles"] / clock_mhz,
            "max": result["configuration"]["cycles"] / clock_mhz,
        }
        result["configuration"]["microseconds_at_iep"] = {
            "min": result["configuration"]["iep_ticks"] / result["iep_tick_hz"] * 1_000_000,
            "max": result["configuration"]["iep_ticks"] / result["iep_tick_hz"] * 1_000_000,
        }
    for region in (*REGIONS[1:], "active"):
        values = [sample[region] for sample in samples]
        result["regions"][region] = {
            field: {
                "min": min(value[field] for value in values),
                "max": max(value[field] for value in values),
            }
            for field in ("cycles", "instructions", "stalls")
            + ("iep_ticks",)
        }
        result["regions"][region]["microseconds_at_clock"] = {
            "min": result["regions"][region]["cycles"]["min"] / clock_mhz,
            "max": result["regions"][region]["cycles"]["max"] / clock_mhz,
        }
        result["regions"][region]["microseconds_at_iep"] = {
            "min": result["regions"][region]["iep_ticks"]["min"] / result["iep_tick_hz"] * 1_000_000,
            "max": result["regions"][region]["iep_ticks"]["max"] / result["iep_tick_hz"] * 1_000_000,
        }
    for name in STAGE_LABELS:
        values = [sample["stages"][name] for sample in samples
                  if name in sample["stages"]]
        if not values:
            continue
        result["stages"][name] = {
            field: {
                "min": min(value[field] for value in values),
                "max": max(value[field] for value in values),
            }
            for field in ("cycles", "instructions", "stalls", "iep_ticks")
        }
        result["stages"][name]["microseconds_at_clock"] = {
            "min": result["stages"][name]["cycles"]["min"] / clock_mhz,
            "max": result["stages"][name]["cycles"]["max"] / clock_mhz,
        }
        result["stages"][name]["microseconds_at_iep"] = {
            "min": result["stages"][name]["iep_ticks"]["min"] / result["iep_tick_hz"] * 1_000_000,
            "max": result["stages"][name]["iep_ticks"]["max"] / result["iep_tick_hz"] * 1_000_000,
        }
    if result["stages"]:
        clamp_bound = _measure_conservative_clamp_bound(sim, core, labels)
        fixed_cycles = sum(
            result["stages"][name]["cycles"]["max"]
            for name in STAGE_LABELS
            if name != "duty_clamp_status"
        )
        result["clamp_branch_bound"] = clamp_bound
        result["conservative_computation_cycles"] = (
            fixed_cycles + clamp_bound["conservative_cycles"]
        )
    return result


def _print_result(result: dict) -> None:
    print(
        f"{result['case']}: {result['clock_mhz']:.0f} MHz, "
        f"IEP {result['iep_tick_hz']:.0f} ticks/s, "
        f"{result['period_ticks']} ticks/update"
    )
    for name in ("configuration", "input", "computation", "deadline",
                 "publication", "active"):
        values = result["configuration"] if name == "configuration" else result["regions"][name]
        cycles = values.get("cycles", {})
        if isinstance(cycles, int):
            cycles = {"min": cycles, "max": cycles}
            instructions = {
                "min": values["instructions"], "max": values["instructions"]
            }
            stalls = {"min": values["stalls"], "max": values["stalls"]}
        else:
            instructions = values["instructions"]
            stalls = values["stalls"]
        if not cycles:
            print(f"  {name:13} unavailable")
            continue
        micros = values["microseconds_at_clock"]
        print(
            f"  {name:13} cycles {cycles['min']:4}-{cycles['max']:4}, "
            f"instructions {instructions['min']:4}-{instructions['max']:4}, "
            f"stalls {stalls['min']:4}-{stalls['max']:4}, "
            f"time {micros['min']:.3f}-{micros['max']:.3f} us"
        )
    if result["stages"]:
        print("  FOC stages")
        for name in STAGE_LABELS:
            if name not in result["stages"]:
                print(f"    {name:21} unavailable on this branch")
                continue
            values = result["stages"][name]
            print(
                f"    {name:21} cycles {values['cycles']['min']:4}-"
                f"{values['cycles']['max']:4}, "
                f"IEP ticks {values['iep_ticks']['min']:4}-"
                f"{values['iep_ticks']['max']:4}, "
                f"time {values['microseconds_at_clock']['min']:.3f}-"
                f"{values['microseconds_at_clock']['max']:.3f} us"
            )
    else:
        print("  FOC stages unavailable on this branch")
    if result["conservative_computation_cycles"] is not None:
        bound = result["clamp_branch_bound"]
        print(
            "  conservative valid computation bound: "
            f"{result['conservative_computation_cycles']} cycles "
            f"(clamp baseline {bound['baseline_cycles']}, "
            f"per-phase maxima {bound['per_phase_max_cycles']})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock", type=float, default=200.0)
    parser.add_argument("--updates", type=int, default=20)
    parser.add_argument(
        "--case",
        choices=(
            "nominal", "reverse", "settled", "ramp_clamped",
            "boundary", "invalid", "disabled",
        ),
        action="append", default=None,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = []
    cases = args.case or ["nominal"]
    case_config = {
        "nominal": (0.4, 0.1, 0.2, 0.01),
        "reverse": (-0.4, 0.1, -0.2, 0.01),
        "settled": (0.4, 0.0, 0.2, 1.0),
        "ramp_clamped": (0.4, -0.1, 0.2, 0.5),
        "boundary": (0.4, 0.0, 0.5773, 0.01),
        "invalid": (0.4, 0.5, 0.5, 0.01),
        "disabled": (0.4, 0.1, 0.2, 0.01),
    }
    with tempfile.TemporaryDirectory(prefix="foc-profile-") as temp:
        directory = pathlib.Path(temp)
        for case in cases:
            speed, id_ref, iq, ramp = case_config[case]
            results.append(
                _run_case(
                    args.clock,
                    speed,
                    iq,
                    args.updates,
                    directory,
                    id_pu=id_ref,
                    ramp_pu=ramp,
                    case_name=case,
                    enable=case != "disabled",
                )
            )
    if args.json:
        print(json.dumps(results, indent=2, sort_keys=True))
    else:
        for result in results:
            _print_result(result)


if __name__ == "__main__":
    main()
