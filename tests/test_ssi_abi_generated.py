import pytest

from tools.gen_ssi_abi import ROOT, generate_files, load_schema
from pru_io import ssi_config_abi as abi


def test_generated_ssi_abi_files_match_versioned_schema():
    for relative_path, expected in generate_files(load_schema()).items():
        assert (ROOT / relative_path).read_text(encoding="utf-8") == expected


def test_ssi_config_pack_round_trip_and_mailbox_decode():
    config = abi.pack_config(frame_bits=12, clock_delay_loops=20,
                             idle_delay_loops=3000)
    assert len(config) == abi.CONFIG_SIZE
    assert abi.unpack_config(config) == {
        "abi_version": abi.ABI_VERSION,
        "frame_bits": 12,
        "clock_delay_loops": 20,
        "idle_delay_loops": 3000,
    }
    mailbox = abi.unpack_mailbox(bytes.fromhex(
        "02000000bc0a00000100000003000000" "00000000"))
    assert mailbox == {"sequence": 2, "raw_frame_lo": 0xABC, "raw_frame_hi": 1,
                       "frame_count": 3, "status": 0}


def test_abi_version_two_blocks_do_not_overlap_and_accept_64_bit_frames():
    blocks = [(abi.CONFIG_OFFSET, abi.CONFIG_SIZE),
              (abi.MAILBOX_OFFSET, abi.MAILBOX_SIZE),
              (abi.EMULATOR_OFFSET, abi.EMULATOR_SIZE)]
    ends = [offset + size for offset, size in blocks]
    assert abi.ABI_VERSION == 2
    assert all(end <= start for end, (start, _) in zip(ends, blocks[1:]))
    assert abi.unpack_config(abi.pack_config(64, 1, 1))["frame_bits"] == 64
    with pytest.raises(ValueError):
        abi.pack_config(65, 1, 1)
    with pytest.raises(ValueError):
        abi.pack_emulator_config(0, 0, 0)
    assert abi.unpack_emulator(abi.pack_emulator_config(33, 5, 1)) == {
        "abi_version": 2, "frame_bits": 33, "frame_lo": 5, "frame_hi": 1,
        "status": 0}
