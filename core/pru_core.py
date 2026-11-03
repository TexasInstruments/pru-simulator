"""PRU Core execution engine — ties all modules together.

Dispatches each parsed instruction to the appropriate functional unit:
  - ALU for arithmetic/logic/shift/bit operations
  - BranchUnit for conditional branches
  - MemoryBus for LBBO/SBBO
  - XFRBus for XIN/XOUT/XCHG
  - IOPort for R30/R31 access
  - CycleCounters for timing
"""

from __future__ import annotations

import logging
import math
import re
import struct
from typing import Callable

logger = logging.getLogger(__name__)

from core.device_capabilities import get_device_capabilities
from core.registers import RegisterFile
from core.alu import ALU
from core.branch import BranchUnit, LoopState
from core.counters import CycleCounters
from core.parser import Parser, Instruction
from core.operands import Register, Immediate, BitField, Label, MVIOperand
from mem.memory_bus import MemoryBus
from mem.constant_table import ConstantTable
from xfr.xfr_bus import XFRBus, IPC_SPAD, SPAD_BANK0, SPAD_BANK1, SPAD_BANK2
from pru_io.io_port import IOPort
from xfr.accelerator import Accelerator
from xfr.mac_accelerator import MACAccelerator
from xfr.xfrdma_accelerator import XFRDMAAccelerator, XFRDMABridge, XFRDMA_STATUS_ID, XFRDMA_THREAD_IDS
from xfr.crc_accelerator import CRCAccelerator
from xfr.bswap_accelerator import (
    BSWAP_4_8,
    BSWAP_4_16,
    BSWAP_BYTE_ORDER,
    BSwapAccelerator,
)
from xfr.task_ring_accelerator import attach_task_ring
from xfr.xfr2vbus_accelerator import (
    RD_ID0,
    RD_ID1,
    WR_ID0,
    WR_ID1,
    XFR2VBUSReadAccelerator,
    XFR2VBUSWriteAccelerator,
)


# A memory fault whose base register is an uninitialised R2 is almost always
# one thing, and the raw "no memory region mapped" message names none of it.
#
# R2 is the PRU ABI stack pointer and the stack grows DOWN, so an image whose
# R2 is still zero stores its first register spill just below zero - which
# wraps to the top of the address space and faults there. Images linked with
# `-e main` never run `_c_int00`, which is what sets R2 up on hardware, and
# that linker pattern is the one nearly every headless PRU example uses.
#
# The behaviour is correct and is deliberately unchanged. Only the diagnosis
# is added, because without it the fault reads as a wild pointer bug in the
# firmware rather than as a missing C runtime.
_UNINIT_SP_HINT = (
    "the base register is R2, the PRU ABI stack pointer, and it is either zero "
    "or has wrapped below zero - the signature of a stack that was never set "
    "up. R2 is initialised by the C runtime entry `_c_int00`, so this happens "
    "whenever execution starts somewhere else. Loaders that begin at address 0 "
    "(PRUICSS_loadFirmware, and this simulator) will do that unless the entry "
    "is placed there: `.text:_c_int00* > 0x0, PAGE 0` in the linker command "
    "file puts the runtime at 0 so it runs first. Overriding the entry point "
    "with `-e main` has the same effect and is the usual cause."
)

# A stack that grows down from an uninitialised R2 of 0 lands just below zero,
# which wraps to the very top of the 32-bit space. That is what the fault
# address looks like in practice: a frame setup emits `SUB r2, r2, <frame>`
# before the spill, so by the time the store executes R2 is 0xFFFFFFxx rather
# than 0 - checking only for zero would miss every real occurrence.
_SP_WRAP_FLOOR = 0xFFFF0000


def _perif_quiet(perif) -> bool:
    """True when advancing a (mux-disabled) PERIF only moves its clock.

    Its channel state machines then do nothing whose result depends on how
    finely time is advanced, so one jump to the end of an idle span equals
    stepping through it. An armed RX is allowed once its sample clock has
    started (it then samples at fixed times) and while no loopback drives
    its line (it then samples a constant 0). SD firmware arms one that way:
    the PERIF decodes R30[25] (SD_EN) as an RX enable even when muxed off.
    """
    for ch in perif.channels:
        if ch.fsm != "IDLE" or ch._rx_auto_arm_deadline_ns is not None:
            return False
        if ch.rx_en and (ch._rx_next_edge_ns is None
                         or ch.rx_line_source is not None):
            return False
    return True


def _stack_pointer_hint(core, base_op, addr) -> str:
    """Return a trailing hint if this fault is the uninitialised-R2 trap."""
    if not isinstance(base_op, Register) or base_op.index != 2:
        return ""
    sp = core.registers.regs[2]
    if sp != 0 and not (sp >= _SP_WRAP_FLOOR or addr >= _SP_WRAP_FLOOR):
        return ""
    return "  HINT: " + _UNINIT_SP_HINT


class UnsupportedXFRError(RuntimeError):
    """A broadside transfer named an XFR device ID this simulator does not model.

    Raised only when :attr:`PRUCore.strict_unsupported_xfr` is set.  It is a
    diagnostic for callers that would rather a run fail than quietly read
    zeros; this switch is a simulator diagnostic, not a hardware trap model.
    """


class PRUCore:
    """Simulates a single PRU core executing PRU assembly instructions."""

    def __init__(self, name: str, memory: MemoryBus, xfr: XFRBus, io_port: IOPort,
                 constant_table: ConstantTable | None = None,
                 dram_swap: bool = False,
                 vbus_memory: MemoryBus | None = None,
                 cycle_observer: Callable[[int], None] | None = None,
                 reset_observer: Callable[[], None] | None = None,
                 target: str = "am243x"):
        self.name = name
        self.target = target.split("#", 1)[0].strip().lower()
        self.capabilities = get_device_capabilities(target)
        self.registers = RegisterFile()
        self.counters = CycleCounters()
        # Cycle at which the executing instruction started. Output pin
        # changes are stamped from it (PdmLink: retire = start + 1), so the
        # stamp never depends on where an opcode counts its stalls. -1 while
        # no instruction runs after a reset: the reset's own pin change then
        # lands at cycle 0, the reset instant.
        self.step_start_cycle: int = -1
        self.iep = None          # set by Simulator when an IEP is present
        self.cycle_observer = cycle_observer
        self.reset_observer = reset_observer
        self.clock_mhz: float = 200.0   # set by Simulator from the config
        self.memory = memory
        self.xfr = xfr
        self.io_port = io_port
        self.constant_table: ConstantTable = constant_table if constant_table is not None else ConstantTable()
        # PRU1/RTU1 see DRAM1 at local 0x0000 and DRAM0 at 0x2000. See
        # _map_data_addr.
        self.dram_swap = dram_swap
        # XFR2VBUS (TRM 6.4.6.3.1) reaches SoC CBASS0/MSMC memory, a distinct
        # 48-bit address space from the ICSSG-local `memory` bus above. Public
        # so tests back it with a real MemoryBus/MemoryRegion instead of
        # reaching into accelerator internals; defaults to an empty bus (any
        # access without a configured region fails loud, same as `memory`).
        self.vbus_memory: MemoryBus = vbus_memory if vbus_memory is not None else MemoryBus()
        self.pc: int = 0
        self.halted: bool = False
        self.fault: dict | None = None
        self.instructions: list[Instruction] = []
        self.loop_state: LoopState | None = None
        self.breakpoints: set[int] = set()
        # Cycles that step_run() advanced in bulk instead of stepping (idle
        # skip; run modes only). Cumulative, cleared by reset(). For the UI.
        self.skipped_cycles: int = 0
        # Per-program cache for step_run(): (start, end) -> loop body length
        # if every instruction in [start, end) is a NOP, else 0. Keyed to the
        # instruction list it was built for, so a reload drops it.
        self._nop_body_cache: dict[tuple[int, int], int] = {}
        self._nop_body_cache_for: list | None = None
        # pc -> "is an R31[28] self-spin" (static per program; see _spin_at).
        self._spin_cache: dict[int, bool] = {}
        self._spin_cache_for: list | None = None
        # Unsupported-XFR diagnostics. Reads from an unconnected broadside
        # device ID use a zero/no-op compatibility fallback, with a warning
        # and record. Strict mode rejects it before changing transfer state.
        self.strict_unsupported_xfr: bool = False
        self.unsupported_xfr: dict[int, dict] = {}

        self._parser = Parser()
        self._branch = BranchUnit()
        xfrdma_bridge = XFRDMABridge()
        factories = {
            MACAccelerator.DEVICE_ID: lambda: MACAccelerator(self.registers),
            CRCAccelerator.DEVICE_ID: lambda: CRCAccelerator(self.registers),
            **{device_id: (lambda xid=device_id: XFRDMAAccelerator(xfrdma_bridge, xid))
               for device_id in (XFRDMA_STATUS_ID, *XFRDMA_THREAD_IDS)},
            **{device_id: (lambda xid=device_id: BSwapAccelerator(self.registers, xid))
               for device_id in (BSWAP_BYTE_ORDER, BSWAP_4_8, BSWAP_4_16)},
            **{device_id: (lambda xid=device_id: XFR2VBUSReadAccelerator(self.vbus_memory, xid))
               for device_id in (RD_ID0, RD_ID1)},
            **{device_id: (lambda xid=device_id: XFR2VBUSWriteAccelerator(self.vbus_memory, xid))
               for device_id in (WR_ID0, WR_ID1)},
        }
        self.accelerators: dict[int, Accelerator] = {
            xid: factory() for xid, factory in factories.items()
            if xid in self.capabilities.broadside_ids
        }
        # XFR2TR ring accelerator, broadside IDs 0x70/0x71/0x72.  TRM SPRUIM2J
        # Table 6-60 places it on RTU_PRU1/0 only, so `attach_task_ring`
        # registers nothing on the other cores and their XFR accesses to those
        # IDs fall through to the unsupported-XFR handling (recorded diagnostic,
        # or an error in strict mode).
        self.task_ring = (attach_task_ring(self)
                          if 0x70 in self.capabilities.broadside_ids else None)
        # Accelerators whose state changes with time (XFR2VBUS read latency,
        # WR_BUSY) expose advance(cycles); step() feeds them core cycles.
        self._timed_accelerators = tuple(
            acc for acc in self.accelerators.values() if hasattr(acc, "advance")
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_asm(self, source: str, include_paths: list[str] | None = None) -> list[str]:
        """Parse assembly source and load instructions.

        Returns a list of error strings (empty on success).

        Loading installs instructions only: halted, pc, loop state and
        registers are left as they were, so reset() the core before running a
        newly loaded program. A successful load drops the recorded fault; a
        failed parse leaves the previous program and its fault in place.
        """
        errors: list[str] = []
        try:
            self.instructions = self._parser.parse_text(source, include_paths)
            self.fault = None  # a fault belongs to the program that raised it
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        return errors

    def load_binary(self, text_words: list, data_bytes: bytes = b'',
                    data_addr: int = 0, symbols: dict = None) -> list:
        """Load pre-compiled binary instructions (from .out ELF).

        Returns a list of error strings (empty on success).

        Loading installs instructions only: halted, pc, loop state and
        registers are left as they were, so reset() the core before running a
        newly loaded program. A successful load drops the recorded fault; a
        failed load leaves the previous program and its fault in place.
        """
        from .disassembler import disassemble
        errors: list[str] = []
        try:
            syms = symbols or {}
            instructions = disassemble(text_words, syms)
            if data_bytes:
                self.memory.write(data_addr, data_bytes)
            self.instructions = instructions
            # Store symbols as labels (name → addr) for the frontend
            self._parser.labels = {name: addr for addr, name in syms.items()}
            self.fault = None
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        return errors

    def reset(self) -> None:
        """Reset all state to initial conditions."""
        self.registers.regs[:] = [0] * 32
        self.registers.carry = False
        self.counters.reset()
        if self.reset_observer is not None:
            self.reset_observer()
        self.pc = 0
        self.halted = False
        self.fault = None
        self.loop_state = None
        for acc in self.accelerators.values():
            acc.reset()
        self.step_start_cycle = -1
        self.skipped_cycles = 0
        self.io_port.reset()
        self.unsupported_xfr.clear()

    def _record_fault(self, opcode: str, address: int, error: Exception) -> None:
        self.fault = {
            "type": "memory",
            "opcode": opcode,
            "address": int(address) & 0xFFFF_FFFF,
            "pc": self.pc,
            "error": str(error),
        }

    def step(self) -> None:
        """Execute one instruction."""
        if self.halted or self.pc >= len(self.instructions):
            # The XFR2TR copy engine runs independently of the core: queued
            # TRs still land after HALT / end of program.  Each such step is
            # one cycle of simulated time, so the core's cycle count, the
            # IEP (via cycle_observer) and the other time-driven devices move
            # with the copy instead of freezing at the HALT.  No instruction
            # retires, so instruction_count stays put.
            if self.task_ring is not None and self.task_ring.busy:
                self._advance_idle_cycle()
            return
        cycles_before = self.counters.cycles
        self.step_start_cycle = cycles_before

        # Pre-tick: advance UART frame generator before instruction reads R31
        if self.io_port.uart_generator is not None:
            self.io_port.uart_generator.tick(self.counters.cycles)

        # Pre-tick: apply producer pin edges to pin-driven SD channels, so
        # this instruction sees every edge stamped up to its start time.
        sd = self.io_port.sd_filter
        if sd is not None and sd.pin_link is not None and sd.pin_link.consumer is self:
            sd.pin_link.advance_consumer(self.counters.cycles)

        # The MAC samples R28/R29 one instruction late (silicon, upstream #58).
        self.accelerators[MACAccelerator.DEVICE_ID].begin_step()

        instr = self.instructions[self.pc]
        branch_taken = False
        xfr_held = False

        # Dispatch
        op = instr.opcode

        # ---- Data movement -----------------------------------------------
        if op == "LDI":
            dst = instr.operands[0]
            val = self._read_operand(instr.operands[1])
            self._write_operand(dst, val)

        elif op == "MOV":
            dst = instr.operands[0]
            val = self._read_operand(instr.operands[1])
            self._write_operand(dst, val)

        elif op in ("MVIB", "MVIW", "MVID"):
            size = {"MVIB": 1, "MVIW": 2, "MVID": 4}[op]
            dst_op, src_op = instr.operands

            # --- Read source ---
            if src_op.indirect:
                if src_op.predec:
                    self._mvi_update_ptr(src_op, -size)
                ptr = self._mvi_read_ptr(src_op)
                data = self._mvi_read_regfile(ptr, size)
                if src_op.postinc:
                    self._mvi_update_ptr(src_op, size)
            else:
                data = self._mvi_read_direct(src_op, size)

            # --- Write destination ---
            if dst_op.indirect:
                if dst_op.predec:
                    self._mvi_update_ptr(dst_op, -size)
                ptr = self._mvi_read_ptr(dst_op)
                self._mvi_write_regfile(ptr, data)
                if dst_op.postinc:
                    self._mvi_update_ptr(dst_op, size)
            else:
                self._mvi_write_direct(dst_op, data)

        # ---- Arithmetic --------------------------------------------------
        elif op == "ADD":
            dst, s1, s2 = instr.operands
            width = dst.width if isinstance(dst, Register) else 32
            result, carry = ALU.add(self._read_operand(s1), self._read_operand(s2), width)
            self.registers.carry = carry
            self._write_operand(dst, result)

        elif op == "ADC":
            dst, s1, s2 = instr.operands
            width = dst.width if isinstance(dst, Register) else 32
            result, carry = ALU.adc(self._read_operand(s1), self._read_operand(s2),
                                    self.registers.carry, width)
            self.registers.carry = carry
            self._write_operand(dst, result)

        elif op == "SUB":
            dst, s1, s2 = instr.operands
            width = dst.width if isinstance(dst, Register) else 32
            result, carry = ALU.sub(self._read_operand(s1), self._read_operand(s2), width)
            self.registers.carry = carry
            self._write_operand(dst, result)

        elif op == "SUC":
            dst, s1, s2 = instr.operands
            width = dst.width if isinstance(dst, Register) else 32
            result, carry = ALU.suc(self._read_operand(s1), self._read_operand(s2),
                                    self.registers.carry, width)
            self.registers.carry = carry
            self._write_operand(dst, result)

        elif op == "RSB":
            dst, s1, s2 = instr.operands
            width = dst.width if isinstance(dst, Register) else 32
            result, carry = ALU.rsb(self._read_operand(s1), self._read_operand(s2), width)
            self.registers.carry = carry
            self._write_operand(dst, result)

        elif op == "RSC":
            dst, s1, s2 = instr.operands
            width = dst.width if isinstance(dst, Register) else 32
            result, carry = ALU.rsc(self._read_operand(s1), self._read_operand(s2),
                                    self.registers.carry, width)
            self.registers.carry = carry
            self._write_operand(dst, result)

        # ---- Logic -------------------------------------------------------
        elif op == "AND":
            dst, s1, s2 = instr.operands
            result = ALU.and_(self._read_operand(s1), self._read_operand(s2))
            self._write_operand(dst, result)

        elif op == "OR":
            dst, s1, s2 = instr.operands
            result = ALU.or_(self._read_operand(s1), self._read_operand(s2))
            self._write_operand(dst, result)

        elif op == "XOR":
            dst, s1, s2 = instr.operands
            result = ALU.xor_(self._read_operand(s1), self._read_operand(s2))
            self._write_operand(dst, result)

        elif op == "NOT":
            dst, src = instr.operands
            result = ALU.not_(self._read_operand(src))
            self._write_operand(dst, result)

        # ---- Shift -------------------------------------------------------
        elif op == "LSL":
            dst, src, amount = instr.operands
            result = ALU.lsl(self._read_operand(src), self._read_operand(amount))
            self._write_operand(dst, result)

        elif op == "LSR":
            dst, src, amount = instr.operands
            result = ALU.lsr(self._read_operand(src), self._read_operand(amount))
            self._write_operand(dst, result)

        # ---- Bit ---------------------------------------------------------
        elif op == "SET":
            dst, src, bit = instr.operands
            result = ALU.set_bit(self._read_operand(src), self._read_operand(bit))
            self._write_operand(dst, result)

        elif op == "CLR":
            dst, src, bit = instr.operands
            result = ALU.clr_bit(self._read_operand(src), self._read_operand(bit))
            self._write_operand(dst, result)

        elif op == "LMBD":
            dst, src, target = instr.operands
            result = ALU.lmbd(self._read_operand(src), self._read_operand(target))
            self._write_operand(dst, result)

        # ---- Compare -----------------------------------------------------
        elif op == "MIN":
            dst, s1, s2 = instr.operands
            result = ALU.min_(self._read_operand(s1), self._read_operand(s2))
            self._write_operand(dst, result)

        elif op == "MAX":
            dst, s1, s2 = instr.operands
            result = ALU.max_(self._read_operand(s1), self._read_operand(s2))
            self._write_operand(dst, result)

        # ---- Branch -------------------------------------------------------
        elif op == "QBA":
            label = instr.operands[0]
            self.pc = self._read_operand(label)
            branch_taken = True

        elif op == "JMP":
            target = instr.operands[0]
            self.pc = self._read_operand(target)
            branch_taken = True

        elif op == "JAL":
            reg, target = instr.operands
            self._write_operand(reg, self.pc + 1)
            self.pc = self._read_operand(target)
            branch_taken = True

        elif op in ("QBEQ", "QBNE", "QBGT", "QBGE", "QBLT", "QBLE"):
            label, reg, operand = instr.operands
            reg_val = self._read_operand(reg)
            op_val = self._read_operand(operand)
            method = getattr(self._branch, op.lower())
            if method(reg_val, op_val):
                self.pc = self._read_operand(label)
                branch_taken = True

        elif op == "QBBS":
            label, reg, bit = instr.operands
            reg_val = self._read_operand(reg)
            bit_val = self._read_operand(bit)
            if self._branch.qbbs(reg_val, bit_val):
                self.pc = self._read_operand(label)
                branch_taken = True

        elif op == "QBBC":
            label, reg, bit = instr.operands
            reg_val = self._read_operand(reg)
            bit_val = self._read_operand(bit)
            if self._branch.qbbc(reg_val, bit_val):
                self.pc = self._read_operand(label)
                branch_taken = True

        # ---- Loop --------------------------------------------------------
        elif op == "LOOP":
            label, count_op = instr.operands
            count = self._read_operand(count_op)
            end_addr = self._read_operand(label)
            self.loop_state = LoopState(
                count=count,
                start_address=self.pc + 1,
                end_address=end_addr,
            )

        # ---- Memory ------------------------------------------------------
        elif op == "LBBO":
            # LBBO &reg, base, offset, length
            reg_op, base_op, offset_op, length_op = instr.operands
            base = self._read_operand(base_op)
            offset = self._read_operand(offset_op)
            length = self._read_operand(length_op)
            addr = self._map_data_addr(base + offset)
            try:
                data, stalls = self.memory.read(addr, length, requester=self.name)
                start_reg = reg_op.index if isinstance(reg_op, Register) else 0
                start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
                self._write_registers_from_bytes(start_reg, data, start_byte)
                self.counters.stall(stalls)
            except ValueError as e:
                logger.error(f"LBBO fault at 0x{addr:08X}: {e}"
                             f"{_stack_pointer_hint(self, base_op, addr)}")
                self._record_fault("LBBO", addr, e)
                self.halted = True

        elif op == "LBCO":
            # LBCO &reg, CN, offset, length
            reg_op, cn_op, offset_op, length_op = instr.operands
            base = self._resolve_cn(cn_op)
            offset = self._read_operand(offset_op)
            length = self._read_operand(length_op)
            addr = self._map_data_addr(base + offset)
            try:
                data, stalls = self.memory.read(addr, length, requester=self.name)
                start_reg = reg_op.index if isinstance(reg_op, Register) else 0
                start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
                self._write_registers_from_bytes(start_reg, data, start_byte)
                self.counters.stall(stalls)
            except ValueError as e:
                logger.error(f"LBCO fault at 0x{addr:08X}: {e}")
                self._record_fault("LBCO", addr, e)
                self.halted = True

        elif op == "SBCO":
            # SBCO &reg, CN, offset, length
            reg_op, cn_op, offset_op, length_op = instr.operands
            base = self._resolve_cn(cn_op)
            offset = self._read_operand(offset_op)
            length = self._read_operand(length_op)
            addr = self._map_data_addr(base + offset)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            data = self._read_registers_to_bytes(start_reg, length, start_byte)
            try:
                stalls = self.memory.write(addr, data)
                self.counters.stall(stalls)
            except ValueError as e:
                logger.error(f"SBCO fault at 0x{addr:08X}: {e}")
                self._record_fault("SBCO", addr, e)
                self.halted = True

        elif op == "SBBO":
            # SBBO &reg, base, offset, length
            reg_op, base_op, offset_op, length_op = instr.operands
            base = self._read_operand(base_op)
            offset = self._read_operand(offset_op)
            length = self._read_operand(length_op)
            addr = self._map_data_addr(base + offset)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            data = self._read_registers_to_bytes(start_reg, length, start_byte)
            try:
                stalls = self.memory.write(addr, data)
                self.counters.stall(stalls)
            except ValueError as e:
                logger.error(f"SBBO fault at 0x{addr:08X}: {e}"
                             f"{_stack_pointer_hint(self, base_op, addr)}")
                self._record_fault("SBBO", addr, e)
                self.halted = True

        # ---- XFR ---------------------------------------------------------
        elif op == "XIN":
            # XIN device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            if device_id in self.accelerators:
                accelerator = self.accelerators[device_id]
                data = accelerator.xin(start_reg, length, start_byte)
                if getattr(accelerator, "hold_pc", False):
                    # A stalled broadside read holds the pipeline: the
                    # destination registers keep their previous contents until
                    # the transfer actually completes.  Writing the placeholder
                    # here would make the stall observable as register damage.
                    branch_taken = True
                    xfr_held = True
                    self.counters.stall_cycles += 1
                else:
                    self._write_registers_from_bytes(start_reg, data, start_byte)
            elif device_id not in self.capabilities.broadside_ids:
                self._note_unsupported_xfr("XIN", device_id, start_reg, start_byte, length)
                self._write_registers_from_bytes(start_reg, bytes(length), start_byte)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xin_shifted(device_id, start_reg, length)
            else:
                if not self.xfr.supports(device_id):
                    self._note_unsupported_xfr("XIN", device_id, start_reg, start_byte, length)
                xfr_offset = (start_reg - 2) * 4 + start_byte if device_id == IPC_SPAD else start_reg * 4 + start_byte
                data = self.xfr.xin(device_id, xfr_offset, length)
                self._write_registers_from_bytes(start_reg, data, start_byte)

        elif op == "XOUT":
            # XOUT device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            if device_id in self.accelerators:
                data = self._read_registers_to_bytes(start_reg, length, start_byte)
                accelerator = self.accelerators[device_id]
                accelerator.xout(start_reg, data, start_byte)
                if getattr(accelerator, "hold_pc", False):
                    branch_taken = True
                    xfr_held = True
                    self.counters.stall_cycles += 1
            elif device_id not in self.capabilities.broadside_ids:
                self._note_unsupported_xfr("XOUT", device_id, start_reg, start_byte, length)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xout_shifted(device_id, start_reg, length)
            else:
                if not self.xfr.supports(device_id):
                    self._note_unsupported_xfr("XOUT", device_id, start_reg, start_byte, length)
                xfr_offset = (start_reg - 2) * 4 + start_byte if device_id == IPC_SPAD else start_reg * 4 + start_byte
                data = self._read_registers_to_bytes(start_reg, length, start_byte)
                self.xfr.xout(device_id, xfr_offset, data)

        elif op == "XCHG":
            # XCHG device_id, &reg, length
            device_id_op, reg_op, length_op = instr.operands
            device_id = self._read_operand(device_id_op)
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            if device_id in self.accelerators:
                data = self._read_registers_to_bytes(start_reg, length, start_byte)
                accelerator = self.accelerators[device_id]
                old_data = accelerator.xchg(start_reg, data, start_byte)
                if getattr(accelerator, "hold_pc", False):
                    # Held XCHG: neither side moved, so leave the registers alone.
                    branch_taken = True
                    xfr_held = True
                    self.counters.stall_cycles += 1
                else:
                    self._write_registers_from_bytes(start_reg, old_data, start_byte)
            elif device_id not in self.capabilities.broadside_ids:
                self._note_unsupported_xfr("XCHG", device_id, start_reg, start_byte, length)
                self._write_registers_from_bytes(start_reg, bytes(length), start_byte)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xchg_shifted(device_id, start_reg, length)
            else:
                if not self.xfr.supports(device_id):
                    self._note_unsupported_xfr("XCHG", device_id, start_reg, start_byte, length)
                xfr_offset = (start_reg - 2) * 4 + start_byte if device_id == IPC_SPAD else start_reg * 4 + start_byte
                data = self._read_registers_to_bytes(start_reg, length, start_byte)
                old_data = self.xfr.xchg(device_id, xfr_offset, data)
                self._write_registers_from_bytes(start_reg, old_data, start_byte)

        # ---- Control -----------------------------------------------------
        elif op == "HALT":
            self.halted = True

        elif op == "SLP":
            self.halted = True

        elif op == "ZERO":
            # ZERO &reg, length  (length in bytes)
            reg_op, length_op = instr.operands
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            self._write_registers_from_bytes(start_reg, bytes(length), start_byte)

        elif op == "FILL":
            # FILL &reg, length  (length in bytes)
            reg_op, length_op = instr.operands
            length = self._read_operand(length_op)
            start_reg = reg_op.index if isinstance(reg_op, Register) else 0
            start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
            fill_data = bytes([0xFF] * length)
            self._write_registers_from_bytes(start_reg, fill_data, start_byte)

        elif op in ("WBS", "WBC"):
            # WBS/WBC stall the core until the selected bit is set/clear.
            # Treating them as no-ops lets peripheral-driven firmware fall
            # straight through its status polls: it never yields the simulated
            # time the peripheral needs to produce data, so every transaction
            # times out and retries forever. Re-executing the instruction (by
            # suppressing the PC advance) is what makes the wait observable,
            # because PRUCore.step() advances the peripheral each step.
            # Two operand forms reach here: the disassembler emits
            # (source, bit) — WBS/WBC are QBBS/QBBC with a zero branch offset,
            # i.e. branch-to-self — while the assembler's mnemonic takes the
            # bit alone and implies R31.
            ops = instr.operands
            if len(ops) >= 2:
                value, bit_op = self._read_operand(ops[0]), ops[1]
            else:
                value, bit_op = self.io_port.read_r31(), ops[0]
            bit = self._read_operand(bit_op) & 0x1F
            is_set = (value >> bit) & 1
            waiting = (op == "WBS" and not is_set) or (op == "WBC" and is_set)
            if waiting:
                branch_taken = True          # hold PC: re-execute the wait
                self.counters.stall_cycles += 1

        elif op == "NOP":
            pass

        # Unknown opcodes are silently ignored (or could raise)
        # else: pass

        # ---- Advance PC (unless branch taken or halted) ------------------
        if not branch_taken and not self.halted:
            self.pc += 1

        # ---- Hardware loop check -----------------------------------------
        if self.loop_state is not None and not self.loop_state.is_done():
            if self.pc == self.loop_state.end_address:
                if self.loop_state.tick():
                    # Loop back to start
                    self.pc = self.loop_state.start_address
                else:
                    # Loop finished
                    self.loop_state = None

        # ---- Advance SD filter clock (if attached) ----------------------
        if self.io_port.sd_filter is not None:
            self.io_port.sd_filter.tick()

        # ---- Advance Peripheral Interface timeline (if attached) --------
        if self.io_port.perif is not None:
            self.io_port.perif.advance_cycles(self.counters.cycles)

        # ---- Advance timed XFR accelerators -----------------------------
        # Every core cycle this instruction took, memory stalls included (+1
        # for the instruction cycle counted just below).
        elapsed = self.counters.cycles - cycles_before + 1
        for acc in self._timed_accelerators:
            acc.advance(elapsed)

        # ---- Count instruction cycle ------------------------------------
        if xfr_held:
            # A held XFR consumes a cycle (stall_cycles was bumped above) but
            # retires nothing, so it must not count as an instruction.
            self.counters.cycles += 1
        else:
            self.counters.tick()
        # Time-driven devices advance by the cycles this step consumed, stalls
        # included, so a core built without a Simulator still ticks them. With
        # no device attached only the cycle count moves; it stays exact so a
        # device attached later starts at the right cycle.
        elapsed_cycles = self.counters.cycles - cycles_before
        io_port = self.io_port
        if io_port.device_bus.devices:
            io_port.advance_devices(elapsed_cycles)
        else:
            io_port._device_cycle += elapsed_cycles
        # XFR2TR runs independently for every elapsed core cycle, including
        # stalls.  Keep it separate from the shared device bus because it is
        # attached only to RTU_PRU0/1.
        if self.task_ring is not None:
            self.task_ring.tick(elapsed_cycles)
        if self.cycle_observer is not None:
            self.cycle_observer(elapsed_cycles)

    def _advance_idle_cycle(self) -> None:
        """Advance simulated time by one cycle with no instruction retiring.

        The same clocks a retired instruction moves at the end of ``step()``:
        timed XFR accelerators, the Peripheral Interface timeline, the device
        bus, the XFR2TR ring and the cycle observer (the IEP under
        ``Simulator``).  Used while a halted core waits for its ring to drain.
        """
        for acc in self._timed_accelerators:
            acc.advance(1)
        self.counters.cycles += 1
        io_port = self.io_port
        if io_port.perif is not None:
            io_port.perif.advance_cycles(self.counters.cycles)
        if io_port.device_bus.devices:
            io_port.advance_devices(1)
        else:
            io_port._device_cycle += 1
        self.task_ring.tick(1)
        if self.cycle_observer is not None:
            self.cycle_observer(1)

    # ------------------------------------------------------------------
    # Idle skip (run modes only)
    # ------------------------------------------------------------------
    #
    # Two idle patterns are executed in bulk instead of one step() per
    # instruction. Both consist of 1-cycle instructions that change no
    # register, memory or pin, so the only effect of n of them is time:
    #
    # * Delay-loop collapse: a LOOP whose whole body is NOPs.
    # * Poll fast-forward: a QBBC/QBBS-to-self spin on R31[28] (SD valid)
    #   of a pin-driven SD channel; the next valid rise is known from the
    #   PdmLink queue (PdmLink.next_valid_time).
    #
    # Whatever the patterns do not cover falls back to step(). Callers that
    # must keep "one call = one instruction" (single-step, Simulator.step,
    # the MCP step tool) simply never call step_run().

    def step_run(self, max_cycles: int) -> int:
        """Execute one ``step()`` or one skipped idle span of <= *max_cycles*.

        Returns the instructions retired. With ``max_cycles < 2`` or no
        applicable pattern this is exactly ``step()``. A skipped span leaves
        the core, its peripherals and the PdmLink exactly where stepping the
        same instructions would.
        """
        before = self.counters.instruction_count
        # The cheap pattern test first, so busy code pays only for it: the
        # cached "not a spin" answer of idle_candidate(), inlined (hot path).
        plan = None
        if max_cycles >= 2:
            loop = self.loop_state
            if ((loop is not None and loop.start_address == self.pc)
                    or self._spin_cache_for is not self.instructions
                    or self._spin_cache.get(self.pc) is not False):
                if self.idle_candidate():
                    plan = self._idle_plan_at_candidate(max_cycles)
        if plan is None:
            self.step()
        else:
            self._run_idle_plan(plan)
        return self.counters.instruction_count - before

    def idle_span(self, max_cycles: int, horizon: int | float | None = None) -> int:
        """Cycles ``step_run(max_cycles)`` would skip now; 0 if it would step.

        Executes nothing. *horizon* overrides the PdmLink horizon for the poll
        pattern (Simulator pacing passes ``math.inf`` when the producer is the
        lead core, whose span is decided before this core runs).
        """
        if max_cycles < 2:
            return 0
        plan = self._idle_plan(max_cycles, horizon)
        return 0 if plan is None else plan[1]

    def idle_candidate(self) -> bool:
        """Cheap pre-check: could ``step_run`` skip at the current pc?

        False guarantees ``step_run(n)`` is exactly ``step()`` for every n;
        True means the full check (``_idle_plan``) has to decide. Looks only
        at the pc and the loop state: an active LOOP at its start address,
        or a QBBC/QBBS-to-self on R31 bit 28 (cached per pc).
        """
        if self.halted:
            return False
        pc = self.pc
        loop = self.loop_state
        if loop is not None and pc == loop.start_address and loop.count > 0:
            return pc < len(self.instructions)
        # Hot path (every run-mode step of busy code): one dict lookup.
        if self._spin_cache_for is self.instructions:
            hit = self._spin_cache.get(pc)
            if hit is not None:
                return hit
        return self._spin_at(pc)

    def _spin_at(self, pc: int) -> bool:
        """True if instruction *pc* is ``QBBC/QBBS pc, r31, n`` with R31 bit
        28 (SD valid) as the tested bit. Static, so cached per program
        (a pc past the end caches False)."""
        if self._spin_cache_for is not self.instructions:
            self._spin_cache = {}
            self._spin_cache_for = self.instructions
        hit = self._spin_cache.get(pc)
        if hit is None:
            instructions = self.instructions
            hit = pc < len(instructions) and self._is_valid_spin(instructions[pc], pc)
            self._spin_cache[pc] = hit
        return hit

    @staticmethod
    def _is_valid_spin(instr, pc: int) -> bool:
        op = instr.opcode
        if op != "QBBC" and op != "QBBS":
            return False
        if len(instr.operands) != 3:
            return False
        label, reg, bit = instr.operands
        if isinstance(label, Label):
            target = label.resolved_addr
        elif isinstance(label, Immediate):
            target = label.value
        else:
            return False
        if target != pc:
            return False
        if not isinstance(reg, Register) or reg.index != 31:
            return False
        if not isinstance(bit, Immediate) or not 0 <= bit.value < reg.width:
            return False
        return reg.offset + bit.value == 28      # only the SD valid flag

    def _idle_plan(self, max_cycles: int,
                   horizon: int | float | None = None) -> tuple[str, int] | None:
        """Return ``(kind, cycles)`` for a skippable span at pc, else None.

        The static pattern test (``idle_candidate``) comes first, so busy
        code pays only for that.
        """
        if not self.idle_candidate():
            return None
        return self._idle_plan_at_candidate(max_cycles, horizon)

    def _idle_plan_at_candidate(self, max_cycles: int,
                                horizon: int | float | None = None) -> tuple[str, int] | None:
        """``_idle_plan`` for a core whose ``idle_candidate()`` is True."""
        pc = self.pc
        io_port = self.io_port
        if io_port.uart_generator is not None or io_port.i2c_device is not None:
            return None
        perif = io_port.perif
        if perif is not None and (perif.enabled or not _perif_quiet(perif)):
            return None
        if self.task_ring is not None and self.task_ring.busy:
            return None
        breakpoints = self.breakpoints
        loop = self.loop_state
        loop_active = loop is not None and not loop.is_done()

        # ---- Delay-loop collapse ---------------------------------------
        if loop_active and pc == loop.start_address:
            body = self._nop_body_len(loop.start_address, loop.end_address)
            if body and not any(a in breakpoints for a in range(loop.start_address,
                                                                loop.end_address)):
                budget = max_cycles
                sd = io_port.sd_filter
                link = sd.pin_link if sd is not None else None
                if link is not None and link.consumer is self:
                    # step() pre-ticks the link before every instruction, NOPs
                    # included, and raises once a start passes the horizon.
                    # Keep every start of the span within the horizon.
                    starts = self._starts_within_horizon(link, horizon)
                    if starts is not None:
                        budget = min(budget, starts)
                k = min(loop.count, budget // body)
                if k >= 1:
                    return ("loop", k * body)
            return None

        # ---- Poll fast-forward (pc holds an R31[28] self-spin) ----------
        if pc in breakpoints:
            return None
        # A taken branch at the loop's end address would wrap the loop: that
        # is no spin.
        if loop_active and pc == loop.end_address:
            return None
        sd = io_port.sd_filter
        if sd is None or not sd.sd_en:        # R31 must read SD status
            return None
        link = sd.pin_link
        if link is None or link.consumer is not self:
            return None
        ch = sd.ch_sel if sd.ch_sel < len(sd.channels) else 0
        if sd.sources[ch] != "pin":
            return None
        valid = sd.channels[ch].valid
        if self.instructions[pc].opcode == "QBBC":
            # Spins while valid is clear; exits at the first start >= te.
            if valid:
                return None
            te = link.next_valid_time(ch)
        else:
            # Spins while valid is set. Clock edges never clear valid (only
            # an R31 command does), so the spin is bounded by time alone.
            if not valid:
                return None
            te = None
        k = max_cycles
        if te is not None:
            # Iteration j starts at t0 + j*unit; count the starts before te.
            t0, unit = self._link_clock(link)
            k = min(k, max(0, -((t0 - te) // unit)))
        starts = self._starts_within_horizon(link, horizon)
        if starts is not None:
            k = min(k, starts)
        if k < 1:
            return None
        return ("poll", int(k))

    def _link_clock(self, link) -> tuple[int, int]:
        """(time of the current cycle, time units per cycle) on the link."""
        cycles = self.counters.cycles
        name = self.name.lower()
        t0 = link.time_of(name, cycles)
        return t0, link.time_of(name, cycles + 1) - t0

    def _starts_within_horizon(self, link, horizon: int | float | None) -> int | None:
        """How many consecutive 1-cycle instruction starts, from now, lie at
        or before the producer horizon (the consumer may not start past it).
        None = unbounded. *horizon* overrides ``link.horizon()``."""
        h = link.horizon() if horizon is None else horizon
        if h == math.inf:
            return None
        t0, unit = self._link_clock(link)
        return 0 if h < t0 else int((h - t0) // unit + 1)

    def _nop_body_len(self, start: int, end: int) -> int:
        """Length of [start, end) if every instruction in it is a NOP, else 0."""
        if self._nop_body_cache_for is not self.instructions:
            self._nop_body_cache = {}
            self._nop_body_cache_for = self.instructions
        key = (start, end)
        body = self._nop_body_cache.get(key)
        if body is None:
            instructions = self.instructions
            body = end - start
            if (body <= 0 or end > len(instructions)
                    or any(instructions[a].opcode != "NOP" for a in range(start, end))):
                body = 0
            self._nop_body_cache[key] = body
        return body

    def _run_idle_plan(self, plan: tuple[str, int]) -> None:
        kind, cycles = plan
        last_start = self.counters.cycles + cycles - 1
        if kind == "loop":
            loop = self.loop_state
            body = loop.end_address - loop.start_address
            loop.count -= cycles // body
            if loop.count <= 0:
                # The last NOP retired at end_address and ended the loop.
                self.pc = loop.end_address
                self.loop_state = None
            sd = self.io_port.sd_filter
            link = sd.pin_link if sd is not None else None
            if link is not None and link.consumer is self:
                # As stepping: every edge up to the last NOP's start applied.
                link.advance_consumer(last_start)
        else:
            # Each skipped iteration is one taken 1-cycle branch: pc stays.
            # Stepping pre-ticks the link at every iteration start, the last
            # one at last_start; applying up to there leaves the same edges
            # applied (and stays within the producer's horizon).
            self.io_port.sd_filter.pin_link.advance_consumer(last_start)
        self.step_start_cycle = last_start
        self._advance_bulk(cycles, cycles)

    def _advance_bulk(self, cycles: int, instructions: int) -> None:
        """Advance *cycles* cycles of *instructions* idle 1-cycle instructions.

        Does, for the whole span, what the tail of ``step()`` does per
        instruction: same subsystems, same order. Only valid for spans that
        change no register, memory or pin (NOP spans, R31 poll spins).
        """
        mac = self.accelerators[MACAccelerator.DEVICE_ID]
        # R28/R29 do not change in the span: two begin_step() calls leave the
        # one-instruction-late latch exactly as n stepped instructions would.
        for _ in range(min(instructions, 2)):
            mac.begin_step()
        io_port = self.io_port
        if io_port.sd_filter is not None:
            io_port.sd_filter.tick_n(instructions)
        if io_port.perif is not None:
            # step() advances PERIF to the instruction's start cycle; the
            # span's last instruction starts one cycle before its end.
            io_port.perif.advance_cycles(self.counters.cycles + cycles - 1)
        for acc in self._timed_accelerators:
            acc.advance(cycles)
        self.counters.cycles += cycles
        self.counters.instruction_count += instructions
        if io_port.device_bus.devices:
            io_port.advance_devices(cycles)
        else:
            io_port._device_cycle += cycles
        if self.task_ring is not None:
            self.task_ring.tick(cycles)
        if self.cycle_observer is not None:
            self.cycle_observer(cycles)
        self.skipped_cycles += cycles

    def run(self, max_steps: int = 100_000) -> int:
        """Run until halted or max_steps reached. Returns steps executed."""
        steps = 0
        while steps < max_steps:
            running = not self.halted and self.pc < len(self.instructions)
            if not running and not (self.task_ring is not None and self.task_ring.busy):
                break
            if self.pc in self.breakpoints:
                break
            self.step()
            steps += 1
        return steps

    # ------------------------------------------------------------------
    # Unsupported-XFR diagnostics
    # ------------------------------------------------------------------

    def _note_unsupported_xfr(self, opcode: str, device_id: int, start_reg: int,
                              start_byte: int, length: int) -> None:
        """Record a broadside transfer to a device ID this simulator does not model.

        Default: warn once per ID, record every transfer and use zero reads /
        discarded writes as a compatibility fallback. This is not a hardware
        claim for arbitrary absent or unmodelled IDs. Only AM261x XIN 0xA1
        has a measured zero-fill observation (upstream #32). Strict mode
        raises before changing destination registers or dispatching a write.
        """
        record = self.unsupported_xfr.get(device_id)
        first_sighting = record is None
        if first_sighting:
            record = {
                "device_id": device_id,
                "core": self.name,
                "opcodes": [],
                "first_pc": self.pc,
                "count": 0,
            }
            self.unsupported_xfr[device_id] = record
        record["count"] += 1
        if opcode not in record["opcodes"]:
            record["opcodes"].append(opcode)

        absent = device_id not in self.capabilities.broadside_ids
        reason = (f"absent on {self.target}" if absent else "not modelled by this simulator")
        detail = (
            f"{opcode} XFR device ID {device_id} (0x{device_id:02X}) is {reason}; "
            f"{self.name} at PC {self.pc} transfers {length} byte(s) "
            f"starting at R{start_reg}.b{start_byte}"
        )
        if self.strict_unsupported_xfr:
            raise UnsupportedXFRError(
                f"{detail}. strict_unsupported_xfr is enabled, so the run fails here "
                f"instead of continuing on zero data."
            )
        if first_sighting:
            logger.warning(
                "%s. Reads return zeros and writes are discarded as a simulator fallback "
                "(silicon zero-fill observed only for AM261x XIN BSWAP_4_8, #32). "
                "Other absent IDs have unverified hardware behaviour. "
                "Use strict_unsupported_xfr to reject this fallback. Later transfers are "
                "not logged again; see "
                "PRUCore.unsupported_xfr for the full record.",
                detail,
            )

    # ------------------------------------------------------------------
    # Operand helpers
    # ------------------------------------------------------------------

    def _map_data_addr(self, addr: int) -> int:
        """Translate a core-local data address into the global address space.

        Each PRU sees its OWN DRAM at local 0x0000 and the other core's DRAM at
        local 0x2000 (AM243x ICSSG).  The simulator holds DRAM0 at global
        0x0000 and DRAM1 at global 0x2000, so PRU1's two 8 KB banks are
        swapped; XOR with 0x2000 does exactly that.  Addresses at or above
        0x4000 (shared RAM, MS_RAM, ICSS_CFG) are global for both cores.
        """
        if self.dram_swap and addr < 0x4000:
            return addr ^ 0x2000
        return addr

    def _resolve_cn(self, op) -> int:
        """Resolve a constant-table operand ('c0'–'c31') to its base address."""
        name = op.name if isinstance(op, Label) else (op if isinstance(op, str) else None)
        if name is not None:
            m = re.match(r'^c(\d+)$', name, re.IGNORECASE)
            if m:
                return self.constant_table.resolve(int(m.group(1)))
        # Disassembler emits Immediate(N) for constant table index in LBCO/SBCO
        if isinstance(op, Immediate) and 0 <= op.value <= 31:
            return self.constant_table.resolve(op.value)
        return self._read_operand(op)

    def _read_operand(self, op) -> int:
        """Read the value of an operand."""
        if isinstance(op, Register):
            if op.index == 31:
                # R31 is read live from the I/O port, but the operand's byte /
                # half-word selection still applies. Returning the full word
                # for `r31.b3` silently reads bits [7:0] instead of [31:24].
                val = self.io_port.read_r31()
                if op.width >= 32 and op.offset == 0:
                    return val
                return (val >> op.offset) & ((1 << op.width) - 1)
            return self.registers.read(op.index, op.offset, op.width)
        if isinstance(op, Immediate):
            return op.value
        if isinstance(op, BitField):
            return self.registers.read_full(op.reg)
        if isinstance(op, Label):
            return op.resolved_addr
        # Fallback: integer or other
        return int(op)

    def _write_operand(self, op, value: int) -> None:
        """Write *value* to an operand destination."""
        if isinstance(op, Register):
            if op.index == 31:
                # R31 is write-only to hardware (command register) - do not
                # store in the register file. The operand's byte / half-word
                # selection still applies: `MOV r31.b3, rX.b0` targets bits
                # [31:24]. R30 below already derives its strobe this way.
                if op.width < 32 or op.offset:
                    value = (value & ((1 << op.width) - 1)) << op.offset
                self.io_port.write_r31(value)
                return
            self.registers.write(op.index, op.offset, op.width, value)
            if op.index == 30:
                wstrb = ((1 << (op.width // 8)) - 1) << (op.offset // 8)
                self.io_port.write_r30(self.registers.read_full(30), wstrb & 0xF)
        elif isinstance(op, BitField):
            # BitField writes are typically not direct destinations, but handle gracefully
            self.registers.write_full(op.reg, value)

    def _write_registers_from_bytes(self, start_reg: int, data: bytes,
                                    start_byte: int = 0) -> None:
        """Write *data* into consecutive registers starting at byte *start_byte*
        of *start_reg*.

        Only the bytes covered by *data* are modified; other bytes in the
        first/last register are preserved (byte-level write strobes).
        """
        byte_offset = start_byte  # 0-3 within current register
        reg_idx = start_reg
        pos = 0
        while pos < len(data) and reg_idx < 32:
            # How many bytes can we write into this register?
            space = 4 - byte_offset
            chunk = min(space, len(data) - pos)

            if byte_offset == 0 and chunk == 4:
                # Full register write — fast path
                word = struct.unpack_from("<I", data, pos)[0]
                self.registers.write_full(reg_idx, word)
            else:
                # Partial register write — preserve untouched bytes
                old_word = self.registers.read_full(reg_idx)
                old_bytes = bytearray(struct.pack("<I", old_word))
                old_bytes[byte_offset:byte_offset + chunk] = data[pos:pos + chunk]
                word = struct.unpack_from("<I", bytes(old_bytes), 0)[0]
                self.registers.write_full(reg_idx, word)
                # R31 intentionally omitted: bulk writes to R31 are architecturally invalid

            if reg_idx == 30:
                # Byte write-strobe for the bytes this chunk actually wrote.
                wstrb = ((1 << chunk) - 1) << byte_offset
                self.io_port.write_r30(self.registers.read_full(30), wstrb & 0xF)

            pos += chunk
            reg_idx += 1
            byte_offset = 0  # subsequent registers always start at byte 0

    def _read_registers_to_bytes(self, start_reg: int, length: int,
                                 start_byte: int = 0) -> bytes:
        """Read *length* bytes from consecutive registers starting at byte
        *start_byte* of *start_reg*.

        Registers are read as 32-bit little-endian words.
        """
        result = bytearray()
        byte_offset = start_byte
        reg_idx = start_reg
        remaining = length
        while remaining > 0 and reg_idx < 32:
            word = self.registers.read_full(reg_idx)
            reg_bytes = struct.pack("<I", word)
            available = 4 - byte_offset
            chunk = min(available, remaining)
            result += reg_bytes[byte_offset:byte_offset + chunk]
            remaining -= chunk
            reg_idx += 1
            byte_offset = 0
        return bytes(result)

    # ------------------------------------------------------------------
    # XFR shift helpers (ICSSG_SPP_REG XFR_SHIFT_EN)
    # ------------------------------------------------------------------
    # When XFR_SHIFT_EN is set, each register word is mapped to bank
    # position (reg + R0[4:0]) % 30.  SPAD banks hold R0–R29 (30 regs,
    # no R30/R31), so the modulus wraps within 30 not 32.

    _SPAD_BANK_SIZE = 30  # registers per bank (R0–R29)

    def _xfr_bank_offset(self, reg: int) -> int:
        """Return the shifted bank byte offset for *reg* using R0[4:0]."""
        shift = self.registers.regs[0] & 0x1F
        return ((reg + shift) % self._SPAD_BANK_SIZE) * 4

    def _xout_shifted(self, device_id: int, start_reg: int, length: int) -> None:
        num_regs = (length + 3) // 4
        for i in range(num_regs):
            reg = start_reg + i
            word_len = min(4, length - i * 4)
            data = self._read_registers_to_bytes(reg, word_len)
            self.xfr.xout(device_id, self._xfr_bank_offset(reg), data)

    def _xin_shifted(self, device_id: int, start_reg: int, length: int) -> None:
        num_regs = (length + 3) // 4
        for i in range(num_regs):
            reg = start_reg + i
            word_len = min(4, length - i * 4)
            data = self.xfr.xin(device_id, self._xfr_bank_offset(reg), word_len)
            self._write_registers_from_bytes(reg, data)

    def _xchg_shifted(self, device_id: int, start_reg: int, length: int) -> None:
        num_regs = (length + 3) // 4
        for i in range(num_regs):
            reg = start_reg + i
            word_len = min(4, length - i * 4)
            data = self._read_registers_to_bytes(reg, word_len)
            old_data = self.xfr.xchg(device_id, self._xfr_bank_offset(reg), data)
            self._write_registers_from_bytes(reg, old_data)

    # ------------------------------------------------------------------
    # MVI helpers (register file indirect)
    # ------------------------------------------------------------------
    # The 128-byte register file (R0-R31, 4 bytes each, little-endian) is
    # addressed by a byte offset held in a byte field of R1.
    # Pointer values are used mod 128; byte fields wrap at 256.

    # MVI sel → bit offset within the direct register (for the data field)
    _MVI_SEL_BIT_OFFSET = {0: 0, 1: 8, 2: 16, 3: 24,
                            4: 0, 5: 8,  6: 16,  7: 0}

    def _mvi_read_ptr(self, op: MVIOperand) -> int:
        """Read pointer byte from op.sel byte-field of op.reg; result is mod 128."""
        bit_off = self._MVI_SEL_BIT_OFFSET[op.sel]
        return self.registers.read(op.reg, bit_off, 8) & 0x7F

    def _mvi_update_ptr(self, op: MVIOperand, delta: int) -> None:
        """Add *delta* to the pointer byte field (wraps at 256)."""
        bit_off = self._MVI_SEL_BIT_OFFSET[op.sel]
        old = self.registers.read(op.reg, bit_off, 8)
        self.registers.write(op.reg, bit_off, 8, (old + delta) & 0xFF)

    def _mvi_read_regfile(self, byte_offset: int, size: int) -> bytes:
        """Read *size* bytes from the register file starting at *byte_offset* (mod 128)."""
        result = bytearray()
        for i in range(size):
            bo = (byte_offset + i) & 0x7F
            word = self.registers.read_full(bo >> 2)
            result.append((word >> ((bo & 3) * 8)) & 0xFF)
        return bytes(result)

    def _mvi_write_regfile(self, byte_offset: int, data: bytes) -> None:
        """Write *data* bytes to the register file starting at *byte_offset* (mod 128)."""
        for i, b in enumerate(data):
            bo = (byte_offset + i) & 0x7F
            reg_idx = bo >> 2
            shift = (bo & 3) * 8
            word = self.registers.read_full(reg_idx)
            word = (word & ~(0xFF << shift)) | (b << shift)
            self.registers.write_full(reg_idx, word)
            if reg_idx == 30:
                self.io_port.write_r30(word)

    def _mvi_read_direct(self, op: MVIOperand, size: int) -> bytes:
        """Read *size* bytes from the direct register operand.

        R31 reads go through io_port.read_r31() so the loopback-updated GPI
        value (and SD status in SD mode) is returned, not the stale register file.
        """
        start_byte = self._MVI_SEL_BIT_OFFSET[op.sel] >> 3
        if op.reg == 31:
            word = self.io_port.read_r31()
            return bytes((word >> ((start_byte + i) * 8)) & 0xFF for i in range(size))
        return self._read_registers_to_bytes(op.reg, size, start_byte)

    def _mvi_write_direct(self, op: MVIOperand, data: bytes) -> None:
        """Write *data* bytes into the direct register operand."""
        start_byte = self._MVI_SEL_BIT_OFFSET[op.sel] >> 3
        self._write_registers_from_bytes(op.reg, data, start_byte)
