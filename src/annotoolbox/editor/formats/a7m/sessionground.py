"""Ground edits of an Anno 2205 session map: heights, painted ground, rivers and lakes written into the session's
tree files (``gamedata.data``, ``rd3d.data``).

``apply(isl, edits)`` is the entry (``isl``: ``editor.formats.a7m.session.Session2205``); it puts the edit file's
``heights``, ``paint``, ``rivers`` and ``lakes`` (the same keys as 1800's, ``groundedit``) on the opened session and
returns what it did. Only what an edit reaches is rewritten, so an unedited session keeps every byte. What a height
edit touches, what it leaves and why: docs/formats/anno2205-map-format.md, "Writing a map".
"""

from __future__ import annotations

import struct

import numpy as np

from ....formats import filedb
from ....islands import islandedits
from ....islands.islandterrain import set_bytes
from ....islands.materialorder import set_ranks
from ... import groundedit
from . import sessionpages as a7m_sessionpages
from .session import FIXED, Session2205

CODES = groundedit.CODES  # coarse height codes per world unit
GAME_CODES = groundedit.GAME_CODES  # gamedata HeightMap codes per world unit
FLOOR = -8.0  # the flat sea floor of an unedited map; its gamedata height is 0
WATER_STEP = 255 / 4.0  # WaterInfos depth byte per world unit of depth, full at 4
WATER_LAND = 23  # the depth byte on land and in the shallows


def fixed_bytes(value: float) -> bytes:
    return struct.pack("<q", round(value * FIXED))


def set_axes(tag: filedb.Tag, values: dict[str, float]) -> None:
    """Set the fixed-point attributes ``values`` of ``tag`` (an axis it does not store is added unless it is 0)."""
    for name, value in values.items():
        attr = tag.find(name)
        if attr is not None:
            attr.data = fixed_bytes(value)
        elif abs(value) >= 0.5 / FIXED:
            tag.children.append(filedb.Attrib(name, fixed_bytes(value)))


def apply(isl: Session2205, edits: dict) -> dict:
    """Put the ground edits on ``isl``; returns the report (``members``: the tree files that changed)."""
    report: dict = {}
    members: set[str] = set()
    deltas = groundedit.corner_deltas(edits, isl.size)
    if deltas is not None:
        report["heights"] = apply_heights(isl, deltas)
        isl.new_files.update(a7m_sessionpages.rewrite(isl, deltas))
        report["normal pages"] = len(isl.new_files)
        members |= {"gamedata.data", "rd3d.data"}
    strokes = edits.get("paint", [])
    if strokes:
        report["paint"] = apply_paint(isl, strokes)
        members.add("rd3d.data")
    if any(edits.get(k) for k in islandedits.WATER_EDITS):
        report["water"] = apply_water(isl, edits)
        members.add("gamedata.data")
    report["members"] = sorted(members)
    return report


# Heights


def apply_heights(isl: Session2205, deltas) -> dict:
    """Raise the coarse height map by the corner deltas, then what is derived from it: the game's own height map,
    the per-tile heights, the land bits, the sea's depth byte and the stored terrain meshes of every level."""
    t = isl.rd.path("Terrain")
    old, new = groundedit.raise_coarse(isl, deltas)
    box = groundedit.edit_box(deltas != 0)
    old_h, new_h = old / CODES, new / CODES
    report = {"corners": int((deltas != 0).sum())}
    report["game heights"] = _game_heights(isl, old_h, new_h)
    report["tile heights"] = _tile_centres(isl, new_h, new != old)
    report["land tiles changed"] = groundedit.follow_land(isl, old_h, new_h)
    report["sea cells"] = _sea_depth(isl, old, new)
    report["nodes"] = _raise_nodes(t, deltas, new_h, box)
    return report


def _game_heights(isl: Session2205, old, new) -> int:
    """``TerrainManager/HeightMap`` (128 per unit, the gameplay height): raised by the same change. The flat floor of
    the sea is 0 there, not -8; it stays 0 until an edit lifts it clear of the floor."""
    tag = isl.gm.path("TerrainManager/HeightMap")
    if tag is None or tag.find("Width").u32() != old.shape[0]:
        return 0
    g = np.frombuffer(tag.find("HeightMap").data, "<i2").reshape(old.shape).astype(np.int64)
    changed = new != old
    on_floor = (g == 0) & (np.abs(old - FLOOR) < 1e-3)
    lifted = np.where(new - FLOOR < 1e-3, 0, np.rint(new * GAME_CODES))
    out = np.where(on_floor, lifted, g + np.rint((new - old) * GAME_CODES).astype(np.int64))
    out = np.where(changed, np.clip(out, -32768, 32767), g)
    tag.find("HeightMap").data = out.astype("<i2").tobytes()
    return int(changed.sum())


def _tile_centres(isl: Session2205, new, changed) -> int:
    """``TerrainHeightMap``: the height at each tile's centre (float32, world units) = the coarse sample there."""
    tag = isl.rd.find("TerrainHeightMap")
    if tag is None:
        return 0
    n = tag.find("width").u32()
    cur = np.frombuffer(tag.find("map").data, "<f4").reshape(n, n)
    hit = changed[1::2, 1::2][:n, :n]
    out = np.where(hit, new[1::2, 1::2][:n, :n].astype("<f4"), cur)
    tag.find("map").data = out.astype("<f4").tobytes()
    return int(hit.sum())


def _sea_depth(isl: Session2205, old, new) -> int:
    """The depth byte (first channel) of ``WaterInfos`` in the cells whose centre sample the edit moved. The other
    channels (the shore's foam and the like) are kept: their rule is not known."""
    tag = isl.rd.find("WaterInfos")
    if tag is None:
        return 0
    n = tag.find("width").u32()
    cur = np.frombuffer(tag.find("map").data, np.uint8).reshape(n, n, -1).copy()
    step = (old.shape[0] - 1) // n  # samples per cell
    centre = (np.arange(n) * step + step // 2).clip(0, old.shape[0] - 1)
    before, after = old[np.ix_(centre, centre)], new[np.ix_(centre, centre)]
    hit = before != after
    code = np.clip(np.rint(-after / CODES * WATER_STEP), WATER_LAND, 255).astype(np.uint8)
    cur[..., 0] = np.where(hit, code, cur[..., 0])
    tag.find("map").data = cur.tobytes()
    return int(hit.sum())


def _levels(t: filedb.Tag):
    """(level, tag, nodes per side) of the stored mesh levels."""
    lv = t.path("Levels")
    for level, tag in enumerate(lv.findall("#1") if lv is not None else []):
        yield level, tag, tag.find("LevelSize").u32()


def _reached(level: int, side: int, box) -> list[tuple[int, int]]:
    """The (x, z) node indices of a level that the corner box (tiles) touches."""
    span = 4 << level  # tiles per node
    x0, z0 = max(0, box[0] // span), max(0, box[1] // span)
    x1, z1 = min(side - 1, box[2] // span), min(side - 1, box[3] // span)
    return [(x, z) for z in range(z0, z1 + 1) for x in range(x0, x1 + 1)]


def _raise_nodes(t: filedb.Tag, deltas, new_h, box) -> int:
    """The stored meshes (``Levels``): each vertex under the edit raised by it and lit by the new slope, the node's
    bounds widened; the meshes keep their vertices, so neighbouring nodes still meet."""
    gz, gx = np.gradient(new_h, 0.5)
    count = 0
    for level, tag, side in _levels(t):
        nodes, span = tag.find("Nodes").children, 4 << level
        for x, z in _reached(level, side, box):
            node = nodes[z * side + x] if z * side + x < len(nodes) else None
            if isinstance(node, filedb.Tag):
                _raise_node(node, deltas, new_h, (gx, gz), (x * span, z * span, span))
                count += 1
    return count


def _raise_node(node: filedb.Tag, deltas, new_h, slopes, cell: tuple[int, int, int]) -> None:
    x0, z0, span = cell
    block = new_h[z0 * 2 : (z0 + span) * 2 + 1, x0 * 2 : (x0 + span) * 2 + 1]
    lo, hi = float(block.min()), float(block.max())
    groundedit.raise_vertices(node.find("Vertices"), deltas, slopes)
    for name, pick in (("BoxMin", min), ("BoxMax", max)):
        corner = list(struct.unpack("<3f", node.find(name).data))
        corner[1] = pick(corner[1], lo if name == "BoxMin" else hi)
        node.find(name).data = struct.pack("<3f", *corner)


# Paint


def apply_paint(isl: Session2205, strokes: list[dict]) -> dict:
    """Paint the strokes into the session's material maps (``MaterialIDs2`` per 4 x 4 texels, ``MaterialAlphas2``) and
    mark their materials used on the stored mesh nodes under them."""
    try:
        order = set_ranks(isl.files.read(isl.material_set).decode("utf-8", "ignore"))
    except (KeyError, OSError):
        order = {}
    ok = groundedit.painted_strokes(strokes, order)
    report = {"strokes": len(ok), "other materials": groundedit.other_materials(strokes, ok), "blocks": 0}
    t = isl.rd.path("Terrain")
    ids_tag, al_tag = t.find("MaterialIDs2"), t.find("MaterialAlphas2")
    if not ok or ids_tag is None or al_tag is None:
        return report
    n = t.find("MaterialMapWidth").u32()
    m = len(ids_tag.data) // 4
    side = int(round(m**0.5))
    ids = np.frombuffer(ids_tag.data, "<u4").reshape(side, side).copy()
    alphas = np.frombuffer(al_tag.data, "<u4").reshape(n, n).copy()
    report["blocks"] = groundedit.paint_map(ids, alphas, ok, n // isl.size, order)
    if report["blocks"]:
        set_bytes(ids_tag, ids)
        set_bytes(al_tag, alphas)
        _mark_used(t, ok)
    return report


def _mark_used(t: filedb.Tag, strokes: list[dict]) -> None:
    """Set the painted materials' bits in ``UsedMaterialBits`` of every stored node the strokes reach (a pass that a
    node's bits lack is skipped by the game)."""
    mask = groundedit.used_mask(strokes)
    x0, z0, x1, z1 = groundedit.stroke_box(strokes)
    box = (int(x0) - 1, int(z0) - 1, int(x1) + 1, int(z1) + 1)
    for level, tag, side in _levels(t):
        nodes = tag.find("Nodes").children
        for x, z in _reached(level, side, box):
            node = nodes[z * side + x] if z * side + x < len(nodes) else None
            bits = node.find("UsedMaterialBits") if isinstance(node, filedb.Tag) else None
            if bits is not None and len(bits.data) == 4:
                bits.data = struct.pack("<I", struct.unpack("<I", bits.data)[0] | mask)


# Rivers and lakes


def _place(obj: filedb.Tag, point) -> None:
    """An editor object's position in fixed point; an axis given as None stays."""
    pos = obj.find("Position")
    set_axes(pos, {a: v for a, v in zip("xyz", point, strict=True) if v is not None})


def apply_water(isl: Session2205, edits: dict) -> dict:
    """The river and lake edits (``groundedit.apply_water``) on the session's editor objects."""
    tags = [o for _group, o in isl.object_tags(("EditorObject", "GameObject"))]
    found = (*groundedit.water_objects(tags), groundedit.coast_objects(tags))
    return groundedit.apply_water(isl, edits, found, _place)
