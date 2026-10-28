"""C24-C31 follow CTBIR0/1 and CTPPR0/1 of the executing core's CTRL block.

Expected values are literals, taken from the constant table read directly
(ICTE debug block and CCS register view) on an LP-AM243 (ICSSG0, six cores) and
an LP-AM261 (ICSSM0, two cores) after the host wrote each register set below
and again at the values found. Registers at reset read 0 in the simulator; the
boards' power-on value was not measured (no power cycle).
"""
import pytest

from mem.constant_table import CTBIR0, CTBIR1, CTPPR0, CTPPR1, ConstantTable
from simulator import Simulator

# (CTBIR0, CTBIR1, CTPPR0, CTPPR1) -> C24..C31, as read from silicon.
SET_A = (0x00030003, 0x00030003, 0x01000100, 0x01000100)
SET_A_TABLE = [0x300, 0x2300, 0x2E300, 0x32300,
               0x10000, 0xD0010000, 0xE0010000, 0xF0010000]
SET_B = (0x00070007, 0x00070007, 0x02000120, 0x02000200)
SET_B_TABLE = [0x700, 0x2700, 0x2E700, 0x32700,
               0x12000, 0xD0020000, 0xE0020000, 0xF0020000]
RESET_TABLE = [0, 0x2000, 0x2E000, 0x32000, 0, 0xD0000000, 0xE0000000, 0xF0000000]
# AM261x as found: CTPPR0 = 0x100 left over from earlier use.
AM261_AS_FOUND = (0, 0, 0x100, 0)
AM261_AS_FOUND_TABLE = [0, 0x2000, 0x2E000, 0x32000,
                        0x10000, 0xD0000000, 0xE0000000, 0xF0000000]

# core -> CTRL block base (local address).
AM243_CTRL = {"pru0": 0x22000, "rtu0": 0x23000, "pru1": 0x24000, "rtu1": 0x23800}
ICSSM_CTRL = {"pru0": 0x22000, "pru1": 0x24000}
CASES = ([("AM243x", c, b) for c, b in AM243_CTRL.items()]
         + [("AM261x", c, b) for c, b in ICSSM_CTRL.items()]
         + [("AM263x", c, b) for c, b in ICSSM_CTRL.items()])
IDS = [f"{t}-{c}" for t, c, _ in CASES]


def _words(values):
    return b"".join(v.to_bytes(4, "little") for v in values)


def _table(sim, core):
    return [sim.cores[core].constant_table.resolve(n) for n in range(24, 32)]


def _run(sim, core, source, steps=200):
    assert sim.load(core, source) == []
    for _ in range(steps):
        if sim.cores[core].halted:
            break
        sim.step(core)
    assert sim.cores[core].fault is None
    assert sim.cores[core].halted


def test_formulas_on_the_bare_table():
    t = ConstantTable()
    assert [t.resolve(n) for n in range(24, 32)] == RESET_TABLE
    for i, v in enumerate(SET_A):
        t.write_ctrl(i, v)
    assert [t.resolve(n) for n in range(24, 32)] == SET_A_TABLE
    for i, v in enumerate(SET_B):
        t.write_ctrl(i, v)
    assert [t.resolve(n) for n in range(24, 32)] == SET_B_TABLE
    assert [t.read_ctrl(i) for i in range(4)] == list(SET_B)


@pytest.mark.parametrize("field,reg,value,entry,expected", [
    ("CTBIR0[7:0]", CTBIR0, 0x000000AB, 24, 0xAB00),
    ("CTBIR0[23:16]", CTBIR0, 0x00CD0000, 25, 0x2000 | 0xCD00),
    ("CTBIR1[7:0]", CTBIR1, 0x000000EF, 26, 0x2E000 | 0xEF00),
    ("CTBIR1[23:16]", CTBIR1, 0x00120000, 27, 0x32000 | 0x1200),
    ("CTPPR0[15:0]", CTPPR0, 0x0000ABCD, 28, 0xABCD00),
    ("CTPPR0[31:16]", CTPPR0, 0x1234_0000, 29, 0xD0000000 | 0x1234 << 8),
    ("CTPPR1[15:0]", CTPPR1, 0x0000BEEF, 30, 0xE0000000 | 0xBEEF << 8),
    ("CTPPR1[31:16]", CTPPR1, 0x5678_0000, 31, 0xF0000000 | 0x5678 << 8),
])
def test_each_field_drives_only_its_entry(field, reg, value, entry, expected):
    t = ConstantTable()
    t.write_ctrl(reg, value)
    for n in range(24, 32):
        want = expected if n == entry else RESET_TABLE[n - 24]
        assert t.resolve(n) == want, f"{field}: c{n}"


@pytest.mark.parametrize("target,core,base", CASES, ids=IDS)
def test_reset_value_then_host_writes_update_the_table(sim_config, target, core, base):
    sim = Simulator(sim_config(target=target))
    assert _table(sim, core) == RESET_TABLE
    assert sim.memory_read(base + 0x20, 16) == b"\0" * 16
    sim.memory.write(base + 0x20, _words(SET_A))
    assert _table(sim, core) == SET_A_TABLE
    assert sim.memory_read(base + 0x20, 16) == _words(SET_A)
    sim.ctrl_write(core, CTPPR0, SET_B[2])     # host API, one register
    sim.ctrl_write(core, CTPPR1, SET_B[3])
    sim.ctrl_write(core, CTBIR0, SET_B[0])
    sim.ctrl_write(core, CTBIR1, SET_B[1])
    assert _table(sim, core) == SET_B_TABLE
    assert sim.memory_read(base + 0x20, 16) == _words(SET_B)


@pytest.mark.parametrize("target,core,base", CASES, ids=IDS)
def test_write_is_per_core(sim_config, target, core, base):
    sim = Simulator(sim_config(target=target))
    sim.memory.write(base + 0x20, _words(SET_A))
    for other in sim.cores:
        if other != core:
            assert _table(sim, other) == RESET_TABLE, other


def test_am261_as_found_state_gives_shared_ram_c28(sim_config):
    sim = Simulator(sim_config(target="AM261x"))
    for i, v in enumerate(AM261_AS_FOUND):
        sim.ctrl_write("pru1", i, v)
    assert _table(sim, "pru1") == AM261_AS_FOUND_TABLE


def test_single_byte_write_keeps_the_rest_of_the_register(sim_config):
    sim = Simulator(sim_config(target="AM243x"))
    sim.ctrl_write("pru0", CTPPR0, 0x01000100)
    sim.memory.write(0x22028 + 1, b"\x02")      # CTPPR0 byte 1: 0x100 -> 0x200
    assert sim.memory_read(0x22028, 4) == (0x01000200).to_bytes(4, "little")
    assert sim.cores["pru0"].constant_table.resolve(28) == 0x20000


def test_hard_reset_returns_registers_to_reset(sim_config):
    sim = Simulator(sim_config(target="AM243x"))
    sim.memory.write(0x22020, _words(SET_A))
    sim.hard_reset()
    assert _table(sim, "pru0") == RESET_TABLE
    assert sim.memory_read(0x22020, 16) == b"\0" * 16


def test_register_write_overrides_a_direct_set():
    t = ConstantTable()
    t.set(28, 0x1234)
    assert t.resolve(28) == 0x1234
    t.write_ctrl(CTPPR0, 0x100)
    assert t.resolve(28) == 0x10000


# ---- firmware level -------------------------------------------------------

@pytest.mark.parametrize("target,core,base", CASES, ids=IDS)
def test_firmware_writes_all_four_registers_through_c11(sim_config, target, core, base):
    sim = Simulator(sim_config(target=target))
    src = "\n".join([
        f"ldi32 r4, 0x{SET_B[0]:08X}", f"ldi32 r5, 0x{SET_B[1]:08X}",
        f"ldi32 r6, 0x{SET_B[2]:08X}", f"ldi32 r7, 0x{SET_B[3]:08X}",
        "sbco &r4, c11, 0x20, 16",         # CTBIR0..CTPPR1 through C11
        "lbco &r8, c11, 0x20, 16",         # read back
        "halt"])
    _run(sim, core, src)
    assert _table(sim, core) == SET_B_TABLE
    assert [sim.cores[core].registers.read_full(i) for i in range(8, 12)] == list(SET_B)


@pytest.mark.parametrize("target,core,base", CASES, ids=IDS)
def test_firmware_writes_through_the_local_ctrl_address(sim_config, target, core, base):
    sim = Simulator(sim_config(target=target))
    src = "\n".join([
        f"ldi32 r1, 0x{base:X}",
        f"ldi32 r4, 0x{SET_A[0]:08X}", f"ldi32 r5, 0x{SET_A[1]:08X}",
        f"ldi32 r6, 0x{SET_A[2]:08X}", f"ldi32 r7, 0x{SET_A[3]:08X}",
        "sbbo &r4, r1, 0x20, 16",
        "halt"])
    _run(sim, core, src)
    assert _table(sim, core) == SET_A_TABLE


def test_firmware_cross_core_write_updates_the_target_core(sim_config):
    sim = Simulator(sim_config(target="AM261x"))
    src = "\n".join(["ldi32 r1, 0x24000", "ldi32 r6, 0x00000100",
                     "sbbo &r6, r1, 0x28, 4", "halt"])    # PRU0 programs PRU1's CTPPR0
    _run(sim, "pru0", src)
    assert sim.cores["pru1"].constant_table.resolve(28) == 0x10000
    assert sim.cores["pru0"].constant_table.resolve(28) == 0


@pytest.mark.parametrize("target,core", [("AM243x", "pru0"), ("AM243x", "pru1"),
                                         ("AM261x", "pru0"), ("AM261x", "pru1"),
                                         ("AM263x", "pru1")])
def test_sbco_through_c28_lands_in_shared_ram_after_ctppr0_is_programmed(
        sim_config, target, core):
    sim = Simulator(sim_config(target=target))
    src = "\n".join([
        "ldi32 r6, 0x00000100",
        "sbco &r6, c11, 0x28, 4",          # CTPPR0.blk_ptr = 0x100 -> C28 = 0x10000
        "ldi32 r2, 0xCAFEF00D",
        "sbco &r2, c28, 0x40, 4",
        "lbco &r3, c28, 0x40, 4",
        "halt"])
    _run(sim, core, src)
    assert sim.cores[core].constant_table.resolve(28) == 0x10000
    assert sim.memory_read(0x10040, 4) == (0xCAFEF00D).to_bytes(4, "little")
    assert sim.cores[core].registers.read_full(3) == 0xCAFEF00D


def test_sbco_through_c28_before_programming_does_not_reach_shared_ram(sim_config):
    sim = Simulator(sim_config(target="AM243x"))
    src = "\n".join(["ldi32 r2, 0xCAFEF00D", "sbco &r2, c28, 0x40, 4", "halt"])
    _run(sim, "pru0", src)
    assert sim.memory_read(0x10040, 4) == b"\0" * 4        # not Shared RAM
    assert sim.memory_read(0x0040, 4) == (0xCAFEF00D).to_bytes(4, "little")  # own DRAM0


def test_c24_follows_ctbir0_in_the_cores_own_view(sim_config):
    # PRU1 sees its own DRAM (DRAM1, global 0x2000) at local 0 + n<<8.
    sim = Simulator(sim_config(target="AM261x"))
    sim.memory.write(0x2000 + 0x300 + 8, (0x600DF00D).to_bytes(4, "little"))
    sim.ctrl_write("pru1", CTBIR0, 0x00000003)         # C24 = 0x300
    _run(sim, "pru1", "lbco &r2, c24, 8, 4\nhalt")
    assert sim.cores["pru1"].registers.read_full(2) == 0x600DF00D


# ---- dashboard ------------------------------------------------------------

def test_dashboard_step_back_restores_ctbir_ctppr(monkeypatch):
    from ui import server as ui_server

    sim = Simulator()
    monkeypatch.setattr(ui_server, "sim", sim)
    sim.ctrl_write("pru0", CTPPR0, 0x100)
    before = ui_server._snapshot("pru0")
    sim.ctrl_write("pru0", CTPPR0, 0x120)
    sim.ctrl_write("pru0", CTBIR0, 0x03)
    assert sim.cores["pru0"].constant_table.resolve(28) == 0x12000

    ui_server._restore("pru0", before)

    assert sim.cores["pru0"].constant_table.resolve(28) == 0x10000
    assert sim.cores["pru0"].constant_table.resolve(24) == 0


def test_dashboard_constant_table_endpoint_is_live(monkeypatch):
    from fastapi.testclient import TestClient
    from ui import server as ui_server

    sim = Simulator("config/memory_am261x.cfg")
    monkeypatch.setattr(ui_server, "sim", sim)
    client = TestClient(ui_server.app)

    rows = client.get("/constant_table/pru1").json()
    table = {r["c"]: r["addr"] for r in rows}
    assert table[10] == 0x2A200 and table[12] == 0x27000    # pointers shown
    assert table[13] == 0x2C000 and table[14] == 0x24800
    assert table[28] == 0                                   # CTPPR0 = 0 at reset

    sim.ctrl_write("pru1", CTPPR0, 0x100)
    table = {r["c"]: r["addr"] for r in client.get("/constant_table/pru1").json()}
    assert table[28] == 0x10000
    assert client.get("/constant_table/nosuch").status_code == 404
