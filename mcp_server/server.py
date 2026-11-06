"""MCP Server wrapper exposing PRU Simulator methods as MCP-compatible tool functions."""

import sys
import os
import base64
import binascii
import struct
import copy
import math
import random

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator import Simulator
from pru_io.device_profiles import create_device, discover_device_profiles
from pru_io.device_model import PUSH_PULL
from mcp_server.vcd_export import export_pin_waveform
from core.peripheral_view import io_mode, read_crc, read_mac, read_spad
from pru_io.scenarios import ssi_demo_state


def _require_int(name: str, value) -> None:
    """Reject bool and non-int values for an integer tool parameter."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")


def _require_bool(name: str, value) -> None:
    """Accept only a real bool: strings are truthy and 0/1 are not booleans."""
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")


class PRUSimulatorMCP:
    """Wraps the Simulator and exposes its methods as MCP-compatible tool functions."""

    def __init__(self, config_path: str = "memory.cfg",
                 simulator: Simulator | None = None):
        self._config_path = config_path
        self.sim = simulator if simulator is not None else Simulator(config_path)

    def pru_gpio_drive_mask(self, core: str = "pru0", mask: int | None = None) -> dict:
        """Query or set the twenty GPIO output-enable bits without changing R30."""
        if mask is not None:
            if (isinstance(mask, bool) or not isinstance(mask, int)
                    or not 0 <= mask < (1 << 20)):
                raise ValueError("GPIO drive mask must be a 20-bit integer")
            self.sim.set_gpio_drive_mask(core, mask)
        return {"core": core, "drive_mask": self.sim.io(core)["gpo_drive_mask"]}

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
        _require_int("count", count)
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
        _require_int("count", count)
        if isinstance(guard_ns, bool) or not isinstance(guard_ns, (int, float)):
            raise ValueError("guard_ns must be a number")
        if isinstance(guard_ns, float) and not math.isfinite(guard_ns):
            raise ValueError("guard_ns must be finite")
        if count < 0:
            raise ValueError("count must be non-negative")
        if guard_ns < 0:
            raise ValueError("guard_ns must be non-negative")
        if lead == follow:
            raise ValueError("lead and follow must name different cores")

        lead_steps = 0
        for _ in range(count):
            pacing = self.sim.step_paced(lead, follow, 1, guard_ns=guard_ns)
            lead_steps += pacing["lead_steps"]
            stop = self._pacing_stop(lead, follow, pacing, lead_steps)
            if stop is not None:
                return stop

        return {
            "success": True,
            "reason": "stepped",
            "lead_steps": lead_steps,
            "lead": self._core_state(lead),
            "follow": self._core_state(follow),
        }

    def _core_state(self, core: str) -> dict:
        c = self.sim.cores[core]
        return {
            "core": core,
            "pc": c.pc,
            "cycles": c.counters.cycles,
            "halted": c.halted,
            "fault": dict(c.fault) if c.fault is not None else None,
        }

    def _pacing_stop(self, lead: str, follow: str, pacing: dict,
                     lead_steps: int) -> dict | None:
        """Failure result after a paced step (fault or incomplete pacing), else None."""
        lead_core = self.sim.cores[lead]
        follow_core = self.sim.cores[follow]
        if lead_core.fault is not None or follow_core.fault is not None:
            return {
                "success": False,
                "reason": "core_fault",
                "lead_steps": lead_steps,
                "lead": self._core_state(lead),
                "follow": self._core_state(follow),
            }
        if not pacing["complete"]:
            target_units = pacing["targets"][follow]
            follow_units = self.sim.iep.core_time_units(
                follow, follow_core.counters.cycles
            )
            target_ns = float(self.sim.iep.time_units_to_ns(target_units))
            follow_ns = float(self.sim.iep.time_units_to_ns(follow_units))
            return {
                "success": False,
                "reason": pacing["reason"],
                "lead_steps": lead_steps,
                "target_ns": float(target_ns),
                "follow_ns": float(follow_ns),
                "lead": self._core_state(lead),
                "follow": self._core_state(follow),
            }
        return None

    def _condition_checker(self, core: str, condition: str):
        """Parse a run-until condition and return a zero-argument predicate.

        Raises ValueError for a malformed condition before anything runs.
        """
        if not isinstance(condition, str):
            raise ValueError("condition must be a string")
        c = self.sim.cores[core]
        try:
            if condition == "halt":
                return lambda: c.halted
            if condition.startswith("cycles>"):
                limit = int(condition[7:], 0)
                return lambda: c.counters.cycles > limit
            if condition.startswith("reg:"):
                register, value = condition[4:].split("==", 1)
                if not register.lower().startswith("r"):
                    raise ValueError(f"Invalid register condition: {condition}")
                index = int(register[1:])
                if not 0 <= index < 32:
                    raise ValueError(f"Invalid register condition: {condition}")
                target = int(value, 0)
                return lambda: c.registers.read_full(index) == target
            if condition.startswith("mem:"):
                address, value = condition[4:].split("!=", 1)
                address = int(address, 0)
                target = int(value, 0)
                return lambda: int.from_bytes(
                    self.sim.memory_read(address, 4), "little") != target
            pin_prefix = next((prefix for prefix in ("pin:", "gpi:", "gpo:")
                               if condition.startswith(prefix)), None)
            if pin_prefix:
                pin, value = condition[len(pin_prefix):].split("==", 1)
                index = int(pin, 0)
                target = int(value, 0)
                if not 0 <= index < 20 or target not in (0, 1):
                    raise ValueError(f"Invalid pin condition: {condition}")
                kind = "gpo" if pin_prefix == "gpo:" else "gpi"
                return lambda: self.sim.io(core)[f"{kind}_pins"][index] == target
        except ValueError as exc:
            if str(exc).startswith(("Invalid ", "Unsupported ")):
                raise
            raise ValueError(f"Invalid run condition {condition!r}: {exc}") from exc
        raise ValueError(f"Unsupported run condition: {condition}")

    def pru_run_until_multicore(self, lead: str = "pru0", follow: str = "pru1",
                                condition: str = "halt", condition_core: str = "",
                                max_steps: int = 10000, guard_ns: float = 0.0) -> dict:
        """Run two time-paced cores until a condition, breakpoint or step limit.

        Steps the lead one instruction at a time and paces the follow core as
        pru_step_multicore does. ``condition`` uses the pru_run_until grammar
        (halt, cycles>N, reg:rN==V, mem:ADDR!=V, pin:/gpi:/gpo:N==V) and is
        evaluated on ``condition_core`` (default: lead); ``halt`` means that
        core halted. Stops at a breakpoint on either core (breakpoint_core).
        ``max_steps`` counts lead steps. reason: halted, condition_met,
        breakpoint, max_steps, lead_halted (lead cannot advance), core_fault,
        or a pacing reason.
        """
        _require_int("max_steps", max_steps)
        if isinstance(guard_ns, bool) or not isinstance(guard_ns, (int, float)):
            raise ValueError("guard_ns must be a number")
        if isinstance(guard_ns, float) and not math.isfinite(guard_ns):
            raise ValueError("guard_ns must be finite")
        if max_steps < 0:
            raise ValueError("max_steps must be non-negative")
        if guard_ns < 0:
            raise ValueError("guard_ns must be non-negative")
        if lead == follow:
            raise ValueError("lead and follow must name different cores")
        for name in (lead, follow):
            self.sim._get_core(name)
        condition_core = condition_core or lead
        self.sim._get_core(condition_core)
        met = self._condition_checker(condition_core, condition)
        met_reason = "halted" if condition == "halt" else "condition_met"
        lead_core = self.sim.cores[lead]

        def result(reason: str, lead_steps: int, breakpoint_core: str | None = None):
            out = {
                "success": reason in ("halted", "condition_met"),
                "reason": reason,
                "lead_steps": lead_steps,
                "lead": self._core_state(lead),
                "follow": self._core_state(follow),
            }
            if breakpoint_core is not None:
                out["breakpoint_core"] = breakpoint_core
            return out

        lead_steps = 0
        for _ in range(max_steps):
            if met():
                return result(met_reason, lead_steps)
            for name in (lead, follow):
                core = self.sim.cores[name]
                if core.pc in core.breakpoints:
                    return result("breakpoint", lead_steps, name)
            pacing = self.sim.step_paced(lead, follow, 1, guard_ns=guard_ns)
            lead_steps += pacing["lead_steps"]
            stop = self._pacing_stop(lead, follow, pacing, lead_steps)
            if stop is not None:
                return stop
            if pacing["lead_steps"] == 0 and (
                    lead_core.halted or lead_core.pc >= len(lead_core.instructions)):
                if met():
                    return result(met_reason, lead_steps)
                return result("lead_halted", lead_steps)
        if met():
            return result(met_reason, lead_steps)
        return result("max_steps", lead_steps)

    def pru_run_until(self, core: str = "pru0", condition: str = "halt",
                      max_steps: int = 10000, max_cycles: int = 0) -> dict:
        """Run until a condition, step limit, or cycle budget is reached.

        Conditions: ``halt``, ``cycles>N``, ``reg:rN==V``, ``mem:ADDR!=V``
        (32-bit little-endian), and ``pin:N==V``/``gpi:N==V``/``gpo:N==V``.
        ``pin`` is an alias for GPI; input and output predicates are never ORed.
        A positive ``max_cycles`` is an inclusive budget measured from this call.
        """
        _require_int("max_steps", max_steps)
        _require_int("max_cycles", max_cycles)
        if max_steps < 0:
            raise ValueError("max_steps must be non-negative")
        if max_cycles < 0:
            raise ValueError("max_cycles must be non-negative")
        c = self.sim.cores[core]
        start_cycles = c.counters.cycles

        condition_met = self._condition_checker(core, condition)

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
        _require_int("addr", addr)
        _require_int("length", length)
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
        _require_int("pin", pin)
        _require_bool("value", value)
        self.sim.set_input(core, pin, value)
        return {"ok": True}

    def pru_vcd_export(self, path: str, core: str = "pru0", max_steps: int = 10000,
                       pins: str = "0-19", include_gpi: bool = False) -> dict:
        """Run a loaded core and export selected GPIO pins as deterministic VCD."""
        _require_int("max_steps", max_steps)
        _require_bool("include_gpi", include_gpi)
        return export_pin_waveform(
            self.sim, core, path, max_steps=max_steps, pins=pins,
            include_gpi=include_gpi,
        )

    def pru_i2c_attach(self, core: str = "pru0", enabled: bool = True, address: int = 0x23) -> dict:
        """Attach or detach a TCA9538 I2C device model on SCL=bit0/SDA=bit1 of the specified core."""
        _require_bool("enabled", enabled)
        _require_int("address", address)
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
        default_core_clock_hz = (
            self.sim.iep.core_clock_hz(core) if profile == "ssi_encoder" else None
        )
        device = create_device(
            profile, config, default_core_clock_hz=default_core_clock_hz)
        if any(attached.name == device.name for attached in self.sim.device_bus.devices):
            raise ValueError(f"device name {device.name!r} is already attached")

        if profile == "ssi_encoder":
            drive_mask = 1 << device.clock_pin
            managed_mask = drive_mask | (1 << device.data_pin)
        else:
            drive_mask = (1 << device.scl_pin) | (1 << device.sda_pin)
            managed_mask = drive_mask
        self.sim.lease_gpio_outputs(core, managed_mask, device,
                                    drive_mask=drive_mask)
        try:
            self.sim.attach_device(core, device)
        except BaseException:
            self.sim.device_bus.release_core_outputs(device)
            raise
        return {"success": True, "core": core, "device": device.get_state()}

    def pru_device_detach(self, device_name: str) -> dict:
        """Detach a generic device by name and release its bus ownership."""
        device = self._get_device(device_name)
        self.sim.detach_device(device)
        return {"success": True, "device": device_name}

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

    def _get_device(self, name: str):
        return self.sim.device_bus.get_device(name)

    def pru_reset(self, core: str = "pru0") -> dict:
        """Reset the specified core to its initial state."""
        self.sim.reset(core)
        return {"ok": True}

    def pru_breakpoint(self, core: str = "pru0", address: int = 0) -> dict:
        """Add a breakpoint at the specified address for the specified core."""
        _require_int("address", address)
        self.sim.cores[core].breakpoints.add(address)
        return {"id": len(self.sim.cores[core].breakpoints)}

    def pru_breakpoint_list(self, core: str = "") -> dict:
        """List breakpoints (word addresses) for one core, or all cores if core is empty."""
        names = [core] if core else list(self.sim.cores)
        for name in names:
            self.sim._get_core(name)
        return {"breakpoints": {n: sorted(self.sim.cores[n].breakpoints) for n in names}}

    def pru_breakpoint_remove(self, core: str = "pru0", address: int = 0) -> dict:
        """Remove the breakpoint at address on core; removed is false if none was set."""
        _require_int("address", address)
        breakpoints = self.sim._get_core(core).breakpoints
        removed = address in breakpoints
        breakpoints.discard(address)
        return {"removed": removed, "breakpoints": sorted(breakpoints)}

    def pru_breakpoint_clear(self, core: str = "") -> dict:
        """Remove all breakpoints on one core, or on every core if core is empty."""
        names = [core] if core else list(self.sim.cores)
        cleared = 0
        for name in names:
            breakpoints = self.sim._get_core(name).breakpoints
            cleared += len(breakpoints)
            breakpoints.clear()
        return {"cleared": cleared}

    def pru_disassemble(self, core: str = "pru0", address: int = -1,
                        before: int = 5, after: int = 10) -> dict:
        """List loaded instructions around a word address (default: current PC).

        Each entry has address, text, opcode, line (source line, 0 for ELF),
        label (if any), is_pc and breakpoint. ELF programs are shown as
        disassembly. The window is clamped to the loaded program.
        """
        _require_int("address", address)
        _require_int("before", before)
        _require_int("after", after)
        if before < 0 or after < 0:
            raise ValueError("before and after must be non-negative")
        c = self.sim._get_core(core)
        instructions = c.instructions
        center = c.pc if address < 0 else address
        labels: dict[int, str] = {}
        for name, word in getattr(c._parser, "labels", {}).items():
            labels.setdefault(word, name)
        entries = []
        if instructions:
            first = min(max(0, center - before), len(instructions) - 1)
            last = max(min(len(instructions) - 1, center + after), first)
            for index in range(first, last + 1):
                inst = instructions[index]
                entry = {
                    "address": inst.address,
                    "text": inst.source_text,
                    "opcode": inst.opcode,
                    "line": inst.source_line,
                    "is_pc": inst.address == c.pc,
                    "breakpoint": inst.address in c.breakpoints,
                }
                if inst.address in labels:
                    entry["label"] = labels[inst.address]
                entries.append(entry)
        return {
            "core": core,
            "pc": c.pc,
            "program_length": len(instructions),
            "instructions": entries,
        }

    def pru_config(self) -> dict:
        """Return processor, shared PRU clock, IEP clock, available cores and config path."""
        return {
            "processor": self.sim.target,
            "pru_clock_mhz": self.sim._pru_clock_mhz,
            "iep_clock_mhz": float(self.sim.iep.active_clock_mhz),
            "available_cores": list(self.sim.cores),
            "config_path": self._config_path,
        }

    def pru_peripherals(self, core: str = "pru0") -> dict:
        """Return peripheral state for a core, as the dashboard shows it.

        io_mode (gpio/perif/sd) and gpcfg mux_sel; sd, perif and i2c blocks
        when present (else null); perif loopback; IEP timer; MAC and CRC
        accelerators; scratchpad banks; XFR shift enable; constant table
        C0-C31; attached device bus (only when active).
        """
        c = self.sim._get_core(core)
        sim = self.sim
        sd_data = sim.sd_state(core)
        perif_data = sim.perif_state(core)
        mux_sel = sim.gpcfg_state(core)["mux_sel"]
        iep = sim.iep
        out = {
            "core": core,
            "io_mode": io_mode(sim, core, sd_data, perif_data, mux_sel),
            "mux_sel": mux_sel,
            "gpio_loopback_mask": c.io_port.loopback_mask,
            "sd": sd_data,
            "perif": perif_data,
            "i2c": sim.i2c_state(core),
            "perif_loopback": sim.loopback_state() if perif_data is not None else None,
            "iep": {
                "count": iep.count,
                "clock_mhz": float(iep.active_clock_mhz),
                "external_mhz": float(iep.external_clock_hz / 1_000_000),
                "core_clock": bool(iep.iepclk & iep.IEPCLK_OCP_EN),
                "global_cfg": iep.global_cfg,
                "compare": list(iep.compare),
                "capture": list(iep.capture),
            },
            "mac": read_mac(c),
            "crc": read_crc(c),
            "spad": read_spad(sim),
            "xfr_shift_en": sim.xfr.xfr_shift_en,
            "constant_table": sim.constant_table_view(core),
            "device_bus": sim.device_state() if sim.device_bus.active else None,
        }
        demo = ssi_demo_state(sim)
        if demo is not None:
            out["ssi_demo"] = demo
        return out

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
        for name, value in (("baudrate", baudrate), ("frames", frames), ("pin", pin),
                            ("dram0_offset", dram0_offset), ("max_steps", max_steps)):
            _require_int(name, value)
        if not isinstance(payload, list) or any(
                isinstance(byte, bool) or not isinstance(byte, int) for byte in payload):
            raise ValueError("payload must be a list of integers")

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


def build_server():
    """Build an MCP server with every public PRUSimulatorMCP method registered as a tool.

    Requires the 'mcp' SDK (>= 2.0) to be installed.
    """
    import inspect
    from mcp.server import MCPServer

    server = MCPServer("pru-simulator")
    mcp_wrapper = PRUSimulatorMCP()

    # The input schema of each tool is derived from the method signature.
    for name, method in inspect.getmembers(mcp_wrapper, predicate=inspect.ismethod):
        if name.startswith("_"):
            continue
        server.add_tool(method, name=name, description=method.__doc__ or name)
    return server


def run_stdio_server():
    """Start the MCP stdio server."""
    try:
        server = build_server()
    except ImportError as exc:
        print(f"Error: could not import the 'mcp' SDK ({exc}). "
              "Install or upgrade it with: pip install 'mcp>=2.0'", file=sys.stderr)
        sys.exit(1)
    server.run("stdio")


if __name__ == "__main__":
    run_stdio_server()
