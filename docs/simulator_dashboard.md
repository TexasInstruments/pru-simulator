# Simulator dashboard

The dashboard keeps firmware inspection and external devices in separate views
while using the same simulator session. Open the **Simulator**, **Devices**,
**Motor control** or **Events** view from the workspace toolbar. Switching views does not reset or
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

Devices contains the generic SSI runtime panel; the FOC motor is controlled from
Motor control (below). Attach an encoder and set the position for its next
frame. The SSI form's Preset list (filled from
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

## Motor control

The Motor control button is always in the toolbar (a dot marks that a motor is
attached). The view drives the open-loop V/f example described in
[FOC](foc_open_loop.md); it has no execution loop of its own.

- **Setup.** *Load firmware* attaches the `foc_motor` profile on PRU0 if needed,
  opens and loads `source/foc_open_loop.asm` through the normal Load action and
  stages the reference form (disabled). *Detach motor* removes the model. The
  two SD channel selects apply physical SD input routes (for example GPI3/GPI4
  to sample the PDM current outputs); they are independent of the controller.
- **References.** Speed (rpm), Vd and Vq (per unit of the DC bus, linear limit
  0.577) and acceleration (rpm/s). *Apply* stages the values; the firmware adopts
  them at its next control update. *Start* enables the controller and starts the
  dashboard's ordinary **Run** loop, so breakpoints, the Signal Graph capture and
  step-back keep working; *Stop* stops that loop and clears `enable` (the PWM
  output goes neutral once the firmware runs again). The form follows the backend
  only until you stage values yourself. Start refuses to run when another core
  is selected.
- **Motor physics.** Rs, Ls, flux linkage (Ke), pole pairs, J, B, load torque and
  Vdc, applied live to the running plant; *Defaults* restores the profile
  defaults. Values the model rejects (non-positive resistance, pole pairs
  outside 1 to 64, and so on) show an error and change nothing.
- **Readouts.** Requested, ramped (commanded) and rotor speed; commanded and rotor
  electrical angle and the angle error; duties and alpha/beta from the PWM pins;
  phase and d/q currents; PWM rate; PRU and IEP clocks; the firmware status word
  and handshake; plant simulation time. The ramped speed and the commanded angle
  are derived from the pins (the angle of the measured voltage vector minus the
  Vd/Vq reference angle that Apply sets as a display-only offset); only the rotor
  quantities come from the plant state.
- **Plots.** A rotor dial (commanded needle dashed, rotor needle solid), duty
  cycles, phase currents, the alpha/beta voltage vector, speed and Id/Iq. They
  draw the per-PWM-period samples the server returns for "samples since index N"
  (at most 2,048 kept in the page, 1,024 held by the model); a paused firmware
  produces no new samples and the plots stay as they were.

WebSocket actions (PRU0): `foc_set_reference` (`speed_rpm`, `vd_pu`, `vq_pu`,
`accel_rpm_s`, optional `enable`), `foc_enable`, `foc_set_motor`
(`parameters`), `foc_state` (`since`, answered with a `foc_samples` message) and
`foc_apply` (ABI fields plus the two SD routes). Errors come back with
`tag: "motor"`. Each change clears the step-back history, like the SSI setters;
step-back otherwise restores the plant and its sample ring with the rest of the
simulator state.

The simulator spends most of its time on the device bus: with the motor attached
it runs about 40,000 PRU cycles per second (a 16 kHz period is 15,625 cycles),
so tens of milliseconds of motor time take minutes of wall time. The plots fill
at that pace.

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
