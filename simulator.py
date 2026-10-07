"""Simulator — top-level orchestrator wiring two PRU cores to shared memory and XFR buses.

Exposes a simple API for loading assembly, stepping, inspecting registers,
reading memory, and querying I/O state.
"""

import configparser
import copy
import os
import re

from core.pru_core import PRUCore
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from perif.iep import IepTimer, IEP_SIZE
from mem.constant_table import ConstantTable
from xfr.xfr_bus import XFRBus
from pru_io.io_port import IOPort
from pru_io.sd_filter import SigmaDeltaFilter
from pru_io.sd_registers import SDRegisters
from pru_io.tca9538 import TCA9538Device
from perif.peripheral_interface import PeripheralInterface
from perif.perif_registers import PerifRegisters
from perif.gpcfg import GpcfgRegisters, MUX_PERIF
from perif.loopback import Loopback

_GPCFG_INDEX = {"pru0": 0, "pru1": 1}   # rtu0 has no GPCFG GP-mux (TRM)


class SDRegisterRegion(MemoryRegion):
    """Memory region backed by SD configuration registers with write callbacks.

    Note: MemoryRegion.__init__ allocates an unused _data bytearray (32 bytes).
    All reads/writes are delegated to SDRegisters._data. As a result, the
    step-back snapshot feature in server.py does not capture SD register state.
    """

    def __init__(self, sd_registers: SDRegisters):
        super().__init__("ICSS_SD_CFG", 0x00026044, 0x20, 2, 1, 0)
        self._sd_regs = sd_registers

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        return self._sd_regs.read(addr, length)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, length=len(data))
        self._sd_regs.write(addr, data)


class PerifRegisterRegion(MemoryRegion):
    """Memory region backed by a per-core Peripheral Interface register block."""

    def __init__(self, perif_registers: PerifRegisters, base_addr: int):
        super().__init__("ICSS_PERIF_CFG", base_addr, 0x20, 2, 1, 0)
        self._regs = perif_registers

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        return self._regs.read(addr, length)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, length=len(data))
        self._regs.write(addr, data)


class GpcfgRegion(MemoryRegion):
    """Memory region backed by the GPCFG0/1 registers (mux mode select)."""

    def __init__(self, gpcfg: GpcfgRegisters):
        super().__init__("ICSS_GPCFG", 0x00026008, 0x08, 2, 1, 0)
        self._gpcfg = gpcfg

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        return self._gpcfg.read(addr, length)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, length=len(data))
        self._gpcfg.write(addr, data)


class IepRegisterRegion(MemoryRegion):
    """Memory region backed by the IEP0 timer (counter + compare registers)."""

    def __init__(self, iep: IepTimer, base_addr: int = 0x0002E000):
        super().__init__("ICSS_IEP", base_addr, IEP_SIZE, 2, 1, 0)
        self._iep = iep

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        return self._iep.read(addr - self.base_addr, length)

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, length=len(data))
        self._iep.write(addr - self.base_addr, data)


class IepClockRegisterRegion(MemoryRegion):
    """ICSS CFG IEPCLK word at C4 + 0x30; bit 0 selects the OCP clock."""

    BASE_ADDR = 0x00026030

    def __init__(self, iep: IepTimer):
        super().__init__("ICSS_IEPCLK", self.BASE_ADDR, 4, 2, 1, 0)
        self._iep = iep

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        local = addr - self.base_addr
        return self._iep.iepclk.to_bytes(4, "little")[local : local + length]

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, length=len(data))
        local = addr - self.base_addr
        value = bytearray(self._iep.iepclk.to_bytes(4, "little"))
        value[local : local + len(data)] = data
        self._iep.write_iepclk(int.from_bytes(value, "little"))


class Simulator:
    """Orchestrates ICSSG cores sharing memory, XFR, and one IEP timer."""

    def __init__(self, config_path: str = "memory.cfg"):
        self.xfr = XFRBus()
        self.memory = self._load_memory(config_path)
        self.constant_table = self._load_constants(config_path)
        dev = self._get_device_config(config_path)
        pru_clock = dev.get("pru_clock_mhz", "250")
        pru1_clock = dev.get("pru1_clock_mhz", pru_clock)
        iep_clock = dev.get("iep_clock_mhz", pru_clock)
        io_pru0 = IOPort()
        io_rtu0 = IOPort()
        io_pru1 = IOPort()
        io_rtu1 = IOPort()
        self.cores: dict[str, PRUCore] = {
            "pru0": PRUCore("PRU0", self.memory, self.xfr, io_pru0, copy.deepcopy(self.constant_table)),
            "rtu0": PRUCore("RTU0", self.memory, self.xfr, io_rtu0, copy.deepcopy(self.constant_table)),
            "pru1": PRUCore("PRU1", self.memory, self.xfr, io_pru1, copy.deepcopy(self.constant_table),
                            dram_swap=True),
            "rtu1": PRUCore("RTU1", self.memory, self.xfr, io_rtu1, copy.deepcopy(self.constant_table),
                            dram_swap=True),
        }

        if dev.get("target", "AM243x").lower() not in ("am243x", "am64x"):
            del self.cores["rtu1"]

        # Wire SD filters to each core's IOPort (slice 1 runs on its own clock)
        pru_clock_mhz = float(pru_clock)
        pru1_clock_mhz = float(pru1_clock)
        self._pru_clock_mhz = pru_clock_mhz
        self._pru1_clock_mhz = pru1_clock_mhz
        core_clocks = {"pru0": pru_clock_mhz, "rtu0": pru_clock_mhz,
                       "pru1": pru1_clock_mhz, "rtu1": pru1_clock_mhz}
        for name, core in self.cores.items():
            core.io_port.sd_filter = SigmaDeltaFilter(pru_clock_mhz=core_clocks[name])

        # Wire SD registers into memory bus (pru0 owns the register region)
        pru0_sd = self.cores["pru0"].io_port.sd_filter
        pru0_sd.registers = SDRegisters()
        self.memory.add_region(SDRegisterRegion(pru0_sd.registers))

        # RTU0 shares the same register space (same physical hardware)
        rtu0_sd = self.cores["rtu0"].io_port.sd_filter
        rtu0_sd.registers = pru0_sd.registers

        # Wire callback to update both cores' channel state on register writes
        def _combined_config_change(ch, field, value):
            pru0_sd._on_config_change(ch, field, value)
            rtu0_sd._on_config_change(ch, field, value)
        pru0_sd.registers.on_config_change = _combined_config_change

        # ---- Peripheral Interface (3-channel SCU): PRU0 + PRU1 --------------
        # TRM: GPCFG1_REG (0x2600C) and block 0x26100 belong to PRU1, not RTU0.
        uart_clock_mhz = float(dev.get("uart_clock_mhz", "192"))
        core_order = ["pru0", "pru1"]
        perif_bases = {"pru0": 0x260E0, "pru1": 0x26100}
        self._perif = {}
        for name in core_order:
            core = self.cores[name]
            perif = PeripheralInterface(pru_clock_mhz=core_clocks[name],
                                        uart_clock_mhz=uart_clock_mhz)
            perif.registers = PerifRegisters(perif_bases[name])
            perif.build_channels()
            core.io_port.perif = perif
            self.memory.add_region(PerifRegisterRegion(perif.registers, perif_bases[name]))
            self._perif[name] = perif

        # GPCFG mux-select register: PRU_GP_MUX_SEL == 1 enables Peripheral mode
        self._gpcfg = GpcfgRegisters()

        def _on_mux_change(pru_index: int, mux_sel: int) -> None:
            name = core_order[pru_index] if pru_index < len(core_order) else None
            if name is not None:
                self._perif[name].enabled = (mux_sel == MUX_PERIF)
        self._gpcfg.on_mux_change = _on_mux_change
        self.memory.add_region(GpcfgRegion(self._gpcfg))

        # IEP0 timer. Shared by all cores on the ICSSG, like the real peripheral.
        core_clock_rates = {
            "pru0": pru_clock,
            "rtu0": pru_clock,
            "pru1": pru1_clock,
            "rtu1": pru1_clock,
        }
        self.iep = IepTimer(
            clock_mhz=iep_clock,
            ocp_clock_mhz=pru_clock,
            core_clocks_mhz=core_clock_rates,
        )
        self.memory.add_region(IepRegisterRegion(self.iep))
        self.memory.add_region(IepClockRegisterRegion(self.iep))
        for name, core in self.cores.items():
            core.iep = self.iep

            def observe_cycles(elapsed_cycles: int, *, _name=name, _core=core) -> None:
                self.iep.observe_core_cycles(_name, _core.counters.cycles)

            core.cycle_observer = observe_cycles
            core.reset_observer = lambda *, _name=name: self.iep.rebase_core(_name)

        # Loopback: PRU0 TX channel-N -> PRU1 RX channel-N.
        self._loopback = Loopback(self._perif["pru0"], self._perif["pru1"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_memory(self, config_path: str) -> MemoryBus:
        """Create and return a MemoryBus populated from *config_path*.

        Falls back to a default set of regions when the file does not exist.
        """
        bus = MemoryBus()

        if not os.path.exists(config_path):
            bus.add_region(MemoryRegion("DRAM0",       0x00000000, 0x2000,  2,  1,  0))
            bus.add_region(MemoryRegion("DRAM1",       0x00002000, 0x2000,  2,  1,  0))
            bus.add_region(MemoryRegion("ICSS_SHARED", 0x00010000, 0x10000, 2,  1,  0))
            bus.add_region(MemoryRegion("MS_RAM",      0x80000000, 0x10000, 40, 1, 10))
            return bus

        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        for section in cfg.sections():
            if section == "device":
                continue
            if "base" in cfg[section]:
                bus.add_region(MemoryRegion(
                    name=section,
                    base_addr=int(cfg[section]["base"], 16),
                    size=int(cfg[section]["size"], 16),
                    read_latency=int(cfg[section].get("read_latency", "0")),
                    write_latency=int(cfg[section].get("write_latency", "0")),
                    jitter=int(cfg[section].get("jitter", "0")),
                ))
        return bus

    def _load_constants(self, config_path: str) -> ConstantTable:
        """Load constants from the project root or its config directory."""
        table = ConstantTable()
        project_root = os.path.dirname(os.path.abspath(config_path))
        candidates = (
            os.path.join(project_root, "config", "constants_am243x.cfg"),
            os.path.join(project_root, "constants_am243x.cfg"),
        )
        constants_path = next(
            (path for path in candidates if os.path.exists(path)),
            None,
        )
        if constants_path is None:
            return table
        cfg = configparser.ConfigParser()
        cfg.read(constants_path)
        if "constants" in cfg:
            for key, val in cfg["constants"].items():
                m = re.match(r'^c(\d+)$', key)
                if m:
                    table.set(int(m.group(1)), int(val, 0))
        return table

    def _get_device_config(self, config_path: str) -> dict:
        """Read [device] section from config file."""
        if not os.path.exists(config_path):
            return {}
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        if "device" in cfg:
            return dict(cfg["device"])
        return {}

    def _get_core(self, core: str) -> PRUCore:
        """Return the PRUCore for *core* name, raising KeyError for unknown names."""
        try:
            return self.cores[core]
        except KeyError:
            raise KeyError(f"Unknown core '{core}'. Available: {list(self.cores)}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, core: str, source: str, include_paths: list[str] | None = None) -> list[str]:
        """Parse and load assembly *source* into *core*.

        Returns a list of error strings (empty on success).
        """
        return self._get_core(core).load_asm(source, include_paths)

    def load_elf(self, core: str, elf_data: bytes) -> list[str]:
        """Parse ELF .out file and load into *core*.

        Returns a list of error strings (empty on success).
        """
        from core.elf_loader import load_elf, ElfParseError
        errors: list[str] = []
        try:
            image = load_elf(elf_data)
            errors = self._get_core(core).load_binary(
                image.text_words, image.data_bytes, image.data_addr, image.symbols)
        except ElfParseError as exc:
            errors.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ELF load failed: {exc}")
        return errors

    def step(self, core: str, count: int = 1) -> dict:
        """Execute *count* instructions on *core*.

        Returns a dict with keys: pc, cycles, stall_cycles, halted, fault.
        """
        pru = self._get_core(core)
        for _ in range(count):
            pru.step()
        return {
            "pc": pru.pc,
            "cycles": pru.counters.cycles,
            "stall_cycles": pru.counters.stall_cycles,
            "halted": pru.halted,
            "fault": dict(pru.fault) if pru.fault is not None else None,
        }

    def step_paced(self, lead: str, follow: str, count: int = 1,
                   guard_ns: float = 20.0) -> None:
        """Step *lead* and pace *follow* by elapsed core time."""
        self.step_paced_many(lead, [follow], count=count, guard_ns=guard_ns)

    def step_paced_many(self, lead: str, followers: list[str], count: int = 1,
                        guard_ns: float = 20.0) -> None:
        """Step a lead core and catch each follower up by exact elapsed time.

        When both cores have enabled PERIF blocks, *guard_ns* keeps the follower
        behind the lead's line history. If either PERIF is absent or disabled,
        the follower catches up to the lead's exact core time without a guard.
        PERIF clocks continue to advance while mux-disabled; the mux controls
        signal routing, and the guard applies only while both routes are active.
        """
        lead_pru = self._get_core(lead)
        lead_perif = self._perif.get(lead)
        follower_states = [
            (follower, self._get_core(follower), self._perif.get(follower))
            for follower in followers
        ]
        guard_units = self.iep.nanoseconds_to_units(guard_ns)
        for _ in range(count):
            if not lead_pru.halted and lead_pru.pc < len(lead_pru.instructions):
                lead_pru.step()
            lead_time = self.iep.core_time_units(lead, lead_pru.counters.cycles)
            for follower, follow_pru, follow_perif in follower_states:
                use_guard = (
                    lead_perif is not None and follow_perif is not None
                    and lead_perif.enabled and follow_perif.enabled
                )
                target = lead_time - (guard_units if use_guard else 0)
                while (
                    self.iep.core_time_units(follower, follow_pru.counters.cycles) < target
                    and not follow_pru.halted
                    and follow_pru.pc < len(follow_pru.instructions)
                ):
                    follow_pru.step()

    def registers(self, core: str) -> list[int]:
        """Return the 32 general-purpose register values for *core*."""
        pru = self._get_core(core)
        return [pru.registers.read_full(i) for i in range(32)]

    def memory_read(self, addr: int, length: int) -> bytes:
        """Read *length* bytes from the shared memory bus at *addr*."""
        data, _stalls = self.memory.read(addr, length)
        return data

    def io(self, core: str) -> dict:
        """Return I/O pin state for *core*.

        Returns a dict with keys:
          gpo_pins — list of 20 ints (0 or 1) representing GPO (R30) pins
          gpi_pins — list of 20 ints (0 or 1) representing GPI (R31) pins
        """
        pru = self._get_core(core)
        return {
            "gpo_pins": pru.io_port.get_gpo_pins(),
            "gpi_pins": pru.io_port.get_gpi_pins(),
        }

    def set_input(self, core: str, pin: int, value: bool) -> None:
        """Set a single GPI pin on *core*'s I/O port."""
        self._get_core(core).io_port.set_gpi_pin(pin, value)

    def set_loopback(self, core: str, group: int, enabled: bool) -> None:
        """Enable/disable GPO→GPI loopback for a 4-bit *group* (0–4) on *core*."""
        self._get_core(core).io_port.set_loopback_group(group, enabled)

    def sd_state(self, core: str) -> dict | None:
        """Return SD filter state for *core*, or None if no SD filter attached."""
        pru = self._get_core(core)
        if pru.io_port.sd_filter is None:
            return None
        sd = pru.io_port.sd_filter
        state = sd.get_state()
        # Add register config per channel if registers are wired
        if sd.registers is not None:
            for i, ch_state in enumerate(state["channels"]):
                ch_state["config"] = sd.registers.get_channel_config(i)
        return state

    def perif_state(self, core: str) -> dict | None:
        """Return Peripheral Interface state for *core*, or None if not attached."""
        perif = self._get_core(core).io_port.perif
        return perif.get_state() if perif is not None else None

    def i2c_attach(self, core: str, enabled: bool, address: int = 0x23) -> None:
        """Attach or detach a TCA9538 device model on *core*'s SCL/SDA
        (R30/R31 bits 0/1)."""
        pru = self._get_core(core)
        pru.io_port.attach_i2c_device(TCA9538Device(address) if enabled else None)

    def i2c_state(self, core: str) -> dict | None:
        """Return TCA9538 device state for *core*, or None if not attached."""
        dev = self._get_core(core).io_port.i2c_device
        return dev.get_state() if dev is not None else None

    def gpcfg_write(self, core: str, mux_sel: int) -> None:
        """Set the GPCFG PRU_GP_MUX_SEL for *core* (0=GP, 1=Perif, 3=SD)."""
        idx = _GPCFG_INDEX.get(core)
        if idx is None:
            return          # rtu0: no GPCFG mux — ignore (keeps WS server robust)
        self._gpcfg.set_mux_sel(idx, mux_sel)

    def gpcfg_state(self, core: str) -> dict:
        """Return the current GPCFG PRU_GP_MUX_SEL for *core*."""
        idx = _GPCFG_INDEX.get(core)
        return {"mux_sel": self._gpcfg.get_mux_sel(idx) if idx is not None else 0}

    def write_perif_register(self, core: str, addr: int, value: int) -> None:
        """Write a 32-bit Peripheral Interface config register on *core*."""
        perif = self._get_core(core).io_port.perif
        if perif is not None and perif.registers is not None:
            perif.registers.write(addr, (value & 0xFFFFFFFF).to_bytes(4, "little"))

    def perif_loopback(self, channel: int, enabled: bool, latency_ns: float = 0.0,
                       jitter_ns: float = 0.0, drift_ppm: float = 0.0) -> None:
        """Configure the PRU0-TX -> core-1-RX loopback for *channel*."""
        self._loopback.configure(channel, enabled, latency_ns, jitter_ns, drift_ppm)

    def loopback_state(self) -> dict:
        """Return the loopback configuration for the UI."""
        return self._loopback.get_state()

    def set_sd_modulator(self, core: str, channel: int, **params) -> None:
        """Update pattern generator parameters for *channel* on *core*."""
        sd = self._get_core(core).io_port.sd_filter
        if sd is None:
            return
        if channel < 0 or channel >= len(sd.modulators):
            raise ValueError(f"Channel {channel} out of range")
        mod = sd.modulators[channel]
        for key, val in params.items():
            if hasattr(mod, key):
                setattr(mod, key, val)

    def reset(self, core: str) -> None:
        """Reset *core* to its initial state (registers, counters, PC, halted flag)."""
        self._get_core(core).reset()

    def set_strict_unsupported_xfr(self, enabled: bool) -> None:
        """Choose what happens when firmware drives an unmodelled XFR device ID.

        The default (``False``) keeps the hardware-faithful result -- XIN reads
        zeros, XOUT is ignored -- and records the event.  ``True`` makes such a
        transfer raise :class:`~core.pru_core.UnsupportedXFRError` instead, for
        callers that would rather a run fail than continue on zero data.
        """
        for core in self.cores.values():
            core.strict_unsupported_xfr = enabled

    def unsupported_xfr_report(self) -> list[dict]:
        """Return one record per unmodelled XFR device ID seen since reset.

        Each record carries the device ID, the core and PC that first used it,
        the opcodes involved and how many transfers were made, so a caller can
        tell a run that exercised a real model from one that read zeros.
        """
        return [record
                for core in self.cores.values()
                for record in core.unsupported_xfr.values()]

    def hard_reset(self) -> None:
        """Full hardware reset: reset all cores, clear all SPAD banks, and reset XFR config.

        Each core's reset also clears its IO port and Peripheral Interface state
        (TX/RX FIFOs, overrun/underrun, RX valid/overflow, busy, line history),
        so the UI's status bits start clean.  Configuration entered in the UI --
        perif config registers, GPCFG mux, loopback parameters -- is kept.
        """
        for core in self.cores.values():
            core.reset()
        self.iep.hardware_reset()
        self.xfr.reset()

    def uart_inject(
        self,
        core: str = "pru0",
        pin: int = 0,
        payload: list[int] | None = None,
        baudrate: int = 4_000_000,
        trigger_cycle: int = 0,
        frames: int = 1,
        idle_gap_bits: int = 2,
    ) -> None:
        """Attach a UARTFrameGenerator to the specified core's IOPort.

        The generator will update GPI pin state on each step() call via
        the pre-tick hook in PRUCore.step().
        """
        from pru_io.uart_frame_generator import UARTFrameGenerator

        pru = self._get_core(core)
        gen = UARTFrameGenerator(
            pin=pin,
            payload=payload if payload is not None else [],
            baudrate=baudrate,
            pru_clock_mhz=self._pru_clock_mhz,
            frames=frames,
            idle_gap_bits=idle_gap_bits,
        )
        gen.attach(pru.io_port)
        gen.start(trigger_cycle=trigger_cycle)
        pru.io_port.uart_generator = gen
        pru.io_port.set_gpi_pin(pin, True)  # Set idle HIGH

    def status(self) -> dict:
        """Return a status snapshot for all cores.

        Returns a dict mapping each core name to:
          pc, cycles, stall_cycles, instruction_count, ipc, halted, fault
        """
        result: dict[str, dict] = {}
        for name, pru in self.cores.items():
            result[name] = {
                "pc": pru.pc,
                "cycles": pru.counters.cycles,
                "stall_cycles": pru.counters.stall_cycles,
                "instruction_count": pru.counters.instruction_count,
                "ipc": pru.counters.ipc,
                "halted": pru.halted,
                "fault": dict(pru.fault) if pru.fault is not None else None,
            }
        return result
