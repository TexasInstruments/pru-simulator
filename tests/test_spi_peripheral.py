"""PRU1 SPI peripheral firmware (source/spi/spi_peripheral.asm) against the
PRU0 controller, wired pin to pin with the calibrated 5-cycle hop delay
(Ruling R6) on both cores' GPI.

Every cycle number asserted here comes from stepping the simulator. Each test
pins the core clock through ``sim_config`` (never the repo memory.cfg).
"""
import bisect

import pytest

from pru_io.spi_link import (CMD_READ, CMD_RW, CMD_WRITE, HOP_DELAY_CYCLES,
                             P_MISO, P_RX, P_STATUS, P_TX, PATTERNS,
                             PERIPHERAL_SOURCE, SpiConfig, SpiLinkRuntime,
                             controller_delay_ok, render_header)
from simulator import Simulator

#: miso_delay per (mode, core, k), picked from the passing window measured by
#: SpiLinkRuntime.find_miso_window (see test_miso_window_contains_choice).
MISO_DELAY = {
    (0, 200, 10): 22,     # window 12..31 (holes 20, 30)
    (1, 200, 10): 23,     # window 15..32 (holes 20, 30)
    (0, 200, 4): 17,      # window 13..19 (hole 16)
    (1, 200, 4): 17,      # window 15..19 (hole 16)
}

MODES = [0, 1]


def _link(sim_config, mode, core=200, k=10, d=None):
    sim = Simulator(sim_config(pru_clock_mhz=core, pru1_clock_mhz=core))
    cfg = SpiConfig(mode, core, k, MISO_DELAY[(mode, core, k)] if d is None else d)
    rt = SpiLinkRuntime(sim, cfg)
    rt.load_controller()
    rt.load_peripheral()
    rt.wire()
    return sim, rt


def _status(**kw):
    base = {"frames": 0, "errors": 0, "aborts": 0, "last_cmd": 0, "last_addr": 0}
    base.update(kw)
    return base


def _preload_all(rt, seed=0x11):
    """Fill every P_TX block with distinct non-zero bytes."""
    blocks = {}
    for a in range(0x80):
        blk = bytes(((seed + a * 16 + i) % 255) + 1 for i in range(16))
        rt.preload_peripheral_tx(a, blk)
        blocks[a] = blk
    return blocks


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", MODES)
def test_write_lands_in_rx_block(sim_config, mode):
    sim, rt = _link(sim_config, mode)
    tx_before = _preload_all(rt)
    data = PATTERNS["walking1"][0]
    res = rt.transfer(CMD_WRITE, 0x05, data)
    assert rt.read_peripheral_rx(5) == data
    assert res["miso"] == bytes(19)                 # WRITE: MISO all 0x00
    assert res["frame"]["mosi"] == bytes([CMD_WRITE, 5, 0]) + data
    for a, blk in tx_before.items():                # P_TX unchanged
        assert sim.memory_read(P_TX + 16 * a, 16) == blk
    for a in range(0x80):                           # only block 5 written
        if a != 5:
            assert rt.read_peripheral_rx(a) == bytes(16)
    assert rt.peripheral_status() == _status(frames=1, last_cmd=CMD_WRITE, last_addr=5)
    rt.close()


@pytest.mark.parametrize("mode", MODES)
def test_read_returns_tx_block(sim_config, mode):
    sim, rt = _link(sim_config, mode)
    block = PATTERNS["count"][1]
    rt.preload_peripheral_tx(0x7F, block)
    res = rt.transfer(CMD_READ, 0x7F, bytes(16))
    assert res["c_rx"] == block
    assert res["miso"][:3] == bytes(3)              # header bytes 00 00 00
    assert res["frame"]["miso"] == bytes(3) + block  # independent decode
    assert rt.read_peripheral_rx(0x7F) == bytes(16)  # READ writes nothing
    assert rt.peripheral_status() == _status(frames=1, last_cmd=CMD_READ, last_addr=0x7F)
    rt.close()


@pytest.mark.parametrize("mode", MODES)
def test_rw_full_duplex(sim_config, mode):
    sim, rt = _link(sim_config, mode)
    c_tx, p_tx = PATTERNS["count"]
    rt.preload_peripheral_tx(2, p_tx)
    res = rt.transfer(CMD_RW, 0x02, c_tx)
    assert res["c_rx"] == p_tx
    assert rt.read_peripheral_rx(2) == c_tx
    assert rt.peripheral_status() == _status(frames=1, last_cmd=CMD_RW, last_addr=2)
    rt.close()


def _bad_frame(sim_config, mode, cmd, addr):
    sim, rt = _link(sim_config, mode)
    _preload_all(rt)
    res = rt.transfer(cmd, addr, PATTERNS["toggle"][0])
    assert res["miso"] == bytes(19)                 # MISO data all 0x00
    for a in range(0x80):                           # nothing written
        assert rt.read_peripheral_rx(a) == bytes(16)
    assert rt.peripheral_status() == _status(frames=1, errors=1,
                                             last_cmd=cmd, last_addr=addr)
    # The next valid frame is served normally.
    p_tx = PATTERNS["a5_5a"][1]
    rt.preload_peripheral_tx(1, p_tx)
    res = rt.transfer(CMD_RW, 1, PATTERNS["a5_5a"][0])
    assert res["c_rx"] == p_tx and rt.read_peripheral_rx(1) == PATTERNS["a5_5a"][0]
    assert rt.peripheral_status()["errors"] == 1
    rt.close()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cmd", [0x04, 0x00, 0xFF])
def test_unknown_command_counted(sim_config, mode, cmd):
    _bad_frame(sim_config, mode, cmd, 0x03)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("cmd,addr", [(CMD_RW, 0x80), (CMD_READ, 0xFF), (CMD_WRITE, 0x80)])
def test_out_of_range_address(sim_config, mode, cmd, addr):
    _bad_frame(sim_config, mode, cmd, addr)


@pytest.mark.parametrize("mode", MODES)
def test_abort_then_back_to_back_frame(sim_config, mode):
    sim, rt = _link(sim_config, mode)
    c_tx, p_tx = PATTERNS["walking0"]
    rt.preload_peripheral_tx(3, p_tx)
    res = rt.transfer(CMD_WRITE, 3, c_tx, abort_after_bits=50)
    assert res["frame"]["aborted"] is True and res["frame"]["bits"] == 50
    assert rt.read_peripheral_rx(3) == bytes(16)    # not committed
    assert rt.peripheral_status() == _status(aborts=1)
    # Next frame straight away: the controller's minimum /CS-high gap.
    res = rt.transfer(CMD_RW, 3, c_tx)
    assert res["c_rx"] == p_tx
    assert rt.read_peripheral_rx(3) == c_tx
    assert rt.peripheral_status() == _status(frames=1, aborts=1,
                                             last_cmd=CMD_RW, last_addr=3)
    # ... and a second one back to back after a complete frame.
    res = rt.transfer(CMD_READ, 3, bytes(16))
    assert res["c_rx"] == p_tx
    frames = rt.monitor.frames(mode)
    gaps = [b["start"] - a["end"] for a, b in zip(frames, frames[1:])]
    tclk = 2 * rt.cfg.half_k
    assert all(g >= 2 * tclk for g in gaps)
    print(f"\n[spi] mode {mode}: /CS-high gaps between frames {gaps} cycles")
    assert rt.peripheral_status()["frames"] == 2
    rt.close()


# ---------------------------------------------------------------------------
# Edge polling: one PRU1 assembly, two controller rates
# ---------------------------------------------------------------------------

def test_peripheral_header_has_only_mode():
    for mode in MODES:
        hdr = render_header(SpiConfig(mode, 200, 10, 25), role="peripheral")
        assert f"SPI_MODE .set {mode}" in hdr
        assert "HALF_K" not in hdr and "MISO_DELAY" not in hdr
    src = PERIPHERAL_SOURCE.read_text(encoding="utf-8")
    code = "\n".join(line.split(";", 1)[0] for line in src.splitlines())
    assert "HALF_K" not in code and "MISO_DELAY" not in code


@pytest.mark.parametrize("mode", MODES)
def test_peripheral_is_edge_polling(sim_config, mode):
    sim, rt = _link(sim_config, mode, 200, 10)
    loads = []
    orig = sim.load

    def load(core, *a, **kw):
        loads.append(core)
        return orig(core, *a, **kw)
    sim.load = load
    c_tx, p_tx = PATTERNS["a5_5a"]
    for k, addr in ((10, 0x10), (4, 0x11)):
        if k != rt.cfg.half_k:
            rt.reload_controller(SpiConfig(mode, 200, k, MISO_DELAY[(mode, 200, k)]))
        rt.preload_peripheral_tx(addr, p_tx)
        res = rt.transfer(CMD_RW, addr, c_tx)
        assert res["c_rx"] == p_tx, f"k={k}"
        assert rt.read_peripheral_rx(addr) == c_tx, f"k={k}"
        fr = res["frame"]
        clk_edges = [c for (c, w), (_, p) in zip(rt.monitor.edges[1:], rt.monitor.edges[:-1])
                     if fr["start"] <= c <= fr["end"] and (w >> 1) & 1 != (p >> 1) & 1]
        assert clk_edges[1] - clk_edges[0] == k
    assert loads == ["pru0"]                        # PRU1 assembled once
    assert rt.peripheral_status() == _status(frames=2, last_cmd=CMD_RW, last_addr=0x11)
    rt.close()


@pytest.mark.parametrize("mode,core,k", sorted(MISO_DELAY))
def test_miso_window_contains_choice(sim_config, capsys, mode, core, k):
    sim, rt = _link(sim_config, mode, core, k)
    window = rt.find_miso_window()
    with capsys.disabled():
        print(f"\n[spi] PRU1 peripheral mode {mode} {core} MHz k={k} hop={HOP_DELAY_CYCLES}: "
              f"passing miso_delay {window}")
    assert MISO_DELAY[(mode, core, k)] in window
    assert all(controller_delay_ok(k, d) for d in window)
    assert rt.cfg.miso_delay == MISO_DELAY[(mode, core, k)]    # restored
    rt.close()


@pytest.mark.parametrize("mode,d", [(0, 16), (1, 16)])
def test_fastest_rate_all_patterns(sim_config, mode, d):
    """50 MHz (300 MHz core, k=3): every pattern, R/W and WRITE. With 6 core
    cycles per bit this bounds PRU1's per-bit cost (5 in mode 1, 4 in mode 0,
    plus the gather) from above. miso_delay windows measured with all
    patterns: mode 0 [14, 16, 17], mode 1 [16, 17]. Task 5 sweeps the rest."""
    sim, rt = _link(sim_config, mode, 300, 3, d=d)
    for i, (name, (c_tx, p_tx)) in enumerate(PATTERNS.items()):
        rt.preload_peripheral_tx(i, p_tx)
        res = rt.transfer(CMD_RW, i, c_tx)
        assert res["miso"] == bytes(3) + p_tx, name
        assert rt.read_peripheral_rx(i) == c_tx, name
        res = rt.transfer(CMD_WRITE, 0x40 + i, p_tx)
        assert res["miso"] == bytes(19) and rt.read_peripheral_rx(0x40 + i) == p_tx, name
    assert rt.peripheral_status() == _status(frames=10, last_cmd=CMD_WRITE, last_addr=0x44)
    rt.close()


# ---------------------------------------------------------------------------
# MISO drive: one R30 write, other GPO bits stay off the pins
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", MODES)
def test_miso_one_r30_write_is_harmless(sim_config, mode):
    sim, rt = _link(sim_config, mode)
    assert sim.io("pru1")["gpo_drive_mask"] == 1 << P_MISO
    p_tx = PATTERNS["toggle"][1]
    rt.preload_peripheral_tx(4, p_tx)
    pru0, pru1 = sim.cores["pru0"], sim.cores["pru1"]
    spi_nets0 = (1 << 13) | (1 << 14) | (1 << 16) | (1 << 17)   # PRU0 SPI pins
    spi_nets1 = (1 << 1) | (1 << 2) | (1 << 6) | (1 << P_MISO)  # PRU1 SPI pins
    other = leaked = 0
    orig = pru1.step

    def step():
        nonlocal other, leaked
        orig()
        other |= pru1.io_port.gpo & ~(1 << P_MISO)
        leaked |= (pru0.io_port.gpi & ~spi_nets0) | (pru1.io_port.gpi & ~spi_nets1)
    pru1.step = step
    res = rt.transfer(CMD_READ, 4, bytes(16))
    pru1.step = orig
    assert other != 0             # the write did set other GPO bits ...
    assert res["c_rx"] == p_tx    # ... and MISO is still exact
    assert leaked & 0xFFFFF == 0  # ... and none of them reached a pin
    assert {(w >> 3) & 1 for _, w in rt.monitor.edges} == {0, 1}
    rt.close()


# ---------------------------------------------------------------------------
# Cycle costs, single-stepped
# ---------------------------------------------------------------------------

def _work_trace(sim, rt, cmd, addr, data):
    """Step a frame and record PRU1's (pc, cycles) per instruction."""
    pru1 = sim.cores["pru1"]
    trace = []
    orig = pru1.step

    def step():
        pc, c0 = pru1.pc, pru1.counters.cycles
        orig()
        trace.append((pc, c0, pru1.counters.cycles - c0, pru1.pc))
    pru1.step = step
    rt.transfer(cmd, addr, data)
    pru1.step = orig
    return trace


def _labels(sim):
    return dict(sim.cores["pru1"]._parser.labels)


def _bit_work(trace, labels, mode):
    """PRU1 cycles per frame bit, without waiting: every poll iteration that
    branched back to its own label (the edge was not visible yet) is dropped.
    The cycles after bit 151 up to frame_end count for bit 151."""
    polls = sorted((v, int(k[4:])) for k, v in labels.items()
                   if k.startswith((f"m{mode}_l", f"m{mode}_c")))
    poll_pcs = {v for v, _ in polls}
    keys = [v for v, _ in polls]
    first, end = polls[0][0], labels["frame_end"]
    seq = []
    for pc, _c0, dc, nxt in trace:
        if pc == first and not seq:
            seq.append((pc, dc, nxt))
        elif seq:
            if pc == end:
                break
            seq.append((pc, dc, nxt))
    work = {}
    j = 0
    while j < len(seq):
        pc = seq[j][0]
        if pc in poll_pcs:
            back = next((i for i in range(j, min(j + 4, len(seq))) if seq[i][2] == pc), None)
            if back is not None:            # a waiting iteration
                j = back + 1
                continue
        n = polls[bisect.bisect_right(keys, pc) - 1][1]
        work[n] = work.get(n, 0) + seq[j][1]
        j += 1
    return work


@pytest.mark.parametrize("mode", MODES)
def test_per_bit_cycles(sim_config, capsys, mode):
    """PRU1 work per bit (everything but waiting in a poll loop), single-stepped,
    at 200 MHz k=10 so that PRU1 always waits for each edge."""
    sim, rt = _link(sim_config, mode)
    rt.preload_peripheral_tx(6, PATTERNS["count"][1])
    trace = _work_trace(sim, rt, CMD_RW, 6, PATTERNS["count"][0])
    work = _bit_work(trace, _labels(sim), mode)
    assert sorted(work) == list(range(152))
    import tools.gen_spi_peripheral as gen
    st = gen.stats(mode)
    fixed = {n: st["fixed"][n] for n in range(152)}
    slot = {n: st["slot"][n] for n in range(152)}
    # stepped == generator's static count (polls, MISO drive, gap work incl.
    # the 3-cycle lbbo fetches); bit 151 also runs the leftover gather ops
    # and the jmp to frame_end
    for n in range(151):
        assert work[n] == fixed[n] + slot[n], n
    assert work[151] == fixed[151] + slot[151] + st["tail_ops"] + 1
    by_class = {"cmd/addr": {fixed[n] for n in range(16)},
                "idle": {fixed[n] for n in range(16, 24)},
                "data": {fixed[n] for n in range(24, 151)}}
    want = {1: {"cmd/addr": {4}, "idle": {3}, "data": {5}},
            0: {"cmd/addr": {3}, "idle": {2, 3}, "data": {4}}}[mode]
    assert by_class == want
    per_byte = [sum(work[8 * m + i] for i in range(8)) for m in range(19)]
    with capsys.disabled():
        print(f"\n[spi] PRU1 mode {mode} work cycles per bit class {by_class}, "
              f"per byte incl. gap work {per_byte}")
    rt.close()


@pytest.mark.parametrize("mode", MODES)
def test_fetch_and_commit_cycles(sim_config, capsys, mode):
    sim, rt = _link(sim_config, mode)
    rt.preload_peripheral_tx(9, PATTERNS["count"][1])
    trace = _work_trace(sim, rt, CMD_RW, 9, PATTERNS["count"][0])
    pru1 = sim.cores["pru1"]
    lbbo = [dc for pc, c0, dc, nxt in trace if pru1.instructions[pc].opcode == "LBBO"]
    assert lbbo == [3, 3, 3, 3]          # 4 x 4 B from DMEM, 3 cycles each
    labels = _labels(sim)
    # commit: from the R31 read that sees /CS high to the next frame_wait poll
    fe = labels["frame_end"]
    idx = max(i for i, t in enumerate(trace) if t[0] == fe)
    j = next(i for i in range(idx, len(trace)) if trace[i][0] == labels["frame_wait"])
    commit = trace[j][1] - trace[idx][1]
    with capsys.disabled():
        print(f"\n[spi] PRU1 mode {mode}: idle-byte TX fetch 4 x lbbo = {sum(lbbo)} cycles; "
              f"commit after /CS high = {commit} cycles")
    assert commit == {1: 22, 0: 26}[mode]     # quoted in spi_peripheral.asm
    rt.close()


# ---------------------------------------------------------------------------
# Start-up, helpers, generated code
# ---------------------------------------------------------------------------

def test_waits_for_cs_idle_before_arming(sim_config):
    """/CS low at start-up is not a frame: PRU1 first waits for /CS high."""
    sim = Simulator(sim_config(pru_clock_mhz=200, pru1_clock_mhz=200))
    rt = SpiLinkRuntime(sim, SpiConfig(1, 200, 10, 25))
    rt.load_peripheral()                  # not wired: PRU1 GPI6 (/CS) reads 0
    for _ in range(200):
        sim.step("pru1")
    labels = _labels(sim)
    assert sim.cores["pru1"].pc == labels["frame_wait"]
    rt.close()


def test_helpers_validate(sim_config):
    sim = Simulator(sim_config(pru_clock_mhz=200, pru1_clock_mhz=200))
    rt = SpiLinkRuntime(sim, SpiConfig(1, 200, 10, 25))
    with pytest.raises(ValueError):
        rt.preload_peripheral_tx(0x80, bytes(16))
    with pytest.raises(ValueError):
        rt.preload_peripheral_tx(0, bytes(15))
    with pytest.raises(ValueError):
        rt.read_peripheral_rx(-1)
    rt.preload_peripheral_tx(0x7F, bytes(range(16)))
    assert sim.memory_read(P_TX + 0x7F0, 16) == bytes(range(16))
    sim.memory.write(P_RX + 0x20, bytes([7] * 16))
    assert rt.read_peripheral_rx(2) == bytes([7] * 16)
    sim.memory.write(P_STATUS, (5).to_bytes(4, "little") + (1).to_bytes(4, "little")
                     + (2).to_bytes(4, "little") + bytes([3, 0x44]))
    assert rt.peripheral_status() == _status(frames=5, errors=1, aborts=2,
                                             last_cmd=3, last_addr=0x44)
    rt.load_controller()
    rt.load_peripheral()
    with pytest.raises(ValueError):       # PRU1 assembled for mode 1
        rt.reload_controller(SpiConfig(0, 200, 10, 25))
    rt.close()


def test_generated_blocks_match_generator():
    import tools.gen_spi_peripheral as gen
    text = gen.ASM.read_text(encoding="utf-8")
    assert gen.render(text) == text, \
        "spi_peripheral.asm generated blocks were edited; run tools/gen_spi_peripheral.py --write"
    assert gen.gather_check() == {0: 0x00, 2: 0xFF}
