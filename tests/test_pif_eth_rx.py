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
