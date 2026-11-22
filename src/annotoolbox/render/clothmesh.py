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
from .gamedraw import CMesh

POSITION, NORMAL, TANGENT, BITANGENT = 0, 1, 2, 3
FLOAT, HALF, PACKED = 7, 6, 5  # rdm.md's element types
SIM_BUFFER = "cb_cClothSimVertices"  # 117's cloth.fx: the vertices from a buffer (``gameshader`` makes it a cbuffer)


def _read(raw: np.ndarray, kind: int, count: int, offset_bytes: int) -> np.ndarray | None:
    """An element of every vertex as floats [n, count] (a packed direction as its three components), None for a
    type this does not read."""
    if kind in (FLOAT, HALF):
        size = 4 if kind == FLOAT else 2
        return (
            np.ascontiguousarray(raw[:, offset_bytes : offset_bytes + size * count])
            .view("<f4" if kind == FLOAT else "<f2")
            .astype(float)
        )
    if kind == PACKED:
        return raw[:, offset_bytes : offset_bytes + 3].astype(float) / 127.5 - 1.0
    return None


def _write(raw: np.ndarray, kind: int, count: int, offset_bytes: int, values: np.ndarray) -> None:
    """The first three components of an element written in its own type (a fourth, w or a sign, is kept)."""
    values = np.asarray(values, float)[:, :3]
    if kind in (FLOAT, HALF):
        size = 4 if kind == FLOAT else 2
        typed = (
            np.ascontiguousarray(raw[:, offset_bytes : offset_bytes + size * count])
            .view("<f4" if kind == FLOAT else "<f2")
            .copy()
        )
        typed[:, :3] = values
        raw[:, offset_bytes : offset_bytes + size * count] = typed.view(np.uint8)
    elif kind == PACKED:
        raw[:, offset_bytes : offset_bytes + 3] = unit_bytes(values)


def reposed(mesh: CMesh, positions: np.ndarray, normals: np.ndarray) -> bytes:
    """The mesh's stored vertices with the cloth's ``positions`` and ``normals`` (one row per stored vertex), the
    tangent frame projected onto the normals; elements it does not know keep their bytes."""
    if mesh.m_elements == cloth2070.ELEMENTS:
        return cloth2070.reposed(mesh, positions, normals)
    raw = np.frombuffer(mesh.m_rest or mesh.m_data, np.uint8).reshape(mesh.m_count, mesh.m_stride).copy()
    unit_normals = np.asarray(normals, float)
    seen: set[int] = set()
    for usage, kind, count, offset in mesh.m_elements:
        if usage in seen or count < 1:
            continue  # the first element of a usage is the one the shaders read
        seen.add(usage)
        if usage == POSITION:
            _write(raw, kind, count, offset, positions)
        elif usage == NORMAL:
            _write(raw, kind, count, offset, unit_normals)
        elif usage in (TANGENT, BITANGENT):
            tangent = _read(raw, kind, count, offset)
            if tangent is not None:
                _write(raw, kind, count, offset, on_surface(tangent[:, :3], unit_normals))
    return raw.tobytes()


def mesh_of(model) -> CMesh | None:
    """The mesh a pass's model draws (117's ``CGameModel``, a forward or 2070 model's ``draw_call``)."""
    mesh = getattr(model, "m_mesh", None)
    if mesh is None:
        mesh = getattr(getattr(model, "m_draw_call", None), "m_mesh", None)
    return mesh if isinstance(mesh, CMesh) else None


def stored(mesh: CMesh, usage: int) -> np.ndarray | None:
    """The first element of a usage of the mesh's vertices as first made, [n, 3] floats (None: it has none)."""
    raw = np.frombuffer(mesh.m_rest or mesh.m_data, np.uint8).reshape(mesh.m_count, mesh.m_stride)
    found = next((e for e in mesh.m_elements if e[0] == usage), None)
    got = _read(raw, found[1], found[2], found[3]) if found is not None else None
    return got[:, :3] if got is not None else None


def _packed(vectors: np.ndarray) -> np.ndarray:
    """Unit vectors as 117's cloth shader unpacks them (``UnpackFloat3FromUint``): x | y << 8 | z << 16."""
    unit_values = unit_bytes(vectors).astype(np.uint32)
    return unit_values[:, 0] | (unit_values[:, 1] << 8) | (unit_values[:, 2] << 16)


def sim_vertices(mesh: CMesh, positions: np.ndarray, normals: np.ndarray, moved: np.ndarray | None = None) -> bytes:
    """117's ``ClothSimVertex`` per stored vertex (32 bytes: position, packed normal, tangent and bitangent, the
    motion since the last frame as halves), what its ``cloth.fx`` reads instead of a vertex stream; the tangent frame
    is the stored one turned onto the normals."""
    vertex_count = len(positions)
    normals = np.asarray(normals, float)
    out = np.zeros((vertex_count, 8), np.uint32)
    out[:, 0:3] = np.asarray(positions, "<f4").view(np.uint32)
    out[:, 3] = _packed(normals)
    for k, usage in ((4, TANGENT), (5, BITANGENT)):
        tangent = stored(mesh, usage)
        out[:, k] = _packed(on_surface(tangent, normals)) if tangent is not None else 0
    if moved is not None:
        halves = np.asarray(moved, "<f2").view(np.uint16).astype(np.uint32)
        out[:, 6], out[:, 7] = (halves[:, 0] << 16) | halves[:, 1], halves[:, 2] << 16
    return out.astype("<u4").tobytes()


def _nibbles(color) -> int:
    """A colour as 4 bits a channel, red high, stored as the shader's ``FastGammaToLinear`` expects (the square root
    of the linear value)."""
    red, green, blue = (min(max(int(round(float(channel) ** 0.5 * 15.0)), 0), 15) for channel in color)
    return (red << 8) | (green << 4) | blue


def cloth_constant(color=(1.0, 1.0, 1.0), dye=(1.0, 1.0, 1.0), second=(1.0, 1.0, 1.0), logo: int = 0) -> list[int]:
    """``cClothConstant`` (``cloth.fx``'s packing): the object colour in x bits 1 to 12 and fully opaque (bits 13 to
    15), the dye in x bits 16 to 27, the second colour in z bits 2 to 13, the logo's atlas index in z bits 14 to 21
    (``logo``, 0 none), no other atlas index (y), no damage, terrain adaption off, the vertex data at 0. The second
    colour multiplies the object colour in every cloth: left at 0 it paints the cloth black (docs/engine-cloth.md
    "The logo atlas")."""
    x = 1 | (_nibbles(color) << 1) | (0x7 << 13) | (_nibbles(dye) << 16)
    return [x, 0, (_nibbles(second) << 2) | ((logo & 0xFF) << 14), 0]


def attach_logo(model, logo) -> int:
    """The atlas index of the participant logo in a 117 cloth draw (0: none). The logo's texture joins the draw's maps
    while its resources are not made yet, and its entry goes into ``cTextureAtlas`` (a draw whose shader reads no
    atlas, or whose resources exist without the logo, gets none)."""
    table = getattr(model, "m_table", None)
    if not logo or table is None or clothlogo.ATLAS_BUFFER not in getattr(model.m_program, "m_cbuffers", {}):
        return 0
    slot = table.m_slots.get(clothlogo.LOGO_MAP)
    if slot is None and not model.m_keep:
        slot = table.add(clothlogo.LOGO_MAP, logo)
    if not slot:
        return 0
    model.m_values[f"{clothlogo.ATLAS}[{clothlogo.LOGO_INDEX}]"] = clothlogo.atlas_word(slot, logo)
    return clothlogo.LOGO_INDEX


def _feed_buffer(item: dict, model, mesh: CMesh, place, dye, logo) -> None:
    """117's cloth draws: the simulated vertices into ``cClothSimVertices`` (again when the mesh moved), the place
    into ``cbCloth``."""
    if model.m_raw.get(SIM_BUFFER) is None or getattr(model, "fed", None) != mesh.m_version:
        posed = item.get("posed") if mesh.m_version else None
        if posed is not None and len(posed) == mesh.m_count:
            vertex_positions, normals = posed[:, :3], posed[:, 3:6]
        else:
            vertex_positions, normals = stored(mesh, POSITION), stored(mesh, NORMAL)
        if vertex_positions is None or normals is None:
            return
        model.m_raw[SIM_BUFFER], model.fed = sim_vertices(mesh, vertex_positions, normals), mesh.m_version
    if place is not None:
        world = (MIRROR @ np.asarray(place, float)).T  # row vectors, as MUL(v, M) takes them
        model.m_values.update({"cCurrClothWorldMtx[0]": world, "cLastClothWorldMtx[0]": world,
                             "cClothConstant[0]": cloth_constant(dye=dye, logo=attach_logo(model, logo))})  # fmt: skip


def follow(item: dict, model, placement=None, dye=(1.0, 1.0, 1.0), logo=None) -> None:
    """The model's mesh brought to the cloth group's vertices of this frame (``viewer.clothplay`` writes them into
    ``item``: ``posed`` and its ``cloth_version``; no pose is the rest); a 117 cloth draw also gets its simulated
    vertices buffer and its place (``placement``: the part's this frame), dye colour (``dye``: linear rgb) and participant
    logo (``logo``: its texture, ``render.clothlogo``)."""
    mesh = mesh_of(model)
    if mesh is None:
        return
    version = int(item.get("cloth_version", 0))
    if mesh.m_version != version:
        posed = item.get("posed")
        if posed is None:
            mesh.set_vertices(mesh.m_rest or mesh.m_data, version)
        elif len(posed) == mesh.m_count:
            mesh.set_vertices(reposed(mesh, posed[:, :3], posed[:, 3:6]), version)
    if SIM_BUFFER in getattr(getattr(model, "m_program", None), "m_cbuffers", {}):
        _feed_buffer(item, model, mesh, placement, dye, logo)
