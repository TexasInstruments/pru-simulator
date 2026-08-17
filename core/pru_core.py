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
import re
import struct

logger = logging.getLogger(__name__)

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


class PRUCore:
    """Simulates a single PRU core executing PRU assembly instructions."""

    def __init__(self, name: str, memory: MemoryBus, xfr: XFRBus, io_port: IOPort,
                 constant_table: ConstantTable | None = None,
                 dram_swap: bool = False):
        self.name = name
        self.registers = RegisterFile()
        self.counters = CycleCounters()
        self.memory = memory
        self.xfr = xfr
        self.io_port = io_port
        self.constant_table: ConstantTable = constant_table if constant_table is not None else ConstantTable()
        # PRU1 sees its own DRAM (DRAM1) at core-local 0x0000 and DRAM0 at
        # 0x2000 -- the reverse of PRU0. See _map_data_addr.
        self.dram_swap = dram_swap
        self.pc: int = 0
        self.halted: bool = False
        self.instructions: list[Instruction] = []
        self.loop_state: LoopState | None = None
        self.breakpoints: set[int] = set()

        self._parser = Parser()
        self._branch = BranchUnit()
        self.accelerators: dict[int, Accelerator] = {
            MACAccelerator.DEVICE_ID: MACAccelerator(self.registers),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_asm(self, source: str, include_paths: list[str] | None = None) -> list[str]:
        """Parse assembly source and load instructions.

        Returns a list of error strings (empty on success).
        """
        errors: list[str] = []
        try:
            self.instructions = self._parser.parse_text(source, include_paths)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        return errors

    def load_binary(self, text_words: list, data_bytes: bytes = b'',
                    data_addr: int = 0, symbols: dict = None) -> list:
        """Load pre-compiled binary instructions (from .out ELF).

        Returns a list of error strings (empty on success).
        """
        from .disassembler import disassemble
        errors: list[str] = []
        try:
            syms = symbols or {}
            self.instructions = disassemble(text_words, syms)
            # Store symbols as labels (name → addr) for the frontend
            self._parser.labels = {name: addr for addr, name in syms.items()}
            if data_bytes:
                self.memory.write(data_addr, data_bytes)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        return errors

    def reset(self) -> None:
        """Reset all state to initial conditions."""
        self.registers.regs[:] = [0] * 32
        self.registers.carry = False
        self.counters.reset()
        self.pc = 0
        self.halted = False
        self.loop_state = None
        for acc in self.accelerators.values():
            acc.reset()
        self.io_port.reset()

    def step(self) -> None:
        """Execute one instruction."""
        if self.halted or self.pc >= len(self.instructions):
            return

        # Pre-tick: advance UART frame generator before instruction reads R31
        if self.io_port.uart_generator is not None:
            self.io_port.uart_generator.tick(self.counters.cycles)

        # Pre-tick: advance SSI encoder generator (edge-driven off the clock GPO)
        # before instruction reads R31, so a same-instruction data sample is fresh
        if self.io_port.ssi_generator is not None:
            self.io_port.ssi_generator.tick(self.counters.cycles)

        instr = self.instructions[self.pc]
        branch_taken = False

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
                data, stalls = self.memory.read(addr, length)
                start_reg = reg_op.index if isinstance(reg_op, Register) else 0
                start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
                self._write_registers_from_bytes(start_reg, data, start_byte)
                self.counters.stall(stalls)
            except ValueError as e:
                logger.error(f"LBBO fault at 0x{addr:08X}: {e}")
                self.halted = True

        elif op == "LBCO":
            # LBCO &reg, CN, offset, length
            reg_op, cn_op, offset_op, length_op = instr.operands
            base = self._resolve_cn(cn_op)
            offset = self._read_operand(offset_op)
            length = self._read_operand(length_op)
            addr = self._map_data_addr(base + offset)
            try:
                data, stalls = self.memory.read(addr, length)
                start_reg = reg_op.index if isinstance(reg_op, Register) else 0
                start_byte = (reg_op.offset // 8) if isinstance(reg_op, Register) else 0
                self._write_registers_from_bytes(start_reg, data, start_byte)
                self.counters.stall(stalls)
            except ValueError as e:
                logger.error(f"LBCO fault at 0x{addr:08X}: {e}")
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
                logger.error(f"SBBO fault at 0x{addr:08X}: {e}")
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
                data = self.accelerators[device_id].xin(start_reg, length)
                self._write_registers_from_bytes(start_reg, data, start_byte)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xin_shifted(device_id, start_reg, length)
            else:
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
                self.accelerators[device_id].xout(start_reg, data)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xout_shifted(device_id, start_reg, length)
            else:
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
                old_data = self.accelerators[device_id].xchg(start_reg, data)
                self._write_registers_from_bytes(start_reg, old_data, start_byte)
            elif self.xfr.xfr_shift_en and device_id in (SPAD_BANK0, SPAD_BANK1, SPAD_BANK2):
                self._xchg_shifted(device_id, start_reg, length)
            else:
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

        elif op == "NOP":
            pass

        elif op in ("WBS", "WBC"):
            # WBS/WBC are QBBS/QBBC with offset=0 (branch to self until bit changes).
            # Real hardware: PRU sleeps until the pin matches. We model this by
            # re-executing the same instruction (branch_taken=True, PC stays) until
            # the bit condition is satisfied.
            reg, bit = instr.operands
            reg_val = self._read_operand(reg)
            bit_val = self._read_operand(bit)
            # WBS waits until bit is SET  (keep spinning while bit is CLEAR)
            # WBC waits until bit is CLEAR (keep spinning while bit is SET)
            should_spin = self._branch.qbbc(reg_val, bit_val) if op == "WBS" else self._branch.qbbs(reg_val, bit_val)
            if should_spin:
                branch_taken = True  # PC does not advance — re-execute next cycle

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

        # ---- Count instruction cycle ------------------------------------
        self.counters.tick()

    def run(self, max_steps: int = 100_000) -> int:
        """Run until halted or max_steps reached. Returns steps executed."""
        steps = 0
        while not self.halted and self.pc < len(self.instructions) and steps < max_steps:
            if self.pc in self.breakpoints:
                break
            self.step()
            steps += 1
        return steps

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
                return self.io_port.read_r31()
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
                # R31 is write-only to hardware (command register) — do not store in register file
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
