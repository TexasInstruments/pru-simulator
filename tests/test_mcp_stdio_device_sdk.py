"""Exercise the real MCP SDK stdio transport, not only wrapper methods."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pru_io import foc_control_abi as foc, ssi_config_abi as ssi


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

            for core, firmware in (
                ("pru0", "source/foc_open_loop.asm"),
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

                await call("pru_device_attach", profile="foc_motor")
                for channel, pin in enumerate((3, 4)):
                    await call("pru_sd_route_input", channel=channel, pin=pin)
                await configure(foc.CONTROL_ADDRESS, foc.pack_config())
                assert (await call("pru_load", include_paths=["source"],
                    source=(ROOT / "source/foc_open_loop.asm").read_text()))["success"]
                path = tmp_path / "sdk-foc.vcd"
                await call("pru_vcd_export", path=str(path), max_steps=40000,
                           pins="0-4", include_gpi=True)
                # Read actual GPIO edge timestamps independently of firmware registers.
                identifier, timestamp, previous = None, 0, 0
                rises = []
                current_identifiers = {}
                current_values = {"gpi_3": set(), "gpi_4": set()}
                for line in path.read_text().splitlines():
                    fields = line.split()
                    if len(fields) >= 5 and fields[0] == "$var":
                        if fields[4] == "gpo_0":
                            identifier = fields[3]
                        if fields[4] in current_values:
                            current_identifiers[fields[3]] = fields[4]
                    elif line.startswith("#"):
                        timestamp = int(line[1:])
                    elif identifier and line in ("0" + identifier, "1" + identifier):
                        value = int(line[0])
                        if value and not previous:
                            rises.append(timestamp)
                        previous = value
                    elif line[:1] in ("0", "1") and line[1:] in current_identifiers:
                        current_values[current_identifiers[line[1:]]].add(int(line[0]))
                periods = [b - a for a, b in zip(rises, rises[1:])]
                assert len(periods) >= 2
                assert all(abs(period - 62_500_000) <= 50_000 for period in periods)
                state = await call("pru_device_state")
                assert not state["faults"]
                motor = state["devices"][0]
                assert motor["pwm_periods"] >= 2
                assert any(abs(current) > 0.01 for current in motor["phase_currents_a"])
                assert all(values == {0, 1} for values in current_values.values())


def test_valid_ssi_frames_and_foc_pwm_over_sdk_stdio(tmp_path):
    asyncio.run(_exercise_valid_firmware(tmp_path))
