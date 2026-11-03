#!/usr/bin/env python3
"""Speed bench of the PDM audio demo: lockstep / run-ahead x idle skip off / on.

Runs ``PdmDemo`` headless (tone_1000.wav on L, tone_3000.wav on R) for
``--audio-ms`` of simulated audio in each configuration and prints a
Markdown table: wall seconds of the run (setup excluded), simulated audio,
ms of audio per wall second and seconds of audio per wall minute.

Both PRU clocks are forced to 200 MHz through a temporary copy of the repo's
``memory.cfg`` (with its ``config/`` directory), so the local
``memory.cfg`` is not touched.

    python3 source/pdm/tools/bench.py --audio-ms 20
"""
import argparse
import configparser
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / "source")]

from pdm.pdm_demo import CLOCK_MHZ, PdmDemo  # noqa: E402
from simulator import Simulator             # noqa: E402

CLIPS = REPO / "source" / "pdm" / "clips"
CONFIGS = [("lockstep", False), ("lockstep", True),
           ("run-ahead", False), ("run-ahead", True)]


def nominal_config(out_dir: Path) -> str:
    """Repo memory.cfg with PRU0 and PRU1 at 200 MHz, written to *out_dir*."""
    cfg = configparser.ConfigParser()
    cfg.read(REPO / "memory.cfg")
    if "device" not in cfg:
        cfg["device"] = {}
    cfg["device"]["pru_clock_mhz"] = str(CLOCK_MHZ)
    cfg["device"]["pru1_clock_mhz"] = str(CLOCK_MHZ)
    path = out_dir / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    shutil.copytree(REPO / "config", out_dir / "config", dirs_exist_ok=True)
    return str(path)


def bench(config: str, mode: str, skip: bool, audio_ms: float,
          chunk_cycles: int) -> tuple[float, float]:
    """(wall s, simulated audio ms) of one configuration."""
    sim = Simulator(config_path=config)
    sim.idle_skip = skip
    demo = PdmDemo(sim, run_ahead=(mode == "run-ahead"), chunk_cycles=chunk_cycles)
    notices = demo.setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav")
    if notices or demo.mode != mode:
        raise SystemExit(f"{mode}: setup gave mode {demo.mode}, notices {notices}")
    t0 = time.perf_counter()
    while demo.status()["audio_ms"] < audio_ms:
        demo.run_chunk()
    return time.perf_counter() - t0, demo.status()["audio_ms"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audio-ms", type=float, default=20.0,
                    help="simulated audio per configuration, ms (default 20)")
    ap.add_argument("--chunk-cycles", type=int, default=400_000,
                    help="PRU0 cycles per run_chunk (default 400000)")
    args = ap.parse_args(argv)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        config = nominal_config(Path(tmp))
        for mode, skip in CONFIGS:
            wall, audio = bench(config, mode, skip, args.audio_ms, args.chunk_cycles)
            rows.append((mode, "on" if skip else "off", wall, audio))
    print("| mode | skip | wall s | audio ms | ms audio / s | s audio / min |")
    print("|---|---|---:|---:|---:|---:|")
    for mode, skip, wall, audio in rows:
        speed = audio / wall
        print(f"| {mode} | {skip} | {wall:.2f} | {audio:.2f} | {speed:.3f} | "
              f"{speed * 60 / 1000:.4f} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
