# SSI Device Model and Reader

The simulator has an independent SSI encoder device model, a small PRU reader
firmware, and a host helper that runs the firmware through normal
`Simulator.step` execution. `pru_io/ssi_encoder_model.py` remains the example
of a time-driven external `DeviceModel`; the firmware neither imports nor
shares its protocol logic.

## SSI edge and timing rules

The model follows the SSI timing descriptions in the [Pepperl+Fuchs AVM78E
data sheet, page 5](https://files.pepperl-fuchs.com/webcat/navi/productInfo/pds/t157829_eng.pdf?v=20241026195055)
and [AVS36M data sheet, page 4](https://files.pepperl-fuchs.com/webcat/navi/productInfo/pds/t42141_eng.pdf):
the first falling clock edge latches position, each rising edge presents the
next bit MSB first, and Tm is measured from the latest falling edge. Data may
be sampled during the high phase after a rising edge.

The Nth rising edge completes a word. Tm can then expire with the clock held
high, or the reader may produce its closing falling edge followed by the
return to high idle. That one closing pair does not request another data bit;
another falling edge before Tm expires is reported as an extra clock pulse.
An incomplete word and an SSI clock above `f_max_hz` also produce fault
events. Completed-frame events retain both the transmitted `raw_value` and
the decoded binary `position` for binary or Gray encoding.

SSI data is push-pull. When an SSI profile is attached through the generic MCP
API, its data pin is released from the PRU's output mask while attached. TCA
SCL/SDA stay open-drain, so the PRU master can still pull either line low and
release it high.

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
| Reader config | `0x00` | ABI version, frame width, clock delay loops, idle delay loops |
| Latest-frame mailbox | `0x20` | seqlock sequence, raw frame, frame count, status |

Frame widths are 1–32 bits because the mailbox contains one 32-bit raw frame.
The reader sets the sequence odd while publishing, writes the sample and
count, then sets it even. `status` is zero for a valid config and one if the
reader halts on an invalid ABI version or frame width.

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

`clock_delay_loops` adds low and high phase delay. `idle_delay_loops` defaults
to enough cycles for the configured encoder's Tm to expire. A completed sample
is published to the mailbox immediately after its last bit; the encoder's
`frame` event is emitted after Tm expires so a later extra pulse can invalidate
the transaction.

## Manual 300 MHz run

`memory.cfg` stays at the shipped 250 MHz default. To try the example at
300 MHz, make a temporary copy and change only the copy:

```bash
python - <<'PY'
from pathlib import Path

data = Path("memory.cfg").read_bytes()
data = data.replace(b"pru_clock_mhz = 250", b"pru_clock_mhz = 300", 1)
data = data.replace(b"pru1_clock_mhz = 250", b"pru1_clock_mhz = 300", 1)
Path("/tmp/memory-ssi-300.cfg").write_bytes(data)
PY
```

Then run the same firmware with the temporary clock configuration:

```python
from pru_io.ssi_runtime import SSIRuntime
from simulator import Simulator

sim = Simulator("/tmp/memory-ssi-300.cfg")
with SSIRuntime(sim, position=0xABC, resolution=12,
                f_max_hz=4_000_000, monoflop_us=20.5) as ssi:
    ssi.load(clock_delay_loops=20)
    result = ssi.run_until_frames(1, max_steps=20_000)
    assert result["reached"]
    assert ssi.encoder.faults() == []
    print(result["mailbox"])
```

The helper reads the selected core clock from that temporary config, and
computes its idle delay from Tm. It leaves the tracked `memory.cfg` untouched.

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
`encoding`, `f_max_hz`, and `monoflop_us`. The TCA profile accepts a 7-bit
`address` and distinct `scl_pin`/`sda_pin` values from 0–19. Unknown fields
and invalid values are rejected before attachment. The existing TCA
`pru_i2c_attach` tool remains available for compatibility.
