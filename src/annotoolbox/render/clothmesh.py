"""A simulated cloth handed to the games' own shaders: the moved positions and normals written into a draw's mesh in
the layout the file stores (floats, halves or packed bytes, each element in its own type), the tangent and
bitangent turned onto the new normal; 2070's cloth layout by its own rule (``cloth2070.reposed``); 117's cloth
shader reads them from a buffer of its own (``sim_vertices``). A mesh's ``version`` tells its device copies to upload.

``follow(item, model)`` is what an own-shader pass calls per cloth group and frame (``viewer.ownpass``);
``reposed(mesh, positions, normals)`` the bytes. docs/engine-cloth.md "The cloth in the view".
"""

from __future__ import annotations

import numpy as np

from . import cloth2070, clothlogo
from .camera import MIRROR
from .clothsim import on_surface, unit_bytes
from .gamedraw import Mesh

POSITION, NORMAL, TANGENT, BITANGENT = 0, 1, 2, 3
FLOAT, HALF, PACKED = 7, 6, 5  # rdm.md's element types
SIM_BUFFER = "cb_cClothSimVertices"  # 117's cloth.fx: the vertices from a buffer (``gameshader`` makes it a cbuffer)


def _read(raw: np.ndarray, kind: int, count: int, at: int) -> np.ndarray | None:
    """An element of every vertex as floats [n, count] (a packed direction as its three components), None for a
    type this does not read."""
    if kind in (FLOAT, HALF):
        size = 4 if kind == FLOAT else 2
        return (
            np.ascontiguousarray(raw[:, at : at + size * count]).view("<f4" if kind == FLOAT else "<f2").astype(float)
        )
    if kind == PACKED:
        return raw[:, at : at + 3].astype(float) / 127.5 - 1.0
    return None


def _write(raw: np.ndarray, kind: int, count: int, at: int, values: np.ndarray) -> None:
    """The first three components of an element written in its own type (a fourth, w or a sign, is kept)."""
    values = np.asarray(values, float)[:, :3]
    if kind in (FLOAT, HALF):
        size = 4 if kind == FLOAT else 2
        typed = np.ascontiguousarray(raw[:, at : at + size * count]).view("<f4" if kind == FLOAT else "<f2").copy()
        typed[:, :3] = values
        raw[:, at : at + size * count] = typed.view(np.uint8)
    elif kind == PACKED:
        raw[:, at : at + 3] = unit_bytes(values)


def reposed(mesh: Mesh, positions: np.ndarray, normals: np.ndarray) -> bytes:
    """The mesh's stored vertices with the cloth's ``positions`` and ``normals`` (one row per stored vertex), the
    tangent frame projected onto the normals; elements it does not know keep their bytes."""
    if mesh.elements == cloth2070.ELEMENTS:
        return cloth2070.reposed(mesh, positions, normals)
    raw = np.frombuffer(mesh.rest or mesh.data, np.uint8).reshape(mesh.count, mesh.stride).copy()
    n = np.asarray(normals, float)
    seen: set[int] = set()
    for usage, kind, count, at in mesh.elements:
        if usage in seen or count < 1:
            continue  # the first element of a usage is the one the shaders read
        seen.add(usage)
        if usage == POSITION:
            _write(raw, kind, count, at, positions)
        elif usage == NORMAL:
            _write(raw, kind, count, at, n)
        elif usage in (TANGENT, BITANGENT):
            t = _read(raw, kind, count, at)
            if t is not None:
                _write(raw, kind, count, at, on_surface(t[:, :3], n))
    return raw.tobytes()


def mesh_of(model) -> Mesh | None:
    """The mesh a pass's model draws (117's ``GameModel``, a forward or 2070 model's ``draw_call``)."""
    mesh = getattr(model, "mesh", None)
    if mesh is None:
        mesh = getattr(getattr(model, "draw_call", None), "mesh", None)
    return mesh if isinstance(mesh, Mesh) else None


def stored(mesh: Mesh, usage: int) -> np.ndarray | None:
    """The first element of a usage of the mesh's vertices as first made, [n, 3] floats (None: it has none)."""
    raw = np.frombuffer(mesh.rest or mesh.data, np.uint8).reshape(mesh.count, mesh.stride)
    found = next((e for e in mesh.elements if e[0] == usage), None)
    got = _read(raw, found[1], found[2], found[3]) if found is not None else None
    return got[:, :3] if got is not None else None


def _packed(v: np.ndarray) -> np.ndarray:
    """Unit vectors as 117's cloth shader unpacks them (``UnpackFloat3FromUint``): x | y << 8 | z << 16."""
    b = unit_bytes(v).astype(np.uint32)
    return b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)


def sim_vertices(mesh: Mesh, positions: np.ndarray, normals: np.ndarray, moved: np.ndarray | None = None) -> bytes:
    """117's ``ClothSimVertex`` per stored vertex (32 bytes: position, packed normal, tangent and bitangent, the
    motion since the last frame as halves), what its ``cloth.fx`` reads instead of a vertex stream; the tangent frame
    is the stored one turned onto the normals."""
    n = len(positions)
    normals = np.asarray(normals, float)
    out = np.zeros((n, 8), np.uint32)
    out[:, 0:3] = np.asarray(positions, "<f4").view(np.uint32)
    out[:, 3] = _packed(normals)
    for k, usage in ((4, TANGENT), (5, BITANGENT)):
        t = stored(mesh, usage)
        out[:, k] = _packed(on_surface(t, normals)) if t is not None else 0
    if moved is not None:
        h = np.asarray(moved, "<f2").view(np.uint16).astype(np.uint32)
        out[:, 6], out[:, 7] = (h[:, 0] << 16) | h[:, 1], h[:, 2] << 16
    return out.astype("<u4").tobytes()


def _nibbles(color) -> int:
    """A colour as 4 bits a channel, red high, stored as the shader's ``FastGammaToLinear`` expects (the square root
    of the linear value)."""
    r, g, b = (min(max(int(round(float(c) ** 0.5 * 15.0)), 0), 15) for c in color)
    return (r << 8) | (g << 4) | b


def cloth_constant(color=(1.0, 1.0, 1.0), dye=(1.0, 1.0, 1.0), second=(1.0, 1.0, 1.0), logo: int = 0) -> list[int]:
    """``cClothConstant`` (``cloth.fx``'s packing): the object colour in x bits 1 to 12 and fully opaque (bits 13 to
    15), the dye in x bits 16 to 27, the second colour in z bits 2 to 13, the logo's atlas index in z bits 14 to 21
    (``logo``, 0 none), no other atlas index (y), no damage, terrain adaption off, the vertex data at 0. The second
    colour multiplies the object colour in every cloth: left at 0 it paints the cloth black (docs/engine-cloth.md
    "The logo atlas")."""
    x = 1 | (_nibbles(color) << 1) | (0x7 << 13) | (_nibbles(dye) << 16)
    return [x, 0, (_nibbles(second) << 2) | ((logo & 0xFF) << 14), 0]


def attach_logo(model, logo) -> int:
    """The atlas index of the player logo in a 117 cloth draw (0: none). The logo's texture joins the draw's maps
    while its resources are not made yet, and its entry goes into ``cTextureAtlas`` (a draw whose shader reads no
    atlas, or whose resources exist without the logo, gets none)."""
    table = getattr(model, "table", None)
    if not logo or table is None or clothlogo.ATLAS_BUFFER not in getattr(model.program, "cbuffers", {}):
        return 0
    slot = table.slots.get(clothlogo.LOGO_MAP)
    if slot is None and not model.keep:
        slot = table.add(clothlogo.LOGO_MAP, logo)
    if not slot:
        return 0
    model.values[f"{clothlogo.ATLAS}[{clothlogo.LOGO_INDEX}]"] = clothlogo.atlas_word(slot, logo)
    return clothlogo.LOGO_INDEX


def _feed_buffer(item: dict, model, mesh: Mesh, place, dye, logo) -> None:
    """117's cloth draws: the simulated vertices into ``cClothSimVertices`` (again when the mesh moved), the place
    into ``cbCloth``."""
    if model.raw.get(SIM_BUFFER) is None or getattr(model, "fed", None) != mesh.version:
        posed = item.get("posed") if mesh.version else None
        if posed is not None and len(posed) == mesh.count:
            pos, normals = posed[:, :3], posed[:, 3:6]
        else:
            pos, normals = stored(mesh, POSITION), stored(mesh, NORMAL)
        if pos is None or normals is None:
            return
        model.raw[SIM_BUFFER], model.fed = sim_vertices(mesh, pos, normals), mesh.version
    if place is not None:
        world = (MIRROR @ np.asarray(place, float)).T  # row vectors, as MUL(v, M) takes them
        model.values.update({"cCurrClothWorldMtx[0]": world, "cLastClothWorldMtx[0]": world,
                             "cClothConstant[0]": cloth_constant(dye=dye, logo=attach_logo(model, logo))})  # fmt: skip


def follow(item: dict, model, place=None, dye=(1.0, 1.0, 1.0), logo=None) -> None:
    """The model's mesh brought to the cloth group's vertices of this frame (``viewer.clothplay`` writes them into
    ``item``: ``posed`` and its ``cloth_version``; no pose is the rest); a 117 cloth draw also gets its simulated
    vertices buffer and its place (``place``: the part's this frame), dye colour (``dye``: linear rgb) and player
    logo (``logo``: its texture, ``render.clothlogo``)."""
    mesh = mesh_of(model)
    if mesh is None:
        return
    version = int(item.get("cloth_version", 0))
    if mesh.version != version:
        posed = item.get("posed")
        if posed is None:
            mesh.set_vertices(mesh.rest or mesh.data, version)
        elif len(posed) == mesh.count:
            mesh.set_vertices(reposed(mesh, posed[:, :3], posed[:, 3:6]), version)
    if SIM_BUFFER in getattr(getattr(model, "program", None), "cbuffers", {}):
        _feed_buffer(item, model, mesh, place, dye, logo)
