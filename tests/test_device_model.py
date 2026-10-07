"""DeviceModel contract + bus resolution, with falsification arms.

The falsification tests are the reason this file exists. A device model that
only ever produces plausible waveforms proves nothing: the question is whether
it REJECTS firmware that is wrong. Each `test_*_rejects_*` below drives a
deliberately broken sequence and requires a fault. If one of them starts
passing silently, the corresponding model has stopped being an oracle.
"""
from __future__ import annotations

import pytest

from pru_io.device_model import OPEN_DRAIN, PUSH_PULL, DeviceBus, DeviceModel
from pru_io.tca9538_device_model import TCA9538Model

SCL, SDA = 0, 1


class _Puller(DeviceModel):
    """Minimal device: pulls a declared pin low on demand."""

    def __init__(self, pin, net, name="puller", level=0):
        self.name = name
        self.pin = pin
        self.nets = {pin: net}
        self.level = level
        self.driving = False

    def tick(self, cycle, bus):
        if not self.driving:
            return 0, 0
        return (1 << self.pin), (self.level << self.pin)


# ----------------------------------------------------------------------
# Bus resolution
# ----------------------------------------------------------------------

def test_idle_bus_is_pulled_high():
    bus = DeviceBus()
    bus.set_pru_drive_mask(0)
    assert bus.settle(0, 0) == (1 << 20) - 1


def test_open_drain_is_wired_and():
    """One device pulling low beats every other driver. This is the property
    the whole multi-driver story rests on."""
    bus = DeviceBus()
    dev = bus.attach(_Puller(SDA, OPEN_DRAIN))
    bus.set_pru_drive_mask(1 << SDA)

    # PRU drives high, device released -> high
    assert (bus.settle(0, 1 << SDA) >> SDA) & 1 == 1
    # PRU still drives high, device pulls low -> low
    dev.driving = True
    assert (bus.settle(1, 1 << SDA) >> SDA) & 1 == 0


def test_push_pull_conflict_is_recorded_not_resolved():
    """Two drivers at opposite levels is a short. The bus must say so rather
    than quietly pick a winner - silently resolving hides a design error."""
    bus = DeviceBus()
    dev = bus.attach(_Puller(4, PUSH_PULL, level=0))
    dev.driving = True
    bus.set_pru_drive_mask(1 << 4)

    bus.settle(0, 1 << 4)          # PRU drives 1, device drives 0
    assert any("contention" in f for f in bus.faults())


def test_open_drain_declaration_wins_for_the_whole_net():
    """A single open-drain device makes the net open-drain for everyone, which
    is how a real bus behaves - so this must NOT be reported as contention."""
    bus = DeviceBus()
    dev = bus.attach(_Puller(SDA, OPEN_DRAIN, level=0))
    dev.driving = True
    bus.set_pru_drive_mask(1 << SDA)

    bus.settle(0, 1 << SDA)
    assert bus.faults() == []


def test_devices_see_the_pru_immediately_but_not_their_own_drive():
    """A device must react to the PRU's output in the same cycle - an I2C slave
    a cycle late would miss a START. It must NOT see the result of its own
    drive, which is the part real silicon cannot do either."""
    seen = []

    class Watcher(_Puller):
        def tick(self, cycle, bus):
            seen.append((bus >> SDA) & 1)
            return super().tick(cycle, bus)

    bus = DeviceBus()
    dev = bus.attach(Watcher(SDA, OPEN_DRAIN))
    dev.driving = True
    bus.set_pru_drive_mask(0)

    bus.settle(0, 0)
    bus.settle(1, 0)
    assert seen[0] == 1            # idle high on the first look
    assert seen[1] == 0            # last cycle's pull-down, not this one's


def test_reset_clears_faults_and_bus():
    bus = DeviceBus()
    dev = bus.attach(_Puller(4, PUSH_PULL, level=0))
    dev.driving = True
    bus.set_pru_drive_mask(1 << 4)
    bus.settle(0, 1 << 4)
    assert bus.faults()

    bus.reset()
    assert bus.faults() == []


def test_snapshot_restore_round_trips():
    bus = DeviceBus()
    bus.attach(TCA9538Model())
    bus.set_pru_drive_mask((1 << SCL) | (1 << SDA))
    for cycle in range(4):
        bus.settle(cycle, (1 << SCL) | (1 << SDA))

    snap = bus.snapshot()
    bus.settle(99, 0)
    bus.restore(snap)
    assert bus.snapshot()["bus"] == snap["bus"]


# ----------------------------------------------------------------------
# TCA9538 port
# ----------------------------------------------------------------------

def _drive(bus, cycle, scl, sda):
    """One settle with the PRU driving SCL/SDA to the given levels."""
    gpo = (scl << SCL) | (sda << SDA)
    return bus.settle(cycle, gpo)


def _make_bus():
    bus = DeviceBus()
    model = bus.attach(TCA9538Model())
    bus.set_pru_drive_mask((1 << SCL) | (1 << SDA))
    return bus, model


def test_tca9538_declares_both_lines_open_drain():
    _, model = _make_bus()
    assert model.nets == {SCL: OPEN_DRAIN, SDA: OPEN_DRAIN}


def test_tca9538_decodes_start_and_stop():
    bus, model = _make_bus()
    cycle = 0
    for scl, sda in ((1, 1), (1, 1), (1, 0)):        # SDA falls, SCL high
        _drive(bus, cycle, scl, sda); cycle += 1
    for scl, sda in ((0, 0), (1, 0), (1, 1)):        # SDA rises, SCL high
        _drive(bus, cycle, scl, sda); cycle += 1

    kinds = [e["kind"] for e in model.events()]
    assert "start" in kinds
    assert "stop" in kinds


def test_tca9538_clean_transfer_raises_no_fault():
    """Control arm. Without this, a fault test proves nothing - the model might
    simply fault on everything."""
    bus, model = _make_bus()
    cycle = 0
    _drive(bus, cycle, 1, 1); cycle += 1
    _drive(bus, cycle, 1, 0); cycle += 1             # START
    for bit in (1, 0, 0, 0, 1, 1, 0, 0):             # address, SDA moves low-clock
        _drive(bus, cycle, 0, bit); cycle += 1
        _drive(bus, cycle, 1, bit); cycle += 1
        _drive(bus, cycle, 0, bit); cycle += 1
    assert model.faults() == []


def test_tca9538_rejects_sda_moving_while_clock_high():
    """FALSIFICATION ARM.

    Driving SDA before pulling SCL low is the classic bit-banged-I2C ordering
    bug, and it is illegal regardless of intent: a transition on a high clock
    IS a START or STOP by definition, so mid-byte it corrupts the transfer.
    The model must catch it. If this test ever passes without the fault, the
    TCA9538 port has stopped being able to reject anything.
    """
    bus, model = _make_bus()
    cycle = 0
    _drive(bus, cycle, 1, 1); cycle += 1
    _drive(bus, cycle, 1, 0); cycle += 1             # START -> leaves IDLE
    _drive(bus, cycle, 0, 0); cycle += 1
    _drive(bus, cycle, 1, 0); cycle += 1             # clock the first bit
    _drive(bus, cycle, 1, 1); cycle += 1             # SDA moves, clock STILL high

    assert model.faults(), "SDA change on a high clock was not rejected"
    assert "SCL high" in model.faults()[0]


@pytest.mark.parametrize("pins", [(0, 1), (5, 6), (12, 13)])
def test_tca9538_works_on_arbitrary_pins(pins):
    """The whole point of the port: no longer frozen at IOPort's pins 0 and 1."""
    scl_pin, sda_pin = pins
    bus = DeviceBus()
    model = bus.attach(TCA9538Model(scl_pin=scl_pin, sda_pin=sda_pin))
    bus.set_pru_drive_mask((1 << scl_pin) | (1 << sda_pin))

    high = (1 << scl_pin) | (1 << sda_pin)
    bus.settle(0, high)
    bus.settle(1, 1 << scl_pin)                      # SDA falls, SCL high
    assert [e["kind"] for e in model.events()] == ["start"]
