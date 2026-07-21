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
        # The "2 consecutive zero bytes" EOF signal is deliberately
        # conservative -- 8 straight zero line-bits can never occur in
        # legitimate 8b/10b data (max run length is 5), so it never fires on
        # real payload/comma content. But it drains the FIFO fully before
        # checking, so `raw` typically holds a few extra idle-zero bytes past
        # the true end of transmission (TX halts to a spin loop after its 2
        # trailing commas, letting the wire settle back to 0). A trailing
        # zero *byte* run of length >= 1 is by the same argument always pure
        # post-EOF padding once it follows a >=2 confirmed run, so strip it;
        # otherwise decode_capture's fixed 10-bit symbol chunking can land an
        # all-zero remainder exactly on a symbol boundary and flag it as an
        # invalid (0x000 is not a legal codeword) symbol that never existed.
        captures.append(bytes(raw).rstrip(b"\x00"))
    return captures


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
