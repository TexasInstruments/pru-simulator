"""IEP compare register offsets: CMP0..CMP7, IEP_RXIPG0/1, CMP8..CMP15.

Offsets are literals from the AM243x TRM (SPRUIM2J) IEP register map,
Table 14-10902: CMP7_REG0/1 = 0xB0/0xB4, IEP_RXIPG0/1 = 0xB8/0xBC,
CMP8_REG0/1 = 0xC0/0xC4, CMP15_REG0/1 = 0xF8/0xFC. They are deliberately not
derived from the module's constants.
"""

import pytest

from perif.iep import IepTimer, GLOBAL_CFG, CMP_CFG, CMP_STATUS


def w32(iep, off, val):
    iep.write(off, val.to_bytes(4, "little"))


def r32(iep, off):
    return int.from_bytes(iep.read(off, 4), "little")


@pytest.fixture
def iep():
    return IepTimer()


def test_cmp7_at_0xb0_0xb4(iep):
    w32(iep, 0xB0, 0x11111111)
    w32(iep, 0xB4, 0x22222222)
    assert iep.compare[7] == 0x2222222211111111
    assert r32(iep, 0xB0) == 0x11111111
    assert r32(iep, 0xB4) == 0x22222222


def test_cmp8_at_0xc0_0xc4_not_cmp9(iep):
    w32(iep, 0xC0, 0xAAAA0001)
    w32(iep, 0xC4, 0xBBBB0002)
    assert iep.compare[8] == 0xBBBB0002AAAA0001
    assert iep.compare[9] == 0
    assert r32(iep, 0xC0) == 0xAAAA0001
    assert r32(iep, 0xC4) == 0xBBBB0002


def test_cmp15_at_0xf8_0xfc(iep):
    w32(iep, 0xF8, 0xCAFE0001)
    w32(iep, 0xFC, 0xCAFE0002)
    assert iep.compare[15] == 0xCAFE0002CAFE0001
    assert r32(iep, 0xF8) == 0xCAFE0001
    assert r32(iep, 0xFC) == 0xCAFE0002


def test_every_compare_has_its_own_offset(iep):
    bases = [0x78 + 8 * j for j in range(8)] + [0xC0 + 8 * (j - 8) for j in range(8, 16)]
    for j, base in enumerate(bases):
        w32(iep, base, 0x100 + j)
        w32(iep, base + 4, 0x200 + j)
    for j, base in enumerate(bases):
        assert iep.compare[j] == ((0x200 + j) << 32) | (0x100 + j)
        assert r32(iep, base) == 0x100 + j


def test_write_to_rxipg_does_not_touch_any_compare(iep):
    before = list(iep.compare)
    w32(iep, 0xB8, 0xFFFFFFFF)
    w32(iep, 0xBC, 0xFFFFFFFF)
    assert iep.compare == before


def test_rxipg_reset_and_storage(iep):
    # Reset 0xFFFF0000: RX_MIN_IPG R/W = 0xFFFF, RX_IPG R = 0.
    assert r32(iep, 0xB8) == 0xFFFF0000
    assert r32(iep, 0xBC) == 0xFFFF0000
    w32(iep, 0xB8, 0x12340000)
    w32(iep, 0xBC, 0x56781234)          # low half is read-only
    assert r32(iep, 0xB8) == 0x12340000
    assert r32(iep, 0xBC) == 0x56780000


def test_reset_restores_rxipg(iep):
    w32(iep, 0xB8, 0)
    iep.reset()
    assert r32(iep, 0xB8) == 0xFFFF0000


def test_offsets_past_cmp15_are_ignored(iep):
    w32(iep, 0x100, 0xDEADBEEF)         # COUNT_RESET_VAL_REG0: not modelled
    assert r32(iep, 0x100) == 0
    assert all(c == 0 for c in iep.compare)


@pytest.mark.parametrize("j, reg0", [(8, 0xC0), (15, 0xF8)])
def test_compare_event_sets_status_bit_at_real_address(iep, j, reg0):
    w32(iep, GLOBAL_CFG, 0x11)          # DEFAULT_INC=1, CNT_ENABLE=1
    w32(iep, reg0, 5)
    w32(iep, CMP_CFG, 1 << (1 + j))     # CMP_EN bit 1 -> CMP0
    for _ in range(10):
        iep.tick()
    assert r32(iep, CMP_STATUS) == 1 << j
