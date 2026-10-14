"""DeviceModel — one contract for anything wired to the PRU's pins.

WHY
---
The simulator models the PRU well and the *wire* not at all. `IOPort` holds
`gpo`/`gpi` words, and the single external device it supports is bolted on:
`attach_i2c_device()` takes exactly one TCA9538 and hardcodes its SDA/SCL bit
positions. Every other device we have grew its own shape:

    BissCEncoder.next_bit() / .line_at(t_ns)     clock-edge driven, returns a level
    TCA9538Device.step(scl, sda_master) -> sda   pin-level, wired-AND
    Loopback.sample(channel, t_ns)               a wire, not a device

So nothing can be attached generically to a block under test, and adding an
encoder means editing IOPort again.

This module is that missing contract. It deliberately does NOT extend
`perif.peripheral_interface.PeripheralInterface`: despite the name, that class
models the AM243x *on-chip* PERIF SCU — it decodes R30/R31 for a hardware block
inside the PRU subsystem. A device on the far end of a pin is a different thing
and shares none of its register interface.

It does follow this codebase's established convention for attachable models —
`reset()` / `snapshot()` / `restore()` / `get_state()`, as `SigmaDeltaFilter`,
`TCA9538Device` and `Loopback` all do — so the UI's step-back and state views
keep working without special-casing.

ELECTRICAL MODEL
----------------
`tick()` returns what the device *drives*, not the resulting bus level: a
`(drive_mask, drive_values)` pair. The bus resolves them, because with more than
one device on a net only the bus can. Two net types:

    OPEN_DRAIN  a device may only pull low; the net is the wired-AND of every
                driver, with a pull-up supplying the idle high. I2C, 1-Wire,
                I3C, and the SDA half of anything bidirectional.
    PUSH_PULL   one driver at a time. Two devices driving opposite levels is
                contention, and it is recorded as a fault rather than silently
                resolved — that is a real short on real hardware, and a model
                that quietly picks a winner hides a design error.

FAULTS ARE THE POINT
--------------------
A device model that can only produce plausible waveforms is decoration. Its
value is `faults()`: the things it saw that the protocol forbids. A model that
cannot reject a wrong firmware is not an oracle, and every model here ships a
test that drives it with deliberately broken firmware and requires a fault.
"""
from __future__ import annotations

import abc

OPEN_DRAIN = "open_drain"
PUSH_PULL = "push_pull"

_MASK_20 = (1 << 20) - 1


class DeviceModel(abc.ABC):
    """A device on the far end of one or more PRU pins.

    Subclasses see the pins the PRU drives and say what they drive back. What
    `tick()` receives is the PRU's output for THIS cycle resolved against every
    device's drive from the previous one — so a device reacts to the PRU
    immediately, but never to the result of its own drive within the same cycle.
    """

    #: Human-readable, appears in fault text and UI state.
    name: str = "device"

    #: Net type per pin: {pin_index: OPEN_DRAIN | PUSH_PULL}. Pins absent from
    #: this map are inputs to the device only — it observes, never drives them.
    nets: dict[int, str] = {}

    #: Does this device change state on its own, with no pin activity?
    #:
    #: False (default) means purely reactive: it is ticked when a driver
    #: changes the pins, and nothing else. That is right for bus slaves —
    #: I2C, SPI, 1-Wire — and it matters, because ticking an edge-detecting
    #: state machine repeatedly between pin changes feeds it its own output as
    #: fresh input and derails it.
    #:
    #: True means the device also advances with time: an encoder rotating, a
    #: free-running oscillator, a sensor sampling on its own clock. Those get
    #: an additional tick every core cycle.
    time_driven: bool = False

    @abc.abstractmethod
    def tick(self, cycle: int, bus: int) -> tuple[int, int]:
        """Advance one PRU cycle.

        *bus* is the resolved 20-bit pin state as the device sees it: the PRU's
        output for this cycle, combined with every device's drive from the
        previous settle.

        Returns ``(drive_mask, drive_values)``. Bits set in *drive_mask* are
        driven by this device to the corresponding bit of *drive_values*; bits
        clear are released (high-Z). Returning ``(0, 0)`` means "driving
        nothing this cycle", which is the correct idle for an open-drain device.
        """

    def events(self) -> list[dict]:
        """Protocol-level decode: what this device believes it received.

        Each event is a dict with at least ``{"cycle": int, "kind": str}``.
        This is what gets diffed against the independent bus-cosim decoder —
        two implementations agreeing is worth far more than one asserting.
        """
        return []

    def faults(self) -> list[str]:
        """Protocol violations observed. Non-empty means the firmware is wrong
        (or this model is — investigate, do not assume)."""
        return []

    # -- codebase convention: reset / snapshot / restore / get_state ---------

    def reset(self) -> None:
        """Hardware reset. Must clear events and faults."""

    def snapshot(self) -> dict:
        """Full internal state for step-back."""
        return {}

    def restore(self, snap: dict) -> None:
        """Inverse of snapshot()."""

    def get_state(self) -> dict:
        """UI-facing summary. Deliberately coarser than snapshot()."""
        return {"name": self.name, "faults": len(self.faults())}

    def configure(self, parameters: dict) -> dict:
        """Change run-time parameters; return the values now in effect.

        Validation is all-or-nothing: a rejected change leaves the device
        untouched. Devices without run-time parameters keep this default.
        """
        raise ValueError(f"device {self.name!r} has no run-time parameters")


class DeviceBus:
    """Resolves the PRU's outputs against every attached device's drive.

    Attach to one or more IOPorts. Pin changes call `settle()` immediately;
    time-driven models advance through `advance_cycles()` for elapsed core
    cycles. Cross-core GPIO connections share this resolver. GPIO outputs are
    released until callers explicitly select a drive mask. Reactive propagation
    has two phases: sample inputs, resolve changed drives, then sample affected
    devices once more. It does not recursively seek a fixed point. Unchanged
    R30 writes do not retick reactive devices; elapsed cycles (including stalls)
    still advance time-driven devices.
    """

    def __init__(self) -> None:
        self.devices: list[DeviceModel] = []
        self.contentions: list[str] = []
        self.contention_records: list[dict] = []
        self._active_conflicts: dict[object, tuple] = {}
        self._last_drives: dict[int, tuple[int, int]] = {}
        self._device_ports: dict[int, str | None] = {}
        self._last_inputs: dict[int, int] = {}
        self._bus = _MASK_20          # idle: pull-ups high
        self._pru_drive_mask = 0
        self._last_gpo = 0
        self._ports: dict[str, object] = {}
        self._port_devices: dict[str, list[DeviceModel]] = {}
        self._port_net_masks: dict[str, int] = {}
        self._port_net_modes: dict[str, dict[int, set[str]]] = {}
        self._port_gpo: dict[str, int] = {}
        self._core_drive_masks: dict[str, int] = {}
        self._port_buses: dict[str, int] = {}
        self._managed_masks: dict[str, int] = {}
        self._output_leases: dict[tuple[str, int], dict] = {}
        self._lease_owner_pins: dict[int, set[tuple[str, int]]] = {}
        self._lease_owners: dict[int, object] = {}
        self._wires: set[tuple[tuple[str, int], tuple[str, int]]] = set()
        self._last_endpoint: str | None = None
        self._component_finder = None

    @property
    def active(self) -> bool:
        """Whether this bus has work to do on a pin or cycle."""
        return bool(self.devices or self._wires)

    def register_port(self, name: str, port) -> None:
        """Register one PRU GPIO endpoint on this shared bus."""
        self._ports[name] = port
        self._port_devices[name] = []
        self._port_net_masks[name] = 0
        self._port_net_modes[name] = {}
        self._port_gpo[name] = port.gpo & _MASK_20
        self._core_drive_masks[name] = port.gpo_drive_mask & _MASK_20
        self._port_buses[name] = _MASK_20
        self._managed_masks[name] = 0

    def lease_core_outputs(self, core: str, mask: int, owner: object,
                           drive_mask: int = 0) -> None:
        """Lease selected GPIO directions, restoring them after the last owner."""
        if core not in self._ports:
            raise KeyError(f"Unknown GPIO endpoint {core!r}")
        if (isinstance(mask, bool) or not isinstance(mask, int)
                or not 0 <= mask <= _MASK_20):
            raise ValueError("GPIO lease mask must be a 20-bit integer")
        if (not isinstance(drive_mask, int) or drive_mask < 0
                or drive_mask & ~mask):
            raise ValueError("GPIO lease drive mask must be a subset of its mask")
        for pin in range(20):
            bit = 1 << pin
            lease = self._output_leases.get((core, pin))
            if mask & bit and lease is not None:
                if lease["drive_output"] != bool(drive_mask & bit):
                    raise ValueError("GPIO lease direction conflicts with an active owner")
        if not mask:
            return
        owner_id = id(owner)
        held = self._lease_owner_pins.setdefault(owner_id, set())
        self._lease_owners[owner_id] = owner
        current = self._ports[core].gpo_drive_mask & _MASK_20
        changed = False
        for pin in range(20):
            bit = 1 << pin
            key = (core, pin)
            if not mask & bit or key in held:
                continue
            lease = self._output_leases.get(key)
            if lease is None:
                lease = {"was_output": bool(current & bit),
                         "drive_output": bool(drive_mask & bit), "owners": set()}
                self._output_leases[key] = lease
            lease["owners"].add(owner_id)
            held.add(key)
            changed = True
        if changed:
            self.set_core_drive_mask(core, (current & ~mask) | drive_mask,
                                     cycle=self._current_cycle())

    def release_core_outputs(self, owner: object) -> None:
        """Release an owner's GPIO direction leases and restore initial directions."""
        owner_id = id(owner)
        held = self._lease_owner_pins.pop(owner_id, set())
        self._lease_owners.pop(owner_id, None)
        restore_by_core: dict[str, tuple[int, int]] = {}
        for core, pin in held:
            key = (core, pin)
            lease = self._output_leases.get(key)
            if lease is None:
                continue
            lease["owners"].discard(owner_id)
            if lease["owners"]:
                continue
            mask, value = restore_by_core.get(core, (0, 0))
            bit = 1 << pin
            mask |= bit
            if lease["was_output"]:
                value |= bit
            restore_by_core[core] = mask, value
            del self._output_leases[key]
        for core, (mask, value) in restore_by_core.items():
            current = self._ports[core].gpo_drive_mask
            self.set_core_drive_mask(
                core, (current & ~mask) | value, cycle=self._current_cycle())

    def release_all_core_outputs(self) -> None:
        """Release every temporary GPIO direction lease on this bus."""
        for owner in list(self._lease_owners.values()):
            self.release_core_outputs(owner)

    def contentions_for_device(self, device: DeviceModel) -> list[dict]:
        """Return bus contention records involving this exact device instance."""
        device_id = id(device)
        return [
            {**record, "drivers": [dict(driver) for driver in record["drivers"]]}
            for record in self.contention_records
            if any(driver.get("device_id") == device_id
                   for driver in record["drivers"])
        ]

    def faults_for_device(self, device: DeviceModel) -> list[str]:
        """Return model faults and contentions involving this device."""
        out = [f"{device.name}: {fault}" for fault in device.faults()]
        out.extend(record["message"]
                   for record in self.contentions_for_device(device))
        return out

    def attach(self, device: DeviceModel, port: str | None = None,
               cycle: int = 0) -> DeviceModel:
        """Attach a device at one GPIO endpoint (or the stand-alone bus)."""
        if port is None and self._ports:
            raise ValueError("A registered GPIO bus requires an attachment endpoint")
        if port is not None and port not in self._ports:
            raise KeyError(f"Unknown GPIO endpoint {port!r}")
        key = id(device)
        self.devices.append(device)
        self._device_ports[key] = port
        self._last_drives[key] = (0, 0)
        if port is not None:
            self._index_device(device, port, add=True)
            self._refresh_shared(cycle, process_reactive=True,
                                 affected={port})
        else:
            self.settle(cycle, self._last_gpo)
        return device

    def detach(self, device: DeviceModel) -> None:
        """Detach one model and immediately remove its previous pin drives."""
        key = id(device)
        if device not in self.devices:
            self.release_core_outputs(device)
            return
        self.devices.remove(device)
        port = self._device_ports.pop(key, None)
        self._last_drives.pop(key, None)
        self._last_inputs.pop(key, None)
        if port is not None:
            self._index_device(device, port, add=False)
        if self._ports:
            affected = {port} if port is not None else set(self._ports)
            self._refresh_shared(self._current_cycle(), process_reactive=True,
                                 affected=affected)
        elif port is None:
            self._bus = self._resolve_standalone(0, self._last_gpo,
                                                 record_contention=True)
        self.release_core_outputs(device)

    def detach_all(self, port: str | None = None) -> None:
        """Detach models; removed outputs are released before returning."""
        if port is None:
            removed = list(self.devices)
        else:
            removed = [d for d in self.devices if self._device_ports.get(id(d)) == port]
        for device in removed:
            self.detach(device)

    def set_pru_drive_mask(self, mask: int) -> None:
        """Set the stand-alone PRU output-enable mask (inputs are released)."""
        self._pru_drive_mask = mask & _MASK_20

    def set_core_drive_mask(self, core: str, mask: int, cycle: int = 0) -> None:
        """Set which output pins a registered core is actively driving."""
        if core not in self._ports:
            raise KeyError(f"Unknown GPIO endpoint {core!r}")
        mask &= _MASK_20
        self._core_drive_masks[core] = mask
        self._ports[core].gpo_drive_mask = mask
        self._refresh_shared(cycle, process_reactive=True, affected={core})

    def add_gpio_wire(self, core_a: str, pin_a: int,
                      core_b: str, pin_b: int, cycle: int | None = None) -> bool:
        """Connect two core pins to one electrical net without changing GPIO direction."""
        a = self._gpio_node(core_a, pin_a)
        b = self._gpio_node(core_b, pin_b)
        if a == b:
            return False
        wire = tuple(sorted((a, b)))
        if wire in self._wires:
            return False
        self._wires.add(wire)
        self._component_finder = None
        self._refresh_shared(self._current_cycle() if cycle is None else cycle,
                             process_reactive=True,
                             affected={core_a, core_b})
        return True

    def remove_gpio_wire(self, core_a: str, pin_a: int,
                         core_b: str, pin_b: int, cycle: int | None = None) -> bool:
        """Remove a direct pin connection and release its stale bus drive."""
        a = self._gpio_node(core_a, pin_a)
        b = self._gpio_node(core_b, pin_b)
        wire = tuple(sorted((a, b)))
        if wire not in self._wires:
            return False
        self._wires.remove(wire)
        self._component_finder = None
        self._refresh_shared(self._current_cycle() if cycle is None else cycle,
                             process_reactive=True,
                             affected={core_a, core_b})
        return True

    def list_gpio_wires(self) -> list[dict]:
        return [{"core_a": a[0], "pin_a": a[1], "core_b": b[0], "pin_b": b[1]}
                for a, b in sorted(self._wires)]

    def _gpio_node(self, core: str, pin: int) -> tuple[str, int]:
        if core not in self._ports:
            raise KeyError(f"Unknown GPIO endpoint {core!r}")
        if pin < 0 or pin >= 20:
            raise ValueError(f"GPIO pin index {pin} out of range 0-19")
        return core, pin

    def _current_cycle(self) -> int:
        return max((port._device_cycle for port in self._ports.values()), default=0)

    def _index_device(self, device: DeviceModel, port: str, add: bool) -> None:
        devices = self._port_devices[port]
        if add:
            devices.append(device)
        else:
            devices.remove(device)
        masks = 0
        modes: dict[int, set[str]] = {}
        for attached in devices:
            for pin, mode in attached.nets.items():
                masks |= 1 << pin
                modes.setdefault(pin, set()).add(mode)
        self._port_net_masks[port] = masks
        self._port_net_modes[port] = modes

    def settle(self, cycle: int, gpo: int, port: str | None = None) -> int:
        """Settle one pin change and return the resolved 20-bit input word.

        Stand-alone callers explicitly stepping a DeviceBus retain the original
        one-settle/one-device-tick contract. IOPort calls this only when its
        resolved pin state changes, so reactive models do no work on idle cores.
        """
        if port is None and not self._ports:
            self._last_gpo = gpo & _MASK_20
            provisional = self._resolve_standalone(cycle, self._last_gpo,
                                                   record_contention=False)
            drives = []
            for device in self.devices:
                mask, values = device.tick(cycle, provisional)
                drives.append((device, (mask & _MASK_20, values & _MASK_20)))
            self._last_drives = {id(d): drive for d, drive in drives}
            bus = self._resolve_standalone(cycle, self._last_gpo,
                                           record_contention=True)
            self._bus = bus
            return bus

        if port not in self._ports:
            raise KeyError(f"Unknown GPIO endpoint {port!r}")
        self._port_gpo[port] = gpo & _MASK_20
        self._last_endpoint = port
        self._refresh_shared(cycle, process_reactive=True,
                             affected={port} if port is not None else set(self._ports))
        return self._port_buses[port]

    def advance_cycles(self, port: str | None, cycles: int,
                       first_cycle: int = 1) -> None:
        """Advance time-driven models once per elapsed core cycle."""
        if cycles <= 0 or not self.has_time_driven(port):
            return
        for offset in range(cycles):
            cycle = first_cycle + offset
            if self._ports:
                self._tick_time_driven_shared(port, cycle)
            else:
                provisional = self._resolve_standalone(
                    cycle, self._last_gpo, record_contention=False)
                for device in self.devices:
                    if device.time_driven:
                        mask, values = device.tick(cycle, provisional)
                        self._last_drives[id(device)] = (
                            mask & _MASK_20, values & _MASK_20)
                        self._last_inputs[id(device)] = provisional
                self._bus = self._resolve_standalone(
                    cycle, self._last_gpo, record_contention=True)

    def has_time_driven(self, port: str | None = None) -> bool:
        return any(device.time_driven and self._device_ports.get(id(device)) == port
                   for device in self.devices)

    def _tick_time_driven_shared(self, port: str, cycle: int) -> None:
        self._refresh_shared(cycle, process_reactive=False, time_port=port)

    def _resolve_standalone(self, cycle: int, gpo: int,
                            record_contention: bool) -> int:
        levels = _MASK_20
        device_types = {pin: {d.nets[pin] for d in self.devices if pin in d.nets}
                        for pin in range(20)}
        for pin in range(20):
            bit = 1 << pin
            drivers = []
            modes = device_types[pin]
            if self._pru_drive_mask & bit:
                mode = OPEN_DRAIN if OPEN_DRAIN in modes and PUSH_PULL not in modes else PUSH_PULL
                drivers.append(("PRU", (gpo >> pin) & 1, mode,
                                {"type": "core", "name": "PRU"}))
            for device in self.devices:
                mask, values = self._last_drives.get(id(device), (0, 0))
                if mask & bit:
                    drivers.append((device.name, (values >> pin) & 1,
                                    device.nets.get(pin, PUSH_PULL),
                                    {"type": "device", "name": device.name,
                                     "device_id": id(device)}))
            level = self._resolve_pin(cycle, pin, drivers, record_contention)
            if level:
                levels |= bit
            else:
                levels &= ~bit
        return levels

    def _resolve_pin(self, cycle: int, pin: int, drivers: list[tuple],
                     record_contention: bool, net=None) -> int:
        pp_values = {value for _, value, mode, _ in drivers if mode == PUSH_PULL}
        od_low = any(value == 0 and mode == OPEN_DRAIN
                     for _, value, mode, _ in drivers)
        mixed = od_low and 1 in pp_values
        conflict = len(pp_values) > 1 or mixed
        net = pin if net is None else net
        ordered = sorted(drivers, key=lambda d: (d[0], d[1], d[2],
                                                d[3].get("device_id", 0)))
        if record_contention:
            if conflict:
                identity = tuple((name, value, mode, info.get("device_id"))
                                 for name, value, mode, info in ordered)
                if self._active_conflicts.get(net) != identity:
                    kind = "mixed_open_drain_push_pull" if mixed else "push_pull"
                    label = "mixed open-drain/push-pull" if mixed else "push-pull"
                    who = ", ".join(f"{name}={value}" for name, value, _, _ in ordered)
                    message = f"cycle {cycle}: {label} contention on pin {pin} ({who})"
                    self.contentions.append(message)
                    self.contention_records.append({
                        "kind": kind, "cycle": cycle, "pin": pin,
                        "message": message,
                        "drivers": [{**info, "value": value, "mode": mode}
                                    for _, value, mode, info in ordered],
                    })
                self._active_conflicts[net] = identity
            else:
                self._active_conflicts.pop(net, None)
        if conflict or od_low or 0 in pp_values:
            return 0
        return 1

    def _shared_components(self):
        if self._component_finder is not None:
            return self._component_finder
        parent: dict[tuple[str, int], tuple[str, int]] = {}

        def find(node):
            parent.setdefault(node, node)
            if parent[node] != node:
                parent[node] = find(parent[node])
            return parent[node]

        for a, b in self._wires:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra
        self._component_finder = find
        return find

    def _shared_drivers(self, cycle: int):
        find = self._shared_components()
        drivers: dict[tuple[str, int], list[tuple[str, int, str]]] = {}
        declared: dict[tuple[str, int], set[str]] = {}
        for device in self.devices:
            port = self._device_ports.get(id(device))
            for pin, mode in device.nets.items():
                declared.setdefault(find((port, pin)), set()).add(mode)
        for core, port in self._ports.items():
            gpo = port.gpo & _MASK_20
            self._port_gpo[core] = gpo
            mask = self._core_drive_masks[core]
            for pin in range(20):
                bit = 1 << pin
                if not mask & bit:
                    continue
                node = find((core, pin))
                modes = declared.get(node, set())
                mode = OPEN_DRAIN if OPEN_DRAIN in modes and PUSH_PULL not in modes else PUSH_PULL
                drivers.setdefault(node, []).append(
                    (core, (gpo >> pin) & 1, mode,
                     {"type": "core", "name": core}))
        for device in self.devices:
            port = self._device_ports.get(id(device))
            mask, values = self._last_drives.get(id(device), (0, 0))
            for pin in range(20):
                bit = 1 << pin
                if mask & bit:
                    node = find((port, pin))
                    drivers.setdefault(node, []).append(
                        (device.name, (values >> pin) & 1,
                         device.nets.get(pin, PUSH_PULL),
                         {"type": "device", "name": device.name,
                          "device_id": id(device)}))
        return find, drivers

    def _resolve_shared_levels(self, cycle: int, find, drivers,
                               record_contention: bool):
        roots = {find((core, pin)) for core in self._ports for pin in range(20)}
        roots.update(find((self._device_ports.get(id(device)), pin))
                     for device in self.devices for pin in device.nets)
        levels = {}
        for root in roots:
            pin = root[1]
            levels[root] = self._resolve_pin(
                cycle, pin, drivers.get(root, []), record_contention, net=root)
        return find, levels

    def _shared_levels(self, cycle: int, record_contention: bool):
        find, drivers = self._shared_drivers(cycle)
        return self._resolve_shared_levels(cycle, find, drivers,
                                           record_contention), drivers

    def _resolve_unwired_port(self, port: str, cycle: int,
                              record_contention: bool = False) -> int:
        """Resolve one endpoint with bitmasks when no shared net is present."""
        endpoint = self._ports[port]
        gpo = endpoint.gpo & _MASK_20
        self._port_gpo[port] = gpo
        core_mask = endpoint.gpo_drive_mask & _MASK_20
        self._core_drive_masks[port] = core_mask
        device_masks = {id(device): self._last_drives.get(id(device), (0, 0))
                        for device in self._port_devices[port]}
        word = 0
        for pin in range(20):
            bit = 1 << pin
            modes = self._port_net_modes[port].get(pin, set())
            drivers = []
            if core_mask & bit:
                mode = OPEN_DRAIN if OPEN_DRAIN in modes and PUSH_PULL not in modes else PUSH_PULL
                drivers.append((port, (gpo >> pin) & 1, mode,
                                {"type": "core", "name": port}))
            for device in self._port_devices[port]:
                mask, values = device_masks[id(device)]
                if mask & bit:
                    drivers.append((device.name, (values >> pin) & 1,
                                    device.nets.get(pin, PUSH_PULL),
                                    {"type": "device", "name": device.name,
                                     "device_id": id(device)}))
            if self._resolve_pin(cycle, pin, drivers, record_contention,
                                 net=(port, pin)):
                word |= bit
        return word

    def _refresh_unwired_port(self, port: str, cycle: int,
                              process_reactive: bool,
                              time_port: str | None) -> None:
        devices = self._port_devices[port]
        bus = self._resolve_unwired_port(port, cycle)
        if process_reactive:
            self._tick_reactive_port(port, devices, cycle, bus)
            bus = self._resolve_unwired_port(port, cycle)
        if time_port is not None:
            for device in devices:
                if not device.time_driven:
                    continue
                mask, values = device.tick(cycle, bus)
                self._last_drives[id(device)] = (mask & _MASK_20,
                                                 values & _MASK_20)
                self._last_inputs[id(device)] = bus
            bus = self._resolve_unwired_port(port, cycle)
            if any(not device.time_driven for device in devices):
                self._tick_reactive_port(port, devices, cycle, bus)
                bus = self._resolve_unwired_port(port, cycle)
        bus = self._resolve_unwired_port(port, cycle, record_contention=True)
        self._port_buses[port] = bus
        self._bus = bus
        managed = self._port_net_masks[port]
        changed = self._managed_masks[port] | managed
        endpoint = self._ports[port]
        endpoint.gpi = ((endpoint.gpi & ~changed) | (bus & changed)) & _MASK_20
        self._managed_masks[port] = managed

    def _tick_reactive_port(self, port: str, devices: list[DeviceModel],
                            cycle: int, bus: int) -> None:
        for device in devices:
            if device.time_driven:
                continue
            key = id(device)
            if self._last_inputs.get(key) == bus:
                continue
            mask, values = device.tick(cycle, bus)
            self._last_drives[key] = (mask & _MASK_20, values & _MASK_20)
            self._last_inputs[key] = bus

    def _bus_word(self, core: str, find, levels) -> int:
        word = 0
        for pin in range(20):
            if levels[find((core, pin))]:
                word |= 1 << pin
        return word

    def _refresh_shared(self, cycle: int, process_reactive: bool,
                        time_port: str | None = None,
                        affected: set[str] | None = None) -> None:
        if self._ports and not self._wires:
            endpoints = ({time_port} if time_port is not None else
                         (affected if affected is not None else set(self._ports)))
            for port in endpoints:
                self._refresh_unwired_port(port, cycle, process_reactive,
                                           time_port)
            return
        (find, provisional), drivers = self._shared_levels(
            cycle, record_contention=False)
        reactive = any(not device.time_driven for device in self.devices)
        if process_reactive:
            if self._tick_reactive(cycle, find, provisional):
                find, drivers = self._shared_drivers(cycle)
                find, provisional = self._resolve_shared_levels(
                    cycle, find, drivers, record_contention=False)
        if time_port is not None:
            for device in self.devices:
                if not device.time_driven or self._device_ports.get(id(device)) != time_port:
                    continue
                key = id(device)
                bus = self._bus_word(time_port, find, provisional)
                mask, values = device.tick(cycle, bus)
                self._last_drives[key] = (mask & _MASK_20, values & _MASK_20)
                self._last_inputs[key] = bus
            find, drivers = self._shared_drivers(cycle)
            find, provisional = self._resolve_shared_levels(
                cycle, find, drivers, record_contention=False)
            # A timed model may have changed a pin. Reactive models on that
            # net must observe the edge in this same elapsed core cycle.
            if reactive and self._tick_reactive(cycle, find, provisional):
                find, drivers = self._shared_drivers(cycle)
                find, provisional = self._resolve_shared_levels(
                    cycle, find, drivers, record_contention=False)
        find, levels = self._resolve_shared_levels(
            cycle, find, drivers, record_contention=True)
        for core, port in self._ports.items():
            word = self._bus_word(core, find, levels)
            self._port_buses[core] = word
            driven = 0
            for device in self.devices:
                if self._device_ports.get(id(device)) == core:
                    for pin in device.nets:
                        driven |= 1 << pin
            for a, b in self._wires:
                if a[0] == core:
                    driven |= 1 << a[1]
                if b[0] == core:
                    driven |= 1 << b[1]
            changed = self._managed_masks[core] | driven
            port.gpi = ((port.gpi & ~changed) | (word & changed)) & _MASK_20
            self._managed_masks[core] = driven
        endpoint = time_port if time_port is not None else self._last_endpoint
        self._bus = self._port_buses.get(endpoint, _MASK_20)

    def _tick_reactive(self, cycle: int, find, levels) -> bool:
        drives_changed = False
        for device in self.devices:
            if device.time_driven:
                continue
            key = id(device)
            port = self._device_ports.get(key)
            bus = self._bus_word(port, find, levels) if port is not None else _MASK_20
            if self._last_inputs.get(key) == bus:
                continue
            mask, values = device.tick(cycle, bus)
            drive = (mask & _MASK_20, values & _MASK_20)
            drives_changed |= self._last_drives.get(key, (0, 0)) != drive
            self._last_drives[key] = drive
            self._last_inputs[key] = bus
        return drives_changed

    # -- aggregate views -----------------------------------------------------

    def get_device(self, name: str) -> DeviceModel:
        """Return the uniquely attached device with *name*."""
        matches = [device for device in self.devices if device.name == name]
        if not matches:
            raise KeyError(f"No attached device named {name!r}")
        if len(matches) > 1:
            raise ValueError(f"more than one attached device is named {name!r}")
        return matches[0]

    def core_for_device(self, device: DeviceModel) -> str | None:
        """Return the GPIO endpoint that owns *device*, if it is attached."""
        if device not in self.devices:
            return None
        return self._device_ports.get(id(device))

    def events(self) -> list[dict]:
        out = []
        for dev in self.devices:
            for ev in dev.events():
                out.append({**ev, "device": dev.name})
        return sorted(out, key=lambda e: e.get("cycle", 0))

    def faults(self) -> list[str]:
        out = list(self.contentions)
        for dev in self.devices:
            out.extend(f"{dev.name}: {f}" for f in dev.faults())
        return out

    def reset(self) -> None:
        self.contentions.clear()
        self.contention_records.clear()
        self._active_conflicts.clear()
        self._last_inputs.clear()
        for device in self.devices:
            self._last_drives[id(device)] = (0, 0)
        self._bus = _MASK_20
        for dev in self.devices:
            dev.reset()
        if self._ports:
            self._refresh_shared(0, process_reactive=False)
        else:
            self._bus = self._resolve_standalone(0, self._last_gpo,
                                                 record_contention=False)

    def reset_port(self, port: str | None, gpo: int | None = None) -> None:
        """Reset only devices on one core endpoint of a shared bus."""
        if port is None or not self._ports:
            if gpo is not None:
                self._last_gpo = gpo & _MASK_20
            self.reset()
            return
        reset_devices = {id(device) for device in self.devices
                         if self._device_ports.get(id(device)) == port}
        self.contention_records = [
            record for record in self.contention_records
            if not any((driver["type"] == "core" and driver["name"] == port)
                       or driver.get("device_id") in reset_devices
                       for driver in record["drivers"])
        ]
        self.contentions = [record["message"] for record in self.contention_records]
        self._active_conflicts = {
            net: identity for net, identity in self._active_conflicts.items()
            if not any((device_id is None and name == port)
                       or device_id in reset_devices
                       for name, _value, _mode, device_id in identity)
        }
        self._port_gpo[port] = self._ports[port].gpo & _MASK_20
        self._core_drive_masks[port] = self._ports[port].gpo_drive_mask & _MASK_20
        for device in self.devices:
            if self._device_ports.get(id(device)) == port:
                device.reset()
                self._last_drives[id(device)] = (0, 0)
                self._last_inputs.pop(id(device), None)
        self._refresh_shared(0, process_reactive=False, affected={port})

    def snapshot(self) -> dict:
        return {
            "active_conflicts": dict(self._active_conflicts),
            "bus": self._bus,
            "output_leases": {key: {"was_output": lease["was_output"],
                                    "drive_output": lease["drive_output"],
                                    "owners": set(lease["owners"])}
                              for key, lease in self._output_leases.items()},
            "lease_owner_pins": {owner: set(pins)
                                 for owner, pins in self._lease_owner_pins.items()},
            "lease_owners": dict(self._lease_owners),
            "contentions": list(self.contentions),
            "contention_records": [
                {**record, "drivers": [dict(driver) for driver in record["drivers"]]}
                for record in self.contention_records
            ],
            "device_refs": list(self.devices),
            "device_ports": [self._device_ports.get(id(d)) for d in self.devices],
            "devices": [d.snapshot() for d in self.devices],
            "drives": [self._last_drives.get(id(d), (0, 0)) for d in self.devices],
            "inputs": [self._last_inputs.get(id(d)) for d in self.devices],
            "last_gpo": self._last_gpo,
            "pru_drive_mask": self._pru_drive_mask,
            "core_drive_masks": dict(self._core_drive_masks),
            "port_gpo": {core: port.gpo & _MASK_20
                         for core, port in self._ports.items()},
            "port_buses": dict(self._port_buses),
            "managed_masks": dict(self._managed_masks),
            "last_endpoint": self._last_endpoint,
            "port_state": {
                core: {"gpo": port.gpo, "gpi": port.gpi,
                       "gpo_drive_mask": port.gpo_drive_mask,
                       "device_cycle": port._device_cycle}
                for core, port in self._ports.items()
            },
            "wires": self.list_gpio_wires(),
        }

    def restore(self, snap: dict) -> None:
        self._active_conflicts = dict(snap.get("active_conflicts", {}))
        self._bus = snap["bus"]
        self._output_leases = {
            key: {"was_output": lease["was_output"],
                  "drive_output": lease.get("drive_output", False),
                  "owners": set(lease["owners"])}
            for key, lease in snap.get("output_leases", {}).items()
        }
        self._lease_owner_pins = {owner: set(pins)
                                  for owner, pins in snap.get("lease_owner_pins", {}).items()}
        self._lease_owners = dict(snap.get("lease_owners", {}))
        self._last_gpo = snap.get("last_gpo", self._last_gpo)
        self.contentions = list(snap["contentions"])
        self.contention_records = [
            {**record, "drivers": [dict(driver) for driver in record["drivers"]]}
            for record in snap.get("contention_records", [])
        ]
        if "device_refs" in snap:
            self.devices = list(snap["device_refs"])
            self._device_ports = {
                id(device): port
                for device, port in zip(self.devices, snap["device_ports"])
            }
            self._last_drives.clear()
            self._last_inputs.clear()
            for core in self._ports:
                self._port_devices[core] = []
                self._port_net_masks[core] = 0
                self._port_net_modes[core] = {}
            for device in self.devices:
                port = self._device_ports.get(id(device))
                self._last_drives[id(device)] = (0, 0)
                if port is not None:
                    self._index_device(device, port, add=True)
        for dev, ds in zip(self.devices, snap["devices"]):
            dev.restore(ds)
        for dev, drive in zip(self.devices, snap.get("drives", [])):
            self._last_drives[id(dev)] = tuple(drive)
        for dev, bus in zip(self.devices, snap.get("inputs", [])):
            if bus is None:
                self._last_inputs.pop(id(dev), None)
            else:
                self._last_inputs[id(dev)] = bus
        self._pru_drive_mask = snap.get("pru_drive_mask", self._pru_drive_mask)
        self._core_drive_masks.update(snap.get("core_drive_masks", {}))
        self._port_gpo.update(snap.get("port_gpo", {}))
        self._port_buses.update(snap.get("port_buses", {}))
        self._managed_masks.update(snap.get("managed_masks", {}))
        self._last_endpoint = snap.get("last_endpoint", self._last_endpoint)
        if "wires" in snap:
            self._wires = {
                tuple(sorted(((wire["core_a"], wire["pin_a"]),
                              (wire["core_b"], wire["pin_b"]))))
                for wire in snap["wires"]
            }
            self._component_finder = None
        for core, port in self._ports.items():
            state = snap.get("port_state", {}).get(core, {})
            port.gpo = state.get("gpo", self._port_gpo.get(core, port.gpo))
            port.gpo_drive_mask = state.get(
                "gpo_drive_mask",
                self._core_drive_masks.get(core, port.gpo_drive_mask))
            port.gpi = state.get("gpi", port.gpi) & _MASK_20
            port._device_cycle = state.get("device_cycle", port._device_cycle)


    def get_state(self) -> dict:
        return {"bus": self._bus,
                "buses": dict(self._port_buses),
                "drive_masks": dict(self._core_drive_masks),
                "wires": self.list_gpio_wires(),
                "contentions": len(self.contentions),
                "faults": self.faults(),
                "events": self.events(),
                "devices": [
                    {**device.get_state(),
                     "core": self._device_ports.get(id(device))}
                    for device in self.devices
                ]}
