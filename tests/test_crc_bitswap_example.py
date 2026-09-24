"""Tests for crc_bitswap_example.asm via PRUSimulatorMCP."""
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server.server import PRUSimulatorMCP

FRAME = bytes.fromhex("DEADBEEFCAFEBABE")


def _rev(value: int, width: int) -> int:
    return int(f"{value:0{width}b}"[::-1], 2)


def _bflip8(value: int) -> int:
    return sum(_rev((value >> s) & 0xFF, 8) << s for s in (0, 8, 16, 24))


class TestCrcBitswapExample:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
        with open("source/crc_bitswap_example.asm") as f:
            self.src = f.read()

    def _run(self) -> dict:
        assert self.mcp.pru_load(self.src, core="pru0")["success"]
        run = self.mcp.pru_run_until(core="pru0", max_steps=2000)
        assert run["reason"] == "halted"
        regs = self.mcp.pru_registers(core="pru0")
        return {r: int(regs[r], 16) for r in ("r10", "r11", "r12",
                                              "r13", "r14", "r15")}

    def test_assembles(self):
        result = self.mcp.pru_load(self.src, core="pru0")
        assert result["success"] is True
        assert result["errors"] == []

    def test_raw_crc_is_ethernet_crc32_before_final_xor(self):
        regs = self._run()
        assert regs["r12"] == (~zlib.crc32(FRAME)) & 0xFFFFFFFF == 0xB12B5C1C

    def test_byte_wide_flip_r27(self):
        regs = self._run()
        assert regs["r10"] == _bflip8(regs["r12"]) == 0x8DD43A38

    def test_32bit_wide_flip_r28(self):
        regs = self._run()
        assert regs["r11"] == _rev(regs["r12"], 32) == 0x383AD48D
        # The 32-bit mirror is the byte-wise mirror with its bytes swapped.
        assert regs["r11"] == int.from_bytes(
            regs["r10"].to_bytes(4, "little"), "big")

    def test_byte_and_word_data_writes_agree(self):
        regs = self._run()
        assert (regs["r10"], regs["r11"], regs["r12"]) == \
            (regs["r13"], regs["r14"], regs["r15"])
