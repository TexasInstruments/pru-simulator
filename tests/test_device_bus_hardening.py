"""Focused regressions for DeviceBus cycle and GPIO lifecycle behavior."""

from core.pru_core import PRUCore
from pru_io.device_model import OPEN_DRAIN, PUSH_PULL, DeviceModel
from pru_io.io_port import IOPort
from pru_io.tca9538_device_model import TCA9538Model
from simulator import Simulator
from xfr.xfr_bus import XFRBus

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


def test_reactive_device_is_settled_by_r30_changes_and_not_by_idle_steps():
    sim = Simulator()
    sim.cores["pru0"].io_port.set_gpo_drive_mask(_MASK_20 ^ (1 << 4))
    device = sim.attach_device("pru0", _PinDevice(4))
    assert sim.load(
        "pru0", "nop\n" * 50 + "ldi r30, 1\nldi r30, 1\nldi r30, 0\nhalt\n") == []
    settled = len(device.calls)

    sim.step("pru0", 50)
    assert len(device.calls) == settled

    sim.step("pru0")   # R30 0 -> 1
    assert len(device.calls) == settled + 1
    sim.step("pru0")   # the same value again
    assert len(device.calls) == settled + 1
    sim.step("pru0")   # R30 1 -> 0
    assert len(device.calls) == settled + 2


def test_elapsed_cycles_count_even_without_timed_devices_and_late_attach_uses_time():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    sampled = []
    port.advance_devices(7, lambda cycle, gpi: sampled.append((cycle, gpi)))
    device = sim.attach_device("pru0", _PinDevice(4))

    assert port._device_cycle == 7
    assert device.calls[0][0] == 7
    assert [cycle for cycle, _ in sampled] == list(range(1, 8))


def test_core_observer_advances_timed_device_for_instruction_and_stall_cycles():
    sim = Simulator()
    port = sim.cores["pru0"].io_port
    port.set_gpo_drive_mask(_MASK_20 ^ (1 << 4))
    device = sim.attach_device(
        "pru0", _PinDevice(4, name="clock", time_driven=True))
    assert sim.load(
        "pru0",
        "ldi r0, 0\n"
        "lbbo &r1, r0, 0, 4\n"
        "wbs 1\n"
        "halt\n",
    ) == []
    sim.set_input("pru0", 1, False)

    sim.step("pru0", 3)

    assert sim.cores["pru0"].counters.stall_cycles == 3
    assert [cycle for cycle, _ in device.calls] == [1, 2, 3, 4, 5]
    assert port._device_cycle == 5

    sim.set_input("pru0", 1, True)
    sim.step("pru0")

    sim.step("pru0")  # HALT is an executed core cycle
    assert [cycle for cycle, _ in device.calls] == [1, 2, 3, 4, 5, 6, 7]
    assert port._device_cycle == 7

    sim.step("pru0")  # halted no-op must not advance external time
    assert [cycle for cycle, _ in device.calls] == [1, 2, 3, 4, 5, 6, 7]
    assert port._device_cycle == 7


class _TickCounter(DeviceModel):
    """Time-driven model that only counts how often the bus ticked it."""

    name = "tick-counter"
    nets = {4: PUSH_PULL}
    time_driven = True

    def __init__(self):
        self.ticks = 0

    def tick(self, cycle, bus):
        self.ticks += 1
        return 0, 0


def _ticks_over_steps(core, source, steps):
    device = core.io_port.attach_device(_TickCounter())
    baseline = device.ticks
    assert core.load_asm(source) == []
    for _ in range(steps):
        core.step()
    return device.ticks - baseline


def test_time_driven_device_ticks_on_a_bare_core_as_under_a_simulator(monkeypatch):
    # MS_RAM (memory.cfg) has random read jitter; pin it so the stalls are exact.
    monkeypatch.setattr("mem.regions.random.randint", lambda low, high: 0)
    memory = Simulator().memory

    for source, steps, expected in (
        ("nop\n" * 5 + "halt\n", 5, 5),
        # ldi32 is two cycles; the 8-word LBBO is 1 + MS_RAM's 40 + 7 more words.
        ("ldi32 r1, 0x70000000\nlbbo &r2, r1, 0, 32\nhalt\n", 3, 2 + 1 + 40 + 7),
    ):
        bare = PRUCore("PRU0", memory, XFRBus(), IOPort())
        owned = Simulator().cores["pru0"]

        assert _ticks_over_steps(bare, source, steps) == expected
        assert _ticks_over_steps(owned, source, steps) == expected


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
    a.set_gpo_drive_mask(_MASK_20)
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
    sim.set_gpio_drive_mask("pru0", 1 << 2)
    sim.set_gpio_drive_mask("rtu0", 1 << 2)
    sim.add_gpio_wire("pru0", 2, "rtu0", 2)
    sim.cores["pru0"].io_port.write_r30(1 << 2)

    assert any("push-pull contention" in fault and "pru0=1" in fault
               and "rtu0=0" in fault for fault in sim.device_bus.faults())


def test_device_vs_core_push_pull_conflict_is_reported():
    sim = Simulator()
    port = sim.cores["pru1"].io_port
    port.set_gpo_drive_mask(1 << 5)
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
    sim.set_gpio_drive_mask("pru0", _MASK_20)
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

    assert port.gpo == before["cores"]["pru0"]["gpo"]
    assert port.gpo_drive_mask == _MASK_20
    assert port._device_cycle == 4
    assert sim.cores["rtu0"].io_port.gpo_drive_mask == (_MASK_20 ^ (1 << 5))
    assert sim.list_gpio_wires() == [
        {"core_a": "pru0", "pin_a": 0, "core_b": "rtu0", "pin_b": 1}
    ]
    assert device in sim.device_bus.devices
    assert device.enabled is True
    assert sim.device_bus._last_drives[id(device)] == (1 << 5, 0)


def test_ui_step_back_restores_every_core_with_shared_gpio(monkeypatch):
    from ui import server as ui_server

    sim = Simulator()
    monkeypatch.setattr(ui_server, "sim", sim)
    pru0 = sim.cores["pru0"]
    rtu0 = sim.cores["rtu0"]
    sim.set_gpio_drive_mask("pru0", 0)
    sim.set_gpio_drive_mask("rtu0", 1 << 1)
    sim.add_gpio_wire("pru0", 0, "rtu0", 1)
    before = ui_server._snapshot("pru0")

    rtu0.registers.write_full(30, 1 << 1)
    rtu0.io_port.write_r30(1 << 1)
    assert _level(pru0.io_port.gpi, 0) == 1

    ui_server._restore("pru0", before)

    assert rtu0.registers.read_full(30) == 0
    assert rtu0.io_port.gpo == 0
    assert _level(pru0.io_port.gpi, 0) == 0


def test_ui_step_back_rewinds_remote_core_and_tca_transaction(
        monkeypatch, nominal_config):
    from pathlib import Path
    from ui import server as ui_server

    sim = Simulator(nominal_config)
    monkeypatch.setattr(ui_server, "sim", sim)
    firmware = Path("source/i2c_tca9538_running_led.asm").read_text()
    assert sim.load("rtu0", firmware) == []
    sim.set_gpio_drive_mask("rtu0", 3)
    model = sim.attach_device("rtu0", TCA9538Model())
    rtu0 = sim.cores["rtu0"]
    before = ui_server._snapshot("pru0")

    sim.step("rtu0", count=20_000)

    assert model.device.config_reg == 0x00
    assert model.events()
    assert rtu0.counters.instruction_count > 0

    ui_server._restore("pru0", before)

    assert rtu0.pc == 0
    assert rtu0.counters.instruction_count == 0
    assert rtu0.registers.read_full(30) == 0
    assert model.device.config_reg == 0xFF
    assert model.events() == []


def test_ui_step_back_preserves_shared_history_order(monkeypatch):
    from ui import server as ui_server

    sim = Simulator()
    monkeypatch.setattr(ui_server, "sim", sim)
    histories = {name: [] for name in sim.cores}
    monkeypatch.setattr(ui_server, "_history", histories)
    monkeypatch.setattr(ui_server, "_history_order", [])
    assert sim.load("pru0", "ldi r1, 1\nldi r2, 2") == []
    assert sim.load("rtu0", "ldi r3, 3") == []

    ui_server._record_step("pru0")
    sim.step("pru0")
    ui_server._record_step("rtu0")
    sim.step("rtu0")
    ui_server._record_step("pru0")
    sim.step("pru0")

    assert sim.cores["pru0"].pc == 2
    assert sim.cores["rtu0"].pc == 1

    assert ui_server._step_back("pru0")
    assert sim.cores["pru0"].pc == 1
    assert sim.cores["rtu0"].pc == 1
    assert [core for core, _ in ui_server._history_order] == ["pru0", "rtu0"]

    # Rewinding the earlier PRU0 instruction also rewinds the later RTU0
    # instruction and discards that event from the shared timeline.
    assert ui_server._step_back("pru0")
    assert sim.cores["pru0"].pc == 0
    assert sim.cores["rtu0"].pc == 0
    assert ui_server._history_order == []
    assert all(not history for history in histories.values())


def test_ui_step_back_restores_xfr_mac_mux_loopback_and_diagnostics(monkeypatch):
    from ui import server as ui_server

    sim = Simulator()
    monkeypatch.setattr(ui_server, "sim", sim)
    mac = sim.cores["pru0"].accelerators[0]
    sim.xfr.xout(10, 0, b"before")
    sim.gpcfg_write("pru0", 0)
    sim.perif_loopback(0, False)
    sim.cores["pru0"].unsupported_xfr.clear()
    before = ui_server._snapshot("pru0")

    sim.xfr.xout(10, 0, b"after!")
    sim.xfr.xfr_shift_en = True
    mac.xout(26, (5).to_bytes(8, "little"))
    mac.xout(25, b"\x01")
    sim.gpcfg_write("pru0", 1)
    sim.perif_loopback(0, True, latency_ns=3.0)
    sim.cores["pru0"].unsupported_xfr[99] = {"count": 1}

    ui_server._restore("pru0", before)

    assert sim.xfr.xin(10, 0, 6) == b"before"
    assert sim.xfr.xfr_shift_en is False
    assert (mac._accumulator, mac.mac_mode, mac.acc_carry) == (0, False, False)
    assert sim.gpcfg_state("pru0")["mux_sel"] == 0
    assert sim.loopback_state()["channels"][0]["enabled"] is False
    assert sim.cores["pru0"].unsupported_xfr == {}


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


def test_default_inputs_and_standalone_attachment_settle_immediately():
    from pru_io.device_model import DeviceBus
    bus = DeviceBus()
    device = bus.attach(_PinDevice(4, value=0))
    assert bus.get_state()["bus"] & (1 << 4) == 0
    assert device.calls
    sim = Simulator()
    assert sim.io("pru0")["gpo_drive_mask"] == 0
    sim.attach_device("pru0", _PinDevice(4, value=1))
    assert _level(sim.cores["pru0"].io_port.gpi, 4) == 1
    assert sim.device_bus.faults() == []


def test_conflict_is_low_sorted_and_one_record_per_episode():
    results = []
    for reverse in (False, True):
        sim = Simulator()
        devices = [_PinDevice(4, value=1, name="high"),
                   _PinDevice(4, value=0, name="low")]
        for device in devices[::-1] if reverse else devices:
            sim.attach_device("pru0", device)
        for cycle in range(10000):
            sim.device_bus.settle(cycle, 0, port="pru0")
        assert _level(sim.cores["pru0"].io_port.gpi, 4) == 0
        assert len(sim.device_bus.contentions) == 1
        results.append(sim.device_bus.contentions)
        snap = sim.device_bus.snapshot()
        sim.device_bus.restore(snap)
        sim.device_bus.settle(10001, 0, port="pru0")
        assert len(sim.device_bus.contentions) == 1
        devices[0].value = 0
        sim.device_bus._last_inputs.clear()
        sim.device_bus.settle(10002, 0, port="pru0")
        devices[0].value = 1
        sim.device_bus._last_inputs.clear()
        sim.device_bus.settle(10003, 0, port="pru0")
        assert len(sim.device_bus.contentions) == 2
        assert len(sim.device_bus.contentions_for_device(devices[0])) == 2
    assert results[0][0] == results[1][0]


def test_generic_direction_lease_detach_and_snapshot_restore():
    sim = Simulator()
    sim.set_gpio_drive_mask("pru0", 1 << 4)
    device = sim.attach_device("pru0", _PinDevice(4, value=1))
    sim.lease_gpio_outputs("pru0", 1 << 4, device)
    assert sim.io("pru0")["gpo_drive_mask"] == 0
    snap = sim.device_bus.snapshot()
    sim.detach_device(device)
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 4
    sim.device_bus.restore(snap)
    assert sim.io("pru0")["gpo_drive_mask"] == 0
    sim.detach_device(device)
    assert sim.io("pru0")["gpo_drive_mask"] == 1 << 4


def test_unrelated_same_pin_conflicts_are_separate_and_filtered():
    sim = Simulator()
    attached = []
    for core in ("pru0", "pru1"):
        sim.set_gpio_drive_mask(core, 1 << 4)
        attached.append(sim.attach_device(core, _PinDevice(4, value=1)))
    assert len(sim.device_bus.contention_records) == 2
    assert len(sim.device_bus.contentions_for_device(attached[0])) == 1
    sim.device_bus.settle(1, 0, port="pru0")
    sim.device_bus.settle(1, 0, port="pru1")
    assert len(sim.device_bus.contention_records) == 2
    records = sim.device_bus.contentions_for_device(attached[0])
    records[0]["drivers"][0]["value"] = 99
    assert all(driver["value"] in (0, 1)
               for record in sim.device_bus.contention_records
               for driver in record["drivers"])


def test_mixed_conflicts_resolve_low_in_both_attach_orders():
    for reverse in (False, True):
        sim = Simulator()
        devices = [_PinDevice(3, OPEN_DRAIN, 0, name="low"),
                   _PinDevice(3, PUSH_PULL, 1, name="high")]
        for device in devices[::-1] if reverse else devices:
            sim.attach_device("pru0", device)
        assert _level(sim.cores["pru0"].io_port.gpi, 3) == 0
        assert len(sim.device_bus.contention_records) == 1
        assert sim.device_bus.contention_records[0]["kind"] == "mixed_open_drain_push_pull"


def test_standalone_detach_clears_conflict_episode():
    from pru_io.device_model import DeviceBus
    bus = DeviceBus()
    bus.attach(_PinDevice(2, value=0, name="low"))
    high = bus.attach(_PinDevice(2, value=1, name="high"))
    assert len(bus.contentions) == 1
    bus.detach(high)
    bus.attach(high)
    assert len(bus.contentions) == 2


def test_reset_one_core_preserves_unrelated_contention_episode():
    sim = Simulator()
    sim.set_gpio_drive_mask("pru1", 1 << 4)
    device = sim.attach_device("pru1", _PinDevice(4, value=1))
    before = sim.device_bus.contentions_for_device(device)
    assert len(before) == 1
    sim.reset("pru0")
    assert sim.device_bus.contentions_for_device(device) == before
    sim.device_bus.settle(1, 0, port="pru1")
    assert sim.device_bus.contentions_for_device(device) == before


def test_registered_bus_requires_endpoint_before_attachment_mutates_state():
    import pytest
    sim = Simulator()
    before = sim.device_bus.snapshot()
    with pytest.raises(ValueError, match="endpoint"):
        sim.device_bus.attach(_PinDevice(4))
    assert sim.device_bus.snapshot() == before


def test_output_lease_enables_transmitter_and_restores_both_directions():
    sim = Simulator()
    sim.set_gpio_drive_mask("pru0", 2)
    device = sim.attach_device("pru0", _PinDevice(5))
    sim.lease_gpio_outputs("pru0", 3, device, drive_mask=1)
    assert sim.io("pru0")["gpo_drive_mask"] == 1
    snap = sim.device_bus.snapshot()
    sim.detach_device(device)
    assert sim.io("pru0")["gpo_drive_mask"] == 2
    sim.device_bus.restore(snap)
    assert sim.io("pru0")["gpo_drive_mask"] == 1
    sim.detach_device(device)
    assert sim.io("pru0")["gpo_drive_mask"] == 2


def test_overlapping_direction_leases_restore_after_last_owner():
    sim = Simulator()
    owners = [object(), object()]
    for owner in owners:
        sim.lease_gpio_outputs("pru0", 1, owner, drive_mask=1)
    sim.lease_gpio_outputs("pru0", 1, owners[0], drive_mask=1)
    sim.device_bus.release_core_outputs(owners[0])
    assert sim.io("pru0")["gpo_drive_mask"] == 1
    sim.device_bus.release_core_outputs(owners[1])
    assert sim.io("pru0")["gpo_drive_mask"] == 0


def test_contradictory_or_invalid_direction_leases_do_not_mutate_state():
    import pytest
    sim = Simulator()
    owner = object()
    sim.lease_gpio_outputs("pru0", 1, owner, drive_mask=1)
    before = sim.device_bus.snapshot()
    for request_owner in (owner, object()):
        with pytest.raises(ValueError, match="direction"):
            sim.lease_gpio_outputs("pru0", 3, request_owner, drive_mask=2)
        assert sim.device_bus.snapshot() == before
    for mask, drive in ((-1, 0), (1 << 20, 0), (1, 2), (1, -1)):
        with pytest.raises(ValueError):
            sim.lease_gpio_outputs("pru0", mask, object(), drive_mask=drive)
        assert sim.device_bus.snapshot() == before
