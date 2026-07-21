# pif_eth PRU1 RX — Reference Layer, Harness and Option 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the host-side RX reference layer, the PRU0→PRU1 loopback harness, and the Option 1 RX firmware (realtime SOF/EOF + raw oversample capture, post-frame decode/CRC/BER in firmware), and measure the fastest line rate Option 1 sustains.

**Architecture:** A pure-Python golden reference (decode LUT + decimation + comma alignment) defines the exact behaviour PRU1 firmware must reproduce. A driver wires PRU0 TX to PRU1 RX over the zero-drift perif loopback and validates the clock ladder with a Python-armed RX *before* any firmware exists. Option 1 firmware is then built in three increments — realtime capture, post-frame decode, post-frame CRC/BER — each gated by tests against the reference.

**Tech Stack:** Python 3.12, pytest, PRU assembly (this repo's assembler), the in-repo perif model.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md`. All addresses, bit layouts and thresholds come from it verbatim.
- Both cores at 250 MHz: `pru_clock_mhz = 250`, `pru1_clock_mhz = 250`.
- Loopback channel 0 only, with `latency_ns = 0`, `jitter_ns = 0`, `drift_ppm = 0`.
- `n_rx = n_tx / 2` — only even TX dividers. Ladder is `n_tx ∈ {2, 4, 6, 8}`.
- `RXCFG = ((n_rx - 1) << 16) | 0x1F` (sample_size 7, sb_pol 1, clk_sel core).
- `TXCFG = ((n_tx - 1) << 16) | 0x10` (clk_sel core, frac 0).
- EOF is **two or more consecutive all-zero captured bytes**. One zero byte is never sufficient.
- PRU1 reaches its own DRAM (DRAM1) through **`c24`** at core-local `0x0000`; the simulator translates this per core (`_map_data_addr`). Host-side code uses GLOBAL addresses (DRAM1 base `0x2000`).
- **No sustained-rate claim may rest on a hand cycle-tally.** Every rate is confirmed by running the simulator and asserting `rx_ovf_count == 0`.
- DRAM1 map — firmware/core-local: LUT `0x0000`, capture `0x0800`, symbols `0x0C00`, frame `0x0E00`, stats `0x0F00`, control `0x0F40`. Host/global: add `0x2000` to each.

**Scope note:** Options 2 and 3 are deliberately excluded. Their task detail depends on the service-loop cycle cost this plan *measures* (Task 8); planning them now would be speculation. A second plan follows once Option 1's numbers are in.

---

### Task 1: Decode LUT builder

**Files:**
- Modify: `source/pif_eth/codec.py` (append after `build_dram0_lut`)
- Test: `tests/test_pif_eth.py`

**Interfaces:**
- Consumes: existing `codec.build_decode_map()`, `codec.COMMA_SYMBOLS`, `codec.encode_byte`, `codec.RD_MINUS`, `codec.RD_PLUS`
- Produces: `codec.symbol_disparity(code10: int) -> int` returning −2/0/+2; `codec.build_dram1_decode_lut() -> bytes` returning exactly 2048 bytes

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pif_eth.py`:

```python
def test_symbol_disparity_values():
    assert codec.symbol_disparity(codec.K28_5_RD_MINUS) == 2
    assert codec.symbol_disparity(codec.K28_5_RD_PLUS) == -2
    for b in range(256):
        for rd in (codec.RD_MINUS, codec.RD_PLUS):
            sym, _ = codec.encode_byte(b, rd)
            assert codec.symbol_disparity(sym) in (-2, 0, 2)


def _lut_entry(lut, code):
    return int.from_bytes(lut[code * 2:code * 2 + 2], "little")


def test_dram1_decode_lut_size_and_octets():
    lut = codec.build_dram1_decode_lut()
    assert len(lut) == 2048
    for b in range(256):
        for rd in (codec.RD_MINUS, codec.RD_PLUS):
            sym, _ = codec.encode_byte(b, rd)
            e = _lut_entry(lut, sym)
            assert e & 0xFF == b
            assert (e >> 8) & 1 == 1            # valid


def test_dram1_decode_lut_disparity_bits():
    """Neutral flag and resulting-RD bit must agree with the encoder."""
    lut = codec.build_dram1_decode_lut()
    for b in range(256):
        for rd in (codec.RD_MINUS, codec.RD_PLUS):
            sym, new_rd = codec.encode_byte(b, rd)
            e = _lut_entry(lut, sym)
            neutral = (e >> 9) & 1
            if codec.symbol_disparity(sym) == 0:
                assert neutral == 1
                assert new_rd == rd             # RD unchanged
            else:
                assert neutral == 0
                assert (e >> 10) & 1 == (1 if new_rd == codec.RD_PLUS else 0)


def test_dram1_decode_lut_commas_and_invalid():
    lut = codec.build_dram1_decode_lut()
    for sym in codec.COMMA_SYMBOLS:
        e = _lut_entry(lut, sym)
        assert (e >> 8) & 1 == 1                # valid
        assert (e >> 11) & 1 == 1               # is-comma
    # All-zeros is not a legal 8b/10b codeword.
    assert (_lut_entry(lut, 0) >> 8) & 1 == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pif_eth.py -k "disparity or dram1" -v`
Expected: FAIL with `AttributeError: module 'pif_eth.codec' has no attribute 'symbol_disparity'`

- [ ] **Step 3: Implement**

Append to `source/pif_eth/codec.py`:

```python
def symbol_disparity(code10: int) -> int:
    """Disparity (ones - zeros) of a 10-bit codeword: -2, 0 or +2."""
    return 2 * bin(code10 & 0x3FF).count("1") - 10


def build_dram1_decode_lut() -> bytes:
    """Build the 1024-entry (2 bytes each) DRAM1 decode table for PRU1 RX.

    A 10-bit codeword identifies its octet without knowing the running
    disparity, so one flat table suffices (``build_decode_map`` asserts the
    absence of collisions).  RD is needed only to *validate* the stream.

    Entry layout (little-endian u16) for codeword ``c``::

        [7:0]   decoded octet
        [8]     valid (1 = legal codeword)
        [9]     disparity-neutral (1 = disparity 0, RD unchanged)
        [10]    resulting RD when not neutral (0=negative, 1=positive)
        [11]    is-comma (K28.5)
    """
    dm = build_decode_map()
    buf = bytearray(1024 * 2)
    for code in range(1024):
        octet = dm.get(code)
        is_comma = code in COMMA_SYMBOLS
        word = 0
        if octet is not None or is_comma:
            word |= 1 << 8
            if octet is not None:
                word |= octet & 0xFF
            disp = symbol_disparity(code)
            if disp == 0:
                word |= 1 << 9
            else:
                word |= (1 if disp > 0 else 0) << 10
            if is_comma:
                word |= 1 << 11
        buf[code * 2:code * 2 + 2] = word.to_bytes(2, "little")
    return bytes(buf)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pif_eth.py -k "disparity or dram1" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add source/pif_eth/codec.py tests/test_pif_eth.py
git commit -m "feat(pif_eth): 8b/10b decode LUT builder for PRU1 RX"
```

---

### Task 2: Host-side RX reference helpers

**Files:**
- Create: `source/pif_eth/rx_reference.py`
- Test: `tests/test_pif_eth.py`

**Interfaces:**
- Consumes: `codec.COMMA_SYMBOLS`, `decoder.bits_to_symbols`, `decoder.decode_symbols`, `decoder.DecodeResult`
- Produces:
  - `samples_from_capture(raw: bytes) -> list[int]`
  - `decimate(samples: list[int], factor: int = 2) -> list[int]`
  - `find_comma_offset(bits: list[int]) -> int | None`
  - `decode_capture(raw: bytes, oversample: int = 2, decode_map: dict | None = None) -> DecodeResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pif_eth.py` (add `from pif_eth import rx_reference` to the imports at the top of the file):

```python
def _encode_burst(payload: bytes):
    """Build the on-wire bit list the TX firmware would produce for payload."""
    rd = codec.RD_MINUS
    syms = []
    for _ in range(4):                       # leading + 3 idle commas
        sym, rd = codec.encode_comma(rd)
        syms.append(sym)
    for b in payload:
        sym, rd = codec.encode_byte(b, rd)
        syms.append(sym)
    for _ in range(2):                       # trailing commas
        sym, rd = codec.encode_comma(rd)
        syms.append(sym)
    bits = []
    for s in syms:
        for i in range(9, -1, -1):
            bits.append((s >> i) & 1)
    return bits


def _bits_to_capture(bits, oversample=2, skew=0):
    """Oversample bits and pack into captured bytes, MSB-first, 8 samples each.

    *skew* drops leading samples to emulate the hardware start bit landing at
    an arbitrary sample phase.
    """
    samples = []
    for b in bits:
        samples.extend([b] * oversample)
    samples = samples[skew:]
    samples = samples[:len(samples) // 8 * 8]
    out = bytearray()
    for i in range(0, len(samples), 8):
        v = 0
        for s in samples[i:i + 8]:
            v = (v << 1) | s
        out.append(v)
    return bytes(out)


def test_samples_from_capture_is_msb_first():
    assert rx_reference.samples_from_capture(bytes([0b10110000])) == [1, 0, 1, 1, 0, 0, 0, 0]


@pytest.mark.parametrize("skew", [0, 1])
def test_decimate_is_phase_insensitive(skew):
    """At zero drift, decimation from either phase recovers the same bits."""
    bits = [1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 0]
    samples = []
    for b in bits:
        samples.extend([b, b])
    got = rx_reference.decimate(samples[skew:], 2)
    assert got[:len(bits) - 1] == bits[:len(bits) - 1]


@pytest.mark.parametrize("skew", [0, 1, 2, 3])
def test_decode_capture_recovers_payload_at_any_skew(skew):
    payload = bytes(range(64))
    raw = _bits_to_capture(_encode_burst(payload), oversample=2, skew=skew)
    res = rx_reference.decode_capture(raw, oversample=2)
    assert res.invalid_symbols == 0
    assert payload in res.frames


def test_find_comma_offset_returns_none_without_comma():
    assert rx_reference.find_comma_offset([0] * 100) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pif_eth.py -k "capture or decimate or comma_offset" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pif_eth.rx_reference'`

- [ ] **Step 3: Implement**

Create `source/pif_eth/rx_reference.py`:

```python
"""Host-side golden reference for the PRU1 RX path.

The perif RX FIFO captures the *raw* oversampled shift register: with
``sample_size = 7`` each captured byte is 8 consecutive line samples, MSB
first (oldest sample in bit 7).  At 2x oversampling that is 4 line bits.

Reconstruction is: expand captured bytes to samples, decimate by the
oversample factor, find the symbol boundary by locating a K28.5 comma, then
hand the aligned bit list to the existing 8b/10b decoder.

Decimation is *phase-insensitive* at zero clock drift: both samples of a bit
are identical, so sampling every Nth from any starting phase yields the same
bit sequence.  This does not hold under drift.
"""

from __future__ import annotations

from .codec import COMMA_SYMBOLS
from .decoder import DecodeResult, bits_to_symbols, decode_symbols


def samples_from_capture(raw: bytes) -> list[int]:
    """Expand captured FIFO bytes into raw line samples, oldest first."""
    out: list[int] = []
    for byte in raw:
        for i in range(7, -1, -1):
            out.append((byte >> i) & 1)
    return out


def decimate(samples: list[int], factor: int = 2) -> list[int]:
    """Reduce oversampled samples to line bits by taking every *factor*-th."""
    return samples[::factor]


def find_comma_offset(bits: list[int]) -> int | None:
    """Return the bit offset (0..9) that puts symbols on a comma boundary.

    Scans each candidate phase and returns the first whose symbol grid
    contains a K28.5 comma.  Returns None if no phase yields one.
    """
    for offset in range(10):
        for sym in bits_to_symbols(bits[offset:]):
            if sym in COMMA_SYMBOLS:
                return offset
    return None


def decode_capture(raw: bytes, oversample: int = 2,
                   decode_map: dict[int, int] | None = None) -> DecodeResult:
    """Full RX reconstruction: captured bytes -> decoded frames."""
    bits = decimate(samples_from_capture(raw), oversample)
    offset = find_comma_offset(bits)
    if offset is None:
        return DecodeResult()
    return decode_symbols(bits_to_symbols(bits[offset:]), decode_map)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_pif_eth.py -k "capture or decimate or comma_offset" -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add source/pif_eth/rx_reference.py tests/test_pif_eth.py
git commit -m "feat(pif_eth): host-side RX reconstruction reference"
```

---

### Task 3: Loopback harness and clock-ladder validation (no firmware)

Proves the loopback, the clock ladder and the reference layer all agree **before** any RX firmware exists, using a Python-armed RX. If this task fails, no amount of firmware work will help.

**Files:**
- Create: `source/pif_eth/rx_driver.py`
- Create: `config/memory_pif_eth_rx.cfg`
- Test: `tests/test_pif_eth_rx.py`

**Interfaces:**
- Consumes: `codec.build_dram0_lut`, `codec.build_dram1_decode_lut`, `rx_reference.decode_capture`, `simulator.Simulator`
- Produces:
  - constants `LUT1_ADDR, CAP_ADDR, SYM_ADDR, FRAME_ADDR, STATS_ADDR, CTRL_ADDR`, stats offsets `S_FRAMES, S_CAPBYTES, S_OVF, S_SYMERR, S_CRCOK, S_BITERR, S_TOTBITS, S_EOF`, control offsets `C_MODE, C_SEED, C_PLEN, C_GO, C_RXCFG`
  - `txcfg_word(n_tx: int) -> int`, `rxcfg_word(n_rx: int) -> int`, `tx_firmware_for(n_tx: int) -> str`
  - `build_sim(n_tx: int) -> Simulator` — configured, loopback enabled, TX loaded and past self-config
  - `capture_python_rx(sim, n_tx, num_frames=1) -> list[bytes]`

- [ ] **Step 1: Write the config file**

Create `config/memory_pif_eth_rx.cfg`:

```ini
[device]
target = AM243x
core_version = V4
pru_clock_mhz = 250
pru1_clock_mhz = 250

[DRAM0]
base = 0x00000000
size = 0x2000
read_latency = 2
write_latency = 1
jitter = 0

[DRAM1]
base = 0x00002000
size = 0x2000
read_latency = 2
write_latency = 1
jitter = 0

[ICSS_SHARED]
base = 0x00010000
size = 0x10000
read_latency = 2
write_latency = 1
jitter = 0

[MS_RAM]
base = 0x70000000
size = 0x80000
read_latency = 40
write_latency = 1
jitter = 10

[ICSS_CFG]
base = 0x00022000
size = 0x100
read_latency = 2
write_latency = 1
jitter = 0
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_pif_eth_rx.py`:

```python
"""pif_eth RX over the PRU0->PRU1 perif loopback."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from pif_eth import codec, rx_driver, rx_reference   # noqa: E402
from pif_eth.crc32 import fcs_bytes                   # noqa: E402
from pif_eth.prng import DEFAULT_SEED, prng_bytes     # noqa: E402


def test_divider_words():
    assert rx_driver.txcfg_word(2) == 0x00010010
    assert rx_driver.rxcfg_word(1) == 0x0000001F
    assert rx_driver.rxcfg_word(4) == 0x0003001F


def test_tx_firmware_selection_pins_skipchecks_to_n2():
    """_skipchecks is only valid at n_tx=2; it corrupts data anywhere else."""
    assert rx_driver.tx_firmware_for(2) == "pif_eth_tx_n2_skipchecks.asm"
    for n in (4, 6, 8):
        assert rx_driver.tx_firmware_for(n) == "pif_eth_tx_n2.asm"


@pytest.mark.parametrize("n_tx", [4, 6, 8])
def test_python_armed_rx_recovers_bert_frame(n_tx):
    """Loopback + clock ladder + reference layer agree, with no RX firmware.

    n_tx=2 is deliberately excluded: whether 125 Mbaud is reachable is the
    question Task 8's characterize() answers, and it reports FAIL rows without
    turning the suite red.  This test is a harness gate, not a rate claim.
    """
    sim = rx_driver.build_sim(n_tx)
    captures = rx_driver.capture_python_rx(sim, n_tx, num_frames=1)
    assert len(captures) == 1
    res = rx_reference.decode_capture(captures[0], oversample=2)
    assert res.invalid_symbols == 0
    payload = prng_bytes(128, DEFAULT_SEED)
    assert payload + fcs_bytes(payload) in res.frames
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pif_eth_rx.py -v`
Expected: FAIL with `ImportError: cannot import name 'rx_driver'`

- [ ] **Step 4: Implement the driver**

Create `source/pif_eth/rx_driver.py`:

```python
"""Drive PRU0 TX -> PRU1 RX over the perif loopback and recover frames.

The TX firmware self-configures TXCFG at startup, so the driver steps PRU0
past its prologue and then overrides TXCFG from the host to select a ladder
rung.  The RX firmware instead reads its RXCFG from the DRAM1 control block,
because the RX divider changes at every rung.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from simulator import Simulator                        # noqa: E402

from pif_eth import codec                               # noqa: E402
from pif_eth.prng import DEFAULT_SEED                   # noqa: E402

CONFIG_PATH = str(_ROOT / "config" / "memory_pif_eth_rx.cfg")

# --- DRAM0 (TX side, from pif_eth_tx.asm) ---------------------------------
LUT0_ADDR = 0x0000
T_NUMF, T_MODE, T_SEED, T_PLEN = 0x0400, 0x0404, 0x0408, 0x040C
T_FCNT, T_BURST, T_GOFLAG = 0x0410, 0x0414, 0x0418
T_COREBUF = 0x0500

# --- DRAM1 (RX side) ------------------------------------------------------
LUT1_ADDR = 0x2000
CAP_ADDR = 0x2800
SYM_ADDR = 0x2C00
FRAME_ADDR = 0x2E00
STATS_ADDR = 0x2F00
CTRL_ADDR = 0x2F40

S_FRAMES = STATS_ADDR + 0x00
S_CAPBYTES = STATS_ADDR + 0x04
S_OVF = STATS_ADDR + 0x08
S_SYMERR = STATS_ADDR + 0x0C
S_CRCOK = STATS_ADDR + 0x10
S_BITERR = STATS_ADDR + 0x14
S_TOTBITS = STATS_ADDR + 0x18
S_EOF = STATS_ADDR + 0x1C

C_MODE = CTRL_ADDR + 0x00
C_SEED = CTRL_ADDR + 0x04
C_PLEN = CTRL_ADDR + 0x08
C_GO = CTRL_ADDR + 0x0C
C_RXCFG = CTRL_ADDR + 0x10

TXCFG_PRU0 = 0x260E4
RXCFG_PRU1 = 0x26100

BERT_PAYLOAD_LEN = 128


def txcfg_word(n_tx: int) -> int:
    """TXCFG for divisor *n_tx*: clk_sel=core, frac=0, div_factor=n_tx-1."""
    return ((n_tx - 1) << 16) | 0x10


def rxcfg_word(n_rx: int) -> int:
    """RXCFG for divisor *n_rx*: sample_size=7, sb_pol=1, clk_sel=core."""
    return ((n_rx - 1) << 16) | 0x1F


def tx_firmware_for(n_tx: int) -> str:
    """Pick the TX firmware for a rung.

    pif_eth_tx_n2_skipchecks.asm drops two FIFO-full checks and is therefore
    hard-pinned to n_tx=2 -- at any other divider it silently produces wrong
    data.  Selection is derived from n_tx here so a caller cannot pair them
    incorrectly.
    """
    if n_tx == 2:
        return "pif_eth_tx_n2_skipchecks.asm"
    return "pif_eth_tx_n2.asm"


def _wu32(sim: Simulator, addr: int, val: int) -> None:
    sim.memory.write(addr, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def ru32(sim: Simulator, addr: int) -> int:
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def build_sim(n_tx: int, num_frames: int = 1, seed: int = DEFAULT_SEED,
              payload_len: int = BERT_PAYLOAD_LEN) -> Simulator:
    """Configured simulator: TX loaded and past self-config, loopback live."""
    if n_tx % 2:
        raise ValueError(f"n_tx must be even for exact 2x oversampling: {n_tx}")
    sim = Simulator(config_path=CONFIG_PATH)

    sim.memory.write(LUT0_ADDR, codec.build_dram0_lut())
    _wu32(sim, T_NUMF, num_frames)
    _wu32(sim, T_MODE, 0)                 # PRNG / BERT
    _wu32(sim, T_SEED, seed)
    _wu32(sim, T_PLEN, payload_len)
    for a in (T_FCNT, T_BURST, T_GOFLAG):
        _wu32(sim, a, 0)

    sim.memory.write(LUT1_ADDR, codec.build_dram1_decode_lut())

    sim.gpcfg_write("pru0", 1)
    sim.gpcfg_write("pru1", 1)
    sim.perif_loopback(0, True, latency_ns=0.0, jitter_ns=0.0, drift_ppm=0.0)

    fw = (_HERE / tx_firmware_for(n_tx)).read_text()
    errors = sim.load("pru0", fw)
    if errors:
        raise RuntimeError(f"TX firmware load failed: {errors}")

    # Run the TX prologue, then override its hardcoded TXCFG for this rung.
    sim.step("pru0", 60)
    sim.write_perif_register("pru0", TXCFG_PRU0, txcfg_word(n_tx))
    return sim


def capture_python_rx(sim: Simulator, n_tx: int, num_frames: int = 1,
                      max_steps: int = 4_000_000) -> list[bytes]:
    """Arm PRU1's RX channel from Python and capture raw oversample bytes.

    Validates the loopback and clock ladder before RX firmware exists.

    PRU1 has NO firmware at this point, so ``step_paced`` cannot be used: its
    inner loop is gated on the follow core having instructions, so with an
    empty program PRU1 never steps, its perif never advances, and RX captures
    nothing.  PRU0 is stepped in small batches instead and PRU1's perif is
    advanced manually from PRU0's cycle count -- the pattern established by
    tests/test_perif_drift_experiment.py.

    The batch must be short enough that the 4-deep RX FIFO cannot overflow
    between drains.  A byte is captured every ``8 * n_rx == 4 * n_tx`` core
    cycles, so four bytes span ``16 * n_tx`` cycles; half that is used, leaving
    margin for the gap between instruction count and true cycle count.
    """
    sim.write_perif_register("pru1", RXCFG_PRU1, rxcfg_word(n_tx // 2))
    ch = sim._perif["pru1"].channels[0]
    ch.arm_rx(True)
    rx_perif = sim.cores["pru1"].io_port.perif
    batch = max(1, 8 * n_tx)

    captures: list[bytes] = []
    for i in range(num_frames):
        _wu32(sim, T_GOFLAG, 1)
        raw = bytearray()
        zeros = 0
        steps = 0
        while steps < max_steps:
            sim.step("pru0", batch)
            steps += batch
            rx_perif.advance_cycles(sim.cores["pru0"].counters.cycles)
            if ch.rx_ovf:
                raise RuntimeError(
                    f"n_tx={n_tx}: RX FIFO overflowed between drains -- "
                    f"batch of {batch} instructions is too coarse")
            while ch.rx_valid:
                byte = ch.rx_head()
                ch.clr_val()
                raw.append(byte)
                zeros = zeros + 1 if byte == 0 else 0
            if zeros >= 2 and ru32(sim, T_FCNT) == i + 1:
                break
        if zeros < 2:
            raise RuntimeError(f"frame {i}: no EOF within step budget")
        captures.append(bytes(raw))
    return captures
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pif_eth_rx.py -v`
Expected: PASS (5 tests — 2 unit + 3 parametrized rungs)

All three rungs must pass. A failure here is a harness or reference-layer bug, not a rate limit: the Python-armed RX has no cycle budget of its own, and the batch size is chosen so the FIFO cannot overflow. If `RX FIFO overflowed between drains` is raised, reduce `batch`.

- [ ] **Step 6: Commit**

```bash
git add source/pif_eth/rx_driver.py config/memory_pif_eth_rx.cfg tests/test_pif_eth_rx.py
git commit -m "feat(pif_eth): PRU0->PRU1 RX loopback harness + clock-ladder validation"
```

---

### Task 4: Option 1 firmware — realtime capture with SOF/EOF

Realtime path only. Post-frame reconstruction lands in Tasks 5 and 6.

**Files:**
- Create: `source/pif_eth/pif_eth_rx_o1_raw.asm`
- Modify: `source/pif_eth/rx_driver.py`
- Test: `tests/test_pif_eth_rx.py`

**Interfaces:**
- Consumes: `build_sim`, `rxcfg_word`, `ru32`, DRAM1 constants from Task 3
- Produces: `rx_driver.run_rx(n_tx, option='o1', num_frames=1, seed=DEFAULT_SEED, payload_len=128) -> Simulator` — loads the RX firmware on pru1, runs *num_frames* frames, returns the sim for stats inspection

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pif_eth_rx.py`:

```python
def test_o1_firmware_captures_and_detects_eof():
    """Option 1 at a relaxed rung: capture matches the Python-armed reference."""
    sim = rx_driver.run_rx(8, option="o1", num_frames=1)
    assert rx_driver.ru32(sim, rx_driver.S_FRAMES) == 1
    assert rx_driver.ru32(sim, rx_driver.S_EOF) == 1        # clean EOF
    assert rx_driver.ru32(sim, rx_driver.S_OVF) == 0
    n = rx_driver.ru32(sim, rx_driver.S_CAPBYTES)
    assert n > 300                                          # BERT burst
    raw = sim.memory_read(rx_driver.CAP_ADDR, n)
    res = rx_reference.decode_capture(raw, oversample=2)
    assert res.invalid_symbols == 0
    payload = prng_bytes(128, DEFAULT_SEED)
    assert payload + fcs_bytes(payload) in res.frames


def test_o1_firmware_self_configures_rx():
    """RXCFG/GPCFG1 written by firmware, and RX samples at exactly 2x TX."""
    sim = rx_driver.run_rx(8, option="o1", num_frames=1)
    tx = sim._perif["pru0"].channels[0]
    rx = sim._perif["pru1"].channels[0]
    assert rx.regs.get_rx_sample_size() == 7
    assert rx.regs.get_rx_sb_pol() == 1
    assert rx.regs.get_rx_clk_sel() == 1
    assert rx.regs.get_rx_div_factor() == 3           # n_rx = 4 when n_tx = 8
    assert tx.tx_clock_period_ns() == pytest.approx(2 * rx.rx_clock_period_ns())


def test_o1_single_zero_byte_does_not_end_the_frame():
    """A legal 5-bit run yields an all-zero byte mid-frame; EOF needs two.

    Asserts the hazard was actually present in this capture -- otherwise the
    test would pass vacuously without ever exercising the 2-byte threshold.
    """
    sim = rx_driver.run_rx(8, option="o1", num_frames=1)
    n = rx_driver.ru32(sim, rx_driver.S_CAPBYTES)
    raw = sim.memory_read(rx_driver.CAP_ADDR, n)
    interior = raw[:-2]                              # drop the EOF pair
    assert 0 in interior, "capture contained no interior zero byte to test"
    assert rx_driver.ru32(sim, rx_driver.S_EOF) == 1
    assert n > 300                                   # did not stop early
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pif_eth_rx.py::test_o1_firmware_captures_and_detects_eof -v`
Expected: FAIL with `AttributeError: module 'pif_eth.rx_driver' has no attribute 'run_rx'`

- [ ] **Step 3: Write the firmware**

Create `source/pif_eth/pif_eth_rx_o1_raw.asm`:

```asm
; =============================================================
; pif_eth — Option 1 RX: realtime SOF/EOF + raw oversample capture (PRU1)
; =============================================================
; Channel 0 RX, 2x oversampled.  The realtime loop stores every captured FIFO
; byte verbatim; all reconstruction happens post-frame.
;
; SOF is the RX hardware's own start-bit: the line idles at 0 and sb_pol=1, so
; capture auto-starts on the burst's first 1 bit.  EOF is TWO consecutive
; all-zero captured bytes -- 16 zero samples.  One zero byte is NOT enough:
; 8b/10b allows a 5-bit run, which at 2x oversampling is 10 zero samples and
; can fill a single byte.
;
; The zero-run check is a second copy of the loop body rather than a counter
; reset, to keep the hot path at 7 instructions.
;
; NOTE: PRU1 sees its OWN DRAM (DRAM1) at core-local 0x0000 and DRAM0 at
; 0x2000, so c24 is its own DRAM -- matching AM243x ICSSG silicon.  All
; addresses below are CORE-LOCAL; the host sees the same bytes at global
; 0x2000 + offset (DRAM1 base).  See core/pru_core.py _map_data_addr.
; DRAM1 map (CORE-LOCAL addresses; host adds 0x2000):
;   0x0000  8b/10b decode LUT, 1024 x u16 (c24 offset 0 = own DRAM)
;   0x0800  raw oversample capture buffer
;   0x0E00  reconstructed frame buffer
;   0x0F00  stats:  +0 frames  +4 cap_bytes  +8 ovf  +12 sym_err
;                   +16 crc_ok +20 bit_err   +24 tot_bits +28 eof_status
;   0x0F40  control: +0 mode  +4 seed  +8 payload_len  +12 go  +16 rxcfg
;
; Persistent registers:
;   r1 capture ptr   r5 FIFO-pop cmd   r8 payload_len   r9 core_len
;   r13 seed         r14 frame counter  r18 mode
; =============================================================

start:
        ldi  r0, 0x0000             ; GPCFG1 mux_sel=1 (perif mode)
        ldi  r0.w2, 0x0400
        ldi  r1, 0x600C
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4

        ldi  r2, 0x0F40             ; control block
        lbbo r0, r2, 16, 4          ; rxcfg (host-supplied, encodes n_rx)
        ldi  r1, 0x6100
        ldi  r1.w2, 0x0002
        sbbo r0, r1, 0, 4           ; RXCFG @ 0x26100

        lbbo r18, r2, 0, 4          ; mode
        lbbo r13, r2, 4, 4          ; seed
        lbbo r8,  r2, 8, 4          ; payload_len
        add  r9, r8, 4              ; core_len = payload_len + 4

        ldi  r5, 0                  ; R31 bit24 = clr_val ch0 (FIFO pop)
        ldi  r5.w2, 0x0100
        ldi  r14, 0                 ; frame counter

frame_loop:
        ldi  r2, 0x0F40
go_wait:
        lbbo r6, r2, 12, 4          ; go flag
        qbeq go_wait, r6, 0
        ldi  r0, 0
        sbbo r0, r2, 12, 4          ; clear go

        ldi  r30.b3, 0x01           ; arm RX ch0 -> SOF on first 1 sample
        ldi  r1, 0x0800             ; capture pointer

; --- realtime capture loop: 7 instructions on the hot path ---
poll:
        qbbc poll, r31, 24          ; wait ch0 rx_valid
        and  r4, r31, 0xFF          ; FIFO head byte
        mov  r31, r5                ; pop FIFO
        sbbo r4, r1, 0, 1           ; store raw oversample byte
        add  r1, r1, 1
        qbeq zrun, r4, 0            ; zero byte -> candidate EOF
        jmp  poll
zrun:
        qbbc zrun, r31, 24          ; second byte, same body
        and  r4, r31, 0xFF
        mov  r31, r5
        sbbo r4, r1, 0, 1
        add  r1, r1, 1
        qbeq eof, r4, 0             ; two zero bytes in a row -> EOF
        jmp  poll

eof:
        ldi  r6, 0                  ; sample rx_ovf BEFORE disarming
        qbbc eo_novf, r31, 27       ; ch0 rx_ovf
        ldi  r6, 1
eo_novf:
        ldi  r30.b3, 0x00           ; disarm RX
        ldi  r0, 0
        ldi  r0.w2, 0x0900          ; clr_val(24) | clr_ovf(27)
        mov  r31, r0

        ldi  r3, 0x0F00             ; stats block
        ldi  r0, 0x0800
        sub  r2, r1, r0             ; captured_bytes = ptr - base
        sbbo r2, r3, 4, 4
        sbbo r6, r3, 8, 4           ; rx_ovf
        ldi  r0, 1
        sbbo r0, r3, 28, 4          ; eof_status = 1 (clean EOF)
        add  r14, r14, 1
        sbbo r14, r3, 0, 4          ; frame counter
        jmp  frame_loop
```

- [ ] **Step 4: Add `run_rx` to the driver**

Append to `source/pif_eth/rx_driver.py`:

```python
RX_FIRMWARE = {
    "o1": "pif_eth_rx_o1_raw.asm",
}


def run_rx(n_tx: int, option: str = "o1", num_frames: int = 1,
           seed: int = DEFAULT_SEED, payload_len: int = BERT_PAYLOAD_LEN,
           max_steps: int = 4_000_000) -> Simulator:
    """Run *num_frames* frames through PRU0 TX -> PRU1 RX firmware."""
    sim = build_sim(n_tx, num_frames=num_frames, seed=seed,
                    payload_len=payload_len)

    _wu32(sim, C_MODE, 0)
    _wu32(sim, C_SEED, seed)
    _wu32(sim, C_PLEN, payload_len)
    _wu32(sim, C_GO, 0)
    _wu32(sim, C_RXCFG, rxcfg_word(n_tx // 2))
    for a in (S_FRAMES, S_CAPBYTES, S_OVF, S_SYMERR,
              S_CRCOK, S_BITERR, S_TOTBITS, S_EOF):
        _wu32(sim, a, 0)

    fw = (_HERE / RX_FIRMWARE[option]).read_text()
    errors = sim.load("pru1", fw)
    if errors:
        raise RuntimeError(f"RX firmware load failed: {errors}")
    sim.step("pru1", 20)                  # RX prologue, reach go_wait

    for i in range(num_frames):
        _wu32(sim, C_GO, 1)
        _wu32(sim, T_GOFLAG, 1)
        steps = 0
        while steps < max_steps and ru32(sim, S_FRAMES) != i + 1:
            sim.step_paced("pru0", "pru1", 500)
            steps += 500
        if ru32(sim, S_FRAMES) != i + 1:
            raise RuntimeError(f"frame {i} did not complete within step budget")
    return sim
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pif_eth_rx.py::test_o1_firmware_captures_and_detects_eof -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add source/pif_eth/pif_eth_rx_o1_raw.asm source/pif_eth/rx_driver.py tests/test_pif_eth_rx.py
git commit -m "feat(pif_eth): Option 1 RX firmware - realtime capture with SOF/EOF"
```

---

### Task 5: Option 1 post-frame decode

Adds comma alignment, decimation and LUT decode in PRU1 firmware, producing the reconstructed frame in DRAM1 and counting symbol errors.

**Files:**
- Modify: `source/pif_eth/pif_eth_rx_o1_raw.asm`
- Test: `tests/test_pif_eth_rx.py`

**Interfaces:**
- Consumes: capture buffer + `S_CAPBYTES` from Task 4; decode LUT at `c24` offset 0 (own DRAM)
- Produces: reconstructed octets at `FRAME_ADDR` (length `core_len`), `S_SYMERR` populated

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pif_eth_rx.py`:

```python
def test_o1_firmware_reconstructs_frame_in_dram():
    sim = rx_driver.run_rx(8, option="o1", num_frames=1)
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    payload = prng_bytes(128, DEFAULT_SEED)
    expected = payload + fcs_bytes(payload)
    got = sim.memory_read(rx_driver.FRAME_ADDR, len(expected))
    assert got == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pif_eth_rx.py::test_o1_firmware_reconstructs_frame_in_dram -v`
Expected: FAIL — `FRAME_ADDR` still holds zeros, so `got != expected`

- [ ] **Step 3: Implement the post-frame decode**

In `source/pif_eth/pif_eth_rx_o1_raw.asm`, append these routines at the end of the file (the `eo_novf` block is rewritten in Step 3b below to call them):

```asm
; -------------------------------------------------------------
; post_frame: capture buffer -> decoded octets at 0x0E00.  ret r29
;
; Walks the captured bytes as a bit stream, taking every 2nd sample
; (phase-insensitive at zero drift), assembling 10-bit symbols and
; decoding them through the LUT.  The symbol grid is anchored on the
; first comma found; octets before that are discarded as pre-alignment.
;
;   r1 cap ptr   r2 cap_bytes   r7 out ptr   r10 rd(0=neg,1=pos)
;   r11 bit acc  r12 nbits      r15 sym_err  r16 aligned flag
;   r19 sample toggle           r20 byte     r21 bit index
;   r22 symbol   r23 LUT entry  r24 LUT offset
; -------------------------------------------------------------
post_frame:
        ldi  r1, 0x0800
        ldi  r3, 0x0F00
        lbbo r2, r3, 4, 4           ; captured_bytes
        ldi  r7, 0x0E00             ; frame output pointer
        ldi  r10, 0                 ; rd = negative
        ldi  r11, 0                 ; bit accumulator
        ldi  r12, 0                 ; nbits held
        ldi  r15, 0                 ; symbol errors
        ldi  r16, 0                 ; aligned = false
        ldi  r19, 0                 ; sample toggle (take every 2nd)

pf_byte:
        qbeq pf_done, r2, 0
        lbbo r20, r1, 0, 1          ; one captured byte = 8 samples
        add  r1, r1, 1
        sub  r2, r2, 1
        ldi  r21, 7                 ; MSB = oldest sample

pf_bit:
        xor  r19, r19, 1            ; toggle; take the sample when it is 1
        qbeq pf_bit_next, r19, 0
        lsr  r6, r20, r21
        and  r6, r6, 1
        lsl  r11, r11, 1
        or   r11, r11, r6
        add  r12, r12, 1
        qblt pf_bit_next, r12, 9    ; nbits < 10 -> keep filling
        jal  r28, pf_symbol
pf_bit_next:
        qbeq pf_byte, r21, 0
        sub  r21, r21, 1
        jmp  pf_bit

pf_done:
        sbbo r15, r3, 12, 4         ; publish symbol_errors
        jmp  r29

; -------------------------------------------------------------
; pf_symbol: consume the 10 bits in r11 as one symbol.  ret r28
; -------------------------------------------------------------
pf_symbol:
        ldi  r6, 0x03FF
        and  r22, r11, r6           ; 10-bit symbol
        ldi  r12, 0                 ; reset bit count
        lsl  r24, r22, 1            ; LUT offset = symbol * 2
        lbco r23, c24, r24, 2       ; decode entry (c24 = own DRAM = DRAM1)

        qbbc ps_bad, r23, 8         ; valid?
        qbbs ps_comma, r23, 11      ; comma -> alignment anchor

        qbeq ps_skip, r16, 0        ; not aligned yet -> discard octet
        and  r6, r23, 0xFF
        sbbo r6, r7, 0, 1           ; store decoded octet
        add  r7, r7, 1

        qbbs ps_rd_done, r23, 9     ; neutral -> RD unchanged
        lsr  r6, r23, 10
        and  r6, r6, 1
        qbne ps_rd_ok, r6, r10      ; must flip RD, else violation
        add  r15, r15, 1
ps_rd_ok:
        mov  r10, r6
ps_rd_done:
        jmp  r28

ps_comma:
        ldi  r16, 1                 ; symbol grid is now anchored
        lsr  r6, r23, 10
        and  r6, r6, 1
        mov  r10, r6                ; comma always flips RD
        jmp  r28

ps_bad:
        qbeq ps_skip, r16, 0        ; pre-alignment garbage is expected
        add  r15, r15, 1
ps_skip:
        jmp  r28
```

- [ ] **Step 3b: Rewrite the `eof` block tail to run post-processing**

In the same file, replace everything from `ldi r0, 1` through `jmp frame_loop`
at the end of the `eo_novf` block with:

```asm
        ldi  r0, 1
        sbbo r0, r3, 28, 4          ; eof_status = 1 (clean EOF)
        jal  r29, post_frame
        add  r14, r14, 1
        ldi  r3, 0x0F00
        sbbo r14, r3, 0, 4          ; frame counter published LAST, so the
        jmp  frame_loop             ; host never sees a half-written stats block
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pif_eth_rx.py -v`
Expected: PASS (all tests)

The symbol grid is anchored on the first comma, so `find_comma_offset`'s host-side result and the firmware's anchor must agree; if `S_SYMERR` is non-zero, compare the firmware's frame buffer against `rx_reference.decode_capture` output on the same capture bytes to localise the divergence.

- [ ] **Step 5: Commit**

```bash
git add source/pif_eth/pif_eth_rx_o1_raw.asm tests/test_pif_eth_rx.py
git commit -m "feat(pif_eth): Option 1 post-frame 8b/10b decode into DRAM1"
```

---

### Task 6: Extract the CRC-32 routine into a shared include

TX and RX both need the same reflected CRC-32 inner loop. The assembler
supports `.include`, so the loop is extracted once rather than copied. This
task changes only *where* the code lives — TX behaviour must be unchanged,
which the existing TX tests prove.

**Files:**
- Create: `source/pif_eth/pif_eth_crc32.inc`
- Modify: `source/pif_eth/pif_eth_tx.asm` (replace the `crc32_compute` body, append the include)
- Modify: `source/pif_eth/driver.py`
- Modify: `source/pif_eth/rx_driver.py`
- Modify: `mcp_server/server.py:18-22`
- Test: `tests/test_pif_eth.py:154`, `tests/test_pif_eth.py:211`

**Interfaces:**
- Produces: `crc32_core` — entry `r21` = buffer base, `r8` = byte count; exit `r20` = final CRC (already inverted), `r21` = base + count; clobbers `r22`–`r25`; returns via **`r26`**
- Produces: `driver.ASM_DIR: str` — the directory to pass as `include_paths`
- Produces: `MCPServer.pru_load(source, core="pru0", include_paths=None)`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pif_eth.py`:

```python
def test_mcp_pru_load_accepts_include_paths():
    """The MCP wrapper must be able to load multi-file assembly."""
    import inspect
    from mcp_server.server import PRUSimulatorMCP
    assert "include_paths" in inspect.signature(PRUSimulatorMCP.pru_load).parameters


def test_tx_firmware_uses_shared_crc32_include():
    src = (Path(driver.ASM_DIR) / "pif_eth_tx.asm").read_text()
    assert ".include" in src and "pif_eth_crc32.inc" in src
    # The inner loop must live in exactly one place.
    assert "0xEDB8" not in src
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_pif_eth.py -k "include" -v`
Expected: FAIL — `include_paths` missing from the signature, and `AttributeError: module 'pif_eth.driver' has no attribute 'ASM_DIR'`

- [ ] **Step 3: Create the shared include**

Create `source/pif_eth/pif_eth_crc32.inc`:

```asm
; =============================================================
; crc32_core — reflected CRC-32 (poly 0xEDB88320, init/final 0xFFFFFFFF)
; Shared by pif_eth_tx.asm and the pif_eth RX firmware.
; =============================================================
; Entry:  r21 = buffer base address, r8 = byte count
; Exit:   r20 = final CRC (already inverted), r21 = base + count
;         (so the caller can read/append the 4 FCS octets at r21)
; Clobbers: r22, r23, r24, r25
; Returns via r26.
;
; MUST be included at the END of a program: it is straight-line code and
; would execute at startup if pasted before the entry point.
; =============================================================
crc32_core:
        ldi  r20, 0xFFFF
        ldi  r20.w2, 0xFFFF        ; crc = 0xFFFFFFFF
        ldi  r22, 0
c3_byte:
        qble c3_done, r22, r8      ; i >= count
        lbbo r23, r21, 0, 1
        xor  r20, r20, r23         ; crc ^= byte
        ldi  r24, 0
c3_bit:
        qble c3_bitend, r24, 8     ; j >= 8
        and  r25, r20, 1
        qbeq c3_noxor, r25, 0
        lsr  r20, r20, 1
        ldi  r25, 0x8320
        ldi  r25.w2, 0xEDB8        ; poly 0xEDB88320
        xor  r20, r20, r25
        jmp  c3_bitnext
c3_noxor:
        lsr  r20, r20, 1
c3_bitnext:
        add  r24, r24, 1
        jmp  c3_bit
c3_bitend:
        add  r21, r21, 1
        add  r22, r22, 1
        jmp  c3_byte
c3_done:
        not  r20, r20              ; final XOR 0xFFFFFFFF
        jmp  r26
```

- [ ] **Step 4: Rewrite `crc32_compute` in `pif_eth_tx.asm`**

Replace the whole `crc32_compute` routine (from the `crc32_compute:` label through its `jmp r29`, including the `cc_*` labels) with:

```asm
; -------------------------------------------------------------
; crc32_compute: CRC-32 over payload_len bytes at 0x0500, append the
;   4 FCS bytes little-endian at 0x0500+payload_len. ret r29
;   The inner loop lives in pif_eth_crc32.inc (shared with the RX firmware).
;   r26 is free here: it is only used as push_symbol's return register
;   inside send_frame, which runs after this.
; -------------------------------------------------------------
crc32_compute:
        ldi  r21, 0x0500
        jal  r26, crc32_core       ; -> r20 = FCS, r21 = 0x0500+payload_len
        sbbo r20, r21, 0, 4        ; append FCS (little-endian)
        jmp  r29
```

Then append as the **last line** of `pif_eth_tx.asm`:

```asm
        .include "pif_eth_crc32.inc"
```

- [ ] **Step 5: Thread `include_paths` through the loaders**

In `source/pif_eth/driver.py`, add next to the `FIRMWARE` definition:

```python
ASM_DIR = str(_HERE)
```

and change the load call to:

```python
    errors = sim.load("pru0", FIRMWARE, include_paths=[ASM_DIR])
```

In `source/pif_eth/rx_driver.py`, change both load calls:

```python
    errors = sim.load("pru0", fw, include_paths=[str(_HERE)])
```
```python
    errors = sim.load("pru1", fw, include_paths=[str(_HERE)])
```

In `mcp_server/server.py`, replace `pru_load`:

```python
    def pru_load(self, source: str, core: str = "pru0",
                 include_paths: list[str] | None = None) -> dict:
        """Parse and load assembly source into a PRU core."""
        errors = self.sim.load(core, source, include_paths)
        line_count = len([l for l in source.split('\n') if l.strip()])
        return {"success": len(errors) == 0, "errors": errors, "line_count": line_count}
```

In `tests/test_pif_eth.py`, update the two existing load sites:

```python
    assert sim.load("pru0", driver.FIRMWARE, include_paths=[driver.ASM_DIR]) == []
```
```python
    assert mcp.pru_load(driver.FIRMWARE, core="pru0",
                        include_paths=[driver.ASM_DIR])["success"]
```

- [ ] **Step 6: Verify TX behaviour is unchanged**

Run: `python3 -m pytest tests/test_pif_eth.py -q`
Expected: all PASS — including the existing zero-BER round-trip and MCP tests. Any BER regression means the register contract in the include is wrong.

Run: `python3 -m pytest tests/test_pif_eth_rx.py -q`
Expected: all PASS (rx_driver loads TX firmware too)

- [ ] **Step 7: Commit**

```bash
git add source/pif_eth/pif_eth_crc32.inc source/pif_eth/pif_eth_tx.asm \
        source/pif_eth/driver.py source/pif_eth/rx_driver.py \
        mcp_server/server.py tests/test_pif_eth.py
git commit -m "refactor(pif_eth): share CRC-32 routine between TX and RX via .include"
```

---

### Task 7: Option 1 post-frame CRC32 and PRNG BER

**Files:**
- Modify: `source/pif_eth/pif_eth_rx_o1_raw.asm`
- Test: `tests/test_pif_eth_rx.py`

**Interfaces:**
- Consumes: reconstructed frame at `FRAME_ADDR`, `payload_len` (r8), `core_len` (r9), `seed` (r13) from Task 5
- Produces: `S_CRCOK` (1 = FCS matched), `S_BITERR`, `S_TOTBITS` populated

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pif_eth_rx.py`:

```python
def test_o1_firmware_validates_crc_and_ber():
    sim = rx_driver.run_rx(8, option="o1", num_frames=3)
    assert rx_driver.ru32(sim, rx_driver.S_FRAMES) == 3
    assert rx_driver.ru32(sim, rx_driver.S_CRCOK) == 1
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_BITERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_TOTBITS) == 128 * 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_pif_eth_rx.py::test_o1_firmware_validates_crc_and_ber -v`
Expected: FAIL with `assert 0 == 1` on `S_CRCOK`

- [ ] **Step 3: Implement**

In `source/pif_eth/pif_eth_rx_o1_raw.asm`, change `pf_done` to chain into the checks:

```asm
pf_done:
        sbbo r15, r3, 12, 4         ; publish symbol_errors
        jal  r28, rx_crc_check
        jal  r28, rx_ber_check
        jmp  r29
```

and append:

```asm
; -------------------------------------------------------------
; rx_crc_check: CRC-32 over the reconstructed payload at 0x0E00,
;   compared against the 4 received FCS octets.  ret r28
;   The inner loop is the shared crc32_core from pif_eth_crc32.inc.
;   r26 is free here (post_frame uses r28/r29 for returns).
; -------------------------------------------------------------
rx_crc_check:
        ldi  r21, 0x0E00
        jal  r26, crc32_core        ; -> r20 = computed FCS, r21 = end of payload
        lbbo r25, r21, 0, 4         ; received FCS (little-endian)
        ldi  r6, 0
        qbne rc_store, r20, r25
        ldi  r6, 1                  ; match
rc_store:
        sbbo r6, r3, 16, 4          ; crc_ok
        jmp  r28

; -------------------------------------------------------------
; rx_ber_check: regenerate the BERT payload with the same xorshift32
;   seed and count differing bits against the received payload.  ret r28
;   Skipped (counters zeroed) when mode != 0.
;   r20 prng state  r21 ptr  r22 i  r23 rx byte  r24 xor  r25 tmp
;   r17 bit errors
; -------------------------------------------------------------
rx_ber_check:
        ldi  r17, 0
        ldi  r6, 0
        qbne rb_publish, r18, 0     ; mode != 0 -> not a BERT frame
        mov  r20, r13               ; PRNG state = seed
        ldi  r21, 0x0E00
        ldi  r22, 0
rb_byte:
        qble rb_bits, r22, r8       ; i >= payload_len
        lsl  r25, r20, 13           ; xorshift32
        xor  r20, r20, r25
        lsr  r25, r20, 17
        xor  r20, r20, r25
        lsl  r25, r20, 5
        xor  r20, r20, r25
        lbbo r23, r21, 0, 1
        and  r24, r20, 0xFF
        xor  r24, r24, r23          ; differing bits in this octet
        ldi  r6, 0
rb_pop:
        qbeq rb_popdone, r24, 0
        and  r25, r24, 1
        add  r17, r17, r25
        lsr  r24, r24, 1
        jmp  rb_pop
rb_popdone:
        add  r21, r21, 1
        add  r22, r22, 1
        jmp  rb_byte
rb_bits:
        mov  r13, r20               ; persist PRNG state -> next frame continues
        lsl  r6, r8, 3              ; total_bits = payload_len * 8
rb_publish:
        sbbo r17, r3, 20, 4         ; prng_bit_errors
        sbbo r6, r3, 24, 4          ; total_bits_checked
        jmp  r28
```

Finally, append as the **last line** of `pif_eth_rx_o1_raw.asm`:

```asm
        .include "pif_eth_crc32.inc"
```

`r13` starts as the seed from the control block and is written back after each
frame's `payload_len` draws, so frame *n* checks against the same PRNG
sub-stream PRU0 transmitted. Regenerating from the seed every frame would
match only frame 0 — which is exactly what the 3-frame test in Step 1 catches.

Stats are **overwritten per frame**, not accumulated: `S_BITERR` and
`S_TOTBITS` describe the most recent frame, which is why the 3-frame test
asserts `S_TOTBITS == 128 * 8` rather than `3 * 128 * 8`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_pif_eth_rx.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add source/pif_eth/pif_eth_rx_o1_raw.asm tests/test_pif_eth_rx.py
git commit -m "feat(pif_eth): Option 1 post-frame CRC32 + PRNG BER validation"
```

---

### Task 8: Characterize Option 1 on the ladder and document

Measures the fastest rung Option 1 sustains, pins it as a regression constant, and writes up the result.

**Files:**
- Modify: `source/pif_eth/rx_driver.py`
- Modify: `tests/test_pif_eth_rx.py`
- Modify: `source/pif_eth/README.md`
- Modify: `docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md`

**Interfaces:**
- Consumes: `run_rx` from Task 4
- Produces: `rx_driver.RATED_DIVIDER: dict[str, int]`; `rx_driver.characterize(option: str) -> list[dict]`; a `main()` CLI entry point

- [ ] **Step 1: Add the characterization helper**

Append to `source/pif_eth/rx_driver.py`:

```python
LADDER = [2, 4, 6, 8]

# Smallest n_tx each option is MEASURED to run clean at (Task 8).
# Filled in from characterize() output -- never from a hand cycle-tally.
RATED_DIVIDER: dict[str, int] = {}


def characterize(option: str = "o1", num_frames: int = 2) -> list[dict]:
    """Run *option* at every ladder rung and report which ones stay clean."""
    from pif_eth.crc32 import fcs_bytes
    from pif_eth.prng import prng_bytes

    rows = []
    for n_tx in LADDER:
        row = {"option": option, "n_tx": n_tx,
               "baud_mhz": 250.0 / n_tx, "ok": False, "error": None}
        try:
            sim = run_rx(n_tx, option=option, num_frames=num_frames)
            payload = prng_bytes(BERT_PAYLOAD_LEN, DEFAULT_SEED)
            expected = payload + fcs_bytes(payload)
            # Strip trailing idle-zero bytes before decoding a firmware capture,
            # exactly as capture_python_rx does. The firmware's reported length
            # already drops one EOF byte, but that only avoids a spurious tail
            # symbol by bit-count arithmetic; rstrip makes it robust by
            # construction (a real symbol always ends with a '1' sample).
            row.update(
                ovf=ru32(sim, S_OVF),
                sym_err=ru32(sim, S_SYMERR),
                bit_err=ru32(sim, S_BITERR),
                crc_ok=ru32(sim, S_CRCOK),
                frames=ru32(sim, S_FRAMES),
            )
            row["ok"] = (row["ovf"] == 0 and row["sym_err"] == 0
                         and row["bit_err"] == 0 and row["crc_ok"] == 1
                         and row["frames"] == num_frames
                         and sim.memory_read(FRAME_ADDR, len(expected)) == expected)
        except Exception as exc:                       # noqa: BLE001
            row["error"] = str(exc)
        rows.append(row)
    return rows


def main(argv: list[str]) -> int:
    option = argv[1] if len(argv) > 1 else "o1"
    print(f"{'opt':>4} {'n_tx':>5} {'Mbaud':>8} {'ovf':>5} {'symerr':>7} "
          f"{'biterr':>7} {'crc':>4}  result")
    for r in characterize(option):
        if r["error"]:
            print(f"{r['option']:>4} {r['n_tx']:>5} {r['baud_mhz']:>8.2f} "
                  f"{'-':>5} {'-':>7} {'-':>7} {'-':>4}  FAIL ({r['error']})")
        else:
            print(f"{r['option']:>4} {r['n_tx']:>5} {r['baud_mhz']:>8.2f} "
                  f"{r['ovf']:>5} {r['sym_err']:>7} {r['bit_err']:>7} "
                  f"{r['crc_ok']:>4}  {'PASS' if r['ok'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 2: Run the characterization and record the real numbers**

Run: `python3 source/pif_eth/rx_driver.py o1`
Expected: a four-row table. Record the actual output — **do not assume** which rungs pass. The fastest rung with `PASS` is Option 1's rated divider.

- [ ] **Step 3: Pin the measured rated divider**

Set `RATED_DIVIDER` in `source/pif_eth/rx_driver.py` to the measured value, e.g. if `n_tx=4` was the fastest passing rung:

```python
RATED_DIVIDER: dict[str, int] = {"o1": 4}
```

- [ ] **Step 4: Add the regression test**

Append to `tests/test_pif_eth_rx.py`:

```python
def test_o1_clean_at_rated_divider():
    """Pins the measured rating from Task 8 so a regression is visible."""
    n_tx = rx_driver.RATED_DIVIDER["o1"]
    sim = rx_driver.run_rx(n_tx, option="o1", num_frames=2)
    assert rx_driver.ru32(sim, rx_driver.S_OVF) == 0
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_BITERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_CRCOK) == 1
```

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/test_pif_eth.py tests/test_pif_eth_rx.py -q`
Expected: all PASS

Then confirm nothing else regressed: `python3 -m pytest -q`

- [ ] **Step 6: Document the measured results**

Add a "PRU1 RX" section to `source/pif_eth/README.md` with the measured ladder table, and append a "Measured results" section to `docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md` recording Option 1's rated divider and, if `n_tx=2` failed, the observed failure mode (`rx_ovf`, symbol errors, or capture-buffer overrun).

- [ ] **Step 7: Commit**

```bash
git add source/pif_eth/rx_driver.py tests/test_pif_eth_rx.py \
        source/pif_eth/README.md \
        docs/superpowers/specs/2026-07-21-pif-eth-pru1-rx-design.md
git commit -m "feat(pif_eth): characterize Option 1 RX across the rate ladder"
```

---

## Follow-on

Once Option 1's numbers are known, a second plan covers Option 2 (`pif_eth_rx_o2_bits.asm`, realtime 2→1 decimation into packed 10-bit symbols) and Option 3 (`pif_eth_rx_o3_decode.asm`, realtime LUT decode). Both reuse `post_frame`'s CRC/BER routines and the `characterize()` harness; their realistic target rungs depend on the service-loop cost measured here.
