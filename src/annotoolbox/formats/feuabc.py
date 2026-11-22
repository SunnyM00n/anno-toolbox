"""The ActionScript 3 byte code of a UI movie (its DoABC tag): the constant pool read, its strings changed.

``strings`` lists the string constants (names, the callbacks a movie calls on the game, texts written in the
code); ``replace_strings`` changes some and gives the byte code back, everything after the strings as it was,
since the code refers to a string by its number, never by its place. Decompiling and changing the code itself is a
Flash tool's work (docs/formats/anno2205-ui-format.md, "ActionScript").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from . import leb128
from .feuread import CFeuError


@dataclass(frozen=True)
class SPool:
    """Where the string constants sit in the byte code, and the strings (index 0, the empty name, left out)."""

    version: tuple[int, int]  # major, minor
    start: int  # the string count's first byte
    end: int  # the first byte after the last string
    strings: tuple[str, ...]  # constant 1, 2, ... (``strings[i - 1]``)


def read_pool(byte_code: bytes) -> SPool:
    if len(byte_code) < 4:
        raise CFeuError("byte code too short")
    minor, major = struct.unpack_from("<HH", byte_code)
    if major != 46:
        raise CFeuError(f"not ActionScript 3 byte code (version {major}.{minor})")
    offset = 4
    for entry_bytes in (None, None, 8):  # ints, uints (variable length), doubles (8 bytes each)
        count, offset = leb128.decode(byte_code, offset, 5)
        for _ in range(max(count - 1, 0)):
            if entry_bytes:
                offset += entry_bytes
            else:
                offset = leb128.decode(byte_code, offset, 5)[1]
    start = offset
    count, offset = leb128.decode(byte_code, offset, 5)
    strings = []
    for _ in range(max(count - 1, 0)):
        length_bytes, offset = leb128.decode(byte_code, offset, 5)
        strings.append(byte_code[offset : offset + length_bytes].decode("utf-8", "surrogateescape"))
        offset += length_bytes
    return SPool((major, minor), start, offset, tuple(strings))


def strings(byte_code: bytes) -> tuple[str, ...]:
    return read_pool(byte_code).strings


def replace_strings(byte_code: bytes, changes: dict[int, str]) -> bytes:
    """The byte code with constants changed: ``changes`` maps a constant's number (1-based) to its new text."""
    if not changes:
        return byte_code
    pool = read_pool(byte_code)
    values = list(pool.strings)
    for index, text in changes.items():
        if not 1 <= index <= len(values):
            raise CFeuError(f"no string constant {index} (the pool has {len(values)})")
        values[index - 1] = text
    body = bytearray(leb128.encode(len(values) + 1))
    for text in values:
        data = text.encode("utf-8", "surrogateescape")
        body += leb128.encode(len(data)) + data
    return byte_code[: pool.start] + bytes(body) + byte_code[pool.end :]
