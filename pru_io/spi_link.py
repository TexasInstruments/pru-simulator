"""SPI link model: PRU0 (controller) <-> PRU1 (edge-polling peripheral).

Protocol constants, rate table, test patterns, a passive bus monitor that acts
as scope and independent decoder, and the timing measurement behind the SPI
view's parameter table (spec docs/superpowers/specs/2026-11-04-spi-pru0-pru1-design.md).

Mode semantics (CPOL = 0 only). Mode 1: data set on CLK rising, captured on CLK
falling. Mode 0: captured on rising, set on falling, first bit valid before the
first rising edge.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from pru_io.device_model import DeviceModel

# R30/R31 bit numbers
C_CLK, C_MOSI, C_MISO, C_CS = 16, 13, 14, 17
P_CLK, P_MOSI, P_CS, P_MISO = 1, 2, 6, 11

# Memory map (global ICSSG addresses; each PRU uses its own DMEM)
CTRL = 0x0000
C_TX = 0x0100
C_RX = 0x0110
C_RX_HDR = 0x0120
P_STATUS = 0x2000
P_TX = 0x2800
P_RX = 0x3000

CMD_WRITE, CMD_READ, CMD_RW = 1, 2, 3
ADDR_MAX = 0x7F
FRAME_BYTES = 19
HOP_DELAY_CYCLES = 5          # Ruling R6, confirmed by single-stepping

CORE_MHZ = (200, 225, 250, 300, 333)

# Output skew limit, AM243 Table 6-113 PRDO1 (SPRSP65J)
SKEW_NS = 2.0


@dataclass(frozen=True)
class SpiConfig:
    mode: int
    core_mhz: int
    half_k: int
    miso_delay: int = 0

    def __post_init__(self):
        if self.mode not in (0, 1):
            raise ValueError(f"SPI mode must be 0 or 1, got {self.mode}")
        if self.core_mhz not in CORE_MHZ:
            raise ValueError(f"core clock must be one of {CORE_MHZ}, got {self.core_mhz}")
        if self.half_k < 3:
            raise ValueError(f"half_k must be >= 3, got {self.half_k}")
        rate = self.spi_mhz
        if not 10 <= rate <= 50:
            raise ValueError(f"SPI rate {rate:.3f} MHz outside 10..50 MHz")
        if rate == 50 and self.core_mhz == 200:
            raise ValueError("50 MHz is excluded at 200 MHz core clock")

    @property
    def spi_mhz(self) -> float:
        return self.core_mhz / (2 * self.half_k)

    @property
    def id(self) -> str:
        return f"m{self.mode}_c{self.core_mhz}_k{self.half_k}_d{self.miso_delay}"


def rate_candidates() -> list[tuple[int, int]]:
    out = []
    for core in CORE_MHZ:
        for k in range(1, core // 20 + 2):
            rate = core / (2 * k)
            if 10 <= rate <= 50 and k >= 3 and not (rate == 50 and core == 200):
                out.append((core, k))
    out.sort(key=lambda ck: (ck[0] / (2 * ck[1]), ck[0]))
    return out


def render_header(cfg: SpiConfig, role: str = "controller") -> str:
    lines = [f"; SPI link {cfg.id}: mode {cfg.mode}, {cfg.spi_mhz:.3f} MHz "
             f"at {cfg.core_mhz} MHz core",
             f"SPI_MODE .set {cfg.mode}"]
    if role == "controller":
        lines += [f"HALF_K .set {cfg.half_k}", f"MISO_DELAY .set {cfg.miso_delay}"]
    elif role != "peripheral":
        raise ValueError(f"unknown role {role!r}")
    return "\n".join(lines) + "\n"


_W1 = bytes([1 << (i % 8) for i in range(16)])
_W0 = bytes([(~(1 << (i % 8))) & 0xFF for i in range(16)])

#: name -> (controller TX 16 B, peripheral TX 16 B)
PATTERNS: dict[str, tuple[bytes, bytes]] = {
    "a5_5a": (bytes([0xA5, 0x5A] * 8), bytes([0x5A, 0xA5] * 8)),
    "walking1": (_W1, _W0),
    "walking0": (_W0, _W1),
    "count": (bytes(range(16)), bytes(range(0xF0, 0x100))),
    "toggle": (bytes([0x00, 0xFF] * 8), bytes([0xFF, 0x00] * 8)),
}


# ---------------------------------------------------------------------------
# Edge-list helpers (shared by the monitor and measure_timing)
# ---------------------------------------------------------------------------

def _cs(w): return w & 1
def _clk(w): return (w >> 1) & 1
def _mosi(w): return (w >> 2) & 1
def _miso(w): return (w >> 3) & 1


def _split_frames(edges, mode):
    """Decode frames from (cycle, word) edges. Only /CS-terminated frames."""
    frames = []
    prev = None
    cur = None
    for cyc, w in edges:
        if prev is not None:
            if _cs(prev) == 1 and _cs(w) == 0:
                cur = {"start": cyc, "end": None, "bits": 0, "mosi": [], "miso": [],
                       "recs": [(cyc, w)]}
            elif cur is not None:
                cur["recs"].append((cyc, w))
                if _cs(w) == 1:
                    cur["end"] = cyc
                    frames.append(cur)
                    cur = None
                elif _clk(prev) != _clk(w):
                    rising = _clk(w) == 1
                    if rising == (mode == 0):          # capture edge
                        cur["mosi"].append(_mosi(w))
                        cur["miso"].append(_miso(w))
        prev = w
    return frames


def _pack(bits):
    out = bytearray()
    for i in range(0, len(bits) - len(bits) % 8, 8):
        v = 0
        for b in bits[i:i + 8]:
            v = (v << 1) | b
        out.append(v)
    return bytes(out)


class SpiBusMonitor(DeviceModel):
    """Passive logic analyser + independent decoder on PRU0's four SPI pins."""

    name = "spi_monitor"
    nets: dict[int, str] = {}
    time_driven = False

    def __init__(self, mode: int = 1, name: str = "spi_monitor"):
        self.name = name
        self.mode = mode
        self.edges: list[tuple[int, int]] = []
        self._last: int | None = None
        self._faults: list[str] = []

    def tick(self, cycle: int, bus: int):
        w = (((bus >> C_CS) & 1) | (((bus >> C_CLK) & 1) << 1)
             | (((bus >> C_MOSI) & 1) << 2) | (((bus >> C_MISO) & 1) << 3))
        if w != self._last:
            if self._last is not None and _cs(w) == 1 and _cs(self._last) == 1 \
                    and _clk(w) != _clk(self._last):
                self._faults.append(f"cycle {cycle}: CLK edge while /CS high")
            self.edges.append((cycle, w))
            self._last = w
        return 0, 0

    def frames(self, mode: int | None = None) -> list[dict]:
        mode = self.mode if mode is None else mode
        out = []
        for f in _split_frames(self.edges, mode):
            out.append({"start": f["start"], "end": f["end"],
                        "mosi": _pack(f["mosi"]), "miso": _pack(f["miso"]),
                        "bits": len(f["mosi"]),
                        "aborted": len(f["mosi"]) != FRAME_BYTES * 8})
        return out

    def events(self) -> list[dict]:
        return [{"cycle": f["end"], "kind": "frame", **f} for f in self.frames()]

    def faults(self) -> list[str]:
        return list(self._faults)

    def clear(self) -> None:
        self.edges = []
        self._last = None

    def reset(self) -> None:
        self.clear()
        self._faults = []

    def snapshot(self) -> dict:
        return {"edges": list(self.edges), "last": self._last,
                "faults": list(self._faults)}

    def restore(self, snap: dict) -> None:
        self.edges = list(snap["edges"])
        self._last = snap["last"]
        self._faults = list(snap["faults"])

    def get_state(self) -> dict:
        fr = self.frames()
        return {"name": self.name, "faults": len(self._faults),
                "edges": len(self.edges), "frames": len(fr),
                "aborted": sum(1 for f in fr if f["aborted"])}


# ---------------------------------------------------------------------------
# Timing measurement
# ---------------------------------------------------------------------------

def measure_timing(edges, cfg: SpiConfig, frame_index: int = -1) -> list[dict]:
    """Timing rows for one frame of an edge list. See spec section 6."""
    ns = 1000.0 / cfg.core_mhz
    poll = ns                                   # one core cycle of polling uncertainty
    tclk_d = 1000.0 / cfg.spi_mhz
    half_d = tclk_d / 2
    frames = _split_frames(edges, cfg.mode)
    sim: dict[str, float | None] = {}
    k = None
    if frames:
        k = frame_index if frame_index >= 0 else len(frames) + frame_index
        if not 0 <= k < len(frames):
            k = None
    if k is not None:
        f = frames[k]
        recs = f["recs"]
        rise = [c for (c, w), (_, p) in zip(recs[1:], recs[:-1])
                if _clk(w) == 1 and _clk(p) == 0]
        fall = [c for (c, w), (_, p) in zip(recs[1:], recs[:-1])
                if _clk(w) == 0 and _clk(p) == 1]
        launch, capture = (rise, fall) if cfg.mode == 1 else (fall, rise)
        if len(rise) >= 2:
            sim["tCLK"] = statistics.median(b - a for a, b in zip(rise, rise[1:])) * ns
        hi = [min((b for b in fall if b > a), default=None) - a
              for a in rise if any(b > a for b in fall)]
        lo = [min((b for b in rise if b > a), default=None) - a
              for a in fall if any(b > a for b in rise)]
        if hi and lo:
            sim["tH"] = statistics.median(hi) * ns
            sim["tL"] = statistics.median(lo) * ns
        if rise:
            sim["tLEAD"] = (rise[0] - f["start"]) * ns
        if fall:
            sim["tLAG"] = (f["end"] - fall[-1]) * ns
        # tCSH: gap to the following frame, else the gap after the preceding one
        if k + 1 < len(frames):
            sim["tCSH"] = (frames[k + 1]["start"] - f["end"]) * ns
        elif k > 0:
            sim["tCSH"] = (f["start"] - frames[k - 1]["end"]) * ns

        def changes(sel):
            return [c for (c, w), (_, p) in zip(recs[1:], recs[:-1]) if sel(w) != sel(p)]

        def around(chg, cap):
            su = [c - max(x for x in chg if x <= c) for c in cap
                  if any(x <= c for x in chg)]
            hd = [min(x for x in chg if x > c) - c for c in cap
                  if any(x > c for x in chg)]
            return su, hd

        mc = changes(_mosi)
        su, hd = around(mc, capture)
        if su:
            sim["tSU,MOSI"] = min(su) * ns
        if hd:
            sim["tH,MOSI"] = min(hd) * ns
        ic = changes(_miso)
        v = [c - max(x for x in launch if x <= c) for c in ic if any(x <= c for x in launch)]
        if v:
            sim["tV,MISO"] = max(v) * ns
        # PRU0 samples each bit at its MISO launch edge + miso_delay cycles
        # (Ruling R7), which may fall after the next launch edge when pipelined.
        su, hd = [], []
        bounds = launch[1:] + [float("inf")]
        for lc, nxt in zip(launch, bounds):
            sp = lc + cfg.miso_delay
            own = [x for x in ic if lc <= x < nxt]       # this bit's MISO change
            if not own:
                continue                                 # constant MISO: nothing to measure
            su.append(sp - own[0])                       # negative: sampled before the change
            after = [x for x in ic if x > sp]
            if after:
                hd.append(min(after) - sp)
        if su:
            sim["tSU,MISO"] = min(su) * ns
        if hd:
            sim["tH,MISO"] = min(hd) * ns

    th = sim.get("tH")
    tl = sim.get("tL")
    rows: list[dict] = []

    def row(param, symbol, design_ns, sim_ns, lo, hi, source, margin, cycles=None,
            derived=False):
        if cycles is None and sim_ns is not None:
            cycles = round(sim_ns / ns)
        rows.append({"param": param, "symbol": symbol,
                     "design_ns": design_ns, "cycles": cycles, "sim_ns": sim_ns,
                     "am243_min": lo, "am243_max": hi, "source": source,
                     "margin_ns": margin, "worst": False, "derived": derived})

    def budget(s):
        return None if s is None else s - SKEW_NS - poll

    s = sim.get
    row("SPI clock period", "tCLK", tclk_d, s("tCLK"), None, None,
        "no direct-mode limit", None, cycles=2 * cfg.half_k)
    row("CLK high / low time", "tH/tL", half_d,
        th, None, None, "PRDO1 skew",
        None if th is None else min(th, tl) - SKEW_NS, cycles=cfg.half_k)
    row("Output skew CLK / MOSI / CS (PRU0 pins)", "tsk(GPO-GPO)", 0.0, 0.0,
        None, SKEW_NS, "Table 6-113 PRDO1", SKEW_NS, cycles=0,
        derived=True)   # the simulator models no pad skew
    row("/CS low to first CLK edge", "tLEAD", tclk_d, s("tLEAD"), -SKEW_NS, SKEW_NS,
        "PRDO1", None if s("tLEAD") is None else s("tLEAD") - SKEW_NS,
        cycles=2 * cfg.half_k)
    row("Last CLK edge to /CS high", "tLAG", tclk_d, s("tLAG"), -SKEW_NS, SKEW_NS,
        "PRDO1", None if s("tLAG") is None else s("tLAG") - SKEW_NS,
        cycles=2 * cfg.half_k)
    row("/CS high between frames", "tCSH", 2 * tclk_d, s("tCSH"), None, None,
        "design (min 2 tCLK)",
        None if s("tCSH") is None else s("tCSH") - 2 * tclk_d, cycles=4 * cfg.half_k)
    row("MOSI setup before PRU1 capture edge", "tSU,MOSI", half_d, s("tSU,MOSI"),
        None, None, "design budget", budget(s("tSU,MOSI")), cycles=cfg.half_k)
    row("MOSI hold after PRU1 capture edge", "tH,MOSI", half_d, s("tH,MOSI"),
        None, None, "design budget", budget(s("tH,MOSI")), cycles=cfg.half_k)
    row("PRU1: CLK launch edge to MISO valid", "tV,MISO", None, s("tV,MISO"),
        None, None, "firmware only", None)
    row("MISO setup before PRU0 capture edge", "tSU,MISO", None, s("tSU,MISO"),
        None, None, "design budget", budget(s("tSU,MISO")))
    row("MISO hold after PRU0 capture edge", "tH,MISO", None, s("tH,MISO"),
        None, None, "design budget", budget(s("tH,MISO")))
    row("PRU0 MISO sample delay in effect", "MISO_DELAY", cfg.miso_delay * ns,
        cfg.miso_delay * ns, None, None, "firmware only", None, cycles=cfg.miso_delay)
    row("Modelled hop delay (pin to pin)", "HOP", HOP_DELAY_CYCLES * ns,
        HOP_DELAY_CYCLES * ns, None, None, "simulator calibration", None,
        cycles=HOP_DELAY_CYCLES)
    row("Input slew rate (all inputs)", "SRI", None, None, 1.0, 3.0, "Table 6-112",
        None, cycles=None)
    row("Output load capacitance", "CL", None, None, 2.0, 30.0, "Table 6-112",
        None, cycles=None)
    row("Idle byte budget for PRU1 data fetch", "tIDLE", 8 * tclk_d, 8 * tclk_d,
        None, None, "firmware only", None, cycles=16 * cfg.half_k,
        derived=True)   # design value; replaced by PRU1's measured fetch time

    cand = [r for r in rows if r["margin_ns"] is not None and not r["derived"]]
    if cand:
        min(cand, key=lambda r: r["margin_ns"])["worst"] = True
    else:
        rows[0]["worst"] = True
    return rows
