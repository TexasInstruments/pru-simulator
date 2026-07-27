"""End-to-end tests for pru_uart_inject MCP tool."""
import pytest
from pathlib import Path

from mcp_server.server import PRUSimulatorMCP


class TestPRUUARTInjectMCP:
    """Test the pru_uart_inject MCP tool method."""

    def test_basic_inject_returns_received_data(self, nominal_config):
        """pru_uart_inject should load assembly, inject frames, and return received data."""
        mcp = PRUSimulatorMCP(nominal_config)
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()

        result = mcp.pru_uart_inject(
            source=asm_source,
            payload=[0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21],
            baudrate=4_000_000,
            frames=1,
        )

        assert result["status"] == "success"
        assert result["frames_received"] == 1
        assert result["received_data"] == [0x48, 0x65, 0x6C, 0x6C, 0x6F, 0x57, 0x6F, 0x72, 0x6C, 0x64, 0x21]
        assert result["error_flag"] == 0

    def test_inject_no_error_with_clean_data(self, nominal_config):
        """Clean transmission should have error_flag == 0."""
        mcp = PRUSimulatorMCP(nominal_config)
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()

        result = mcp.pru_uart_inject(
            source=asm_source,
            payload=[0x41] * 11,
            baudrate=4_000_000,
            frames=1,
        )

        assert result["status"] == "success"
        assert result["error_flag"] == 0

    def test_inject_multiple_frames(self, nominal_config):
        """pru_uart_inject with frames=2 should receive both frames."""
        mcp = PRUSimulatorMCP(nominal_config)
        asm_source = (Path(__file__).parent.parent / "source" / "uart_rx_11frame.asm").read_text()

        result = mcp.pru_uart_inject(
            source=asm_source,
            payload=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B],
            baudrate=4_000_000,
            frames=2,
        )

        assert result["status"] == "success"
        assert result["frames_received"] == 2
        # First frame data
        assert result["received_data"] == [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B]
        # All data includes both frames
        expected_all = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B] * 2
        assert result["all_data"] == expected_all

    def test_inject_with_invalid_source(self, nominal_config):
        """Invalid assembly should return error status."""
        mcp = PRUSimulatorMCP(nominal_config)

        result = mcp.pru_uart_inject(
            source="mov r0\n",
            payload=[0x41] * 11,
        )

        assert result["status"] == "error"
        assert len(result["errors"]) > 0
