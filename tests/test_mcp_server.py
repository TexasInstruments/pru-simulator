"""Tests for MCP Server tool implementations."""
from pathlib import Path

import pytest
from mcp_server.server import PRUSimulatorMCP


class TestMCPTools:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")

    def test_pru_load(self):
        result = self.mcp.pru_load(source="ldi r0, 42\nhalt", core="pru0")
        assert result["success"] is True
        assert result["line_count"] == 2
        assert result["errors"] == []

    def test_pru_step(self):
        self.mcp.pru_load(source="ldi r0, 1\nldi r1, 2\nhalt", core="pru0")
        result = self.mcp.pru_step(core="pru0", count=2)
        assert result["pc"] == 2
        assert result["cycles"] == 2

    def test_pru_registers(self):
        self.mcp.pru_load(source="ldi r0, 0xFF\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        result = self.mcp.pru_registers(core="pru0")
        assert result["r0"] == "0x000000ff"
        assert result["carry"] is False

    def test_pru_io(self):
        self.mcp.pru_load(source="ldi r30, 5\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        result = self.mcp.pru_io(core="pru0")
        assert result["gpo_pins"][0] == 1
        assert result["gpo_pins"][2] == 1
        assert len(result["gpo_pins"]) == 20

    def test_pru_set_input(self):
        self.mcp.pru_set_input(core="pru0", pin=5, value=True)
        self.mcp.pru_load(source="mov r0, r31\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        regs = self.mcp.pru_registers(core="pru0")
        assert int(regs["r0"], 16) == (1 << 5)

    def test_pru_reset(self):
        self.mcp.pru_load(source="ldi r0, 1\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=1)
        self.mcp.pru_reset(core="pru0")
        regs = self.mcp.pru_registers(core="pru0")
        assert regs["r0"] == "0x00000000"

    def test_pru_status(self):
        self.mcp.pru_load(source="ldi r0, 1\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=2)
        status = self.mcp.pru_status()
        assert "pru0" in status["cores"]
        assert status["cores"]["pru0"]["halted"] is True

    def test_pru_run_until(self):
        self.mcp.pru_load(source="ldi r0, 1\nldi r1, 2\nhalt", core="pru0")
        result = self.mcp.pru_run_until(core="pru0")
        assert result["reason"] == "halted"

    def test_pru_memory(self):
        self.mcp.pru_load(source="ldi r1, 0\nldi r2, 0xABCD\nsbbo &r2, r1, 0, 4\nhalt", core="pru0")
        self.mcp.pru_step(core="pru0", count=3)
        result = self.mcp.pru_memory(addr=0, length=4)
        assert "cd" in result["hex_dump"] or "ab" in result["hex_dump"]

    def test_pru_breakpoint(self):
        result = self.mcp.pru_breakpoint(core="pru0", address=5)
        assert "id" in result

    def test_pru_ssi_inject(self):
        reader = (
            Path(__file__).parent.parent
            / "source"
            / "ssi_reader_4mhz_12bit"
            / "ssi_reader_4mhz_12bit.asm"
        ).read_text()
        result = self.mcp.pru_ssi_inject(
            source=reader, value=0xABC, bits=12, max_steps=20_000
        )
        assert result["match"] is True
        assert result["captured"] == 0xABC
        assert result["frames_captured"] >= 1

    def test_pru_i2c_attach(self):
        result = self.mcp.pru_i2c_attach(core="pru0", enabled=True, address=0x23)
        assert result["success"] is True
        assert result["address"] == 0x23
        io_state = self.mcp.sim.i2c_state("pru0")
        assert io_state["address"] == 0x23
        assert io_state["config_reg"] == 0xFF

        detach = self.mcp.pru_i2c_attach(core="pru0", enabled=False)
        assert detach["success"] is True
        assert self.mcp.sim.i2c_state("pru0") is None
