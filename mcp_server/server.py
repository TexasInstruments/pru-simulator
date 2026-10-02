"""MCP Server wrapper exposing PRU Simulator methods as MCP-compatible tool functions."""

import sys
import os
import base64
import binascii
import struct
import copy
import random
import inspect
from typing import get_args, get_origin

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator import Simulator
from pru_io.device_profiles import create_device, discover_device_profiles
from pru_io.device_model import PUSH_PULL
from mcp_server.vcd_export import export_pin_waveform


def _build_tool_input_schema(method) -> dict:
    """Build an MCP JSON schema from a wrapper method's signature."""
    properties = {}
    required = []
    sig = inspect.signature(method)
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        annotation = param.annotation
        annotated_type = annotation
        union_args = get_args(annotation)
        if type(None) in union_args:
            annotated_type = next(arg for arg in union_args if arg is not type(None))

        if get_origin(annotated_type) is list and get_args(annotated_type) == (str,):
            prop = {"type": "array", "items": {"type": "string"}}
        else:
            ptype = "string"
            if annotated_type is int:
                ptype = "integer"
            elif annotated_type is float:
                ptype = "number"
            elif annotated_type is bool:
                ptype = "boolean"
            elif annotated_type is dict:
                ptype = "object"
            prop = {"type": ptype}

        if param.default is inspect.Parameter.empty:
            required.append(param_name)
        else:
            prop["default"] = param.default
        properties[param_name] = prop

    return {"type": "object", "properties": properties, "required": required}


class PRUSimulatorMCP:
    """Wraps the Simulator and exposes its methods as MCP-compatible tool functions."""

    def __init__(self, config_path: str = "memory.cfg",
                 simulator: Simulator | None = None):
        self._config_path = config_path
        self.sim = simulator if simulator is not None else Simulator(config_path)
        self._device_drive_masks: dict[str, tuple[str, int]] = {}

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
                self.sim.device_bus.detach_all()
                self.sim.device_bus.release_all_core_outputs()
                self._restore_device_output_masks(self.sim)
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

    def pru_step_multicore(self, lead: str = "pru0", follow: str = "pru1",
                           count: int = 1, guard_ns: float = 0.0) -> dict:
        """Step two cores with elapsed-time pacing and return both core states.

        ``guard_ns`` is how far the follow core may trail the lead while both
        Peripheral Interfaces are enabled. Otherwise both cores are paced by
        exact elapsed core time with no guard.
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
            self.sim.step_paced(lead, follow, 1, guard_ns=guard_ns)
            lead_perif = self.sim._perif.get(lead)
            follow_perif = self.sim._perif.get(follow)
            use_guard = (
                lead_perif is not None and follow_perif is not None
                and lead_perif.enabled and follow_perif.enabled
            )
            lead_core = self.sim.cores[lead]
            follow_core = self.sim.cores[follow]
            target_units = self.sim.iep.core_time_units(
                lead, lead_core.counters.cycles
            ) - (
                self.sim.iep.nanoseconds_to_units(guard_ns) if use_guard else 0
            )
            follow_units = self.sim.iep.core_time_units(
                follow, follow_core.counters.cycles
            )
            if follow_units < target_units:
                target_ns = float(self.sim.iep.time_units_to_ns(target_units))
                follow_ns = float(self.sim.iep.time_units_to_ns(follow_units))
                return {
                    "success": False,
                    "reason": "pacing_catchup_failed",
                    "target_ns": float(target_ns),
                    "follow_ns": float(follow_ns),
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

    def pru_device_discover(self) -> dict:
        """Discover supported generic device profiles and attached devices."""
        return {
            "profiles": discover_device_profiles(),
            "devices": self.sim.device_state()["devices"],
        }

    def pru_device_attach(self, profile: str, core: str = "pru0",
                          config: dict | None = None) -> dict:
        """Attach one validated generic device profile to a core's GPIO pins."""
        profile_info = discover_device_profiles().get(profile)
        clock_field = profile_info.get("sim_clock_field") if profile_info else None
        if clock_field and (config is None or isinstance(config, dict)
                            and clock_field not in config):
            if config is None:
                config = {}
            config = {**config,
                      clock_field: float(self.sim.iep.core_clock_hz(core))}
        default_core_clock_hz = (
            self.sim.iep.core_clock_hz(core) if profile == "ssi_encoder" else None
        )
        device = create_device(
            profile, config, default_core_clock_hz=default_core_clock_hz)
        supported_cores = profile_info.get("supported_cores") if profile_info else None
        if supported_cores is not None and core not in supported_cores:
            raise ValueError(f"{profile} is only supported on {', '.join(supported_cores)}")
        if profile == "foc_motor":
            sd = self.sim.cores[core].io_port.sd_filter
            for channel in range(2):
                clock_hz = float(sd.modulators[channel].sd_clock_mhz) * 1_000_000
                clock_field = ("current_a_clock_hz", "current_b_clock_hz")[channel]
                if config is None or clock_field not in config:
                    device.set_current_modulator_clock_hz(channel, clock_hz)
            self.sim._sync_sd_device_clocks(core, devices=[device])
        if any(attached.name == device.name for attached in self.sim.device_bus.devices):
            raise ValueError(f"device name {device.name!r} is already attached")
        attached_on_core = [
            attached for attached in self.sim.device_bus.devices
            if self.sim.device_bus.core_for_device(attached) == core
        ]
        mask_owner = next((name for name, (owner_core, _) in self._device_drive_masks.items()
                           if owner_core == core), None)
        if mask_owner is not None or (
                getattr(device, "pru_output_mask", None) is not None
                and attached_on_core):
            owner = mask_owner or profile
            raise ValueError(
                f"{owner} controls the {core} GPIO output mask and cannot share that core"
            )

        # A push-pull device output owns that pin while attached. Open-drain
        # lines remain driven by the PRU so I2C masters can pull SDA/SCL low.
        previous_mask = self.sim.io(core)["gpo_drive_mask"]
        output_mask = sum(1 << pin for pin, mode in device.nets.items()
                          if mode == PUSH_PULL)
        if output_mask:
            self.sim.lease_gpio_outputs(core, output_mask, device)
        self.sim.attach_device(core, device)
        device_drive_mask = getattr(device, "pru_output_mask", None)
        if device_drive_mask is not None:
            self._device_drive_masks[device.name] = (core, previous_mask)
            self.sim.set_gpio_drive_mask(core, device_drive_mask)
        return {"success": True, "core": core, "device": device.get_state()}

    def pru_device_detach(self, device_name: str) -> dict:
        """Detach a generic device by name and release its bus ownership."""
        device = self._get_device(device_name)
        core = self.sim.device_bus.core_for_device(device)
        self.sim.detach_device(device)
        saved_drive = self._device_drive_masks.pop(device_name, None)
        if saved_drive is not None:
            drive_core, previous_mask = saved_drive
            if drive_core == core:
                self.sim.set_gpio_drive_mask(core, previous_mask)
        return {"success": True, "device": device_name}

    def pru_device_configure(self, device_name: str, parameters: dict) -> dict:
        """Change an attached device's run-time parameters (all or nothing)."""
        device = self._get_device(device_name)
        applied = device.configure(parameters)
        return {"success": True, "device": device_name, "parameters": applied}

    def pru_device_state(self) -> dict:
        """Return attached device state, bus levels, events, and faults."""
        return self.sim.device_state()

    def pru_device_events(self, device_name: str = "") -> dict:
        """Read generic bus events, optionally filtered to one device."""
        if device_name:
            device = self._get_device(device_name)
            return {"device": device_name, "events": device.events()}
        return {"events": self.sim.device_bus.events()}

    def pru_device_faults(self, device_name: str = "") -> dict:
        """Read generic bus/device faults, optionally filtered to one device."""
        if device_name:
            device = self._get_device(device_name)
            return {
                "device": device_name,
                "faults": self.sim.device_bus.faults_for_device(device),
                "contentions": self.sim.device_bus.contentions_for_device(device),
            }
        return {"faults": self.sim.device_bus.faults()}

    def pru_sd_route_input(self, channel: int, pin: int = -1) -> dict:
        """Route an SD channel to a physical GPI pin; pin=-1 restores its modulator."""
        sd = self.sim.cores["pru0"].io_port.sd_filter
        selected_pin = None if pin == -1 else pin
        sd.route_input(channel, selected_pin)
        return {"channel": channel, "pin": selected_pin}

    def _get_device(self, name: str):
        return self.sim.device_bus.get_device(name)

    def _restore_device_output_masks(self, sim: Simulator) -> None:
        for core, mask in self._device_drive_masks.values():
            sim.set_gpio_drive_mask(core, mask)
        self._device_drive_masks.clear()

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
        import json

        mcp_wrapper = PRUSimulatorMCP()

        # Build tool list from PRUSimulatorMCP public methods
        _TOOLS = []
        for name, method in inspect.getmembers(mcp_wrapper, predicate=inspect.ismethod):
            if name.startswith("_"):
                continue
            _TOOLS.append(Tool(
                name=name,
                description=method.__doc__ or name,
                inputSchema=_build_tool_input_schema(method),
            ))

        def call_tool_content(name, arguments):
            method = getattr(mcp_wrapper, name, None)
            if method is None:
                raise ValueError(f"Unknown tool: {name}")
            result = method(**arguments)
            return TextContent(type="text", text=json.dumps(result, indent=2))

        if "on_list_tools" in inspect.signature(Server).parameters:
            from mcp.types import CallToolResult, ListToolsResult

            async def list_tools(ctx, params):
                return ListToolsResult(tools=_TOOLS)

            async def call_tool(ctx, params):
                return CallToolResult(content=[call_tool_content(
                    params.name, params.arguments or {})])

            server = Server(
                "pru-simulator",
                on_list_tools=list_tools,
                on_call_tool=call_tool,
            )
        else:
            server = Server("pru-simulator")

            @server.list_tools()
            async def list_tools():
                return _TOOLS

            @server.call_tool()
            async def call_tool(name, arguments):
                return [call_tool_content(name, arguments)]

        async def main():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        asyncio.run(main())

    except ImportError:
        print("Error: 'mcp' SDK is not installed. Install it with: pip install mcp", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_stdio_server()
