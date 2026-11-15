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
from .feuread import FeuError


@dataclass(frozen=True)
class Pool:
    """Where the string constants sit in the byte code, and the strings (index 0, the empty name, left out)."""

    version: tuple[int, int]  # major, minor
    start: int  # the string count's first byte
    end: int  # the first byte after the last string
    strings: tuple[str, ...]  # constant 1, 2, ... (``strings[i - 1]``)


def read_pool(abc: bytes) -> Pool:
    if len(abc) < 4:
        raise FeuError("byte code too short")
    minor, major = struct.unpack_from("<HH", abc)
    if major != 46:
        raise FeuError(f"not ActionScript 3 byte code (version {major}.{minor})")
    pos = 4
    for width in (None, None, 8):  # ints, uints (variable length), doubles (8 bytes each)
        count, pos = leb128.decode(abc, pos, 5)
        for _ in range(max(count - 1, 0)):
            if width:
                pos += width
            else:
                pos = leb128.decode(abc, pos, 5)[1]
    start = pos
    count, pos = leb128.decode(abc, pos, 5)
    strings = []
    for _ in range(max(count - 1, 0)):
        length, pos = leb128.decode(abc, pos, 5)
        strings.append(abc[pos : pos + length].decode("utf-8", "surrogateescape"))
        pos += length
    return Pool((major, minor), start, pos, tuple(strings))


def strings(abc: bytes) -> tuple[str, ...]:
    return read_pool(abc).strings


def replace_strings(abc: bytes, changes: dict[int, str]) -> bytes:
    """The byte code with constants changed: ``changes`` maps a constant's number (1-based) to its new text."""
    if not changes:
        return abc
    pool = read_pool(abc)
    values = list(pool.strings)
    for index, text in changes.items():
        if not 1 <= index <= len(values):
            raise FeuError(f"no string constant {index} (the pool has {len(values)})")
        values[index - 1] = text
    body = bytearray(leb128.encode(len(values) + 1))
    for text in values:
        data = text.encode("utf-8", "surrogateescape")
        body += leb128.encode(len(data)) + data
    return abc[: pool.start] + bytes(body) + abc[pool.end :]
