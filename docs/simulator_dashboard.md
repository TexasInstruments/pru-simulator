# Simulator dashboard

The dashboard keeps firmware inspection and external devices in separate views
while using the same simulator session. Open the **Simulator**, **Devices**, or
**Events** view from the workspace toolbar. Switching views does not reset or
stop execution.

The Simulator view retains the source, editor, register, memory, GPIO and graph
panels. Use the Panels buttons to hide panels you do not need; remaining panels
reclaim the space. Drag the panel headers to rearrange them and the dividers to
resize them. Visibility and layout are remembered separately for single-core
and multicore modes. At least one panel stays visible. Reset Layout restores
the current mode's panels and default sizes.

Choose PRU0, PRU1, RTU0 or RTU1 in single-core mode. Multicore mode displays PRU0
and a selected partner. The Devices view follows the selected core, with an
explicit device-core selector in multicore mode. SSI models may attach to that
core; the FOC motor remains restricted to PRU0.

Devices contains the generic SSI and FOC runtime panels. Attach an encoder and
set the position for its next frame, or attach a motor and apply its control
configuration and physical SD routes. The SSI form's Preset list (filled from
the server's device discovery) offers the twelve frames in the
[SSI model](ssi_device_model.md) plus Custom; choosing one fills the
resolution, error bits and encoding, which you can still edit. An encoder with
error bits also shows a "Set next error" field, applied like the position to
the next frame, and its frame events list the decoded error. The page sends
numbers, so position and error values are limited to 2^53 - 1; use the MCP
tools for wider values. The Devices view does not write the SSI reader's
shared-memory config block; load and configure the reader firmware yourself.
The FOC example produces 16 kHz PWM at
the default 200 MHz IEP clock; the PRU default remains 250 MHz. See
[SSI](ssi_device_model.md) for the explicit manual 300 MHz option and
[FOC](foc_open_loop.md) for the firmware and ABI.

Events shows the attached devices' frame/fault events and bus contention
messages, plus memory faults from the current core state. Event cycles belong
to the named device's core, so unequal-clock cores do not share a cycle scale.
The view reflects current simulator state, including reset and step-back,
rather than retaining a separate history that could disagree with it. The
display includes up to 500 events and 100 fault messages.

Dark and High contrast themes, view selection and panel visibility are local
browser preferences. Navigation and panel toggles are keyboard-accessible
buttons with visible focus and pressed state. On narrow screens, the simulator
columns stack and the device forms flow into one column.

This dashboard implements general navigation, panel controls and device event
inspection on the new generic runtime surface. It does not revive the original
branch's protocol-specific run shortcuts or mailbox-coupled model controls.
