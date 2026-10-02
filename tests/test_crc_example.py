"""Tests for crc_example.asm via PRUSimulatorMCP."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server.server import PRUSimulatorMCP

FRAME = bytes.fromhex("DEADBEEFCAFEBABE")


def _reflected_crc(data: bytes, poly: int, width: int, init: int) -> int:
    """Reference reflected bit-serial CRC, same shape as pif_eth's crc32_bitwise."""
    mask = (1 << width) - 1
    crc = init
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        crc &= mask
    return crc


class TestCrcExample:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")
        with open("source/crc_example.asm") as f:
            self.src = f.read()

    def test_assembles(self):
        result = self.mcp.pru_load(self.src, core="pru0")
        assert result["success"] is True
        assert result["errors"] == []

    def test_crc16_and_crc32_results(self):
        """Run both passes and check the final registers against a reference
        reflected-CRC implementation over the same 8-byte frame."""
        assert self.mcp.pru_load(self.src, core="pru0")["success"]
        run = self.mcp.pru_run_until(core="pru0", max_steps=2000)
        assert run["reason"] == "halted"

        regs = self.mcp.pru_registers(core="pru0")

        expected_crc16 = _reflected_crc(FRAME, 0xA001, 16, 0x0000)
        expected_crc32 = _reflected_crc(FRAME, 0xEDB88320, 32, 0xFFFFFFFF)

        assert int(regs["r10"], 16) == expected_crc16
        assert int(regs["r11"], 16) == expected_crc32

    def test_crc16_result_is_width_invariant(self):
        """Byte-wide and half-word-wide feeding of the same frame must agree.

        The example only exercises the half-word-wide path (see the .asm
        header comment for why the byte-wide "CRC8" path is out of scope),
        but the accelerator itself must still be width-invariant -- verify
        that directly against the CRCAccelerator model.
        """
        from core.registers import RegisterFile
        from xfr.crc_accelerator import CRCAccelerator

        byte_wide = CRCAccelerator(RegisterFile())
        for b in FRAME:
            byte_wide.xout(29, bytes([b]))

        assert self.mcp.pru_load(self.src, core="pru0")["success"]
        self.mcp.pru_run_until(core="pru0", max_steps=2000)
        halfword_wide_result = int(self.mcp.pru_registers(core="pru0")["r10"], 16)

        assert byte_wide.crc_reg == halfword_wide_result
