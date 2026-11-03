#!/usr/bin/env python3
"""Generate / convert the booth clips of the PDM audio demo.

  make_clips.py chirps
      Write the four generated clips (0.25 s, amplitude 0.5 FS, 16 kHz).
  make_clips.py speech <in-file> --start S --dur D
      Convert a recording with ffmpeg to clips/speech_librivox.wav and print
      the command (paste it into clips/SOURCES.md).  ffmpeg is needed only here.
"""
import argparse
import math
import shlex
import struct
import subprocess
import sys
import wave
from pathlib import Path

CLIPS_DIR = Path(__file__).resolve().parent.parent / "clips"
RATE = 16000
DURATION = 0.25
AMPLITUDE = 0.5
F_LO, F_HI = 100.0, 7000.0


def _write(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _pcm(phase_fn, n: int) -> list[int]:
    return [round(AMPLITUDE * 32767 * math.sin(phase_fn(i / RATE))) for i in range(n)]


def make_chirps(out_dir: Path = CLIPS_DIR) -> list[Path]:
    n = int(RATE * DURATION)
    T = n / RATE
    k_lin = (F_HI - F_LO) / T
    ratio = F_HI / F_LO
    lnr = math.log(ratio)
    clips = {
        "chirp_lin_100_7000.wav":
            _pcm(lambda t: 2 * math.pi * (F_LO * t + 0.5 * k_lin * t * t), n),
        "chirp_log_100_7000.wav":
            _pcm(lambda t: 2 * math.pi * F_LO * T / lnr * (ratio ** (t / T) - 1), n),
        "tone_1000.wav": _pcm(lambda t: 2 * math.pi * 1000.0 * t, n),
        "tone_3000.wav": _pcm(lambda t: 2 * math.pi * 3000.0 * t, n),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, samples in clips.items():
        _write(out_dir / name, samples)
        paths.append(out_dir / name)
    return paths


def convert_speech(src: Path, start: float, dur: float,
                   out_dir: Path = CLIPS_DIR) -> str:
    out = out_dir / "speech_librivox.wav"
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-t", str(dur), "-i", str(src),
           "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out)]
    subprocess.run(cmd, check=True)
    return shlex.join(cmd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chirps", help="write the four generated clips")
    sp = sub.add_parser("speech", help="convert a recording with ffmpeg")
    sp.add_argument("infile", type=Path)
    sp.add_argument("--start", type=float, required=True, help="start time, s")
    sp.add_argument("--dur", type=float, required=True, help="duration, s (<= 0.512)")
    args = ap.parse_args(argv)
    if args.cmd == "chirps":
        for p in make_chirps():
            print(p)
    else:
        if not 0 < args.dur <= 0.512:
            ap.error("--dur must be in (0, 0.512]")
        print(convert_speech(args.infile, args.start, args.dur))
    return 0


if __name__ == "__main__":
    sys.exit(main())
