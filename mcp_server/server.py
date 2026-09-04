"""MCP Server wrapper exposing PRU Simulator methods as MCP-compatible tool functions."""

import sys
import os

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from simulator import Simulator
from pru_io import foc_abi
from pru_io import ssi_config_abi as abi
from pru_io.foc_runtime import FocRuntime
from pru_io.ssi_runtime import SSIRuntime, PROFILES


class PRUSimulatorMCP:
    """Wraps the Simulator and exposes its methods as MCP-compatible tool functions."""

    def __init__(self, config_path: str = "memory.cfg"):
        self.sim = Simulator(config_path)
        self._runtime: SSIRuntime | None = None
        self._foc_runtime: FocRuntime | None = None

    def _get_runtime(self) -> SSIRuntime:
        """Lazily construct the SSIRuntime on first SSI-tool use.

        SSIRuntime(sim) blocks until a loaded PRU core acks its implicit
        default-profile apply (see ssi_runtime.py's module docstring), so it
        can't be constructed in __init__ -- no core is loaded yet at that
        point.
        """
        if self._runtime is None:
            self._runtime = SSIRuntime(self.sim)
        return self._runtime

    def pru_load(self, source: str, core: str = "pru0",
                 include_paths: list[str] | None = None) -> dict:
        """Parse and load assembly source into a PRU core."""
        errors = self.sim.load(core, source, include_paths)
        line_count = len([l for l in source.split('\n') if l.strip()])
        return {"success": len(errors) == 0, "errors": errors, "line_count": line_count}

    def pru_step(self, core: str = "pru0", count: int = 1) -> dict:
        """Execute count instructions on the specified core."""
        result = self.sim.step(core, count)
        # Add instruction_text from last executed instruction
        c = self.sim.cores[core]
        inst_text = ""
        if c.pc > 0 and c.pc - 1 < len(c.instructions):
            inst_text = c.instructions[c.pc - 1].source_text
        result["instruction_text"] = inst_text
        return result

    def pru_run_until(self, core: str = "pru0", condition: str = "halt", max_steps: int = 10000) -> dict:
        """Run the core until halted or max_steps reached."""
        c = self.sim.cores[core]
        for _ in range(max_steps):
            if c.halted:
                break
            c.step()
        return {
            "pc": c.pc,
            "cycles": c.counters.cycles,
            "reason": "halted" if c.halted else "max_steps",
        }

    def pru_registers(self, core: str = "pru0") -> dict:
        """Return all 32 general-purpose register values for the specified core."""
        regs = self.sim.registers(core)
        result = {f"r{i}": f"0x{regs[i]:08x}" for i in range(32)}
        result["carry"] = self.sim.cores[core].registers.carry
        return result

    def pru_memory(self, addr: int, length: int) -> dict:
        """Read length bytes from shared memory at addr."""
        data = self.sim.memory_read(addr, length)
        return {
            "hex_dump": data.hex(),
            "ascii": ''.join(chr(b) if 32 <= b < 127 else '.' for b in data),
        }

    def pru_io(self, core: str = "pru0") -> dict:
        """Return I/O pin state for the specified core."""
        return self.sim.io(core)

    def pru_set_input(self, core: str = "pru0", pin: int = 0, value: bool = False) -> dict:
        """Set a single GPI pin on the specified core's I/O port."""
        self.sim.set_input(core, pin, value)
        return {"ok": True}

    def pru_i2c_attach(self, core: str = "pru0", enabled: bool = True, address: int = 0x23) -> dict:
        """Attach or detach a TCA9538 I2C device model on SCL=bit0/SDA=bit1 of the specified core."""
        self.sim.i2c_attach(core, enabled, address)
        return {"success": True, "core": core, "enabled": enabled, "address": address}

    def pru_reset(self, core: str = "pru0") -> dict:
        """Reset the specified core to its initial state."""
        self.sim.reset(core)
        return {"ok": True}

    def pru_breakpoint(self, core: str = "pru0", address: int = 0) -> dict:
        """Add a breakpoint at the specified address for the specified core."""
        self.sim.cores[core].breakpoints.add(address)
        return {"id": len(self.sim.cores[core].breakpoints)}

    def pru_uart_inject(
        self,
        source: str,
        payload: list[int],
        baudrate: int = 4_000_000,
        frames: int = 1,
        core: str = "pru0",
        pin: int = 0,
        dram0_offset: int = 0,
        max_steps: int = 20_000,
    ) -> dict:
        """Inject UART frames into PRU and verify reception.

        Loads assembly source, attaches a UARTFrameGenerator, runs the PRU,
        and returns the received data from DRAM0.
        """
        # Reset and load
        self.sim.reset(core)
        errors = self.sim.load(core, source)
        if errors:
            return {"status": "error", "errors": errors}

        # Clear DRAM0 storage area and error flag
        frame_size = len(payload)
        total_bytes = frame_size * frames
        clear_data = bytes(total_bytes)
        self.sim.memory.write(dram0_offset, clear_data)
        self.sim.memory.write(0x0FFE, bytes(1))

        # Inject UART frames
        self.sim.uart_inject(
            core=core,
            pin=pin,
            payload=payload,
            baudrate=baudrate,
            trigger_cycle=5,
            frames=frames,
        )

        # Run PRU
        self.sim.step(core, count=max_steps)

        # Read results
        received_data = self.sim.memory_read(dram0_offset, total_bytes)
        err_data = self.sim.memory_read(0x0FFE, 1)

        # Count frames received by checking frame counter register (R20)
        pru = self.sim.cores[core]
        frames_received = pru.registers.read_full(20)

        return {
            "status": "success",
            "frames_received": frames_received,
            "received_data": list(received_data[:frame_size]),
            "all_data": list(received_data),
            "error_flag": err_data[0],
        }

    def pru_ssi_inject(
        self,
        source: str,
        value: int = 0,
        bits: int = 12,
        clk_pin: int = 0,
        data_pin: int = 8,
        core: str = "pru0",
        dram0_offset: int = 16,
        max_steps: int = 20_000,
    ) -> dict:
        """Inject an SSI encoder reading into a PRU reader and verify capture.

        Loads assembly source, attaches an SSIEncoderGenerator that emulates an
        absolute encoder (straight binary, MSB-first) on the reader's data-in
        pin, runs the reader, and reads back the captured word.

        The reader firmware convention stores the captured word to DRAM0 offset
        16 (4 bytes, little-endian) and keeps a frame counter in R20. *value*
        is the position the encoder presents; *bits* its width.
        """
        self.sim.reset(core)
        errors = self.sim.load(core, source)
        if errors:
            return {"status": "error", "errors": errors}

        self.sim.memory.write(dram0_offset, bytes(4))
        self.sim.ssi_inject(
            core=core,
            clk_pin=clk_pin,
            data_pin=data_pin,
            value=value,
            bits=bits,
        )
        self.sim.step(core, count=max_steps)

        raw = self.sim.memory_read(dram0_offset, 4)
        captured = int.from_bytes(raw, "little") & ((1 << bits) - 1)
        pru = self.sim.cores[core]
        frames_captured = pru.registers.read_full(20)
        expected = value & ((1 << bits) - 1)
        return {
            "status": "success",
            "expected": expected,
            "expected_hex": f"0x{expected:03x}",
            "captured": captured,
            "captured_hex": f"0x{captured:03x}",
            "match": captured == expected,
            "frames_captured": frames_captured,
            "cycles": pru.counters.cycles,
        }

    def pru_foc_inject(
        self,
        source: str,
        speed_rpm: float = 0.0,
        id_ref: float = 0.0,
        iq_ref: float = 0.0,
        ramp_rate: float = 0.0,
        core: str = "pru0",
        max_steps: int = 20_000,
    ) -> dict:
        """Load open-loop FOC firmware, attach the plant, and return shared state."""
        self.sim.hard_reset()
        errors = self.sim.load(core, source)
        if errors:
            return {"status": "error", "errors": errors}

        self.sim.iep.write_iepclk(1)
        self.sim.iep.write_global_cfg(0x11)
        runtime = FocRuntime(self.sim)
        self._foc_runtime = runtime
        runtime.set_reference(
            speed=float(speed_rpm) / foc_abi.SPEED_BASE_RPM,
            id=float(id_ref),
            iq=float(iq_ref),
            ramp=float(ramp_rate),
        )
        runtime.start()
        self.sim.step(core, count=max(0, int(max_steps)))
        state = runtime.state()
        return {
            "status": "success",
            "control": state["control"],
            "pwm": state["pwm"],
            "fb": state["fb"],
            "model": state["model"],
            "cycles": self.sim.cores[core].counters.cycles,
        }

    def ssi_profile_list(self) -> dict:
        """List every named SSI encoder profile ssi_stage can reference by name."""
        return {"profiles": list(PROFILES.keys())}

    def ssi_stage(self, profile: str = "", overrides_json: str = "{}") -> dict:
        """Stage (but don't yet commit) an SSI encoder configuration.

        ``profile`` is a name from ssi_profile_list, or "" to keep staging
        on top of whatever was last staged/applied (SSIRuntime.stage()'s
        own None convention). ``overrides_json`` is a JSON object string of
        config-field-name -> value overrides applied on top of the
        profile's defaults. Nothing reaches shared memory here; call
        ssi_apply for that. A validation failure (unknown profile/field
        name, an out-of-range value, etc.) is returned as
        {"status": "error", "error": <message>} rather than raised, so an
        MCP caller always gets a JSON-able result back -- this also covers
        malformed ``overrides_json`` (invalid JSON, or valid JSON that
        isn't an object) rather than letting json.loads/**overrides raise
        past this method.
        """
        runtime = self._get_runtime()
        try:
            overrides = json.loads(overrides_json)
            runtime.stage(profile if profile else None, **overrides)
        except (ValueError, TypeError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "success", "staged": dict(runtime._staged)}

    def ssi_set_positions(
        self,
        positions_json: str = "[]",
        statuses_json: str = "",
        position_count: int = 0,
        gray_excess_offset: int = -1,
    ) -> dict:
        """Pack natural positions into the staged emulator frame slots.

        Values accept JSON integers or hexadecimal strings (with or without
        ``0x``). This is intentionally separate from ``ssi_stage`` and
        ``ssi_apply``: callers can stage a new shape, pack slots, then commit
        the whole generation with ``ssi_apply``.
        """
        runtime = self._get_runtime()

        def parse_values(payload: str, label: str) -> list[int]:
            values = json.loads(payload)
            if not isinstance(values, list):
                raise ValueError(f"{label} must be a JSON array")
            parsed = []
            for raw in values:
                if isinstance(raw, bool):
                    raise ValueError(f"invalid {label} value: {raw!r}")
                if isinstance(raw, int):
                    value = raw
                elif isinstance(raw, str):
                    token = raw.strip()
                    value = int(token, 16)
                else:
                    raise ValueError(f"invalid {label} value: {raw!r}")
                if value < 0:
                    raise ValueError(f"invalid {label} value: {raw!r}")
                parsed.append(value)
            return parsed

        try:
            positions = parse_values(positions_json, "position")
            statuses = None
            if statuses_json:
                statuses = parse_values(statuses_json, "status")
            count = position_count if position_count > 0 else None
            offset = gray_excess_offset if gray_excess_offset >= 0 else None
            runtime.set_positions(
                positions,
                statuses,
                position_count=count,
                gray_excess_offset=offset,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "success", "frames": runtime.read_raw_frames()}

    def ssi_apply(self, timeout_steps: int = 200_000) -> dict:
        """Commit the currently staged SSI config to shared memory and block
        (stepping the simulator up to timeout_steps times) until both PRU
        cores ack the new generation. Returns {"status": "timeout", ...}
        instead of raising if the ack never arrives within the budget,
        alongside the requested/pru0/pru1 ack generations either way.
        """
        runtime = self._get_runtime()
        try:
            runtime.apply(timeout_steps)
            status = "success"
        except TimeoutError:
            status = "timeout"

        def read_gen(offset: int) -> int:
            return int.from_bytes(
                self.sim.memory_read(abi.CONFIG_BASE + offset, 4), "little"
            )

        return {
            "status": status,
            "requested_generation": read_gen(abi.CONFIG_REQUESTED_GENERATION_OFF),
            "pru0_ack_generation": read_gen(abi.CONFIG_PRU0_ACK_GENERATION_OFF),
            "pru1_ack_generation": read_gen(abi.CONFIG_PRU1_ACK_GENERATION_OFF),
        }

    def ssi_read_mailbox(self) -> dict:
        """Seqlock-safe read of the latest-sample SSI mailbox, with
        position_value already semantically decoded per the currently
        staged encoding_type/position_width_bits."""
        return self._get_runtime().read_mailbox()

    def ssi_read_trace(self, newest_first: bool = True, limit: int = 100) -> dict:
        """Read back currently-valid SSI trace-buffer records (a 1,024-slot
        ring buffer). newest_first orders the returned records most-recent
        first (oldest-first if False); limit caps the count (most recent N,
        or oldest N if not newest_first). Also reports the raw
        write_index/overrun_count counters.
        """
        runtime = self._get_runtime()
        records = runtime.read_trace(newest_first=newest_first, limit=limit)
        write_index = int.from_bytes(
            self.sim.memory_read(
                abi.CAPTURE_BASE + abi.CAPTURE_TRACE_WRITE_INDEX_OFF, 4
            ),
            "little",
        )
        overrun_count = int.from_bytes(
            self.sim.memory_read(
                abi.CAPTURE_BASE + abi.CAPTURE_TRACE_OVERRUN_COUNT_OFF, 4
            ),
            "little",
        )
        return {
            "records": records,
            "overrun_count": overrun_count,
            "write_index": write_index,
        }

    def ssi_producer_configure(
        self,
        trajectory: str = "constant",
        initial_position: str = "0",
        velocity_counts_per_second: float = 0.0,
        triangle_low: str = "0",
        triangle_high: str = "4095",
        period_iep_ticks: int = 288,
    ) -> dict:
        """Configure the timestamped ARM producer model in encoder-count units."""
        runtime = self._get_runtime()

        def parse_count(value, name):
            if isinstance(value, bool):
                raise ValueError(f"{name} must be an integer count")
            if isinstance(value, int):
                return value
            return int(str(value).strip(), 0)

        try:
            state = runtime.configure_producer_engineering(
                trajectory=trajectory,
                initial_position=parse_count(initial_position, "initial_position"),
                velocity_counts_per_second=velocity_counts_per_second,
                triangle_low=parse_count(triangle_low, "triangle_low"),
                triangle_high=parse_count(triangle_high, "triangle_high"),
                period_iep_ticks=period_iep_ticks,
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}
        return {"status": "success", "producer": state}

    def ssi_producer_start(self) -> dict:
        """Start automatic timestamped publication at the configured cadence."""
        runtime = self._get_runtime()
        runtime.start_producer()
        return runtime.producer_state()

    def ssi_producer_stop(self) -> dict:
        """Stop automatic publication while preserving the last coherent sample."""
        runtime = self._get_runtime()
        runtime.stop_producer()
        return runtime.producer_state()

    def ssi_producer_step(self, timestamp_iep: int = -1) -> dict:
        """Publish one timestamped sample manually, using current IEP time by default."""
        runtime = self._get_runtime()
        sample = runtime.step_producer(None if timestamp_iep < 0 else timestamp_iep)
        return {"sample": sample, "producer": runtime.producer_state()}

    def ssi_producer_read(self) -> dict:
        """Read producer controls, counters, and PRU0 estimator diagnostics."""
        runtime = self._get_runtime()
        return {
            **runtime.producer_state(),
            "diagnostics": runtime.read_producer_diagnostics(),
        }

    def pru_status(self) -> dict:
        """Return a status snapshot for all cores."""
        return {"cores": self.sim.status()}


def run_stdio_server():
    """Start the MCP stdio server. Requires the 'mcp' SDK to be installed."""
    try:
        import mcp  # noqa: F401
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
        import asyncio
        import inspect
        import json

        server = Server("pru-simulator")
        mcp_wrapper = PRUSimulatorMCP()

        # Build tool list from PRUSimulatorMCP public methods
        _TOOLS = []
        for name, method in inspect.getmembers(mcp_wrapper, predicate=inspect.ismethod):
            if name.startswith("_"):
                continue
            sig = inspect.signature(method)
            properties = {}
            required = []
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                ptype = "string"
                annotation = param.annotation
                if annotation in (int,):
                    ptype = "integer"
                elif annotation in (bool,):
                    ptype = "boolean"
                prop = {"type": ptype}
                if param.default is inspect.Parameter.empty:
                    required.append(param_name)
                else:
                    prop["default"] = param.default
                properties[param_name] = prop
            _TOOLS.append(Tool(
                name=name,
                description=method.__doc__ or name,
                inputSchema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            ))

        @server.list_tools()
        async def list_tools():
            return _TOOLS

        @server.call_tool()
        async def call_tool(name, arguments):
            method = getattr(mcp_wrapper, name, None)
            if method is None:
                raise ValueError(f"Unknown tool: {name}")
            # Some MCP clients may wrap arguments as {"kwargs": "<json-string>"}.
            # Unwrap that form so individual parameters reach the method.
            if list(arguments.keys()) == ["kwargs"]:
                real_args = json.loads(arguments["kwargs"])
            else:
                real_args = arguments
            result = method(**real_args)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        async def main():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        asyncio.run(main())

    except ImportError:
        print("Error: 'mcp' SDK is not installed. Install it with: pip install mcp", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_stdio_server()
