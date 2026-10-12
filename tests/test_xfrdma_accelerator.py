"""XFRDMA vectors from SPRUIM2J §6.4.6.3.2, TI icss_xfer_defines.inc and
LP-AM243 silicon (ICSSG0 PRU0 and RTU_PRU0, no PSI-L peer configured)."""

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.xfr_bus import XFRBus
from xfr.xfrdma_accelerator import XFRDMAAccelerator, XFRDMABridge
from xfr.mac_accelerator import MACAccelerator


def make_core(asm: str) -> PRUCore:
    memory = MemoryBus()
    memory.add_region(MemoryRegion("DRAM0", 0, 0x2000, 2, 1, 0))
    core = PRUCore("PRU0", memory, XFRBus(), IOPort())
    assert core.load_asm(asm) == []
    return core


def bridge(core: PRUCore) -> XFRDMABridge:
    return core.accelerators[0x51].bridge  # type: ignore[attr-defined]


def test_status_is_registered_at_0x50_and_has_two_words():
    core = make_core("xin 0x50, &r2, 8\nhalt\n")
    dma = bridge(core)
    dma.inject_rx(1, b"rx")
    for _ in range(dma.tx_max):
        dma.fill_tx(1)
    core.run()
    assert core.registers.read_full(2) == 0x1   # thread 1 -> bit 0
    assert core.registers.read_full(3) == 0x0   # the one TX thread is full


def test_global_device_id_zero_remains_the_mac_accelerator():
    core = make_core("halt\n")
    assert isinstance(core.accelerators[0], MACAccelerator)
    assert set(range(0x50, 0x55)).issubset(core.accelerators)


def test_four_byte_status_xin_is_rx_ready_only():
    core = make_core("xin 0x50, &r2, 4\nhalt\n")
    dma = bridge(core)
    dma.inject_rx(2, b"rx")
    core.run()
    assert core.registers.read_full(2) == 0x2   # thread 2 -> bit 1


def test_partial_xins_consume_only_requested_bytes():
    core = make_core("\n".join(f"xin 0x51, &r{2 + i}, 4" for i in range(8)) + "\nhalt\n")
    dma = bridge(core)
    payload = bytes(range(32))
    dma.inject_rx(1, payload)
    core.run()
    assert b"".join(core.registers.read_full(2 + i).to_bytes(4, "little") for i in range(8)) == payload


def test_xchg_full_tx_holds_pc_without_consuming_rx():
    """stall_when_full=True: the TRM-implied stall for a configured thread."""
    core = make_core("ldi r2, 0x55\nxchg 0x51, &r2, 4\nhalt\n")
    dma = bridge(core)
    dma.stall_when_full = True
    for _ in range(dma.tx_max):
        dma.fill_tx(1)
    for value in (b"one", b"two", b"tri"):
        dma.inject_rx(1, value)
    core.step()
    assert core.pc == 1
    for _ in range(3):
        core.step()
        assert core.pc == 1
        assert dma.rx_depth(1) == 3
        assert len(dma.tx_fifos[1]) == dma.tx_max


def test_threads_are_separate_and_empty_threads_return_zeros():
    core = make_core("ldi32 r2, 0x21726568\nxout 0x51, &r2, 4\nhalt\n")
    dma = bridge(core)
    core.run()
    assert dma.drain_tx(1) == [b"her!"]
    # Thread 1 traffic does not make the status RX bits or threads 2/3 readable.
    assert core.accelerators[0x50].xin(2, 4) == bytes(4)
    assert not dma.rx_fifos[2]
    assert not dma.rx_fifos[3]
    assert not dma.rx_fifos[4]
    for device_id in (0x52, 0x53, 0x54):
        empty = core.accelerators[device_id]
        assert empty.xin(2, 4) == bytes(4)
        assert not empty.hold_pc


def test_empty_xin_retires_blank_data_and_advances_pc():
    """An empty RX FIFO retires zero data rather than stalling the core."""
    core = make_core("ldi32 r2, 0xDEADBEEF\nxin 0x51, &r2, 4\nhalt\n")
    while core.pc < 2:  # ldi32 expands to two instructions
        core.step()
    core.step()
    assert core.pc == 3
    assert core.registers.read_full(2) == 0
    assert not core.accelerators[0x51].hold_pc


def test_xchg_with_empty_rx_retires_blank_data_and_enqueues_tx():
    core = make_core("ldi32 r2, 0x61746164\nxchg 0x51, &r2, 4\nhalt\n")
    while core.pc < 2:  # ldi32 expands to two instructions
        core.step()
    core.step()
    assert core.pc == 3
    assert core.registers.read_full(2) == 0
    assert bridge(core).drain_tx(1) == [b"data"]


def test_registration_removal_is_caught_by_the_status_vector(monkeypatch):
    """Mutation check: the PR #30 boundary fails loudly without 0x50."""
    core = make_core("xin 0x50, &r2, 8\nhalt\n")
    monkeypatch.delitem(core.accelerators, 0x50)
    with pytest.raises(RuntimeError, match="XFR device ID 80 \\(0x50\\).*not modelled"):
        core.run()


def test_status_id_discards_writes_instead_of_trapping():
    """XID 0 is read-only in the TRM; writing it is undefined, not an error."""
    status = XFRDMAAccelerator(XFRDMABridge())
    status.xout(2, b"test")
    assert status.xchg(2, b"test") == b"test"
    assert status.status_writes == {"XOUT": 1, "XCHG": 1}
    status.reset()
    assert status.status_writes == {}


# --- silicon: LP-AM243, ICSSG0 PRU0 and RTU_PRU0, no PSI-L peer -------------


@pytest.mark.parametrize("name", ["PRU0", "RTU0"])
def test_status_before_any_traffic_matches_silicon(name):
    """Device: XIN 0x50 returns rx_ready 0 and tx_ready 0x1 on both cores --
    one transmit thread, reporting in bit 0."""
    memory = MemoryBus()
    memory.add_region(MemoryRegion("DRAM0", 0, 0x2000, 2, 1, 0))
    core = PRUCore(name, memory, XFRBus(), IOPort())
    assert core.load_asm("xin 0x50, &r2, 8\nhalt\n") == []
    core.run()
    assert (core.registers.read_full(2), core.registers.read_full(3)) == (0x0, 0x1)


@pytest.mark.parametrize("device_id", [0x51, 0x52, 0x53, 0x54])
def test_every_read_widget_answers_an_empty_xin_with_zeros(device_id):
    """Device: 0x51..0x54 all answer (TI: XFR2PSI_RD0..RD3_XID); 0x54 is not
    'unmodelled'.  Empty reads return zeros and do not stall."""
    core = make_core(f"ldi32 r1, 0xDEAD0001\nxin 0x{device_id:x}, &r1, 20\nhalt\n")
    core.run()
    assert core.halted
    assert [core.registers.read_full(r) for r in range(1, 6)] == [0] * 5


@pytest.mark.parametrize("device_id", [0x52, 0x53, 0x54])
def test_writes_to_a_receive_only_thread_are_discarded(device_id):
    """TI names only XFR2PSI_WR0_XID (0x51) for transmit.  Device: an XOUT to
    0x52..0x54 retires without a stall and changes no status bit."""
    core = make_core(f"xout 0x{device_id:x}, &r1, 20\nxin 0x50, &r2, 8\nhalt\n")
    core.run()
    assert core.halted
    assert (core.registers.read_full(2), core.registers.read_full(3)) == (0x0, 0x1)
    assert bridge(core).rx_thread_writes == 1
    assert bridge(core).drain_tx() == []


def test_a_full_transmit_thread_drops_writes_without_stalling():
    """Device, no PSI-L peer: tx_ready falls to 0 within 12 XOUTs and stays 0,
    and 24 further XOUTs to 0x51 retire with no stall."""
    asm = "\n".join(["ldi32 r1, 0x10000003"] + ["xout 0x51, &r1, 20"] * 36 + ["xin 0x50, &r2, 8", "halt"])
    core = make_core(asm + "\n")
    core.run()
    assert core.halted
    dma = bridge(core)
    assert core.registers.read_full(3) == 0x0
    assert len(dma.tx_fifos[1]) == dma.tx_max
    assert dma.tx_dropped == 36 - dma.tx_max
