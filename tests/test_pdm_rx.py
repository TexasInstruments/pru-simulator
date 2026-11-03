"""pdm: PRU0 receiver pdm_rx.asm against reference.pcm_chain.

PRU1 runs pdm_gen.asm (Task 5) with the tone clips and drives the PDM clock
and one data line; PRU0 runs pdm_rx.asm on its SD channels in "pin" mode
(PdmLink, keep_history=True). The two cores run in lockstep with PRU1 as
lead. The reference of channel k is ``reference.pcm_chain`` of the bits the
channel actually sampled after its SD reinit (``link.sampled_bits[k]``), so
the whole chain PRU1 modulator -> pins -> SD hardware -> PRU0 is covered.
Rings are read with ``pdm_layout.read_ring`` while the cores run, in
chunks of at most 128 samples (ring = 256).

The N_CH = 2 run (300 PCM samples per channel, about 33 s) is shared by
three tests (``_run_two``); the whole file takes about 40 s.
"""
import math
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "source"))

from pdm import pdm_layout as L          # noqa: E402
from pdm import reference as R           # noqa: E402
from simulator import Simulator          # noqa: E402
from tests.test_pdm_gen import CLK_PIN, DAT_PIN, GEN_ASM, PDM_DIR, _tones  # noqa: E402

RX_ASM = PDM_DIR / "pdm_rx.asm"
COEFS = PDM_DIR / "fir_coefs.inc"
GEN_TOOL = PDM_DIR / "tools" / "gen_fir_coefs.py"
WINDOW = 6250            # PRU cycles per SD window (OSR 96 at 3.072 MHz, 200 MHz)
BLOCK = 128              # PCM samples per block event
CHUNK = 128              # most samples read from a ring at once
EVENT_BIT = 1 << 5       # R31 event strobe
PCM_HZ = 16000
N_SD = 3                 # SD channels the simulator models


def _set_option(src: str, name: str, value) -> str:
    src, n = re.subn(rf"^{name}(\s+)\.set\s+\S+", rf"{name}\g<1>.set    {value}", src, flags=re.M)
    assert n == 1, f"{name} .set line not found"
    return src


def _rx_source(n_ch: int = 2, **options) -> str:
    src = _set_option(RX_ASM.read_text(), "N_CH", n_ch)
    for name, value in options.items():
        src = _set_option(src, name, value)
    return src


def _fitted_amplitude(x, freq, fs=PCM_HZ):
    """Least-squares amplitude of the *freq* component of x."""
    s = [math.sin(2 * math.pi * freq * i / fs) for i in range(len(x))]
    c = [math.cos(2 * math.pi * freq * i / fs) for i in range(len(x))]
    ss, cc, sc = sum(v * v for v in s), sum(v * v for v in c), sum(a * b for a, b in zip(s, c))
    xs, xc = sum(a * b for a, b in zip(x, s)), sum(a * b for a, b in zip(x, c))
    det = ss * cc - sc * sc
    return math.hypot((xs * cc - xc * sc) / det, (xc * ss - xs * sc) / det)


class Rig:
    """PRU1 pdm_gen + PRU0 pdm_rx with N_CH pin channels, instrumented.

    Records per channel the sampled-bit index of each SD reinit, every R31
    event write (with the header PCM counts at that moment) and the PRU0
    cycle of every spin entry / exit at PDM_WAIT (with the channel index
    r21), and reads the rings while running.
    """

    def __init__(self, config, n_ch: int = 2):
        self.n_ch = n_ch
        self.sim = sim = Simulator(config_path=config)
        for ch in range(N_SD):
            sim.set_sd_source("pru0", ch, "pin" if ch < n_ch else "off")
            sim.set_sd_source("pru1", ch, "off")
        left, right = _tones()
        L.write_clips(sim.memory, left, right)
        assert sim.load("pru1", GEN_ASM.read_text(), [str(PDM_DIR)]) == []
        assert sim.load("pru0", _rx_source(n_ch), [str(PDM_DIR)]) == []
        self.link = sim.attach_pdm_link("pru1", "pru0", clock_pin=CLK_PIN,
                                        data_pins={0: DAT_PIN, 1: DAT_PIN},
                                        keep_history=True)
        core = self.core = sim.cores["pru0"]
        self.wait = core._parser.labels["PDM_WAIT"]
        self.reinits = {ch: [] for ch in range(n_ch)}
        self.events = []
        self.marks = []                      # (kind, ch, cycle)
        self.pcm = {ch: [] for ch in range(n_ch)}
        for ch in range(n_ch):
            self._wrap_reinit(ch)
        write_r31 = core.io_port.write_r31

        def recording_write_r31(value):
            if value & EVENT_BIT:
                self.events.append((value, L.read_header(sim.memory)["pcm_count"]))
            write_r31(value)

        core.io_port.write_r31 = recording_write_r31
        step_run = core.step_run
        regs = core.registers.regs

        def recording_step_run(max_cycles):
            pc0 = core.pc
            n = step_run(max_cycles)
            pc1 = core.pc
            if pc0 == self.wait and pc1 != self.wait:
                self.marks.append(("exit", regs[21], core.counters.cycles))
            elif pc1 == self.wait and pc0 != self.wait:
                self.marks.append(("entry", regs[21], core.counters.cycles))
            return n

        core.step_run = recording_step_run

    def _wrap_reinit(self, ch):
        chan = self.core.io_port.sd_filter.channels[ch]
        reinit = chan.reinit

        def recording_reinit():
            self.reinits[ch].append(len(self.link.sampled_bits[ch]))
            reinit()

        chan.reinit = recording_reinit

    def counts(self) -> list[int]:
        return L.read_header(self.sim.memory)["pcm_count"][:self.n_ch]

    def _drain(self):
        for ch, count in enumerate(self.counts()):
            have = len(self.pcm[ch])
            new = count - have
            assert 0 <= new <= CHUNK, (ch, new)
            if new:
                self.pcm[ch] += L.read_ring(self.sim.memory, ch, have, new)

    def run_pcm(self, n: int):
        """Run lockstep (PRU1 lead) until every channel has >= n PCM samples."""
        sim = self.sim
        while min(self.counts()) < n:
            sim.step_paced_many("pru1", ["pru0"], 4000, idle_skip=sim.idle_skip)
            assert not self.core.halted and self.core.fault is None
            self._drain()
        self._drain()

    def reference(self, ch: int) -> list[int]:
        assert len(self.reinits[ch]) == 1, self.reinits
        bits = self.link.sampled_bits[ch][self.reinits[ch][0]:]
        # Odd channels drop one more warm-up output (FIR stagger).
        return R.pcm_chain(bits, warmup=R.WARMUP + ch % 2)

    def step_costs(self):
        """[(ch, cycles from spin exit of ch to the next spin entry)]."""
        out = []
        for a, b in zip(self.marks, self.marks[1:]):
            if a[0] == "exit" and b[0] == "entry":
                out.append((a[1], b[2] - a[2]))
        return out

    def windows(self) -> list[int]:
        """Cycles from each spin exit of ch0 to the next spin entry of ch0."""
        exits = [c for kind, ch, c in self.marks if kind == "exit" and ch == 0]
        entries = [c for kind, ch, c in self.marks if kind == "entry" and ch == 0]
        out = []
        for t in exits:
            nxt = [e for e in entries if e > t]
            if nxt:
                out.append(nxt[0] - t)
        return out


_RUNS = {}


def _run_two(config, n_pcm=300):
    """The shared N_CH = 2 run (cached: every test pins the same 200 MHz config)."""
    if n_pcm not in _RUNS:
        rig = Rig(config, n_ch=2)
        rig.run_pcm(n_pcm)
        _RUNS[n_pcm] = rig
    return _RUNS[n_pcm]


# ---------------------------------------------------------------------------
# Coefficients
# ---------------------------------------------------------------------------

def test_fir_coefs_regenerate_identically(tmp_path):
    out = tmp_path / "fir_coefs.inc"
    subprocess.run([sys.executable, str(GEN_TOOL), "--out", str(out)], check=True)
    assert out.read_bytes() == COEFS.read_bytes()
    assert len(R.fir_table()) == 64


# ---------------------------------------------------------------------------
# Firmware, N_CH = 2
# ---------------------------------------------------------------------------

def test_pcm_matches_reference_exactly(nominal_config):
    rig = _run_two(nominal_config)
    for ch, freq in ((0, 1000), (1, 3000)):
        got = rig.pcm[ch]
        assert len(got) >= 300
        ref = rig.reference(ch)
        assert len(ref) >= len(got)
        assert got == ref[:len(got)], f"ch{ch}"
        # Sanity of the reference itself: the tone comes out at 0.5 FS
        # (generator peak 16384 = 0.5 FS), within the FIR / ZOH droop.
        level_db = 20 * math.log10(_fitted_amplitude(got[64:], freq) / (0.5 * (1 << 23)))
        assert abs(level_db) < 1.0, (ch, level_db)


def test_pcm_count_and_block_event(nominal_config):
    rig = _run_two(nominal_config)
    counts = rig.counts()
    assert counts == [len(rig.pcm[ch]) for ch in range(2)]
    last = counts[-1]
    assert last >= 2 * BLOCK
    assert [v for v, _ in rig.events] == [EVENT_BIT | 0] * (last // BLOCK)
    for k, (_, pcm_count) in enumerate(rig.events):
        assert pcm_count[1] == BLOCK * (k + 1)     # after the block's last sample and count
        assert pcm_count[0] >= BLOCK * (k + 1)     # ch0 finished the block first


def test_worst_window_fits_budget(nominal_config):
    rig = _run_two(nominal_config)
    windows = rig.windows()[4:]                   # past the warm-up windows
    assert len(windows) > 500
    worst = max(windows)
    assert worst <= WINDOW, worst

    # Worst cost of each path, from a channel's spin exit to the next spin
    # entry: ch0 stands for every channel but the last (comb only, or comb
    # + FIR); ch1 = N_CH-1 adds the round wrap and the block-event check.
    worst_path = {}
    for ch, cost in rig.step_costs()[8:]:      # past the warm-up rounds
        key = (ch, cost >= 500)                # True: this step ran the FIR
        worst_path[key] = max(worst_path.get(key, 0), cost)
    assert len(worst_path) == 4, worst_path
    w_comb = worst_path[(0, False)]
    w_fir = worst_path[(0, True)] - w_comb
    last_extra = max(worst_path[(1, False)] - w_comb,
                     worst_path[(1, True)] - w_comb - w_fir)
    spins = [b[2] - a[2] for a, b in zip(rig.marks, rig.marks[1:])
             if a[0] == "entry" and b[0] == "exit" and a[1] != 0]
    overhead = last_extra + max(spins)         # wrap + event check + spin of ch1..
    assert 2 * w_comb + w_fir + overhead >= worst
    # N_CH = 8, staggered: each window runs 8 combs and 4 FIRs.
    est8 = 8 * w_comb + 4 * w_fir + overhead
    # Pessimistic: the MAC sign correction costs one cycle per negative
    # operand, so a FIR is at most 64 cycles longer than measured (every
    # input negative), and the eight shadows may latch up to one PDM period
    # (66 cycles) apart.
    bound8 = 8 * w_comb + 4 * (w_fir + 64) + overhead + 66
    print(f"\nN_CH=2: worst window {worst} cycles; w_comb {w_comb}, w_fir {w_fir}, "
          f"overhead {overhead} (last channel +{last_extra}, spin {max(spins)}); "
          f"N_CH=8: 8*w_comb + 4*w_fir + overhead = {est8} "
          f"({100 * est8 / WINDOW:.0f} % of {WINDOW}), pessimistic {bound8} "
          f"({100 * bound8 / WINDOW:.0f} %)")
    assert est8 <= WINDOW
    assert bound8 <= WINDOW


# ---------------------------------------------------------------------------
# Build options
# ---------------------------------------------------------------------------

def _load_errors(source: str) -> list[str]:
    sim = Simulator()
    return sim.load("pru0", source, [str(PDM_DIR)])


@pytest.mark.parametrize("n_ch, options, guard", [
    (9, {}, "N_CH must be 1..8"),
    (0, {}, "N_CH must be 1..8"),
    (2, {"SD_CH_SEL_B3_CH1": "0x07"}, "SD_CH_SEL_B3_CHk must be k * 4 + 2 (ch 0..3)"),
])
def test_n_ch_build_options_guarded(n_ch, options, guard):
    assert _load_errors(_rx_source(8)) == []              # upper bound still builds
    src = _rx_source(n_ch, **options)
    errors = _load_errors(src)
    assert errors, "guard did not fail the load"
    # The simulator assembler has no .emsg: a failing guard reaches the
    # parser as an unknown directive (clpru prints the .emsg text). Check
    # that the error points at the expected guard's .emsg line.
    lines = src.splitlines()
    hits = [int(m.group(1)) for e in errors
            if (m := re.match(r"line (\d+): unknown mnemonic or unsupported directive '\.emsg'", e))]
    assert [lines[n - 1].strip() for n in hits] == [f'.emsg "{guard}"'], errors


def test_n_ch_1_still_works(nominal_config):
    rig = Rig(nominal_config, n_ch=1)
    rig.run_pcm(64)
    got = rig.pcm[0]
    assert got == rig.reference(0)[:len(got)]
    assert len(rig.events) == 0                   # 64 < one block
