"""Exercise the real MCP SDK stdio transport, not only wrapper methods."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).parents[1]


def _payload(result):
    is_error = getattr(result, "is_error", None)
    if is_error is None:
        is_error = result.isError
    assert not is_error
    return json.loads(result.content[0].text)


async def _exercise_stdio_tools():
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp_server" / "server.py")],
        cwd=ROOT,
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            by_name = {tool.name: tool for tool in tools.tools}
            required = {
                "pru_device_discover", "pru_device_attach", "pru_device_detach",
                "pru_device_state", "pru_device_events", "pru_device_faults",
                "pru_load", "pru_step", "pru_run_until", "pru_status",
            }
            assert required <= by_name.keys()
            attach_tool = by_name["pru_device_attach"]
            attach_schema = getattr(attach_tool, "input_schema", None)
            if attach_schema is None:
                attach_schema = attach_tool.inputSchema
            assert attach_schema[
                "properties"]["config"]["type"] == "object"

            discovered = _payload(await session.call_tool(
                "pru_device_discover", {}))
            assert set(discovered["profiles"]) == {"ssi_encoder", "tca9538"}

            attached = _payload(await session.call_tool(
                "pru_device_attach", {
                    "core": "pru1",
                    "profile": "ssi_encoder",
                    "config": {"name": "sdk_encoder", "data_pin": 16},
                }))
            assert attached["success"] is True

            state = _payload(await session.call_tool("pru_device_state", {}))
            assert state["devices"][0]["name"] == "sdk_encoder"
            assert _payload(await session.call_tool(
                "pru_device_events", {"device_name": "sdk_encoder"}))[
                    "events"] == []
            assert _payload(await session.call_tool(
                "pru_device_faults", {"device_name": "sdk_encoder"}))[
                    "faults"] == []
            assert _payload(await session.call_tool(
                "pru_device_detach", {"device_name": "sdk_encoder"}))[
                    "success"] is True


def test_generic_device_tools_use_the_official_sdk_stdio_transport():
    asyncio.run(_exercise_stdio_tools())
