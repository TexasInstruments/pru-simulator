"""ELF loader for TI PRU .out files (ELF32-LE, machine=0x0090)."""

import struct
from dataclasses import dataclass, field

EM_TI_PRU = 0x0090
PF_X = 0x1  # Execute
PF_W = 0x2  # Write
PF_R = 0x4  # Read

SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8
SHF_ALLOC = 0x2


@dataclass
class ElfImage:
    """Parsed ELF image ready for loading into the simulator."""
    text_words: list  # list[int] — 32-bit instruction words
    data_bytes: bytes = b''
    data_addr: int = 0
    symbols: dict = field(default_factory=dict)  # addr → name (for labels)
    entry_point: int = 0
    # Every initialized, allocated non-text section, as (addr, bytes) in
    # address order — .data, .rodata, .cinit and anything else the linker
    # command file places. `data_bytes`/`data_addr` remain the .data-only view
    # so existing callers are unaffected.
    #
    # This exists because loading only `.data` silently drops const lookup
    # tables. Firmware that indexes one then reads whatever DMEM happened to
    # contain, which surfaces as a decode bug in the code under test rather
    # than as the loader gap it is, which makes it expensive to chase. It goes
    # unnoticed for as long as every firmware under test happens to have no
    # const tables.
    data_sections: list = field(default_factory=list)


class ElfParseError(Exception):
    pass


def load_elf(data: bytes) -> ElfImage:
    """Parse a PRU ELF32 .out file and return an ElfImage."""
    if len(data) < 52:
        raise ElfParseError("File too small to be a valid ELF")

    # ELF header
    magic = data[0:4]
    if magic != b'\x7fELF':
        raise ElfParseError("Not an ELF file (bad magic)")

    ei_class = data[4]
    ei_data = data[5]
    if ei_class != 1:
        raise ElfParseError(f"Expected ELF32 (class=1), got class={ei_class}")
    if ei_data != 1:
        raise ElfParseError(f"Expected little-endian (data=1), got data={ei_data}")

    e_type, e_machine = struct.unpack_from('<HH', data, 16)
    if e_machine != EM_TI_PRU:
        raise ElfParseError(
            f"Not a PRU ELF (machine=0x{e_machine:04X}, expected 0x{EM_TI_PRU:04X})")

    e_entry = struct.unpack_from('<I', data, 24)[0]
    e_phoff = struct.unpack_from('<I', data, 28)[0]
    e_shoff = struct.unpack_from('<I', data, 32)[0]
    e_phentsize, e_phnum = struct.unpack_from('<HH', data, 42)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', data, 46)

    # --- Read section headers ---
    sections = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size = struct.unpack_from(
            '<IIIIII', data, off)
        sh_link = struct.unpack_from('<I', data, off + 24)[0]
        sections.append({
            'name_off': sh_name,
            'type': sh_type,
            'flags': sh_flags,
            'addr': sh_addr,
            'offset': sh_offset,
            'size': sh_size,
            'link': sh_link,
            'index': i,
        })

    # Section name string table
    shstrtab = b''
    if e_shstrndx < len(sections):
        s = sections[e_shstrndx]
        shstrtab = data[s['offset']:s['offset'] + s['size']]

    def get_section_name(name_off):
        if not shstrtab or name_off >= len(shstrtab):
            return ''
        end = shstrtab.index(0, name_off)
        return shstrtab[name_off:end].decode('ascii', errors='replace')

    # Assign names
    for s in sections:
        s['name'] = get_section_name(s['name_off'])

    # --- Extract .text sections (code) ---
    # Combine all .text* sections in address order
    text_sections = []
    for s in sections:
        if s['name'].startswith('.text') and s['type'] != SHT_NOBITS and s['size'] > 0:
            text_sections.append(s)

    text_sections.sort(key=lambda s: s['addr'])

    # Build instruction word array
    text_words = []
    for s in text_sections:
        sec_data = data[s['offset']:s['offset'] + s['size']]
        n_words = len(sec_data) // 4
        for i in range(n_words):
            word = struct.unpack_from('<I', sec_data, i * 4)[0]
            text_words.append(word)

    # --- Extract .data section (initialized data) ---
    data_bytes = b''
    data_addr = 0
    for s in sections:
        if s['name'] == '.data' and s['type'] != SHT_NOBITS and s['size'] > 0:
            data_bytes = data[s['offset']:s['offset'] + s['size']]
            data_addr = s['addr']
            break

    # --- Extract every initialized, allocated non-text section ---
    # .rodata holds const lookup tables and .cinit holds the initializers the
    # C runtime would copy. Skipping them leaves those addresses as whatever
    # DMEM already contained.
    data_sections = []
    for s in sections:
        if s['type'] == SHT_NOBITS or s['size'] == 0:
            continue
        if not (s['flags'] & SHF_ALLOC):
            continue
        if s['name'].startswith('.text'):
            continue
        data_sections.append((s['addr'],
                              data[s['offset']:s['offset'] + s['size']]))
    data_sections.sort(key=lambda item: item[0])

    # --- Extract symbols ---
    symbols = {}
    symtab_sec = None
    for s in sections:
        if s['type'] == SHT_SYMTAB:
            symtab_sec = s
            break

    if symtab_sec:
        # Get linked string table
        strtab_sec = sections[symtab_sec['link']] if symtab_sec['link'] < len(sections) else None
        strtab_data = b''
        if strtab_sec:
            strtab_data = data[strtab_sec['offset']:strtab_sec['offset'] + strtab_sec['size']]

        def get_sym_name(name_off):
            if not strtab_data or name_off >= len(strtab_data):
                return ''
            end = strtab_data.index(0, name_off)
            return strtab_data[name_off:end].decode('ascii', errors='replace')

        # Parse symbol entries (ELF32_Sym = 16 bytes)
        sym_count = symtab_sec['size'] // 16
        for i in range(sym_count):
            off = symtab_sec['offset'] + i * 16
            st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from(
                '<IIIBBH', data, off)

            st_type = st_info & 0xF
            # Only include FUNC and NOTYPE symbols with nonzero value in .text sections
            if st_type in (0, 2) and st_value > 0:  # NOTYPE or FUNC
                name = get_sym_name(st_name)
                # Filter out internal compiler symbols and section names
                if name and not name.startswith('.') and not name.startswith('$'):
                    # Convert byte address to word address for .text symbols
                    if st_shndx < len(sections):
                        sec = sections[st_shndx]
                        if sec['name'].startswith('.text'):
                            word_addr = st_value // 4
                            symbols[word_addr] = name

    return ElfImage(
        text_words=text_words,
        data_bytes=data_bytes,
        data_addr=data_addr,
        symbols=symbols,
        entry_point=e_entry,
        data_sections=data_sections,
    )
