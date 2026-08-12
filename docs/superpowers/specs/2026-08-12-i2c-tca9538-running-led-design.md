# I2C Master → TCA9538 Running LED — Design

**Date:** 2026-08-12
**Status:** Approved

## Problem

The user wants PRU assembler firmware that bit-bangs I2C at 1 Mbit/s
and drives a TCA9538 8-bit I2C IO expander to run a walking-LED
pattern across its 8 output ports, developed stepwise with the PRU
simulator and MCP server. The simulator has no I2C concept today —
only raw GPO/GPI (`R30`/`R31`), a generic bit-mirror loopback, and
protocol-specific peripherals (Peripheral Interface, SD filter) that
take over R30/R31 wholesale. There is no I2C slave to talk to, so
without a device model the firmware's ACK bit and the "LED" state are
both unobservable — decided (via user Q&A) to build a real TCA9538
device model rather than firmware-only with a fake loopback ACK.

## Scope decisions (from brainstorming Q&A)

- **Fidelity:** full TCA9538 device model (Python), not just firmware
  + waveform inspection. Closed-loop: firmware gets a real ACK/NACK,
  wrong address really NACKs, output pins are genuinely observable.
- **PRU clock:** 200 MHz (matches `spi_master_tx.asm` and the
  simulator default) → 200 cycles per 1 Mbit/s I2C bit.
- **Slave address:** 0x23 (7-bit).
- **UI:** new auto-appearing IO-panel section (same pattern as the SD
  filter panel), not backend-only.
- **Direction:** write-only master. The TCA9538 model does not
  implement the Input Port register or read transactions — out of
  scope, since the task only needs to drive outputs. A read request
  (R/W=1) NACKs.
- **Register scope:** Output Port (0x01) and Configuration (0x03)
  only. Polarity Inversion (0x02) is accepted but unused by firmware;
  Input Port (0x00) is not writable and not modeled as readable.

## Findings (verified before design)

- `JAL`/`JMP` are implemented (`core/pru_core.py:272-281`): `JAL reg,
  target` stores `pc+1` in `reg` and jumps; `JMP target` jumps to a
  register or label value. This supports a real call/return idiom
  (`jal r_ret, sub` ... `jmp r_ret`), so the firmware doesn't need
  `spi_master_tx.asm`'s fully-unrolled style — 1 Mbit/s at a 200 MHz
  core gives 200 cycles/bit, ample budget for a small branchy loop,
  and the protocol has real per-byte ACK branch points that unrolling
  would obscure three times over (address, register pointer, data).
- `IOPort.write_r30`/`read_r31` (`pru_io/io_port.py`) already have a
  clean attach pattern: `sd_filter` and `perif` hijack R30/R31
  wholesale when enabled; `loopback_mask` mirrors GPO bits into GPI
  unconditionally whenever set. Bits 0/1 are already meaningful to
  `spi_master_tx.asm` (SCLK/MOSI) — an I2C device permanently wired to
  bits 0/1 would corrupt that program's GPI reads if ever loaded while
  attached. The device must be **opt-in**, mirroring how loopback
  groups are user-toggled per session rather than always active.
- `tests/test_io_loopback.py` and `tests/test_io_port.py` establish
  the pytest convention for `IOPort` unit tests: instantiate `IOPort`
  directly, call the mutator, assert on internal state.
- TCA9538 real reset defaults (datasheet): Output Port = 0xFF,
  Polarity Inversion = 0x00, **Configuration = 0xFF (all 8 pins
  inputs)**. Firmware must explicitly write Configuration = 0x00
  before any Output Port write has a visible effect — this is a
  faithful protocol detail, not an extra step invented for the demo.

## Design

### 1. `TCA9538Device` — new module `pru_io/i2c/tca9538.py`

A single-slave I2C state machine, stepped once per `R30` write (same
instruction-granularity sampling the simulator already uses for
UART/loopback — there is no separate wall-clock tick between
instructions in GP mode).

```
class TCA9538Device:
    def __init__(self, address: int = 0x23):
        self.address = address & 0x7F
        self.output_reg = 0xFF
        self.polarity_reg = 0x00
        self.config_reg = 0xFF
        self.state = "IDLE"
        self._prev_scl = True
        self._prev_sda = True
        self._shift = 0
        self._bit_count = 0
        self._reg_ptr = None
        self.last_transaction = None   # dict for UI: {addr, reg, data, ack}
        self.saw_start = False         # UI panel visibility trigger

    def step(self, scl: bool, sda_master: bool) -> bool:
        """Advance one R30-write's worth of bus state. Returns the bus
        SDA level (wired-AND of master + slave drive) for IOPort to
        fold into GPI."""
        slave_drive = self._drive_bit()          # True = release (default)
        bus_sda = sda_master and slave_drive
        self._detect_start_stop(scl, bus_sda)
        self._maybe_sample_bit(scl, bus_sda)
        self._prev_scl, self._prev_sda = scl, bus_sda
        return bus_sda
```

State machine (states advance only on the SCL rising edge that
follows a full bit, except START/STOP which are SDA edges while
SCL=1):

```
IDLE --START--> ADDR (shift in 7 addr bits + R/W, MSB first)
ADDR --8th bit--> if addr matches & R/W==0: drive SDA low next cycle (ACK), else NACK (stay released, return to IDLE on STOP)
  --> ACK_ADDR --> REGPTR (shift in 8 bits: register pointer)
REGPTR --8th bit--> ACK_REG (slave drives low) --> DATA (shift in 8 bits)
DATA --8th bit--> ACK_DATA (slave drives low; on the *release* edge
                             after driving ACK, commit the byte:
                             config_reg or output_reg per _reg_ptr)
ACK_DATA --STOP--> IDLE
ACK_DATA --repeated START--> ADDR (transaction abandoned/restarted)
any state --STOP or malformed--> IDLE (no partial register writes)
```

Only single-byte register writes are supported (STOP must follow the
first data ACK) — matches the firmware's design (one CONFIG write,
then repeated OUTPUT writes, each its own transaction). A second DATA
byte before STOP is treated as a protocol error: reset to IDLE,
`last_transaction["ack"] = False`, no register write.

`_drive_bit()` returns `False` (drive low) only during the ACK cycle
of a matched, in-progress transaction; `True` (released) otherwise —
this is what produces the real ACK/NACK the master reads back on
`R31`.

`saw_start` latches `True` on the first valid START and never clears
— it's the UI panel's appear trigger, independent of later NACKs.

### 2. `IOPort` integration (`pru_io/io_port.py`)

```
_I2C_SCL_BIT = 0
_I2C_SDA_BIT = 1

# __init__
self.i2c_device: TCA9538Device | None = None

def attach_i2c_device(self, device: TCA9538Device | None) -> None:
    self.i2c_device = device

# write_r30, appended after the existing loopback_mask block:
if self.i2c_device is not None:
    scl = bool(value & (1 << _I2C_SCL_BIT))
    sda_master = bool(value & (1 << _I2C_SDA_BIT))
    bus_sda = self.i2c_device.step(scl, sda_master)
    if bus_sda:
        self.gpi |= (1 << _I2C_SDA_BIT)
    else:
        self.gpi &= ~(1 << _I2C_SDA_BIT)
```

Open-drain convention: firmware writes **1 = release**, **0 = drive
low**, for both SCL and SDA — this matches real open-drain I2C pad
behavior (not push-pull GPIO), and is what makes the wired-AND
(`sda_master and slave_drive`) a correct bus model. SCL is master-only
(TCA9538 does not clock-stretch per its datasheet), so SCL is read
directly from `value` with no wired-AND needed.

Reset/detach: `attach_i2c_device(None)` fully restores plain-GPIO
behavior on bits 0/1, so loading `spi_master_tx.asm` (or any other
demo) after detaching is unaffected — same as disabling a loopback
group.

### 3. Firmware — `source/i2c_tca9538_running_led.asm`

Timing: 200 MHz / 1 Mbit/s = 200 cycles/bit. Split 100 cycles low /
100 cycles high (SDA changes during SCL-low, sampled during SCL-high)
— same shape as `spi_master_tx.asm`'s per-bit split, scaled 10x.

Subroutines (JAL/JMP call convention, one dedicated return-address
register per call site to keep it simple — no stack, matching every
other example in `source/`):

- `i2c_start` — from idle (SDA=1, SCL=1): SDA→0 while SCL=1 (START),
  then SCL→0.
- `i2c_stop` — from SCL=0: SDA→0, SCL→1, then SDA→1 while SCL=1
  (STOP), leaves bus idle (SDA=1, SCL=1).
- `i2c_write_byte` (byte in a fixed register, MSB first): 8× {SCL low,
  drive SDA=bit, delay to mid-low, SCL high, delay to mid-high, SCL
  low}; 9th cycle releases SDA (write 1) and pulses SCL to sample the
  ACK bit off `R31` bit1 during SCL-high. Returns ACK (0) / NACK (1)
  in a fixed register.

Main flow:

```
init:
    i2c_start
    write_byte (ADDR<<1 | 0)      ; address + write
    check ack -> on NACK: set DRAM0[error_flag], halt
    write_byte 0x03                ; Configuration register pointer
    check ack
    write_byte 0x00                ; all 8 pins = outputs
    check ack
    i2c_stop

run_loop:
    i2c_start
    write_byte (ADDR<<1 | 0)
    check ack -> on NACK: set DRAM0[error_flag], continue (soft error, next iteration retries)
    write_byte 0x01                ; Output Port register pointer
    check ack
    write_byte pattern
    check ack
    i2c_stop
    rotate pattern (lsl by 1; if pattern == 0x100 -> pattern = 0x01)
    qba run_loop
```

`pattern` walks `0x01 -> 0x02 -> ... -> 0x80 -> 0x01 -> ...` — one bit
per port, matching `running_led.asm`'s style but scoped to the
TCA9538's 8 physical ports (vs. 20 simulated GPO pins).

Error convention: NACK during `init` is fatal (halt — nothing useful
can happen without Configuration set); NACK during `run_loop` sets
`DRAM0[0x0FFE] = 1` (same offset/convention as
`uart_rx_11frame.asm`'s framing-error flag) and continues to the next
iteration, so a transient/simulated bus fault is visible in the
Memory panel without stopping the LED pattern.

### 4. UI — IO panel section

New section, auto-appears once `i2c_device.saw_start` is true (same
trigger style as the SD panel appearing on `sd_en`). Shows:

- 8 LED indicators: `output_reg & ~config_reg` (bit=1 → configured as
  output AND driven high) — reusing the existing GPO-indicator visual
  style.
- Last decoded transaction: address, register pointer, data byte,
  ACK/NACK, rendered as a single-line log entry per completed
  transaction (append-only, capped like existing log panels).
- An attach/detach toggle ("Attach TCA9538 @ 0x23 (SCL=bit0,
  SDA=bit1)"), off by default, parallel to the existing loopback-group
  toggles. Detaching hides the panel and calls
  `attach_i2c_device(None)`.

Exact wiring into `server.py`'s state-push payload and `app.js`'s
panel-render code is an implementation-plan concern, not a design
concern — follows the same request/response and snapshot shape
already used for the SD filter panel.

## Testing

- **`tests/test_io_port.py`-style unit tests for `TCA9538Device`**
  (new `tests/test_i2c_tca9538.py`): full valid transaction (config
  write) updates `config_reg`; full valid transaction (output write)
  updates `output_reg`; wrong address → NACK, no register write;
  read request (R/W=1) → NACK; STOP mid-transaction → no partial
  write, state resets to IDLE; a second data byte before STOP is
  rejected.
- **`IOPort` wired-AND tests** (same file or `test_io_loopback.py`):
  master releases + slave releases → bus high; master releases +
  slave drives low (ACK) → bus low; master drives low → bus low
  regardless of slave state; detaching mid-run restores plain GPIO
  (bit1 no longer forced by the device).
- **Firmware integration test**: load
  `i2c_tca9538_running_led.asm` into a `PRUCore` with an attached
  `TCA9538Device`, run enough steps to complete the init transaction +
  several run-loop iterations, assert `config_reg == 0x00` and
  `output_reg` cycles `0x01 -> 0x02 -> 0x04 -> ... -> 0x80 -> 0x01`.
- **Stepwise MCP verification** (interactive, during implementation,
  not a checked-in test): `pru_load` the firmware, `pru_step` through
  the first `i2c_start` + first byte + ACK by hand, cross-check the
  SCL/SDA waveform against the bit timing computed above before
  trusting the full `run_loop` — consistent with the project's
  standing rule that hand-tallied cycle-timing claims near a fixed
  budget must be confirmed by single-stepping, not just arithmetic.
- All existing tests must keep passing unchanged (opt-in attach means
  `IOPort`'s default behavior is untouched when no I2C device is
  attached).
