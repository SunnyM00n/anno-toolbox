"""What the game's draws on QRhi share to keep the number of QRhi objects down: one uniform buffer for all the
constant buffers of a model, and the stand-ins (a white map, a scratch buffer, a sampler) made once per device.

``CUniformPool(rhi, keep, capacity_bytes).take(size_bytes)`` carves a constant buffer's range out of the model's one
dynamic buffer; ``shared(rhi, key, make)`` gives the resource ``make()`` built for ``key`` on that device (counted
per user), and ``give_back(token)`` lets one user go: the last destroys it. ``render.gamedraw.CGameModel`` and the
passes' ``render.gamebind.CResources`` are the users (docs/rendering/viewer-performance.md section 4, "Built: item 8").
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QRhiBuffer

TABLE = "_model_shared"  # the device's table of shared resources: key -> [resource, users]


def shared(rhi, key: tuple, make: Callable) -> tuple:
    """(resource, token) of ``key`` on ``rhi``: ``make()``'s, made at the first call and kept for every later one. A
    device that takes no attributes (a test's stand-in) gets a resource of its own and no token."""
    if not hasattr(rhi, "__dict__"):
        return make(), None
    table = rhi.__dict__.setdefault(TABLE, {})
    entry = table.get(key)
    if entry is None:
        entry = table[key] = [make(), 0]
    entry[1] += 1
    return entry[0], (table, key)


def give_back(token: tuple | None) -> None:
    """One user of a shared resource is done with it; the last one destroys it."""
    if token is None:
        return
    table, key = token
    entry = table.get(key)
    if entry is not None:
        entry[1] -= 1
        if entry[1] <= 0:
            entry[0].destroy()
            del table[key]


class CUniformPool:
    """The constant buffers of one model in one dynamic uniform buffer, each at an offset the backend allows
    (``QRhi.ubufAlignment``: 256 on Direct3D 12). The buffer is made with room for ``capacity_bytes``, and another
    chunk is made if a model asks for more. Its chunks are added to ``keep`` (the model's resources, destroyed
    with it). QRhi keeps a dynamic buffer once per frame in flight and applies every update to each of them, so a
    range sent once is right in every frame (``render.gamedraw.CGameModel._send``)."""

    def __init__(self, rhi, keep: list, capacity_bytes: int) -> None:
        self.m_rhi, self.m_keep, self.m_capacity = rhi, keep, capacity_bytes
        self.m_buffer = None
        self.m_used = self.m_room = 0

    def take(self, size_bytes: int) -> tuple[QRhiBuffer, int, int]:
        """(buffer, offset, size) of a range for a constant buffer of ``size_bytes``: whole alignment units, as a
        buffer of its own would have been."""
        size = self.m_rhi.ubufAligned(max(16, size_bytes))
        if self.m_buffer is None or self.m_used + size > self.m_room:
            self.m_room = max(size, self.m_capacity)
            self.m_buffer = self.m_rhi.newBuffer(
                QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, self.m_room
            )
            self.m_buffer.create()
            self.m_keep.append(self.m_buffer)
            self.m_used = 0
        offset = self.m_used
        self.m_used += size
        return self.m_buffer, offset, size
