"""Exercise the real MCP SDK stdio transport, not only wrapper methods."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pru_io import ssi_config_abi as ssi


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
                "pru_load", "pru_step", "pru_run_until", "pru_status",
                "pru_gpio_drive_mask",
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

            load_schema = _input_schema(by_name["pru_load"])
            assert "include_paths" not in load_schema.get("required", [])

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

            for core, firmware in (
                ("pru1", "source/ssi_generic_reader/ssi_generic_reader.asm"),
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
                assert reset == {"ok": True}


def test_generic_device_tools_use_the_official_sdk_stdio_transport():
    asyncio.run(_exercise_stdio_tools())


async def _exercise_valid_firmware(tmp_path):
    server = StdioServerParameters(command=sys.executable,
        args=[str(ROOT / "mcp_server" / "server.py")], cwd=ROOT)
    async with asyncio.timeout(60):
        async with stdio_client(server) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()

                async def call(name, **arguments):
                    return _payload(await session.call_tool(name, arguments))

                async def configure(address, data):
                    words = [int.from_bytes(data[i:i+4], "little")
                             for i in range(0, len(data), 4)]
                    source = [f"ldi32 r0, {address}"]
                    source += [f"ldi32 r{i}, {word}" for i, word in enumerate(words, 1)]
                    source += [f"sbbo &r1, r0, 0, {len(data)}", "halt"]
                    assert (await call("pru_load", source="\n".join(source)))["success"]
                    assert (await call("pru_run_until", condition="halt", max_steps=100))["condition_met"]
                    await call("pru_reset")

                await call("pru_device_attach", profile="ssi_encoder", core="pru1",
                           config={"data_pin": 16, "position": 0xABC})
                await configure(ssi.CONFIG_ADDRESS, ssi.pack_config(
                    frame_bits=12, clock_delay_loops=20, idle_delay_loops=2571))
                # The reader reaches Shared RAM through C28: program PRU1 CTPPR0 (0x24028), as the host does.
                await configure(0x24028, (0x100).to_bytes(4, "little"))
                assert (await call("pru_load", core="pru1", include_paths=["source"],
                    source=(ROOT / "source/ssi_generic_reader/ssi_generic_reader.asm").read_text()))["success"]
                assert not (await call("pru_step", core="pru1", count=14000))["halted"]
                raw = await call("pru_memory", addr=ssi.MAILBOX_ADDRESS, length=ssi.MAILBOX_SIZE)
                mailbox = ssi.unpack_mailbox(bytes.fromhex(raw["hex_dump"]))
                assert mailbox["frame_count"] >= 2 and mailbox["raw_frame_lo"] == 0xABC
                assert mailbox["raw_frame_hi"] == 0
                assert mailbox["status"] == 0
                assert not (await call("pru_device_faults", device_name="ssi_encoder"))["faults"]
                await call("pru_device_detach", device_name="ssi_encoder")



def test_valid_ssi_frames_over_sdk_stdio(tmp_path):
    asyncio.run(_exercise_valid_firmware(tmp_path))
