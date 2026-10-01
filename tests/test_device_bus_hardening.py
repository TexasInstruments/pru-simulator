"""Focused regressions for DeviceBus cycle and GPIO lifecycle behavior."""

from pru_io.device_model import OPEN_DRAIN, PUSH_PULL, DeviceModel
from pru_io.io_port import IOPort
from simulator import Simulator

_MASK_20 = (1 << 20) - 1


class _PinDevice(DeviceModel):
    def __init__(self, pin, mode=PUSH_PULL, value=0, *, name="pin-device",
                 time_driven=False):
        self.name = name
        self.nets = {pin: mode}
        self.pin = pin
        self.value = value
        self.time_driven = time_driven
        self.calls = []
        self.enabled = True

    def tick(self, cycle, bus):
        self.calls.append((cycle, bus))
        if self.time_driven:
            self.value = (cycle + 1) & 1
        if not self.enabled:
            return 0, 0
        return 1 << self.pin, self.value << self.pin

    def reset(self):
        self.calls.clear()
        self.enabled = False

    def snapshot(self):
        return {"value": self.value, "calls": list(self.calls),
                "enabled": self.enabled}

    def restore(self, snap):
        self.value = snap["value"]
        self.calls = list(snap["calls"])
        self.enabled = snap["enabled"]


def _level(word, pin):
    return (word >> pin) & 1


def test_reactive_device_does_not_tick_for_repeated_r30_value():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    device = sim.attach_device("pru0", _PinDevice(4))
    initial_calls = len(device.calls)

    port.write_r30(1)
    first_changed_value_calls = len(device.calls)
    port.write_r30(1)

    assert first_changed_value_calls > initial_calls
    assert len(device.calls) == first_changed_value_calls


def test_elapsed_cycles_count_even_without_timed_devices_and_late_attach_uses_time():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    sampled = []
    port.advance_devices(7, lambda cycle, gpi: sampled.append((cycle, gpi)))
    device = sim.attach_device("pru0", _PinDevice(4))

    assert port._device_cycle == 7
    assert device.calls[0][0] == 7
    assert [cycle for cycle, _ in sampled] == list(range(1, 8))


def test_time_driven_device_ticks_each_elapsed_cycle_and_pin_edges_reach_reactive_models():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    port.set_gpo_drive_mask(_MASK_20 ^ 1)
    timed = sim.attach_device(
        "pru0", _PinDevice(0, value=0, name="clock", time_driven=True))
    watcher = sim.attach_device("pru0", _PinDevice(2, name="watcher"))
    watcher.calls.clear()

    port.advance_devices(4)

    assert [cycle for cycle, _ in timed.calls] == [1, 2, 3, 4]
    assert [cycle for cycle, _ in watcher.calls] == [1, 2, 3, 4]
    assert [_level(bus, 0) for _, bus in watcher.calls] == [0, 1, 0, 1]


def test_unwired_and_shared_resolvers_have_matching_device_semantics():
    sims = [Simulator(), Simulator()]
    devices = []
    for sim in sims:
        port = sim.cores["pru0"].io_port
        port.set_gpo_drive_mask(_MASK_20 ^ 1)
        timed = sim.attach_device(
            "pru0", _PinDevice(0, value=0, name="clock", time_driven=True))
        watcher = sim.attach_device("pru0", _PinDevice(2, name="watcher"))
        watcher.calls.clear()
        devices.append((timed, watcher))
    # An unrelated shared net routes one simulator through the general resolver.
    sims[1].set_gpio_drive_mask("rtu0", _MASK_20 ^ (1 << 8))
    sims[1].add_gpio_wire("pru0", 8, "rtu0", 8)

    for sim in sims:
        port = sim.cores["pru0"].io_port
        port.advance_devices(4)
        port.write_r30(1 << 3)

    assert devices[0][0].calls == devices[1][0].calls
    assert devices[0][1].calls == devices[1][1].calls
    assert sims[0].cores["pru0"].io_port.gpi == sims[1].cores["pru0"].io_port.gpi
    assert sims[0].device_bus.faults() == sims[1].device_bus.faults()


def test_cross_core_wire_resolves_direction_without_changing_drive_masks():
    sim = Simulator()
    a = sim.cores["pru0"].io_port
    b = sim.cores["rtu0"].io_port
    pin_a, pin_b = 3, 7
    a.write_r30(1 << pin_a)
    b.set_gpo_drive_mask(_MASK_20 ^ (1 << pin_b))

    assert sim.add_gpio_wire("pru0", pin_a, "rtu0", pin_b)

    assert _level(b.gpi, pin_b) == 1
    assert a.gpo_drive_mask == _MASK_20
    assert b.gpo_drive_mask == (_MASK_20 ^ (1 << pin_b))
    assert sim.list_gpio_wires() == [
        {"core_a": "pru0", "pin_a": pin_a, "core_b": "rtu0", "pin_b": pin_b}
    ]
    assert sim.device_bus.faults() == []


def test_two_core_push_pull_conflict_is_reported():
    sim = Simulator()
    sim.add_gpio_wire("pru0", 2, "rtu0", 2)
    sim.cores["pru0"].io_port.write_r30(1 << 2)

    assert any("push-pull contention" in fault and "pru0=1" in fault
               and "rtu0=0" in fault for fault in sim.device_bus.faults())


def test_device_vs_core_push_pull_conflict_is_reported():
    sim = Simulator()
    port = sim.cores["pru1"].io_port
    port.write_r30(1 << 5)
    sim.attach_device("pru1", _PinDevice(5, value=0, name="low-device"))

    assert any("push-pull contention" in fault and "pru1=1" in fault
               and "low-device=0" in fault for fault in sim.device_bus.faults())


def test_mixed_open_drain_and_push_pull_conflict_is_reported():
    sim = Simulator()
    sim.set_gpio_drive_mask("pru0", _MASK_20 ^ (1 << 6))
    sim.attach_device("pru0", _PinDevice(6, OPEN_DRAIN, 0, name="open-drain"))
    sim.attach_device("pru0", _PinDevice(6, PUSH_PULL, 1, name="push-pull"))

    assert any("mixed open-drain/push-pull contention" in fault
               and "open-drain=0" in fault and "push-pull=1" in fault
               for fault in sim.device_bus.faults())


def test_detach_releases_previous_device_drive():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    port.set_gpo_drive_mask(_MASK_20 ^ 1)
    device = sim.attach_device("pru0", _PinDevice(0, value=0))
    assert _level(port.gpi, 0) == 0

    sim.detach_device(device)

    assert _level(port.gpi, 0) == 1
    assert device not in sim.device_bus.devices


def test_reset_keeps_attachment_and_direction_but_drops_stale_device_drive():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    port.set_gpo_drive_mask(_MASK_20 ^ 1)
    device = sim.attach_device("pru0", _PinDevice(0, value=0))
    port.write_r30(1)
    assert port.gpo == 1
    assert _level(port.gpi, 0) == 0

    sim.reset("pru0")

    assert port.gpo == 0
    assert port.gpo_drive_mask == (_MASK_20 ^ 1)
    assert device in sim.device_bus.devices
    assert sim.device_bus._last_drives[id(device)] == (0, 0)
    assert _level(port.gpi, 0) == 1


def test_standalone_ioport_reset_uses_reset_gpo_for_device_resolution():
    port = IOPort()
    port.set_gpo_drive_mask(_MASK_20 ^ 1)
    device = port.attach_device(_PinDevice(0, value=0))
    port.write_r30(1)
    assert _level(port.gpi, 0) == 0

    port.reset()

    assert device in port.device_bus.devices
    assert port.gpo == 0
    assert _level(port.gpi, 0) == 1


def test_ui_step_back_restores_device_drives_wires_masks_and_cycle(monkeypatch):
    from ui import server as ui_server

    sim = Simulator()
    monkeypatch.setattr(ui_server, "sim", sim)
    port = sim.cores["pru0"].io_port
    sim.set_gpio_drive_mask("rtu0", _MASK_20 ^ (1 << 5))
    device = sim.attach_device("rtu0", _PinDevice(5, value=0))
    sim.add_gpio_wire("pru0", 0, "rtu0", 1)
    port.write_r30(1)
    port.advance_devices(4)
    before = ui_server._snapshot("pru0")

    port.write_r30(1 << 2)
    sim.set_gpio_drive_mask("rtu0", _MASK_20)
    sim.remove_gpio_wire("pru0", 0, "rtu0", 1)
    port.advance_devices(3)
    device.enabled = False

    ui_server._restore("pru0", before)

    assert port.gpo == before["gpo"]
    assert port.gpo_drive_mask == _MASK_20
    assert port._device_cycle == 4
    assert sim.cores["rtu0"].io_port.gpo_drive_mask == (_MASK_20 ^ (1 << 5))
    assert sim.list_gpio_wires() == [
        {"core_a": "pru0", "pin_a": 0, "core_b": "rtu0", "pin_b": 1}
    ]
    assert device in sim.device_bus.devices
    assert device.enabled is True
    assert sim.device_bus._last_drives[id(device)] == (1 << 5, 0)


def test_ui_step_back_restores_device_attachments(monkeypatch):
    from ui import server as ui_server

    sim = Simulator()
    monkeypatch.setattr(ui_server, "sim", sim)
    original = sim.attach_device("pru0", _PinDevice(4, name="original"))
    before = ui_server._snapshot("pru0")
    later = sim.attach_device("rtu0", _PinDevice(5, name="later"))
    sim.detach_device(original)

    ui_server._restore("pru0", before)

    assert sim.device_bus.devices == [original]
    assert later not in sim.device_bus.devices
