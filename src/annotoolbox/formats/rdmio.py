"""The RDM header constants and where a mesh's vertex and index buffers lie, read at fixed offsets.

For the code that reads a mesh's buffers directly (``models/modelread.surface``) or patches a little-endian file in
place, and for the test helpers that build files; everything else reads the whole file with ``rdmread`` (rdm.md
"Container").
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

RDM_MAGIC = b"RDM\x01\x14\x00\x00\x00"
HEADER1_OFFSET = 0x1C
META_PTR_OFFSET = HEADER1_OFFSET + 4  # second pointer in header1
VERTEX_POINTER_INDEX = 3  # the meta block's fourth pointer: the vertex buffer
INDEX_POINTER_INDEX = 4  # ... its fifth: the index buffer


def u32(b: bytes | bytearray, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


@dataclass(frozen=True)
class MeshBlocks:
    """Where the first mesh's vertex and index buffers lie, and how they are laid out."""

    vertex_ptr: int
    count: int  # vertices
    stride: int
    index_ptr: int
    n_idx: int  # indices
    index_size: int  # bytes: 2 or 4

    @property
    def index_format(self) -> str:
        return "<%d%s" % (self.n_idx, "H" if self.index_size == 2 else "I")


def mesh_blocks(data: bytes | bytearray) -> MeshBlocks | None:
    """The buffers of the first mesh, or None when the file holds no readable mesh."""
    try:
        return _blocks_at(data, u32(data, META_PTR_OFFSET))
    except struct.error:
        return None


def all_mesh_blocks(data: bytes | bytearray) -> list[MeshBlocks]:
    """The buffers of every mesh record of the file that has them (a file holds up to 40 meshes)."""
    try:
        meta = u32(data, META_PTR_OFFSET)
        count, size = u32(data, meta - 8), u32(data, meta - 4)
        found = [_blocks_at(data, meta + k * size) for k in range(count)]
    except struct.error:
        return []
    return [b for b in found if b is not None]


def _blocks_at(data: bytes | bytearray, meta: int) -> MeshBlocks | None:
    try:
        vptr = u32(data, meta + 4 * VERTEX_POINTER_INDEX)
        iptr = u32(data, meta + 4 * INDEX_POINTER_INDEX)
        count, stride = u32(data, vptr - 8), u32(data, vptr - 4)
        n_idx, isize = u32(data, iptr - 8), u32(data, iptr - 4)
    except struct.error:
        return None
    if stride < 8 or isize not in (2, 4) or count == 0 or n_idx < 3:
        return None
    if vptr + count * stride > len(data) or iptr + n_idx * isize > len(data):
        return None
    return MeshBlocks(vptr, count, stride, iptr, n_idx, isize)
