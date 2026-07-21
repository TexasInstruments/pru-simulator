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
