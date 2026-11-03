"""PRU_CTRL timing from LP-AM243 PRU1, issue #70.

C11/bank addresses: SPRUIM2J Table 6-65; CYCLE offset 0x0c:
TI PRU Software Support Package include/am335x/pru_ctrl.h (common CTRL layout).
"""

from pathlib import Path

import pytest

from simulator import Simulator

ROOT = Path(__file__).resolve().parents[1]


# Back-to-back CYCLE reads: 6 cycles measured on LP-AM243 ICSSG0, 4 cycles measured
# on LP-AM261 ICSSM0 (register level).
@pytest.mark.parametrize("device,cycles", [("am243x", 6), ("am261x", 4)])
@pytest.mark.parametrize("core,base", [("pru0", 0x22000), ("pru1", 0x24000)])
@pytest.mark.parametrize("opcode", ["lbco", "lbbo"])
def test_back_to_back_cycle_reads(device, cycles, core, base, opcode):
    sim = Simulator(str(ROOT / "config" / f"memory_{device}.cfg"))
    if opcode == "lbco":
        source = "lbco &r2, c11, 12, 4\nlbco &r3, c11, 12, 4\nhalt"
    else:
        source = f"ldi32 r1, {base}\nlbbo &r2, r1, 12, 4\nlbbo &r3, r1, 12, 4\nhalt"
    assert sim.load(core, source) == []
    if opcode == "lbbo":
        sim.step(core, 2)
    before = sim.cores[core].counters.cycles
    sim.step(core, 2)
    assert sim.cores[core].fault is None
    assert sim.registers(core)[3] - sim.registers(core)[2] == cycles
    assert sim.cores[core].counters.cycles - before == 2 * cycles


@pytest.mark.parametrize("preset,cycles", [("memory_am243x.cfg", 6), ("memory_am261x.cfg", 4),
                                         ("memory_am263x.cfg", 4)])
def test_ctrl_window_cost_by_preset(preset, cycles):
    sim = Simulator(str(ROOT / "config" / preset))
    assert sim.load("pru0", "lbco &r2, c11, 0, 4\nhalt") == []
    sim.step("pru0")
    assert sim.cores["pru0"].fault is None
    assert sim.cores["pru0"].counters.cycles == cycles


# Silicon words: back-to-back CYCLE reads / around a DMEM LBBO / around a CTRL LBBO.
# LP-AM261 ICSSM0 PRU0 and PRU1, 16 reps each, identical: 4 / 7 / 8.
# LP-AM243 ICSSG0, same method: 6 / 9 / 12. The AM263x preset reuses the AM261x
# figure (same ICSS-M RTL; AM263x silicon not measured directly).
@pytest.mark.parametrize("preset,words", [("memory_am261x.cfg", (4, 7, 8)),
                                          ("memory_am263x.cfg", (4, 7, 8)),
                                          ("memory_am243x.cfg", (6, 9, 12))])
@pytest.mark.parametrize("core,base", [("pru0", 0x22000), ("pru1", 0x24000)])
def test_cycle_read_deltas_match_silicon(preset, words, core, base):
    def delta(middle):
        sim = Simulator(str(ROOT / "config" / preset))
        lines = [f"ldi32 r1, {base}", "ldi32 r5, 0x400", "lbbo &r2, r1, 12, 4",
                 *middle, "lbbo &r3, r1, 12, 4", "halt"]
        assert sim.load(core, chr(10).join(lines)) == []
        sim.step(core, 20)
        assert sim.cores[core].fault is None
        return sim.registers(core)[3] - sim.registers(core)[2]

    assert delta([]) == words[0]
    assert delta(["lbbo &r6, r5, 0, 4"]) == words[1]
    assert delta(["lbbo &r6, r1, 0, 4"]) == words[2]


@pytest.mark.parametrize("core", ["pru0", "pru1"])
def test_root_template_ctrl_window_costs_six_on_am243x(core):
    sim = Simulator(str(ROOT / "memory.cfg"))
    assert sim.load(core, "lbco &r2, c11, 12, 4\nhalt") == []
    sim.step(core)
    assert sim.cores[core].fault is None
    assert sim.cores[core].counters.cycles == 6


# RTU_PRU and TX_PRU CTRL (SPRUIM2J Table 6-63): not measured, assumed to cost
# the same as the PRU windows. Covered by the root template and the preset.
_OTHER_CTRL = [("rtu0", 0x23000), ("rtu1", 0x23800),
               ("tx_pru0", 0x25000), ("tx_pru1", 0x25800)]


@pytest.mark.parametrize("cfg", ["memory.cfg", "config/memory_am243x.cfg"])
@pytest.mark.parametrize("window,base", _OTHER_CTRL)
def test_rtu_and_tx_pru_ctrl_windows_are_mapped(cfg, window, base):
    sim = Simulator(str(ROOT / cfg))
    assert sim.load("pru0", f"ldi32 r1, {base}\nlbbo &r2, r1, 0, 4\nhalt") == []
    sim.step("pru0", 3)  # ldi32 is two instructions
    assert sim.cores["pru0"].fault is None
    assert sim.cores["pru0"].counters.cycles == 2 + 6
    sim.memory.write(base + 4, bytes.fromhex("aabbccdd"))
    assert sim.memory_read(base + 4, 4) == bytes.fromhex("aabbccdd")


@pytest.mark.parametrize("cfg", ["memory.cfg", "config/memory_am243x.cfg"])
@pytest.mark.parametrize("core,base", [("rtu0", 0x23000), ("rtu1", 0x23800)])
def test_rtu_cycle_reads_observe_the_rtu(cfg, core, base):
    sim = Simulator(str(ROOT / cfg))
    sim.cores[core].counters.cycles = 33
    assert int.from_bytes(sim.memory_read(base + 0x0C, 4), "little") == 33
    assert sim.load(core, "lbco &r2, c11, 12, 4\nlbco &r3, c11, 12, 4\nhalt") == []
    sim.step(core, 2)
    assert sim.cores[core].fault is None
    assert sim.registers(core)[3] - sim.registers(core)[2] == 6


def test_rtu_ctrl_keeps_its_constant_table_registers():
    sim = Simulator(str(ROOT / "memory.cfg"))
    sim.ctrl_write("rtu0", 0, 0x12)
    assert sim.memory_read(0x23020, 4) == (0x12).to_bytes(4, "little")


@pytest.mark.parametrize("window,base", _OTHER_CTRL)
def test_root_template_retargeted_to_icssm_keeps_absent_ctrl(tmp_path, window, base):
    text = (ROOT / "memory.cfg").read_text().replace("target = AM243x", "target = AM261x")
    cfg = tmp_path / "memory.cfg"
    cfg.write_text(text)
    sim = Simulator(str(cfg))
    sim.memory.write(base + 4, bytes.fromhex("aabbccdd"))
    assert sim.memory_read(base + 4, 4) == bytes(4)


@pytest.mark.parametrize("target", ["AM243x", "AM261x"])
def test_explicit_ctrl_region_latency_wins(tmp_path, target):
    text = (ROOT / "memory.cfg").read_text()
    text = text.replace("target = AM243x", f"target = {target}").replace(
        "pru_ctrl_read_latency = 5", "pru_ctrl_read_latency = 3").replace(
        chr(10) + "read_latency = 5", chr(10) + "read_latency = 9")
    cfg = tmp_path / "memory.cfg"
    cfg.write_text(text)
    sim = Simulator(str(cfg))
    assert sim.load("pru0", "lbco &r2, c11, 12, 4\nhalt") == []
    sim.step("pru0")
    assert sim.cores["pru0"].counters.cycles == 10


def test_partial_and_burst_cycle_readback():
    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    core = sim.cores["pru1"]
    core.counters.cycles = 0x12345678
    assert sim.memory_read(0x2400d, 2) == bytes.fromhex("5634")
    assert sim.memory_read(0x24008, 12) == bytes(4) + bytes.fromhex("78563412") + bytes(4)
    # Other CTRL offsets retain generic storage; no extra register side effects.
    sim.memory.write(0x24020, bytes.fromhex("aabbccdd"))
    assert sim.memory_read(0x24020, 4) == bytes.fromhex("aabbccdd")


def test_cycle_reads_observe_the_addressed_core():
    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    sim.cores["pru0"].counters.cycles = 10
    sim.cores["pru1"].counters.cycles = 20
    assert int.from_bytes(sim.memory_read(0x2200c, 4), "little") == 10
    assert int.from_bytes(sim.memory_read(0x2400c, 4), "little") == 20


def test_ctrl_storage_and_cycle_readback_restore_together(monkeypatch):
    from ui import server

    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    monkeypatch.setattr(server, "sim", sim)
    sim.cores["pru1"].counters.cycles = 20
    sim.memory.write(0x24020, b"abcd")
    snapshot = server._snapshot("pru1")
    sim.cores["pru1"].counters.cycles = 40
    sim.memory.write(0x24020, b"wxyz")
    server._restore("pru1", snapshot)
    assert sim.memory_read(0x24020, 4) == b"abcd"
    assert int.from_bytes(sim.memory_read(0x2400c, 4), "little") == 20


@pytest.mark.parametrize("core,base", [("pru0", 0x22000), ("pru1", 0x24000)])
def test_cycle_write_rebases_the_count(core, base):
    # SPRUIM2J ICSSG_PRU_CYCLE is R/W: the clear-then-read idiom measures a delta.
    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    sim.cores[core].counters.cycles = 100
    sim.memory.write(base + 0x0C, bytes(4))
    sim.cores[core].counters.cycles = 107
    assert int.from_bytes(sim.memory_read(base + 0x0C, 4), "little") == 7
    sim.memory.write(base + 0x0C, (50).to_bytes(4, "little"))
    sim.cores[core].counters.cycles = 109
    assert int.from_bytes(sim.memory_read(base + 0x0C, 4), "little") == 52
    # A partial write patches only its bytes of the current count.
    sim.memory.write(base + 0x0E, bytes([1, 0]))
    assert int.from_bytes(sim.memory_read(base + 0x0C, 4), "little") == 0x10000 + 52


def test_cycle_write_through_sbco_then_lbco():
    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    assert sim.load("pru0", "ldi r1, 0\nsbco &r1, c11, 12, 4\nlbco &r2, c11, 12, 4\nhalt") == []
    sim.cores["pru0"].counters.cycles = 1000
    sim.step("pru0", 3)
    # The count was cleared by SBCO, so LBCO sees a small delta, not ~1000.
    assert 0 < sim.registers("pru0")[2] <= 12


def test_cycle_saturates_instead_of_wrapping():
    # The TRM says the count does not wrap; it stops at FFFFFFFFh.
    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    sim.cores["pru0"].counters.cycles = (1 << 32) + 5
    assert int.from_bytes(sim.memory_read(0x2200C, 4), "little") == 0xFFFFFFFF


def test_cycle_rebase_restores_with_the_region(monkeypatch):
    from ui import server

    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    monkeypatch.setattr(server, "sim", sim)
    sim.cores["pru0"].counters.cycles = 100
    sim.memory.write(0x2200C, bytes(4))
    snapshot = server._snapshot("pru0")
    sim.memory.write(0x2200C, (900).to_bytes(4, "little"))
    server._restore("pru0", snapshot)
    sim.cores["pru0"].counters.cycles = 103
    assert int.from_bytes(sim.memory_read(0x2200C, 4), "little") == 3


@pytest.mark.parametrize("hard", [False, True])
def test_core_reset_clears_the_cycle_rebase(hard):
    sim = Simulator(str(ROOT / "config" / "memory_am243x.cfg"))
    sim.cores["pru0"].counters.cycles = 100
    sim.memory.write(0x2200C, bytes(4))
    assert int.from_bytes(sim.memory_read(0x2200C, 4), "little") == 0
    sim.hard_reset() if hard else sim.reset("pru0")
    sim.cores["pru0"].counters.cycles = 5
    assert int.from_bytes(sim.memory_read(0x2200C, 4), "little") == 5


def _declared_window_sim(tmp_path, latency_line):
    text = (ROOT / "memory.cfg").read_text()
    lines = text.splitlines()
    start = lines.index("[ICSS_CFG]")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")),
               len(lines))
    section = [line for line in lines[start:end] if not line.startswith("read_latency")]
    if latency_line:
        section.insert(1, latency_line)
    text = chr(10).join(lines[:start] + section + lines[end:])
    text = text.replace("pru_ctrl_read_latency = 5", "pru_ctrl_read_latency = 3")
    cfg = tmp_path / "memory.cfg"
    cfg.write_text(text)
    return Simulator(str(cfg))


def test_declared_ctrl_window_without_read_latency_uses_device_key(tmp_path):
    sim = _declared_window_sim(tmp_path, None)
    assert sim.load("pru0", "lbco &r2, c11, 12, 4\nhalt") == []
    sim.step("pru0")
    assert sim.cores["pru0"].counters.cycles == 4


def test_declared_ctrl_window_with_zero_read_latency_is_an_override(tmp_path):
    sim = _declared_window_sim(tmp_path, "read_latency = 0")
    assert sim.load("pru0", "lbco &r2, c11, 12, 4\nhalt") == []
    sim.step("pru0")
    assert sim.cores["pru0"].counters.cycles == 1
