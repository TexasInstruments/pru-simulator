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


def _input_schema(tool):
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = tool.inputSchema
    return schema


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
                "pru_sd_route_input", "pru_load", "pru_step", "pru_run_until",
                "pru_status",
            }
            assert required <= by_name.keys()
            attach_tool = by_name["pru_device_attach"]
            attach_schema = _input_schema(attach_tool)
            assert attach_schema[
                "properties"]["config"]["type"] == "object"

            load_schema = _input_schema(by_name["pru_load"])
            include_paths_schema = load_schema["properties"]["include_paths"]
            assert include_paths_schema["type"] == "array"
            assert include_paths_schema["items"]["type"] == "string"
            assert "include_paths" not in load_schema.get("required", [])

            discovered = _payload(await session.call_tool(
                "pru_device_discover", {}))
            assert set(discovered["profiles"]) == {"ssi_encoder", "tca9538", "foc_motor"}

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

            attached_foc = _payload(await session.call_tool(
                "pru_device_attach", {"profile": "foc_motor"}))
            assert attached_foc["success"] is True
            assert attached_foc["device"]["model"] == "three_phase_rl"
            routed = _payload(await session.call_tool(
                "pru_sd_route_input", {"channel": 0, "pin": 3}))
            assert routed == {"channel": 0, "pin": 3}
            restored = _payload(await session.call_tool(
                "pru_sd_route_input", {"channel": 0, "pin": -1}))
            assert restored == {"channel": 0, "pin": None}
            assert _payload(await session.call_tool(
                "pru_device_detach", {"device_name": "foc_motor"}))[
                    "success"] is True

            for core, firmware in (
                ("pru0", "source/foc_open_loop.asm"),
                ("pru1", "source/ssi_generic_reader.asm"),
            ):
                loaded = _payload(await session.call_tool("pru_load", {
                    "core": core,
                    "source": (ROOT / firmware).read_text(encoding="utf-8"),
                    "include_paths": ["source"],
                }))
                assert loaded["success"] is True, loaded["errors"]
                executed = _payload(await session.call_tool("pru_run_until", {
                    "core": core, "condition": "halt", "max_steps": 1000,
                }))
                assert executed["condition_met"] is True
                assert executed["reason"] == "halted"
                assert executed["cycles"] > 0
                reset = _payload(await session.call_tool(
                    "pru_reset", {"core": core}))
                assert reset["success"] is True


def test_generic_device_tools_use_the_official_sdk_stdio_transport():
    asyncio.run(_exercise_stdio_tools())
