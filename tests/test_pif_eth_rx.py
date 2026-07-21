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


def test_o1_firmware_captures_and_detects_eof():
    """Option 1 at a relaxed rung: capture matches the Python-armed reference."""
    sim = rx_driver.run_rx(8, option="o1", num_frames=1)
    assert rx_driver.ru32(sim, rx_driver.S_FRAMES) == 1
    # eof_status has two legitimate non-zero values (1 = clean EOF, 2 =
    # overflow abort, see pif_eth_rx_o1_raw.asm's pf_symbol overrun guard).
    # Assert the clean-EOF value specifically, not just truthiness, so this
    # test would fail if the guard mis-fired on a frame well under the 256 B
    # frame-buffer ceiling; the other branch is exercised directly by
    # test_o1_overrun_sets_eof_status_2_and_preserves_control_block.
    assert rx_driver.ru32(sim, rx_driver.S_EOF) == 1        # clean EOF, not 2
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

    seed=1 (not DEFAULT_SEED): whether any interior byte goes fully to zero
    depends on where an 8b/10b max-run (5 line bits) happens to fall modulo
    the 4-real-bit capture-byte grid -- a property of the encoded payload,
    not of n_tx or of the RX firmware. Verified directly against Task 3's
    python-armed reference (rx_driver.capture_python_rx), which is generated
    by an entirely separate code path and shows the identical absence: for
    DEFAULT_SEED at payload_len=128, no interior capture byte is ever all
    zero, so this test would fail its own vacuousness guard no matter how
    the RX firmware is written. seed=1 is confirmed (by direct sweep) to
    reliably produce the hazard while everything else about the scenario
    (n_tx, payload_len) matches the other o1 tests.
    """
    sim = rx_driver.run_rx(8, option="o1", num_frames=1, seed=1)
    n = rx_driver.ru32(sim, rx_driver.S_CAPBYTES)
    raw = sim.memory_read(rx_driver.CAP_ADDR, n)
    interior = raw[:-2]                              # drop the EOF pair
    assert 0 in interior, "capture contained no interior zero byte to test"
    assert rx_driver.ru32(sim, rx_driver.S_EOF) == 1
    assert n > 300                                   # did not stop early


def test_o1_firmware_reconstructs_frame_in_dram():
    sim = rx_driver.run_rx(8, option="o1", num_frames=1)
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    payload = prng_bytes(128, DEFAULT_SEED)
    expected = payload + fcs_bytes(payload)
    got = sim.memory_read(rx_driver.FRAME_ADDR, len(expected))
    assert got == expected


def test_o1_firmware_validates_crc_and_ber():
    sim = rx_driver.run_rx(8, option="o1", num_frames=3)
    assert rx_driver.ru32(sim, rx_driver.S_FRAMES) == 3
    assert rx_driver.ru32(sim, rx_driver.S_CRCOK) == 1
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_BITERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_TOTBITS) == 128 * 8


def test_o1_clean_at_rated_divider():
    """Pins the measured rating from Task 8 so a regression is visible."""
    n_tx = rx_driver.RATED_DIVIDER["o1"]
    sim = rx_driver.run_rx(n_tx, option="o1", num_frames=2)
    assert rx_driver.ru32(sim, rx_driver.S_OVF) == 0
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_BITERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_CRCOK) == 1


# --- Final-review fix: bounded frame buffer / payload_len validation -------
#
# pf_symbol advanced its decoded-octet pointer with no limit, so a
# payload_len above 252 walked the 256 B frame buffer (0x0E00-0x0EFF) into
# the stats block, then the control block, poisoning go/seed/payload_len/
# rxcfg for every later frame. Reproduced at n_tx=8, payload_len=300:
# cap_bytes=3435530199 (garbage), crc_ok=0, bit_err=31.


def test_o1_clean_at_payload_len_200():
    """A payload_len under the 252-octet ceiling still decodes cleanly."""
    sim = rx_driver.run_rx(8, option="o1", num_frames=1, payload_len=200)
    assert rx_driver.ru32(sim, rx_driver.S_EOF) == 1
    assert rx_driver.ru32(sim, rx_driver.S_SYMERR) == 0
    assert rx_driver.ru32(sim, rx_driver.S_CRCOK) == 1
    assert rx_driver.ru32(sim, rx_driver.S_BITERR) == 0


def test_run_rx_rejects_oversized_payload_len():
    """payload_len=300 (>252) must raise, not silently corrupt DRAM1.

    Both entry points take payload_len directly, so both are checked --
    run_rx calls build_sim internally, but a caller could reach either.
    """
    with pytest.raises(ValueError):
        rx_driver.run_rx(8, option="o1", num_frames=1, payload_len=300)
    with pytest.raises(ValueError):
        rx_driver.build_sim(8, payload_len=300)


def test_max_payload_len_boundary():
    """252 is accepted (payload + 4-byte FCS == 256 B exactly); 253 is not."""
    rx_driver._validate_payload_len(rx_driver.MAX_PAYLOAD_LEN)
    with pytest.raises(ValueError):
        rx_driver._validate_payload_len(rx_driver.MAX_PAYLOAD_LEN + 1)


def _synthetic_capture(payload: bytes) -> bytes:
    """Build a raw 2x-oversampled RX capture: one leading K28.5 comma plus
    *payload*, 8b/10b-encoded with the same tables as the firmware's DRAM1
    LUT. Lets a test hand post_frame a capture directly -- bypassing the
    realtime capture loop, the TX firmware, and payload_len entirely (only
    rx_ber_check reads payload_len; post_frame's decode loop does not) -- so
    the firmware's own overrun guard can be proven reachable even though
    payload_len is now host-validated and can never carry an over-large
    frame through run_rx/build_sim.
    """
    rd = codec.RD_MINUS
    comma, rd = codec.encode_comma(rd)
    data_codes, rd = codec.encode_bytes(payload, rd)
    codes = [comma] + data_codes

    bits: list[int] = []
    for code in codes:
        for i in range(9, -1, -1):
            bits.append((code >> i) & 1)
    assert len(bits) % 4 == 0, "pick a payload length that byte-aligns the capture"

    samples: list[int] = []
    for b in bits:
        samples.append(b)
        samples.append(b)                 # 2x oversample: duplicate each bit

    raw = bytearray()
    for i in range(0, len(samples), 8):
        byte = 0
        for s in samples[i:i + 8]:
            byte = (byte << 1) | s
        raw.append(byte)
    return bytes(raw)


def test_o1_overrun_sets_eof_status_2_and_preserves_control_block():
    """pf_symbol's frame-buffer overrun guard, exercised directly.

    261 octets (> 252) decoded from a synthetic capture must fill the 256 B
    frame buffer, report eof_status=2 (not the garbage cap_bytes the
    reviewer's reproduction showed), and leave the control block's
    go/seed/payload_len/rxcfg sentinels untouched.
    """
    n_octets = 261
    payload = bytes((i * 7 + 3) & 0xFF for i in range(n_octets))
    raw = _synthetic_capture(payload)

    sim = rx_driver.build_sim(8, payload_len=rx_driver.BERT_PAYLOAD_LEN)

    # Sentinel control-block values: an overrun must not disturb these.
    rx_driver._wu32(sim, rx_driver.C_GO, 0)
    rx_driver._wu32(sim, rx_driver.C_SEED, 0xABCD1234)
    rx_driver._wu32(sim, rx_driver.C_PLEN, 0x11111111)
    rx_driver._wu32(sim, rx_driver.C_RXCFG, 0x22222222)
    rx_driver._wu32(sim, rx_driver.S_EOF, 0)
    rx_driver._wu32(sim, rx_driver.S_CAPBYTES, len(raw))

    fw = (rx_driver._HERE / rx_driver.RX_FIRMWARE["o1"]).read_text()
    errors = sim.load("pru1", fw, include_paths=[str(rx_driver._HERE)])
    assert not errors
    sim.memory.write(rx_driver.CAP_ADDR, raw)

    core = sim.cores["pru1"]
    core.pc = core._parser.labels["post_frame"]
    sim.step("pru1", 100_000)           # decode + CRC + BER, well past return

    assert rx_driver.ru32(sim, rx_driver.S_EOF) == 2              # overflow abort
    assert rx_driver.ru32(sim, rx_driver.S_CAPBYTES) == len(raw)  # untouched
    assert rx_driver.ru32(sim, rx_driver.C_GO) == 0
    assert rx_driver.ru32(sim, rx_driver.C_SEED) == 0xABCD1234
    assert rx_driver.ru32(sim, rx_driver.C_PLEN) == 0x11111111
    assert rx_driver.ru32(sim, rx_driver.C_RXCFG) == 0x22222222
    # The frame buffer is full (exactly 256 B) but not corrupted -- the
    # bytes actually decoded before the guard tripped must match.
    got = sim.memory_read(rx_driver.FRAME_ADDR, rx_driver.FRAME_BUFFER_SIZE)
    assert got == payload[:rx_driver.FRAME_BUFFER_SIZE]
