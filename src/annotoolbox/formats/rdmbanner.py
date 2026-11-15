"""The leaf cards ("banners") of a tree RDM: the layout of their vertex block and a reader for the island editor.

A banner mesh has one quad per card with all four corners at the card's centre; the corner offset sits in the
normal and the shader spreads it along the camera's axes. ``read_banner`` is the entry (the vertex layout is
``rdmwrite``'s; docs/engine-rendering.md).
"""

from __future__ import annotations

import struct

STRIDE = 24  # P4h (8) + N4b + G4b + B4b (12) + T2h (4)
BANNER_UNIT = 4.0  # metres per unit of the normal's corner offset


def read_banner(rdm: bytes) -> dict:
    """The cards of a banner RDM, for the island editor (a glTF of the mesh carries unit normals, which
    loses each card's size): per vertex its card's centre, its corner offset in world units, the crown's outward
    direction and the uv; and the triangles. Its vertex block is the one of ``STRIDE`` bytes followed by the
    16-bit index block (``rdmwrite`` layout)."""
    at = 0
    while at + 8 <= len(rdm):
        count, size = struct.unpack_from("<II", rdm, at)
        end = at + 8 + count * size
        if size == STRIDE and count and end + 8 <= len(rdm):
            n_idx, idx_size = struct.unpack_from("<II", rdm, end)
            if idx_size == 2 and n_idx % 3 == 0 and end + 8 + 2 * n_idx <= len(rdm):
                break
        at += 1
    else:
        raise ValueError("no banner vertex block")
    out = {"positions": [], "corners": [], "radial": [], "uvs": []}
    for k in range(count):
        v = rdm[at + 8 + k * STRIDE : at + 8 + (k + 1) * STRIDE]
        out["positions"] += struct.unpack_from("<3e", v)
        out["corners"] += [round((b / 127.5 - 1) * BANNER_UNIT, 4) for b in v[8:10]]
        out["radial"] += [round(b / 127.5 - 1, 4) for b in v[12:15]]
        out["uvs"] += struct.unpack_from("<2e", v, 20)
    out["indices"] = list(struct.unpack_from(f"<{n_idx}H", rdm, end + 8))
    return out
