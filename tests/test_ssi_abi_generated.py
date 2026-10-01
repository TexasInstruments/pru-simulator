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
    mailbox = abi.unpack_mailbox(bytes.fromhex("02000000bc0a00000300000000000000"))
    assert mailbox == {"sequence": 2, "raw_frame": 0xABC,
                       "frame_count": 3, "status": 0}
