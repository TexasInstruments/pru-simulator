# Simulator dashboard

The dashboard keeps firmware inspection, the things wired to the pins and the
motor example in separate views while using the same simulator session. The
workspace toolbar has three tabs: **Simulator**, **I/O & Devices** and **Motor
control**. Switching views does not reset or stop execution. A glider slides
under the active tab (see [Motion](#motion)).

## Simulator

The Simulator view is generic debugging and holds the source and disassembly,
the assembly editor, registers, memory panels, the memory and signal graphs
and an **I/O Pins** panel. I/O Pins is reduced to what every core has: the
GP-mux selector (GPCFG `PRU_GP_MUX_SEL`), the R30 output grid, the R31 input
grid and the GPIO loopback strip. The R30/R31 grids stay visible in every mux
mode; in SD or Peripheral Interface mode a note next to the selector points to
**I/O & Devices > Peripherals** for the controls of that block.

A core memory fault (for example an access to an unmapped address, which halts the core) is
shown in a red **Core fault** band above the panels, with the core, opcode,
address and message; the same fault is listed in **I/O & Devices > Events**.

Use the Panels buttons to hide panels you do not need; remaining panels
reclaim the space. The row wraps onto more lines instead of scrolling, and the
Telemetry strip wraps too, so nothing is clipped at 1700, 1280 or 900 px.
Drag the panel headers to rearrange them and the dividers to resize them.
Visibility and layout are remembered separately for single-core and multicore
modes. At least one panel stays visible. Reset Layout restores the current
mode's panels and default sizes. The panel ids and the layout version did not
change, so layouts saved before the reorganisation load as they were.

Choose PRU0, PRU1, RTU0 or RTU1 in single-core mode. Multicore mode displays PRU0
and a selected partner. The I/O & Devices view follows the selected core, with
an explicit device-core selector in multicore mode.

**Three or four cores.** In multicore mode the **+RTU0 / +PRU1 / +RTU1** toggles
next to the partner selector add a third and a fourth core (the partner's own
toggle is pressed and locked). Each shown core gets a compact source panel
(PC badge, breakpoints, double-click to toggle) and a register panel; three
cores fill two rows (two over one), four cores a 2x2 grid, with their own saved
layouts. Step, SIM, Reset, Run (`run_multicore` with a `partners` list, paced by
core time) and the Space/Arrow shortcuts act on every shown core, and a
breakpoint on any of them stops Run. Header counters, the I/O pins and the SPAD
columns still follow PRU0 (and the counters the partner); the device-core
selector offers PRU0 and the partner only. With no extra core toggled the view
is the original two-core one.

**Run and Stop.** Run waits for each instruction chunk to finish before sending
another, including all core states in multicore mode. Stop prevents further
chunks; the one already executing can finish. This keeps slow device models
from building a queue of work after Stop. When updating the dashboard, restart
the Python server and reload the tab so the frontend and backend use the same
protocol. A tab reload alone keeps simulator state in the server; a server
restart creates a new session.

**IEP counter clock.** The header offers 200, 225, 250, 300 and 333 MHz
shortcuts, a custom finite positive MHz rate (including fractions), and a
**configured rate** reset. The visible clock status identifies the configured
rate, external session rate, and active rate/source. `set_iep_clock` changes
only the external source; `mhz: null` clears the session override and returns to
the backend's configured rate. Clock-only changes preserve timer counters and
elapsed time; a sub-tick fraction is dropped when the active rate changes.
Firmware's IEPCLK bit 0 selects the core clock independently. Session overrides
survive core-clock reload and step-back; saving a valid config clears them.
Reset uses the existing session semantics (hardware reset clears the source
bit, retaining the external session rate). 250 MHz is a chosen software
convention for core clocks; configured IEP rates can be independent. The current
parent still defaults omitted IEP rates to 200 MHz, contrary to the accepted
250 MHz/core-inheritance policy; configured reset follows the backend and will
inherit its eventual correction.

## I/O & Devices

"I/O & Devices" is only a container: it has no content of its own and opens on
its **Devices** sub-tab. The sub-tabs follow the WAI-ARIA tabs pattern (Left,
Right, Home and End move between them; the selected one is in the tab order).
The chosen view and sub-tab are remembered in the browser; views saved before
this layout (`devices`, `events`) open the matching sub-tab.

A badge on the tab counts **new faults**: device protocol faults, bus
contention and core memory faults you have not yet seen. It carries the text
"N new faults" for assistive technology (it is not colour only) and clears when
you open Events. Ordinary events never count.

### Devices: things plugged on the pins

Cards use the protocol names. Each attaches through the generic
`device_attach` action (the same profiles as the MCP device API).

- **SSI encoder.** Attach an encoder and set the position for its next
  frame. The Preset list (filled from the server's device discovery) offers the
  twelve frames in the [SSI model](ssi_device_model.md) plus Custom; choosing
  one fills the resolution, error bits and encoding, which you can still edit.
  An encoder with error bits also shows a "Set next error" field, applied like
  the position to the next frame, and its frame events list the decoded error.
  The page sends numbers, so position and error values are limited to 2^53 - 1;
  use the MCP tools for wider values. The panel does not write the SSI reader's
  shared-memory config block; load and configure the reader firmware yourself.
  The FOC example produces 16 kHz PWM at the default 200 MHz IEP clock; the PRU
  default remains 250 MHz. See [SSI](ssi_device_model.md) for the explicit
  manual 300 MHz option and [FOC](foc_open_loop.md) for the firmware and ABI.
- **I2C expander (TCA9538).** Name, 7-bit address (`0x23` or `35`), SCL and SDA
  pins (both open-drain; defaults 0 and 1), Attach and Detach. The card shows
  the eight output pins as boxes with a 0/1 digit (an input-configured pin
  reads 0, since the model is write-only and has no Input Port register), the
  OUTPUT, POLARITY and CONFIG registers, the number of bus events and faults,
  the protocol state and the last transaction. It is a `tca9538` profile device,
  so its faults also reach the badge and Events.
- **UART.** The decoder and the RX inject controls, moved here unchanged. The
  decoder works on the GPO0 trace recorded by the Signal Graph, so capture with
  the Signal Graph REC button in the Simulator view first; inject sends frames
  onto GPI0-3 with the same `uart_inject` action as before.

Below the cards **Attached devices** lists every device on the selected core,
not only SSI encoders: name, core, model or profile, wiring and the latest
fault, with Detach. SSI rows keep the position and error setters; the FOC motor
row has an "Open Motor control" button. A refused attach (a bad address, a
duplicate name, or a pin conflict, see below) is shown in the card's alert.

**Legacy I2C attach.** The old *Attach TCA9538* button of the I/O Pins panel is
gone. The `i2c_attach` WebSocket action and the `pru_i2c_attach` MCP tool still
work but are **deprecated**: they attach a separate legacy model on pins 0/1
that is not part of the device bus. Use the TCA9538 card, `device_attach` with
profile `tca9538`, or `pru_device_attach`. If the legacy slot is attached (for
example from MCP) and a START was seen, its LEDs are still drawn in a "legacy
attach" block under the cards. The two paths cannot share pins 0 and 1: a
`tca9538` device on those pins is refused while the legacy slot is attached,
and the legacy attach is refused while a device uses pin 0 or 1, each with an
error message.

### Peripherals: blocks inside the chip

The **Sigma-delta (SD) filter** and **Peripheral Interface** cards moved here
from the I/O Pins panel. Each is live only when the GP mux selects it. Otherwise
the card stays visible with an "Inactive in this mux mode" notice and a link
that writes the GPCFG mux (the same action as the selector in I/O Pins).

### Events

Device frame/fault events, bus contention messages and the memory faults of the
visible cores. Event cycles belong to the named device's core, so unequal-clock
cores do not share a cycle scale. The view reflects current simulator state,
including reset and step-back, rather than retaining a separate history that
could disagree with it. The display includes up to 500 events and 100 fault
messages.

## Motor control

The Motor control tab is always in the toolbar (a dot marks that a motor is
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

## Look and themes

The default **Dark** theme is the red-brand operator workbench: raised and inset
surfaces, a red accent (`--accent`, `--brand-red`), outlined uppercase toolbar
buttons with a filled red **Run**, and panels with a red bar before an uppercase
title. The header is split into labelled groups (Target, Execute, Session,
Status, Theme), a segmented view switcher (Simulator, I/O & Devices, Motor
control), a **Panels** row of icon toggle buttons, and a Telemetry strip. Below
about 1760 px the groups flow and wrap instead of sharing fixed columns. UI text
uses the `Segoe UI Variable` / `Inter` / system sans stack and code, registers
and memory use `Cascadia Code` / `Consolas`; no web fonts are loaded, so the page
works offline.

**High contrast** keeps the same layout on a black background with white text
and brighter borders. Both themes are sets of CSS custom properties at the top
of `ui/static/index.html` (`:root` for Dark, `:root[data-theme="contrast"]` for
High contrast), and `tests/test_ui_design_tokens.py` checks that High contrast
defines every token and that the main text/background pairs meet WCAG AA (4.5:1).
The signal graph, memory graph, memory-fill preview and Motor control plots read
their colours from the `--graph-*` tokens when they draw and redraw when the
theme changes.

Dark and High contrast themes, view selection, the I/O & Devices sub-tab and
panel visibility are local browser preferences. Navigation and panel toggles
are keyboard-accessible buttons with visible focus and pressed state. On narrow screens, the simulator
columns stack and the device forms flow into one column.

This dashboard implements general navigation, panel controls and device event
inspection on the new generic runtime surface. It does not revive the original
branch's protocol-specific run shortcuts or mailbox-coupled model controls.

## Motion

Two small animations are CSS only (no JavaScript animation loops, no layout
shift) and are switched off when the browser asks for reduced motion.

- **Tab glider.** One highlight slides under the active tab of the top
  workspace tabs and of the I/O & Devices sub-tabs. A small script reads the
  active tab's offset and width and sets `--glider-x` and `--glider-w`, so it
  works for any number of tabs and for the wider tab that carries the fault
  badge. It does not animate on first paint (the transition is armed after the
  first frame, and a hidden strip is measured when it is first shown), and
  reduced motion moves it instantly. In forced-colours (Windows high contrast)
  mode the active tab is marked by an outline and underline instead of shadows.
- **Button wipe.** Hovering a neutral text button (the toolbar's Multi-core, Step, SIM,
  Config, Reset Layout, Help, and the panel, memory, breakpoint, device, motor
  and dialog buttons) sweeps two skewed `--wipe` coloured shapes across
  the pill while the label inverts (`mix-blend-mode: difference`) and rolls
  once; the label ends as dark text on `--wipe`. The filled and danger buttons
  (Run/Stop, Reset, HW Reset, Stop SIM, and `.primary` buttons, which also carry
  `.btn-run`) use a plain colour swap (red text on
  white) because the blend would turn their white label cyan. The wipe runs only
  where hover is available (`(hover: hover) and (pointer: fine)`), never on
  disabled buttons, and not at all under reduced motion, where hover only
  changes the border. Tabs, toggles (SPAD, memory format, loopback, pins, panel
  toggles, REC) and icon-only buttons keep their own hover. Only the toolbar
  buttons are pills; other buttons keep their size. Labels sit in `.text-container > .text`; code that
  changes a button's text must use `setButtonLabel(button, text)`
  (`ui/static/chrome.js`), never `button.textContent`, which would remove the
  label wrapper; it also builds the wrapper for a `.btn-17` button created in script. `--wipe` is defined in both themes, and
  `tests/test_ui_design_tokens.py` checks the label's contrast before and at the
  end of the wipe.


## Target cores and GPIO directions

Every core selector, multi-core partner/extra, load target, and device target
follows `available_cores` from the simulator. AM263x offers PRU0, RTU0 and PRU1,
with no RTU1. Reloading a target returns unsupported selections to an available
core; invalid WebSocket core requests report an error and keep the session.

The I/O panel separates R30 values from GPIO output enable. Each pin's
**Drive / Release** button switches its direction without changing R30. Device
leases reserve their pins' directions; attempting to change them shows an
inline error and preserves the mask/R30 until that device is detached. In the
multi-core view these GPIO controls operate on the displayed PRU0 pins.

The WebSocket `set_gpio_drive_mask` action accepts either a 20-bit integer
`mask` to replace all directions or an integer `pin` from 0 to 19 to toggle
that pin against the current server mask. Supplying both is rejected. The
dashboard sends pin intents, so clicks made before state feedback accumulate
in arrival order; two clicks on the same pin cancel. Both forms enforce device
direction leases and leave R30 unchanged. Errors return the `gpio` tag followed
by current state.

FOC speed and acceleration conversions, clean staged fields, and requested
RPM readouts use the active IEP rate divided by the firmware's fixed 12500-tick
period. User edits remain intact during clock updates.
