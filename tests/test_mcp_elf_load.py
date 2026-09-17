"""Acceptance tests for MCP ELF loading."""

from pathlib import Path

import pytest

from mcp_server.server import PRUSimulatorMCP


ELF_FIXTURE = Path(__file__).parents[1] / "references" / "pru_encoding_test.out"


def fresh_mcp() -> PRUSimulatorMCP:
    return PRUSimulatorMCP(config_path="nonexistent.cfg")


def test_elf_load_reports_metadata_honors_entry_and_resets_all_state():
    mcp = fresh_mcp()
    assert mcp.pru_load("ldi r0, 1\nhalt")["success"]
    assert mcp.pru_run_until()["reason"] == "halted"
    mcp.sim.memory.write(0, b"stale")
    mcp.pru_breakpoint(address=7)

    result = mcp.pru_elf_load(path=str(ELF_FIXTURE))

    assert result["success"] is True
    assert result["entry"] is not None
    assert result["sections"]
    state = mcp.sim.cores["pru0"]
    assert (state.pc, state.counters.cycles, state.halted) == (
        result["entry"] // 4, 0, False)
    assert mcp.sim.memory_read(0, 5) == bytes(5)
    assert state.breakpoints == set()


@pytest.mark.parametrize(
    ("offset", "replacement", "message"),
    [
        (16, b"\x00\x00", "ET_EXEC"),
        (40, b"\x00\x00", "header size"),
    ],
)
def test_elf_load_rejects_malformed_header(offset, replacement, message):
    data = bytearray(ELF_FIXTURE.read_bytes())
    data[offset:offset + len(replacement)] = replacement

    import base64
    result = fresh_mcp().pru_elf_load(b64=base64.b64encode(data).decode("ascii"))

    assert result["success"] is False
    assert result["entry"] is None
    assert result["sections"] == []
    assert message in result["errors"][0]
