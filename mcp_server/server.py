"""MCP Server wrapper exposing PRU Simulator methods as MCP-compatible tool functions."""

import sys
import os
import base64
import binascii
import struct

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator import Simulator
from mcp_server.vcd_export import export_pin_waveform


class PRUSimulatorMCP:
    """Wraps the Simulator and exposes its methods as MCP-compatible tool functions."""

    def __init__(self, config_path: str = "memory.cfg"):
        self._config_path = config_path
        self.sim = Simulator(config_path)

    def pru_load(self, source: str, core: str = "pru0",
                 include_paths: list[str] | None = None) -> dict:
        """Parse and load assembly source into a PRU core."""
        errors = self.sim.load(core, source, include_paths)
        line_count = len([l for l in source.split('\n') if l.strip()])
        return {"success": len(errors) == 0, "errors": errors, "line_count": line_count}

    @staticmethod
    def _elf_metadata(elf_data: bytes) -> tuple[int, list[dict]]:
        """Validate an ELF32 image and return its entry point and loadable sections."""
        if len(elf_data) < 52:
            raise ValueError("File too small to be a valid ELF")
        if elf_data[:4] != b"\x7fELF":
            raise ValueError("Not an ELF file (bad magic)")
        if elf_data[4] != 1 or elf_data[5] != 1:
            raise ValueError("Expected a little-endian ELF32 image")

        elf_type, machine, version = struct.unpack_from("<HHI", elf_data, 16)
        if elf_type != 2:
            raise ValueError(f"Expected an executable ELF (ET_EXEC=2), got {elf_type}")
        if machine != 0x90:
            raise ValueError(f"Expected a TI PRU ELF (machine=0x0090), got 0x{machine:04x}")
        if version != 1:
            raise ValueError(f"Invalid ELF version: {version}")

        entry = struct.unpack_from("<I", elf_data, 24)[0]
        header_size = struct.unpack_from("<H", elf_data, 40)[0]
        if header_size != 52:
            raise ValueError(f"Invalid ELF32 header size: {header_size}")
        if entry % 4:
            raise ValueError(f"PRU ELF entry point is not instruction-aligned: 0x{entry:x}")
        section_offset = struct.unpack_from("<I", elf_data, 32)[0]
        section_size, section_count, names_index = struct.unpack_from("<HHH", elf_data, 46)
        if section_count == 0:
            raise ValueError("ELF contains no sections")
        if section_size < 40:
            raise ValueError(f"Invalid ELF section header size: {section_size}")
        if section_offset > len(elf_data) or section_count > (
                len(elf_data) - section_offset) // section_size:
            raise ValueError("ELF section header table is truncated")

        headers = []
        for index in range(section_count):
            offset = section_offset + index * section_size
            name_offset, section_type, flags, address, data_offset, size = struct.unpack_from(
                "<IIIIII", elf_data, offset)
            if section_type != 8 and size and (
                    data_offset > len(elf_data) or size > len(elf_data) - data_offset):
                raise ValueError(f"ELF section {index} data is truncated")
            headers.append({
                "name_offset": name_offset,
                "type": section_type,
                "flags": flags,
                "address": address,
                "offset": data_offset,
                "size": size,
            })

        if names_index >= len(headers):
            raise ValueError("ELF section-name table index is invalid")
        names_header = headers[names_index]
        names = elf_data[names_header["offset"]:
                         names_header["offset"] + names_header["size"]]

        def section_name(name_offset: int) -> str:
            if name_offset >= len(names):
                raise ValueError("ELF section name offset is invalid")
            end = names.find(b"\0", name_offset)
            if end < 0:
                raise ValueError("ELF section name table is truncated")
            return names[name_offset:end].decode("ascii", errors="replace")

        sections = []
        for header in headers:
            name = section_name(header["name_offset"])
            if ((name.startswith(".text") or name == ".data")
                    and header["type"] != 8 and header["size"] > 0):
                sections.append({
                    "name": name,
                    "address": header["address"],
                    "size": header["size"],
                })
        if not sections:
            raise ValueError("ELF contains no loadable .text or .data sections")
        return entry, sections

    def pru_elf_load(self, core: str = "pru0", path: str = "", b64: str = "") -> dict:
        """Load a PRU ELF image from a filesystem path or base64-encoded bytes."""
        errors = []
        entry = None
        sections = []
        try:
            if bool(path) == bool(b64):
                raise ValueError("Provide exactly one of 'path' or 'b64'")
            if path:
                with open(path, "rb") as elf_file:
                    elf_data = elf_file.read()
            else:
                elf_data = base64.b64decode(b64, validate=True)
            entry, sections = self._elf_metadata(elf_data)
            text_sections = sorted(
                (section for section in sections if section["name"].startswith(".text")),
                key=lambda section: section["address"],
            )
            entry_pc = None
            preceding_words = 0
            for section in text_sections:
                if section["size"] % 4:
                    raise ValueError(
                        f"ELF text section {section['name']} size is not word-aligned")
                if section["address"] <= entry < section["address"] + section["size"]:
                    entry_pc = preceding_words + (entry - section["address"]) // 4
                    break
                preceding_words += section["size"] // 4
            if entry_pc is None:
                raise ValueError(
                    f"ELF entry point 0x{entry:x} is not inside a loadable text section")

            # Load transactionally into a fresh simulator.  Besides ensuring a
            # failed load cannot corrupt the current session, this prevents
            # stale shared memory, other-core state, or breakpoints from
            # contaminating a supposedly independent ELF run.
            candidate = Simulator(self._config_path)
            errors = candidate.load_elf(core, elf_data)
            if not errors:
                candidate.cores[core].pc = entry_pc
                self.sim = candidate
        except (OSError, ValueError, binascii.Error) as exc:
            errors = [f"ELF load failed: {exc}"]
        if errors:
            entry = None
            sections = []
        return {
            "success": len(errors) == 0,
            "errors": errors,
            "entry": entry,
            "sections": sections,
        }

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

    def pru_vcd_export(self, path: str, core: str = "pru0", max_steps: int = 10000,
                       pins: str = "0-19", include_gpi: bool = False) -> dict:
        """Run a loaded core and export selected GPIO pins as deterministic VCD."""
        return export_pin_waveform(
            self.sim, core, path, max_steps=max_steps, pins=pins,
            include_gpi=include_gpi,
        )

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
