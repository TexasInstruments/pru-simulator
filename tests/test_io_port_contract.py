"""IOPort / PRUCore contract: device cycle accounting and drive-mask validation."""

import pytest

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from pru_io.device_model import PUSH_PULL, DeviceModel
from pru_io.io_port import IOPort
from xfr.xfr_bus import XFRBus


class _CycleRecorder(DeviceModel):
    """Time-driven model that records the cycle number of every tick."""

    name = "cycle-recorder"
    nets = {4: PUSH_PULL}
    time_driven = True

    def __init__(self):
        self.cycles = []

    def tick(self, cycle, bus):
        self.cycles.append(cycle)
        return 0, 0


def _bare_core(asm):
    mem = MemoryBus()
    mem.add_region(MemoryRegion("DRAM0", 0x0000, 0x2000, 2, 1, 0))
    core = PRUCore("PRU0", mem, XFRBus(), IOPort())
    assert core.load_asm(asm) == []
    return core


def test_device_cycle_stays_exact_while_no_device_is_attached():
    # The DRAM load stalls the core, so steps and cycles differ.
    core = _bare_core("ldi r1, 0\nlbbo &r2, r1, 0, 4\nnop\nhalt\n")

    for _ in range(3):
        core.step()

    assert core.counters.cycles == 5
    assert core.io_port._device_cycle == core.counters.cycles


def test_device_attached_mid_run_ticks_from_the_exact_cycle():
    core = _bare_core("ldi r1, 0\nlbbo &r2, r1, 0, 4\n" + "nop\n" * 6 + "halt\n")
    core.step()
    core.step()                             # ldi + stalled lbbo, no device yet
    attach_cycle = core.counters.cycles
    assert attach_cycle == 4

    device = core.io_port.attach_device(_CycleRecorder())
    device.cycles.clear()                   # drop any settle done by attach itself
    for _ in range(4):
        core.step()

    assert device.cycles == list(range(attach_cycle + 1, attach_cycle + 5))
    assert core.io_port._device_cycle == core.counters.cycles == attach_cycle + 4


def test_device_attached_after_many_idle_steps_starts_at_the_right_cycle():
    n = 50
    core = _bare_core("nop\n" * (n + 3) + "halt\n")
    for _ in range(n):
        core.step()

    device = core.io_port.attach_device(_CycleRecorder())
    device.cycles.clear()
    for _ in range(3):
        core.step()

    assert device.cycles == [n + 1, n + 2, n + 3]


def test_detached_device_leaves_the_cycle_count_exact():
    core = _bare_core("nop\n" * 8 + "halt\n")
    device = core.io_port.attach_device(_CycleRecorder())
    for _ in range(3):
        core.step()
    core.io_port.detach_device(device)
    for _ in range(3):
        core.step()

    assert core.io_port._device_cycle == core.counters.cycles == 6


@pytest.mark.parametrize("mask", [True, False, 1.0, "3", None])
def test_gpo_drive_mask_rejects_bool_and_non_int(mask):
    port = IOPort()

    with pytest.raises(ValueError, match="20-bit integer"):
        port.set_gpo_drive_mask(mask)

    assert port.gpo_drive_mask == 0


def test_gpo_drive_mask_still_accepts_an_int():
    port = IOPort()
    port.set_gpo_drive_mask(0x3)
    assert port.gpo_drive_mask == 0x3
