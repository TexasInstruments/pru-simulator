"""pdm: shared-RAM map (asm .inc vs Python mirror), clip loader, booth clips."""
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "source"))

from pdm import pdm_layout as L          # noqa: E402
from simulator import Simulator          # noqa: E402

PDM_DIR = REPO / "source" / "pdm"
INC = PDM_DIR / "pdm_shared.inc"
CLIPS = PDM_DIR / "clips"


def _write_wav(path, samples, rate=16000, width=2, channels=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
        else:
            w.writeframes(bytes(samples))


def _tone(n, amp, f=1000.0, rate=16000):
    return [int(round(amp * 32767 * math.sin(2 * math.pi * f * i / rate)))
            for i in range(n)]


def test_inc_matches_python():
    consts = L.parse_inc(INC)
    assert consts, "no PDM_* symbols parsed"
    for name, value in consts.items():
        assert name.startswith("PDM_")
        assert getattr(L, name[4:]) == value, name
    # every layout constant is mirrored in the .inc
    for py in ("SHARED_BASE", "HDR_MAGIC", "HDR_N_CH", "HDR_BLOCK",
               "HDR_PCM_RATE", "HDR_CLIP_LEN", "HDR_GEN_POS", "HDR_GEN_DONE",
               "HDR_PCM_COUNT", "RING_BASE", "RING_STRIDE", "RING_SAMPLES",
               "CLIP_BASE", "CLIP_MAX_FRAMES"):
        assert "PDM_" + py in consts, py
    assert L.RING_BASE + 8 * L.RING_STRIDE <= L.CLIP_BASE
    assert L.CLIP_BASE + 4 * L.CLIP_MAX_FRAMES <= 0x10000
    assert L.HDR_PCM_COUNT + 4 * 8 <= L.RING_BASE


def test_inc_accepted_by_simulator(nominal_config):
    sim = Simulator(nominal_config)
    src = ('    .include "pdm_shared.inc"\n'
           "    ldi r1, PDM_HDR_BLOCK\n"
           "    ldi r2, PDM_RING_STRIDE\n"
           "    halt\n")
    errors = sim.load("pru0", src, include_paths=[str(PDM_DIR)])
    assert errors == [], errors


def test_loader_normalises_to_half_scale(tmp_path):
    p = tmp_path / "t.wav"
    _write_wav(p, _tone(4000, 0.9))
    x = L.load_clip(p)
    assert max(abs(v) for v in x) == 16384
    assert len(x) == 4000


def test_loader_rejects_bad_clips(tmp_path):
    p = tmp_path / "rate48.wav"
    _write_wav(p, [100] * 100, rate=48000)
    with pytest.raises(ValueError, match="rate48.wav.*16000"):
        L.load_clip(p)
    p = tmp_path / "eight.wav"
    _write_wav(p, [128] * 100, width=1)
    with pytest.raises(ValueError, match="eight.wav.*16-bit"):
        L.load_clip(p)
    p = tmp_path / "long.wav"
    _write_wav(p, [100] * 9000)
    with pytest.raises(ValueError, match="long.wav.*8192"):
        L.load_clip(p)


def test_loader_accepts_silence(tmp_path):
    p = tmp_path / "s.wav"
    _write_wav(p, [0] * 500)
    assert L.load_clip(p) == [0] * 500


def test_loader_stereo_uses_first_channel(tmp_path):
    p = tmp_path / "st.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack("<8h", 10, -999, -20, 999, 5, 0, 0, 0))
    assert L.load_clip(p) == [8192, -16384, 4096, 0]


def test_write_clips_header_and_padding(nominal_config):
    sim = Simulator(nominal_config)
    left = list(range(1, 101))
    right = [-v for v in range(1, 51)]
    n = L.write_clips(sim.memory, left, right)
    assert n == 100
    h = L.read_header(sim.memory)
    assert h["magic"] == 0x50444D31
    assert h["n_ch"] == 2
    assert h["block"] == 128
    assert h["pcm_rate"] == 16000
    assert h["clip_len"] == 100
    assert h["gen_pos"] == 0 and h["gen_done"] == 0
    assert h["pcm_count"] == [0] * 8
    base = L.SHARED_BASE + L.CLIP_BASE
    raw, _ = sim.memory.read(base + 4 * 75, 4)
    assert struct.unpack("<hh", bytes(raw)) == (76, 0)
    raw, _ = sim.memory.read(base + 4 * 10, 4)
    assert struct.unpack("<hh", bytes(raw)) == (11, -11)


def test_read_ring_wraps(nominal_config):
    sim = Simulator(nominal_config)
    L.write_clips(sim.memory, [0], [0])
    ring1 = L.SHARED_BASE + L.RING_BASE + 1 * L.RING_STRIDE
    vals = {n: (n * 1000 - 400000) for n in (254, 255, 256, 257)}
    for n, v in vals.items():
        sim.memory.write(ring1 + 4 * (n % 256), struct.pack("<i", v))
    assert L.read_ring(sim.memory, 1, 254, 4) == [vals[n] for n in (254, 255, 256, 257)]


def test_shipped_clips_load():
    wavs = sorted(CLIPS.glob("*.wav"))
    assert {w.name for w in wavs} >= {
        "chirp_lin_100_7000.wav", "chirp_log_100_7000.wav", "tone_1000.wav",
        "tone_3000.wav", "speech_librivox.wav"}
    sources = (CLIPS / "SOURCES.md").read_text()
    for w in wavs:
        x = L.load_clip(w)
        assert 0 < len(x) <= L.CLIP_MAX_FRAMES
        assert max(abs(v) for v in x) == 16384
        assert w.name in sources, w.name
