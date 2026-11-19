"""Shared-RAM map of the PDM audio demo, clip loader and header helpers.

Mirror of ``pdm_shared.inc``; all offsets are relative to ``SHARED_BASE``
(ICSS shared RAM).  ``memory`` arguments are ``Simulator.memory``
(``read(addr, n) -> (bytes, stalls)``, ``write(addr, bytes)``).
"""
import re
import struct
import wave
from pathlib import Path

SHARED_BASE = 0x10000

HDR_MAGIC = 0x00          # u32 = MAGIC, host
HDR_N_CH = 0x04           # u8, host
HDR_BLOCK = 0x06          # u16, host
HDR_PCM_RATE = 0x08       # u32, host
HDR_CLIP_LEN = 0x0C       # u32 frames, host
HDR_GEN_POS = 0x10        # u32 frames fetched, PRU1 (k+1 while frame k plays; LEN at clip end)
HDR_GEN_DONE = 0x14       # u32 0/1 clip end reached, PRU1; rises shortly before the last bits are sampled
HDR_PCM_COUNT = 0x20      # u32[8], PRU0, per channel, written after the sample
RING_BASE = 0x100
RING_STRIDE = 0x400
RING_SAMPLES = 256
CLIP_BASE = 0x2100
CLIP_MAX_FRAMES = 8192    # 0.512 s

MAGIC = 0x50444D31        # "PDM1"
N_CH = 2
BLOCK = 128
PCM_RATE = 16000
N_RINGS = 8
NORM_PEAK = 16384         # 0.5 FS


def load_clip(path) -> list[int]:
    """Load a mono (or first channel of a stereo) 16 kHz 16-bit WAV.

    Normalised so max(|x|) == 16384; silence stays all-zero.  Raises
    ValueError("<name>: ...") naming the broken rule.
    """
    path = Path(path)
    try:
        w = wave.open(str(path), "rb")
    except (wave.Error, EOFError) as e:
        raise ValueError(f"{path.name}: not a readable PCM WAV ({e})") from e
    with w:
        rate, width = w.getframerate(), w.getsampwidth()
        nch, nframes = w.getnchannels(), w.getnframes()
        if rate != PCM_RATE:
            raise ValueError(f"{path.name}: sample rate {rate} Hz, need {PCM_RATE}")
        if width != 2:
            raise ValueError(f"{path.name}: {8 * width}-bit samples, need 16-bit")
        if nframes > CLIP_MAX_FRAMES:
            raise ValueError(
                f"{path.name}: {nframes} frames, at most {CLIP_MAX_FRAMES} allowed")
        raw = w.readframes(nframes)
    samples = struct.unpack(f"<{len(raw) // 2}h", raw)[::nch]
    peak = max((abs(s) for s in samples), default=0)
    if peak == 0:
        return [0] * len(samples)
    return [round(s * NORM_PEAK / peak) for s in samples]


def write_clips(memory, left: list[int], right: list[int]) -> int:
    """Write the clip frames and header; returns the frame count."""
    n = max(len(left), len(right))
    if n > CLIP_MAX_FRAMES:
        raise ValueError(f"clip has {n} frames, at most {CLIP_MAX_FRAMES} allowed")
    l = list(left) + [0] * (n - len(left))
    r = list(right) + [0] * (n - len(right))
    frames = bytearray()
    for a, b in zip(l, r):
        frames += struct.pack("<hh", a, b)
    if frames:
        memory.write(SHARED_BASE + CLIP_BASE, bytes(frames))
    hdr = bytearray(HDR_PCM_COUNT + 4 * N_RINGS)
    struct.pack_into("<I", hdr, HDR_MAGIC, MAGIC)
    struct.pack_into("<B", hdr, HDR_N_CH, N_CH)
    struct.pack_into("<H", hdr, HDR_BLOCK, BLOCK)
    struct.pack_into("<I", hdr, HDR_PCM_RATE, PCM_RATE)
    struct.pack_into("<I", hdr, HDR_CLIP_LEN, n)
    memory.write(SHARED_BASE, bytes(hdr))
    return n


def _read(memory, addr: int, n: int) -> bytes:
    return bytes(memory.read(addr, n)[0])


def read_header(memory) -> dict:
    raw = _read(memory, SHARED_BASE, HDR_PCM_COUNT + 4 * N_RINGS)
    return {
        "magic": struct.unpack_from("<I", raw, HDR_MAGIC)[0],
        "n_ch": raw[HDR_N_CH],
        "block": struct.unpack_from("<H", raw, HDR_BLOCK)[0],
        "pcm_rate": struct.unpack_from("<I", raw, HDR_PCM_RATE)[0],
        "clip_len": struct.unpack_from("<I", raw, HDR_CLIP_LEN)[0],
        "gen_pos": struct.unpack_from("<I", raw, HDR_GEN_POS)[0],
        "gen_done": struct.unpack_from("<I", raw, HDR_GEN_DONE)[0],
        "pcm_count": list(struct.unpack_from(f"<{N_RINGS}I", raw, HDR_PCM_COUNT)),
    }


def read_ring(memory, ch: int, start: int, count: int) -> list[int]:
    """Samples ``start .. start+count-1`` of ring ``ch`` (signed int32, wrapping)."""
    if not 0 <= ch < N_RINGS:
        raise ValueError(f"ring {ch} out of range 0..{N_RINGS - 1}")
    base = SHARED_BASE + RING_BASE + ch * RING_STRIDE
    raw = _read(memory, base, RING_SAMPLES * 4)
    ring = struct.unpack(f"<{RING_SAMPLES}i", raw)
    return [ring[(start + i) % RING_SAMPLES] for i in range(count)]


_SET_RE = re.compile(
    r"^\s*(PDM_\w+)\s+\.set\s+(0[xX][0-9a-fA-F]+|\d+)\s*(?:;.*)?$")


def parse_inc(path) -> dict[str, int]:
    """Return {name: value} for every ``PDM_* .set <literal>`` line of an .inc."""
    out = {}
    for line in Path(path).read_text().splitlines():
        m = _SET_RE.match(line)
        if m:
            out[m.group(1)] = int(m.group(2), 0)
    return out
