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
from pathlib import Path

from pru_io.device_model import PUSH_PULL, DeviceModel

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
# PRU1 firmware scratch blocks in DMEM1 (not host data; do not write):
P_ZERO_BLK = 0x27F0   # 16 zero bytes: MISO data source for frames that do not read
P_RX_DUMMY = 0x3800   # 16 B: commit target for frames that must not write

# CTRL block field offsets and status values
CTRL_GO, CTRL_CMD, CTRL_ADDR, CTRL_STATUS, CTRL_FRAMES, CTRL_ABORT = 0, 1, 2, 3, 4, 8
ST_IDLE, ST_BUSY, ST_DONE = 0, 1, 2

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
        self._starts: list[int] = []     # indices in ``edges`` of /CS falling edges

    #: Complete frames kept in ``edges`` (measure_timing needs two for tCSH);
    #: older edges are dropped so per-frame work stays bounded.
    KEEP_FRAMES = 8

    def tick(self, cycle: int, bus: int):
        w = (((bus >> C_CS) & 1) | (((bus >> C_CLK) & 1) << 1)
             | (((bus >> C_MOSI) & 1) << 2) | (((bus >> C_MISO) & 1) << 3))
        if w != self._last:
            if self._last is not None and _cs(w) == 1 and _cs(self._last) == 1 \
                    and _clk(w) != _clk(self._last):
                self._faults.append(f"cycle {cycle}: CLK edge while /CS high")
            if self._last is not None and _cs(self._last) == 1 and _cs(w) == 0:
                self._starts.append(len(self.edges))
            self.edges.append((cycle, w))
            if (self._last is not None and _cs(self._last) == 0 and _cs(w) == 1
                    and len(self._starts) > self.KEEP_FRAMES):
                cut = self._starts[-self.KEEP_FRAMES] - 1    # keep the idle edge before it
                del self.edges[:cut]
                self._starts = [i - cut for i in self._starts[-self.KEEP_FRAMES:]]
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
        # The device bus publishes events as JSON: payload bytes travel as hex.
        return [{"cycle": f["end"], "kind": "frame", **f,
                 "mosi": f["mosi"].hex(), "miso": f["miso"].hex()}
                for f in self.frames()]

    def faults(self) -> list[str]:
        return list(self._faults)

    def clear(self) -> None:
        self.edges = []
        self._starts = []
        self._last = None

    def reset(self) -> None:
        self.clear()
        self._faults = []

    def snapshot(self) -> dict:
        return {"edges": list(self.edges), "last": self._last,
                "faults": list(self._faults), "starts": list(self._starts)}

    def restore(self, snap: dict) -> None:
        self.edges = list(snap["edges"])
        self._starts = list(snap.get("starts", []))
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

def measure_timing(edges, cfg: SpiConfig, frame_index: int = -1,
                   p1_fetch_cycles: int | None = None,
                   hop_delay: int = HOP_DELAY_CYCLES) -> list[dict]:
    """Timing rows for one frame of an edge list. See spec section 6.

    ``hop_delay``: the GPI delay on both cores (Ruling R7a). The MISO rows
    refer PRU0's sample to the pin: its delayed R31 read at launch +
    miso_delay sees the pin as it was ``hop_delay`` cycles earlier. A MISO
    change belongs to the latest launch edge at least ``hop_delay`` cycles
    before it (PRU1 sees the edge one hop late), which also keeps tV,MISO
    from wrapping when the sample is pipelined. MOSI rows are not affected
    (the delay shifts CLK and MOSI equally at PRU1).

    ``p1_fetch_cycles``: PRU1's idle-byte TX fetch time, single-stepped
    (``measure_peripheral_work``). Given, the tIDLE row reports it against
    the idle-byte budget as a measured row; otherwise tIDLE is the derived
    design value.
    """
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
        # PRU1 cannot answer a launch edge before it sees it, one hop later
        # (R7a): a MISO change at c belongs to the latest launch <= c - hop.
        hp = hop_delay
        v = [c - max(x for x in launch if x <= c - hp) for c in ic
             if any(x <= c - hp for x in launch)]
        if v:
            sim["tV,MISO"] = max(v) * ns
        # PRU0 samples each bit at its MISO launch edge + miso_delay cycles
        # (Ruling R7), which may fall after the next launch edge when pipelined;
        # at the pin that read sees the state hop_delay cycles earlier (R7a).
        su, hd = [], []
        bounds = launch[1:] + [float("inf")]
        for lc, nxt in zip(launch, bounds):
            sp = lc + cfg.miso_delay - hop_delay
            own = [x for x in ic if lc + hp <= x < nxt + hp]   # this bit's MISO change
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
        hop_delay * ns, None, None, "simulator calibration", None,
        cycles=hop_delay)
    row("Input slew rate (all inputs)", "SRI", None, None, 1.0, 3.0, "Table 6-112",
        None, cycles=None)
    row("Output load capacitance", "CL", None, None, 2.0, 30.0, "Table 6-112",
        None, cycles=None)
    if p1_fetch_cycles is None:
        row("Idle byte budget for PRU1 data fetch", "tIDLE", 8 * tclk_d, 8 * tclk_d,
            None, None, "firmware only", None, cycles=16 * cfg.half_k,
            derived=True)   # design value; no PRU1 measurement given
    else:
        fetch_ns = p1_fetch_cycles * ns
        row("Idle byte budget for PRU1 data fetch", "tIDLE", 8 * tclk_d, fetch_ns,
            None, None, "PRU1 fetch, stepped", 8 * tclk_d - fetch_ns,
            cycles=p1_fetch_cycles)

    cand = [r for r in rows if r["margin_ns"] is not None and not r["derived"]]
    if cand:
        min(cand, key=lambda r: r["margin_ns"])["worst"] = True
    else:
        rows[0]["worst"] = True
    return rows


# ---------------------------------------------------------------------------
# Controller firmware runtime
# ---------------------------------------------------------------------------

CONTROLLER_SOURCE = (Path(__file__).resolve().parents[1]
                     / "source" / "spi" / "spi_controller.asm")
PERIPHERAL_SOURCE = CONTROLLER_SOURCE.with_name("spi_peripheral.asm")
#: Deepest MISO pipelining the controller unrolls (MISO_DELAY // (2*HALF_K)).
MISO_Q_MAX = 6
_C_MASK = (1 << C_CLK) | (1 << C_MOSI) | (1 << C_CS)
_FRAME_BITS = FRAME_BYTES * 8


def controller_delay_ok(half_k: int, miso_delay: int) -> bool:
    """Can spi_controller.asm sample MISO ``miso_delay`` cycles after launch?

    The sample is an R31 read in its own cycle, so it cannot fall on the
    cycle of a CLK write (offset 0 or ``half_k`` within a bit), and the
    firmware unrolls at most ``MISO_Q_MAX`` bits of pipelining.
    """
    if isinstance(miso_delay, bool) or not isinstance(miso_delay, int) or miso_delay < 0:
        return False
    s = miso_delay % (2 * half_k)
    return s not in (0, half_k) and miso_delay // (2 * half_k) <= MISO_Q_MAX


class IdealSpiPeripheral(DeviceModel):
    """Test-only ideal SPI peripheral on PRU0's pins (attach on ``"pru0"``).

    Drives MISO (pin 14, push-pull) with ``response`` (19 bytes, MSB first),
    each bit ``reaction_cycles`` after its launch edge as seen on PRU0's pins:
    mode 1 after each CLK rising edge, mode 0 bit 0 after /CS falling and bit
    n after the n-th falling edge. MISO returns to 0 ``reaction_cycles``
    after /CS rises. Model the PRU1 input hop by including it in
    ``reaction_cycles``; PRU0's own GPI delay adds the return hop.
    """

    name = "ideal_spi_peripheral"
    time_driven = True

    def __init__(self, mode: int, response: bytes, reaction_cycles: int,
                 name: str = "ideal_spi_peripheral"):
        if mode not in (0, 1):
            raise ValueError("mode must be 0 or 1")
        response = bytes(response)
        if len(response) != FRAME_BYTES:
            raise ValueError(f"response must be {FRAME_BYTES} bytes")
        if (isinstance(reaction_cycles, bool) or not isinstance(reaction_cycles, int)
                or reaction_cycles < 0):
            raise ValueError("reaction_cycles must be a non-negative integer")
        self.mode = mode
        self.response = response
        self.reaction_cycles = reaction_cycles
        self.name = name
        self.nets = {C_MISO: PUSH_PULL}
        self.reset()

    def _bit(self, n: int) -> int:
        if n >= _FRAME_BITS:
            return 0
        return (self.response[n // 8] >> (7 - n % 8)) & 1

    def _schedule(self, cycle: int, level: int) -> None:
        self._pending.append((cycle + self.reaction_cycles, level))

    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        cs = (bus >> C_CS) & 1
        clk = (bus >> C_CLK) & 1
        if cs != self._cs:
            if cs == 0:
                self._n = 0
                if self.mode == 0:
                    self._schedule(cycle, self._bit(0))
            else:
                self._frames += 1
                self._schedule(cycle, 0)
        elif cs == 0 and clk != self._clk:
            if self.mode == 1 and clk == 1:
                self._schedule(cycle, self._bit(self._n))
                self._n += 1
            elif self.mode == 0 and clk == 0:
                self._n += 1
                self._schedule(cycle, self._bit(self._n))
        self._cs, self._clk = cs, clk
        while self._pending and self._pending[0][0] <= cycle:
            self._level = self._pending.pop(0)[1]
        return 1 << C_MISO, self._level << C_MISO

    def reset(self) -> None:
        self._cs, self._clk, self._n, self._level = 1, 0, 0, 0
        self._pending: list[tuple[int, int]] = []
        self._frames = 0

    def snapshot(self) -> dict:
        return {"cs": self._cs, "clk": self._clk, "n": self._n,
                "level": self._level, "pending": list(self._pending),
                "frames": self._frames}

    def restore(self, snap: dict) -> None:
        self._cs, self._clk, self._n = snap["cs"], snap["clk"], snap["n"]
        self._level, self._frames = snap["level"], snap["frames"]
        self._pending = list(snap["pending"])

    def get_state(self) -> dict:
        return {"name": self.name, "mode": self.mode, "frames": self._frames,
                "bit": self._n, "miso": self._level}


class SpiLinkRuntime:
    """Load, wire and drive the PRU0 controller (and later the PRU1 peripheral).

    ``load_controller`` can be called again (for a new ``cfg``) without
    touching the wiring; the monitor stays attached and follows ``cfg.mode``.
    """

    def __init__(self, sim, cfg: SpiConfig, hop_delay: int = HOP_DELAY_CYCLES):
        self.sim = sim
        self.cfg = cfg
        self.hop_delay = hop_delay
        self.monitor: SpiBusMonitor | None = None
        self._masks = {core: sim.io(core)["gpo_drive_mask"] for core in ("pru0", "pru1")}
        self._wires: list[tuple[str, int, str, int]] = []
        self._controller_loaded = False
        self._peripheral_loaded = False
        self._peripheral_mode: int | None = None

    # -- setup -----------------------------------------------------------------

    def _set_delay(self, core: str) -> None:
        # Setting the delay restarts its history: only touch it on a change.
        if self.sim.get_gpi_delay(core) != self.hop_delay:
            self.sim.set_gpi_delay(core, self.hop_delay)

    def load_controller(self) -> None:
        cfg = self.cfg
        if not controller_delay_ok(cfg.half_k, cfg.miso_delay):
            raise ValueError(
                f"miso_delay {cfg.miso_delay} not realisable at half_k {cfg.half_k}: "
                f"miso_delay mod {2 * cfg.half_k} must not be 0 or {cfg.half_k} "
                f"and miso_delay // {2 * cfg.half_k} must be <= {MISO_Q_MAX}; "
                f"choose miso_delay with controller_delay_ok()")
        source = render_header(cfg) + CONTROLLER_SOURCE.read_text(encoding="utf-8")
        errors = self.sim.load("pru0", source,
                               include_paths=[str(CONTROLLER_SOURCE.parent)])
        if errors:
            raise ValueError("SPI controller assembly failed: " + "; ".join(errors))
        self.sim.reset("pru0")
        self.sim.memory.write(CTRL, bytes(16))
        # The reset zeroed R30 (/CS low). Put /CS idle high before the pins
        # are driven so a (re)load never shows PRU1 a /CS pulse; the
        # firmware's first instruction sets the same state.
        pru0 = self.sim.cores["pru0"]
        pru0.registers.write_full(30, 1 << C_CS)
        pru0.io_port.write_r30(1 << C_CS)
        self.sim.set_gpio_drive_mask("pru0", _C_MASK)
        self._set_delay("pru0")
        if self.monitor is None:
            self.monitor = SpiBusMonitor(cfg.mode)
            self.sim.attach_device("pru0", self.monitor)
        else:
            self.monitor.mode = cfg.mode
        self._controller_loaded = True

    def reload_controller(self, cfg: SpiConfig) -> None:
        """Reassemble PRU0 only for ``cfg``; PRU1, wires and monitor stay.

        The PRU1 firmware is rate independent but assembled for one SPI
        mode, so ``cfg.mode`` must match it once PRU1 is loaded.
        """
        if self._peripheral_loaded and cfg.mode != self._peripheral_mode:
            raise ValueError(f"PRU1 runs mode {self._peripheral_mode}; "
                             f"reload_controller got mode {cfg.mode}")
        old = self.cfg
        self.cfg = cfg
        try:
            self.load_controller()
        except Exception:
            self.cfg = old
            raise
        self._align_time_base()

    def _align_time_base(self) -> None:
        """Ruling R10: one time base after a reload. Loading PRU0 reset its
        cycle counter (and its GPI delay history); reset PRU1's with it and
        clear the monitor, so PRU0- and PRU1-driven edges share a clock."""
        if self._peripheral_loaded:
            pru1 = self.sim.cores["pru1"]
            pru1.counters.reset()
            io = pru1.io_port
            io._device_cycle = 0     # device-bus stamp of PRU1's pin writes (as io_port.reset)
            io.gpi_delay_cycles = io.gpi_delay_cycles   # restart history at cycle 0
        if self.monitor is not None:
            self.monitor.clear()
            io0 = self.sim.cores["pru0"].io_port   # seed with the idle bus word
            self.monitor.tick(0, io0.gpo | io0.gpi)

    def load_peripheral(self) -> None:
        """Assemble and start the PRU1 edge-polling peripheral (mode = cfg.mode).

        Its header carries only SPI_MODE: nothing depends on the rate.
        """
        source = (render_header(self.cfg, role="peripheral")
                  + PERIPHERAL_SOURCE.read_text(encoding="utf-8"))
        errors = self.sim.load("pru1", source,
                               include_paths=[str(PERIPHERAL_SOURCE.parent)])
        if errors:
            raise ValueError("SPI peripheral assembly failed: " + "; ".join(errors))
        self.sim.reset("pru1")
        self.sim.memory.write(P_STATUS, bytes(16))
        pru1 = self.sim.cores["pru1"]
        pru1.registers.write_full(30, 0)
        pru1.io_port.write_r30(0)
        self.sim.set_gpio_drive_mask("pru1", 1 << P_MISO)
        self._set_delay("pru1")
        self._peripheral_loaded = True
        self._peripheral_mode = self.cfg.mode

    # -- PRU1 memory -----------------------------------------------------------

    @staticmethod
    def _block(addr: int) -> int:
        if isinstance(addr, bool) or not isinstance(addr, int) or not 0 <= addr <= ADDR_MAX:
            raise ValueError(f"block address must be 0..0x{ADDR_MAX:02X}, got {addr!r}")
        return 16 * addr

    def preload_peripheral_tx(self, addr: int, data: bytes) -> None:
        """Write PRU1 TX block ``addr`` (the source of READ and R/W data)."""
        off = self._block(addr)
        data = bytes(data)
        if len(data) != 16:
            raise ValueError("TX block must be 16 bytes")
        self.sim.memory.write(P_TX + off, data)

    def read_peripheral_rx(self, addr: int) -> bytes:
        """PRU1 RX block ``addr`` (committed only at the end of a full frame)."""
        return bytes(self.sim.memory_read(P_RX + self._block(addr), 16))

    def peripheral_status(self) -> dict:
        """PRU1 status block: frames (completed, valid or not), errors (completed
        with an unknown command or address > 0x7F), aborts (/CS high before
        bit 152), last_cmd / last_addr (last completed frame)."""
        raw = bytes(self.sim.memory_read(P_STATUS, 14))
        u32 = lambda i: int.from_bytes(raw[i:i + 4], "little")  # noqa: E731
        return {"frames": u32(0), "errors": u32(4), "aborts": u32(8),
                "last_cmd": raw[12], "last_addr": raw[13]}

    def find_miso_window(self, delays=None, addr: int = 0x7E,
                         pattern: str = "count", contiguous: bool = False) -> list[int]:
        """miso_delay values for which PRU0 reads PRU1 correctly at this rate.

        For each candidate (default: every ``controller_delay_ok`` value up to
        Q = MISO_Q_MAX) PRU0 is reassembled with that delay and runs one R/W
        frame against PRU1 TX block ``addr``; a delay passes when PRU0 gets
        the whole MISO frame (header zeros and the 16 data bytes) right. The
        original ``cfg`` is restored. PRU1 RX/TX block ``addr`` and its status
        counters change.

        ``contiguous``: stop once the window has closed, i.e. after two
        realisable delays in a row fail following a pass (the sweep uses this;
        a later second window would read a neighbouring bit and cannot pass).
        """
        if not (self._controller_loaded and self._peripheral_loaded and self._wires):
            raise RuntimeError("load_controller(), load_peripheral() and wire() first")
        cfg = self.cfg
        k = cfg.half_k
        if delays is None:
            delays = [d for d in range(1, 2 * k * (MISO_Q_MAX + 1))
                      if controller_delay_ok(k, d)]
        c_tx, p_tx = PATTERNS[pattern]
        self.preload_peripheral_tx(addr, p_tx)
        window = []
        misses = 0
        try:
            for d in delays:
                if not controller_delay_ok(k, d):
                    continue
                self.reload_controller(SpiConfig(cfg.mode, cfg.core_mhz, k, d))
                res = self.transfer(CMD_RW, addr, c_tx)
                if res["miso"] == bytes(3) + p_tx:
                    window.append(d)
                    misses = 0
                elif window:
                    misses += 1
                    if contiguous and misses >= 2:
                        break
        finally:
            self.reload_controller(cfg)
        return window

    def wire(self) -> None:
        """Spec 5.3 nets; each has exactly one driver."""
        if self._wires:
            return
        wires = [("pru0", C_CLK, "pru1", P_CLK), ("pru0", C_MOSI, "pru1", P_MOSI),
                 ("pru0", C_CS, "pru1", P_CS), ("pru1", P_MISO, "pru0", C_MISO)]
        for w in wires:
            self.sim.add_gpio_wire(*w)
            self._wires.append(w)
        self.sim.set_gpio_drive_mask("pru1", 1 << P_MISO)

    # -- transfers -------------------------------------------------------------

    def _step(self) -> None:
        if self._peripheral_loaded:
            self.sim.step_paced_many("pru0", ["pru1"], 1)
        else:
            self.sim.step("pru0")

    def transfer(self, cmd: int, addr: int, data: bytes = bytes(16),
                 max_cycles: int = 20000,
                 abort_after_bits: int | None = None) -> dict:
        if not self._controller_loaded:
            raise RuntimeError("load_controller() first")
        data = bytes(data)
        if len(data) != 16:
            raise ValueError("data must be 16 bytes")
        for name, v in (("cmd", cmd), ("addr", addr)):
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 0xFF:
                raise ValueError(f"{name} must be a byte")
        abort = 0 if abort_after_bits is None else abort_after_bits
        if (isinstance(abort, bool) or not isinstance(abort, int)
                or not 0 <= abort < _FRAME_BITS):
            raise ValueError("abort_after_bits must be None or 0 (full frame), "
                             f"or 1..{_FRAME_BITS - 1} bits")
        mem = self.sim.memory
        mem.write(C_TX, data)
        mem.write(CTRL + CTRL_CMD, bytes([cmd, addr, ST_IDLE]))
        mem.write(CTRL + CTRL_ABORT, abort.to_bytes(2, "little"))
        seen = self.monitor.frames(self.cfg.mode)
        last_end = seen[-1]["end"] if seen else -1     # the monitor keeps only recent frames
        pru0 = self.sim.cores["pru0"]
        start = pru0.counters.cycles
        mem.write(CTRL + CTRL_GO, b"\x01")
        status = CTRL + CTRL_STATUS
        while self.sim.memory_read(status, 1)[0] != ST_DONE:
            if pru0.counters.cycles - start > max_cycles:
                raise TimeoutError(f"SPI transfer not done after {max_cycles} cycles")
            if pru0.halted or pru0.fault is not None:
                raise RuntimeError(f"PRU0 stopped: fault={pru0.fault}")
            self._step()
        cycles = pru0.counters.cycles - start
        frames = self.monitor.frames(self.cfg.mode)
        rx_hdr = self.sim.memory_read(C_RX_HDR, 3)
        c_rx = self.sim.memory_read(C_RX, 16)
        return {"miso": rx_hdr + c_rx, "c_rx": c_rx, "cycles": cycles,
                "frame": frames[-1] if frames and frames[-1]["end"] > last_end else None}

    def close(self) -> None:
        for w in self._wires:
            self.sim.remove_gpio_wire(*w)
        self._wires = []
        if self.monitor is not None:
            if self.monitor in self.sim.device_bus.devices:
                self.sim.detach_device(self.monitor)
            self.monitor = None
        for core, mask in self._masks.items():
            self.sim.set_gpio_drive_mask(core, mask)
        for core in ("pru0", "pru1"):
            self.sim.set_gpi_delay(core, 0)
        self._controller_loaded = False
        self._peripheral_loaded = False


# ---------------------------------------------------------------------------
# Acceptance sequence, PRU1 work measurement, supported-rate table
# ---------------------------------------------------------------------------

#: Committed sweep result (tools/spi_sweep.py --write).
RATES_PATH = Path(__file__).resolve().with_name("spi_rates.json")


def choose_miso_delay(passing: list[int]) -> int | None:
    """Ruling R9: the passing (controller_delay_ok) value nearest the middle
    of the passing window; on a tie the earlier one (pin-referred MISO
    setup and hold are then the more balanced at k = 3, see the sweep)."""
    if not passing:
        return None
    mid = (min(passing) + max(passing)) / 2
    return min(passing, key=lambda d: (abs(d - mid), d))


def measure_peripheral_work(runtime: "SpiLinkRuntime", cmd: int, addr: int,
                            data: bytes) -> dict:
    """Single-step one frame and measure PRU1's work, not counting waiting.

    Every poll iteration that branched back to its own label (the edge was
    not visible yet) is dropped; everything else counts for the bit whose
    poll label precedes it. Returns ``bit_work`` {bit: cycles},
    ``data_bit_cycles`` (data bits 24..150 without gap work: polls + MISO
    drive), ``data_bit_cycles_max`` (a data bit that also carries gap work),
    ``data_byte_cycles``
    (max over the steady data bytes 4..17), ``max_byte_cycles`` (max over
    bytes 0..17, incl. fetch and gather work in the gaps) and
    ``fetch_cycles`` (the idle-byte TX fetch, every LBBO in the frame).
    """
    import bisect

    rt = runtime
    sim = rt.sim
    pru1 = sim.cores["pru1"]
    trace = []
    orig = pru1.step

    def step():
        pc, c0 = pru1.pc, pru1.counters.cycles
        orig()
        trace.append((pc, pru1.counters.cycles - c0, pru1.pc))

    pru1.step = step
    try:
        rt.transfer(cmd, addr, data)
    finally:
        del pru1.step
    labels = dict(pru1._parser.labels)
    mode = rt.cfg.mode
    polls = sorted((v, int(k[4:])) for k, v in labels.items()
                   if k.startswith((f"m{mode}_l", f"m{mode}_c")))
    poll_pcs = {v for v, _ in polls}
    keys = [v for v, _ in polls]
    first, end = polls[0][0], labels["frame_end"]
    seq = []
    for pc, dc, nxt in trace:
        if pc == first and not seq:
            seq.append((pc, dc, nxt))
        elif seq:
            if pc == end:
                break
            seq.append((pc, dc, nxt))
    work: dict[int, int] = {}
    fetch = 0
    j = 0
    while j < len(seq):
        pc = seq[j][0]
        if pc in poll_pcs:
            back = next((i for i in range(j, min(j + 4, len(seq))) if seq[i][2] == pc), None)
            if back is not None:
                j = back + 1
                continue
        if pru1.instructions[pc].opcode == "LBBO":
            fetch += seq[j][1]
        n = polls[bisect.bisect_right(keys, pc) - 1][1]
        work[n] = work.get(n, 0) + seq[j][1]
        j += 1
    per_byte = [sum(work.get(8 * m + i, 0) for i in range(8)) for m in range(FRAME_BYTES)]
    data_bits = [work[n] for n in range(24, 151)]
    return {"bit_work": work,
            "data_bit_cycles": min(data_bits),
            "data_bit_cycles_max": max(data_bits),
            "data_byte_cycles": max(per_byte[4:18]),
            "max_byte_cycles": max(per_byte[:18]),
            "fetch_cycles": fetch}


def run_test_sequence(runtime: "SpiLinkRuntime", patterns=None) -> dict:
    """Spec section 9 acceptance sequence on a loaded, wired link.

    Per pattern: WRITE a block, READ a preloaded TX block, R/W full duplex.
    Then an unknown command, an out-of-range address, a mid-frame /CS abort,
    and two back-to-back R/W frames at the controller's minimum /CS-high gap
    (the last two frames on the bus, so ``measure_timing(..., frame_index=-2)``
    sees tCSH). Each step checks the MISO bytes PRU0 received, the MOSI bytes
    on the bus (monitor decode), every PRU1 RX block, and the PRU1 status
    counters. ``byte_errors`` counts wrong bytes over all checks; ``ok`` also
    needs every status and the gap check right.
    """
    rt = runtime
    names = list(PATTERNS) if patterns is None else list(patterns)
    steps: list[dict] = []
    k = rt.cfg.half_k

    def rx_all():
        return bytes(rt.sim.memory_read(P_RX, 16 * (ADDR_MAX + 1)))

    def diff(a, b):
        return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))

    def frame(step, cmd, addr, data, miso, rx_write=None, valid=True, abort=None):
        before_rx = rx_all()
        st = rt.peripheral_status()
        res = rt.transfer(cmd, addr, data, abort_after_bits=abort)
        errs = 0
        notes = []
        fr = res["frame"]
        if abort is None:
            errs += diff(res["miso"], miso)
            mosi = bytes([cmd, addr, 0]) + bytes(data)
            if fr is None or fr["aborted"]:
                errs += FRAME_BYTES
                notes.append("no complete frame on the bus")
            else:
                errs += diff(fr["mosi"], mosi)
        elif fr is None or fr["bits"] != abort or not fr["aborted"]:
            notes.append(f"abort frame not seen with {abort} bits")
        exp_rx = bytearray(before_rx)
        if rx_write is not None:
            exp_rx[16 * addr:16 * addr + 16] = rx_write
        errs += diff(rx_all(), bytes(exp_rx))
        want = dict(st)
        if abort is None:
            want["frames"] += 1
            want["errors"] += 0 if valid else 1
            want["last_cmd"], want["last_addr"] = cmd, addr
        else:
            want["aborts"] += 1
        got = rt.peripheral_status()
        if got != want:
            notes.append(f"status {got} != {want}")
        s = {"step": step, "cmd": cmd, "addr": addr, "byte_errors": errs,
             "ok": errs == 0 and not notes, "notes": notes, "cycles": res["cycles"],
             "frame": fr}
        steps.append(s)
        return s

    for i, name in enumerate(names):
        c_tx, p_tx = PATTERNS[name]
        aw, ar, arw = 3 * i, ADDR_MAX - i, 0x40 + i
        frame(f"write:{name}", CMD_WRITE, aw, c_tx, bytes(FRAME_BYTES), rx_write=c_tx)
        rt.preload_peripheral_tx(ar, p_tx)
        frame(f"read:{name}", CMD_READ, ar, c_tx, bytes(3) + p_tx)
        rt.preload_peripheral_tx(arw, p_tx)
        frame(f"rw:{name}", CMD_RW, arw, c_tx, bytes(3) + p_tx, rx_write=c_tx)

    c_tx, p_tx = PATTERNS[names[0]]
    rt.preload_peripheral_tx(0x10, p_tx)
    frame("unknown_cmd", 0x04, 0x10, c_tx, bytes(FRAME_BYTES), valid=False)
    frame("bad_addr", CMD_RW, 0x80, c_tx, bytes(FRAME_BYTES), valid=False)
    rt.preload_peripheral_tx(0x20, p_tx)
    frame("abort", CMD_RW, 0x20, c_tx, None, abort=76)

    c2, p2 = PATTERNS[names[-1]]
    rt.preload_peripheral_tx(0x30, p_tx)
    rt.preload_peripheral_tx(0x31, p2)
    a = frame("back_to_back:1", CMD_RW, 0x30, c_tx, bytes(3) + p_tx, rx_write=c_tx)
    b = frame("back_to_back:2", CMD_RW, 0x31, c2, bytes(3) + p2, rx_write=c2)
    gap = None
    if a["frame"] is not None and b["frame"] is not None:
        gap = b["frame"]["start"] - a["frame"]["end"]
    notes = [] if gap is not None and gap >= 4 * k else [f"/CS-high gap {gap} < 2 tCLK"]
    steps.append({"step": "back_to_back", "byte_errors": 0, "ok": not notes,
                  "notes": notes, "tcsh_cycles": gap, "frame": None})

    for s in steps:
        s.pop("frame", None)
    total = sum(s["byte_errors"] for s in steps)
    return {"ok": all(s["ok"] for s in steps) and total == 0,
            "byte_errors": total, "steps": steps}


def load_supported(path: Path | str | None = None) -> dict[int, list[dict]]:
    """Supported rates per mode from the committed sweep (spi_rates.json).

    Mode -> passing rows at the supported hop delay, one per SPI rate (the
    lowest passing core clock), sorted by rate.
    """
    import json

    data = json.loads(Path(path or RATES_PATH).read_text(encoding="utf-8"))
    hop = data["hop_delay_supported"]
    out: dict[int, list[dict]] = {0: [], 1: []}
    for mode in out:
        best: dict[float, dict] = {}
        for r in data["rows"]:
            if r["mode"] != mode or r["hop_delay"] != hop or not r["pass"]:
                continue
            key = round(r["spi_mhz"], 6)
            if key not in best or r["core_mhz"] < best[key]["core_mhz"]:
                best[key] = r
        out[mode] = [best[key] for key in sorted(best)]
    return out
