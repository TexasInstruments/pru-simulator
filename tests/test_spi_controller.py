"""PRU0 SPI controller firmware (source/spi/spi_controller.asm) + SpiLinkRuntime.

Every timing number asserted here comes from stepping the simulator: the bus
monitor records the cycle of every pin change on PRU0's four SPI pins. Each
test pins the core clock through ``sim_config`` (never the repo memory.cfg).
"""
import pytest

from pru_io.spi_link import (CMD_RW, CMD_WRITE, C_RX, C_RX_HDR, FRAME_BYTES,
                             HOP_DELAY_CYCLES, PATTERNS, IdealSpiPeripheral,
                             SpiConfig, SpiLinkRuntime, controller_delay_ok,
                             measure_timing)
from simulator import Simulator

ADDR = 0x05


def _sim(sim_config, core):
    return Simulator(sim_config(pru_clock_mhz=core, pru1_clock_mhz=core))


def _first_valid_delay(k, start=1):
    d = start
    while not controller_delay_ok(k, d):
        d += 1
    return d


def _runtime(sim_config, mode, core, k, miso_delay=None, response=None,
             reaction=8):
    sim = _sim(sim_config, core)
    if miso_delay is None:
        miso_delay = _first_valid_delay(k, 2 * k + 1)
    cfg = SpiConfig(mode, core, k, miso_delay)
    rt = SpiLinkRuntime(sim, cfg)
    rt.load_controller()
    periph = None
    if response is not None:
        periph = IdealSpiPeripheral(mode, response, reaction)
        sim.attach_device("pru0", periph)
    return sim, rt, periph


def _half_periods(frame_edges):
    """Cycle counts between consecutive CLK edges of one frame."""
    clk = [c for (c, w), (_, p) in zip(frame_edges[1:], frame_edges[:-1])
           if (w >> 1) & 1 != (p >> 1) & 1]
    return [b - a for a, b in zip(clk, clk[1:])]


def _frame_edges(edges, frame):
    return [(c, w) for c, w in edges if frame["start"] <= c <= frame["end"]]


# ---------------------------------------------------------------------------
# MOSI content
# ---------------------------------------------------------------------------

def _check_mosi(sim_config, mode, core, k):
    sim, rt, _ = _runtime(sim_config, mode, core, k)
    for name, (tx, _) in PATTERNS.items():
        res = rt.transfer(CMD_RW, ADDR, tx)
        fr = res["frame"]
        assert fr["bits"] == 8 * FRAME_BYTES, name
        assert fr["aborted"] is False, name
        assert fr["mosi"] == bytes([CMD_RW, ADDR, 0]) + tx, name
    assert len(rt.monitor.frames(mode)) == len(PATTERNS)
    assert rt.monitor.faults() == []
    rt.close()


@pytest.mark.parametrize("core,k", [(200, 10), (300, 3)])
def test_controller_mosi_bytes_mode1(sim_config, core, k):
    _check_mosi(sim_config, 1, core, k)


@pytest.mark.parametrize("core,k", [(200, 10), (300, 3)])
def test_controller_mosi_bytes_mode0(sim_config, core, k):
    _check_mosi(sim_config, 0, core, k)


# ---------------------------------------------------------------------------
# Clock timing
# ---------------------------------------------------------------------------

def _row(rows, symbol):
    return next(r for r in rows if r["symbol"] == symbol)


def test_controller_clock_exact(sim_config):
    for mode in (0, 1):
        sim, rt, _ = _runtime(sim_config, mode, 200, 4)
        rt.transfer(CMD_WRITE, ADDR, PATTERNS["a5_5a"][0])
        rows = measure_timing(rt.monitor.edges, rt.cfg)
        assert _row(rows, "tCLK")["sim_ns"] == 40.0
        assert _row(rows, "tH/tL")["sim_ns"] == 20.0
        fr = rt.monitor.frames(mode)[-1]
        assert set(_half_periods(_frame_edges(rt.monitor.edges, fr))) == {4}
        assert _row(rows, "tLEAD")["sim_ns"] >= 40.0
        assert _row(rows, "tLAG")["sim_ns"] >= 40.0

        sim, rt, _ = _runtime(sim_config, mode, 300, 3)
        rt.transfer(CMD_WRITE, ADDR, PATTERNS["a5_5a"][0])
        rows = measure_timing(rt.monitor.edges, rt.cfg)
        assert _row(rows, "tCLK")["sim_ns"] == pytest.approx(20.0)


@pytest.mark.parametrize("mode", [0, 1])
@pytest.mark.parametrize("k", [3, 4, 5, 6, 7, 8, 11, 16])
def test_every_half_period_is_exactly_k(sim_config, mode, k):
    """All 303 half periods, tLEAD and tLAG are exact cycle counts, for every
    MISO sample position class (inline k<=5, sample in either half for k>=6)."""
    core = 333 if k > 10 else 200
    for d in sorted({_first_valid_delay(k, 2 * k + 1),
                     _first_valid_delay(k, 3 * k + 1)}):
        sim, rt, _ = _runtime(sim_config, mode, core, k, miso_delay=d)
        rt.transfer(CMD_RW, ADDR, PATTERNS["toggle"][0])
        fr = rt.monitor.frames(mode)[-1]
        fe = _frame_edges(rt.monitor.edges, fr)
        halves = _half_periods(fe)
        assert len(halves) == 2 * 152 - 1
        assert set(halves) == {k}, (k, d)
        clk = [c for (c, w), (_, p) in zip(fe[1:], fe[:-1])
               if (w >> 1) & 1 != (p >> 1) & 1]
        assert clk[0] - fr["start"] == 2 * k        # tLEAD
        assert fr["end"] - clk[-1] == 2 * k         # tLAG
        assert fr["mosi"] == bytes([CMD_RW, ADDR, 0]) + PATTERNS["toggle"][0]


# ---------------------------------------------------------------------------
# MISO path
# ---------------------------------------------------------------------------

def _sweep(sim_config, mode, core, k, reaction, delays):
    response = bytes(3) + PATTERNS["walking0"][1]
    ok = []
    for d in delays:
        if not controller_delay_ok(k, d):
            continue
        sim, rt, _ = _runtime(sim_config, mode, core, k, miso_delay=d,
                              response=response, reaction=reaction)
        res = rt.transfer(CMD_RW, ADDR, PATTERNS["walking0"][0])
        if res["c_rx"] == response[3:] and res["miso"] == response:
            ok.append(d)
        rt.close()
    return ok


@pytest.mark.parametrize("mode", [0, 1])
@pytest.mark.parametrize("core,k", [(300, 3), (200, 10)])
def test_controller_reads_ideal_peripheral(sim_config, capsys, mode, core, k):
    reaction = HOP_DELAY_CYCLES + 3      # PRU1 input hop + 3 firmware cycles
    window = _sweep(sim_config, mode, core, k, reaction, range(1, 14 * k))
    with capsys.disabled():
        print(f"\n[spi] mode {mode} {core} MHz k={k} reaction={reaction} "
              f"hop={HOP_DELAY_CYCLES}: passing miso_delay {window}")
    assert window, "no passing miso_delay"
    # Round trip = reaction + PRU0 input hop; the sample must come after it.
    assert min(window) > reaction + HOP_DELAY_CYCLES - 1


def test_miso_frame_matches_monitor(sim_config):
    """With a peripheral that answers within the half period, the monitor's
    independent decode at PRU0's capture edges agrees with what PRU0 stored."""
    response = bytes(3) + PATTERNS["count"][1]
    k, reaction = 4, 1
    d = _sweep(sim_config, 1, 200, k, reaction, range(1, 40))[0]
    sim, rt, _ = _runtime(sim_config, 1, 200, k, miso_delay=d,
                          response=response, reaction=reaction)
    res = rt.transfer(CMD_RW, ADDR, PATTERNS["count"][0])
    assert res["frame"]["miso"] == response      # independent decode
    assert sim.memory_read(C_RX, 16) == response[3:]
    assert sim.memory_read(C_RX_HDR, 3) == bytes(3)
    assert res["cycles"] > 0


def _sample_cycles(sim):
    """Wrap PRU0's step: cycle of every MISO sample (and r3, r31.b1, 0x40)."""
    pru = sim.cores["pru0"]
    seen = []
    orig = pru.step

    def step():
        ins = pru.instructions[pru.pc] if pru.pc < len(pru.instructions) else None
        if (ins is not None and ins.opcode == "AND"
                and getattr(ins.operands[1], "index", None) == 31):
            seen.append(pru.counters.cycles)
        orig()
    pru.step = step
    return seen


@pytest.mark.parametrize("mode", [0, 1])
@pytest.mark.parametrize("core,k,d", [
    (300, 3, 14), (300, 3, 19), (200, 4, 9), (200, 4, 30), (200, 5, 23),
    (200, 5, 41), (200, 6, 13), (200, 6, 19), (200, 10, 25), (200, 10, 35),
    (333, 16, 21), (333, 16, 50)])
def test_sample_point_is_launch_plus_delay(sim_config, mode, core, k, d):
    """Single-stepped: PRU0 reads R31 for bit n exactly MISO_DELAY cycles
    after bit n's launch edge (Ruling R7), for all 152 bits."""
    sim, rt, _ = _runtime(sim_config, mode, core, k, miso_delay=d)
    samples = _sample_cycles(sim)
    rt.transfer(CMD_RW, ADDR, PATTERNS["a5_5a"][0])
    fr = rt.monitor.frames(mode)[-1]
    fe = _frame_edges(rt.monitor.edges, fr)
    rise = [c for (c, w), (_, p) in zip(fe[1:], fe[:-1]) if (w >> 1) & 1 > (p >> 1) & 1]
    fall = [c for (c, w), (_, p) in zip(fe[1:], fe[:-1]) if (w >> 1) & 1 < (p >> 1) & 1]
    launch = rise if mode == 1 else [rise[0] - k] + fall[:-1]
    assert len(launch) == 152
    q = d // (2 * k)
    # units 0 .. 153 + Q; with HALF_K >= 6 and the sample before C the last
    # unit's skeleton call takes one more (unused) sample
    assert len(samples) == 154 + q + (k >= 6 and d % (2 * k) < k)
    assert [samples[n + q] - launch[n] for n in range(152)] == [d] * 152


# ---------------------------------------------------------------------------
# Frame sequencing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [0, 1])
def test_back_to_back_gap(sim_config, mode):
    sim, rt, _ = _runtime(sim_config, mode, 300, 3)
    rt.transfer(CMD_RW, ADDR, PATTERNS["count"][0])
    rt.transfer(CMD_RW, ADDR + 1, PATTERNS["walking1"][0])
    frames = rt.monitor.frames(mode)
    assert len(frames) == 2
    assert frames[1]["mosi"][:2] == bytes([CMD_RW, ADDR + 1])
    rows = measure_timing(rt.monitor.edges, rt.cfg, frame_index=0)
    tclk = 1000.0 / rt.cfg.spi_mhz
    assert _row(rows, "tCSH")["sim_ns"] >= 2 * tclk
    assert sim.memory_read(0x0004, 4) == (2).to_bytes(4, "little")


@pytest.mark.parametrize("mode", [0, 1])
@pytest.mark.parametrize("bits", [1, 20, 151])
@pytest.mark.parametrize("core,k,d", [(300, 3, None), (200, 10, 21), (200, 10, 35)])
def test_abort_after_bits(sim_config, mode, bits, core, k, d):
    sim, rt, _ = _runtime(sim_config, mode, core, k, miso_delay=d)
    tx = PATTERNS["a5_5a"][0]
    res = rt.transfer(CMD_RW, ADDR, tx, abort_after_bits=bits)
    fr = res["frame"]
    assert fr["aborted"] is True and fr["bits"] == bits
    full = bytes([CMD_RW, ADDR, 0]) + tx
    want = int.from_bytes(full, "big") >> (152 - bits)
    got = int.from_bytes(fr["mosi"], "big") >> (8 * len(fr["mosi"]) - (bits // 8) * 8) \
        if bits >= 8 else None
    if got is not None:
        assert got == want >> (bits % 8)
    fe = _frame_edges(rt.monitor.edges, fr)
    assert set(_half_periods(fe)) <= {k}
    clk = [c for (c, w), (_, p) in zip(fe[1:], fe[:-1])
           if (w >> 1) & 1 != (p >> 1) & 1]
    assert clk[0] - fr["start"] == 2 * k and fr["end"] - clk[-1] == 2 * k
    # The controller completes as normal and accepts the next frame.
    res = rt.transfer(CMD_RW, ADDR, tx)
    assert res["frame"]["aborted"] is False
    assert res["frame"]["mosi"] == full
    assert sim.memory_read(0x0004, 4) == (2).to_bytes(4, "little")


def test_invalid_miso_delay_rejected(sim_config):
    sim = _sim(sim_config, 300)
    for d in (0, 3, 6, 9, 6 * 7):          # sample on a CLK edge, or Q > 6
        with pytest.raises(ValueError):
            SpiLinkRuntime(sim, SpiConfig(1, 300, 3, d)).load_controller()


def test_close_restores(sim_config):
    sim = _sim(sim_config, 300)
    masks = {c: sim.io(c)["gpo_drive_mask"] for c in ("pru0", "pru1")}
    rt = SpiLinkRuntime(sim, SpiConfig(1, 300, 3, _first_valid_delay(3, 7)))
    rt.load_controller()
    assert sim.get_gpi_delay("pru0") == HOP_DELAY_CYCLES
    rt.transfer(CMD_RW, ADDR, PATTERNS["count"][0])
    rt.close()
    assert sim.get_gpi_delay("pru0") == 0 and sim.get_gpi_delay("pru1") == 0
    assert rt.monitor not in sim.device_bus.devices
    assert {c: sim.io(c)["gpo_drive_mask"] for c in ("pru0", "pru1")} == masks


def test_load_peripheral_not_yet(sim_config):
    sim, rt, _ = _runtime(sim_config, 1, 300, 3)
    with pytest.raises(NotImplementedError):
        rt.load_peripheral()


# ---------------------------------------------------------------------------
# Deep MISO pipelining (Q = 4..6): tail units 154+ and the realign shift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [0, 1])
@pytest.mark.parametrize("k", [3, 6])
@pytest.mark.parametrize("q", [4, 5, 6])
def test_miso_deep_pipeline(sim_config, capsys, mode, k, q):
    """A slow peripheral puts the whole passing window at depth Q. Every
    realisable delay of that window reads the data back, for both merge
    orders (MP 0 and 1: sample before / after B, or before / after C)."""
    reaction = 2 * k * q - 6           # window = [reaction + 6, reaction + 5 + 2k]
    core = 300 if k == 3 else 200
    lo, hi = reaction + 6, reaction + 5 + 2 * k
    window = _sweep(sim_config, mode, core, k, reaction, range(lo - 2, hi + 3))
    with capsys.disabled():
        print(f"\n[spi] Q={q} mode {mode} k={k} reaction={reaction}: passing {window}")
    expected = [d for d in range(lo, hi + 1) if controller_delay_ok(k, d)]
    assert window == expected
    assert {d // (2 * k) for d in window} == {q}
    # both merge classes are inside the window
    if k == 3:
        assert any(d % 6 == 5 for d in window) and any(d % 6 in (1, 2, 4) for d in window)
    else:
        assert any(d % (2 * k) < k for d in window) and any(d % (2 * k) > k for d in window)


# ---------------------------------------------------------------------------
# Runtime life cycle
# ---------------------------------------------------------------------------

def test_close_then_reload_reattaches_monitor(sim_config):
    sim, rt, _ = _runtime(sim_config, 1, 300, 3)
    rt.transfer(CMD_RW, ADDR, PATTERNS["count"][0])
    rt.close()
    assert rt.monitor is None
    rt.load_controller()
    assert rt.monitor in sim.device_bus.devices
    res = rt.transfer(CMD_RW, ADDR, PATTERNS["count"][0])
    assert res["frame"] is not None and res["frame"]["bits"] == 152


def _cs_levels(rt):
    return [w & 1 for _, w in rt.monitor.edges]


def test_no_cs_glitch_on_load_and_reload(sim_config):
    sim, rt, _ = _runtime(sim_config, 1, 300, 3)
    rt.wire()
    assert sim.io("pru1")["gpi_pins"][6] == 1           # P_CS sees /CS high
    sim.step("pru0", 3)
    assert _cs_levels(rt) and set(_cs_levels(rt)) == {1}
    rt.transfer(CMD_RW, ADDR, PATTERNS["count"][0])
    rt.load_controller()                                # re-load, wires attached
    assert sim.io("pru1")["gpi_pins"][6] == 1
    sim.step("pru0", 3)
    assert set(_cs_levels(rt)) == {1}


def test_generated_blocks_match_generator():
    import tools.gen_spi_controller_units as gen
    text = gen.ASM.read_text(encoding="utf-8")
    assert gen.render(text) == text, \
        "spi_controller.asm generated blocks were edited; run tools/gen_spi_controller_units.py"
    assert text.count("    m_unit ") == 152 + gen.TAIL_MAX
