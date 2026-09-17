"""MCP Server wrapper exposing PRU Simulator methods as MCP-compatible tool functions."""

import sys
import os
import base64
import binascii
import struct
import copy
import random

# Allow imports from parent directory when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from pathlib import Path

from simulator import Simulator
from pru_io import foc_abi
from pru_io import ssi_config_abi as abi
from pru_io.foc_runtime import FocRuntime
from pru_io.ssi_runtime import SSIRuntime, PROFILES
from mcp_server.vcd_export import export_pin_waveform


class PRUSimulatorMCP:
    """Wraps the Simulator and exposes its methods as MCP-compatible tool functions."""

    def __init__(self, config_path: str = "memory.cfg"):
        self._config_path = config_path
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
        final_reason = "fault" if c.fault else (reason or "max_steps")
        result = {
            "pc": c.pc,
            "cycles": c.counters.cycles,
            "reason": final_reason,
            "budget_exceeded": final_reason == "budget_exceeded",
            "condition_met": final_reason in ("halted", "condition_met"),
        }
        # Keep main's stable non-fault response shape while exposing the
        # feature branch's structured execution fault when one occurred.
        if c.fault:
            result["fault"] = c.fault
        return result

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
        source_dir = Path(__file__).resolve().parents[1] / "source"
        errors = self.sim.load(core, source, [str(source_dir)])
        if errors:
            return {"status": "error", "errors": errors}

        self.sim.iep.write_iepclk(1)
        self.sim.iep.write_global_cfg(0x11)
        runtime = FocRuntime(self.sim, core=core)
        self._foc_runtime = runtime
        try:
            runtime.set_reference(
                speed=float(speed_rpm) / foc_abi.SPEED_BASE_RPM,
                id=float(id_ref),
                iq=float(iq_ref),
                ramp=float(ramp_rate),
            )
            runtime.start()
        except (TypeError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}
        step_result = self.sim.step(core, count=max(0, int(max_steps)))
        if step_result.get("fault") is not None:
            return {
                "status": "error",
                "error": "FOC firmware faulted during execution",
                "fault": step_result["fault"],
            }
        state = runtime.state()
        return {
            "status": "success",
            "control": state["control"],
            "pwm": state["pwm"],
            "fb": state["fb"],
            "model": state["model"],
            "clock": state["clock"],
            "telemetry": state["telemetry"],
            "session_id": state["session_id"],
            "fault": state["fault"],
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

    def pru_ssi_simple_run(self, iterations: int = 100000) -> dict:
        """Run the bundled SSI project's three real images through its harness.

        The simulator repository includes the harness, generated profile, and
        three assembly images. ``SSI_PROJECT_ROOT`` is an explicit optional
        override for a matching external workspace.
        """
        project_root = os.environ.get("SSI_PROJECT_ROOT")
        if project_root:
            harness_path = (
                Path(project_root)
                / "encoder-workspace"
                / "firmware"
                / "ccs-tests"
                / "ssi_test"
                / "tools"
                / "simulate_ssi.py"
            )
        else:
            harness_path = (
                Path(__file__).resolve().parents[1]
                / "firmware"
                / "ssi_test"
                / "tools"
                / "simulate_ssi.py"
            )
        if not harness_path.is_file():
            return {"status": "error", "error": f"SSI harness not found: {harness_path}"}
        import importlib.util

        spec = importlib.util.spec_from_file_location("ssi_mcp_harness", harness_path)
        if spec is None or spec.loader is None:
            return {"status": "error", "error": "cannot import SSI harness"}
        harness = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(harness)
        profile = harness.load_default_profile()
        return harness.run(profile, int(iterations), use_mcp=False)


def run_stdio_server():
    """Start the MCP stdio server. Requires the 'mcp' SDK to be installed."""
    try:
        import mcp  # noqa: F401
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import Tool, TextContent
        from mcp import types
        import asyncio
        import inspect
        import json

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

        def dispatch_tool(name, arguments):
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

            return TextContent(type="text", text=json.dumps(result, indent=2))

        # MCP's low-level Server API changed from decorator registration to
        # constructor callbacks. Keep both paths so the checked-in stdio
        # transport works with the SDK declared by requirements.txt as well
        # as the older SDK used by the original wrapper.
        if hasattr(Server, "list_tools"):
            server = Server("pru-simulator")

            @server.list_tools()
            async def list_tools():
                return _TOOLS

            @server.call_tool()
            async def call_tool(name, arguments):
                return [dispatch_tool(name, arguments)]
        else:
            from mcp.types import CallToolResult, ListToolsResult

            async def list_tools(_context, _params):
                return ListToolsResult(tools=_TOOLS)

            async def call_tool(_context, params):
                name = getattr(params, "name", None)
                arguments = getattr(params, "arguments", None) or {}
                return CallToolResult(content=[dispatch_tool(name, arguments)])

            server = Server(
                "pru-simulator",
                on_list_tools=list_tools,
                on_call_tool=call_tool,
            )

        async def main():
            # The current MCP SDK's stdio_server wraps stdin/stdout with
            # anyio.to_thread.  The execution environment used by the
            # simulator tests has a non-progressing anyio worker pool, while
            # asyncio's native pipe transports work normally.  Use the
            # native path for the constructor-callback API; retain the
            # historical transport for older decorator-based SDKs.
            if hasattr(Server, "list_tools"):
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(
                        read_stream, write_stream,
                        server.create_initialization_options())
                return

            import anyio
            from mcp.shared.message import SessionMessage

            class AsyncioReadStream:
                def __init__(self, reader):
                    self._reader = reader

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    try:
                        return await self.receive()
                    except anyio.EndOfStream:
                        raise StopAsyncIteration

                async def receive(self):
                    line = await self._reader.readline()
                    if not line:
                        raise anyio.EndOfStream
                    try:
                        message = types.jsonrpc_message_adapter.validate_json(
                            line, by_name=False)
                    except Exception as exc:  # noqa: BLE001
                        return exc
                    return SessionMessage(message=message)

                async def aclose(self):
                    return None

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return None

            class AsyncioWriteStream:
                def __init__(self, transport):
                    self._transport = transport

                async def send(self, session_message):
                    message = session_message.message.model_dump_json(
                        by_alias=True, exclude_unset=True) + "\n"
                    self._transport.write(message.encode("utf-8"))
                    await asyncio.sleep(0)

                async def aclose(self):
                    self._transport.close()
                    await asyncio.sleep(0)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    await self.aclose()

            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            read_protocol = asyncio.StreamReaderProtocol(reader)
            read_transport, _ = await loop.connect_read_pipe(
                lambda: read_protocol, sys.stdin.buffer)
            write_protocol = asyncio.BaseProtocol()
            write_transport, _ = await loop.connect_write_pipe(
                lambda: write_protocol, sys.stdout.buffer)
            try:
                await server.run(
                    AsyncioReadStream(reader),
                    AsyncioWriteStream(write_transport),
                    server.create_initialization_options())
            finally:
                read_transport.close()

        asyncio.run(main())

    except ImportError:
        print("Error: 'mcp' SDK is not installed. Install it with: pip install mcp", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_stdio_server()
