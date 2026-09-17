"""Regression tests for core/elf_loader.py.

RND-18 / A1: load_elf() used to extract only the .data section and silently
drop every other initialized, allocated section — most importantly .rodata,
which holds const lookup tables. Firmware that indexed one read whatever DMEM
already contained instead of the table, which surfaces as a wrong decode in
the code under test rather than as the loader gap it actually is. Nothing
gone unnoticed for as long as every firmware under test happened to have no
lookup tables to expose it.

These tests build a small synthetic PRU ELF32 object by hand (section headers
only — load_elf never reads program headers) so the defect and its fix can be
demonstrated without depending on a real toolchain or a specific firmware
image.
"""

import struct

import pytest

from core.elf_loader import load_elf, EM_TI_PRU, SHT_NOBITS

SHT_PROGBITS = 1
SHT_STRTAB = 3
# Defined locally rather than imported from core.elf_loader: that constant is
# part of the fix itself, and importing it would turn "fix reverted" into an
# ImportError instead of the assertion failure the revert should produce.
SHF_ALLOC = 0x2


def _build_pru_elf(sections):
    """Build a minimal, valid ELF32-LE PRU object file.

    *sections* is a list of (name, sh_type, sh_flags, addr, data) tuples for
    the sections beyond the mandatory NULL section and the shstrtab this
    helper adds itself. Program headers are omitted entirely: load_elf() only
    ever reads the section header table.
    """
    HEADER_SIZE = 52
    SHDR_SIZE = 40

    # Build .shstrtab up front so every section can record its name offset.
    shstrtab = b"\x00"
    name_offsets = {}
    for name, *_rest in sections:
        name_offsets[name] = len(shstrtab)
        shstrtab += name.encode("ascii") + b"\x00"
    shstrtab_name_off = len(shstrtab)
    shstrtab += b".shstrtab\x00"

    all_sections = [("", 0, 0, 0, 0, b"")]  # NULL section
    for name, sh_type, sh_flags, addr, data in sections:
        all_sections.append((name, sh_type, sh_flags, addr, name_offsets[name], data))
    all_sections.append((".shstrtab", SHT_STRTAB, 0, 0, shstrtab_name_off, shstrtab))
    shstrndx = len(all_sections) - 1

    # Lay out section payloads immediately after the ELF header.
    body = b""
    offsets = []
    cursor = HEADER_SIZE
    for _name, sh_type, _flags, _addr, _name_off, data in all_sections:
        if sh_type == SHT_NOBITS:
            offsets.append(cursor)
            continue
        offsets.append(cursor)
        body += data
        cursor += len(data)

    shoff = HEADER_SIZE + len(body)

    shdrs = b""
    for (name, sh_type, sh_flags, addr, name_off, data), off in zip(all_sections, offsets):
        size = len(data)
        shdrs += struct.pack(
            "<IIIIIIIIII",
            name_off,      # sh_name
            sh_type,       # sh_type
            sh_flags,      # sh_flags
            addr,          # sh_addr
            off,           # sh_offset
            size,          # sh_size
            0,             # sh_link
            0,             # sh_info
            1,             # sh_addralign
            0,             # sh_entsize
        )

    e_ident = b"\x7fELF" + bytes([1, 1, 1]) + b"\x00" * 9
    header = e_ident
    header += struct.pack("<HH", 2, EM_TI_PRU)          # e_type, e_machine
    header += struct.pack("<I", 1)                       # e_version
    header += struct.pack("<I", 0)                       # e_entry
    header += struct.pack("<I", 0)                       # e_phoff (unused)
    header += struct.pack("<I", shoff)                    # e_shoff
    header += struct.pack("<I", 0)                       # e_flags
    header += struct.pack("<H", HEADER_SIZE)              # e_ehsize
    header += struct.pack("<HH", 0, 0)                    # e_phentsize, e_phnum
    header += struct.pack("<HHH", SHDR_SIZE, len(all_sections), shstrndx)
    assert len(header) == HEADER_SIZE

    return header + body + shdrs


RODATA_TABLE = bytes(range(16)) * 4  # 64 bytes, distinctive non-zero pattern
DATA_BYTES = b"\xAA\xBB\xCC\xDD"


def _elf_with_rodata():
    text = struct.pack("<I", 0x00000000)  # single NOP-ish word, content irrelevant
    return _build_pru_elf([
        (".text", SHT_PROGBITS, SHF_ALLOC | 0x4, 0x0000, text),
        (".data", SHT_PROGBITS, SHF_ALLOC | 0x2, 0x0100, DATA_BYTES),
        (".rodata", SHT_PROGBITS, SHF_ALLOC, 0x0200, RODATA_TABLE),
    ])


def test_rodata_section_is_loaded():
    """The defect: load_elf() must not silently drop .rodata.

    Reverting the fix (restricting the loader to `.data` only) makes this
    fail with `AssertionError: assert [] == [...]` because data_sections
    comes back empty for the .rodata entry — see the commit message for the
    exact repro command and captured failure.
    """
    image = load_elf(_elf_with_rodata())
    sections_by_addr = dict(image.data_sections)
    assert 0x0200 in sections_by_addr, (
        f"expected a .rodata section loaded at 0x200, got addrs "
        f"{[hex(a) for a in sections_by_addr]}"
    )
    assert sections_by_addr[0x0200] == RODATA_TABLE


def test_data_section_still_loaded_via_legacy_fields():
    """The .data-only fields must keep working for existing callers."""
    image = load_elf(_elf_with_rodata())
    assert image.data_addr == 0x0100
    assert image.data_bytes == DATA_BYTES


def test_data_sections_covers_every_allocated_initialized_section():
    """General claim: ALL initialized+allocated non-text sections load, not
    a hardcoded allowlist of section names."""
    text = struct.pack("<I", 0)
    elf = _build_pru_elf([
        (".text", SHT_PROGBITS, SHF_ALLOC | 0x4, 0x0000, text),
        (".data", SHT_PROGBITS, SHF_ALLOC | 0x2, 0x0100, DATA_BYTES),
        (".rodata", SHT_PROGBITS, SHF_ALLOC, 0x0200, RODATA_TABLE),
        (".cinit", SHT_PROGBITS, SHF_ALLOC, 0x0300, b"\x01\x02\x03\x04"),
        # Not allocated: must NOT appear (e.g. debug/comment sections).
        (".comment", SHT_PROGBITS, 0, 0x0000, b"unallocated"),
        # NOBITS (.bss-like): has no file payload and must NOT appear.
        (".bss", SHT_NOBITS, SHF_ALLOC | 0x2, 0x0400, b""),
    ])
    image = load_elf(elf)
    addrs = sorted(a for a, _ in image.data_sections)
    assert addrs == [0x0100, 0x0200, 0x0300]


def test_text_sections_excluded_from_data_sections():
    text = struct.pack("<I", 0)
    elf = _build_pru_elf([
        (".text", SHT_PROGBITS, SHF_ALLOC | 0x4, 0x0000, text),
        (".rodata", SHT_PROGBITS, SHF_ALLOC, 0x0200, RODATA_TABLE),
    ])
    image = load_elf(elf)
    addrs = [a for a, _ in image.data_sections]
    assert 0x0000 not in addrs
