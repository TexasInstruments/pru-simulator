"""MCP Server wrapper exposing PRU Simulator methods as MCP-compatible tool functions."""

import sys
import os
import copy
import random

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator import Simulator


class PRUSimulatorMCP:
    """Wraps the Simulator and exposes its methods as MCP-compatible tool functions."""

    def __init__(self, config_path: str = "memory.cfg"):
        self.sim = Simulator(config_path)

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

    def pru_step_multicore(self, lead: str = "pru0", follow: str = "pru1",
                           count: int = 1, guard_ns: float = 0.0) -> dict:
        """Step two cores with peripheral-clock pacing and return both core states.

        ``guard_ns`` is how far the follow core may trail the lead core.  The
        MCP default is zero so even short validation programs advance both
        cores; callers modelling a receiver guard may request a positive lag.
        """
        if count < 0:
            raise ValueError("count must be non-negative")
        if guard_ns < 0:
            raise ValueError("guard_ns must be non-negative")
        if lead == follow:
            raise ValueError("lead and follow must name different cores")

        def state(core: str) -> dict:
            c = self.sim.cores[core]
            return {
                "core": core,
                "pc": c.pc,
                "cycles": c.counters.cycles,
                "halted": c.halted,
            }

        for _ in range(count):
            follow_pru = self.sim.cores[follow]
            follow_cycles = follow_pru.counters.cycles
            self.sim.step_paced(lead, follow, 1, guard_ns=guard_ns)
            # Peripheral time does not advance for ordinary ALU-only programs.
            # A user-facing "step both" tool must still retire one instruction
            # on the follower instead of returning two plausible-looking states
            # after advancing only the lead core.
            if (follow_pru.counters.cycles == follow_cycles
                    and not follow_pru.halted
                    and follow_pru.pc < len(follow_pru.instructions)):
                follow_pru.step()

            lead_perif = self.sim._perif.get(lead)
            follow_perif = self.sim._perif.get(follow)
            if lead_perif is not None and follow_perif is not None:
                target_ns = lead_perif._now_ns - guard_ns
                if follow_perif._now_ns < target_ns:
                    return {
                        "success": False,
                        "reason": "pacing_catchup_failed",
                        "target_ns": target_ns,
                        "follow_ns": follow_perif._now_ns,
                        "lead": state(lead),
                        "follow": state(follow),
                    }

        return {
            "success": True,
            "reason": "stepped",
            "lead": state(lead),
            "follow": state(follow),
        }

    def pru_run_until(self, core: str = "pru0", condition: str = "halt",
                      max_steps: int = 10000, max_cycles: int = 0) -> dict:
        """Run until a condition, step limit, or cycle budget is reached.

        Conditions: ``halt``, ``cycles>N``, ``reg:rN==V``, ``mem:ADDR!=V``
        (32-bit little-endian), and ``pin:N==V``/``gpi:N==V``/``gpo:N==V``.
        ``pin`` is an alias for GPI; input and output predicates are never ORed.
        A positive ``max_cycles`` is an inclusive budget measured from this call.
        """
        if max_steps < 0:
            raise ValueError("max_steps must be non-negative")
        if max_cycles < 0:
            raise ValueError("max_cycles must be non-negative")
        c = self.sim.cores[core]
        start_cycles = c.counters.cycles

        def condition_met() -> bool:
            if condition == "halt":
                return c.halted
            if condition.startswith("cycles>"):
                return c.counters.cycles > int(condition[7:], 0)
            if condition.startswith("reg:"):
                register, value = condition[4:].split("==", 1)
                if not register.lower().startswith("r"):
                    raise ValueError(f"Invalid register condition: {condition}")
                index = int(register[1:])
                if not 0 <= index < 32:
                    raise ValueError(f"Invalid register condition: {condition}")
                return c.registers.read_full(index) == int(value, 0)
            if condition.startswith("mem:"):
                address, value = condition[4:].split("!=", 1)
                actual = int.from_bytes(self.sim.memory_read(int(address, 0), 4), "little")
                return actual != int(value, 0)
            pin_prefix = next((prefix for prefix in ("pin:", "gpi:", "gpo:")
                               if condition.startswith(prefix)), None)
            if pin_prefix:
                pin, value = condition[len(pin_prefix):].split("==", 1)
                index = int(pin, 0)
                target = int(value, 0)
                if not 0 <= index < 20 or target not in (0, 1):
                    raise ValueError(f"Invalid pin condition: {condition}")
                io_state = self.sim.io(core)
                kind = "gpo" if pin_prefix == "gpo:" else "gpi"
                return io_state[f"{kind}_pins"][index] == target
            raise ValueError(f"Unsupported run condition: {condition}")

        reason = None
        for _ in range(max_steps):
            if condition_met():
                reason = "halted" if condition == "halt" else "condition_met"
                break
            if c.pc in c.breakpoints:
                reason = "breakpoint"
                break
            if max_cycles:
                # An instruction may add memory stall cycles.  Predict it on a
                # private copy, and do not mutate the live simulator unless the
                # complete instruction fits inside the inclusive budget.
                rng_state = random.getstate()
                try:
                    projected = copy.deepcopy(self.sim)
                    projected_core = projected.cores[core]
                    projected_core.step()
                finally:
                    # Memory read jitter uses the module RNG.  The live step
                    # must draw the same value as the projection.
                    random.setstate(rng_state)
                projected_delta = projected_core.counters.cycles - c.counters.cycles
                if c.counters.cycles - start_cycles + projected_delta > max_cycles:
                    return {
                        "pc": c.pc,
                        "cycles": c.counters.cycles,
                        "reason": "budget_exceeded",
                        "budget_exceeded": True,
                        "condition_met": False,
                    }
            c.step()
        if reason is None and condition_met():
            reason = "halted" if condition == "halt" else "condition_met"
        return {
            "pc": c.pc,
            "cycles": c.counters.cycles,
            "reason": reason or "max_steps",
            "budget_exceeded": False,
            "condition_met": reason in ("halted", "condition_met"),
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
                elif annotation in (float,):
                    ptype = "number"
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
            result = method(**arguments)
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
