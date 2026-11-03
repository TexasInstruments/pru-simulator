"""pdm: PdmDemo driver end to end (spec §12.4-12.6).

``PdmDemo`` is the one driver the tests and the UI server share: PRU1 runs
pdm_gen.asm, PRU0 runs pdm_rx.asm (N_CH = 2), the PdmLink carries the PDM
clock and data line, and every chunk the driver drains the PCM rings into
``history``.

* Tones and reference: 1 kHz on L and 3 kHz on R, run to ``done``. The
  default run uses the first 576 frames of the tone clips (36 ms clip + 64
  samples filter delay = 40 ms of audio); ``PDM_SLOW=1`` adds the full
  0.25 s clips. Frequency by zero crossings, crosstalk by Hann-windowed
  Goertzel; the history equals ``reference.pcm_chain_for_channel`` of the
  bits the link recorded (full chain, PRU1 modulator included).
* Chirp (``PDM_SLOW=1`` only, the full 0.25 s clip): the spectrogram ridge
  follows the linear chirp law.
* Equivalence gates (merge blockers, always run): 20 ms of audio with the
  idle skip on vs off (lockstep both) and run-ahead vs lockstep (skip on
  both) give identical history, registers, counters, memory, SD/IEP/link
  state, link edge history and per-core GPO edges.

The repo has no slow marker; the opt-in ``PDM_SLOW=1`` follows audio_pipe.
Default run of this file: about 3 minutes.
"""
import math
import os
import struct
import sys
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
sys.path.insert(0, str(REPO / "source"))

from pdm import pdm_layout as L          # noqa: E402
from pdm.pdm_demo import CHUNK_CAP, PdmDemo  # noqa: E402
from simulator import Simulator          # noqa: E402
from tests.pdm_helpers import (goertzel_db, link_reference, peak_bins,  # noqa: E402
                               pipeline_delay, record_reinits, zero_crossing_hz)
from tests.test_idle_skip import _probe_pins, _state  # noqa: E402
from tests.test_pdm_gen import CLIPS     # noqa: E402
from tests.test_run_ahead import _assert_same, _step_alone_to  # noqa: E402

PCM_HZ = 16000
FILTER_DELAY = 64        # samples PdmDemo waits for past the clip end
SHORT_FRAMES = 576       # default tone run: 36 ms clip -> done at 40 ms
EQUIV_MS = 20            # audio of each equivalence gate
CYCLES_PER_MS = 200_000  # PRU0 at 200 MHz

SLOW = os.environ.get("PDM_SLOW") == "1"
slow = pytest.mark.skipif(not SLOW, reason="full 0.25 s clip runs take minutes; set PDM_SLOW=1")


def _short_clip(src: Path, frames: int, out_dir: Path) -> Path:
    """Copy of the WAV *src* cut to its first *frames* frames."""
    out = out_dir / f"{src.stem}_{frames}.wav"
    with wave.open(str(src), "rb") as r:
        params = r.getparams()
        raw = r.readframes(frames)
    with wave.open(str(out), "wb") as w:
        w.setparams(params)
        w.writeframes(raw)
    return out


def _demo(config, left, right, *, run_ahead=True, skip=True, keep=False,
          probe=False, **kw):
    sim = Simulator(config_path=config)
    sim.idle_skip = skip
    pins = _probe_pins(sim) if probe else None
    demo = PdmDemo(sim, run_ahead=run_ahead, keep_link_history=keep, **kw)
    assert demo.setup(left, right) == []
    return demo, pins


def _check_new(demo, before, out):
    """run_chunk's ``new``: contiguous with the history, at most 128 samples."""
    for ch, (start, samples) in out["new"].items():
        assert start == before[ch]
        assert len(samples) <= 128
        assert demo.history[ch][start:] == samples


def _run_to_done(demo, max_chunks=2000):
    for _ in range(max_chunks):
        if demo.done:
            return
        before = {ch: len(h) for ch, h in demo.history.items()}
        _check_new(demo, before, demo.run_chunk())
    raise AssertionError("demo not done")


def _run_audio_ms(demo, ms):
    while demo.status()["audio_ms"] < ms:
        before = {ch: len(h) for ch, h in demo.history.items()}
        _check_new(demo, before, demo.run_chunk())


_RUNS = {}


def _tone_run(config, variant):
    """Tones to ``done`` with link history and reinit indices (cached per variant)."""
    if variant not in _RUNS:
        left, right = CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav"
        if variant == "short":
            out_dir = Path(config).parent
            left = _short_clip(left, SHORT_FRAMES, out_dir)
            right = _short_clip(right, SHORT_FRAMES, out_dir)
        demo, _ = _demo(config, left, right, keep=True)
        reinits = record_reinits(demo.sim, demo.sim._pdm_link, (0, 1))
        _run_to_done(demo)
        _RUNS[variant] = (demo, reinits)
    return _RUNS[variant]


TONE_VARIANTS = ["short", pytest.param("full", marks=slow)]


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", TONE_VARIANTS)
def test_tones_end_to_end(nominal_config, variant):
    demo, _ = _tone_run(nominal_config, variant)
    status = demo.status()
    clip_len = L.read_header(demo.sim.memory)["clip_len"]
    assert clip_len == (SHORT_FRAMES if variant == "short" else 4000)
    assert status["done"] and status["mode"] == "run-ahead"
    assert status["overrun"] is None and status["notices"] == []
    assert status["clip_ms"] == clip_len * 1000 / PCM_HZ
    assert status["audio_ms"] >= status["clip_ms"] + FILTER_DELAY * 1000 / PCM_HZ
    assert status["speed_ms_per_s"] > 0
    assert set(status["load"]) == {"pru0", "pru1"}
    assert all(0 <= v <= 1 for v in status["load"].values())
    for ch in (0, 1):
        assert len(demo.history[ch]) >= clip_len + FILTER_DELAY

    own = {0: 1000, 1: 3000}
    other = {0: 3000, 1: 1000}
    report = []
    for ch in (0, 1):
        x = demo.history[ch][FILTER_DELAY:clip_len]   # past the filter warm-up
        f = zero_crossing_hz(x, PCM_HZ)
        assert abs(f / own[ch] - 1) <= 0.01, (ch, f)
        crosstalk = goertzel_db(x, other[ch], PCM_HZ) - goertzel_db(x, own[ch], PCM_HZ)
        assert crosstalk <= -60, (ch, crosstalk)
        report.append(f"ch{ch} {f:.3f} Hz, crosstalk {crosstalk:.1f} dB")
    print(f"\n{variant}: " + "; ".join(report) + f"; status {status}")


@pytest.mark.parametrize("variant", TONE_VARIANTS)
def test_history_equals_reference(nominal_config, variant):
    demo, reinits = _tone_run(nominal_config, variant)
    link = demo.sim._pdm_link
    for ch in (0, 1):
        got = demo.history[ch]
        ref = link_reference(link, reinits, ch)
        assert len(got) > 0 and len(ref) >= len(got)
        assert got == ref[:len(got)], f"ch{ch}"


@slow
def test_chirp_ridge_follows_law(nominal_config):
    left = CLIPS / "chirp_lin_100_7000.wav"
    demo, _ = _demo(nominal_config, left, CLIPS / "tone_3000.wav")
    _run_to_done(demo)
    clip = L.load_clip(left)
    out = demo.history[0]
    delay = pipeline_delay(out, clip)
    f_lo, f_hi, dur = 100.0, 7000.0, len(clip) / PCM_HZ     # make_clips.py
    bin_hz = PCM_HZ / 256
    errors = []
    for centre, peak in peak_bins(out):
        if centre - 128 < delay or centre + 128 > delay + len(clip):
            continue                                       # frame not inside the clip
        t = (centre - delay) / PCM_HZ
        expected = (f_lo + (f_hi - f_lo) * t / dur) / bin_hz
        errors.append(peak - expected)
    assert len(errors) >= 25
    worst = max(errors, key=abs)
    print(f"\nchirp: pipeline delay {delay} samples, {len(errors)} frames, "
          f"worst ridge error {worst:+.2f} bins, mean {sum(errors) / len(errors):+.2f}")
    assert abs(worst) <= 2, errors


# ---------------------------------------------------------------------------
# Equivalence gates (merge blockers)
# ---------------------------------------------------------------------------

def _lockstep_to(demo, core, target):
    """Lockstep (PRU1 lead) until *core* is at or just past *target* cycles.

    Batches while far away (draining the rings in between), then single
    lead steps without skip. For the lead (PRU1) that stops on every
    boundary the other run can have stopped on, so it lands on *target*.
    The follower (PRU0) catches up to the lead's time once per lead step,
    so it can pass *target* inside one catch-up (by less than one lead
    instruction): the caller aligns the other run's consumer to it.
    """
    sim = demo.sim
    c = sim.cores[core]
    while (remaining := target - c.counters.cycles) > 4096:
        sim.step_paced_many("pru1", ["pru0"], min(4096, remaining // 64),
                            idle_skip=sim.idle_skip)
        demo.drain()
    while c.counters.cycles < target:
        sim.step_paced_many("pru1", ["pru0"], 1, idle_skip=False)
    demo.drain()
    assert target <= c.counters.cycles < target + 64, (core, c.counters.cycles, target)


def _assert_equivalent(a, a_pins, b, b_pins):
    _assert_same(a.sim, a_pins, b.sim, b_pins)
    assert a.history == b.history
    assert min(len(h) for h in a.history.values()) >= EQUIV_MS * PCM_HZ // 1000 - 8


def test_idle_skip_equivalence_full_pipeline(nominal_config):
    """Merge blocker: lockstep with the idle skip on == off, 20 ms of audio."""
    left, right = CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav"
    a, a_pins = _demo(nominal_config, left, right, run_ahead=False, skip=True,
                      keep=True, probe=True)
    _run_audio_ms(a, EQUIV_MS)
    assert a.status()["mode"] == "lockstep"
    b, b_pins = _demo(nominal_config, left, right, run_ahead=False, skip=False,
                      keep=True, probe=True)
    end = a.sim.cores["pru1"].counters.cycles
    _lockstep_to(b, "pru1", end)
    assert b.sim.cores["pru1"].counters.cycles == end
    _assert_equivalent(a, a_pins, b, b_pins)
    assert a.sim.cores["pru0"].skipped_cycles > 0
    assert a.sim.cores["pru1"].skipped_cycles > 0
    assert all(c.skipped_cycles == 0 for c in b.sim.cores.values())


def test_run_ahead_equivalence_full_pipeline(nominal_config):
    """Merge blocker: run-ahead == lockstep (skip on both), 20 ms of audio."""
    left, right = CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav"
    a, a_pins = _demo(nominal_config, left, right, run_ahead=True, skip=True,
                      keep=True, probe=True)
    _run_audio_ms(a, EQUIV_MS)
    assert a.status()["mode"] == "run-ahead"
    b, b_pins = _demo(nominal_config, left, right, run_ahead=False, skip=True,
                      keep=True, probe=True)
    _lockstep_to(b, "pru0", a.sim.cores["pru0"].counters.cycles)
    # Lockstep's PRU0 may stand a few cycles past run-ahead's (see
    # _lockstep_to): step run-ahead's PRU0 alone to it. Its producer is a
    # slice ahead, so every edge it needs is decided (as inside run_ahead).
    _step_alone_to(a.sim.cores["pru0"], b.sim.cores["pru0"].counters.cycles)
    a.drain()
    _assert_equivalent(a, a_pins, b, b_pins)


# ---------------------------------------------------------------------------
# Driver behaviour
# ---------------------------------------------------------------------------

def test_overrun_is_reported(nominal_config):
    demo, _ = _demo(nominal_config, CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav",
                    chunk_cycles=5_000_000)
    assert demo._chunk_cycles == CHUNK_CAP == 1_600_000
    demo._chunk_cycles = 3_500_000          # past the cap: > 256 samples per chunk
    with pytest.raises(RuntimeError, match="overrun") as err:
        demo.run_chunk()
    overrun = demo.status()["overrun"]
    assert overrun == str(err.value)
    assert overrun.startswith("PCM ring 0 overrun: ") and overrun.endswith(" samples lost")
    lost = int(overrun.split(": ")[1].split()[0])
    assert lost == L.read_header(demo.sim.memory)["pcm_count"][0] - L.RING_SAMPLES > 0
    with pytest.raises(RuntimeError, match="overrun"):
        demo.run_chunk()                    # stays failed until the next setup


def test_wrong_clock_rejected(sim_config):
    sim = Simulator(config_path=sim_config(pru1_clock_mhz=250, pru_clock_mhz=200))
    with pytest.raises(ValueError, match="200 MHz"):
        PdmDemo(sim).setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav")


def test_setup_again_gives_a_clean_state(nominal_config):
    """A second setup on a used simulator equals a fresh one (no single-core
    reset, no stale link edges, SD channels or memory)."""
    left, right = CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav"
    a, _ = _demo(nominal_config, left, right)
    a.run_chunk()
    a.sim.cores["pru1"].step()              # leave the producer mid-pattern
    assert a.setup(right, left, swap=True) == []
    b, _ = _demo(nominal_config, left, right)
    for when in ("after setup", "after a chunk"):
        sa, sb = _state(a.sim), _state(b.sim)
        for key in sb:
            assert sa[key] == sb[key], (when, key)
        assert a.history == b.history
        a.run_chunk()
        b.run_chunk()
    assert len(a.history[0]) > 20


def test_swap_exchanges_the_clips(nominal_config):
    demo, _ = _demo(nominal_config, CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav",
                    chunk_cycles=10_000)
    demo.setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav", swap=True)
    raw = demo.sim.memory_read(L.SHARED_BASE + L.CLIP_BASE, 8)
    left, right = L.load_clip(CLIPS / "tone_1000.wav"), L.load_clip(CLIPS / "tone_3000.wav")
    assert list(struct.unpack("<4h", raw)) == [right[0], left[0], right[1], left[1]]


def test_one_way_violation_falls_back_to_lockstep(nominal_config):
    sim = Simulator(config_path=nominal_config)
    assert sim.add_gpio_wire("pru0", 5, "pru1", 5)
    demo = PdmDemo(sim, chunk_cycles=20_000)
    notices = demo.setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav")
    assert len(notices) == 1
    assert notices[0].startswith("Run-ahead not possible (GPIO wire ")
    assert notices[0].endswith("); running lockstep")
    out = demo.run_chunk()
    assert out["status"]["mode"] == "lockstep" and out["status"]["notices"] == notices
    assert sim.cores["pru0"].counters.cycles >= 20_000

    plain = PdmDemo(Simulator(config_path=nominal_config), run_ahead=False)
    assert plain.setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav") == []
    assert plain.status()["mode"] == "lockstep"
    assert math.isclose(plain.status()["clip_ms"], 250.0)


def test_teardown_restores_the_wiring_setup_changed(nominal_config):
    """teardown undoes setup's wiring: link, SD sources and state, GPCFG mux
    (to the values before the first setup, also after a second setup)."""
    sim = Simulator(config_path=nominal_config)
    sim.gpcfg_write("pru0", 1)                          # the user's PERIF mux
    sim.gpcfg_write("pru1", 3)
    sim.set_sd_modulator("pru0", 2, signal="sine", amplitude=0.25, sd_clock_mhz=10.0)
    sim.memory.write(0x26048 + 16, (2).to_bytes(4, "little"))   # ch2 CLK_SEL = 2
    before = {name: c.io_port.sd_filter.snapshot() for name, c in sim.cores.items()
              if c.io_port.sd_filter is not None}
    demo = PdmDemo(sim, chunk_cycles=20_000)
    demo.setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav")
    demo.run_chunk()
    demo.setup(CLIPS / "tone_3000.wav", CLIPS / "tone_1000.wav")   # setup again
    demo.run_chunk()
    assert sim._pdm_link is not None and sim.gpcfg_state("pru0")["mux_sel"] == 3
    demo.teardown()
    assert sim._pdm_link is None
    assert sim.cores["pru0"].io_port.sd_filter.pin_link is None
    assert sim.cores["pru1"].io_port.gpo_listeners == []
    assert sim.gpcfg_state("pru0")["mux_sel"] == 1 and sim.gpcfg_state("pru1")["mux_sel"] == 3
    for name, snap in before.items():
        assert sim.cores[name].io_port.sd_filter.snapshot() == snap, name
    with pytest.raises(RuntimeError, match="torn down"):
        demo.run_chunk()
    demo.teardown()                                      # idempotent
    assert demo.setup(CLIPS / "tone_1000.wav", CLIPS / "tone_3000.wav") == []
    assert demo.run_chunk()["status"]["audio_ms"] > 0    # set up again, runs again
