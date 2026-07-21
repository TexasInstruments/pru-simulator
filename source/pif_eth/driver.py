"""Drive the pif_eth PRU0 firmware on the simulator and recover the frames.

Loads the 8b/10b LUT + control block into DRAM0, runs the firmware one frame
("burst") at a time via the go-flag handshake, reconstructs each burst's serial
line from the perif channel's recorded transitions, 8b/10b-decodes it and
checks it against the golden reference.  Also renders the decoded frames to a
pcap for Wireshark.

Run directly to generate the traces::

    python3 source/pif_eth/driver.py            # 100 frames of each type
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "source"))

from simulator import Simulator                      # noqa: E402

from pif_eth import codec, frames as framelib          # noqa: E402
from pif_eth.crc32 import fcs_bytes                     # noqa: E402
from pif_eth.decoder import decode_bits                 # noqa: E402
from pif_eth.pcap import write_pcap                     # noqa: E402
from pif_eth.prng import DEFAULT_SEED, prng_bytes       # noqa: E402

# DRAM0 layout (see pif_eth_tx.asm)
LUT_ADDR = 0x0000
A_NUMF = 0x0400
A_MODE = 0x0404
A_SEED = 0x0408
A_PLEN = 0x040C
A_FCNT = 0x0410
A_BURST = 0x0414
A_GOFLAG = 0x0418
A_COREBUF = 0x0500

MODE_PRNG = 0
MODE_PRELOAD = 1

FIRMWARE = (_HERE / "pif_eth_tx.asm").read_text()
ASM_DIR = str(_HERE)


@dataclass
class FrameResult:
    index: int
    decoded: bytes
    expected: bytes
    invalid_symbols: int
    bit_errors: int

    @property
    def ok(self) -> bool:
        return self.decoded == self.expected and self.invalid_symbols == 0


@dataclass
class RunResult:
    kind: str
    frames: list[FrameResult] = field(default_factory=list)
    sim: object = None      # the Simulator, for memory/perif inspection in tests

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def total_bit_errors(self) -> int:
        return sum(f.bit_errors for f in self.frames)

    @property
    def total_bits(self) -> int:
        return sum(len(f.expected) * 8 for f in self.frames)

    @property
    def ber(self) -> float:
        return self.total_bit_errors / self.total_bits if self.total_bits else 0.0

    @property
    def all_ok(self) -> bool:
        return all(f.ok for f in self.frames)

    def l2_frames(self) -> list[bytes]:
        """Decoded frames with the 4-byte FCS stripped (for pcap)."""
        return [f.decoded[:-4] for f in self.frames]


def _ru32(sim: Simulator, addr: int) -> int:
    return int.from_bytes(sim.memory_read(addr, 4), "little")


def _wu32(sim: Simulator, addr: int, val: int) -> None:
    sim.memory.write(addr, (val & 0xFFFFFFFF).to_bytes(4, "little"))


def _bit_errors(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    errs = 8 * abs(len(a) - len(b))
    for i in range(n):
        errs += bin(a[i] ^ b[i]).count("1")
    return errs


def _capture_burst(sim: Simulator) -> list[int]:
    """Reconstruct the last burst's on-wire bit list from the perif channel."""
    ch = sim._perif["pru0"].channels[0]
    go = ch._go_ns
    period = ch.tx_clock_period_ns()
    nbits = _ru32(sim, A_BURST) * 8
    return [ch.tx_line_at(go + (k + 0.5) * period) for k in range(nbits)]


def run(kind: str, num_frames: int, *, seed: int = DEFAULT_SEED,
        max_steps_per_frame: int = 400_000) -> RunResult:
    """Transmit *num_frames* frames of *kind* ('bert' or 'udp') and decode them."""
    if kind == "bert":
        mode, payload_len, preload = MODE_PRNG, framelib.BERT_PAYLOAD_LEN, None
        expected_stream = prng_bytes(payload_len * num_frames, seed)
    elif kind == "udp":
        udp = framelib.build_udp_frame()
        mode, payload_len, preload = MODE_PRELOAD, len(udp.l2), udp.l2
    else:
        raise ValueError(f"unknown kind {kind!r}")

    sim = Simulator()
    sim.memory.write(LUT_ADDR, codec.build_dram0_lut())
    _wu32(sim, A_NUMF, num_frames)
    _wu32(sim, A_MODE, mode)
    _wu32(sim, A_SEED, seed)
    _wu32(sim, A_PLEN, payload_len)
    for a in (A_FCNT, A_BURST, A_GOFLAG):
        _wu32(sim, a, 0)
    if preload is not None:
        sim.memory.write(A_COREBUF, preload)

    errors = sim.load("pru0", FIRMWARE, include_paths=[ASM_DIR])
    if errors:
        raise RuntimeError(f"firmware load failed: {errors}")
    sim.step("pru0", 60)                       # run self-config, reach hs_wait

    dm = codec.build_decode_map()
    result = RunResult(kind=kind, sim=sim)
    for i in range(num_frames):
        _wu32(sim, A_GOFLAG, 1)                 # release one frame
        steps = 0
        while _ru32(sim, A_FCNT) != i + 1 and steps < max_steps_per_frame:
            sim.step("pru0", 500)
            steps += 500
        if _ru32(sim, A_FCNT) != i + 1:
            raise RuntimeError(f"frame {i} did not complete within step budget")

        bits = _capture_burst(sim)
        dec = decode_bits(bits, dm)
        decoded = dec.frames[0] if dec.frames else b""

        if kind == "bert":
            payload = expected_stream[i * payload_len:(i + 1) * payload_len]
        else:
            payload = preload
        expected = payload + fcs_bytes(payload)

        result.frames.append(FrameResult(
            index=i, decoded=decoded, expected=expected,
            invalid_symbols=dec.invalid_symbols,
            bit_errors=_bit_errors(decoded, expected)))
    return result


def main(argv: list[str]) -> int:
    num = int(argv[1]) if len(argv) > 1 else 100
    out_dir = _HERE / "traces"
    out_dir.mkdir(exist_ok=True)
    for kind in ("bert", "udp"):
        res = run(kind, num)
        pcap_path = out_dir / f"pif_eth_{kind}.pcap"
        write_pcap(str(pcap_path), res.l2_frames())
        print(f"{kind:>4}: {res.frame_count} frames, "
              f"bit errors={res.total_bit_errors}, BER={res.ber:.2e}, "
              f"all_ok={res.all_ok} -> {os.path.relpath(pcap_path, _ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
