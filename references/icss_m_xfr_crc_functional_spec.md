# ICSS SCU CRC Module (`icss_m_xfr_crc`) — Functional Specification

*Derived from RTL source `references/icss_m_xfr_crc.v`.*

## 1. Overview

`icss_m_xfr_crc` is the ICSS SCU CRC accelerator. It implements a single shared
CRC engine that can be configured at run time to compute one of three
polynomials, and accepts data in 8-, 16-, or 32-bit chunks per write. The
module is driven by a single clock (`ocp_gclk`, nominally 200 MHz) and an
active-low async reset (`ocp_arst_n`); its control/data interface follows an
OCP-style register-mapped protocol (write, seed, run, read strobes driven by
an external register/decode layer).

### 1.1 Supported polynomials

| Mode | Selected by | Polynomial |
|---|---|---|
| CRC-16 | `crc16_crc32_mode = 0`, `crc16_mod_en = 0` | `x^16 + x^15 + x^2 + 1` |
| CRC-16 (mod) | `crc16_crc32_mode = 0`, `crc16_mod_en = 1` | `x^16 + x^12 + x^5 + 1` |
| CRC-32 | `crc16_crc32_mode = 1` | `x^32 + x^26 + x^23 + x^22 + x^16 + x^12 + x^11 + x^10 + x^8 + x^7 + x^5 + x^4 + x^2 + x + 1` |

### 1.2 Supported data-width / clock-count combinations

| `crc_byten` | `crc16_crc32` mode | Clocks to complete | CRC width used |
|---|---|---|---|
| `4'b0001` | CRC-16 (0) | 1 | 8-bit data → 16-bit CRC |
| `4'b0011` | CRC-16 (0) | 1 | 16-bit data → 16-bit CRC |
| `4'b1111` | CRC-16 (0) | 2 | 32-bit data → 16-bit CRC (processed as two 16-bit halves) |
| `4'b0001` | CRC-32 (1) | 1 | 8-bit data → 32-bit CRC |
| `4'b0011` | CRC-32 (1) | 1 | 16-bit data → 32-bit CRC |
| `4'b1111` | CRC-32 (1) | 1 | 32-bit data → 32-bit CRC |

Any other `crc_byten` value (e.g. `0010`, `0100`, `0101`, `0110`, `0111`,
`1000`–`1110`) is illegal and is reported on `crc_error`.

## 2. Interface

### 2.1 Clock and reset

| Signal | Dir | Width | Description |
|---|---|---|---|
| `ocp_gclk` | in | 1 | Functional clock (200 MHz nominal). |
| `ocp_arst_n` | in | 1 | Active-low asynchronous reset. |

### 2.2 CRC inputs

| Signal | Dir | Width | Description |
|---|---|---|---|
| `crc_in` | in | 32 | Multi-purpose data bus: CRC seed value, mode-select payload, or CRC input data (8/16/32 bits, byte-lane selected by `crc_byten`). |
| `crc_byten` | in | 4 | Byte enable for the current data write; selects 8/16/32-bit operating case (see §1.2) and is latched for use across multi-clock operations. |
| `crc_write` | in | 1 | `1` = write `crc_in` into the data path (as seed or as data, depending on `crc_seed`); `0` = no write. |
| `crc_seed` | in | 1 | Chip-select qualifier for a write: `1` = the write loads `crc_in` directly into the CRC accumulator (`crc_reg`) as a seed; `0` = the write loads `crc_in` into the data-input register for normal CRC computation. |
| `crc_run` | in | 1 | `1` = execute one CRC computation step using the currently latched input data. |
| `crc16_crc32` | in | 1 | Chip-select strobe that updates the mode registers: `crc_in[0]` sets/clears CRC-32 mode (`crc16_crc32_mode`), `crc_in[2]` sets/clears the CRC-16 "mod" polynomial select (`crc16_mod_en`). |
| `crc_reset` | in | 1 | `1` = soft-reset the CRC accumulator to its mode-dependent initial value (see §3.5). |
| `crc_read_data` | in | 1 | Asserted by the register interface while `crc_out` is being read. |

### 2.3 CRC outputs

| Signal | Dir | Width | Description |
|---|---|---|---|
| `crc_out` | out | 32 | Current CRC accumulator value (`crc_reg`). Valid width depends on mode (16 or 32 bits, right-justified; unused upper bits are `0` in CRC-16 modes). |
| `crc_rready` | out | 1 | De-asserts for one clock when a read (`crc_read_data`) coincides with a running computation (`crc_run`), signalling the requester to stall (pipeline read-after-write hazard). |
| `crc_wready` | out | 1 | De-asserts for one clock during the first cycle of a 32-bit write performed while in CRC-16 mode, since that write requires two computation clocks before another write may be accepted. |
| `crc_error` | out | 1 | `1` when the latched `crc_byten` / `crc16_crc32_mode` combination is not one of the legal cases in §1.2. |

## 3. Functional Description

### 3.1 Write / seed / run protocol

The module distinguishes three kinds of register-interface transactions,
all issued through the same `crc_in` / `crc_byten` / `crc_write` bus:

1. **Mode-select write** — `crc16_crc32 = 1` together with a write strobe
   updates the two mode-configuration flops:
   - `crc16_crc32_mode <= crc_in[0]` (`0` = CRC-16 family, `1` = CRC-32)
   - `crc16_mod_en <= crc_in[2]` (CRC-16 family only: `0` = standard poly, `1` = "mod" poly)

   When `crc16_crc32` is asserted, `crc_reg` is simultaneously forced to its
   mode's reset value in the same clock: `32'hffff_ffff` if the new mode is
   CRC-32, else `32'h0000_0000` for CRC-16. This load takes priority over
   any concurrent seed load or CRC computation.

2. **Seed write** (`crc_write = 1`, `crc_seed = 1`) — loads `crc_in` directly
   into `crc_reg`, byte-for-byte, bypassing the polynomial math. Used to
   pre-load a starting CRC value.

3. **Data write** (`crc_write = 1`, `crc_seed = 0`) — captures `crc_in` into
   the internal data-input register per the active byte enables
   (`crc_byten`/latched `crc_byten_buf`). This is the value the CRC datapath
   consumes when `crc_run` is subsequently asserted.

`crc_run = 1` performs one CRC "shift" step: it feeds the currently latched
input data through the polynomial datapath selected by `crc_case` (§3.3) and
updates `crc_reg` with the result on the next clock edge.

### 3.2 Byte-enable latching

`crc_byten` only needs to be driven on the cycle of a new write
(latched whenever `crc_write & ~crc_run`); the value is captured and reused
for any follow-on clocks of the same logical operation (notably the
two-clock 32-bit-write-in-CRC-16-mode case). The same byte enable must be
used for back-to-back operations of a multi-clock case.

### 3.3 Operating-case decode (`crc_case`)

```
crc_case[4]   = crc16_crc32_mode
crc_case[3:0] = crc_byten (latched)
```

`crc_case` selects which of five parallel combinational CRC datapaths feeds
the next value of `crc_reg`:

| `crc_case` | Datapath | Data width | CRC width |
|---|---|---|---|
| `5'b00001` | `crc_8_16` / `crc_8_16_mod` (per `crc16_mod_en`) | 8 | 16 |
| `5'b00011` | `crc_16_16` / `crc_16_16_mod` (per `crc16_mod_en`) | 16 | 16 |
| `5'b01111` | `crc_16_16` / `crc_16_16_mod`, run twice (see §3.4) | 32 (as 2×16) | 16 |
| `5'b10001` | `crc_8_32` | 8 | 32 |
| `5'b10011` | `crc_16_32` | 16 | 32 |
| `5'b11111` | `crc_32_32` | 32 | 32 |
| any other value | none (`crc_error` asserted) | — | — |

Each datapath is a fully parallel (combinational) unrolling of the
bit-serial LFSR update for the corresponding data width and polynomial —
functionally equivalent to shifting the data bits one at a time through the
classic CRC shift register, but completed in a single clock.

### 3.4 32-bit write while in CRC-16 mode (2-clock case)

The CRC-16 engine only processes 16 bits per clock. When a 32-bit write
occurs while `crc16_crc32_mode = 0` (`crc_case = 5'b01111`), the module
splits the 32-bit word into two 16-bit halves and processes them across two
`crc_run` clocks using an internal 2-phase state machine:

1. **Phase 0**: the lower half-word (bits `[15:0]`) is fed through
   `crc_16_16`/`crc_16_16_mod`; `crc_reg` is updated with the result, and the
   phase flips.
2. **Phase 1**: the upper half-word (bits `[31:16]`) is fed through the same
   datapath, seeded with the phase-0 result; `crc_reg` is updated again, and
   the phase flips back.

`crc_wready` is held low during the write cycle that starts this sequence,
signalling that the register interface must wait for the two computation
clocks to complete before issuing another write.

### 3.5 Reset behavior

| Trigger | Effect |
|---|---|
| `ocp_arst_n = 0` (async hardware reset) | `crc_reg` and all pipeline/latch state clear to `0`. `crc16_crc32_mode` and `crc16_mod_en` clear to `0` (CRC-16, standard polynomial). |
| `crc16_crc32` mode-select write | `crc_reg` is force-loaded with the new mode's initial value: `32'hffff_ffff` for CRC-32, `32'h0` for CRC-16 (both variants). |
| `crc_reset = 1` (while `crc_rready`) | `crc_reg` reloads its mode's initial value (`0xffffffff` for CRC-32, `0x0` for CRC-16), without changing the mode itself. The reload is stalled (held off) while a back-to-back read is still pending. |

### 3.6 Error detection

`crc_error` is combinationally decoded from `crc_case` every clock (not
registered) using the legality table in §1.2. It is asserted whenever the
latched byte-enable pattern does not correspond to one of the eight legal
`{crc16_crc32_mode, crc_byten}` combinations (the two "idle", `4'b0000`,
combinations are included as legal/no-error).

### 3.7 Backpressure / ready signals

- **`crc_rready`** — de-asserted for one clock whenever a read
  (`crc_read_data`) is requested in the same clock that a computation is
  running (`crc_run`), because `crc_out` will not reflect the new result
  until the following clock. Also gates whether a `crc_reset` reload is
  allowed to proceed immediately (§3.5).
- **`crc_wready`** — de-asserted for one clock at the start of a 32-bit
  write performed in CRC-16 mode, since that operation needs the two-clock
  sequence described in §3.4 before the module can accept another write.
  In all other cases, `crc_wready` is asserted.

## 4. Bit-order function (`bitswap`)

Every data value entering the polynomial datapath is first **bit-reversed**
(mirrored, MSB ↔ LSB) within its own width, before any XOR/polynomial math
is applied. This is needed because the ICSS bus presents data LSB-first,
while the parallel-CRC equations assume the conventional MSB-first bit
order. The reversal is pure bit re-wiring — it does not depend on the seed
or the running CRC value — and is applied identically at each width:

```verilog
// Generic bit-order mirror used by every CRC datapath (8/16/32-bit).
// Reverses bit order only -- no XOR / polynomial math.
function [31:0] bitswap;
  input [31:0] word;
  input  [5:0] width;      // 8, 16, or 32
  integer i;
  begin
    bitswap = 32'h0;
    for (i = 0; i < width; i = i + 1)
      bitswap[i] = word[width-1-i];
  end
endfunction
```

This is the parameterized equivalent of the fixed-width concatenations used
in the RTL:

| Datapath input | Width | Equivalent to |
|---|---|---|
| 8-bit data (CRC-16 and CRC-32 8-bit cases) | 8 | `bitswap(data[7:0], 8)` |
| 16-bit data, CRC-16 cases (per active half-word) | 16 | `bitswap(half_word[15:0], 16)` |
| 16-bit data, CRC-32 case | 16 | `bitswap(data[15:0], 16)` |
| 32-bit data, CRC-32 case | 32 | `bitswap(data[31:0], 32)` |

One datapath applies an **additional** step on top of the bit-reversal: the
CRC-16 "mod" polynomial's 16-bit-data equations were generated against a
byte order that is also swapped relative to the plain bit-reversed word, so
only that path does one extra byte-swap:

```verilog
// Extra step used only by the CRC-16 "mod" 16-bit-data path.
d1_bswap = {d1[7:0], d1[15:8]};   // swap the two bytes of the bit-reversed word
```

No other datapath (standard CRC-16 8-/16-bit, or any CRC-32 case) uses this
extra byte-swap.

## 5. Register Mapping

This mapping is documented ground truth — AM64x/AM243x Processors TRM
(SPRUIM2H), Section 6.4.6.2.2.1 "PRU and CRC16/32 Interface", Table 6-429
"CRC Register to PRU Port Mapping" — not a guess. It is implemented in the
simulator at `xfr/crc_accelerator.py` (`CRCAccelerator`, device_id `1`,
registered in `core/pru_core.py` alongside `MACAccelerator`). Unit tests
live in `tests/test_crc_accelerator.py`.

The CRC16/32 module connects to the *same* fixed broadside register window
as the MAC accelerator, `R25`–`R29` — confirmed by the TRM, not just an
analogy: which accelerator a given `XIN`/`XOUT`/`XCHG` reaches is selected
by the `device_id` operand (`1` for CRC16/32, `0` for MAC), not by the
register number. `R26` is not part of the documented mapping.

| Register | Access | Field(s) | Notes |
|---|---|---|---|
| `R25` `CRC_CFG` | W (write all 4 bytes) | bit0 = `CRC32_ENABLE`, bit1 = `CRC_32B_NOT_EMPTY` (status of an internal 32-byte buffer; no documented read path, not modeled), bit2 = `CRC16_MOD_ENABLE` | Also auto-(re)initializes `CRC_SEED` to the mode default (`0x0000_0000` for CRC-16, `0xFFFF_FFFF` for CRC-32) and reloads the CRC accumulator from it |
| `R27` `CRC_DATA_8_BFLIP` | R | Same byte order as `CRC_DATA`, each byte's bits individually mirrored | No auto reset on read |
| `R28` `CRC_SEED` | W (always 4 bytes) | `[31:0]` seed value | Overrides the auto-set default; reloads the accumulator |
| `R28` `CRC_DATA_32_BFLIP` | R | Full 32-bit mirror of `CRC_DATA` (bit0↔bit31, etc.) | No auto reset; for CRC-16 only bits `[31:16]` are valid |
| `R29` `CRC_DATA` | W (1 / 2 / 4 bytes) | Pushes data through the engine | A session must use one fixed write width throughout |
| `R29` `CRC_DATA` | R | Current CRC value, LSB first | **Reading resets the accumulator back to the `CRC_SEED` state** — read it only once, after the last chunk of a session |

Unlike the raw RTL ports (§2–§4), there is no ready/busy or error status
exposed to the PRU here — no polling loop is needed. The TRM's programming
model instead uses a fixed NOP delay (see §6), putting the "use one write
width per session" discipline on firmware rather than on a hardware error
flag.

## 6. Sample Code

Programming model, straight from the TRM:

1. **(Optional) Configure the CRC type** — `XOUT` device `1`, base `R25`,
   size `1`, e.g. to enable CRC-32.
2. **(Optional) Override the seed** — `XOUT` device `1`, base `R28`,
   size `1`–`4`.
3. **For each chunk of data**: load it into `R29`, then `XOUT` device `1`,
   base `R29`, size `1`–`4` (matching whatever width the session started
   with).
4. **After the last chunk**, insert 1–2 `NOP`s, then `XIN` device `1`,
   base `R29`, size `4` to read the accumulated result. This read resets
   the accumulator back to the seed state, so do it once, at the end.

A runnable version of this (CRC-16 standard + CRC-32 over the same 8-byte
frame, verified through the MCP server) lives at `source/crc_example.asm`
/ `tests/test_crc_example.py`; the excerpt below is Pass 1 of that file
(the CRC-16, half-word-wide case):

```assembly
CRC_XID .set 1

crc16_pass:
    ldi   r25, 0x00            ; bit0=0 -> CRC-16, bit2=0 -> standard poly
    xout  CRC_XID, &r25, 1     ; CRC_CFG write -> also reloads seed/crc_reg=0x0000

    ldi   r29.w0, 0xADDE       ; frame[0:2] = DE AD
    xout  CRC_XID, &r29, 2     ; half-word write -> crc_byten=0011, write+run

    ldi   r29.w0, 0xEFBE       ; frame[2:4] = BE EF
    xout  CRC_XID, &r29, 2

    ldi   r29.w0, 0xFECA       ; frame[4:6] = CA FE
    xout  CRC_XID, &r29, 2

    ldi   r29.w0, 0xBEBA       ; frame[6:8] = BA BE
    xout  CRC_XID, &r29, 2
    nop
    nop

    xin   CRC_XID, &r29, 4     ; r29 = 16-bit CRC-16 result (destructive read)
    mov   r10, r29             ; save off before a later pass reuses r29
```

CRC-32 mode looks identical except for the `CRC_CFG` value (`0x01`) and
using 4-byte (`ldi32`) writes instead of 2-byte ones — see
`source/crc_example.asm` for the full two-pass listing.

> `CRC_DATA_8_BFLIP` (`R27`) and `CRC_DATA_32_BFLIP` (`R28` read side) are
> read-only mirrors of the same accumulator, pre-reversed at the byte or
> bit level so firmware that needs the conventional bit order for a given
> CRC standard doesn't have to reverse it in software — see §4's `bitswap`
> discussion for why the accumulator is stored LSB-first to begin with.
> Reading either mirror does **not** reset the accumulator.
>
> `source/crc_bitswap_example.asm` (tests: `tests/test_crc_bitswap_example.py`)
> reads both mirrors after a CRC-32 run, with the data pushed byte-wide and
> then 32-bit wide.
