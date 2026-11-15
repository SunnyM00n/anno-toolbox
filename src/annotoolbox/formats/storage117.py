"""117's stores of goods in a savegame: the sorted GUID -> amount maps of ``StrgLrg`` and ``StrgSmll``, read only.

``decode(name, data)`` is the entry; the layout and its evidence are ``docs/formats/save-format.md``, "Storages".
"""

from __future__ import annotations

import struct

LARGE, SMALL = "StrgLrg", "StrgSmll"
MONEY_GOOD = 1010017  # the money's key in a participant's meta storage


class StorageError(ValueError):
    pass


def decode_large(data: bytes) -> list[tuple[int, int]]:
    """N u32 keys (the first as is, each next the previous plus the stored word plus 1), then N u32 amounts."""
    if len(data) % 8:
        raise StorageError(f"a large storage of {len(data)} bytes is not N pairs of words")
    n = len(data) // 8
    words = struct.unpack(f"<{2 * n}I", data)
    keys, key = [], 0
    for i in range(n):
        key = words[0] if i == 0 else key + words[i] + 1
        keys.append(key)
    return list(zip(keys, words[n:], strict=True))


def decode_small(data: bytes) -> list[tuple[int, int]]:
    """The u32 first key, N-1 gap bytes, N amount bytes."""
    if len(data) < 5 or len(data) % 2 == 0:
        raise StorageError(f"a small storage of {len(data)} bytes is not 4 + 2 N - 1")
    n = (len(data) - 3) // 2
    (key,) = struct.unpack_from("<I", data)
    keys = [key]
    for gap in data[4 : 4 + n - 1]:
        key += gap + 1
        keys.append(key)
    return list(zip(keys, data[4 + n - 1 :], strict=True))


def decode(name: str, data: bytes) -> list[tuple[int, int]]:
    """The (good GUID, amount) pairs of a ``StrgLrg`` or ``StrgSmll`` attribute, ascending by GUID."""
    if name == LARGE:
        return decode_large(data)
    if name == SMALL:
        return decode_small(data)
    raise StorageError(f"{name} is no storage")
