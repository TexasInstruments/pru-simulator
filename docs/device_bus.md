# Generic GPIO device bus

Core and standalone bus drive masks start at zero (released inputs). R30 still
stores GPO values. Select transmitting pins explicitly, for example
`sim.set_gpio_drive_mask("pru0", 3)` for the TCA9538 SCL/SDA pins 0 and 1.
The legacy GPIO path retains its behavior while the generic bus is inactive.
`pru_gpio_drive_mask(core="pru0", mask=None)` queries the mask; supplying a
20-bit mask sets it without changing R30.

An undriven managed net reads high. Contradictory push-pull drives and mixed
open-drain/push-pull conflicts resolve low. Sorted diagnostics record one
unchanged conflict episode; resolution permits a new record on recurrence.
Structured contention records include driver identities, so device-specific
fault queries exclude unrelated devices. Snapshots preserve active episodes.

Attachments settle immediately. Reactive devices sample changes in two phases:
resolve inputs and sample devices, then resolve new drives and resample affected
devices once. This bounded propagation is not a recursive fixed-point solver;
chains needing further propagation wait for another input/time event. Unchanged
R30 writes do not retick reactive devices. Time-driven devices advance on every
elapsed core cycle, including memory stalls.

GPIO direction leases select a mask of owned pins and an optional `drive_mask`
subset of pins to enable as outputs; the default releases all owned pins. For example,
`sim.lease_gpio_outputs("pru0", 3, owner, drive_mask=1)` enables pin 0 and releases
pin 1. They restore initial directions when the last owner detaches. Overlapping
owners must request matching directions; contradictory requests and invalid masks
are rejected before changing state. Repeated identical requests are idempotent. Leases and directions survive snapshot
restoration. Generic device detachment releases stale drives immediately.

A bus with registered core endpoints requires an explicit endpoint for attachment;
missing endpoints are rejected before changing the registry. Standalone buses accept
`port=None`. Resetting one core clears contention records and active episodes involving
its drivers/devices while preserving unrelated peer conflicts. Successful ELF session
replacement releases all temporary direction leases, including nondevice owners.
