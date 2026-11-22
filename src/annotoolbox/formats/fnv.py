"""The Fowler-Noll-Vo hashes the games name things by: Wwise ids (FNV-1, 32 bit), 117's joint names (FNV-1a, 32 bit),
the shader cache's file names (FNV-1a, 64 bit) and its shaders' hashes (FNV-1a, 64 bit, signed bytes). ``fnv`` is the
one function; the callers choose the width and the form.
"""

from __future__ import annotations

_PARAMETERS = {32: (0x811C9DC5, 0x01000193), 64: (0xCBF29CE484222325, 0x100000001B3)}  # offset basis, prime


def fnv(data: bytes, bit_count: int = 32, is_xor_first: bool = True, is_signed: bool = False) -> int:
    """The FNV hash of ``data``: 32 or 64 bits, FNV-1a (``is_xor_first``: the byte is xored in before the multiply) or
    FNV-1 (after it); ``is_signed``: each byte taken as a signed char, a byte from 0x80 up xored in sign-extended (the
    shader cache's ``ByteCodeHash``, shader-compile.md section 5)."""
    hash_value, prime = _PARAMETERS[bit_count]
    mask = (1 << bit_count) - 1
    high = mask ^ 0xFF  # the bits a sign-extended byte sets above its own
    for byte in data:
        if is_signed and byte & 0x80:
            byte |= high
        hash_value = ((hash_value ^ byte) * prime) & mask if is_xor_first else ((hash_value * prime) & mask) ^ byte
    return hash_value
