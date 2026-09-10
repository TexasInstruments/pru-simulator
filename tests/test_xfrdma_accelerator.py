"""Simulator-tested XFRDMA vectors from SPRUIM2J §6.4.6.3.2."""

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.io_port import IOPort
from xfr.xfr_bus import XFRBus
from xfr.xfrdma_accelerator import XFRDMAAccelerator, XFRDMABridge


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
    assert core.registers.read_full(2) == 0x2
    assert core.registers.read_full(3) == 0xC


def test_four_byte_status_xin_is_rx_ready_only():
    core = make_core("xin 0x50, &r2, 4\nhalt\n")
    dma = bridge(core)
    dma.inject_rx(2, b"rx")
    core.run()
    assert core.registers.read_full(2) == 0x4


def test_partial_xins_consume_only_requested_bytes():
    core = make_core("\n".join(f"xin 0x51, &r{2 + i}, 4" for i in range(8)) + "\nhalt\n")
    dma = bridge(core)
    payload = bytes(range(32))
    dma.inject_rx(1, payload)
    core.run()
    assert b"".join(core.registers.read_full(2 + i).to_bytes(4, "little") for i in range(8)) == payload


def test_xchg_full_tx_holds_pc_without_consuming_rx():
    core = make_core("ldi r2, 0x55\nxchg 0x51, &r2, 4\nhalt\n")
    dma = bridge(core)
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


def test_threads_are_separate_and_empty_threads_hold():
    core = make_core("ldi32 r2, 0x21726568\nxout 0x51, &r2, 4\nhalt\n")
    dma = bridge(core)
    core.run()
    assert dma.drain_tx(1) == [b"her!"]
    # Thread 1 traffic does not make the status RX bits or threads 2/3 readable.
    assert core.accelerators[0x50].xin(2, 4) == bytes(4)
    assert not dma.rx_fifos[2]
    assert not dma.rx_fifos[3]
    for device_id in (0x52, 0x53):
        empty = core.accelerators[device_id]
        assert empty.xin(2, 4) == bytes(4)
        assert empty.hold_pc


def test_registration_removal_fails_loudly(monkeypatch):
    core = make_core("xin 0x50, &r2, 8\nhalt\n")
    monkeypatch.delitem(core.accelerators, 0x50)
    with pytest.raises(RuntimeError, match="XIN XFR device ID 80 \\(0x50\\) is not modelled"):
        core.step()


def test_status_id_rejects_xout():
    with pytest.raises(ValueError, match="status ID"):
        XFRDMAAccelerator(XFRDMABridge()).xout(2, b"test")
