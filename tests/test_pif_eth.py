"""pif_eth: 8b/10b line-coded Ethernet TX over the PRU perif (PRU0, ch0)."""
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "source"))

from pif_eth import codec, crc32, prng, frames, rx_reference  # noqa: E402
from pif_eth.decoder import decode_symbols       # noqa: E402


# --------------------------------------------------------------------------
# 8b/10b codec
# --------------------------------------------------------------------------

def test_encode_decode_roundtrip_all_bytes_both_rd():
    dm = codec.build_decode_map()
    for b in range(256):
        for rd in (codec.RD_MINUS, codec.RD_PLUS):
            sym, _ = codec.encode_byte(b, rd)
            assert dm[sym] == b


def test_running_disparity_stays_bounded():
    """RD must be +/-1 at every symbol boundary; symbol disparity in {-2,0,+2}."""
    rd = codec.RD_MINUS
    # A long stream exercising every octet many times.
    for b in list(range(256)) * 4:
        sym, new_rd = codec.encode_byte(b, rd)
        ones = codec.symbol_ones(sym)
        assert ones in (4, 5, 6), f"byte {b}: {ones} ones (disparity out of range)"
        assert new_rd in (codec.RD_MINUS, codec.RD_PLUS)
        # neutral symbol keeps RD; +/-2 symbol flips it
        if ones == 5:
            assert new_rd == rd
        else:
            assert new_rd == -rd
        rd = new_rd


def test_no_run_longer_than_five():
    """Concatenated symbol stream never has 6 identical consecutive bits."""
    rd = codec.RD_MINUS
    bits = []
    for b in list(range(256)) * 3:
        sym, rd = codec.encode_byte(b, rd)
        bits.extend((sym >> i) & 1 for i in range(9, -1, -1))
    run = 1
    for i in range(1, len(bits)):
        run = run + 1 if bits[i] == bits[i - 1] else 1
        assert run <= 5, f"run of {run} at bit {i}"


def test_comma_flips_disparity():
    for rd in (codec.RD_MINUS, codec.RD_PLUS):
        sym, new_rd = codec.encode_comma(rd)
        assert new_rd == -rd
        assert sym in codec.COMMA_SYMBOLS


def test_dram0_lut_matches_encoder():
    lut = codec.build_dram0_lut()
    assert len(lut) == 256 * 4
    for b in range(256):
        word = int.from_bytes(lut[b * 4:b * 4 + 4], "little")
        code_n, rd_n = codec.encode_byte(b, codec.RD_MINUS)
        code_p, rd_p = codec.encode_byte(b, codec.RD_PLUS)
        assert word & 0x3FF == code_n
        assert (word >> 10) & 1 == (1 if rd_n == codec.RD_PLUS else 0)
        assert (word >> 16) & 0x3FF == code_p
        assert (word >> 26) & 1 == (1 if rd_p == codec.RD_PLUS else 0)


def test_stream_decode_with_commas():
    dm = codec.build_decode_map()
    rd = codec.RD_MINUS
    payloads = [b"\x00\x01\x02\x03", b"hello", bytes(range(10))]
    symbols = []
    for p in payloads:
        c, rd = codec.encode_comma(rd)
        symbols.append(c)
        for byte in p:
            s, rd = codec.encode_byte(byte, rd)
            symbols.append(s)
    c, rd = codec.encode_comma(rd)
    symbols.append(c)
    res = decode_symbols(symbols, dm)
    assert res.invalid_symbols == 0
    assert res.frames == payloads


# --------------------------------------------------------------------------
# CRC32 / PRNG / frames
# --------------------------------------------------------------------------

def test_crc32_bitwise_matches_zlib():
    for data in (b"", b"123456789", bytes(range(64)), b"Hello World Text"):
        assert crc32.crc32_bitwise(data) == crc32.crc32(data)


def test_crc32_check_value():
    # Classic CRC-32 check string.
    assert crc32.crc32(b"123456789") == 0xCBF43926


def test_prng_deterministic_and_reproducible():
    a = prng.prng_bytes(128, seed=0x1BADC0DE)
    b = prng.prng_bytes(128, seed=0x1BADC0DE)
    assert a == b and len(a) == 128


def test_bert_frame_shape():
    f = frames.build_bert_frame()
    assert f.kind == "bert"
    assert len(f.core) == 132           # 128 payload + 4 CRC
    assert f.core[:128] == f.l2
    assert crc32.fcs_bytes(f.l2) == f.core[128:]


def test_udp_frame_valid_length_and_fcs():
    f = frames.build_udp_frame()
    assert f.kind == "udp"
    assert len(f.l2) == 60              # padded to Ethernet minimum
    assert len(f.core) == 64            # + 4 FCS
    assert crc32.fcs_bytes(f.l2) == f.core[60:]
    assert f.l2[12:14] == b"\x08\x00"   # ethertype IPv4


def test_full_frame_line_roundtrip():
    """Encode a full frame's on-wire bytes and decode them back."""
    dm = codec.build_decode_map()
    for f in (frames.build_bert_frame(), frames.build_udp_frame()):
        syms, _ = codec.encode_bytes(f.core)
        res = decode_symbols(syms, dm)
        assert res.invalid_symbols == 0
        assert res.frames == [f.core]


# --------------------------------------------------------------------------
# Firmware on the simulator (PRU0, single core)
# --------------------------------------------------------------------------

from pif_eth import driver                          # noqa: E402
from pif_eth.crc32 import fcs_bytes                  # noqa: E402
from pif_eth.prng import prng_bytes, DEFAULT_SEED    # noqa: E402


def test_firmware_self_configures_perif_ch0():
    """No host register setup: firmware writes GPCFG0/TXCFG/CH0CFG0."""
    from simulator import Simulator
    sim = Simulator()
    assert sim.load("pru0", driver.FIRMWARE, include_paths=[driver.ASM_DIR]) == []
    sim.step("pru0", 40)
    assert sim.gpcfg_state("pru0")["mux_sel"] == 1
    regs = sim._perif["pru0"].registers
    assert regs.get_shared_config()["txcfg"] == 0x00070010
    assert regs.get_tx_frame_size(0) == 0            # continuous mode


def test_firmware_prng_and_crc_in_dram():
    """The firmware's PRNG payload and CRC32 FCS match the golden reference."""
    res = driver.run("bert", 1)
    sim = res.sim
    payload = bytes(sim.memory_read(driver.A_COREBUF, 128))
    fcs = bytes(sim.memory_read(driver.A_COREBUF + 128, 4))
    assert payload == prng_bytes(128, DEFAULT_SEED)
    assert fcs == fcs_bytes(payload)


def test_firmware_bert_roundtrip_zero_ber():
    res = driver.run("bert", 4)
    assert res.frame_count == 4
    assert res.all_ok and res.total_bit_errors == 0
    # Payload advances with the running PRNG -> frames differ.
    assert res.frames[0].decoded != res.frames[1].decoded


def test_firmware_udp_roundtrip_and_valid_ethernet():
    res = driver.run("udp", 3)
    assert res.all_ok and res.ber == 0.0
    l2 = res.frames[0].decoded[:-4]
    assert len(l2) == 60
    assert l2[12:14] == b"\x08\x00"                  # ethertype IPv4
    assert b"Hello World Text" in l2


def test_pcap_output_roundtrips(tmp_path):
    from pif_eth.pcap import write_pcap
    res = driver.run("udp", 5)
    path = tmp_path / "udp.pcap"
    n = write_pcap(str(path), res.l2_frames())
    assert n == 5
    data = path.read_bytes()
    import struct
    magic, _vj, _vn, _tz, _sig, _snap, link = struct.unpack("<IHHiIII", data[:24])
    assert magic == 0xA1B2C3D4 and link == 1         # classic pcap, Ethernet


def test_via_mcp_server():
    """Drive the firmware through the MCP server wrapper (pru_load/step/memory)."""
    from mcp_server.server import PRUSimulatorMCP
    mcp = PRUSimulatorMCP()
    # Set up DRAM0: LUT + control block for one BERT frame.
    mcp.sim.memory.write(driver.LUT_ADDR, codec.build_dram0_lut())
    for addr, val in ((driver.A_NUMF, 1), (driver.A_MODE, driver.MODE_PRNG),
                      (driver.A_SEED, DEFAULT_SEED), (driver.A_PLEN, 128),
                      (driver.A_FCNT, 0), (driver.A_BURST, 0), (driver.A_GOFLAG, 1)):
        mcp.sim.memory.write(addr, (val & 0xFFFFFFFF).to_bytes(4, "little"))
    assert mcp.pru_load(driver.FIRMWARE, core="pru0",
                        include_paths=[driver.ASM_DIR])["success"]
    # Step until the frame counter reports completion.
    for _ in range(200):
        mcp.pru_step(core="pru0", count=2000)
        if int.from_bytes(mcp.sim.memory_read(driver.A_FCNT, 4), "little") == 1:
            break
    assert int.from_bytes(mcp.sim.memory_read(driver.A_FCNT, 4), "little") == 1
    # Verify the firmware-built frame in DRAM via the MCP memory tool.
    dump = mcp.pru_memory(driver.A_COREBUF, 132)
    frame = bytes.fromhex(dump["hex_dump"])
    assert frame[:128] == prng_bytes(128, DEFAULT_SEED)
    assert frame[128:] == fcs_bytes(frame[:128])


def test_mcp_pru_load_accepts_include_paths():
    """The MCP wrapper must be able to load multi-file assembly."""
    import inspect
    from mcp_server.server import PRUSimulatorMCP
    assert "include_paths" in inspect.signature(PRUSimulatorMCP.pru_load).parameters


@pytest.mark.parametrize("fw", ["pif_eth_tx.asm", "pif_eth_tx_n2.asm",
                                "pif_eth_tx_n2_skipchecks.asm",
                                "pif_eth_rx_o1_raw.asm"])
def test_firmware_uses_shared_hw_crc32_include(fw):
    src = (Path(driver.ASM_DIR) / fw).read_text()
    assert '.include "pif_eth_crc32_hw.inc"' in src
    # The CRC must live in exactly one place, not inlined per firmware.
    assert "0xEDB8" not in src and "crc32_core:" not in src


_CRC_HARNESS = """
start:
    ldi  r0, 0x0400
    lbbo &r8, r0, 0, 4          ; byte count
    ldi  r21, 0x0500
    ldi  r28, 0x1234            ; r28/r29 must survive crc32_core
    ldi  r29, 0x5678
    jal  r26, crc32_core
    ldi  r0, 0x0404
    sbbo &r20, r0, 0, 4         ; FCS
    sbbo &r21, r0, 4, 4         ; end pointer
    sbbo &r28, r0, 8, 8         ; r28, r29
    halt

    .include "{inc}"
"""


@pytest.mark.parametrize("inc", ["pif_eth_crc32_hw.inc", "pif_eth_crc32.inc"])
@pytest.mark.parametrize("length", [0, 1, 2, 3, 4, 5, 7, 60, 128, 201])
def test_crc32_core_matches_zlib(inc, length):
    """Both crc32_core builds honour the shared contract at every tail length."""
    from simulator import Simulator
    sim = Simulator()
    data = bytes((i * 37 + 11) & 0xFF for i in range(length))
    sim.memory.write(0x0400, length.to_bytes(4, "little"))
    sim.memory.write(0x0500, data + b"\xAA" * 8)
    assert sim.load("pru0", _CRC_HARNESS.format(inc=inc),
                    include_paths=[driver.ASM_DIR]) == []
    for _ in range(40_000):
        if sim.step("pru0", 100)["halted"]:
            break
    out = bytes(sim.memory_read(0x0404, 16))
    assert int.from_bytes(out[0:4], "little") == crc32.crc32(data)
    assert int.from_bytes(out[4:8], "little") == 0x0500 + length
    assert int.from_bytes(out[8:12], "little") == 0x1234
    assert int.from_bytes(out[12:16], "little") == 0x5678


def test_hw_crc32_core_is_much_cheaper_than_software():
    """Cycle cost of the 128-octet BERT FCS, measured on the simulator."""
    from simulator import Simulator

    def cycles(inc):
        sim = Simulator()
        sim.memory.write(0x0400, (128).to_bytes(4, "little"))
        sim.memory.write(0x0500, prng_bytes(128, DEFAULT_SEED))
        assert sim.load("pru0", _CRC_HARNESS.format(inc=inc),
                        include_paths=[driver.ASM_DIR]) == []
        st = {"halted": False}
        while not st["halted"]:
            st = sim.step("pru0", 1)
        return st["cycles"]

    hw, sw = cycles("pif_eth_crc32_hw.inc"), cycles("pif_eth_crc32.inc")
    assert hw < 200 and sw > 9000


# --------------------------------------------------------------------------
# PRU1 RX: 8b/10b decode LUT builder
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# PRU1 RX: host-side capture decoding reference
# --------------------------------------------------------------------------

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


def _comma_bits(code10):
    return [(code10 >> i) & 1 for i in range(9, -1, -1)]


@pytest.mark.parametrize("pad,expected_offset", [(5, 5), (3, 3), (8, 8)])
def test_find_comma_offset_prefers_clean_alignment_over_spurious_comma(pad, expected_offset):
    """A comma at a WRONG phase must not beat the true, error-free grid.

    Prepends a real K28.5 symbol followed by `pad` filler bits, so offset 0
    genuinely contains a comma (what the old first-match rule locked onto)
    while the true burst grid begins at offset `pad`. Also pins interior
    offsets, which the skew tests never reach.
    """
    bits = _comma_bits(codec.K28_5_RD_MINUS) + [0] * pad + _encode_burst(bytes(range(32)))
    assert rx_reference.find_comma_offset(bits) == expected_offset


def test_find_comma_offset_true_alignment_has_no_invalid_symbols():
    """The chosen offset must be the one that decodes cleanly."""
    from pif_eth.decoder import bits_to_symbols
    dm = codec.build_decode_map()
    bits = _encode_burst(bytes(range(64)))
    off = rx_reference.find_comma_offset(bits)
    syms = bits_to_symbols(bits[off:])
    invalid = [s for s in syms if s not in codec.COMMA_SYMBOLS and s not in dm]
    assert invalid == []
