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

from core.pru_core import PRUCore
from core.device_capabilities import get_device_capabilities
from mem.icssm_absent import (
    ICSSG_ONLY_BLOCKS, IcssgOnlyBlockRegion, icssg_only_regions,
    MODES as _ICSSG_ONLY_MODES, READ_ZERO)
from mem.memory_bus import MemoryBus
from mem.regions import MemoryRegion
from perif.iep import IepTimer, IEP_SIZE, IEP_DEFAULT_CLOCK_MHZ
from mem.constant_table import (
    ConstantTable, CTRL_REG_COUNT, CTRL_REG_OFFSET, PROGRAMMABLE_FIRST)
from xfr.xfr_bus import XFRBus
from pru_io.device_model import DeviceBus, DeviceModel
from pru_io.io_port import IOPort
from pru_io.sd_filter import SigmaDeltaFilter
from pru_io.sd_registers import SDRegisters
from pru_io.tca9538 import TCA9538Device
from perif.peripheral_interface import PeripheralInterface
from perif.perif_registers import PerifRegisters
from perif.gpcfg import GpcfgRegisters, MUX_PERIF
from perif.loopback import Loopback

_GPCFG_INDEX = {"pru0": 0, "pru1": 1}   # rtu0 has no GPCFG GP-mux (TRM)
# RTU_PRU0/1 and TX_PRU0/1 CTRL bases, absent on ICSS-M.
_ICSSG_ONLY_CTRL_BASES = frozenset(
    base for name, base, _, _ in ICSSG_ONLY_BLOCKS if name.endswith(" Control"))
_MAX_CATCHUP_STEPS = 1000


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


class PruControlRegion(MemoryRegion):
    """CTRL window with a CYCLE register tracking the addressed simulator core.

    CYCLE is at 0x0c (TI PRU Software Support Package, pru_ctrl.h). Per
    SPRUIM2J (ICSSG_PRU_CYCLE) it is writable and does not wrap: a write
    rebases the count (so clear-then-read measures a delta) and a count past
    FFFFFFFFh reads back saturated. The CONTROL ENABLE/COUNTER_ENABLE gating,
    the STALL register clearing and the other CTRL side effects are not
    modelled: the counter follows the simulator core's cycles whenever read.
    The rebase offset lives in a hidden tail of the storage buffer so that
    snapshot/restore of the region carries it.
    """

    _CYCLE = 0x0C
    _OFFSET_BYTES = 8

    def __init__(self, core: PRUCore, backing: MemoryRegion, read_latency: int):
        super().__init__(backing.name, backing.base_addr, backing.size,
                         read_latency, backing.write_latency, backing.jitter,
                         backing.misaligned_write_penalty, backing.per_word_latency)
        self._core = core
        # Keep ordinary CTRL storage in the standard snapshot/restore buffer.
        self._data = backing._data
        self._data.extend(bytes(self._OFFSET_BYTES))

    def _offset(self) -> int:
        return int.from_bytes(self._data[self.size:self.size + self._OFFSET_BYTES],
                              "little", signed=True)

    def clear_cycle_offset(self) -> None:
        """Drop the CYCLE rebase; called when the addressed core's counter resets."""
        self._data[self.size:self.size + self._OFFSET_BYTES] = bytes(self._OFFSET_BYTES)

    def _cycle_value(self) -> int:
        return min(max(self._core.counters.cycles - self._offset(), 0), 0xFFFFFFFF)

    def read(self, addr: int, length: int) -> bytes:
        data = bytearray(super().read(addr, length))
        cycle_addr = self.base_addr + self._CYCLE
        start, end = max(addr, cycle_addr), min(addr + length, cycle_addr + 4)
        if start < end:
            cycle = self._cycle_value().to_bytes(4, "little")
            data[start - addr:end - addr] = cycle[start - cycle_addr:end - cycle_addr]
        return bytes(data)

    def write(self, addr: int, data: bytes) -> None:
        super().write(addr, data)
        cycle_addr = self.base_addr + self._CYCLE
        start, end = max(addr, cycle_addr), min(addr + len(data), cycle_addr + 4)
        if start < end:
            word = bytearray(self._cycle_value().to_bytes(4, "little"))
            word[start - cycle_addr:end - cycle_addr] = data[start - addr:end - addr]
            offset = self._core.counters.cycles - int.from_bytes(word, "little")
            self._data[self.size:self.size + self._OFFSET_BYTES] = offset.to_bytes(
                self._OFFSET_BYTES, "little", signed=True)
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
        self._explicit_read_latency: set[str] = set()
        self._ctrl_regions: dict[str, PruControlRegion] = {}
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

        # CTRL banks, one per core (SPRUIM2J Table 6-63). The root config's
        # legacy ICSS_CFG region at 0x22000 is actually PRU0 CTRL; a declared
        # window's storage is wrapped rather than shadowed by a second region.
        if self.capabilities.rtu:
            ctrl_windows = (("pru0", 0x22000), ("rtu0", 0x23000), ("rtu1", 0x23800),
                            ("pru1", 0x24000), ("tx_pru0", 0x25000), ("tx_pru1", 0x25800))
        else:
            ctrl_windows = (("pru0", 0x22000), ("pru1", 0x24000))
            # ICSS-M has no RTU_PRU or TX_PRU CTRL. A config written for the
            # ICSSG may declare them; the absent-block model must still win.
            for region in [r for r in self.memory.regions
                           if r.base_addr in _ICSSG_ONLY_CTRL_BASES
                           and not isinstance(r, IcssgOnlyBlockRegion)]:
                self.memory.regions.remove(region)
        for name, base in ctrl_windows:
            backing = next((r for r in self.memory.regions if r.base_addr == base), None)
            declared = backing is not None
            if backing is None:
                backing = MemoryRegion(f"{name.upper()}_CTRL", base, 0x30, 2, 1)
            else:
                self.memory.regions.remove(backing)
            # Only the PRU0/PRU1 CYCLE read was measured (#70). Treat each CTRL
            # window uniformly. Precedence: a window the config itself
            # declares keeps its own read_latency; otherwise the device key
            # pru_ctrl_read_latency; otherwise the legacy 2 stalls.
            # A declared window that omits read_latency gets the loader's 0
            # default, which is not an override; an explicit 0 still is.
            explicit = declared and backing.name in self._explicit_read_latency
            latency = backing.read_latency if explicit else int(
                dev.get("pru_ctrl_read_latency", 2 if declared else backing.read_latency))
            if name in self.cores:
                self._ctrl_regions[name] = PruControlRegion(self.cores[name], backing, latency)
                self.memory.add_region(self._ctrl_regions[name])
            else:
                # TX_PRU cores are not simulated: plain storage, CYCLE does not count.
                backing.read_latency = latency
                self.memory.add_region(backing)
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
                if _name in self._ctrl_regions:
                    self._ctrl_regions[_name].clear_cycle_offset()

            core.reset_observer = reset_timeline
            if name in self._perif:
                self._perif[name].cycle_time_ns = (
                    lambda cycles, _name=name: self.iep.core_time_ns(_name, cycles))

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
                if "read_latency" in cfg[section]:
                    self._explicit_read_latency.add(section)
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
                        guard_ns: float = 20.0) -> dict:
        """Step a lead core and catch each follower up by exact elapsed time.

        When both cores have enabled PERIF blocks, *guard_ns* keeps the follower
        behind the lead's line history. If either PERIF is absent or disabled,
        the follower catches up to the lead's exact core time without a guard.
        PERIF clocks continue to advance while mux-disabled; the mux controls
        signal routing, and the guard applies only while both routes are active.
        Each follower catch-up is limited to 1000 instructions per lead step.
        The result records the selected targets, actual lead_steps and incomplete work;
        subsequent calls finish those targets before executing another lead step.
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
