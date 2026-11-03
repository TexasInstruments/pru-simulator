"""Simulator — top-level orchestrator wiring two PRU cores to shared memory and XFR buses.

Exposes a simple API for loading assembly, stepping, inspecting registers,
reading memory, and querying I/O state.
"""

import configparser
import copy
import math
import os
import logging
import re
from fractions import Fraction

from core.operands import Immediate, Label, Register
from core.pru_core import PRUCore
from core.device_capabilities import get_device_capabilities
from mem.icssm_absent import icssg_only_regions, MODES as _ICSSG_ONLY_MODES, READ_ZERO
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from perif.iep import IepTimer, IEP_SIZE, IEP_DEFAULT_CLOCK_MHZ, NUM_CAPTURE
from mem.constant_table import (
    ConstantTable, CTRL_REG_COUNT, CTRL_REG_OFFSET, PROGRAMMABLE_FIRST)
from xfr.xfr_bus import IPC_SPAD, SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, XFRBus
from xfr.xfr2vbus_accelerator import RD_ID0, RD_ID1, WR_ID0, WR_ID1
from pru_io.device_model import DeviceBus, DeviceModel
from pru_io.io_port import IOPort
from pru_io.pdm_link import PdmLink
from pru_io.sd_filter import SigmaDeltaFilter
from pru_io.sd_registers import SDRegisters
from pru_io.tca9538 import TCA9538Device
from perif.peripheral_interface import PeripheralInterface
from perif.perif_registers import PerifRegisters
from perif.gpcfg import GpcfgRegisters, MUX_PERIF
from perif.loopback import Loopback

_GPCFG_INDEX = {"pru0": 0, "pru1": 1}   # rtu0 has no GPCFG GP-mux (TRM)
_MAX_CATCHUP_STEPS = 1000
#: Longest idle span one PRUCore.step_run() call may skip in a run mode.
IDLE_SKIP_MAX_CYCLES = 1 << 16

#: Shared-map windows whose contents follow the IEP's global time, which is
#: the furthest core's time: (start, end, name).
_IEP_WINDOWS = ((0x0002E000, 0x0002F000, "IEP0 registers"),
                (0x00026030, 0x00026034, "ICSS CFG IEPCLK"))
_IEP_CONSTANT = 26                       # c26 = IEP0
_SCRATCHPAD_IDS = frozenset((SPAD_BANK0, SPAD_BANK1, SPAD_BANK2, IPC_SPAD))
_XFR2VBUS_IDS = frozenset((RD_ID0, RD_ID1, WR_ID0, WR_ID1))


class RunAheadRefused(RuntimeError):
    """run_ahead() would not match lockstep; ``reasons`` says why."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("run-ahead refused: " + "; ".join(self.reasons))


def _iep_window(start: int, end: int) -> str | None:
    """Name of the first IEP window overlapping [start, end), else None."""
    for lo, hi, name in _IEP_WINDOWS:
        if start < hi and lo < end:
            return name
    return None


def _scan_shared_access(pru: PRUCore) -> dict[str, list[str]]:
    """Statically find program accesses to cross-core, shared-time state.

    Returns ``{"iep": [...], "scratchpad": [...], "vbus": [...]}``, each a
    list of ``pc N `source``` locations. Best effort: an address computed at
    run time (other than an LDI/LDI32 constant) is not seen.
    """
    found: dict[str, list[str]] = {"iep": [], "scratchpad": [], "vbus": []}
    known: dict[int, int] = {}      # register -> value its LDIs built
    for instr in pru.instructions:
        op = instr.opcode
        ops = instr.operands
        where = f"pc {instr.address} `{instr.source_text.strip()}`"
        iep_hit = any(isinstance(o, Immediate) and _iep_window(o.value, o.value + 1)
                      for o in ops)
        if op == "LDI" and len(ops) == 2 and isinstance(ops[0], Register) \
                and isinstance(ops[1], Immediate):
            dst = ops[0]
            mask = ((1 << dst.width) - 1) << dst.offset
            value = ((known.get(dst.index, 0) & ~mask)
                     | ((ops[1].value << dst.offset) & mask)) & 0xFFFF_FFFF
            known[dst.index] = value
            iep_hit = iep_hit or _iep_window(value, value + 1) is not None
        elif op in ("LBCO", "SBCO") and len(ops) >= 3:
            const = ops[1]
            index = None
            if isinstance(const, Label):
                m = re.match(r"^c(\d+)$", const.name, re.IGNORECASE)
                index = int(m.group(1)) if m else None
            elif isinstance(const, Immediate):
                index = const.value
            if index == _IEP_CONSTANT:
                iep_hit = True
            elif index is not None and 0 <= index <= 31:
                base = pru.constant_table.resolve(index)
                offset, length = ops[2], ops[3] if len(ops) > 3 else None
                if isinstance(offset, Immediate):
                    size = length.value if isinstance(length, Immediate) else 124
                    start = base + offset.value
                    iep_hit = iep_hit or _iep_window(start, start + max(1, size)) is not None
                else:
                    iep_hit = iep_hit or _iep_window(base, base + 1) is not None
        elif op in ("XIN", "XOUT", "XCHG") and ops:
            device = ops[0]
            if not isinstance(device, Immediate):
                found["scratchpad"].append(f"{where} (XFR device ID is not a constant)")
            elif device.value in _SCRATCHPAD_IDS:
                found["scratchpad"].append(where)
            elif device.value in _XFR2VBUS_IDS:
                found["vbus"].append(where)
        if iep_hit:
            found["iep"].append(where)
    return found


logger = logging.getLogger(__name__)


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


class ConstantTableRegisterRegion(MemoryRegion):
    """CTBIR0, CTBIR1, CTPPR0, CTPPR1 of one core's CTRL block (+0x20..+0x2F).

    A write from firmware (SBBO/SBCO) or the host updates that core's
    constant table at once; a read returns the stored value.
    """

    def __init__(self, name: str, ctrl_base: int, table: ConstantTable):
        super().__init__(name, ctrl_base + CTRL_REG_OFFSET, 4 * CTRL_REG_COUNT, 2, 1, 0)
        self._table = table

    def read(self, addr: int, length: int) -> bytes:
        self._check_bounds(addr, length)
        image = b"".join(v.to_bytes(4, "little") for v in self._table.ctrl)
        local = addr - self.base_addr
        return image[local : local + length]

    def write(self, addr: int, data: bytes) -> None:
        self._check_bounds(addr, length=len(data))
        image = bytearray(b"".join(v.to_bytes(4, "little") for v in self._table.ctrl))
        local = addr - self.base_addr
        image[local : local + len(data)] = data
        for reg in range(CTRL_REG_COUNT):
            self._table.write_ctrl(
                reg, int.from_bytes(image[4 * reg : 4 * reg + 4], "little"))


class Simulator:
    """Orchestrates ICSSG cores sharing memory, XFR, and one IEP timer."""

    def __init__(self, config_path: str = "memory.cfg"):
        self.xfr = XFRBus()
        self.memory = self._load_memory(config_path)
        self.constant_table = self._load_constants(config_path)
        # XFR2VBUS reaches SoC CBASS0/MSMC memory, a single external bus
        # shared by every core's accelerator instances (TRM 6.4.6.3.1) —
        # mirrors how self.memory above is already shared across cores. No
        # region is registered by default; see PRUCore.vbus_memory.
        self.vbus_memory = MemoryBus()
        dev = self._get_device_config(config_path)
        pru_clock = dev.get("pru_clock_mhz", "200")
        pru1_clock = dev.get("pru1_clock_mhz", pru_clock)
        iep_clock = dev.get("iep_clock_mhz", IEP_DEFAULT_CLOCK_MHZ)
        target = dev.get("target", "AM243x").split("#", 1)[0].strip()
        self.capabilities = get_device_capabilities(target)
        core_names = (["pru0", "rtu0", "pru1", "rtu1"] if self.capabilities.rtu
                      else ["pru0", "pru1"])
        self.cores: dict[str, PRUCore] = {
            name: PRUCore(name.upper(), self.memory, self.xfr, IOPort(),
                          copy.deepcopy(self.constant_table),
                          dram_swap=name.endswith("1"),
                          vbus_memory=self.vbus_memory, target=target)
            for name in core_names
        }
        # The ICSSG-only blocks are absent on ICSS-M; silicon reads them as 0
        # (see mem/icssm_absent.py).  ``strict`` makes an access raise instead.
        icssg_only = dev.get("icssg_only_blocks", READ_ZERO).split("#", 1)[0].strip().lower()
        if icssg_only not in _ICSSG_ONLY_MODES:
            raise ValueError(
                f"Unknown icssg_only_blocks {icssg_only!r}; "
                f"expected one of {', '.join(_ICSSG_ONLY_MODES)}")
        if not self.capabilities.rtu:
            for region in icssg_only_regions(target, icssg_only):
                self.memory.add_region(region)

        # SPRUIM2J Table 6-65: these fixed entries depend on the executing
        # core. A shared sidecar cannot provide all four sets of addresses.
        if target.lower() in ("am243x", "am64x"):
            core_constants = {
                "pru0": {10: 0x2A000, 11: 0x22000, 22: 0x8000},
                "rtu0": {10: 0x2A100, 11: 0x23000, 22: 0x8000},
                "pru1": {10: 0x2A200, 11: 0x24000, 22: 0x9000},
                "rtu1": {10: 0x2A300, 11: 0x23800, 22: 0x9000},
            }
        else:
            # C10 is RESERVED in SPRUJB6 Table 7-40, but silicon holds a
            # pointer (ICTE direct read on an LP-AM261, both PRUs). The
            # window behind it, and behind C12-C14, reads zero: see
            # mem/icssm_absent.py.
            # The legacy simulated RTU0 has no AM263x hardware equivalent;
            # leave its common table unchanged rather than inventing a map.
            core_constants = {
                "pru0": {10: 0x2A000, 11: 0x22000, 22: 0x8000},
                "pru1": {10: 0x2A200, 11: 0x24000, 22: 0x9000},
            }
        for name, entries in core_constants.items():
            self.cores[name].constant_table.load_from_dict(entries)

        # CTBIR0/1 and CTPPR0/1 sit at +0x20 of each core's CTRL block and
        # drive C24-C31 of that core. TX_PRU cores are not simulated.
        if target.lower() in ("am243x", "am64x"):
            ctrl_bases = {"pru0": 0x22000, "rtu0": 0x23000,
                          "pru1": 0x24000, "rtu1": 0x23800}
        else:
            ctrl_bases = {"pru0": 0x22000, "pru1": 0x24000}
        self._ctrl_bases = ctrl_bases
        for name, base in ctrl_bases.items():
            self.memory.add_region(ConstantTableRegisterRegion(
                f"{name.upper()}_CTRL_CT", base, self.cores[name].constant_table))

        # One resolver owns generic external devices and cross-core GPIO nets.
        # It is inert until a device or wire is attached.
        self.device_bus = DeviceBus()
        for name, core in self.cores.items():
            core.io_port.bind_device_bus(self.device_bus, name)

        # PRU0/RTU0 use the first configured rate; PRU1/RTU1 use the second.
        pru_clock_mhz = float(pru_clock)
        pru1_clock_mhz = float(pru1_clock)
        self._pru_clock_mhz = pru_clock_mhz
        self._pru1_clock_mhz = pru1_clock_mhz
        core_clocks = {"pru0": pru_clock_mhz, "rtu0": pru_clock_mhz,
                       "pru1": pru1_clock_mhz, "rtu1": pru1_clock_mhz}
        for name, core in self.cores.items():
            core.clock_mhz = core_clocks[name]
            core.io_port.sd_filter = SigmaDeltaFilter(pru_clock_mhz=core_clocks[name])

        # Wire SD registers into memory bus (pru0 owns the register region)
        pru0_sd = self.cores["pru0"].io_port.sd_filter
        pru0_sd.registers = SDRegisters()
        self.memory.add_region(SDRegisterRegion(pru0_sd.registers))

        # RTU0 shares the same register space (same physical hardware)
        rtu0_sd = (self.cores["rtu0"].io_port.sd_filter
                   if self.capabilities.rtu else None)
        if rtu0_sd is not None:
            rtu0_sd.registers = pru0_sd.registers

        # Wire callback to update both cores' channel state on register writes
        def _combined_config_change(ch, field, value):
            pru0_sd._on_config_change(ch, field, value)
            if rtu0_sd is not None:
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
        core_clock_rates = {name: rate for name, rate in core_clock_rates.items()
                            if name in self.cores}
        self.iep = IepTimer(
            clock_mhz=iep_clock,
            ocp_clock_mhz=pru_clock,
            core_clocks_mhz=core_clock_rates,
        )
        self.memory.add_region(IepRegisterRegion(self.iep))
        self.memory.add_region(IepClockRegisterRegion(self.iep))
        self._paced_pending: dict[tuple[str, frozenset[str]], dict[str, int | Fraction]] = {}
        for name, core in self.cores.items():
            core.iep = self.iep

            def observe_cycles(elapsed_cycles: int, *, _name=name, _core=core) -> None:
                self.iep.observe_core_cycles(_name, _core.counters.cycles)

            core.cycle_observer = observe_cycles

            def reset_timeline(*, _name=name) -> None:
                self._paced_pending.clear()
                self.iep.rebase_core(_name)

            core.reset_observer = reset_timeline
            if name in self._perif:
                self._perif[name].cycle_time_ns = (
                    lambda cycles, _name=name: self.iep.core_time_ns(_name, cycles))

        # Loopback: PRU0 TX channel-N -> PRU1 RX channel-N.
        self._loopback = Loopback(self._perif["pru0"], self._perif["pru1"])

        # Producer GPO -> consumer SD pin link (attach_pdm_link); None = off.
        self._pdm_link: PdmLink | None = None

        # Idle skip (NOP delay loops, R31 poll spins) for the run modes: the
        # dashboard's continuous Run / run_multicore and MCP run_until. They
        # read this switch; single-step paths never skip.
        self.idle_skip: bool = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_memory(self, config_path: str) -> MemoryBus:
        """Create and return a MemoryBus populated from *config_path*.

        Falls back to a default set of regions when the file does not exist.
        """
        bus = MemoryBus()

        if not os.path.exists(config_path):
            bus.add_region(MemoryRegion("DRAM0",       0x00000000, 0x2000,  2,  1,  0,
                                         misaligned_write_penalty=1))
            bus.add_region(MemoryRegion("DRAM1",       0x00002000, 0x2000,  2,  1,  0,
                                         misaligned_write_penalty=1))
            bus.add_region(MemoryRegion("ICSS_SHARED", 0x00010000, 0x10000, 2,  1,  0,
                                         misaligned_write_penalty=1))
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
                    misaligned_write_penalty=int(
                        cfg[section].get("misaligned_write_penalty", "0")),
                    per_word_latency=int(cfg[section].get("per_word_latency", "1")),
                    line_bytes=int(cfg[section].get("line_bytes", "0"), 0),
                    line_cross_fit=int(cfg[section].get("line_cross_fit", "0")),
                    line_readers=self._parse_line_readers(
                        section, cfg[section].get("line_readers", "")),
                ))
        return bus

    @staticmethod
    def _parse_line_readers(section: str, text: str) -> tuple[str, ...]:
        names = tuple(text.lower().split())
        known = {"pru0", "rtu0", "pru1", "rtu1"}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(
                f"[{section}] line_readers has unknown core name(s) {', '.join(unknown)}; "
                f"expected any of {', '.join(sorted(known))}")
        return names

    def _load_constants(self, config_path: str) -> ConstantTable:
        """Load the C0-C31 table for the config's [device] target.

        Uses constants_<target>.cfg (e.g. constants_am261x.cfg) and falls back
        to constants_am243x.cfg when the target has no file of its own.  AM243x,
        AM261x and AM263x each have a named file (AM263x's carries the AM261x
        values: same ICSS-M).  A
        root-level config (memory.cfg) finds the file in its config/
        subdirectory; a preset inside config/ finds it alongside.
        """
        table = ConstantTable()
        cfg_dir = os.path.dirname(os.path.abspath(config_path))
        target = self._get_device_config(config_path).get("target", "").strip().lower()
        names = [f"constants_{target}.cfg"] if re.fullmatch(r"[a-z0-9_]+", target) else []
        names.append("constants_am243x.cfg")
        candidates = [os.path.join(d, n) for n in names
                      for d in (os.path.join(cfg_dir, "config"), cfg_dir)]
        constants_path = next((p for p in candidates if os.path.exists(p)), None)
        if constants_path is None:
            return table
        cfg = configparser.ConfigParser()
        cfg.read(constants_path)
        if "constants" in cfg:
            for key, val in cfg["constants"].items():
                m = re.match(r'^c(\d+)$', key)
                if m:
                    n = int(m.group(1))
                    if n >= PROGRAMMABLE_FIRST:
                        # C24-C31 follow CTBIR0/1 and CTPPR0/1, not the config.
                        logger.warning("%s: c%d ignored; C24-C31 follow the "
                                       "CTBIR/CTPPR registers",
                                       constants_path, n)
                        continue
                    table.set(n, int(val, 0))
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
                   guard_ns: float = 20.0) -> dict:
        """Step *lead* and pace *follow* by elapsed core time."""
        return self.step_paced_many(lead, [follow], count=count, guard_ns=guard_ns)

    def step_paced_many(self, lead: str, followers: list[str], count: int = 1,
                        guard_ns: float = 20.0, idle_skip: bool = False) -> dict:
        """Step a lead core and catch each follower up by exact elapsed time.

        When both cores have enabled PERIF blocks, *guard_ns* keeps the follower
        behind the lead's line history. If either PERIF is absent or disabled,
        the follower catches up to the lead's exact core time without a guard.
        PERIF clocks continue to advance while mux-disabled; the mux controls
        signal routing, and the guard applies only while both routes are active.
        Each follower catch-up is limited to 1000 instructions per lead step.
        The result records the selected targets, actual lead_steps and incomplete work;
        subsequent calls finish those targets before executing another lead step.

        With *idle_skip* the lead and the follower catch-up use
        ``PRUCore.step_run``; a lead step may then be a whole idle span. The
        result stays identical to lockstep stepping: a follower's budget is
        the cycles it would start before its target, and the lead skips only
        as far as no follower would run a non-idle instruction in the
        meantime (see ``_lead_skip_budget``).
        """
        if isinstance(guard_ns, bool) or not isinstance(guard_ns, (int, float)):
            raise ValueError("guard_ns must be a number")
        if isinstance(guard_ns, float) and not math.isfinite(guard_ns):
            raise ValueError("guard_ns must be finite")
        if guard_ns < 0:
            raise ValueError("guard_ns must be non-negative")
        lead_pru = self._get_core(lead)
        lead_perif = self._perif.get(lead)
        follower_states = [
            (follower, self._get_core(follower), self._perif.get(follower))
            for follower in followers
        ]
        guard_units = self.iep.nanoseconds_to_units(guard_ns)
        lead_steps = 0

        def catch_up(targets: dict[str, int | Fraction]) -> dict | None:
            for follower, follow_pru, _ in follower_states:
                if follower not in targets:
                    continue
                target = targets[follower]
                steps = 0
                while (
                    self.iep.core_time_units(follower, follow_pru.counters.cycles) < target
                    and not follow_pru.halted
                    and follow_pru.pc < len(follow_pru.instructions)
                ):
                    if steps >= _MAX_CATCHUP_STEPS:
                        return {"complete": False, "reason": "pacing_incomplete",
                                "follower": follower, "targets": dict(targets),
                                "lead_steps": lead_steps}
                    previous_cycles = follow_pru.counters.cycles
                    if idle_skip:
                        follow_pru.step_run(
                            self._cycles_before(follower, follow_pru, target))
                    else:
                        follow_pru.step()
                    steps += 1
                    if (follow_pru.counters.cycles == previous_cycles
                            and not follow_pru.halted
                            and follow_pru.pc < len(follow_pru.instructions)):
                        return {"complete": False, "reason": "pacing_catchup_failed",
                                "follower": follower, "targets": dict(targets),
                                "lead_steps": lead_steps}
            return None

        # Resume the frozen targets before allowing the lead to add more time.
        # Store every follower's target up front so later followers cannot starve.
        # A different execution selection must not run or erase these followers.
        pacing_key = (lead, frozenset(followers))
        targets = self._paced_pending.get(pacing_key, {})
        if targets:
            incomplete = catch_up(targets)
            if incomplete is not None:
                return incomplete
            del self._paced_pending[pacing_key]
        for _ in range(count):
            if not lead_pru.halted and lead_pru.pc < len(lead_pru.instructions):
                if idle_skip:
                    lead_pru.step_run(self._lead_skip_budget(lead, lead_pru, follower_states))
                else:
                    lead_pru.step()
                lead_steps += 1
            lead_time = self.iep.core_time_units(lead, lead_pru.counters.cycles)
            targets = {}
            for follower, _, follow_perif in follower_states:
                use_guard = (
                    lead_perif is not None and follow_perif is not None
                    and lead_perif.enabled and follow_perif.enabled
                )
                targets[follower] = lead_time - (guard_units if use_guard else 0)
            incomplete = catch_up(targets)
            if incomplete is not None:
                self._paced_pending[pacing_key] = targets
                return incomplete
        return {"complete": True, "targets": dict(targets), "lead_steps": lead_steps}

    def _cycles_before(self, name: str, pru: PRUCore, target) -> int:
        """Cycle starts of *pru* before time *target*: what lockstep runs."""
        iep = self.iep
        cycles = pru.counters.cycles
        now = iep.core_time_units(name, cycles)
        unit = iep.core_time_units(name, cycles + 1) - now
        return max(1, int(-((now - target) // unit)))

    def _lead_skip_budget(self, lead: str, lead_pru: PRUCore,
                          follower_states: list) -> int:
        """Cycles the lead may skip without changing the lockstep result.

        Lockstep runs lead cycle j, then catches every follower up to the
        lead time L_j. If the lead skips B 1-cycle instructions instead, the
        followers catch up to L_B in one go. That is identical as long as
        every follower instruction starting before L_B is itself idle (a NOP
        span or SD-valid spin: it reads nothing another core changes and
        changes nothing another core reads). So B is bounded by the earliest
        time any follower leaves its idle span. The span's last instruction
        is excluded, because it may move the follower's pc onto a breakpoint
        that lockstep would report one lead round earlier.
        """
        iep = self.iep
        cycles = lead_pru.counters.cycles
        lead_now = iep.core_time_units(lead, cycles)
        lead_unit = iep.core_time_units(lead, cycles + 1) - lead_now
        bound = math.inf
        for follower, pru, _ in follower_states:
            if pru.halted or pru.pc >= len(pru.instructions):
                continue
            f_cycles = pru.counters.cycles
            f_now = iep.core_time_units(follower, f_cycles)
            f_unit = iep.core_time_units(follower, f_cycles + 1) - f_now
            sd = pru.io_port.sd_filter
            link = sd.pin_link if sd is not None else None
            # The lead's own span writes no pin, so with the lead as producer
            # the follower's queued edges are final for the whole span.
            horizon = (math.inf if link is not None and link.consumer is pru
                       and link.producer is lead_pru else None)
            span = pru.idle_span(IDLE_SKIP_MAX_CYCLES, horizon=horizon)
            idle_until = f_now + (span - 1) * f_unit if span else f_now
            bound = min(bound, idle_until)
            if bound <= lead_now:
                return 1
        if bound == math.inf:
            return IDLE_SKIP_MAX_CYCLES
        return max(1, min(IDLE_SKIP_MAX_CYCLES, int((bound - lead_now) // lead_unit)))

    # ------------------------------------------------------------------
    # Run-ahead scheduling (one-way producer -> consumer)
    # ------------------------------------------------------------------

    def one_way_violations(self, producer: str, consumer: str,
                           producer_reads: list[tuple[int, int]],
                           consumer_writes: list[tuple[int, int]], *,
                           producer_writes: list[tuple[int, int]] = (),
                           consumer_reads: list[tuple[int, int]] = ()) -> list[str]:
        """Reasons why ``run_ahead(producer, consumer)`` could differ from lockstep.

        Empty = safe. Run-ahead lets *producer* run up to a slice ahead of
        *consumer*, so anything either core sees of the other's time must be
        absent: data may only flow producer -> consumer through the PdmLink,
        whose edges are time-stamped. Checks:

        * a GPIO wire with an end on either core;
        * PERIF enabled on either core, or a PERIF loopback channel enabled;
        * a PdmLink between the two cores in the other direction;
        * an XIN/XOUT/XCHG to a scratchpad (IDs 10-12, 15) or with a device ID
          that is not a constant, in either program; XFR2VBUS in both programs
          (they share one VBUS memory);
        * IEP access in either program (``c26``, an address in
          0x2E000..0x2EFFF, or IEPCLK at 0x26030): the IEP count follows the
          furthest core, which run-ahead moves; IEP compare or capture events
          enabled;
        * overlap of a *producer_reads* range with a *consumer_writes* range,
          or of a *producer_writes* range with a *consumer_reads* range
          (PRU0 would see PRU1's writes up to a slice early). Ranges are
          ``(addr, length)`` in the shared map, declared by the caller.

        The program scan is static: an address computed at run time (other
        than an LDI/LDI32 constant) is not seen.
        """
        p = self._get_core(producer)
        c = self._get_core(consumer)
        if p is c:
            raise ValueError("run-ahead producer and consumer must differ")
        reasons: list[str] = []

        for wire in self.list_gpio_wires():
            ends = {wire["core_a"], wire["core_b"]}
            if ends & {producer, consumer}:
                reasons.append(
                    f"GPIO wire {wire['core_a']}.{wire['pin_a']} <-> "
                    f"{wire['core_b']}.{wire['pin_b']} couples the cores' pins")

        for name in (producer, consumer):
            perif = self._perif.get(name)
            if perif is not None and perif.enabled:
                reasons.append(f"PERIF is enabled on {name}")
        for ch, enabled in enumerate(self._loopback.enabled):
            if enabled:
                reasons.append(f"PERIF loopback channel {ch} (PRU0 TX -> PRU1 RX) is enabled")

        link = self._pdm_link
        if link is not None and link.producer is c and link.consumer is p:
            reasons.append(f"PDM link runs {consumer} -> {producer}, against run-ahead")

        scans = {producer: _scan_shared_access(p), consumer: _scan_shared_access(c)}
        for name, scan in scans.items():
            for where in scan["scratchpad"]:
                reasons.append(f"{name} program uses a shared XFR scratchpad: {where}")
            for where in scan["iep"]:
                reasons.append(f"{name} program accesses the IEP: {where}")
        if scans[producer]["vbus"] and scans[consumer]["vbus"]:
            reasons.append(
                f"both programs use XFR2VBUS (shared VBUS memory): "
                f"{producer} {scans[producer]['vbus'][0]}, "
                f"{consumer} {scans[consumer]['vbus'][0]}")

        iep = self.iep
        if iep.cmp_cfg & 0x1FFFF:
            reasons.append(f"IEP compare events are enabled (CMP_CFG=0x{iep.cmp_cfg:X})")
        if iep.cap_cfg & ((1 << NUM_CAPTURE) - 1):
            reasons.append(f"IEP capture events are enabled (CAP_CFG=0x{iep.cap_cfg:X})")

        def overlaps(reads, writes, reader, writer):
            for r_addr, r_len in reads:
                for w_addr, w_len in writes:
                    if r_len > 0 and w_len > 0 and r_addr < w_addr + w_len \
                            and w_addr < r_addr + r_len:
                        reasons.append(
                            f"{reader} reads 0x{r_addr:X}+0x{r_len:X}, which {writer} "
                            f"writes at 0x{w_addr:X}+0x{w_len:X}")

        overlaps(producer_reads, consumer_writes, producer, consumer)
        overlaps(consumer_reads, producer_writes, consumer, producer)
        return reasons

    def run_ahead(self, producer: str, consumer: str, cycles: int, *,
                  producer_reads: list[tuple[int, int]],
                  consumer_writes: list[tuple[int, int]],
                  producer_writes: list[tuple[int, int]] = (),
                  consumer_reads: list[tuple[int, int]] = (),
                  slice_cycles: int = 4096) -> dict:
        """Advance *consumer* by *cycles*, letting *producer* run a slice ahead.

        Identical to lockstep ``step_paced_many(producer, [consumer], ...)``
        at the same consumer cycle count, provided ``one_way_violations`` is
        empty (else raises :class:`RunAheadRefused` and runs nothing). The
        producer then never reads anything the consumer changes, and the
        consumer sees the producer only through the PdmLink's time-stamped
        edges.

        Each round runs the producer (``step_run`` with ``self.idle_skip``,
        else ``step``) until its time is >= the consumer's time plus
        *slice_cycles* producer cycles, or the consumer's end time; then the
        consumer while its instruction start time is <= the producer's time
        (every edge up to there is decided). The producer is left up to a
        slice ahead.

        Breakpoints stop the run as lockstep does (one paced step, then
        check): at a consumer breakpoint the consumer stops with ``pc`` on
        it (the producer may be up to a slice further on than under
        lockstep). At a producer breakpoint the consumer first catches up to
        the producer's time, as a lockstep follower does, and stops on its
        own breakpoint if it reaches one first. The instruction at a
        breakpoint the core already sits on runs (resume).

        Returns ``{"producer_cycles", "consumer_cycles"}`` (cycles advanced
        in this call), ``"skipped"`` (``{core: idle-skipped cycles}`` in this
        call) and ``"breakpoint"`` (the core stopped at one, or None).
        """
        if isinstance(cycles, bool) or not isinstance(cycles, int) or cycles < 0:
            raise ValueError("cycles must be a non-negative integer")
        if isinstance(slice_cycles, bool) or not isinstance(slice_cycles, int) \
                or slice_cycles < 1:
            raise ValueError("slice_cycles must be a positive integer")
        reasons = self.one_way_violations(
            producer, consumer, producer_reads, consumer_writes,
            producer_writes=producer_writes, consumer_reads=consumer_reads)
        if reasons:
            raise RunAheadRefused(reasons)
        p = self._get_core(producer)
        c = self._get_core(consumer)
        time_of = self.iep.core_time_units
        skip = self.idle_skip
        p_start, c_start = p.counters.cycles, c.counters.cycles
        p_skip0, c_skip0 = p.skipped_cycles, c.skipped_cycles
        c_end = c_start + cycles
        end_time = time_of(consumer, c_end)
        hit: str | None = None

        def running(core: PRUCore) -> bool:
            return not core.halted and core.pc < len(core.instructions)

        def advance(core: PRUCore, budget: int) -> None:
            if skip:
                core.step_run(min(budget, IDLE_SKIP_MAX_CYCLES))
            else:
                core.step()

        while c.counters.cycles < c_end and running(c):
            c_cycles = c.counters.cycles
            c_now = time_of(consumer, c_cycles)
            c_unit = time_of(consumer, c_cycles + 1) - c_now
            # ---- Producer: one slice ahead (or to the consumer's end) ----
            if running(p):
                p_cycles = p.counters.cycles
                p_now = time_of(producer, p_cycles)
                p_unit = time_of(producer, p_cycles + 1) - p_now
                goal = min(c_now + slice_cycles * p_unit, end_time)
                goal_cycles = p_cycles + max(0, -((p_now - goal) // p_unit))
                while p.counters.cycles < goal_cycles and running(p):
                    advance(p, goal_cycles - p.counters.cycles)
                    if p.pc in p.breakpoints:
                        hit = producer
                        break
            # ---- Consumer: every start the producer has decided ---------
            limit = c_end
            if running(p):
                p_time = time_of(producer, p.counters.cycles)
                if hit is None:
                    # Starts at or before the producer's time.
                    limit = min(c_end, c_cycles + (p_time - c_now) // c_unit + 1)
                else:
                    # Lockstep catch-up to the lead's time: starts before it.
                    limit = min(c_end, c_cycles + max(0, -((c_now - p_time) // c_unit)))
            while c.counters.cycles < limit and running(c):
                advance(c, limit - c.counters.cycles)
                if c.pc in c.breakpoints:
                    hit = consumer
                    break
            if hit is not None:
                break
            if c.counters.cycles == c_cycles and not running(p):
                break                       # nothing can move any more

        return {
            "producer_cycles": p.counters.cycles - p_start,
            "consumer_cycles": c.counters.cycles - c_start,
            "skipped": {producer: p.skipped_cycles - p_skip0,
                        consumer: c.skipped_cycles - c_skip0},
            "breakpoint": hit,
        }

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
            "gpo_drive_mask": pru.io_port.gpo_drive_mask,
        }

    def set_input(self, core: str, pin: int, value: bool) -> None:
        """Set a single GPI pin on *core*'s I/O port."""
        self._get_core(core).io_port.set_gpi_pin(pin, value)

    def set_gpio_drive_mask(self, core: str, mask: int) -> None:
        """Set the GPIO output-enable bits for one core without changing R30."""
        self._get_core(core).io_port.set_gpo_drive_mask(mask)

    def add_gpio_wire(self, core_a: str, pin_a: int,
                      core_b: str, pin_b: int) -> bool:
        """Connect two GPIO pins through the shared DeviceBus resolver."""
        return self.device_bus.add_gpio_wire(core_a, pin_a, core_b, pin_b)

    def remove_gpio_wire(self, core_a: str, pin_a: int,
                         core_b: str, pin_b: int) -> bool:
        """Remove a direct GPIO connection."""
        return self.device_bus.remove_gpio_wire(core_a, pin_a, core_b, pin_b)

    def list_gpio_wires(self) -> list[dict]:
        """Return configured cross-core GPIO connections."""
        return self.device_bus.list_gpio_wires()

    def attach_device(self, core: str, device: DeviceModel) -> DeviceModel:
        """Attach a generic pin device at one core's GPIO endpoint."""
        return self._get_core(core).io_port.attach_device(device)

    def lease_gpio_outputs(self, core: str, mask: int, owner: object,
                           drive_mask: int = 0) -> None:
        """Lease GPIO directions and restore their initial state on release."""
        self.device_bus.lease_core_outputs(core, mask, owner, drive_mask=drive_mask)

    def detach_device(self, device: DeviceModel) -> None:
        """Detach a generic device and release all pins it previously drove."""
        self.device_bus.detach(device)

    def device_state(self) -> dict:
        """Return generic device, net, drive-mask, and contention state."""
        return self.device_bus.get_state()

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

    def set_sd_source(self, core: str, ch: int, mode: str) -> None:
        """Select SD channel *ch*'s bit source on *core*: "modulator", "pin" or "off"."""
        self._get_core(core).io_port.sd_filter.set_source(ch, mode)

    def attach_pdm_link(self, producer: str = "pru1", consumer: str = "pru0",
                        clock_pin: int = 0, data_pins: dict[int, int] = {0: 1, 1: 1},
                        keep_history: bool = False) -> PdmLink:
        """Drive *consumer*'s SD clock net 0 and data inputs from *producer*'s GPO.

        *clock_pin* is the producer GPO pin carrying the clock (SD0_CLK);
        *data_pins* maps consumer SD channel -> producer GPO data pin. Only
        channels set to "pin" (set_sd_source) sample the link, on the edge
        their CLK_SEL/CLK_INV select. Replaces any link already attached.
        *keep_history* records edges and sampled bits (tests only: unbounded).
        """
        producer_core = self._get_core(producer)
        consumer_core = self._get_core(consumer)
        if producer_core is consumer_core:
            raise ValueError("PDM link producer and consumer must differ")
        self.detach_pdm_link()
        link = PdmLink(producer_core, consumer_core, self.iep.core_time_units,
                       clock_pin=clock_pin, data_pins=dict(data_pins),
                       keep_history=keep_history)
        producer_core.io_port.gpo_listeners.append(link.on_producer_gpo)
        consumer_core.io_port.sd_filter.pin_link = link
        self._pdm_link = link
        return link

    def detach_pdm_link(self) -> None:
        """Remove the PDM link, if any. Pin-mode channels then get no clock."""
        link = self._pdm_link
        if link is None:
            return
        link.producer.io_port.gpo_listeners.remove(link.on_producer_gpo)
        link.consumer.io_port.sd_filter.pin_link = None
        self._pdm_link = None

    def set_iep_clock_mhz(self, mhz: float) -> None:
        """Set the IEP0 clock (ICSSG_IEP_CLK) independently of the PRU clocks.

        Takes effect from the current simulated time; the counter keeps its
        value. The config equivalent is ``iep_clock_mhz`` under [device].
        """
        self.iep.set_clock_mhz(mhz)

    def reset(self, core: str) -> None:
        """Reset *core* to its initial state (registers, counters, PC, halted flag)."""
        self._get_core(core).reset()

    def set_strict_unsupported_xfr(self, enabled: bool) -> None:
        """Choose what happens when firmware drives an unmodelled XFR device ID.

        The default (``False``) keeps a simulator fallback -- XIN reads zeros,
        XOUT is ignored -- and records the event.  That result is unverified on
        hardware except for AM261x BSWAP_4_8 XIN.  ``True`` makes such a
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

    def ctrl_write(self, core: str, reg: int, value: int) -> None:
        """Host write of CTBIR0/1 or CTPPR0/1 (*reg* 0-3) of *core*.

        Goes through the memory bus to the core's CTRL block, the same path a
        PRU store takes, so C24-C31 of that core update at once.
        """
        self._get_core(core)
        base = self._ctrl_bases.get(core)
        if base is None:
            raise KeyError(f"Core '{core}' has no CTRL block with CTBIR/CTPPR")
        self.memory.write(base + CTRL_REG_OFFSET + 4 * reg,
                          (value & 0xFFFFFFFF).to_bytes(4, "little"))

    def constant_table_view(self, core: str) -> list[dict]:
        """List C0-C31 as *core* sees them: [{"c": n, "addr": value}, ...]."""
        table = self._get_core(core).constant_table
        return [{"c": n, "addr": table.resolve(n)} for n in range(32)]

    def hard_reset(self) -> None:
        """Full hardware reset: reset all cores, clear all SPAD banks, and reset XFR config.

        Each core's reset also clears its IO port and Peripheral Interface state
        (TX/RX FIFOs, overrun/underrun, RX valid/overflow, busy, line history),
        so the UI's status bits start clean.  Configuration entered in the UI --
        perif config registers, GPCFG mux, loopback parameters -- is kept.
        """
        self.iep.hardware_reset()
        for core in self.cores.values():
            core.reset()
            core.constant_table.reset_ctrl()
        self.xfr.reset()
        if self._pdm_link is not None:
            self._pdm_link.reset()

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
