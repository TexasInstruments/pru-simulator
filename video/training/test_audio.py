"""Tests of the narration parser (tts.py) and the audio/subtitle assembly (mix.py).

Run with the video venv:  .venv/bin/python -m pytest test_audio.py -q
"""
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import mix
import tts

HERE = Path(__file__).parent

SAMPLE = """\
# a comment
@title First steps

## start | Start
part-intro: Part 1 of 5: first steps.
clone: Clone the repository. || Clone the repo zitory.

## first-program | First program
load-assemble: Click Load & Assemble.
"""


def test_parse_reads_title_chapters_and_cues():
    doc = tts.parse(SAMPLE)
    assert doc["title"] == "First steps"
    assert doc["chapters"] == [{"id": "start", "title": "Start"},
                               {"id": "first-program", "title": "First program"}]
    assert [(c["key"], c["chapter"]) for c in doc["cues"]] == [
        ("part-intro", "start"), ("clone", "start"), ("load-assemble", "first-program")]


def test_parse_keeps_colons_in_the_text_and_splits_the_spoken_form():
    cues = {c["key"]: c for c in tts.parse(SAMPLE)["cues"]}
    assert cues["part-intro"]["text"] == "Part 1 of 5: first steps."
    assert cues["part-intro"]["spoken"] == "Part 1 of 5: first steps."
    assert cues["clone"]["text"] == "Clone the repository."
    assert cues["clone"]["spoken"] == "Clone the repo zitory."


def test_parse_rejects_a_duplicate_key_by_name():
    with pytest.raises(ValueError, match="load-assemble"):
        tts.parse(SAMPLE + "load-assemble: Again.\n")


def test_parse_rejects_a_cue_before_any_chapter():
    with pytest.raises(ValueError, match="orphan"):
        tts.parse("@title T\norphan: No chapter yet.\n")


def test_parse_rejects_a_line_that_is_not_a_cue():
    with pytest.raises(ValueError, match="line 3"):
        tts.parse("@title T\n## c | C\nThis line has no key\n")


def test_parse_requires_a_title():
    with pytest.raises(ValueError, match="@title"):
        tts.parse("## c | C\nk: Text.\n")


def test_slugify():
    assert tts.slugify("SSI encoder, peripherals and events") == "ssi-encoder-peripherals-and-events"
    assert tts.slugify("UART & I2C devices") == "uart-i2c-devices"


def test_missing_models_exit_with_the_download_commands(tmp_path):
    run = subprocess.run([sys.executable, str(HERE / "tts.py"), "1", "--models", str(tmp_path / "nope")],
                         capture_output=True, text=True)
    assert run.returncode != 0
    assert "kokoro-v1.0.onnx" in run.stderr and "voices-v1.0.bin" in run.stderr and "curl" in run.stderr
    assert "Traceback" not in run.stderr


def test_place_puts_each_clip_at_its_frame():
    sr, fps = 24000, 30
    clip = np.full(sr // 2, 0.5, dtype=np.float32)          # 0.5 s
    out = mix.place([clip, clip], [30, 90], fps, 120, sr)    # at 1.0 s and 3.0 s, 4 s in all
    assert len(out) == 4 * sr
    at = lambda seconds: out[int(seconds * sr)]
    assert at(0.5) == 0 and at(1.25) != 0 and at(2.0) == 0 and at(3.25) != 0 and at(3.75) == 0


def test_place_normalises_the_peak_and_never_clips():
    sr = 24000
    out = mix.place([np.full(sr, 0.2, dtype=np.float32)], [0], 30, 60, sr)
    assert np.max(np.abs(out)) == pytest.approx(0.89, abs=1e-3)


def test_place_rejects_a_clip_that_runs_past_the_end():
    sr = 24000
    with pytest.raises(ValueError, match="ends after the picture"):
        mix.place([np.zeros(2 * sr, dtype=np.float32)], [30], 30, 60, sr)


def test_srt_times_come_from_the_frames():
    text = mix.srt([{"text": "Click Load & Assemble.", "frame": 45, "dur": 2.0}], 30)
    assert "1\n00:00:01,500 --> 00:00:03,500\nClick Load & Assemble.\n" in text


def test_srt_wraps_long_lines_at_58_characters():
    long = "The Signal Graph records every general purpose output and input of the cores you selected."
    lines = mix.srt([{"text": long, "frame": 0, "dur": 4.0}], 30).strip().split("\n")[2:]
    assert len(lines) == 2 and all(len(line) <= 58 for line in lines)


def test_ffmeta_has_one_chapter_per_entry_with_millisecond_bounds():
    text = mix.ffmeta("Part 1: First steps", [{"title": "Start", "frame": 0}, {"title": "First program", "frame": 300}], 900, 30)
    assert text.startswith(";FFMETADATA1\n")
    assert "title=Part 1: First steps" in text
    assert "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=10000\ntitle=Start" in text
    assert "[CHAPTER]\nTIMEBASE=1/1000\nSTART=10000\nEND=30000\ntitle=First program" in text
