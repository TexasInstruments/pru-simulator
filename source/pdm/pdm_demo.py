"""PdmDemo: the one driver of the PDM audio demo (tests, bench and UI server).

PRU1 runs ``pdm_gen.asm`` (two PDM microphones on one data line), PRU0 runs
``pdm_rx.asm`` (N_CH = 2) on its pin-driven SD channels, and the PdmLink
carries PRU1's clock and data pins into PRU0's SD inputs. ``run_chunk``
advances the pair and drains the PCM rings of shared RAM into ``history``.

Scheduling: run-ahead (``Simulator.run_ahead``, PRU1 a slice ahead) when
``one_way_violations`` is empty for the declared memory ranges, else
lockstep (``step_paced_many("pru1", ["pru0"])``). Both use
``sim.idle_skip``. The two give identical results (tests/test_pdm_e2e.py).

Clean state: ``setup`` does a hard reset, zeroes the data RAMs, the shared
RAM and the SD filter state, reloads both firmwares and attaches a fresh
link, so a second setup on a used simulator equals a fresh one. It never
relies on a single-core reset (that drives a real GPO edge into the link).
"""
from __future__ import annotations

import math
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from pru_io.sd_filter import SigmaDeltaFilter

from . import pdm_layout as L

if TYPE_CHECKING:
    from simulator import Simulator

PDM_DIR = Path(__file__).resolve().parent
GEN_ASM = PDM_DIR / "pdm_gen.asm"
RX_ASM = PDM_DIR / "pdm_rx.asm"

CLOCK_MHZ = 200.0         # both firmwares are timed for it
CHANNELS = (0, 1)         # SD ch0 = Gen L (rising edge), ch1 = Gen R (falling)
CLK_PIN, DAT_PIN = 0, 1   # PRU1 GPO pins of the PDM clock and data line
FILTER_DELAY = 64         # PCM samples drained past the clip end before done
CHUNK_CAP = 1_600_000     # PRU0 cycles = 128 PCM samples at 200 MHz
SPEED_WINDOW = 10         # chunks in the speed average
MUX_GP, MUX_SD = 0, 3     # GPCFG PRU_GP_MUX_SEL
SD_CFG_BASE, SD_CFG_SIZE = 0x26044, 0x20   # PRU0 SD config registers
DRAM = ((0x0000, 0x2000), (0x2000, 0x2000))  # DRAM0, DRAM1 (PRU0 view)
SHARED_SIZE = 0x10000

# Memory each core touches during the run, as run_ahead declares it
# (physical addresses, PRU0 view). PRU1 reads the host-written clip and
# CLIP_LEN and writes GEN_POS/GEN_DONE; PRU0 writes the PCM counts and rings
# in shared RAM and keeps its filter state in its own DRAM0 (c24), the only
# memory it reads.
PRODUCER_READS = [(L.SHARED_BASE + L.CLIP_BASE, 4 * L.CLIP_MAX_FRAMES),
                  (L.SHARED_BASE + L.HDR_CLIP_LEN, 4)]
PRODUCER_WRITES = [(L.SHARED_BASE + L.HDR_GEN_POS, 8)]      # GEN_POS, GEN_DONE
CONSUMER_WRITES = [(L.SHARED_BASE + L.HDR_PCM_COUNT, 4 * L.N_RINGS),
                   (L.SHARED_BASE + L.RING_BASE, L.N_RINGS * L.RING_STRIDE),
                   DRAM[0]]
CONSUMER_READS = [DRAM[0]]
RANGES = dict(producer_reads=PRODUCER_READS, consumer_writes=CONSUMER_WRITES,
              producer_writes=PRODUCER_WRITES, consumer_reads=CONSUMER_READS)


class PdmDemo:
    """Set up, run and drain the PDM demo on *sim* (PRU1 -> PRU0).

    *chunk_cycles* (PRU0 cycles per ``run_chunk``) is capped at
    ``CHUNK_CAP`` so at most 128 samples accumulate per chunk (ring = 256).
    *keep_link_history* records the link's edges and sampled bits (tests).
    """

    def __init__(self, sim: Simulator, *, run_ahead: bool = True,
                 chunk_cycles: int = 400_000, keep_link_history: bool = False):
        if isinstance(chunk_cycles, bool) or not isinstance(chunk_cycles, int) \
                or chunk_cycles < 1:
            raise ValueError("chunk_cycles must be a positive integer")
        self.sim = sim
        self.run_ahead = run_ahead
        self._chunk_cycles = min(chunk_cycles, CHUNK_CAP)
        self._keep_link_history = keep_link_history
        self.mode = "run-ahead" if run_ahead else "lockstep"
        self.notices: list[str] = []
        self.history: dict[int, list[int]] = {ch: [] for ch in CHANNELS}
        self.overrun: str | None = None
        self.breakpoint: str | None = None   # core the last chunk stopped at, else None
        self.clip_len = 0
        self._speed: deque[tuple[float, float]] = deque(maxlen=SPEED_WINDOW)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup(self, clip_left: Path, clip_right: Path, *, swap: bool = False) -> list[str]:
        """Build a clean demo state with the two clips; return the notices.

        Raises ValueError if a PRU clock is not 200 MHz or a clip is not
        loadable (``pdm_layout.load_clip``), RuntimeError if a firmware does
        not assemble. *swap* sends *clip_left* to Gen R and *clip_right* to
        Gen L.
        """
        sim = self.sim
        clocks = {name: sim.cores[name].clock_mhz for name in ("pru0", "pru1")}
        if not all(math.isclose(mhz, CLOCK_MHZ) for mhz in clocks.values()):
            raise ValueError(
                f"the PDM demo needs PRU0 and PRU1 at {CLOCK_MHZ:g} MHz "
                f"(configured: PRU0 {clocks['pru0']:g} MHz, PRU1 {clocks['pru1']:g} MHz)")
        left, right = L.load_clip(clip_left), L.load_clip(clip_right)
        if swap:
            left, right = right, left

        sim.detach_pdm_link()
        sim.hard_reset()
        for base, size in DRAM:
            sim.memory.write(base, bytes(size))
        sim.memory.write(L.SHARED_BASE, bytes(SHARED_SIZE))
        # SD filter state survives hard_reset: back to power-on.
        sim.memory.write(SD_CFG_BASE, bytes(SD_CFG_SIZE))
        for core in sim.cores.values():
            sd = core.io_port.sd_filter
            if sd is not None:
                sd.restore(SigmaDeltaFilter(pru_clock_mhz=sd.pru_clock_mhz).snapshot())

        for name, path in (("pru1", GEN_ASM), ("pru0", RX_ASM)):
            errors = sim.load(name, path.read_text(), [str(PDM_DIR)])
            if errors:
                raise RuntimeError(f"{path.name} does not load on {name}: {errors}")
        sim.gpcfg_write("pru0", MUX_SD)
        sim.gpcfg_write("pru1", MUX_GP)
        for ch in range(3):
            sim.set_sd_source("pru0", ch, "pin" if ch in CHANNELS else "off")
            sim.set_sd_source("pru1", ch, "off")
        sim.attach_pdm_link("pru1", "pru0", clock_pin=CLK_PIN,
                            data_pins={ch: DAT_PIN for ch in CHANNELS},
                            keep_history=self._keep_link_history)
        self.clip_len = L.write_clips(sim.memory, left, right)

        self.history = {ch: [] for ch in CHANNELS}
        self.overrun = None
        self._speed.clear()
        self.breakpoint = None
        self.notices = []
        self.mode = "run-ahead" if self.run_ahead else "lockstep"
        if self.run_ahead:
            reasons = sim.one_way_violations(
                "pru1", "pru0", PRODUCER_READS, CONSUMER_WRITES,
                producer_writes=PRODUCER_WRITES, consumer_reads=CONSUMER_READS)
            if reasons:
                self.mode = "lockstep"
                self.notices.append(
                    f"Run-ahead not possible ({'; '.join(reasons)}); running lockstep")
        return list(self.notices)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    @property
    def done(self) -> bool:
        """Both clips' PCM plus the filter delay drained."""
        return all(len(self.history[ch]) >= self.clip_len + FILTER_DELAY
                   for ch in CHANNELS)

    def run_chunk(self) -> dict:
        """Advance PRU0 by about ``chunk_cycles`` cycles and drain the rings.

        Returns ``{"new": {ch: (start_index, samples)}, "status": status()}``.
        ``breakpoint`` names the core the chunk stopped at (run-ahead stops at
        the first breakpoint; lockstep only reports a core that ends on one),
        else None; the next chunk resumes past it.
        Raises RuntimeError on a ring overrun (also kept in
        ``status()["overrun"]``, and raised again on every later call until
        the next ``setup``) or when a core faults or stops.
        """
        if self.overrun is not None:
            raise RuntimeError(self.overrun)
        self.breakpoint = None
        pru0 = self.sim.cores["pru0"]
        start_ms = self._audio_ms()
        t0 = time.perf_counter()
        if self.mode == "run-ahead":
            self.breakpoint = self.sim.run_ahead(
                "pru1", "pru0", self._chunk_cycles, **RANGES)["breakpoint"]
        else:
            self._lockstep(pru0.counters.cycles + self._chunk_cycles)
            self.breakpoint = next((name for name in ("pru0", "pru1")
                                    if self.sim.cores[name].pc
                                    in self.sim.cores[name].breakpoints), None)
        for name in ("pru1", "pru0"):
            core = self.sim.cores[name]
            if core.fault is not None or core.halted:
                raise RuntimeError(f"{name} stopped: fault {core.fault}, halted {core.halted}")
        new = self.drain()
        self._speed.append((time.perf_counter() - t0, self._audio_ms() - start_ms))
        return {"new": new, "status": self.status()}

    def _lockstep(self, target: int) -> None:
        """PRU1-led paced stepping until PRU0 reaches *target* cycles.

        Lead steps go in batches small enough (remaining / 64) that the
        last ones are single steps, so PRU0 stops just past *target*.
        """
        sim = self.sim
        pru0 = sim.cores["pru0"]
        while (remaining := target - pru0.counters.cycles) > 0:
            before = pru0.counters.cycles
            sim.step_paced_many("pru1", ["pru0"], max(1, min(4096, remaining // 64)),
                                idle_skip=sim.idle_skip)
            if pru0.counters.cycles == before and (
                    sim.cores["pru1"].halted or pru0.halted):
                break

    def drain(self) -> dict[int, tuple[int, list[int]]]:
        """Copy every new ring sample into ``history``; ``{ch: (start, samples)}``.

        A ring holds 256 samples: more than that since the last drain is an
        overrun, recorded in ``overrun`` and raised as RuntimeError.
        """
        counts = L.read_header(self.sim.memory)["pcm_count"]
        new = {}
        for ch in CHANNELS:
            have = len(self.history[ch])
            n = counts[ch] - have
            if n > L.RING_SAMPLES:
                self.overrun = (f"PCM ring {ch} overrun: "
                                f"{n - L.RING_SAMPLES} samples lost")
                raise RuntimeError(self.overrun)
            samples = L.read_ring(self.sim.memory, ch, have, n) if n > 0 else []
            self.history[ch].extend(samples)
            new[ch] = (have, samples)
        return new

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _audio_ms(self) -> float:
        pru0 = self.sim.cores["pru0"]
        return pru0.counters.cycles / (pru0.clock_mhz * 1000)

    def status(self) -> dict:
        wall = sum(w for w, _ in self._speed)
        audio = sum(a for _, a in self._speed)
        load = {}
        for name in ("pru0", "pru1"):
            core = self.sim.cores[name]
            cycles = core.counters.cycles
            load[name] = (cycles - core.skipped_cycles) / cycles if cycles else 0.0
        return {
            "audio_ms": self._audio_ms(),
            "clip_ms": self.clip_len * 1000 / L.PCM_RATE,
            "speed_ms_per_s": audio / wall if wall > 0 else 0.0,
            "load": load,
            "mode": self.mode,
            "overrun": self.overrun,
            "done": self.done,
            "notices": list(self.notices),
        }
