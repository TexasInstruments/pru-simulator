"""Tests for uart_tx.asm and uart_print.asm via PRUSimulatorMCP."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_server.server import PRUSimulatorMCP


def _collect_transitions(mcp, max_steps: int = 500) -> list[int]:
    """Step until halt; return unique consecutive GPO pin-0 states."""
    states: list[int] = []
    for _ in range(max_steps):
        mcp.pru_step(core="pru0")
        pin = mcp.pru_io(core="pru0")["gpo_pins"][0]
        if not states or pin != states[-1]:
            states.append(pin)
        if mcp.pru_status()["cores"]["pru0"]["halted"]:
            break
    return states


class TestUartTx:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")

    def _load_fast(self, src: str) -> dict:
        """Load ASM with BAUD_COUNT=2 for fast step-through."""
        src = re.sub(r"BAUD_COUNT\s+\.set\s+\d+", "BAUD_COUNT .set 2", src)
        return self.mcp.pru_load(src, core="pru0")

    def test_uart_tx_assembles(self):
        with open("source/uart_tx.asm") as f:
            result = self.mcp.pru_load(f.read(), core="pru0")
        assert result["success"] is True
        assert result["errors"] == []

    def test_uart_tx_halts(self):
        with open("source/uart_tx.asm") as f:
            src = f.read()
        self._load_fast(src)
        run = self.mcp.pru_run_until(core="pru0", max_steps=5000)
        assert run["reason"] == "halted"

    def test_uart_tx_pin_ends_high(self):
        """STOP bit must leave TX line HIGH (idle)."""
        with open("source/uart_tx.asm") as f:
            src = f.read()
        self._load_fast(src)
        self.mcp.pru_run_until(core="pru0", max_steps=5000)
        assert self.mcp.pru_io(core="pru0")["gpo_pins"][0] == 1

    def test_uart_tx_bit_sequence(self):
        """Patch TX_CHAR=0x55 ('U'); verify alternating 8N1 frame on pin 0."""
        with open("source/uart_tx.asm") as f:
            src = f.read()
        src = re.sub(r"TX_CHAR\s+\.set\s+\S+", "TX_CHAR .set 0x55", src)
        self._load_fast(src)
        transitions = _collect_transitions(self.mcp, max_steps=300)
        # U=0x55=01010101, LSB-first: 1,0,1,0,1,0,1,0
        # idle→START→b0→b1→b2→b3→b4→b5→b6→b7→STOP
        assert transitions == [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1], (
            f"Expected alternating 11-state frame for 'U', got: {transitions}"
        )


class TestUartPrint:
    def setup_method(self):
        self.mcp = PRUSimulatorMCP(config_path="nonexistent.cfg")

    def _load_fast(self, src: str) -> dict:
        src = re.sub(r"BAUD_COUNT\s+\.set\s+\d+", "BAUD_COUNT .set 2", src)
        return self.mcp.pru_load(src, core="pru0")

    def _write_string(self, s: bytes) -> None:
        """Write null-terminated bytes into DRAM0 at offset 0."""
        self.mcp.sim.memory.write(0x00000000, s + b"\x00")

    def test_uart_print_assembles(self):
        with open("source/uart_print.asm") as f:
            result = self.mcp.pru_load(f.read(), core="pru0")
        assert result["success"] is True
        assert result["errors"] == []

    def test_uart_print_halts_on_empty_string(self):
        """Null byte at offset 0 → immediate halt."""
        with open("source/uart_print.asm") as f:
            src = f.read()
        self._load_fast(src)
        self.mcp.sim.memory.write(0x00000000, b"\x00")
        run = self.mcp.pru_run_until(core="pru0", max_steps=100)
        assert run["reason"] == "halted"

    def test_uart_print_single_char_bit_sequence(self):
        """Single 'U' (0x55) must produce same frame as uart_tx."""
        with open("source/uart_print.asm") as f:
            src = f.read()
        self._load_fast(src)
        self._write_string(b"U")
        transitions = _collect_transitions(self.mcp, max_steps=300)
        assert transitions == [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1], (
            f"Expected alternating 11-state frame for 'U', got: {transitions}"
        )

    def test_uart_print_pin_ends_high_after_string(self):
        """After transmitting 'Hi', TX pin is HIGH (idle)."""
        with open("source/uart_print.asm") as f:
            src = f.read()
        self._load_fast(src)
        self._write_string(b"Hi")
        run = self.mcp.pru_run_until(core="pru0", max_steps=10000)
        assert run["reason"] == "halted"
        assert self.mcp.pru_io(core="pru0")["gpo_pins"][0] == 1
