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

Task 3 (second part): PRU1 ``pru1_pdm.asm`` with RTU1 in lockstep
(``run_pair``): the clock edges against the 108 + 73/144 schedule
(``pdm8_clock_edges``), the 8 data pins against ``ref_bits``, setup/hold,
start order and stale scratchpad, underrun, rerun, and that PRU1 touches
no memory.

Shared helpers for the later tasks (PRU0 receiver):
``load_fw``, ``write_header``, ``write_clip_buffer``, ``spad_batches``,
``ref_bits``, ``shared``, ``pair_sim``, ``run_pair``, ``GpoProbe``.
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


def spad_batches(sim, n_batches: int, *, ack: bool = True, echo: bool = True,
                 underruns: dict[int, int] | None = None,
                 max_cycles: int | None = None) -> list[tuple[int, bytes, int]]:
    """Step RTU1 alone with a Python stand-in for PRU1 on the IPC scratchpad.

    Returns ``[(seq, 28 batch bytes, RTU1 cycle count after the XOUT)]`` for
    each batch RTU1 publishes (a change of R9.w0, SPAD bytes 28..29).

    The stand-in does what PRU1 does on its two SPAD bytes:
    - *echo*: while seq == 0 it copies RTU1's start nonce (R2.b0, SPAD byte
      0) into R9.b3 (byte 31), the start handshake (pdm8_shared.inc);
    - *ack*: when seq changes to n it writes ``ack = n`` (R9.b3) at once, as
      PRU1 does when it takes a batch;
    - *underruns* ``{seq: count}``: when seq changes to a key, it writes the
      8-bit underrun count ``count`` into R9.b2 (byte 30) first.
    Stops after *n_batches* batches or *max_cycles* RTU1 cycles. The RTU1 +
    PRU1 pair is driven by ``run_pair`` instead.
    """
    core = sim.cores["rtu1"]
    if max_cycles is None:
        max_cycles = core.counters.cycles + (n_batches + 1) * 4 * BUDGET_CYC
    out = []
    last = sim.xfr.xin(IPC_SPAD, 28, 2)
    while len(out) < n_batches and core.counters.cycles < max_cycles:
        core.step()
        assert not core.halted and core.fault is None, core.fault
        seq_raw = sim.xfr.xin(IPC_SPAD, 28, 2)
        if echo and seq_raw == bytes(2):
            sim.xfr.xout(IPC_SPAD, 31, sim.xfr.xin(IPC_SPAD, 0, 1))
        if seq_raw != last:
            last = seq_raw
            seq = struct.unpack("<H", seq_raw)[0]
            out.append((seq, sim.xfr.xin(IPC_SPAD, 0, BATCH), core.counters.cycles))
            if underruns and seq in underruns:
                sim.xfr.xout(IPC_SPAD, 30, bytes([underruns[seq] & 0xFF]))
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
    # Batch and seq are zero, R2.b0 carries the start nonce (stale ack 0 -> 2).
    assert sim.xfr.xin(IPC_SPAD, 0, 30) == bytes([2]) + bytes(29)


@pytest.mark.parametrize("stale_ack, nonce", [(0, 2), (1, 2), (2, 3), (3, 2), (0xFF, 2)])
def test_rtu1_start_nonce_differs_from_stale_ack(sim_config, stale_ack, nonce):
    """The nonce is never 0 (PRU1's cleared ack), 1 (ack of batch 0) or the stale ack."""
    sim = rtu1_sim(sim_config, tone_clips(4))
    sim.xfr.xout(IPC_SPAD, 31, bytes([stale_ack]))
    core = sim.cores["rtu1"]
    for _ in range(8):
        core.step()
    assert sim.xfr.xin(IPC_SPAD, 0, 1)[0] == nonce
    assert nonce not in (0, 1, stale_ack)


def test_rtu1_holds_batch0_until_nonce_echo(sim_config):
    """A stale ack 0 must not release batch 0: only PRU1's echo of the nonce does."""
    s = shared()
    config = sim_config(pru_clock_mhz=CLOCK_MHZ, pru1_clock_mhz=CLOCK_MHZ)
    sim = Simulator(config_path=config)
    write_clip_buffer(sim.memory, clips_to_frames(tone_clips(4)))
    write_header(sim.memory, 4)
    # Stale counters of an earlier run are cleared at start, before batch 0.
    sim.memory.write(s["PDM8_SHARED_BASE"] + s["PDM8_HDR_UNDERRUN_COUNT"],
                     struct.pack("<II", 77, 88))
    load_fw(sim, "rtu1", "rtu1_mod")
    assert spad_batches(sim, 1, echo=False, max_cycles=5 * BUDGET_CYC) == []
    assert sim.xfr.xin(IPC_SPAD, 28, 2) == bytes(2)
    hdr = read_header(sim.memory)
    assert (hdr["underrun_count"], hdr["batch_count"]) == (0, 0)
    batches = spad_batches(sim, 2)
    assert_batches_match(batches, tone_clips(4))


def test_rtu1_publishes_pru1_counters(sim_config):
    """RTU1 writes UNDERRUN_COUNT (from PRU1's 8-bit R9.b2, wraps included)
    and BATCH_COUNT (batches PRU1 acked) into the header."""
    s = shared()
    sim = rtu1_sim(sim_config, tone_clips(4))
    base = s["PDM8_SHARED_BASE"]
    sim.memory.write(base + s["PDM8_HDR_UNDERRUN_COUNT"], struct.pack("<II", 77, 88))

    def publish(n, **kw):
        spad_batches(sim, n, **kw)
        for _ in range(20):                    # the header stores follow the XOUT
            sim.cores["rtu1"].step()

    publish(1)
    hdr = read_header(sim.memory)
    assert (hdr["underrun_count"], hdr["batch_count"]) == (0, 0)
    # PRU1's byte goes 0 -> 200 -> 44 (= 300 mod 256): 300 in total.
    publish(5, underruns={3: 200, 4: 44})
    hdr = read_header(sim.memory)
    seq = struct.unpack("<H", sim.xfr.xin(IPC_SPAD, 28, 2))[0]
    assert seq == 6
    assert hdr["batch_count"] == seq - 1
    assert hdr["underrun_count"] == 300
    publish(1, underruns={7: 45})
    assert read_header(sim.memory)["underrun_count"] == 301


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


# ---------------------------------------------------------------------------
# Task 3: PRU1 clock and pin driver (pru1_pdm.asm) with RTU1 in lockstep
# ---------------------------------------------------------------------------
#
# RTU1 + PRU1 run in lockstep at 333.333 MHz with PRU1 as the lead
# (``step_paced_many("pru1", ["rtu1"], ...)``; idle skip is exact). PRU1's
# pins are observed with a ``gpo_listeners`` probe: an R30 write by the
# instruction that starts at PRU1 cycle c is recorded as (c, GPO word). A
# core "starts later" through ``Simulator.reset``, which places it at the
# current shared time.

CLK_PIN = 0
DATA_PINS = (1, 2, 6, 11, 12, 13, 15, 16)      # channel k -> PRU1 GPO
DATA_MASK = sum(1 << p for p in DATA_PINS)
T_SH, FRAC_ACC, FRAC_MAX = 108, 73, 144
T_HIGH, T_LOW = T_SH // 2, T_SH - T_SH // 2    # 54 / 54; long period: high 55
SETUP_MIN = 26
SILENCE = bytes([0x00, 0xFF] * (BATCH // 2))   # alternating 0/1, every channel
BATCH_CYC = 3038                               # ~28 x 108.5 PRU1 cycles


def pdm8_clock_edges(n_periods: int, start_cycle: int) -> list[tuple[int, bool]]:
    """Clock edges ``(cycle, rising)`` at 108 + 73/144 cycles per period.

    ``reference.pdm_clock_edges`` written out for 333.333 MHz: high 54,
    low 54. At the end of each high time the accumulator is tested; if
    ``acc >= 144`` it drops by 71 and the *next* period's high time is 55
    (109-cycle period), otherwise it grows by 73: 71 x 108 + 73 x 109 =
    15625 cycles per 144 periods. The extra cycle is in the HIGH half:
    the data changes at the falling edge, so the setup (= low time) is
    the same 54 cycles in every period and only the hold varies (54/55).
    """
    edges = []
    t = start_cycle
    acc = 0
    extra = 0
    for _ in range(n_periods):
        edges.append((t, True))
        t += T_HIGH + extra
        edges.append((t, False))
        if acc >= FRAC_MAX:
            acc += FRAC_ACC - FRAC_MAX
            extra = 1
        else:
            acc += FRAC_ACC
            extra = 0
        t += T_LOW
    return edges


class GpoProbe:
    """Records PRU1 GPO changes and every R30 write, by start cycle."""

    def __init__(self, core):
        self.core = core
        self.events: list[tuple[int, int]] = []     # (cycle, new GPO word)
        self.writes: list[int] = []                 # cycle of every R30 write
        core.io_port.gpo_listeners.append(self._on_change)
        inner = core.io_port.write_r30

        def counted(value, wstrb=0xF):
            self.writes.append(core.step_start_cycle)
            inner(value, wstrb)
        core.io_port.write_r30 = counted

    def _on_change(self, old, new):
        self.events.append((self.core.step_start_cycle, new))

    def clock(self) -> list[tuple[int, bool, int]]:
        """Clock edges ``(cycle, rising, GPO word)``."""
        out, prev = [], 0
        for cycle, word in self.events:
            if (word ^ prev) >> CLK_PIN & 1:
                out.append((cycle, bool(word >> CLK_PIN & 1), word))
            prev = word
        return out

    def data_changes(self) -> list[int]:
        out, prev = [], 0
        for cycle, word in self.events:
            if (word ^ prev) & DATA_MASK:
                out.append(cycle)
            prev = word
        return out

    def period_bytes(self) -> bytes:
        """Byte per period: bit k = channel k's pin at the rising edge."""
        return bytes(sum(((w >> p) & 1) << k for k, p in enumerate(DATA_PINS))
                     for _, rising, w in self.clock() if rising)


def pair_sim(sim_config, clips, *, header: bool = True):
    """333.333 MHz simulator with the clip, the header, rtu1_mod and pru1_pdm."""
    config = sim_config(pru_clock_mhz=CLOCK_MHZ, pru1_clock_mhz=CLOCK_MHZ)
    sim = Simulator(config_path=config)
    write_clip_buffer(sim.memory, clips_to_frames(clips))
    if header:
        write_header(sim.memory, len(clips[0]))
    load_fw(sim, "rtu1", "rtu1_mod")
    load_fw(sim, "pru1", "pru1_pdm")
    return sim, GpoProbe(sim.cores["pru1"])


def _check(sim, *names):
    for name in names:
        core = sim.cores[name]
        assert not core.halted and core.fault is None, (name, core.fault)


def run_pair(sim, pru1_cycles: int, *, idle_skip: bool = True, until=None) -> None:
    """Lockstep RTU1 + PRU1 (PRU1 leads) for *pru1_cycles* more PRU1 cycles,
    or until ``until()`` is true (checked after every lead step)."""
    pru1 = sim.cores["pru1"]
    end = pru1.counters.cycles + pru1_cycles
    count = 1 if until is not None else 500
    while pru1.counters.cycles < end:
        sim.step_paced_many("pru1", ["rtu1"], count, idle_skip=idle_skip)
        _check(sim, "pru1", "rtu1")
        if until is not None and until():
            return


def run_alone(sim, name: str, cycles: int) -> None:
    core = sim.cores[name]
    end = core.counters.cycles + cycles
    while core.counters.cycles < end:
        core.step_run(end - core.counters.cycles)
        _check(sim, name)


def cycles_for(n_periods: int) -> int:
    """PRU1 cycles that cover the start hand-off plus *n_periods* + 1 periods."""
    return 4 * BUDGET_CYC + (n_periods + 2) * 109


def ref_batches(clips, n: int) -> list[bytes]:
    """Reference period bytes (bit k = channel k) of batches 0..n-1."""
    ref = ref_bits(clips, n * BATCH)
    return [bytes(sum(ref[k][b * BATCH + j] << k for k in range(N_CH))
                  for j in range(BATCH)) for b in range(n)]


def split_batches(period_bytes: bytes) -> list[bytes]:
    n = len(period_bytes) // BATCH
    return [period_bytes[b * BATCH:(b + 1) * BATCH] for b in range(n)]


def test_reference_clock_333mhz():
    """The harness schedule is pdm_clock_edges with 108/73/144, 15625 per 144."""
    edges = pdm8_clock_edges(3 * 144 + 1, 100)
    assert edges == R.pdm_clock_edges(3 * 144 + 1, 100, T_SH, FRAC_ACC, FRAC_MAX)
    rises = [c for c, rising in edges if rising]
    periods = [b - a for a, b in zip(rises, rises[1:])]
    assert sorted(set(periods)) == [108, 109]
    assert rises[288] - rises[144] == rises[432] - rises[288] == 15625
    assert periods[144:288].count(109) == 73


def test_clock_follows_333mhz_schedule(sim_config):
    clips = tone_clips(4)
    sim, probe = pair_sim(sim_config, clips)
    n = 3 * 144
    run_pair(sim, cycles_for(n))
    clk = probe.clock()
    assert clk[0][1], "first clock edge must be rising"
    got = [(c, rising) for c, rising, _ in clk[:2 * n]]
    assert got == pdm8_clock_edges(n, clk[0][0])


def test_pins_carry_reference_bits(sim_config):
    clips = tone_clips(4)
    sim, probe = pair_sim(sim_config, clips)
    n = 10
    run_pair(sim, cycles_for(n * BATCH))
    got = split_batches(probe.period_bytes())[:n]
    assert got == ref_batches(clips, n)
    hdr = read_header(sim.memory)
    assert hdr["underrun_count"] == 0
    seq = struct.unpack("<H", sim.xfr.xin(IPC_SPAD, 28, 2))[0]
    assert hdr["batch_count"] == seq - 1 >= n - 1


def test_setup_margin(sim_config):
    """Data changes only with the falling edge: setup = low time (54 cycles),
    hold = high time (54/55), and one R30 write per half period."""
    sim, probe = pair_sim(sim_config, tone_clips(4))
    n = 4 * BATCH
    run_pair(sim, cycles_for(n))
    clk = probe.clock()[:2 * n]
    falls = {c for c, rising, _ in clk if not rising}
    changes = probe.data_changes()
    rises = [c for c, rising, _ in clk if rising]
    setups = []
    for r in rises:
        before = [c for c in changes if c < r]
        assert before, "no data before the first rising edge"
        setups.append(r - before[-1])
    assert min(setups) >= SETUP_MIN
    assert min(setups) == T_LOW            # longer where a byte repeats
    # Every data change after the first is a falling edge (the first one is
    # the data of period 0, T_LOW before the first rising edge).
    assert changes[0] == rises[0] - T_LOW
    assert all(c in falls for c in changes[1:] if c <= clk[-1][0])
    holds = [f - r for (r, f) in zip(rises, sorted(falls))]
    assert set(holds) == {T_HIGH, T_HIGH + 1}
    # One R30 write per half period: the edge write itself.
    edges = [c for c, _, _ in clk]
    in_run = [w for w in probe.writes if edges[0] <= w <= edges[-1]]
    assert in_run == edges
    print(f"\nsetup {min(setups)} cycles ({min(setups) / CLOCK_MHZ * 1000:.0f} ns), "
          f"hold {min(holds)}..{max(holds)} cycles")


def test_pru1_waits_for_first_batch(sim_config):
    """PRU1 alone: no pin moves. RTU1 started later: a clean start at batch 0."""
    clips = tone_clips(4)
    sim, probe = pair_sim(sim_config, clips)
    run_alone(sim, "pru1", 50_000)
    assert probe.events == []
    sim.reset("rtu1")                          # RTU1 starts now
    start = sim.cores["pru1"].counters.cycles
    run_pair(sim, cycles_for(3 * BATCH))
    clk = probe.clock()
    assert clk and clk[0][1] and clk[0][0] > start
    got = [(c, rising) for c, rising, _ in clk[:6 * BATCH]]
    assert got == pdm8_clock_edges(3 * BATCH, clk[0][0])
    assert split_batches(probe.period_bytes())[:3] == ref_batches(clips, 3)
    assert read_header(sim.memory)["underrun_count"] == 0


def test_rtu1_started_first_does_not_deadlock(sim_config):
    """RTU1 alone first (fresh SPAD, ack 0): it holds batch 0 until PRU1
    echoes its nonce; PRU1 started later gets a clean start."""
    clips = tone_clips(4)
    sim, probe = pair_sim(sim_config, clips)
    run_alone(sim, "rtu1", 50_000)
    assert sim.xfr.xin(IPC_SPAD, 28, 2) == bytes(2)     # batch 0 not published
    sim.reset("pru1")                                   # PRU1 starts now
    run_pair(sim, cycles_for(3 * BATCH))
    assert split_batches(probe.period_bytes())[:3] == ref_batches(clips, 3)
    assert read_header(sim.memory)["underrun_count"] == 0


@pytest.mark.parametrize("seq, ack", [(1, 0), (1, 1), (0, 0), (2, 1), (0x1234, 0x33)])
def test_stale_spad_does_not_deadlock(sim_config, seq, ack):
    """Stale seq/batch/ack from an earlier run, PRU1 started before RTU1:
    PRU1 never plays the stale batch, and the start is clean."""
    clips = tone_clips(4)
    sim, probe = pair_sim(sim_config, clips)
    stale = bytes([0x5A] * BATCH) + struct.pack("<H", seq) + bytes([9, ack])
    sim.xfr.xout(IPC_SPAD, 0, stale)
    run_alone(sim, "pru1", 20_000)
    assert probe.events == []
    sim.reset("rtu1")
    run_pair(sim, cycles_for(3 * BATCH))
    assert split_batches(probe.period_bytes())[:3] == ref_batches(clips, 3)
    assert read_header(sim.memory)["underrun_count"] == 0


def _stall_rtu1_at_fetch(sim, n_fetch: int, stall: int) -> int:
    """Lockstep until RTU1 reaches its MSRAM LBBO (FETCH_LOAD) for the
    *n_fetch*-th time, then stall that LBBO by *stall* cycles. Returns the
    PRU1 cycle of the stall."""
    rtu1 = sim.cores["rtu1"]
    fetch = rtu1._parser.labels["FETCH_LOAD"]
    seen = 0
    while True:
        run_pair(sim, 10 * BATCH_CYC, until=lambda: rtu1.pc == fetch)
        assert rtu1.pc == fetch
        seen += 1
        if seen == n_fetch:
            break
        sim.step("rtu1")
    rtu1.counters.stall(stall)                  # e.g. a slow MSRAM read
    return sim.cores["pru1"].counters.cycles


def test_underrun_keeps_clock_and_flags(sim_config):
    """RTU1 stalls ~5 batches: PRU1 keeps the clock on schedule, plays
    silence for the missing batches, counts them, and then resumes the
    stream where it stopped."""
    clips = tone_clips(2)                      # a fetch every 192 periods
    sim, probe = pair_sim(sim_config, clips)
    _stall_rtu1_at_fetch(sim, 2, 5 * BATCH_CYC)
    run_pair(sim, 14 * BATCH_CYC)
    clk = probe.clock()
    n = len(clk) // 2 - 1
    got = [(c, rising) for c, rising, _ in clk[:2 * n]]
    assert got == pdm8_clock_edges(n, clk[0][0])
    played = split_batches(probe.period_bytes())
    silent = [i for i, b in enumerate(played) if b == SILENCE]
    assert silent, "no silence batch"
    u = len(silent)
    first = silent[0]
    assert silent == list(range(first, first + u)), "silence is one gap"
    assert 4 <= u <= 6
    ref = ref_batches(clips, len(played))
    assert played[:first] == ref[:first]
    tail = played[first + u:]
    assert len(tail) >= 3
    assert tail == ref[first:first + len(tail)]
    hdr = read_header(sim.memory)
    assert hdr["underrun_count"] == u
    assert sim.xfr.xin(IPC_SPAD, 30, 1)[0] == u          # PRU1's own byte


def test_rerun_clears_handshake(sim_config):
    """Run, stop, reload both firmwares on stale SPAD, shared RAM and
    registers: the second run is identical and UNDERRUN_COUNT = 0."""
    clips = tone_clips(4)
    s = shared()
    sim, probe = pair_sim(sim_config, clips)
    # Run 1 includes an underrun, so the stale state has a non-zero count.
    _stall_rtu1_at_fetch(sim, 2, 3 * BATCH_CYC)
    run_pair(sim, 3 * BATCH_CYC)
    assert read_header(sim.memory)["underrun_count"] > 0
    stale_spad = sim.xfr.xin(IPC_SPAD, 0, 32)
    assert stale_spad[28:30] != bytes(2) and stale_spad[30] > 0

    def fresh_run(sim):
        for name in ("pru1", "rtu1"):
            sim.reset(name)
            core = sim.cores[name]
            for r in range(30):
                core.registers.write_full(r, 0xDEAD0000 + r)     # stale registers
        load_fw(sim, "rtu1", "rtu1_mod")
        load_fw(sim, "pru1", "pru1_pdm")
        probe = GpoProbe(sim.cores["pru1"])
        run_pair(sim, cycles_for(8 * BATCH), idle_skip=False)
        return probe

    # Reference: the same start from a clean simulator.
    clean, _ = pair_sim(sim_config, clips)
    first = fresh_run(clean)
    sim.cores["pru1"].io_port.gpo_listeners.clear()
    second = fresh_run(sim)
    assert second.events == first.events
    assert split_batches(second.period_bytes())[:8] == ref_batches(clips, 8)
    hdr = read_header(sim.memory)
    assert hdr["underrun_count"] == 0
    assert sim.xfr.xin(IPC_SPAD, 30, 1)[0] == 0
    assert hdr["magic"] == s["PDM8_MAGIC"]


class _MemoryRecorder:
    """Per-core view of the shared MemoryBus that logs (pc, cycle, kind, addr)."""

    def __init__(self, inner, core, log):
        self._inner, self._core, self._log = inner, core, log

    def read(self, addr, length, *, requester=None):
        self._log.append((self._core.pc, self._core.step_start_cycle, "r", addr))
        return self._inner.read(addr, length, requester=requester)

    def write(self, addr, data):
        self._log.append((self._core.pc, self._core.step_start_cycle, "w", addr))
        return self._inner.write(addr, data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_pru1_timed_path_has_no_shared_memory_access(sim_config):
    """PRU1 touches no memory at all (no DRAM1, shared RAM or MSRAM), in its
    init, its start wait, normal batches and an underrun: it has no padded
    memory slot. UNDERRUN_COUNT / BATCH_COUNT are written by RTU1."""
    sim, probe = pair_sim(sim_config, tone_clips(2))
    pru1, rtu1 = sim.cores["pru1"], sim.cores["rtu1"]
    ops = {i.opcode for i in pru1.instructions}
    assert not ops & {"LBBO", "SBBO", "LBCO", "SBCO"}
    xfr_ids = {i.operands[0].value for i in pru1.instructions
               if i.opcode in ("XIN", "XOUT")}
    assert xfr_ids == {IPC_SPAD}
    logs = {"pru1": [], "rtu1": []}
    for name, core in (("pru1", pru1), ("rtu1", rtu1)):
        core.memory = _MemoryRecorder(core.memory, core, logs[name])
    _stall_rtu1_at_fetch(sim, 2, 4 * BATCH_CYC)
    run_pair(sim, 6 * BATCH_CYC)
    assert read_header(sim.memory)["underrun_count"] > 0
    assert len(probe.clock()) > 2 * 9 * BATCH      # stall at batch ~7, then ~10 more
    assert logs["rtu1"], "the recorder sees RTU1's accesses"
    assert logs["pru1"] == []


def test_pru1_leaves_rtu1_scratch_alone():
    """PRU1 places nothing in DRAM1 0x1700..0x17FF (RTU1 scratch): no data
    sections in pru1_pdm.asm, and its linker data region ends below it."""
    asm = (FW / "pru1_pdm.asm").read_text()
    code = "\n".join(line.split(";", 1)[0] for line in asm.splitlines())
    assert not re.search(r"\.(data|bss|usect|space|word|byte)\b", code)
    cmd = (FW / "am243x-lp" / "icss_g0_pru1_fw" / "ti-pru-cgt" / "linker.cmd").read_text()
    m = re.search(r"PRU1_DMEM_1\s*:\s*org\s*=\s*(0x[0-9A-Fa-f]+)\s+len\s*=\s*(0x[0-9A-Fa-f]+)",
                  cmd)
    assert m
    org, length = int(m.group(1), 16), int(m.group(2), 16)
    assert org + length <= shared()["PDM8_RTU1_SCRATCH"]
    sections = re.findall(r"^\s*\.\w+\s*>\s*(\w+)", cmd, re.M)
    assert set(sections) <= {"PRU_IMEM", "PRU1_DMEM_1"}


def test_seq_wraps_at_16_bits(sim_config):
    """seq (R9.w0) passes 0xFFFF -> 0 every 65536 batches (~0.6 s): PRU1
    takes the wrapped seq 0 as a normal batch (no restart, no underrun).

    The wrap is reached by moving both cores' counters forward by the same
    D at PRU1's batch slot: PRU1's expected seq and ack, RTU1's batch
    count, ack target (scratch, and r9.b2 if it waits) and the SPAD bytes.
    """
    clips = tone_clips(4)
    s = shared()
    sim, probe = pair_sim(sim_config, clips)
    pru1, rtu1 = sim.cores["pru1"], sim.cores["rtu1"]
    slot_xin = pru1._parser.labels["P27_LD"] + 1
    wait_ack = rtu1._parser.labels["WAIT_ACK"]
    run_pair(sim, cycles_for(3 * BATCH), until=lambda: (
        pru1.pc == slot_xin and len(probe.clock()) > 4 * BATCH))
    assert pru1.pc == slot_xin
    regs_p, regs_r = pru1.registers, rtu1.registers
    expected = regs_p.read_full(13) & 0xFFFF
    assert struct.unpack("<H", sim.xfr.xin(IPC_SPAD, 28, 2))[0] == expected
    d = (0xFFFE - expected) & 0xFFFF            # seq 0xFFFE now, 0 two batches on
    regs_p.write_full(13, (expected + d) & 0xFFFF)
    r9 = regs_p.read_full(9)
    regs_p.write_full(9, (r9 & 0x00FFFFFF) | ((((r9 >> 24) + d) & 0xFF) << 24))
    spad = bytearray(sim.xfr.xin(IPC_SPAD, 0, 32))
    struct.pack_into("<H", spad, 28, (expected + d) & 0xFFFF)
    spad[31] = (spad[31] + d) & 0xFF
    sim.xfr.xout(IPC_SPAD, 28, bytes(spad[28:30]))
    sim.xfr.xout(IPC_SPAD, 31, bytes(spad[31:32]))
    regs_r.write_full(28, regs_r.read_full(28) + d)
    target = rtu1._map_data_addr(s["PDM8_RTU1_SCRATCH"] + 0x54)    # SCR_ACK_TARGET
    t = sim.memory.read(target, 1)[0][0]
    sim.memory.write(target, bytes([(t + d) & 0xFF]))
    if rtu1.pc in (wait_ack, wait_ack + 1):
        r9 = regs_r.read_full(9)
        regs_r.write_full(9, (r9 & 0xFF00FFFF) | ((((r9 >> 16) + d) & 0xFF) << 16))
    run_pair(sim, 8 * BATCH_CYC)
    seq = struct.unpack("<H", sim.xfr.xin(IPC_SPAD, 28, 2))[0]
    assert 3 <= seq < 0x100, "seq wrapped"
    played = split_batches(probe.period_bytes())
    assert len(played) >= 10
    assert played == ref_batches(clips, len(played))
    assert sim.xfr.xin(IPC_SPAD, 30, 1)[0] == 0
    assert read_header(sim.memory)["underrun_count"] == 0
