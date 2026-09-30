"""pif_eth_100 driver: PRU0 TX -> PRU1 RX at 100 Mbaud (n_tx=3, 300 MHz cores).

Headless counterpart of the browser demo (USERS_GUIDE.md). BERT only:
200 B xorshift32 payload + 4 B FCS per frame.
Spec: docs/superpowers/specs/2026-09-30-pif-eth-100-design.md

    python3 source/pif_eth_100/run_100.py              # 7 seeds x 3 frames, base RX
    python3 source/pif_eth_100/run_100.py --rx both    # base vs fast RX goodput

All figures are simulator measurements, not silicon claims.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from simulator import Simulator                          # noqa: E402

from pif_eth_100 import codec                             # noqa: E402
from pif_eth_100.crc32 import fcs_bytes                   # noqa: E402
from pif_eth_100.decoder import decode_bits               # noqa: E402
from pif_eth_100.prng import DEFAULT_SEED, prng_bytes     # noqa: E402

CONFIG_PATH = str(_ROOT / "config" / "memory_pif_eth_100.cfg")
TX_FIRMWARE = "pif_eth_100_tx.asm"
RX_FIRMWARE = {"base": "pif_eth_100_rx.asm", "fast": "pif_eth_100_rx_fast.asm"}

# --- clock plan (spec section 5) ---------------------------------------------
CORE_MHZ = 300.0
N_TX = 3
RX_DIV = 1.5
T_BIT_NS = 10.0
T_SAMPLE_NS = 5.0
TXCFG_100 = 0x00020010      # div_factor=2, frac=0, clk_sel=core -> n=3
RXCFG_100 = 0x0000801F      # div_factor=0, frac=1 (1.5), clk_sel, sb_pol=1, sample_size=7
TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100

# --- frame (spec section 6) --------------------------------------------------
PAYLOAD_LEN = 200
MAX_PAYLOAD_LEN = 252        # 256 B RX frame buffer - 4 B FCS

# Task 2 spike verdict: 0.0 unless the edge-tie phase forced the 5/6 ns fallback.
# second latency gate (Task 5): edge-tie phase fails at 0 ns + inexact shift; 5/6 ns keeps every sample >= 0.83 ns from a TX edge
LOOPBACK_LATENCY_NS = 5.0 / 6.0
EDGE_PHASE_SHIFTS_NS = (0.0, 10.0 / 3.0, 20.0 / 3.0)   # visits all 3 sample/edge residues
SEEDS = (DEFAULT_SEED, 1, 2, 3, 4, 5, 6)
HOST_POLL_INSTR = 16         # host re-checks DRAM flags every 16 PRU0 instructions

# --- DRAM0: PRU0 TX (global == PRU0 local) -----------------------------------
LUT0_ADDR = 0x0000
T_NUMF, T_MODE, T_SEED, T_PLEN = 0x0400, 0x0404, 0x0408, 0x040C
T_FCNT, T_BURST, T_GOFLAG = 0x0410, 0x0414, 0x0418
T_COREBUF = 0x0500

# --- DRAM1: PRU1 RX, GLOBAL addresses (firmware local = global - 0x2000) ----
LUT1_ADDR = 0x2000
CAP_ADDR = 0x2800
NIB_LUT_ADDR = 0x2C00        # fast RX only: 256 B 2:1 decimation LUT
FRAME_ADDR = 0x2E00
STATS_ADDR = 0x2F00
CTRL_ADDR = 0x2F40
S_FRAMES, S_CAPBYTES, S_OVF, S_SYMERR = (STATS_ADDR + o for o in (0x00, 0x04, 0x08, 0x0C))
S_CRCOK, S_BITERR, S_TOTBITS, S_EOF = (STATS_ADDR + o for o in (0x10, 0x14, 0x18, 0x1C))
C_MODE, C_SEED, C_PLEN, C_GO, C_RXCFG = (CTRL_ADDR + o for o in (0x00, 0x04, 0x08, 0x0C, 0x10))


def burst_fifo_bytes(payload_len: int) -> int:
    """TX FIFO bytes per burst: (4 + 2 commas + payload + 4 FCS) x 10 bits, padded to bytes."""
    return -(-(payload_len + 4 + 6) * 10 // 8)


BURST_FIFO_BYTES = burst_fifo_bytes(PAYLOAD_LEN)          # 263


def validate_payload_len(n: int) -> None:
    """TX loads 4 octets per LBBO; the RX frame buffer is 256 B including the FCS."""
    if n < 4 or n > MAX_PAYLOAD_LEN or n % 4:
        raise ValueError(f"payload_len={n}: must be 4..{MAX_PAYLOAD_LEN} and a multiple "
                         f"of 4 (TX 4-octet loads; 256 B RX frame buffer incl. 4 B FCS)")


def wu32(sim: Simulator, addr: int, val: int) -> None:
    sim.memory.write(addr, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def ru32(sim: Simulator, addr: int) -> int:
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def step_paced_traced(sim: Simulator, count: int, on_follow=None,
                      guard_ns: float = 20.0) -> None:
    """Mirror of ``Simulator.step_paced("pru0", "pru1", count)`` that reports
    every PRU1 instruction as ``on_follow(pc_before, cycles_delta, core)``.

    Same loop, same guard: PRU1 only runs while its perif clock trails PRU0's
    by more than *guard_ns*, so RX never samples line history TX has not
    recorded yet. This is how cycle-budget claims are single-step validated.
    """
    lead, follow = sim.cores["pru0"], sim.cores["pru1"]
    lead_perif, follow_perif = sim._perif["pru0"], sim._perif["pru1"]
    for _ in range(count):
        if not lead.halted and lead.pc < len(lead.instructions):
            lead.step()
        target = lead_perif._now_ns - guard_ns
        safety = 1000
        while (follow_perif._now_ns < target and safety > 0 and not follow.halted
               and follow.pc < len(follow.instructions)):
            pc_before, cyc_before = follow.pc, follow.counters.cycles
            follow.step()
            safety -= 1
            if on_follow is not None:
                on_follow(pc_before, follow.counters.cycles - cyc_before, follow)


def run_until_label(sim: Simulator, label: str, max_lead_steps: int = 400_000) -> None:
    """Pace both cores until PRU1 has executed the instruction at *label*.

    Never poll ``pc == label`` between multi-instruction steps: a 2-instruction
    wait loop can alias and never match. Never step PRU1 alone either: it would
    run ahead of PRU0's time.
    """
    target = sim.cores["pru1"]._parser.labels[label]
    hit = [False]

    def on_follow(pc, dc, core):
        if pc == target:
            hit[0] = True

    steps = 0
    while not hit[0]:
        if steps >= max_lead_steps:
            raise RuntimeError(f"PRU1 did not reach label {label!r} within "
                               f"{max_lead_steps} PRU0 steps")
        step_paced_traced(sim, HOST_POLL_INSTR, on_follow)
        steps += HOST_POLL_INSTR


def _load(sim: Simulator, core: str, name: str) -> None:
    errors = sim.load(core, (_HERE / name).read_text(), include_paths=[str(_HERE)])
    if errors:
        raise RuntimeError(f"{name} load failed on {core}: {errors}")


def build_sim(seed: int = DEFAULT_SEED, num_frames: int = 1,
              payload_len: int = PAYLOAD_LEN, latency_ns: float = LOOPBACK_LATENCY_NS,
              rx: str = "base", load_rx: bool = True, rxcfg: int = RXCFG_100) -> Simulator:
    """Seeded simulator with TX at hs_wait and (if *load_rx*) RX at go_wait.

    No host TXCFG override: the TX firmware's own self-config is the truth.
    """
    validate_payload_len(payload_len)
    sim = Simulator(config_path=CONFIG_PATH)
    sim.memory.write(LUT0_ADDR, codec.build_dram0_lut())
    for addr, val in ((T_NUMF, num_frames), (T_MODE, 0), (T_SEED, seed),
                      (T_PLEN, payload_len), (T_FCNT, 0), (T_BURST, 0), (T_GOFLAG, 0)):
        wu32(sim, addr, val)
    sim.gpcfg_write("pru0", 1)
    _load(sim, "pru0", TX_FIRMWARE)
    sim.step("pru0", 60)                       # TX prologue: self-config -> hs_wait
    if load_rx:
        sim.memory.write(LUT1_ADDR, codec.build_dram1_decode_lut())
        for addr, val in ((C_MODE, 0), (C_SEED, seed), (C_PLEN, payload_len),
                          (C_GO, 0), (C_RXCFG, rxcfg)):
            wu32(sim, addr, val)
        for addr in (S_FRAMES, S_CAPBYTES, S_OVF, S_SYMERR, S_CRCOK, S_BITERR,
                     S_TOTBITS, S_EOF):
            wu32(sim, addr, 0)
        sim.gpcfg_write("pru1", 1)
        sim.perif_loopback(0, True, latency_ns=latency_ns, jitter_ns=0.0, drift_ppm=0.0)
        _load(sim, "pru1", RX_FIRMWARE[rx])
        run_until_label(sim, "go_wait")
    return sim


@dataclass
class TxResult:
    seed: int
    payload_len: int
    frames_ok: list[bool] = field(default_factory=list)
    invalid_symbols: list[int] = field(default_factory=list)
    burst_pushed: list[int] = field(default_factory=list)
    t_go_ns: list[float] = field(default_factory=list)
    t_end_ns: list[float] = field(default_factory=list)
    min_spacing_ns: float = float("inf")
    spacing_on_grid: bool = True
    tx_period_ns: float = 0.0

    @property
    def clean(self) -> bool:
        want = burst_fifo_bytes(self.payload_len)
        return (bool(self.frames_ok) and all(self.frames_ok)
                and not any(self.invalid_symbols)
                and all(p == want for p in self.burst_pushed)
                and self.spacing_on_grid)


def _collect_burst(sim: Simulator, ch, dm: dict, stream: bytes, i: int,
                   res: TxResult) -> None:
    """Decode burst *i* from PRU0's line history (before the next burst starts)."""
    n = res.payload_len
    pushed = ru32(sim, T_BURST)
    go, period = ch._go_ns, ch.tx_clock_period_ns()
    bits = [ch.tx_line_at(go + (k + 0.5) * period) for k in range(pushed * 8)]
    dec = decode_bits(bits, dm)
    payload = stream[i * n:(i + 1) * n]
    res.frames_ok.append(bool(dec.frames) and dec.frames[0] == payload + fcs_bytes(payload))
    res.invalid_symbols.append(dec.invalid_symbols)
    res.burst_pushed.append(pushed)
    end = go + pushed * 8 * period
    ts = [t for t, _ in ch.tx_transitions if go <= t <= end + 1e-6]
    for a, b in zip(ts, ts[1:]):
        d = b - a
        res.min_spacing_ns = min(res.min_spacing_ns, d)
        k = d / T_BIT_NS
        if abs(k - round(k)) * T_BIT_NS > 1e-6:
            res.spacing_on_grid = False


def run_tx_only(seed: int = DEFAULT_SEED, num_frames: int = 4,
                payload_len: int = PAYLOAD_LEN, max_lead_steps: int = 2_000_000) -> TxResult:
    """TX-limited regime: PRU1 not loaded; the host re-arms `go` as soon as TX
    consumes it (during the running burst), so frames run back to back with no
    host latency. Each burst is decoded from the line history right after it
    ends: tx_transitions keeps only the last 4096 entries (~1.9 bursts)."""
    sim = build_sim(seed, num_frames, payload_len, load_rx=False)
    ch = sim._perif["pru0"].channels[0]
    dm = codec.build_decode_map()
    stream = prng_bytes(payload_len * num_frames, seed)
    res = TxResult(seed=seed, payload_len=payload_len, tx_period_ns=ch.tx_clock_period_ns())
    wu32(sim, T_GOFLAG, 1)
    started, done, steps = 1, 0, 0
    while done < num_frames:
        if steps >= max_lead_steps:
            raise RuntimeError(f"TX-only seed={seed}: frame {done} did not finish")
        sim.step("pru0", HOST_POLL_INSTR)
        steps += HOST_POLL_INSTR
        if started < num_frames and ru32(sim, T_GOFLAG) == 0:
            wu32(sim, T_GOFLAG, 1)             # queue the next frame
            started += 1
        if ru32(sim, T_FCNT) > done:
            _collect_burst(sim, ch, dm, stream, done, res)
            done += 1
    res.t_go_ns = [t for t, v in ch.tx_out_en_transitions if v == 1]
    res.t_end_ns = [t for t, v in ch.tx_out_en_transitions if v == 0]
    return res


# --- TX -> RX loopback (spec section 8, F3-E2E regime) -----------------------

@dataclass
class FrameStats:
    frames: int
    cap_bytes: int
    rx_ovf: int
    symbol_errors: int
    crc_ok: int
    bit_err: int
    tot_bits: int
    eof_status: int
    frame_ok: bool

    def clean(self, payload_len: int) -> bool:
        return (self.rx_ovf == 0 and self.symbol_errors == 0 and self.crc_ok == 1
                and self.bit_err == 0 and self.tot_bits == payload_len * 8
                and self.eof_status == 1 and self.frame_ok)


@dataclass
class LoopResult:
    seed: int
    rx: str
    latency_ns: float
    payload_len: int
    num_frames: int
    frames: list[FrameStats] = field(default_factory=list)
    t_go_ns: list[float] = field(default_factory=list)
    t_end_ns: list[float] = field(default_factory=list)
    rx_post_cycles: list[int] = field(default_factory=list)   # PRU1: eof -> frame_loop
    max_rx_fifo: int = 0
    hot_deltas: dict[int, set[int]] = field(default_factory=dict)  # offset from poll -> cycles seen

    @property
    def hot_loop_cycles_per_byte(self) -> int:
        """Worst case for one stored byte on the poll path: qbbc fall-through (1) + body."""
        return 1 + sum(max(v) for v in self.hot_deltas.values())

    @property
    def clean(self) -> bool:
        return (len(self.frames) == self.num_frames
                and all(f.clean(self.payload_len) for f in self.frames))

    @property
    def anchor_risk(self) -> bool:
        """A bad CRC with zero symbol errors = symbol grid locked onto a false comma."""
        return any(f.crc_ok == 0 and f.symbol_errors == 0 for f in self.frames)


def run_loopback(seed: int = DEFAULT_SEED, num_frames: int = 3,
                 payload_len: int = PAYLOAD_LEN, latency_ns: float = LOOPBACK_LATENCY_NS,
                 rx: str = "base", max_lead_steps: int = 4_000_000) -> LoopResult:
    """RX-gated regime: frame i+1 is released only after PRU1 published frame i.

    Every PRU1 instruction is traced (step_paced_traced) for: max RX FIFO
    occupancy, per-instruction cycle cost of the poll hot path, and PRU1
    cycles from `eof` back to `frame_loop` (post-frame time).
    """
    sim = build_sim(seed, num_frames, payload_len, latency_ns, rx=rx)
    labels = sim.cores["pru1"]._parser.labels
    poll, eof, frame_loop = labels["poll"], labels["eof"], labels["frame_loop"]
    rxch = sim._perif["pru1"].channels[0]
    res = LoopResult(seed=seed, rx=rx, latency_ns=latency_ns,
                     payload_len=payload_len, num_frames=num_frames)
    eof_at: list = [None]

    def on_follow(pc, dc, core):
        depth = len(rxch.rx_fifo)
        if depth > res.max_rx_fifo:
            res.max_rx_fifo = depth
        off = pc - poll
        if 1 <= off <= 6:
            res.hot_deltas.setdefault(off, set()).add(dc)
        if pc == eof:
            eof_at[0] = core.counters.cycles - dc
        elif pc == frame_loop and eof_at[0] is not None:
            res.rx_post_cycles.append(core.counters.cycles - dc - eof_at[0])
            eof_at[0] = None

    stream = prng_bytes(payload_len * num_frames, seed)
    for i in range(num_frames):
        wu32(sim, C_GO, 1)          # arm RX first ...
        wu32(sim, T_GOFLAG, 1)      # ... then TX (~2.7k cycles of prep before its first bit)
        steps = 0
        while ru32(sim, S_FRAMES) != i + 1:
            if steps >= max_lead_steps:
                raise RuntimeError(f"seed={seed} rx={rx} frame {i}: no RX result "
                                   f"within {max_lead_steps} PRU0 steps")
            step_paced_traced(sim, HOST_POLL_INSTR, on_follow)
            steps += HOST_POLL_INSTR
        payload = stream[i * payload_len:(i + 1) * payload_len]
        res.frames.append(FrameStats(
            frames=ru32(sim, S_FRAMES), cap_bytes=ru32(sim, S_CAPBYTES),
            rx_ovf=ru32(sim, S_OVF), symbol_errors=ru32(sim, S_SYMERR),
            crc_ok=ru32(sim, S_CRCOK), bit_err=ru32(sim, S_BITERR),
            tot_bits=ru32(sim, S_TOTBITS), eof_status=ru32(sim, S_EOF),
            frame_ok=sim.memory_read(FRAME_ADDR, payload_len + 4)
            == payload + fcs_bytes(payload)))
    step_paced_traced(sim, 64, on_follow)   # let PRU1 reach frame_loop after the last frame
    tx = sim._perif["pru0"].channels[0]
    res.t_go_ns = [t for t, v in tx.tx_out_en_transitions if v == 1]
    res.t_end_ns = [t for t, v in tx.tx_out_en_transitions if v == 0]
    return res
