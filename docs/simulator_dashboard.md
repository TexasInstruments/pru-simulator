# Simulator dashboard

The dashboard keeps firmware inspection and the things wired to the pins
in separate views while using the same simulator session. The workspace
toolbar has two tabs: **Simulator** and **I/O & Devices**. Switching views does not reset or stop execution. A glider slides
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
The same eight panels serve every core selection: Source / Disassembly,
Registers, Assembly Editor, two Memory panels, I/O Pins, Signal Graph and
Memory Graph. Selecting more cores never adds tiles or changes the saved tile
tree. At least one panel stays visible. Reset Layout restores the default
panels and sizes, expands minimized panels, and returns to PRU0 used and shown
with the deck unpinned. It does not rewrite clocks or reload the simulator.
Saved layouts are checked before rendering. Empty trees, unknown or duplicated
panels, missing panels, and invalid split directions or sizes restore the
default layout. The panel-set migration clears old duplicated-core tile trees;
they do not decide which cores run. Execution controls wrap within their
toolbar group so Step, Run/Stop and SIM remain reachable.

### CORES: execution and inspection

Hover **CORES** in Target to open its overlay, or focus the pill with the
keyboard. Clicking the pill or the pin button pins it open. The padded hover
bridge keeps it open while moving into the deck; Escape closes and unpins it,
and clicking outside also closes it. Deck controls are buttons with pressed
states and visible keyboard focus. The cards fan out with a short stagger;
reduced-motion preferences disable the fan, pulse and last-core shake.

**Use** selects the cores that participate in Run, Step and SIM. **Show** gives
a used core a pane in Source and Registers; hiding it does not stop that core
running. A newly used core stays hidden until Show is selected. An unused core has no Show
control. At least one core stays used and one stays shown. Un-using a core also
hides it, selecting the first remaining used core if necessary. **Use all**
enables all available cores without adding panes; **Show all used** reveals their panes.
Clicking a used card's name shows only that core. The collapsed pill shows
full-name chips for shown cores at wide widths (abbreviated below 1840 px), dots for used hidden cores, rings for unused cores,
and a SINGLE or MULTI count.

With one shown core, Source and Registers have no extra pane heading. With two
or more, Source splits inside its existing tile: two side by side, three as two
above one, and four as a 2×2 grid. Registers has one column per shown core.
Each pane has its core colour, PC or Carry readout, and an **×** to hide that
core in both panels; hiding the last shown core does nothing. Panel headers
show non-interactive core chips. Live state patches values in the existing
panes and deck rather than replacing their controls on every tick.

Telemetry buttons list used cores and choose the **focus core**. Choosing a
hidden used core also shows it. If focus is hidden or un-used, focus moves to
the first shown core. The shared breakpoint row, I/O Pins, SPAD and constant-table inspection follow
focus. Selecting a core for inspection also selects its Assembly Editor files.
Double-clicking a source line toggles a breakpoint for that pane's own core;
Space toggles the focused core's current-PC breakpoint. Arrow Right steps the
used set. Each multi-core Step or SIM tick saves one shared undo point and
records one signal sample when any selected core advances. Arrow Left restores
that shared point in one operation. A breakpoint on any
used core stops Run, including a used core whose panes are hidden. I/O & Devices
also retains an explicit device target drawn from available cores.

Step-back cannot restore XFRDMA queues, XFR2VBUS transfers and external VBUS
memory, or task-ring queues and pending work. Reset and rerun programs that
use those accelerators instead of using undo to validate their results.

A new backend starts with PRU0 selected for execution. Each connected view
uses the backend execution-core list. Browser preferences retain shown cores,
focus and pin state beside the panel layout. Saved selections are pruned to
the shared execution cores and the target's available cores; invalid or empty
selections recover to a nonempty used/shown set. Explicit legacy multicore selections can migrate
to used/shown; an old multicore tile tree alone does not enable cores. The
first-use CORES hint hides while the deck is open and is dismissed permanently
by **Got it** or a deck interaction. Reset Layout preserves that dismissal.

### Framebuffer panel

The **Framebuffer** panel draws a region of emulated memory as a picture, so
firmware can "paint" a small screen in RAM and you can watch it. It is an
ordinary memory read: nothing special happens on the bus. The panel is hidden
by default; turn it on with the **Framebuffer** button in the panel toggle bar
(Reset Layout hides it again).

- **Auto (header)** is on by default. The panel reads a 32-byte `FBUF` header
  at the base address (default `0x11000`, Shared RAM) and takes the size,
  format, stride and colors from it. It only re-reads the pixels when the
  header's frame counter changes, so a finished frame is drawn once.
  The status line shows e.g. `FBUF 32x32 RGB565 - frame 12`.
- **Manual**: with Auto off, or when no header is found at the base address
  (the status line says `No FBUF header at 0x11000 - manual 32x32 MONO1`), the
  manual controls appear: preset (Tiny, Mono, Wide, Indexed, Color, Large),
  width, height, format (MONO1, INDEX8, RGB565), stride and the two MONO1
  colors. Pixels start at the base address and are re-read about every 100 ms.
- **Base** is a global bus address, the same as in the Memory panels. PRU1 sees
  DRAM1 at its local `0x0`, which is global `0x2000`.
- **Grid** draws pixel borders when each pixel is at least 4 screen pixels wide.
  The picture is scaled by whole numbers only, so pixels stay crisp.
- **Big view** opens the picture full screen (for a projector) with the same
  live updates. Close it with Esc, the Close button, or a click outside the
  picture.

Memory is only read while the panel (or big view) is visible. Settings are kept
per browser tab in `localStorage`. The byte-level format is in
[framebuffer.md](framebuffer.md).

### Core frequencies

One frequency applies to PRU0 + RTU0 (`pru_clock_mhz`). The other applies
to PRU1 + RTU1 (`pru1_clock_mhz`). Unsupported cores are omitted on targets
such as AM263x. The controls list the affected cores, including hidden and
unused cores. These pairings describe the current simulator implementation;
they do not define hardware clock domains and may change in the future.
Do not label them as slices or ICSSG instances. The API retains its existing
`group` identifiers and configuration keys for compatibility.

The presets are **200, 225, 250, 300 and 333 MHz**, supplied by
`ui/server.py:ALLOWED_CLOCK_MHZ`. These are dashboard choices, not hardware
limits. Configured finite positive custom rates, including fractions, are
displayed unchanged; the arrows move to the next lower or higher preset.
Omitting `pru_clock_mhz` uses the 200 MHz fallback; omitting
`pru1_clock_mhz` inherits `pru_clock_mhz`. Editing `pru_clock_mhz` pins the old effective `pru1_clock_mhz`
rate when necessary to preserve it. No independent RTU clock key is introduced.

The deck explains before an edit that changing a group clock recreates the
simulator and clears loaded programs. **Reload programs after editing.** This
also resets registers, devices, step-back history and firmware's IEP source
selection, then refreshes the dashboard. Steppers are disabled during Run/SIM,
while an execution chunk or clock change is pending, or when no group member is
used. A group edit preserves the other group's effective rate, the configured
IEP rate and the external IEP session override. Layout restoration and Reset
Layout never write either clock group.

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
bit, retaining the external session rate). An omitted core-clock rate uses
200 MHz. An omitted external IEP rate defaults to 200 MHz,
as documented in the AM64x/AM243x TRM. Core and external IEP rates remain
independently configurable. The configured-rate reset follows these backend
defaults; it does not force the IEP to match the core.

### Assembly Editor: files and load target

Use **Load into** in the Assembly Editor to choose PRU0, PRU1 or an available
RTU. Each core has its own open files, unsaved text, active file and project.
Changing this selector does not enable the core for execution or move the
Source/Registers inspector. Use **CORES → Use** when that core must run.
Selecting a core through Telemetry or clicking its name in CORES also opens
that core's editor. The Load into value always names the target for
**Load & Assemble** and imported ELF files.

Closing a file with **×** closes it only in the selected core's editor. Other
cores keep their files and unsaved text, even when the file names are equal.
Closing an editor file does not unload firmware from any core. Save and load
operations stay bound to the core and text selected when clicked, even if you
switch cores while a save or file read is pending. Saved source files on the
server still share their paths; saving a file changes that disk file, while
other open editor copies and loaded firmware stay unchanged. Editor drafts
are local to each browser tab and are not retained after a page reload.

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
  use the MCP tools for wider values. **Attach encoder** only attaches a model;
  the two example actions also load the firmware, configure its shared-memory
  ABI and set GPIO directions. See [SSI](ssi_device_model.md) for the explicit
  manual 300 MHz option and the firmware and ABI.
- **I2C expander (TCA9538).** Name, 7-bit address (`0x23` or `35`), SCL and SDA
  pins (both open-drain; defaults 0 and 1), Attach and Detach. The card shows
  the eight output pins as boxes with a 0/1 digit (an input-configured pin
  reads 0, since the model is write-only and has no Input Port register), the
  OUTPUT, POLARITY and CONFIG registers, the number of bus events and faults,
  the protocol state and the last transaction. It is a `tca9538` profile device,
  so its faults also reach the badge and Events.
- **UART.** Click **Record** next to Decode, run the firmware, stop recording,
  then click **Decode** to read the GPO0 trace. UART Record and Signal Graph REC
  share the same capture buffer and recording state. RX Inject sends frames
  onto GPI0-3. **Reset inputs** restores its form defaults and clears injection
  feedback. It does not reset the core or cancel an already armed injection.

### SSI demos in the dashboard

Start the dashboard and open **I/O & Devices → Devices → SSI encoder**.
Restart Python and reload the page after updating this branch. Example loads
replace the session's programs, devices and GPIO wires; they keep core clock
rates and the selected external IEP rate. They reset hardware registers and
the step-back history. Loading prepares the example; **Run** starts it.

Choose the frame length, position, encoding and optional error field first.
For a 20-bit test, select **Custom**, set **Frame length** to **20**,
**Position** to **703710** (0xABCDE), **Error bits** and **Error value** to **0**,
and **Encoding** to **Binary**. Then use either setup button below. Both
configure the reader, encoder and frame timing together; no memory edits are
needed. Changing a form value takes effect when you press a setup button again.

1. Choose **Set up reader + encoder model**, then **Run**. PRU0 runs the ordinary SSI
   reader against the attached encoder model, using clock GPO0 and data GPI16.
   **Received position** should match the chosen position and **Received frames**
   should increase. Enter **1234** in **Next position (decimal)** and press
   **Set next position**. Requested position changes immediately; received
   position changes only when the reader publishes a later frame.
2. Choose **Set up reader + encoder PRU**, then **Run**. The dashboard selects only
   PRU0 and PRU1 as used and shown. PRU0 runs the reader; PRU1 runs encoder
   firmware. The setup wires PRU0 GPO0 to PRU1 GPI0 (clock), and PRU1 GPO16 to
   PRU0 GPI16 (data). The same received values and position setter work here.
   No host encoder model is attached in this mode.
3. Press **Show waveforms** to inspect the real clock and data pins in the
   Simulator Signal Graph. SSI examples enable recording at a two-instruction
   stride; the multicore graph includes both ends of each wire. Return to
   **I/O & Devices → Devices** for the decoded readouts. **Stop** stops sending
   execution chunks; the frame count should then stay still.

The initial form uses a custom 12-bit binary frame at position **2748 (0xABC)**,
without an error field. A preset fills its frame length, error bit count and
encoding, and supplies its clock limit and timeout. The frame length includes
both position bits and error bits. With no error bits, **Encoder error field**
reads **Not present**. Firmware errors are reported separately by the status text. A partly written
mailbox displays **Publishing a frame**, with received values hidden until
publication completes. To start over, load the example again. After changing
IEP rate, reload the encoder firmware example to recalculate its timeout; a
hardware reset disables that timer and the status asks for the same reload.

**Advanced: attach a device to your own reader program** keeps the device-only
workflow. **Attach device only** uses the frame settings above and the advanced
name and pin fields for the selected core. It does not load or change a reader
program. Use this only when your program already has matching frame length,
pins and timing. The two complete setup buttons always use pins 0 and 16.

For a common demonstration rate, choose **250 MHz** in the IEP selector before
loading. This is our software convention, chosen to match a selected
250 MHz demonstration core clock; it is not a universal hardware default. The examples do
not force 200 or 250 MHz. A user may choose any finite positive IEP rate,
including fractional rates, and a separate core rate. This example setting
does not change the omitted external IEP default of 200 MHz.

Below the cards **Attached devices** lists every device on the selected core,
not only SSI encoders: name, core, model or profile, wiring and the latest
fault, with Detach. SSI rows keep the position and error setters. A refused attach (a bad address, a
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

## Look and themes

The default **Dark** theme is the red-brand operator workbench: raised and inset
surfaces, a red accent (`--accent`, `--brand-red`), outlined uppercase toolbar
buttons with a filled red **Run**, and panels with a red bar before an uppercase
title. At full-screen width, the two toolbar rows distribute their space independently: Execute and Session fit their contents, CORES and IEP stay side by side, and the telemetry core buttons precede readable 12 px counters on one line. Telemetry receives its content width from the panel bar; when that bar becomes constrained, decorative panel icons yield before panel names. The separate Telemetry label and duplicate focus name are omitted.

The header contains the compact PRU SIM/CORES/IEP group, Execute, Session,
and combined Status/Theme controls, a segmented view switcher (Simulator, I/O & Devices), a **Panels** row of icon toggle buttons, and a Telemetry strip. Below
about 1760 px the groups flow and wrap instead of sharing fixed columns. UI text
uses the `Segoe UI Variable` / `Inter` / system sans stack and code, registers
and memory use `Cascadia Code` / `Consolas`; no web fonts are loaded, so the page
works offline.

**High contrast** keeps the same layout on a black background with white text
and brighter borders. Both themes are sets of CSS custom properties at the top
of `ui/static/index.html` (`:root` for Dark, `:root[data-theme="contrast"]` for
High contrast), and `tests/test_ui_design_tokens.py` checks that High contrast
defines every token and that the main text/background pairs meet WCAG AA (4.5:1).
The signal graph, memory graph, memory-fill preview read
their colours from the `--graph-*` tokens when they draw and redraw when the
theme changes.

The round half-filled-circle icon switches between Dark and High contrast; its tooltip names the next theme. The IEP rate and SIM seconds field use the same rounded pill surface and border as the toolbar buttons. The Target label and persistent clock summary are removed; active/configured IEP details remain in the rate selector tooltip, and group rates remain in the CORES deck.

Dark and High contrast themes, view selection, the I/O & Devices sub-tab and
panel visibility are local browser preferences. Navigation and panel toggles
are keyboard-accessible buttons with visible focus and pressed state. On narrow screens, the simulator
columns stack and the device forms flow into one column.

This dashboard implements general navigation, panel controls and device event
inspection on the new generic runtime surface. It does not revive the original
branch's protocol-specific run shortcuts or mailbox-coupled model controls.

## Motion

The tab and button animations are CSS only (no JavaScript animation loops, no
layout shift) and are switched off when the browser asks for reduced motion.
The CORES deck also respects that preference for its card fan, status/coaching
pulses and last-used-core feedback.

- **Tab glider.** One highlight slides under the active tab of the top
  workspace tabs and of the I/O & Devices sub-tabs. A small script reads the
  active tab's offset and width and sets `--glider-x` and `--glider-w`, so it
  works for any number of tabs and for the wider tab that carries the fault
  badge. It does not animate on first paint (the transition is armed after the
  first frame, and a hidden strip is measured when it is first shown), and
  reduced motion moves it instantly. In forced-colours (Windows high contrast)
  mode the active tab is marked by an outline and underline instead of shadows.
- **Button wipe.** Hovering a neutral text button (the toolbar's Step, SIM,
  Config, Reset Layout, Help, and the panel, memory, breakpoint, device,
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

The CORES deck, telemetry, load target, and device target follow
`available_cores` from the simulator. AM263x offers PRU0, RTU0 and PRU1,
with no RTU1. Reloading a target returns unsupported selections to an available
core; invalid WebSocket core requests report an error and keep the session.

The I/O panel separates R30 values from GPIO output enable. Each pin's
**Drive / Release** button switches its direction without changing R30. Device
leases reserve their pins' directions; attempting to change them shows an
inline error and preserves the mask/R30 until that device is detached. In the
multi-core view these GPIO controls operate on the focus core's pins.

The WebSocket `set_gpio_drive_mask` action accepts either a 20-bit integer
`mask` to replace all directions or an integer `pin` from 0 to 19 to toggle
that pin against the current server mask. Supplying both is rejected. The
dashboard sends pin intents, so clicks made before state feedback accumulate
in arrival order; two clicks on the same pin cancel. Both forms enforce device
direction leases and leave R30 unchanged. Errors return the `gpio` tag followed
by current state.

## UI issue regression checks

### One simulator on multiple screens

Start the dashboard once. Open the **same full URL, including the port**, in
each browser tab or window. You can show Simulator in both, or Simulator in
one and I/O & Devices in the other. Page selection, core inspection, layout,
recording and unsaved editor drafts stay local to each view. Loaded firmware,
registers, inputs, memory, devices, clocks, execution cores and execution status
are shared. Loading the encoder-and-reader example selects both execution cores
in every view. It does not open extra source or register panels in other views.
Reset Layout changes only the local view; it keeps the shared execution cores.
Core selection stays disabled until the tab connects and receives the shared
execution state, so a local click cannot be lost while the tab is connecting.
After a disconnect, the page checks server availability over HTTP before
opening another WebSocket. While the server is stopped it shows Waiting for
server; it does not keep starting failed WebSocket handshakes.

One Run or SIM timer advances the backend at a time. All views receive its
updates. Use Stop in either view to stop that run. Closing its controlling
tab stops execution after the current chunk; other tabs remain connected to
the same simulator. Loading firmware, resetting or replacing the configuration
also stops the current run. Reopening a tab reads the current state rather
than restarting the simulator.

To check SSI on two screens:

1. In the first tab, select I/O & Devices and load either SSI example.
2. In the second tab, select Simulator. The loaded code must appear there.
   For the encoder-and-reader example, both PRU0 and PRU1 must be selected
   for execution in the CORES deck.
3. Enable Signal Graph REC in the second tab. You can turn recording off in
   the first tab; the second tab still receives the firmware's signal samples.
4. Press Run in either tab. Check that the other tab's PC, registers and
   traces advance while I/O & Devices shows received SSI frames and position.
5. Change the SSI position. Both tabs must show the new received position
   after the next frame. Press Stop in either tab; both status badges must stop.
6. Open a third tab at the same URL. It must show the current code and counter
   values and execution cores without resetting or executing another copy of
   the firmware. Start SIM in one tab; both SIM buttons must show Stop SIM.
   Stop from the other tab. Then start Run there and stop from the first tab.

Signal buffers begin when that view starts recording; earlier samples and
unsaved editor text are not copied between browser tabs. A view too slow to
receive updates reconnects instead of blocking the run or silently dropping
signal samples.

### Existing issue checks

- [#53](https://github.com/TexasInstruments/pru-simulator/issues/53): load the
  same assembly twice. Both replies must keep Source / Disassembly visible.
  Switching between cores with identical firmware must also keep the listing.
- [#62](https://github.com/TexasInstruments/pru-simulator/issues/62): open the same
  assembly file in the PRU0 and PRU1 editor workspaces. Edit each copy, then
  close the PRU0 copy. PRU1 must keep its file and text. Load different programs
  into each core and repeat; closing a file must leave both loaded programs
  unchanged. Use Load into to test PRU/RTU targets independently of inspection.
- [#55](https://github.com/TexasInstruments/pru-simulator/issues/55): in
  I/O & Devices → Devices → UART, use Record. Signal Graph REC must show the
  same recording state. Stop recording and Decode the captured GPO0 trace.
- [#56](https://github.com/TexasInstruments/pru-simulator/issues/56): edit the
  UART RX injection fields and click Inject. Click Reset inputs; the feedback
  disappears and the input defaults return without restarting Python.
- [#61](https://github.com/TexasInstruments/pru-simulator/issues/61): minimize
  and hide panels, then click Reset Layout. The current layout returns to its
  default sizes with panels visible and expanded. Repeat with 2, 3 and 4 cores.
