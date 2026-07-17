"""PRU instruction disassembler — decodes 32-bit words into Instruction objects.

Based on PDSP v2.4.8 specification (pdsp_v2p4p8.docx).
"""

from .parser import Instruction
from .operands import Register, Immediate, MVIOperand

# Field selector: 3-bit code → (offset_bits, width_bits)
_FIELD_MAP = {
    0: (0, 8),    # b0: bits 7:0
    1: (8, 8),    # b1: bits 15:8
    2: (16, 8),   # b2: bits 23:16
    3: (24, 8),   # b3: bits 31:24
    4: (0, 16),   # w0: bits 15:0
    5: (8, 16),   # w1: bits 23:8
    6: (16, 16),  # w2: bits 31:16
    7: (0, 32),   # full register
}

_FIELD_SUFFIX = {
    0: '.b0', 1: '.b1', 2: '.b2', 3: '.b3',
    4: '.w0', 5: '.w1', 6: '.w2', 7: '',
}

# Format 1 ALU opcodes
_ALU_OPS = [
    'ADD', 'ADC', 'SUB', 'SUC', 'LSL', 'LSR', 'RSB', 'RSC',
    'AND', 'OR', 'XOR', 'NOT', 'MIN', 'MAX', 'CLR', 'SET',
]

# Format 4 branch condition bits → mnemonic
_COND_BRANCH_MAP = {
    (1, 0, 0): 'QBGT',
    (0, 1, 0): 'QBEQ',
    (0, 0, 1): 'QBLT',
    (1, 1, 0): 'QBGE',
    (0, 1, 1): 'QBLE',
    (1, 0, 1): 'QBNE',  # GT|LT = not equal
    (1, 1, 1): 'QBA',    # GT|EQ|LT = always
}


def _decode_reg(sel, num):
    """Decode a register field selector + number into a Register operand."""
    offset, width = _FIELD_MAP[sel]
    return Register(num, offset, width)


def _reg_str(sel, num):
    """Format register as string."""
    suffix = _FIELD_SUFFIX[sel]
    return f'r{num}{suffix}'


def _sign_extend_10(val):
    """Sign-extend a 10-bit value."""
    if val & 0x200:
        return val - 0x400
    return val


def disassemble_word(word, addr=0, symbols=None):
    """Decode a single 32-bit PRU instruction word into an Instruction object."""
    symbols = symbols or {}
    fmt = (word >> 29) & 0x7

    if fmt == 0b000:
        return _decode_format1(word, addr, symbols)
    elif fmt == 0b001:
        return _decode_format2(word, addr, symbols)
    elif fmt in (0b010, 0b011):
        return _decode_format4(word, addr, symbols)
    elif fmt == 0b100:
        return _decode_format6cd(word, addr, symbols)
    elif fmt == 0b110:
        return _decode_format5(word, addr, symbols)
    elif fmt == 0b111:
        return _decode_format6ab(word, addr, symbols)
    else:
        # Format 0b101 — unknown/reserved
        return Instruction(
            address=addr, opcode='DW', operands=[Immediate(word)],
            source_line=0, source_text=f'.dw 0x{word:08X}')


def _decode_format1(word, addr, symbols):
    """Format 1: ALU operations."""
    aluop = (word >> 25) & 0xF
    io = (word >> 24) & 1
    rd_sel = (word >> 5) & 7
    rd_num = word & 0x1F
    rs1_sel = (word >> 13) & 7
    rs1_num = (word >> 8) & 0x1F

    opcode = _ALU_OPS[aluop] if aluop < len(_ALU_OPS) else f'ALU{aluop}'
    rd = _decode_reg(rd_sel, rd_num)
    rs1 = _decode_reg(rs1_sel, rs1_num)

    if opcode == 'NOT':
        # NOT only has Rd, Rs1 (no src2)
        text = f'NOT {_reg_str(rd_sel, rd_num)}, {_reg_str(rs1_sel, rs1_num)}'
        return Instruction(address=addr, opcode=opcode, operands=[rd, rs1],
                           source_line=0, source_text=text)

    if io == 0:
        rs2_sel = (word >> 21) & 7
        rs2_num = (word >> 16) & 0x1F
        rs2 = _decode_reg(rs2_sel, rs2_num)

        # Pseudo-instruction: NOP = AND r0.b0, r0.b0, r0.b0
        if (opcode == 'AND' and rd_num == 0 and rd_sel == 0
                and rs1_num == 0 and rs1_sel == 0
                and rs2_num == 0 and rs2_sel == 0):
            return Instruction(address=addr, opcode='NOP', operands=[],
                               source_line=0, source_text='NOP')

        # Pseudo-instruction: MOV = AND Rd, Rs, Rs (same src1 and src2)
        if (opcode == 'AND' and rs1_sel == rs2_sel and rs1_num == rs2_num):
            text = f'MOV {_reg_str(rd_sel, rd_num)}, {_reg_str(rs1_sel, rs1_num)}'
            return Instruction(address=addr, opcode='MOV', operands=[rd, rs1],
                               source_line=0, source_text=text)

        text = f'{opcode} {_reg_str(rd_sel, rd_num)}, {_reg_str(rs1_sel, rs1_num)}, {_reg_str(rs2_sel, rs2_num)}'
        return Instruction(address=addr, opcode=opcode, operands=[rd, rs1, rs2],
                           source_line=0, source_text=text)
    else:
        imm = (word >> 16) & 0xFF
        text = f'{opcode} {_reg_str(rd_sel, rd_num)}, {_reg_str(rs1_sel, rs1_num)}, {imm}'
        return Instruction(address=addr, opcode=opcode, operands=[rd, rs1, Immediate(imm)],
                           source_line=0, source_text=text)


# MVI SubOp → (dst_indirect, dst_predec, dst_postinc,
#               src_indirect, src_predec, src_postinc)
_MVI_MODES = {
    0:  (False, False, False, False, False, False),  # Reserved
    1:  (False, False, False, True,  False, False),  # Rd = *Rs1
    2:  (False, False, False, True,  False, True),   # Rd = *Rs1++
    3:  (False, False, False, True,  True,  False),  # Rd = *--Rs1
    4:  (True,  False, False, False, False, False),  # *Rd = Rs1
    5:  (True,  False, False, True,  False, False),  # *Rd = *Rs1
    6:  (True,  False, False, True,  False, True),   # *Rd = *Rs1++
    7:  (True,  False, False, True,  True,  False),  # *Rd = *--Rs1
    8:  (True,  False, True,  False, False, False),  # *Rd++ = Rs1
    9:  (True,  False, True,  True,  False, False),  # *Rd++ = *Rs1
    10: (True,  False, True,  True,  False, True),   # *Rd++ = *Rs1++
    11: (True,  False, True,  True,  True,  False),  # *Rd++ = *--Rs1
    12: (True,  True,  False, False, False, False),  # *--Rd = Rs1
    13: (True,  True,  False, True,  False, False),  # *--Rd = *Rs1
    14: (True,  True,  False, True,  False, True),   # *--Rd = *Rs1++
    15: (True,  True,  False, True,  True,  False),  # *--Rd = *--Rs1
}


def _mvi_op_str(indirect, predec, postinc, sel, num):
    """Format an MVIx operand as assembly text."""
    reg = f'r{num}{_FIELD_SUFFIX[sel]}'
    if indirect:
        if predec:
            return f'*--{reg}'
        return f'*{reg}++' if postinc else f'*{reg}'
    if predec:
        return f'--{reg}'
    return f'{reg}++' if postinc else reg


def _decode_format2(word, addr, symbols):
    """Format 2: JMP, JAL, LDI, LMBD, HALT, MVI, XFR, LOOP, SLP."""
    subop = (word >> 25) & 0xF
    io = (word >> 24) & 1

    if subop == 0 or subop == 1:
        # JMP / JAL
        opcode = 'JMP' if subop == 0 else 'JAL'
        rd_sel = (word >> 5) & 7
        rd_num = word & 0x1F
        if io == 0:
            rs2_sel = (word >> 21) & 7
            rs2_num = (word >> 16) & 0x1F
            if subop == 0:
                text = f'JMP {_reg_str(rs2_sel, rs2_num)}'
                return Instruction(address=addr, opcode='JMP',
                                   operands=[_decode_reg(rs2_sel, rs2_num)],
                                   source_line=0, source_text=text)
            else:
                text = f'JAL {_reg_str(rd_sel, rd_num)}, {_reg_str(rs2_sel, rs2_num)}'
                return Instruction(address=addr, opcode='JAL',
                                   operands=[_decode_reg(rd_sel, rd_num),
                                             _decode_reg(rs2_sel, rs2_num)],
                                   source_line=0, source_text=text)
        else:
            imm = (word >> 8) & 0xFFFF
            target_label = symbols.get(imm, '')
            if subop == 0:
                target = target_label or f'0x{imm:04X}'
                text = f'JMP {target}'
                return Instruction(address=addr, opcode='JMP',
                                   operands=[Immediate(imm)],
                                   source_line=0, source_text=text)
            else:
                target = target_label or f'0x{imm:04X}'
                text = f'JAL {_reg_str(rd_sel, rd_num)}, {target}'
                return Instruction(address=addr, opcode='JAL',
                                   operands=[_decode_reg(rd_sel, rd_num), Immediate(imm)],
                                   source_line=0, source_text=text)

    elif subop == 2:
        # LDI
        imm = (word >> 8) & 0xFFFF
        rd_sel = (word >> 5) & 7
        rd_num = word & 0x1F
        rd = _decode_reg(rd_sel, rd_num)
        text = f'LDI {_reg_str(rd_sel, rd_num)}, 0x{imm:04X}'
        return Instruction(address=addr, opcode='LDI', operands=[rd, Immediate(imm)],
                           source_line=0, source_text=text)

    elif subop == 3:
        # LMBD
        rd_sel = (word >> 5) & 7
        rd_num = word & 0x1F
        rs1_sel = (word >> 13) & 7
        rs1_num = (word >> 8) & 0x1F
        rd = _decode_reg(rd_sel, rd_num)
        rs1 = _decode_reg(rs1_sel, rs1_num)
        if io == 0:
            rs2_sel = (word >> 21) & 7
            rs2_num = (word >> 16) & 0x1F
            rs2 = _decode_reg(rs2_sel, rs2_num)
            text = f'LMBD {_reg_str(rd_sel, rd_num)}, {_reg_str(rs1_sel, rs1_num)}, {_reg_str(rs2_sel, rs2_num)}'
            return Instruction(address=addr, opcode='LMBD', operands=[rd, rs1, rs2],
                               source_line=0, source_text=text)
        else:
            imm = (word >> 16) & 0xFF
            text = f'LMBD {_reg_str(rd_sel, rd_num)}, {_reg_str(rs1_sel, rs1_num)}, {imm}'
            return Instruction(address=addr, opcode='LMBD', operands=[rd, rs1, Immediate(imm)],
                               source_line=0, source_text=text)

    elif subop == 5:
        # HALT
        return Instruction(address=addr, opcode='HALT', operands=[],
                           source_line=0, source_text='HALT')

    elif subop == 7:
        # XFR (XIN/XOUT/XCHG)
        mode = (word >> 23) & 3
        device_id = (word >> 15) & 0xFF
        state = (word >> 14) & 1
        byte_len_raw = (word >> 7) & 0x7F
        reg_byte = (word >> 5) & 3
        reg_num = word & 0x1F

        mode_names = {1: 'XIN', 2: 'XOUT', 3: 'XCHG'}
        opcode = mode_names.get(mode, f'XFR{mode}')

        if byte_len_raw <= 123:
            byte_count = byte_len_raw + 1
            len_str = str(byte_count)
        else:
            r0_field = ['r0.b0', 'r0.b1', 'r0.b2', 'r0.b3'][byte_len_raw - 124]
            len_str = r0_field
            byte_count = byte_len_raw  # placeholder

        reg_str = f'r{reg_num}'
        if reg_byte > 0:
            reg_str += f'.b{reg_byte}'

        # Pseudo-instructions: ZERO = XIN 255, FILL = XIN 254
        if mode == 1 and device_id == 255:
            text = f'ZERO &{reg_str}, {len_str}'
            return Instruction(address=addr, opcode='ZERO',
                               operands=[Register(reg_num, reg_byte * 8, 8 if reg_byte else 32),
                                         Immediate(byte_count)],
                               source_line=0, source_text=text)
        if mode == 1 and device_id == 254:
            text = f'FILL &{reg_str}, {len_str}'
            return Instruction(address=addr, opcode='FILL',
                               operands=[Register(reg_num, reg_byte * 8, 8 if reg_byte else 32),
                                         Immediate(byte_count)],
                               source_line=0, source_text=text)

        text = f'{opcode} {device_id}, &{reg_str}, {len_str}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Immediate(device_id),
                                     Register(reg_num, reg_byte * 8, 8 if reg_byte else 32),
                                     Immediate(byte_count)],
                           source_line=0, source_text=text)

    elif subop == 8:
        # LOOP
        offset = word & 0xFF
        if io == 0:
            rs2_sel = (word >> 21) & 7
            rs2_num = (word >> 16) & 0x1F
            text = f'LOOP {addr + offset}, {_reg_str(rs2_sel, rs2_num)}'
            target_label = symbols.get(addr + offset, '')
            if target_label:
                text = f'LOOP {target_label}, {_reg_str(rs2_sel, rs2_num)}'
            return Instruction(address=addr, opcode='LOOP',
                               operands=[Immediate(addr + offset),
                                         _decode_reg(rs2_sel, rs2_num)],
                               source_line=0, source_text=text)
        else:
            count = ((word >> 16) & 0xFF) + 1
            target_label = symbols.get(addr + offset, '')
            target = target_label or str(addr + offset)
            text = f'LOOP {target}, {count}'
            return Instruction(address=addr, opcode='LOOP',
                               operands=[Immediate(addr + offset), Immediate(count)],
                               source_line=0, source_text=text)

    elif subop == 6:
        # MVI — move register file indirect (MVIB/MVIW/MVID)
        mvi_subop = (word >> 21) & 0xF   # bits 24:21
        size_bits  = (word >> 16) & 0x3  # bits 17:16
        rs1_sel    = (word >> 13) & 0x7  # bits 15:13
        rs1_num    = (word >> 8)  & 0x1F # bits 12:8
        rd_sel     = (word >> 5)  & 0x7  # bits 7:5
        rd_num     =  word        & 0x1F # bits 4:0

        opcode = {0: 'MVIB', 1: 'MVIW', 2: 'MVID'}.get(size_bits, 'MVIB')
        modes  = _MVI_MODES.get(mvi_subop, _MVI_MODES[0])
        di, dpre, dpost, si, spre, spost = modes

        dst_op = MVIOperand(rd_num,  rd_sel,  di, dpre, dpost)
        src_op = MVIOperand(rs1_num, rs1_sel, si, spre, spost)

        dst_str = _mvi_op_str(di, dpre, dpost, rd_sel,  rd_num)
        src_str = _mvi_op_str(si, spre, spost, rs1_sel, rs1_num)
        text = f'{opcode} {dst_str}, {src_str}'
        return Instruction(address=addr, opcode=opcode, operands=[dst_op, src_op],
                           source_line=0, source_text=text)

    elif subop == 15:
        # SLP
        wake = (word >> 23) & 1
        text = f'SLP {wake}'
        return Instruction(address=addr, opcode='SLP', operands=[Immediate(wake)],
                           source_line=0, source_text=text)

    # Unknown format 2 subop
    text = f'.dw 0x{word:08X}  ; fmt2 subop={subop}'
    return Instruction(address=addr, opcode='DW', operands=[Immediate(word)],
                       source_line=0, source_text=text)


def _decode_format4(word, addr, symbols):
    """Format 4: Quick arithmetic test and branch (QBEQ, QBNE, QBGT, etc.)."""
    gt = (word >> 29) & 1
    eq = (word >> 28) & 1
    lt = (word >> 27) & 1
    br_hi = (word >> 25) & 3
    io = (word >> 24) & 1
    rs1_sel = (word >> 13) & 7
    rs1_num = (word >> 8) & 0x1F
    br_lo = word & 0xFF

    # 10-bit signed offset
    br_raw = (br_hi << 8) | br_lo
    offset = _sign_extend_10(br_raw)
    target_addr = addr + offset

    opcode = _COND_BRANCH_MAP.get((gt, eq, lt), f'QBx({gt}{eq}{lt})')

    target_label = symbols.get(target_addr, '')
    target_str = target_label or f'{target_addr}'

    if opcode == 'QBA':
        text = f'QBA {target_str}'
        return Instruction(address=addr, opcode='QBA',
                           operands=[Immediate(target_addr)],
                           source_line=0, source_text=text)

    rs1 = _decode_reg(rs1_sel, rs1_num)

    if io == 0:
        rs2_sel = (word >> 21) & 7
        rs2_num = (word >> 16) & 0x1F
        rs2 = _decode_reg(rs2_sel, rs2_num)
        text = f'{opcode} {target_str}, {_reg_str(rs1_sel, rs1_num)}, {_reg_str(rs2_sel, rs2_num)}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Immediate(target_addr), rs1, rs2],
                           source_line=0, source_text=text)
    else:
        imm = (word >> 16) & 0xFF
        text = f'{opcode} {target_str}, {_reg_str(rs1_sel, rs1_num)}, {imm}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Immediate(target_addr), rs1, Immediate(imm)],
                           source_line=0, source_text=text)


def _decode_format5(word, addr, symbols):
    """Format 5: Bit test and branch (QBBS, QBBC, WBS, WBC)."""
    bs = (word >> 28) & 1
    bc = (word >> 27) & 1
    br_hi = (word >> 25) & 3
    io = (word >> 24) & 1
    rs1_sel = (word >> 13) & 7
    rs1_num = (word >> 8) & 0x1F
    br_lo = word & 0xFF

    br_raw = (br_hi << 8) | br_lo
    offset = _sign_extend_10(br_raw)
    target_addr = addr + offset

    # WBS/WBC are QBBS/QBBC with offset=0 (branch to self)
    # WBC = "wait for bit clear" = loop while set = QBBS (bs=1) + offset=0
    # WBS = "wait for bit set" = loop while clear = QBBC (bs=0) + offset=0
    if offset == 0:
        if bs:
            opcode = 'WBC'
        else:
            opcode = 'WBS'
    else:
        if bs:
            opcode = 'QBBS'
        else:
            opcode = 'QBBC'

    target_label = symbols.get(target_addr, '')
    target_str = target_label or f'{target_addr}'

    rs1 = _decode_reg(rs1_sel, rs1_num)

    if io == 0:
        rs2_sel = (word >> 21) & 7
        rs2_num = (word >> 16) & 0x1F
        rs2 = _decode_reg(rs2_sel, rs2_num)
        if offset == 0:
            text = f'{opcode} {_reg_str(rs1_sel, rs1_num)}, {_reg_str(rs2_sel, rs2_num)}'
        else:
            text = f'{opcode} {target_str}, {_reg_str(rs1_sel, rs1_num)}, {_reg_str(rs2_sel, rs2_num)}'
        operands = [Immediate(target_addr), rs1, rs2] if offset != 0 else [rs1, rs2]
        return Instruction(address=addr, opcode=opcode, operands=operands,
                           source_line=0, source_text=text)
    else:
        bit_num = (word >> 16) & 0x1F
        if offset == 0:
            text = f'{opcode} {_reg_str(rs1_sel, rs1_num)}, {bit_num}'
        else:
            text = f'{opcode} {target_str}, {_reg_str(rs1_sel, rs1_num)}, {bit_num}'
        operands = [Immediate(target_addr), rs1, Immediate(bit_num)] if offset != 0 else [rs1, Immediate(bit_num)]
        return Instruction(address=addr, opcode=opcode, operands=operands,
                           source_line=0, source_text=text)


def _decode_burst_len(word):
    """Decode split burst length field from Format 6."""
    hi = (word >> 25) & 7     # bits[27:25]
    mid = (word >> 13) & 7    # bits[15:13]
    lo = (word >> 7) & 1      # bit[7]
    raw = (hi << 4) | (mid << 1) | lo
    if raw <= 123:
        return raw + 1, str(raw + 1)
    else:
        r0_fields = ['r0.b0', 'r0.b1', 'r0.b2', 'r0.b3']
        return raw, r0_fields[raw - 124]


def _decode_format6ab(word, addr, symbols):
    """Format 6a/6b: LBBO/SBBO."""
    load_store = (word >> 28) & 1
    opcode = 'LBBO' if load_store else 'SBBO'
    io = (word >> 24) & 1
    rb_num = (word >> 8) & 0x1F
    rx_byte = (word >> 5) & 3
    rx_num = word & 0x1F
    burst_val, burst_str = _decode_burst_len(word)

    rx_str = f'r{rx_num}'
    if rx_byte > 0:
        rx_str += f'.b{rx_byte}'

    if io == 0:
        ro_sel = (word >> 21) & 7
        ro_num = (word >> 16) & 0x1F
        text = f'{opcode} &{rx_str}, r{rb_num}, {_reg_str(ro_sel, ro_num)}, {burst_str}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Register(rx_num, rx_byte * 8, 8 if rx_byte else 32),
                                     Register(rb_num, 0, 32),
                                     _decode_reg(ro_sel, ro_num),
                                     Immediate(burst_val)],
                           source_line=0, source_text=text)
    else:
        imm_off = (word >> 16) & 0xFF
        text = f'{opcode} &{rx_str}, r{rb_num}, {imm_off}, {burst_str}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Register(rx_num, rx_byte * 8, 8 if rx_byte else 32),
                                     Register(rb_num, 0, 32),
                                     Immediate(imm_off),
                                     Immediate(burst_val)],
                           source_line=0, source_text=text)


def _decode_format6cd(word, addr, symbols):
    """Format 6c/6d: LBCO/SBCO."""
    load_store = (word >> 28) & 1
    opcode = 'LBCO' if load_store else 'SBCO'
    io = (word >> 24) & 1
    cb_num = (word >> 8) & 0x1F
    rx_byte = (word >> 5) & 3
    rx_num = word & 0x1F
    burst_val, burst_str = _decode_burst_len(word)

    rx_str = f'r{rx_num}'
    if rx_byte > 0:
        rx_str += f'.b{rx_byte}'

    if io == 0:
        ro_sel = (word >> 21) & 7
        ro_num = (word >> 16) & 0x1F
        text = f'{opcode} &{rx_str}, c{cb_num}, {_reg_str(ro_sel, ro_num)}, {burst_str}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Register(rx_num, rx_byte * 8, 8 if rx_byte else 32),
                                     Immediate(cb_num),
                                     _decode_reg(ro_sel, ro_num),
                                     Immediate(burst_val)],
                           source_line=0, source_text=text)
    else:
        imm_off = (word >> 16) & 0xFF
        text = f'{opcode} &{rx_str}, c{cb_num}, {imm_off}, {burst_str}'
        return Instruction(address=addr, opcode=opcode,
                           operands=[Register(rx_num, rx_byte * 8, 8 if rx_byte else 32),
                                     Immediate(cb_num),
                                     Immediate(imm_off),
                                     Immediate(burst_val)],
                           source_line=0, source_text=text)


def disassemble(text_words, symbols=None):
    """Disassemble a list of 32-bit instruction words into Instruction objects."""
    symbols = symbols or {}
    instructions = []
    for i, word in enumerate(text_words):
        instr = disassemble_word(word, addr=i, symbols=symbols)
        instructions.append(instr)
    return instructions
