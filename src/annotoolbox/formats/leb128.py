"""LEB128 numbers: an unsigned whole number in groups of seven bits, low group first, the top bit set on every
byte but the last. The Phoenix UI files' counts, FileDB's sizes and the Flash byte code's ``u30`` are all this form.
``encode`` and ``decode`` are the two functions.
"""

from __future__ import annotations


def encode(value: int) -> bytes:
    """``value`` (0 or more) as LEB128 bytes."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def decode(data: bytes, pos: int = 0, most: int = 0) -> tuple[int, int]:
    """``(value, position after it)`` of the number at ``pos``; ``most`` ends it after that many bytes whatever the
    last one says (the byte code's ``u30`` is five at most)."""
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80 or shift == 7 * most:
            return value, pos
