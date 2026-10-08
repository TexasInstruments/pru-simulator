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
                "pru_gpio_drive_mask", "pru_sd_route_input",
            }
            assert required <= by_name.keys()
            def property_schema(name, field):
                tool = by_name[name]
                schema = getattr(tool, "input_schema", None)
                if schema is None:
                    schema = tool.inputSchema
                prop = schema["properties"][field]
                variants = prop.get("anyOf", [prop])
                return next(variant for variant in variants
                            if variant.get("type") != "null")

            assert property_schema("pru_device_attach", "config")["type"] == "object"
            include_paths = property_schema("pru_load", "include_paths")
            assert include_paths["type"] == "array"
            assert include_paths["items"]["type"] == "string"
            assert property_schema("pru_gpio_drive_mask", "mask")["type"] == "integer"
            for arguments, expected in [({}, 0), ({"core": "pru1", "mask": 7}, 7),
                                        ({"core": "pru1"}, 7)]:
                result = _payload(await session.call_tool("pru_gpio_drive_mask", arguments))
                assert result["drive_mask"] == expected
            loaded = _payload(await session.call_tool(
                "pru_load", {"source": "nop\nhalt", "include_paths": []}))
            assert loaded["success"] is True

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
            assert attached_foc["device"]["model"] == "pmsm"
            routed = _payload(await session.call_tool(
                "pru_sd_route_input", {"channel": 0, "pin": 3}))
            assert routed == {"channel": 0, "pin": 3}
            restored = _payload(await session.call_tool(
                "pru_sd_route_input", {"channel": 0, "pin": -1}))
            assert restored == {"channel": 0, "pin": None}
            assert _payload(await session.call_tool(
                "pru_device_detach", {"device_name": "foc_motor"}))[
                    "success"] is True


def test_generic_device_tools_use_the_official_sdk_stdio_transport():
    asyncio.run(_exercise_stdio_tools())
