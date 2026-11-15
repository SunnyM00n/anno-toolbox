"""The Fowler-Noll-Vo hashes the games name things by: Wwise ids (FNV-1, 32 bit), 117's joint names (FNV-1a, 32 bit) and
the shader cache's file names (FNV-1a, 64 bit). ``fnv`` is the one function; the callers choose the width and the form.
"""

from __future__ import annotations

_PARAMETERS = {32: (0x811C9DC5, 0x01000193), 64: (0xCBF29CE484222325, 0x100000001B3)}  # offset basis, prime


def fnv(data: bytes, bits: int = 32, xor_first: bool = True) -> int:
    """The FNV hash of ``data``: 32 or 64 bits, FNV-1a (``xor_first``: the byte is xored in before the multiply) or
    FNV-1 (after it)."""
    h, prime = _PARAMETERS[bits]
    mask = (1 << bits) - 1
    for byte in data:
        h = ((h ^ byte) * prime) & mask if xor_first else ((h * prime) & mask) ^ byte
    return h
