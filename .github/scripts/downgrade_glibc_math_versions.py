#!/usr/bin/env python3
"""Retarget selected libm symbol-version requirements to their glibc 2.28 ABI."""

import struct
import sys
from pathlib import Path


ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
SYMBOL = struct.Struct("<IBBHQQ")
VERNEED = struct.Struct("<HHIII")
VERNAUX = struct.Struct("<IHHII")
GLIBC_229 = "GLIBC_2.29"
GLIBC_225 = "GLIBC_2.2.5"
LIBM = "libm.so.6"
COMPATIBLE_MATH_SYMBOLS = {"exp", "exp2", "log", "log2", "pow"}


def fail(path: Path, message: str) -> None:
    raise ValueError(f"{path}: {message}")


def elf_string(table: bytes, offset: int) -> str:
    if offset >= len(table):
        raise ValueError(f"string offset {offset} is outside its table")
    end = table.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"unterminated string at offset {offset}")
    return table[offset:end].decode("ascii")


def elf_hash(name: str) -> int:
    value = 0
    for char in name.encode("ascii"):
        value = (value << 4) + char
        high = value & 0xF0000000
        if high:
            value ^= high >> 24
            value &= ~high
    return value


def patch_file(path: Path) -> None:
    data = bytearray(path.read_bytes())
    if len(data) < ELF_HEADER.size:
        fail(path, "file is too small to be an ELF")

    header = ELF_HEADER.unpack_from(data)
    ident = header[0]
    if ident[:4] != b"\x7fELF" or ident[4:6] != b"\x02\x01":
        fail(path, "expected a 64-bit little-endian ELF")
    if header[2] != 62:
        fail(path, "expected an x86_64 ELF")

    section_offset, section_entry_size, section_count, string_index = (
        header[6],
        header[11],
        header[12],
        header[13],
    )
    if section_entry_size < SECTION_HEADER.size or string_index >= section_count:
        fail(path, "invalid ELF section table")
    if section_offset + section_entry_size * section_count > len(data):
        fail(path, "ELF section table exceeds file size")

    sections = [
        SECTION_HEADER.unpack_from(data, section_offset + i * section_entry_size)
        for i in range(section_count)
    ]
    shstr = sections[string_index]
    section_names = bytes(data[shstr[4] : shstr[4] + shstr[5]])
    by_name = {
        elf_string(section_names, section[0]): section
        for section in sections
        if section[0] < len(section_names)
    }
    required_sections = (".dynstr", ".dynsym", ".gnu.version", ".gnu.version_r")
    missing = [name for name in required_sections if name not in by_name]
    if missing:
        fail(path, f"missing ELF sections: {', '.join(missing)}")

    dynstr_section = by_name[".dynstr"]
    dynstr = bytes(data[dynstr_section[4] : dynstr_section[4] + dynstr_section[5]])
    dynsym_section = by_name[".dynsym"]
    versym_section = by_name[".gnu.version"]
    verneed_section = by_name[".gnu.version_r"]
    dynsym_entry_size = dynsym_section[9] or SYMBOL.size
    if dynsym_entry_size < SYMBOL.size or dynsym_section[5] % dynsym_entry_size:
        fail(path, "invalid dynamic symbol table")
    symbol_count = dynsym_section[5] // dynsym_entry_size
    if versym_section[5] < symbol_count * 2:
        fail(path, "dynamic symbol version table is truncated")

    symbols_by_version = {}
    for index in range(symbol_count):
        symbol = SYMBOL.unpack_from(data, dynsym_section[4] + index * dynsym_entry_size)
        name_offset, _, _, section_index, _, _ = symbol
        version_index = struct.unpack_from("<H", data, versym_section[4] + index * 2)[0] & 0x7FFF
        if section_index == 0 and name_offset:
            name = elf_string(dynstr, name_offset)
            symbols_by_version.setdefault(version_index, set()).add(name)

    old_name_offset = dynstr.find(GLIBC_225.encode("ascii") + b"\0")
    if old_name_offset < 0:
        fail(path, f"{GLIBC_225} is absent from .dynstr")

    verneed_data = verneed_section[4]
    verneed_end = verneed_data + verneed_section[5]
    cursor = verneed_data
    patches = []
    while cursor < verneed_end:
        if cursor + VERNEED.size > verneed_end:
            fail(path, "truncated version-need record")
        _, auxiliary_count, library_offset, auxiliary_offset, next_need = VERNEED.unpack_from(data, cursor)
        library = elf_string(dynstr, library_offset)
        auxiliary = cursor + auxiliary_offset
        for _ in range(auxiliary_count):
            if auxiliary + VERNAUX.size > verneed_end:
                fail(path, "truncated version-need auxiliary record")
            _, _, other, name_offset, next_auxiliary = VERNAUX.unpack_from(data, auxiliary)
            version_name = elf_string(dynstr, name_offset)
            if version_name == GLIBC_229:
                index = other & 0x7FFF
                symbols = symbols_by_version.get(index, set())
                if library != LIBM:
                    fail(path, f"{GLIBC_229} requirement belongs to unexpected library {library}")
                unexpected = symbols - COMPATIBLE_MATH_SYMBOLS
                if unexpected or not symbols:
                    detail = ", ".join(sorted(unexpected or symbols)) or "<no symbols>"
                    fail(path, f"cannot safely retarget {GLIBC_229} requirement for: {detail}")
                patches.append((auxiliary, index, symbols))
            if not next_auxiliary:
                break
            auxiliary += next_auxiliary
        if not next_need:
            break
        cursor += next_need

    if not patches:
        print(f"{path}: already has no {GLIBC_229} version requirements")
        return

    for auxiliary, index, symbols in patches:
        struct.pack_into("<I", data, auxiliary, elf_hash(GLIBC_225))
        struct.pack_into("<I", data, auxiliary + 8, old_name_offset)
        print(
            f"{path}: retargeted {GLIBC_229} -> {GLIBC_225} for "
            f"{', '.join(sorted(symbols))} (version index {index})"
        )
    path.write_bytes(data)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"Usage: {Path(sys.argv[0]).name} ELF [ELF ...]", file=sys.stderr)
        return 2
    try:
        for name in sys.argv[1:]:
            patch_file(Path(name))
    except (OSError, ValueError, struct.error) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
