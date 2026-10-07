# SSI Device Model and Reader

The simulator has an independent SSI encoder device model, a small PRU reader
firmware, and a host helper that runs the firmware through normal
`Simulator.step` execution. `pru_io/ssi_encoder_model.py` remains the example
of a time-driven external `DeviceModel`; the firmware neither imports nor
shares its protocol logic.

## SSI edge and timing rules

Encoders and documents used. None of the documents is included in this
repository.

* **RM08** miniature magnetic encoder (RLS), data sheet RM08D01_18, issue 18,
  p. 10: the standard 12-bit preset, the 4 MHz clock limit and the tm range of
  12.5 us to 20.5 us.
* **SICK** AHS/AHM36, AFS/AFM60, AFS/AFM60S Pro, ARS60, TTK70 and KH53:
  *Technical information - SSI Interface Description*, SICK AG, IM0100079
  (part no. 8027422, 2022-02-08): frame layouts, error bits, the clock sequence
  (section 2.2) and the tm range.

The model follows the clock sequence in SICK IM0100079, section 2.2 (p. 5): the
first falling clock edge latches position, each rising edge presents the next
bit MSB first, and Tm is measured from the latest falling edge. The document
does not say which edge the master samples on; data may be sampled during the
high phase after a rising edge.

The Nth rising edge completes a word. Tm can then expire with the clock held
high, or the reader may produce its closing falling edge followed by the
return to high idle. That one closing pair does not request another data bit;
another falling edge before Tm expires is reported as an extra clock pulse.
The LSB remains on data through the high-phase sample. The closing falling
edge then drives data low for the rest of Tm, after which the model returns it
to its configured idle value.
An incomplete word and an SSI clock above `f_max_hz` also produce fault
events. Completed-frame events retain both the transmitted `raw_value` and
the decoded binary `position` for binary or Gray encoding, plus the decoded
`error` when the frame has error bits.

SSI data is push-pull. When an SSI profile is attached through the generic MCP
API, its data pin is released from the PRU's output mask while attached. TCA
SCL/SDA stay open-drain, so the PRU master can still pull either line low and
release it high.

## Frame layout and presets

A frame is `resolution` clocked bits (1-64) holding a position field and an
optional error field, sent MSB first. Offset 0 is the last bit sent:

| Option | Default | Meaning |
|---|---|---|
| `resolution` | 12 | total clocked bits |
| `position_bits` | `resolution - error_bits` | width of the position field |
| `error_bits` | 0 | width of the error field |
| `error_offset` | 0 | lowest bit of the error field |
| `position_offset` | `error_bits` (0 if only `error_offset` is given) | lowest bit of the position field |
| `error_value` | 0 | error field latched by the next frame |

The defaults send the error bits after the position, which is how the SICK
document draws every frame. Fields must fit the frame and may not overlap;
unused bits are sent as 0. `encoding="gray"` converts the position field only.
`set_position()` and `set_error()` change what the next frame latches, and
`pack_frame()`/`decode_frame()` convert between fields and the wire word.

`pru_io/ssi_presets.py` holds twelve frames, selected with
`SSIEncoderModel.from_preset(name, **overrides)`, `SSIRuntime(preset=...)` or
the `preset` field of the `ssi_encoder` profile. Explicit options override the
preset.

The SICK frame sizes come from *Technical information - SSI Interface
Description - Synchronous Serial Interface for Absolute Encoders*, SICK AG,
IM0100079 (part no. 8027422, 2022-02-08), section 3. The standard 12-bit preset
comes from the RM08 miniature magnetic encoder data sheet, RM08D01_18 (issue 18,
5 February 2026), p. 10. Neither document is included in this repository.

| Preset | Frame bits | Position bits | Error bits | Source |
|---|---:|---:|---:|---|
| `RM08_12BIT_4MHZ` | 12 | 12 | 0 | RM08D01_18 p. 10 (standard, not SICK) |
| `AHS_AHM36_SINGLETURN` | 15 | 14 | 1 | p. 8 |
| `AHS_AHM36_MULTITURN` | 27 | 26 | 1 | p. 11 |
| `AFS_AFM60_SINGLETURN` | 21 | 18 | 3 | p. 14 |
| `AFS_AFM60_MULTITURN_30BIT` | 33 | 30 | 3 | p. 14 |
| `AFS_AFM60_MULTITURN_27BIT` | 30 | 27 | 3 | pp. 14-15 |
| `AFS_AFM60S_PRO_SINGLETURN` | 21 | 18 | 3 | p. 17 |
| `AFS_AFM60S_PRO_MULTITURN_EXAMPLE` | 28 | 25 | 3 | p. 18 (the 25-bit example) |
| `ARS60_SHORT` | 13 | 13 | 0 | p. 22 |
| `ARS60_LONG` | 17 | 15 | 2 | p. 22 |
| `TTK70` | 26 | 24 | 2 | p. 23 |
| `KH53` | 24 | 24 | 0 | p. 24 |

The standard preset uses `f_max_hz` 4 MHz and `monoflop_us` 12.5, the RM08
limits (clock <= 4 MHz, 12.5 us <= tm <= 20.5 us, p. 10); the model default
`monoflop_us` of 20.5 is the upper limit of the same range.

Timing fields the model uses: SICK presets set `f_max_hz` to 2 MHz, the
highest baud rate the document allows (p. 4), and `monoflop_us` to 20, the
middle of its 15-25 us tm range (p. 5). The document's tv (< T/2) and Tp
(> tm) constrain the master and have no equivalent in the model, so presets
do not carry them. Configurable AHS/AHM and AFS/AFM presets use explicit binary
examples rather than claiming a manufacturer's default. `AFS_AFM60S_PRO_MULTITURN_EXAMPLE`
keeps the illustrated 25-bit position, not a universal width.

`ARS60_SHORT` and `ARS60_LONG` use standard Gray position for the
[ARS60-AAA08192](https://www.sick.com/media/pdf/8/28/728/dataSheet_ARS60-AAA08192_1031458_en.pdf)
and [ARS60-A4B32768](https://www.sick.com/media/pdf/6/06/406/dataSheet_ARS60-A4B32768_1031497_zf.pdf)
SSI variants (Interfaces, p. 2); trimmed Gray variants need a different layout.
`TTK70` uses the [TTK70-AXA0-K02](https://www.sick.com/media/pdf/0/40/840/dataSheet_TTK70-AXA0-K02_1038033_en.pdf)
24-bit Gray position (p. 2), followed by the two separate error bits in
[IM0100079](https://www.sick.com/media/docs/9/79/079/technical_information_ssi_interface_description_en_im0100079.pdf)
p. 23: the resulting full wire frame is 26 bits, not a 26-bit Gray conversion.
`KH53` uses [24-bit Gray SSI](https://www.sick.com/media/docs/0/00/600/product_information_kh53_linear_encoders_en_im0011600.pdf)
(Interfaces, p. 3). The multi-turn presets are a plain
position field; the model does not split turns from steps. Left/right
justification, round-axis and ATM60/ATM90 formats are not presets (use
`position_offset`/`position_bits` for a custom layout).

## Shared-memory ABI

`schema/ssi_config_abi.json` is the source for the generated
`pru_io/ssi_config_abi.py` and `source/ssi_config_abi.inc` files. Regenerate and
check them with:

```bash
python -m tools.gen_ssi_abi
python -m tools.gen_ssi_abi --check
```

All fields are little-endian unsigned 32-bit values. The shared-memory base is
`0x00010000`.

| Block | Offset | Fields |
|---|---:|---|
| Reader config | `0x00` | ABI version (3), frame width, clock delay loops, idle delay loops |
| Latest-frame mailbox | `0x20` | seqlock sequence, raw frame low word, raw frame high word, frame count, status |
| Emulator config | `0x40` | ABI version, frame width, frame word low, frame word high, status, monoflop ticks |

Frame widths are 1–64 bits; the mailbox and the emulator block carry the frame
as low and high 32-bit words (the high word is 0 up to 32 bits).
The reader sets the sequence odd while publishing, writes the sample and
count, then sets it even. `status` is zero for a valid config and one if the
reader halts on an invalid ABI version or frame width.

The emulator block is written by the host with
`pru_io.ssi_runtime.SSIEmulatorRuntime`, which packs position and error with
the same layout code as the encoder model; the firmware only shifts the word
out. The emulator re-reads the block at every frame's first falling clock
edge, so host changes apply to the next frame (the block is not seqlocked).
Its `status` is written by the firmware: 0 while running, 1 if it halted on an
invalid ABI version, a frame width outside 1-64, or zero timeout ticks.
ABI 3 appends `monoflop_ticks` as u32 at emulator offset `0x14`; reader and
mailbox offsets stay unchanged. The host rounds up the selected monoflop
interval times the active IEP rate. Both mid-frame waits use the memory-mapped
64-bit IEP count since the most recent falling edge. Timeout discards a partial
word, restores idle DATA, and requires high idle followed by a fresh falling
start; it is a normal abort, with status zero. Expiry is checked before either
wait accepts an edge, so a late edge after another core advanced shared time
cannot continue an expired word. Normal completion drives DATA low at the
closing fall and holds it through the monoflop interval before restoring idle
high. Polling uses ordinary PRU instructions and memory access cycles.

The runtime reuses an enabled free-running IEP with `DEFAULT_INC=1`. An enabled
timer with another increment, or enabled CMP0 reset, is rejected. A stopped
timer is enabled with increment one without resetting its count. Changing the
active IEP clock requires reloading the emulator timeout configuration.

## Run the reader and SSI model

The example reader uses PRU1 R30.0 for clock and R31.16 for data. The host
helper attaches the encoder, releases the data output direction, packs the ABI
config, loads the assembly, and steps one ordinary PRU instruction at a time:

```python
from pru_io.ssi_runtime import SSIRuntime
from simulator import Simulator

sim = Simulator("memory.cfg")
with SSIRuntime(sim, position=0xABC, resolution=12) as ssi:
    ssi.load(clock_delay_loops=20)
    result = ssi.run_until_frames(1, max_steps=20_000)
    assert result["reached"]
    print(hex(result["mailbox"]["raw_frame"]))
    print(ssi.encoder.events())
    print(ssi.encoder.faults())
```

`mailbox()` also returns the combined `raw_frame` and the `position` and
`error` fields decoded with the encoder's layout. To emulate a SICK encoder,
pass a preset; the 2 MHz limit needs a slower reader clock than the default
(about `clock_delay_loops=40` at 250 MHz):

```python
with SSIRuntime(sim, preset="AFS_AFM60_MULTITURN_30BIT",
                position=0x2ABCDEF1, error_value=0b101) as ssi:
    ssi.load(clock_delay_loops=40)
    result = ssi.run_until_frames(1, max_steps=40_000)
    print(result["mailbox"])  # raw_frame 0x155E6F78D, position, error 5
```

`clock_delay_loops` adds low and high phase delay. `idle_delay_loops` defaults
to enough cycles for the configured encoder's Tm to expire. A completed sample
is published to the mailbox immediately after its last bit; the encoder's
`frame` event is emitted after Tm expires so a later extra pulse can invalidate
the transaction.

## Two-core firmware loopback

`source/ssi_generic_emulator.asm` runs a runtime-configurable SSI encoder on
PRU0 while the reader runs on PRU1. Connect PRU1 R30.0 to PRU0 R31.0 for
clock, and PRU0 R30.16 to PRU1 R31.16 for data. The emulator latches its
frame on the first falling clock edge and presents one bit after each rising
edge. `tests/test_ssi_board_loopback.py` runs both images for the 12-bit
default and for multi-turn presets, and `tests/test_ssi_emulator.py` checks the
emulator against a separate Python SSI master:

```python
from pru_io import ssi_config_abi as abi
from pru_io.ssi_runtime import SSIEmulatorRuntime

sim.add_gpio_wire("pru1", 0, "pru0", 0)
sim.add_gpio_wire("pru0", 16, "pru1", 16)
sim.set_gpio_drive_mask("pru0", (1 << 20) - 1 & ~(1 << 0))
sim.set_gpio_drive_mask("pru1", (1 << 20) - 1 & ~(1 << 16))
emulator = SSIEmulatorRuntime(sim, preset="AFS_AFM60_MULTITURN_30BIT",
                              position=0x2ABCDEF1, error_value=0b101)
emulator.load()                       # PRU0; later: emulator.set_error(0)
sim.memory.write(abi.CONFIG_ADDRESS, abi.pack_config(
    frame_bits=33, clock_delay_loops=20, idle_delay_loops=8))
sim.load("pru1", reader_source, include_paths=["source"])  # the reader asm
```

## Manual 300 MHz run (test only)

The default core clock is **250 MHz** and it stays that way: `memory.cfg`
ships with `pru_clock_mhz = 250` and `pru1_clock_mhz = 250`, and every default,
example and test is written for it. Running at 300 MHz is a manual switch that
someone makes only to try the reader at that clock. Do not change the shipped
`memory.cfg` for it.

Pick one way to switch, and switch back afterwards:

* **Temporary config (preferred).** Copy the config and its constants sidecar
  into a temporary directory and edit the copy, as below. The shipped
  `memory.cfg` is never touched.
* **Dashboard.** The core speed selector (300) rewrites `memory.cfg` in place.
  Pick 250 again, or run `git checkout memory.cfg`, before committing.
* **Editing `memory.cfg` by hand.** Set both `pru_clock_mhz` and
  `pru1_clock_mhz`, then restore 250 the same way.

The reader needs a longer bit period at 300 MHz. Its bit period is about four
cycles per `clock_delay_loops` plus eight, and the encoder model enforces
`f_max_hz`: at 300 MHz the 4 MHz `RM08_12BIT_4MHZ` preset needs at least 75
cycles per bit (`clock_delay_loops=20` gives about 88), and the 2 MHz SICK
presets need at least 150 (`clock_delay_loops=40` gives about 168). The
simulator locates `constants_am243x.cfg` relative to the config file, so the
sidecar must follow the copied `memory.cfg`:

```python
from pathlib import Path
import shutil
import tempfile

from pru_io.ssi_runtime import SSIRuntime
from simulator import Simulator

project = Path.cwd()
with tempfile.TemporaryDirectory(prefix="ssi-300-") as temporary:
    temporary = Path(temporary)
    sidecar = temporary / "config"
    sidecar.mkdir()
    data = (project / "memory.cfg").read_bytes()
    data = data.replace(b"pru_clock_mhz = 250", b"pru_clock_mhz = 300", 1)
    data = data.replace(b"pru1_clock_mhz = 250", b"pru1_clock_mhz = 300", 1)
    config = temporary / "memory.cfg"
    config.write_bytes(data)
    shutil.copy2(project / "config" / "constants_am243x.cfg",
                 sidecar / "constants_am243x.cfg")

    sim = Simulator(str(config))
    assert sim.constant_table.resolve(28) == 0x00010000
    with SSIRuntime(sim, preset="RM08_12BIT_4MHZ", position=0xABC) as ssi:
        ssi.load(clock_delay_loops=20)
        result = ssi.run_until_frames(1, max_steps=20_000)
        assert result["reached"]
        assert result["mailbox"]["raw_frame"] == 0xABC
        assert ssi.encoder.faults() == []
        print(result["mailbox"])
```

The helper reads the selected core clock exactly from the simulator and
computes its idle delay from Tm. The temporary config and constants sidecar are
removed automatically; the tracked `memory.cfg` remains untouched. The same
switch works for the emulator firmware on PRU0 (see
`tests/test_ssi_board_loopback.py`). The IEP clock plays no part in the reader.

## Generic MCP device profiles

The MCP surface stays protocol-generic:

| Tool | Purpose |
|---|---|
| `pru_device_discover` | List validated profiles and attached devices |
| `pru_device_attach` | Attach `ssi_encoder` or `tca9538` using an object config |
| `pru_device_detach` | Detach by unique device name |
| `pru_device_state` | Read devices, bus levels, events, and faults |
| `pru_device_events` | Read all device events or filter by name |
| `pru_device_faults` | Read all bus/device faults or filter by name |

The SSI profile accepts encoder options such as `position`, `resolution`,
`encoding`, `f_max_hz`, and `monoflop_us`, the frame layout fields
(`position_bits`, `position_offset`, `error_bits`, `error_offset`,
`error_value`) and a `preset` name; `pru_device_discover` lists the presets
under `profiles.ssi_encoder.presets`. If `core_clock_hz` is omitted, the
MCP attach tool derives it from the selected core's exact IEP clock. The TCA
profile accepts a 7-bit
`address` and distinct `scl_pin`/`sda_pin` values from 0–19. Unknown fields
and invalid values are rejected before attachment. The existing TCA
`pru_i2c_attach` tool remains available for compatibility.

When `pru_device_faults` is filtered by device name, its `contentions` field
contains structured records for each bus conflict involving that exact device.
The existing `faults` field retains human-readable messages.


SSI clock limits apply to each rising-edge period. Integer cycle quantization
rounds the minimum interval up: at 250 MHz, 4 MHz takes 62.5 cycles, so 62 cycles
exceeds the limit and 63 cycles satisfies it. At 300 MHz, 75 cycles represents
4 MHz exactly. The model does not relax a per-period limit to accept rounding
to a shorter interval.
