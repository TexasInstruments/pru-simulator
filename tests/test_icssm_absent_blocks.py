"""ICSSG-only blocks on the two-core ICSS-M (AM261x / AM263x).

Measured on an LP-AM261 (ICSS-M0) through the PRU: a read of each ICSSG-only
block listed in ``mem/icssm_absent.py`` returned 0x00000000 with no bus error
and no hang.  The words read were +0, +4 and +0x100 of each block, and every
word of CFG 0x130..0x16C.  By default the simulator does the same; with
``[device] icssg_only_blocks = strict`` such an access raises.
"""

import configparser
import logging
import os
import shutil

import pytest

from simulator import Simulator

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = {
    "am261x": os.path.join(REPO_ROOT, "config", "memory_am261x.cfg"),
    "am263x": os.path.join(REPO_ROOT, "config", "memory_am263x.cfg"),
}
AM243X_CFG = os.path.join(REPO_ROOT, "config", "memory_am243x.cfg")

# ICSSG-only blocks (SPRUIM2J Table 6-63): name, base, window size, and whether
# every word was read on silicon.  Otherwise +0, +4 and +0x100 of the block
# were read and the rest of the window is inferred.
ICSSG_ONLY_BLOCKS = [
    ("RTU_PRU0 IRAM", 0x00004000, 0x2000, False),
    ("RTU_PRU1 IRAM", 0x00006000, 0x2000, False),
    ("TX_PRU0 IRAM", 0x0000A000, 0x1800, False),
    ("TX_PRU1 IRAM", 0x0000C000, 0x1800, False),
    ("RTU_PRU0 Control", 0x00023000, 0x400, False),
    ("RTU_PRU0 Debug", 0x00023400, 0x400, False),
    ("RTU_PRU1 Control", 0x00023800, 0x400, False),
    ("RTU_PRU1 Debug", 0x00023C00, 0x400, False),
    ("TX_PRU0 Control", 0x00025000, 0x400, False),
    ("TX_PRU0 Debug", 0x00025400, 0x400, False),
    ("TX_PRU1 Control", 0x00025800, 0x400, False),
    ("TX_PRU1 Debug", 0x00025C00, 0x400, False),
    ("PA_STATS_QRAM", 0x00027000, 0x1000, False),
    ("TM_CFG_PRU0", 0x0002A000, 0x100, False),
    ("TM_CFG_RTU0", 0x0002A100, 0x100, False),
    ("TM_CFG_TX_PRU0", 0x0002A400, 0x100, False),
    ("TM_CFG_TX_PRU1", 0x0002A500, 0x100, False),
    ("PA_STATS_CRAM", 0x0002C000, 0x2000, False),
    ("IEP1", 0x0002F000, 0x1000, False),
    ("CFG ICSSG PWM registers", 0x00026130, 0x40, True),
]
WINDOW_NAMES = {b[0] for b in ICSSG_ONLY_BLOCKS}

SILICON_WORDS = []
for _name, _base, _size, _measured in ICSSG_ONLY_BLOCKS:
    _offsets = range(0, _size, 4) if _measured else (0, 4, 0x100)
    SILICON_WORDS += [(_name, _base + o) for o in _offsets if o < _size]


def _windows(sim):
    return [r for r in sim.memory.regions if r.name in WINDOW_NAMES]


# Blocks and registers the ICSS-M does implement (SDK ICSS-M register header);
# none may be covered by an absent window.
ICSSM_PRESENT = [
    0x00000000, 0x00002000, 0x00008000, 0x00009000, 0x00010000, 0x00017FFC,
    0x00020000, 0x00022000, 0x00022400, 0x00024000, 0x00024400, 0x00024C00,
    0x00026000, 0x00026040, 0x00026044, 0x000260E0, 0x00026100, 0x00026124,
    0x0002612C, 0x00026170, 0x00026174, 0x00026178, 0x00026194,
    0x00028000, 0x0002E000, 0x00030000, 0x00032000, 0x00032400, 0x00033000,
    0x00034000, 0x00038000,
]


def _make(cfg_path, tmp_path, mode=None):
    """Return a Simulator on *cfg_path*, with icssg_only_blocks=*mode* if given."""
    if mode is None:
        return Simulator(cfg_path)
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)
    cfg["device"]["icssg_only_blocks"] = mode
    path = tmp_path / "memory.cfg"
    with open(path, "w") as f:
        cfg.write(f)
    shutil.copytree(os.path.join(REPO_ROOT, "config"), tmp_path / "config",
                    dirs_exist_ok=True)
    return Simulator(str(path))


def _run(sim, core, src):
    assert sim.load(core, src) == []
    for _ in range(64):
        sim.step(core)
        if sim.cores[core].halted:
            break
    return sim.cores[core]


def _lbbo(sim, core, addr, nbytes=4):
    return _run(sim, core,
                f"ldi32 r1, 0x{addr:08X}\nldi32 r2, 0xDEADBEEF\n"
                f"lbbo &r2, r1, 0, {nbytes}\nhalt\n")


def _reg(core, n):
    return core.registers.read_full(n)


@pytest.fixture(params=["am261x", "am263x"])
def target(request):
    return request.param


# ---------------------------------------------------------------------------
# Default: reads return 0, writes are ignored
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_default_lbbo_reads_zero(target, core):
    for name, addr in SILICON_WORDS:
        sim = Simulator(CFG[target])
        c = _lbbo(sim, core, addr)
        assert c.fault is None, (name, hex(addr), c.fault)
        assert _reg(c, 2) == 0, (name, hex(addr))


@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_default_lbco_reads_zero(target, core):
    sim = Simulator(CFG[target])
    sim.cores[core].constant_table.set(5, 0x0002F000)       # IEP1 on the ICSSG
    c = _run(sim, core, "ldi32 r2, 0xDEADBEEF\nlbco &r2, c5, 4, 4\nhalt\n")
    assert c.fault is None
    assert _reg(c, 2) == 0


def test_default_wide_burst_reads_zero(target):
    sim = Simulator(CFG[target])
    c = _lbbo(sim, "pru0", 0x00004000, 64)
    assert c.fault is None
    assert all(_reg(c, n) == 0 for n in range(2, 18))


@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_default_writes_are_ignored(target, core):
    for name, addr in SILICON_WORDS[::5]:
        sim = Simulator(CFG[target])
        c = _run(sim, core,
                 f"ldi32 r1, 0x{addr:08X}\nldi32 r2, 0xA5A55A5A\n"
                 "sbbo &r2, r1, 0, 4\nlbbo &r3, r1, 0, 4\nhalt\n")
        assert c.fault is None, (name, hex(addr), c.fault)
        assert _reg(c, 3) == 0, (name, hex(addr))


def test_default_writes_leave_present_neighbours_alone(target):
    sim = Simulator(CFG[target])
    sim.memory.write(0x00010100, (0x33330003).to_bytes(4, "little"))
    sim.memory.write(0x00002100, (0x22220002).to_bytes(4, "little"))
    before_iep = sim.memory.read(0x0002E000, 0x20)[0]
    before_gp = sim.memory.read(0x00026008, 8)[0]
    for _, base, size, _ in ICSSG_ONLY_BLOCKS:
        sim.memory.write(base, (0xA5A55A5A).to_bytes(4, "little"))
        sim.memory.write(base + size - 4, (0xA5A55A5A).to_bytes(4, "little"))
    assert sim.memory.read(0x00010100, 4)[0] == (0x33330003).to_bytes(4, "little")
    assert sim.memory.read(0x00002100, 4)[0] == (0x22220002).to_bytes(4, "little")
    assert sim.memory.read(0x0002E000, 0x20)[0] == before_iep
    assert sim.memory.read(0x00026008, 8)[0] == before_gp


def test_default_bus_reads_zero_across_every_window(target):
    sim = Simulator(CFG[target])
    for name, base, size, _ in ICSSG_ONLY_BLOCKS:
        for addr in (base, base + size - 4):
            data, _ = sim.memory.read(addr, 4)
            assert data == bytes(4), (name, hex(addr))


def test_default_warns_once_per_block(target, caplog):
    sim = Simulator(CFG[target])
    with caplog.at_level(logging.WARNING, logger="mem.icssm_absent"):
        sim.memory.read(0x0002F000, 4)
        sim.memory.read(0x0002F004, 4)
        sim.memory.write(0x0002F008, bytes(4))
        sim.memory.read(0x00004000, 4)
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("IEP1" in m for m in msgs) == 1
    assert sum("RTU_PRU0 IRAM" in m for m in msgs) == 1
    assert all("absent on ICSS-M" in m for m in msgs)


def test_addresses_outside_the_windows_still_raise(target):
    # Only the listed windows are covered, not the rest of the hole.
    sim = Simulator(CFG[target])
    for addr in (0x00008000, 0x0002A200):       # RAT (not modelled), TM_CFG_PRU1
        with pytest.raises(ValueError, match="No memory region mapped"):
            sim.memory.read(addr, 4)


# ---------------------------------------------------------------------------
# Strict: the same accesses raise and name the block
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_strict_lbbo_faults_naming_the_block(target, core, tmp_path):
    for name, addr in SILICON_WORDS[::4]:
        sim = _make(CFG[target], tmp_path, "strict")
        c = _lbbo(sim, core, addr)
        assert c.halted and c.fault is not None, (name, hex(addr))
        assert c.fault["opcode"] == "LBBO"
        assert name in c.fault["error"], (name, c.fault)
        assert "absent on ICSS-M" in c.fault["error"]


@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_strict_sbbo_faults_naming_the_block(target, core, tmp_path):
    sim = _make(CFG[target], tmp_path, "strict")
    c = _run(sim, core, "ldi32 r1, 0x00023000\nldi32 r2, 1\nsbbo &r2, r1, 0, 4\nhalt\n")
    assert c.fault is not None and c.fault["opcode"] == "SBBO"
    assert "RTU_PRU0 Control" in c.fault["error"]


def test_strict_bus_raises_for_every_window(target, tmp_path):
    sim = _make(CFG[target], tmp_path, "strict")
    for name, base, size, _ in ICSSG_ONLY_BLOCKS:
        with pytest.raises(ValueError, match=name):
            sim.memory.read(base, 4)
        with pytest.raises(ValueError, match=name):
            sim.memory.write(base + size - 4, bytes(4))


def test_strict_leaves_present_blocks_working(target, tmp_path):
    sim = _make(CFG[target], tmp_path, "strict")
    sim.memory.write(0x00010100, (0x33330003).to_bytes(4, "little"))
    assert sim.memory.read(0x00010100, 4)[0] == (0x33330003).to_bytes(4, "little")
    sim.memory.read(0x0002E000, 4)
    sim.memory.read(0x00026008, 4)


def test_unknown_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="icssg_only_blocks"):
        _make(CFG["am261x"], tmp_path, "bogus")


# ---------------------------------------------------------------------------
# AM243x / AM64x: unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [None, "strict"])
def test_am243x_unchanged(mode, tmp_path):
    sim = _make(AM243X_CFG, tmp_path, mode)
    assert not _windows(sim)
    # IEP0 still reaches its model; ICSSG-only blocks are not faked as zero.
    data, _ = sim.memory.read(0x0002E000, 4)
    assert len(data) == 4
    for addr in (0x0002F000, 0x00023000, 0x00026130):
        with pytest.raises(ValueError, match="No memory region mapped"):
            sim.memory.read(addr, 4)


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def test_windows_do_not_overlap_mapped_regions(target):
    sim = Simulator(CFG[target])
    windows = _windows(sim)
    assert len(windows) == len(ICSSG_ONLY_BLOCKS)
    for w in windows:
        for o in sim.memory.regions:
            if o is w:
                continue
            assert (w.base_addr + w.size <= o.base_addr
                    or o.base_addr + o.size <= w.base_addr), (w.name, o.name)


def test_windows_do_not_cover_icssm_blocks(target):
    sim = Simulator(CFG[target])
    for w in _windows(sim):
        for addr in ICSSM_PRESENT:
            assert not w.contains(addr), (w.name, hex(addr))


# ---------------------------------------------------------------------------
# A burst running from one absent window into the adjacent one
# ---------------------------------------------------------------------------

def test_default_burst_across_adjacent_windows_reads_zero(target):
    sim = Simulator(CFG[target])
    c = _lbbo(sim, "pru0", 0x00005FFC, 8)           # RTU_PRU0 IRAM -> RTU_PRU1 IRAM
    assert c.fault is None
    assert _reg(c, 2) == 0 and _reg(c, 3) == 0


def test_default_burst_across_adjacent_windows_write_dropped(target):
    sim = Simulator(CFG[target])
    c = _run(sim, "pru0", "ldi32 r1, 0x00005FFC\nldi32 r2, 1\nldi32 r3, 2\n"
                          "sbbo &r2, r1, 0, 8\nlbbo &r4, r1, 0, 8\nhalt\n")
    assert c.fault is None
    assert _reg(c, 4) == 0 and _reg(c, 5) == 0


def test_strict_burst_across_adjacent_windows_raises(target, tmp_path):
    sim = _make(CFG[target], tmp_path, "strict")
    c = _lbbo(sim, "pru0", 0x00005FFC, 8)
    assert c.fault is not None
    assert "RTU_PRU0 IRAM" in c.fault["error"]


def test_burst_from_absent_window_into_present_region_still_raises(target):
    sim = Simulator(CFG[target])
    with pytest.raises(ValueError):
        sim.memory.read(0x00023FFC, 8)              # RTU_PRU1 Debug -> present block
    with pytest.raises(ValueError):
        sim.memory.write(0x00023FFC, bytes(8))
