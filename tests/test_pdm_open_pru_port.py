"""pdm: the open-pru pdm_audio port (AM243x LP, ICSSG0 at 333.333 MHz).

The firmware lives in the open-pru checkout, found through ``OPEN_PRU_PATH``
(default ``~/GitHub/open-pru``), project
``examples/electronica2026/pdm_audio/pdm_audio``; the module is skipped
when that project is missing. The harness assembles the shipped sources
unchanged, or with plain-literal ``.set`` build options overridden at load
time (``load_fw``), the way audio_pipe's tests set build options.

Task 2 (this file's first part): RTU1 ``rtu1_mod.asm`` alone, with a
Python stand-in for PRU1 that acks every batch on the IPC scratchpad
(``spad_batches``). Each batch must equal ``reference.sigma_delta2`` of
the looped clip of each channel (``ref_bits``), and the cycles between two
batch XOUTs must stay within 75 % of PRU1's 28-period batch time.

Shared helpers for the later tasks (PRU1 pin driver, PRU0 receiver):
``load_fw``, ``write_header``, ``write_clip_buffer``, ``spad_batches``,
``ref_bits``, ``shared``.
"""
import math
import os
import re
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "source"))

from pdm import reference as R           # noqa: E402
from simulator import Simulator          # noqa: E402

OPEN_PRU = Path(os.environ.get("OPEN_PRU_PATH", "~/GitHub/open-pru")).expanduser()
PROJECT = OPEN_PRU / "examples" / "electronica2026" / "pdm_audio" / "pdm_audio"
FW = PROJECT / "firmware"
SHARED_INC = FW / "pdm8_shared.inc"

if not PROJECT.is_dir():
    pytest.skip(f"open-pru pdm_audio project not found at {PROJECT} "
                "(set OPEN_PRU_PATH)", allow_module_level=True)

CLOCK_MHZ = 333.333
MSRAM = 0x7000_0000
N_CH = 8
FS = R.FS
IPC_SPAD = 15
BATCH = 28                    # PDM periods per IPC SPAD batch
PCM_PERIODS = 192             # PDM periods per 16 kHz clip frame
# 75 % of PRU1's batch time: 28 periods x (108 + 73/144) cycles.
BUDGET_CYC = 2278


# ---------------------------------------------------------------------------
# Shared helpers (Tasks 2-4)
# ---------------------------------------------------------------------------

_SET_RE = re.compile(r"^(PDM8_\w+)\s+\.set\s+(0[xX][0-9a-fA-F]+|\d+)\s*(?:;.*)?$")


def shared() -> dict[str, int]:
    """{name: value} of every ``PDM8_* .set <literal>`` line of pdm8_shared.inc."""
    out = {}
    for line in SHARED_INC.read_text().splitlines():
        m = _SET_RE.match(line)
        if m:
            out[m.group(1)] = int(m.group(2), 0)
    return out


def set_option(src: str, name: str, value) -> str:
    """Replace the value of the plain-literal build option ``name .set ...``."""
    src, n = re.subn(rf"^{name}(\s+)\.set\s+\S+", rf"{name}\g<1>.set    {value}",
                     src, flags=re.M)
    assert n == 1, f"{name} .set line not found"
    return src


def load_fw(sim, core: str, name: str, overrides: dict | None = None) -> None:
    """Assemble open-pru ``firmware/<name>.asm`` onto *core*, with .set overrides."""
    src = (FW / f"{name}.asm").read_text()
    for opt, value in (overrides or {}).items():
        src = set_option(src, opt, value)
    errors = sim.load(core, src, include_paths=[str(FW)])
    assert errors == [], errors


def write_header(memory, clip_frames: int, clip_addr: int = MSRAM) -> None:
    """Zero the 256-byte shared-RAM header and fill in what the R5F writes."""
    s = shared()
    hdr = bytearray(256)
    struct.pack_into("<I", hdr, s["PDM8_HDR_MAGIC"], s["PDM8_MAGIC"])
    struct.pack_into("<I", hdr, s["PDM8_HDR_N_CH"], N_CH)
    struct.pack_into("<I", hdr, s["PDM8_HDR_FRAMES_PER_HALF"], s["PDM8_FRAMES_PER_HALF"])
    struct.pack_into("<I", hdr, s["PDM8_HDR_PCM_RATE"], s["PDM8_PCM_RATE"])
    struct.pack_into("<I", hdr, s["PDM8_HDR_CLIP_ADDR"], clip_addr)
    struct.pack_into("<I", hdr, s["PDM8_HDR_CLIP_FRAMES"], clip_frames)
    memory.write(s["PDM8_SHARED_BASE"], bytes(hdr))


def read_header(memory) -> dict[str, int]:
    """u32 header fields by lower-case name without the prefix (pcm_count: list)."""
    s = shared()
    base = s["PDM8_SHARED_BASE"]
    raw = bytes(memory.read(base, 256)[0])
    out = {}
    for name, off in s.items():
        if name.startswith("PDM8_HDR_") and name != "PDM8_HDR_PCM_COUNT":
            out[name[len("PDM8_HDR_"):].lower()] = struct.unpack_from("<I", raw, off)[0]
    out["pcm_count"] = list(struct.unpack_from("<8I", raw, s["PDM8_HDR_PCM_COUNT"]))
    return out


def write_clip_buffer(memory, frames: list[list[int]], addr: int = MSRAM) -> None:
    """Frame-interleaved 8 x int16 per frame (16 B), as the R5F builds it."""
    buf = bytearray()
    for frame in frames:
        assert len(frame) == N_CH
        buf += struct.pack("<8h", *frame)
    memory.write(addr, bytes(buf))


def clips_to_frames(clips: list[list[int]]) -> list[list[int]]:
    """Per-channel sample lists (equal length) -> frames[f][k]."""
    assert len(clips) == N_CH and len({len(c) for c in clips}) == 1
    return [list(f) for f in zip(*clips)]


def ref_bits(clips: list[list[int]], n_periods: int) -> list[list[int]]:
    """Per channel ``sigma_delta2`` of the clip looped to *n_periods* periods."""
    out = []
    for clip in clips:
        reps = -(-n_periods // (len(clip) * PCM_PERIODS))
        out.append(R.sigma_delta2(list(clip) * reps, PCM_PERIODS, n_periods))
    return out


def batch_bits(data: bytes, k: int) -> list[int]:
    """Channel *k*'s bits of one 28-byte batch (bit k of byte j = period j)."""
    return [(b >> k) & 1 for b in data[:BATCH]]


def spad_batches(sim, n_batches: int, *, ack: bool = True, rtu1_only: bool = True,
                 max_cycles: int | None = None) -> list[tuple[int, bytes, int]]:
    """Step RTU1 alone with a Python stand-in for PRU1 on the IPC scratchpad.

    Returns ``[(seq, 28 batch bytes, RTU1 cycle count after the XOUT)]`` for
    each batch RTU1 publishes (a change of R9.w0, SPAD bytes 28..29). With
    *ack* the stand-in writes ``ack = seq`` (R9.b3, SPAD byte 31) at once,
    as PRU1 does when it takes a batch. Stops after *n_batches* batches or
    *max_cycles* RTU1 cycles. Only the RTU1-alone mode exists so far.
    """
    assert rtu1_only, "only the RTU1-alone stand-in is implemented"
    core = sim.cores["rtu1"]
    if max_cycles is None:
        max_cycles = core.counters.cycles + (n_batches + 1) * 4 * BUDGET_CYC
    out = []
    last = sim.xfr.xin(IPC_SPAD, 28, 2)
    while len(out) < n_batches and core.counters.cycles < max_cycles:
        core.step()
        assert not core.halted and core.fault is None, core.fault
        seq_raw = sim.xfr.xin(IPC_SPAD, 28, 2)
        if seq_raw != last:
            last = seq_raw
            seq = struct.unpack("<H", seq_raw)[0]
            out.append((seq, sim.xfr.xin(IPC_SPAD, 0, BATCH), core.counters.cycles))
            if ack:
                sim.xfr.xout(IPC_SPAD, 31, bytes([seq & 0xFF]))
    return out


def tone_clips(n_frames: int) -> list[list[int]]:
    """8 different tones (peak 16384, like the loader), one per channel."""
    freqs = [500, 1000, 2000, 3000, 4000, 5000, 6000, 7000]
    return [[round(16384 * math.sin(2 * math.pi * f * n / 16000 + 0.3 * k))
             for n in range(n_frames)] for k, f in enumerate(freqs)]


def rtu1_sim(sim_config, clips: list[list[int]]):
    """Simulator at 333.333 MHz with the clip buffer, the header and rtu1_mod."""
    config = sim_config(pru_clock_mhz=CLOCK_MHZ, pru1_clock_mhz=CLOCK_MHZ)
    sim = Simulator(config_path=config)
    write_clip_buffer(sim.memory, clips_to_frames(clips))
    write_header(sim.memory, len(clips[0]))
    load_fw(sim, "rtu1", "rtu1_mod")
    return sim


def assert_batches_match(batches, clips, first_batch: int = 0):
    n = len(batches)
    ref = ref_bits(clips, (first_batch + n) * BATCH)
    for i, (seq, data, _) in enumerate(batches):
        b = first_batch + i
        assert seq == b + 1
        for k in range(N_CH):
            assert batch_bits(data, k) == ref[k][b * BATCH:(b + 1) * BATCH], \
                f"batch {b} channel {k}"


# ---------------------------------------------------------------------------
# Task 2: RTU1 modulator
# ---------------------------------------------------------------------------

def test_shared_inc_names():
    s = shared()
    assert s["PDM8_SHARED_BASE"] == 0x10000
    assert s["PDM8_MAGIC"] == 0x50444D38
    assert s["PDM8_FRAMES_PER_HALF"] == 64
    assert (s["PDM8_HALF_A"], s["PDM8_HALF_B"], s["PDM8_HALF_BYTES"]) == (0x100, 0x900, 0x800)
    assert s["PDM8_FRAME_BYTES"] == 32
    assert (s["PDM8_SPAD_XID"], s["PDM8_BATCH_PERIODS"]) == (IPC_SPAD, BATCH)
    assert (s["PDM8_CFG_T_SH_CYC"], s["PDM8_CFG_FRAC_ACC"], s["PDM8_CFG_FRAC_MAX"],
            s["PDM8_PCM_PERIODS"]) == (108, 73, 144, PCM_PERIODS)
    offs = [v for k, v in s.items() if k.startswith("PDM8_HDR_")]
    assert len(offs) == len(set(offs)) and max(offs) + 4 * 8 <= 0x100
    assert all(v % 4 == 0 for v in offs)


def test_rtu1_clears_spad_at_start(sim_config):
    """Stale batch bytes and seq from an earlier run are cleared before batch 0."""
    sim = rtu1_sim(sim_config, tone_clips(4))
    sim.xfr.xout(IPC_SPAD, 0, bytes([0xAA] * 30))
    core = sim.cores["rtu1"]
    for _ in range(8):
        core.step()
    assert sim.xfr.xin(IPC_SPAD, 0, 30) == bytes(30)


def test_rtu1_batches_match_reference(sim_config):
    clips = tone_clips(8)
    sim = rtu1_sim(sim_config, clips)
    batches = spad_batches(sim, 20)
    assert len(batches) == 20
    assert_batches_match(batches, clips)
    hdr = read_header(sim.memory)
    assert hdr["rtu1_frame_pos"] == 20 * BATCH // PCM_PERIODS
    assert hdr["rtu1_loops"] == 0


def test_rtu1_int16_extremes(sim_config):
    """Full-range int16 inputs: sign extension of every halfword lane."""
    vals = [-32768, 32767, -1, 1, -16384, 16384, 0, -12345]
    clips = [[vals[(k + 3 * f) % 8] for f in range(3)] for k in range(N_CH)]
    sim = rtu1_sim(sim_config, clips)
    batches = spad_batches(sim, 8)
    assert len(batches) == 8
    assert_batches_match(batches, clips)


def test_rtu1_no_clip_plays_silence(sim_config):
    """CLIP_FRAMES = 0: RTU1 modulates x = 0 (its own zero frame), no MSRAM read."""
    config = sim_config(pru_clock_mhz=CLOCK_MHZ, pru1_clock_mhz=CLOCK_MHZ)
    sim = Simulator(config_path=config)
    write_clip_buffer(sim.memory, [[12345] * N_CH])      # must not be played
    write_header(sim.memory, 0)
    load_fw(sim, "rtu1", "rtu1_mod")
    batches = spad_batches(sim, 3)
    assert_batches_match(batches, [[0]] * N_CH)


def test_clip_wraps_mid_batch(sim_config):
    """CLIP_FRAMES = 3: the wrap at period 576 is inside batch 20 (byte 16)."""
    clips = tone_clips(3)
    sim = rtu1_sim(sim_config, clips)
    before = spad_batches(sim, 20)
    assert read_header(sim.memory)["rtu1_loops"] == 0
    after = spad_batches(sim, 6)
    assert_batches_match(before + after, clips)
    hdr = read_header(sim.memory)
    assert hdr["rtu1_loops"] == 1
    assert hdr["rtu1_frame_pos"] == (26 * BATCH // PCM_PERIODS) % 3


def test_rtu1_waits_for_ack(sim_config):
    sim = rtu1_sim(sim_config, tone_clips(4))
    batches = spad_batches(sim, 5, ack=False, max_cycles=5 * BUDGET_CYC)
    assert [seq for seq, _, _ in batches] == [1]
    assert struct.unpack("<H", sim.xfr.xin(IPC_SPAD, 28, 2))[0] == 1
    # Acking batch 1 releases batch 2, which is the next 28 periods.
    clips = tone_clips(4)
    sim.xfr.xout(IPC_SPAD, 31, bytes([1]))
    more = spad_batches(sim, 1)
    assert_batches_match(batches + more, clips)


def test_rtu1_budget_checkpoint(sim_config):
    """Cycles per 28-period batch at 8 channels, XOUT to XOUT, PRU1 acking at once.

    CLIP_FRAMES = 2 puts frame fetches and clip wraps into the measured
    batches. The limit is 75 % of 28 x 108.5 cycles: RTU1 reads from MSRAM
    are not line-modelled (flat latency), so 25 % stays for silicon.
    """
    sim = rtu1_sim(sim_config, tone_clips(2))
    batches = spad_batches(sim, 31)
    assert len(batches) == 31
    gaps = [b[2] - a[2] for a, b in zip(batches, batches[1:])]
    worst = max(gaps)
    print(f"\nRTU1 cycles per batch: worst {worst}, mean {sum(gaps) / len(gaps):.1f}, "
          f"min {min(gaps)}; budget {BUDGET_CYC} "
          f"({100 * worst / (28 * 108.5):.1f} % of PRU1's batch time)")
    assert worst <= BUDGET_CYC
