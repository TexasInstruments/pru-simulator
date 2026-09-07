"""Run the physical open-loop FOC acceptance cases against real assembly."""

from __future__ import annotations

import argparse
import configparser
import json
import math
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from pru_io import foc_abi as abi
from pru_io.foc_runtime import FocRuntime
from simulator import Simulator


FOC_SOURCE = (ROOT / "source" / "foc_open_loop" / "foc_open_loop.asm").read_text()
MASK32 = (1 << 32) - 1


CASES = {
    "nominal_400": {
        "speed_rpm": 400.0, "vd": 0.0, "vq": 0.25,
        "acceleration": 1000.0, "duration": 1.0,
    },
    "reference_change": {
        "speed_rpm": 200.0, "vd": 0.0, "vq": 0.25,
        "acceleration": 1000.0, "duration": 1.0,
        "change_at": 0.5, "change_to": 400.0,
    },
    "reverse_reset": {
        "speed_rpm": -400.0, "vd": 0.0, "vq": -0.25,
        "acceleration": 1000.0, "duration": 1.0,
    },
    "high_valid_1000": {
        "speed_rpm": 1000.0, "vd": 0.0, "vq": 0.5,
        "acceleration": 1000.0, "duration": 2.0,
    },
    "insufficient_voltage": {
        "speed_rpm": 800.0, "vd": 0.0, "vq": 0.25,
        "acceleration": 1000.0, "duration": 1.0,
    },
    "zero_voltage_rest": {
        "speed_rpm": 400.0, "vd": 0.0, "vq": 0.0,
        "acceleration": 1000.0, "duration": 0.2,
    },
}


def _temporary_config(clock_mhz: float):
    source = configparser.ConfigParser()
    source.read(ROOT / "memory.cfg")
    source["device"]["pru_clock_mhz"] = str(clock_mhz)
    source["device"]["pru1_clock_mhz"] = str(clock_mhz)
    temp = tempfile.TemporaryDirectory(prefix="foc-acceptance-")
    config_path = Path(temp.name) / "memory.cfg"
    with config_path.open("w") as handle:
        source.write(handle)
    shutil.copytree(ROOT / "config", Path(temp.name) / "config")
    return temp, config_path


def _angle_error_deg(command: int, rotor: int) -> float:
    delta = ((int(command) - int(rotor) + (1 << 31)) & MASK32) - (1 << 31)
    return delta * 360.0 / (1 << 32)


def _append_samples(samples: list[dict], state: dict, clock_hz: float, origin: int) -> None:
    for sample in state["samples"]:
        timestamp = int(sample["timestamp"])
        sim_time = ((timestamp - origin) & ((1 << 64) - 1)) / clock_hz
        samples.append({
            "time": sim_time,
            "speed": int(sample["speed_rpm_q24"]) / abi.Q_ONE * abi.SPEED_BASE_RPM,
            "angle_error": _angle_error_deg(
                sample["theta_cmd_u32"], sample["rotor_theta_u32"]
            ),
            "theta_cmd": int(sample["theta_cmd_u32"]),
            "theta_rotor": int(sample["rotor_theta_u32"]),
        })


def run_case(name: str, clock_mhz: float) -> dict:
    case = dict(CASES[name])
    temp, config_path = _temporary_config(clock_mhz)
    try:
        sim = Simulator(str(config_path))
        errors = sim.load("pru0", FOC_SOURCE, [str(ROOT / "source")])
        if errors:
            raise RuntimeError("FOC assembly failed to load: " + "; ".join(errors))
        sim.iep.write_iepclk(1)
        sim.iep.write_global_cfg(0x11)
        runtime = FocRuntime(sim)
        ramp = case["acceleration"] / (abi.SPEED_BASE_RPM * abi.CONTROL_LOOP_HZ)
        runtime.set_reference(
            speed=case["speed_rpm"] / abi.SPEED_BASE_RPM,
            id=case["vd"], iq=case["vq"], ramp=ramp,
        )
        runtime.start()
        origin = runtime.state()["model"]["timestamp"]
        clock_hz = runtime.state()["clock"]["iep_clock_hz"]
        samples: list[dict] = []
        changed = False
        wall_started = time.perf_counter()
        last_report = -1.0
        while True:
            result = runtime.run_batch(500_000)
            state = runtime.state()
            _append_samples(samples, state, clock_hz, origin)
            sim_time = state["clock"]["sim_time_s"]
            if (
                not changed
                and "change_at" in case
                and sim_time >= case["change_at"]
            ):
                runtime.set_reference(
                    speed=case["change_to"] / abi.SPEED_BASE_RPM,
                    id=case["vd"], iq=case["vq"], ramp=ramp,
                )
                changed = True
            if sim_time - last_report >= 0.1:
                print(
                    json.dumps({"case": name, "clock_mhz": clock_mhz,
                                "sim_time_s": sim_time}),
                    flush=True,
                )
                last_report = sim_time
            if result["fault"] is not None:
                raise RuntimeError(f"FOC execution fault: {result['fault']}")
            if sim_time >= case["duration"]:
                break

        target = case.get("change_to", case["speed_rpm"])
        tail = [sample for sample in samples if sample["time"] >= case["duration"] - 0.2]
        speeds = [sample["speed"] for sample in tail]
        angles = [sample["angle_error"] for sample in samples]
        command_motion = (
            samples[-1]["theta_cmd"] != samples[0]["theta_cmd"]
            if samples else False
        )
        measured_mean = sum(speeds) / len(speeds) if speeds else 0.0
        ripple = max(speeds) - min(speeds) if speeds else 0.0
        elapsed_wall = time.perf_counter() - wall_started
        return {
            "case": name,
            "clock_mhz": clock_mhz,
            "duration_s": case["duration"],
            "simulated_end_s": state["clock"]["sim_time_s"],
            "wall_s": elapsed_wall,
            "sim_wall_ratio": state["clock"]["sim_wall_ratio"],
            "loop_frequency_hz": state["clock"]["control_loop_frequency_hz"],
            "samples": len(samples),
            "target_speed_rpm": target,
            "final_200ms_mean_speed_rpm": measured_mean,
            "final_200ms_ripple_pp_rpm": ripple,
            "final_200ms_error_percent": (
                abs(measured_mean - target) / max(abs(target), 1.0) * 100.0
            ),
            "max_abs_angle_error_deg": max((abs(value) for value in angles), default=0.0),
            "command_angle_moved": command_motion,
            "final_speed_rpm": state["model"]["speed_rpm"],
            "fault": state["fault"],
            "synchronism_lost": max((abs(value) for value in angles), default=0.0) >= 170.0,
        }
    finally:
        temp.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=sorted(CASES), action="append")
    parser.add_argument("--clock", type=float, default=200.0)
    args = parser.parse_args()
    selected = args.case or list(CASES)
    for name in selected:
        print(json.dumps(run_case(name, args.clock)), flush=True)


if __name__ == "__main__":
    main()
