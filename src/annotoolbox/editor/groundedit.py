"""Ground edits of a vanilla new-engine island (a 117 island, an 1800 one): heights, painted ground, rivers and lakes
written into the island and its render data with the writers the island build uses.

``apply(isl, gamedata, edits, order)`` is the entry (``isl``: a ``formats.a7m.edit117.IslandFile``, ``gamedata`` the
island's ``_gamedata`` folder); it returns the ``_gamedata`` files it changed. Only what an edit reaches is
rewritten, so an island without ground edits keeps every byte. ``water_network`` gives the editor the island's own
rivers, lakes and coast lines. Rules and what is left out: island-editor "Ground edits of vanilla islands".
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..formats import a7mread, filedb
from ..infra.i18n import tr
from ..islands import islandedits, islandinfo, mappictures, tintpages
from ..islands.gameterrain import PAGE_TILES
from ..islands.islandgrid import HEIGHT_UNITS, ground_at, read_bits, turned_normals, write_bits
from ..islands.islandground import (
    GroundMixin,
    paint_strokes,
    painted_layers,
    slot_weights,
    stroke_cover,
    stroke_material,
)
from ..islands.islandterrain import TMC_VERTEX, replace_dds_pixels, set_bytes
from ..islands.islandwater import Spline, depth_code, fill_spline
from ..islands.terrainbuild import page_field, write_page
from . import islandgrids

CODES = 256  # height codes per world unit: coarse map, pages, stored meshes (rendering/terrain-geometry.md)
GAME_CODES = 128  # gamedata TerrainManager/HeightMap codes per world unit
LAND_ABOVE = 0.25 / HEIGHT_UNITS  # world units: a tile above this is land (the island build's rule)
TMC_NODES = 32  # nodes across one .tmc file
SLOTS = 6  # material slots per texel


@dataclass
class Gamedata:
    """An island's ``_gamedata/<name>/`` folder: its files (paths inside it) and how to read one."""

    names: list[str]
    read: Callable[[str], bytes]


def gamedata(files, a7m: str) -> Gamedata:
    """The ``_gamedata`` folder of the island ``a7m`` (a game path) in ``files`` (``list`` or ``iter_paths``)."""
    folder, stem = a7m.rsplit("/", 1)
    prefix = f"{folder}/_gamedata/{stem[:-4]}/"
    listing = getattr(files, "list", None) or files.iter_paths
    return Gamedata(sorted(p[len(prefix) :] for p in listing(prefix)), lambda rel: files.read(prefix + rel))


def apply(
    isl, gd: Gamedata, edits: dict, order: dict[int, int], colours: dict[int, tuple] | None = None
) -> tuple[dict[str, bytes], dict]:
    """Put the ground edits on ``isl``; returns the changed ``_gamedata`` files and what was done. ``colours``: the
    painted materials' colours (material ID -> rgb) the map pictures lay the strokes in."""
    out: dict[str, bytes] = {}
    report: dict = {}
    deltas = corner_deltas(edits, isl.size)
    strokes = edits.get("paint", [])
    before = coarse_heights(isl) if deltas is not None or strokes else None
    if deltas is not None:
        report["heights"] = apply_heights(isl, gd, deltas, out)
    if strokes:
        report["paint"] = apply_paint(isl, gd, strokes, order, out)
    if before is not None:
        report["pictures"] = _pictures(
            isl, gd, before, coarse_heights(isl), out, painted_strokes(strokes, order), colours or {}
        )
    if any(edits.get(k) for k in islandedits.WATER_EDITS):
        report["water"] = apply_water(isl, edits)
    if edits.get("tint"):
        names = [n for n in gd.names if tintpages.PAGE.search(n)]
        out_pages, report["tint"] = tintpages.apply(lambda n: _read(gd, out, n), names, edits["tint"])
        out.update(out_pages)
    if edits.get("ford"):
        report["ford"] = islandgrids.apply_ford(isl, edits["ford"])
    if edits.get("environment"):
        report["environment"] = islandgrids.apply_environment(isl, edits["environment"])
    return out, report


def corner_deltas(edits: dict, size: int):
    """The height edits (``"z,x"`` -> world units) as a corner grid, None when there are none."""
    d = islandedits.height_grid(edits.get("heights", {}), size + 1, size + 1)
    return d if np.abs(d).max() > 0 else None


def coarse_heights(isl) -> np.ndarray:
    """The island's ``CoarseHeightMap`` in world units, two samples a tile."""
    w, steps = a7mread.coarse_steps(isl.rd)
    return np.frombuffer(steps, np.int16).reshape(w, w).astype(np.float64) / CODES


def raise_coarse(isl, deltas) -> tuple[np.ndarray, np.ndarray]:
    """Raise the island's ``CoarseHeightMap`` by the corner deltas; returns the samples (codes) before and after."""
    w, steps = a7mread.coarse_steps(isl.rd)
    old = np.frombuffer(steps, np.int16).reshape(w, w).astype(np.int64)
    half = np.arange(w) / 2
    zz, xx = np.meshgrid(half, half, indexing="ij")
    new = np.clip(old + np.rint(ground_at(deltas, xx, zz) * CODES).astype(np.int64), -32768, 32767)
    isl.rd.path("Terrain").find("CoarseHeightMap").find("map").data = new.astype("<i2").tobytes()
    return old, new


def raise_vertices(verts: filedb.Attrib | None, deltas, slopes) -> bool:
    """Raise the vertices of a stored terrain mesh (its ``Vertices`` attribute) by the edit at their place and light
    them by the new slope ``slopes`` (the gradient of the new heights, per half tile). False when none moves."""
    if verts is None or not verts.data:
        return False
    rec = np.frombuffer(verts.data, TMC_VERTEX).copy()
    wx, wz = rec["x"] / 32.0, rec["z"] / 32.0
    d = np.rint(ground_at(deltas, wx, wz) * CODES).astype(np.int64)
    moved = d != 0
    if not moved.any():
        return False
    rec["y"] = np.clip(rec["y"].astype(np.int64) + d, 0, 65535)
    gx, gz = slopes
    n = np.stack([-ground_at(gx, wx * 2, wz * 2), np.ones_like(wx), -ground_at(gz, wx * 2, wz * 2)], 1)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    nb = np.clip(np.rint(128 + 127 * n), 0, 255).astype(np.uint8)
    for k, axis in enumerate((2, 1, 0)):  # stored as z, y, x
        rec["n"][moved, k] = nb[moved, axis]
    verts.data = rec.tobytes()
    return True


def used_mask(strokes: list[dict]) -> int:
    """The material bits (one per material id) of the strokes, as the used-material masks hold them."""
    mask = 0
    for st in strokes:
        if 0 <= (material := stroke_material(st)) < 32:  # a stroke that names none has no bit
            mask |= 1 << material
    return mask


def edit_box(mask) -> tuple[int, int, int, int]:
    """The corners (x0, z0, x1, z1) that hold a ``True`` of ``mask``, one corner wider."""
    zs, xs = np.nonzero(mask)
    return int(xs.min()) - 1, int(zs.min()) - 1, int(xs.max()) + 1, int(zs.max()) + 1


def meets(box, x0: float, z0: float, x1: float, z1: float) -> bool:
    return x0 <= box[2] and box[0] <= x1 and z0 <= box[3] and box[1] <= z1


def _read(gd: Gamedata, out: dict[str, bytes], name: str) -> bytes:
    return out[name] if name in out else gd.read(name)


# Heights
def apply_heights(isl, gd: Gamedata, deltas, out: dict[str, bytes]) -> dict:
    """Raise the coarse and the game's heightmap by the corner deltas, then the node bounds, the land, the sea
    depth, the height pages and the stored meshes they reach."""
    box = edit_box(deltas != 0)
    t = isl.rd.path("Terrain")
    old, new = raise_coarse(isl, deltas)
    hmn = isl.gm.path("TerrainManager/HeightMap")
    if hmn is not None:
        wg = hmn.find("Width").u32()
        g = np.frombuffer(hmn.find("HeightMap").data, "<i2").reshape(-1, wg).astype(np.int64)
        at = np.arange(wg) * isl.size / (wg - 1)
        gz, gx = np.meshgrid(at[: g.shape[0]], at, indexing="ij")
        g = np.clip(g + np.rint(ground_at(deltas, gx, gz) * GAME_CODES).astype(np.int64), -32768, 32767)
        hmn.find("HeightMap").data = g.astype("<i2").tobytes()
    report = {"corners": int((deltas != 0).sum()), "nodes": _node_bounds(t, new, box)}
    report["land tiles changed"] = follow_land(isl, old / CODES, new / CODES)
    report["sea cells"] = _sea_depth(isl, new / CODES, box)
    report["pages"] = _pages(gd, deltas, box, out)
    report["meshes"] = _meshes(gd, deltas, new / CODES, box, out)
    return report


def _pictures(isl, gd: Gamedata, old, new, out: dict[str, bytes], strokes: list[dict], colours: dict) -> int:
    """Draw the island's map pictures again for the new heights and the painted strokes (``mappictures.redraw``), with
    their ``_0.dds``; returns how many pictures the island has."""
    have = {name: gd.read(name) for name in mappictures.PICTURES if name in gd.names}
    if not have:
        return 0
    window = islandinfo.read(isl.info)["active_rect"] or (0, 0, isl.size, isl.size)
    drawn = mappictures.redraw(have, old, new, window, isl.size, strokes, colours)
    out.update(drawn)
    out.update(mappictures.to_dds(drawn))
    return len(drawn)


def _node_bounds(t, hm, box) -> int:
    """MinY / MaxY of the terrain nodes the edit reaches (whole world units, as vanilla rounds them), only ever
    widened: they choose shadow cascades and cull, they place nothing."""
    changed = 0
    for level, lv in enumerate(t.path("Levels").findall("#1") if t.path("Levels") is not None else []):
        mn, mx = lv.path("MinYArray"), lv.path("MaxYArray")
        if mn is None or mx is None:
            continue
        n, node = mn.find("width").u32(), 2**level
        lo = np.frombuffer(mn.find("map").data, "<i2").reshape(n, n).copy()
        hi = np.frombuffer(mx.find("map").data, "<i2").reshape(n, n).copy()
        for j in range(max(0, box[1] // node), min(n, box[3] // node + 1)):
            for i in range(max(0, box[0] // node), min(n, box[2] // node + 1)):
                block = hm[j * 2 * node : (j + 1) * 2 * node + 1, i * 2 * node : (i + 1) * 2 * node + 1]
                if block.size:
                    lo[j, i] = min(lo[j, i], int(np.trunc(block.min() / CODES)))
                    hi[j, i] = max(hi[j, i], int(np.ceil(block.max() / CODES)))
                    changed += 1
        mn.find("map").data = lo.astype("<i2").tobytes()
        mx.find("map").data = hi.astype("<i2").tobytes()
    return changed


def tile_heights(hm):
    """The mean of each tile's four corners (a heightmap of two samples per tile, world units)."""
    c = hm[::2, ::2]
    return (c[:-1, :-1] + c[:-1, 1:] + c[1:, :-1] + c[1:, 1:]) / 4


def follow_land(isl, old, new) -> int:
    """Tiles the edit lifts out of the sea or sinks into it change in the land grid (``WorldManager/Water``); a
    tile that stays on its side keeps the island's own bit."""
    water = isl.gm.path("WorldManager/Water")
    if water is None:
        return 0
    land = np.asarray(read_bits(water), np.int64)
    before, after = tile_heights(old) > LAND_ABOVE, tile_heights(new) > LAND_ABOVE
    flip = before != after
    land[flip] = after[flip]
    if flip.any():
        write_bits(water, land.tolist())
    return int(flip.sum())


def _sea_depth(isl, hm, box) -> int:
    """The sea's depth byte (rd3d ``WaterInfos``) of the cells the edit reaches, as the island build writes it."""
    wi = isl.rd.path("WaterInfos")
    if wi is None:
        return 0
    w = wi.find("width").u32()
    cell = isl.size / w
    info = np.frombuffer(wi.find("map").data, np.uint8).reshape(w, w, -1).copy()
    c = np.minimum(((np.arange(w) + 0.5) * cell * 2).astype(int), hm.shape[0] - 1)
    i0, i1 = max(0, int(box[0] / cell)), min(w, int(box[2] / cell) + 1)
    j0, j1 = max(0, int(box[1] / cell)), min(w, int(box[3] / cell) + 1)
    depth = -hm[np.ix_(c[j0:j1], c[i0:i1])]
    info[j0:j1, i0:i1, 0] = depth_code(depth)
    wi.find("map").data = info.tobytes()
    return (j1 - j0) * (i1 - i0)


def _pages(gd: Gamedata, deltas, box, out: dict[str, bytes]) -> int:
    """Every height page the edit reaches, re-encoded with the edit added (``raise_page``)."""
    count = 0
    for name in gd.names:
        if not (name.startswith("normalmaps/") and name.endswith(".ctt")):
            continue
        level, file = name.split("/")[1:3]
        nx, nz = (int(v) for v in file[:-4].split("x"))
        tiles = PAGE_TILES * 2 ** int(level)
        rim = tiles / 32  # the page's border texels reach this far past it
        x0, z0 = nx * tiles, nz * tiles
        if meets(box, x0 - rim, z0 - rim, x0 + tiles + rim, z0 + tiles + rim):
            out[name] = raise_page(gd.read(name), deltas, x0, z0, tiles)
            count += 1
    return count


def raise_page(data: bytes, deltas, x0: float, z0: float, tiles: float) -> bytes:
    """A page file (``_CTT``, size, zlib FileDB) with the corner deltas added: re-encoded from its own surface (the
    coarse map with the island's detail) plus the edit, through the island build's page encoder
    (``terrainbuild``: one height field, pages generated last); untouched ground lands within one height code."""

    def lift(xs, zs):
        zz, xx = np.meshgrid(zs, xs, indexing="ij")
        return ground_at(deltas, xx, zz)

    return write_page(data, page_field(data, lift, x0, z0, tiles), x0, z0, tiles)


def _meshes(gd: Gamedata, deltas, hm, box, out: dict[str, bytes]) -> int:
    """The stored terrain meshes (LOD 2 and up) under the edit: each vertex raised by the edit at its place and lit
    by the new slope; the meshes keep their vertices, so neighbouring nodes still meet."""
    gz, gx = np.gradient(hm, 0.5)
    count = 0
    for name in gd.names:
        if not (name.startswith("meshes/") and name.endswith(".tmc")):
            continue
        level, file = name.split("/")[1:3]
        tx, tz = (int(v) for v in file[:-4].split("x"))
        span = TMC_NODES * 2 ** int(level)
        if not meets(box, tx * span, tz * span, (tx + 1) * span, (tz + 1) * span):
            continue
        raw = _read(gd, out, name)
        doc, changed = filedb.read(raw), False
        for e in doc.path("Meshes/map").children:
            if isinstance(e, filedb.Tag):
                changed |= raise_vertices(e.find("Vertices"), deltas, (gx, gz))
        if changed:
            out[name] = filedb.write(doc, *filedb.name_ids(raw), version=filedb.version(raw))
            count += 1
    return count


# Paint
class Painted(GroundMixin):
    """The island build's encoder (``GroundMixin.encode_materials``) over weights worked out here."""

    def __init__(self, mats: list[int], weights, rank: dict[int, int]) -> None:
        self._mats, self._weights, self.material_rank = mats, weights, rank

    def material_weights(self, texels_per_tile, material_of):
        return self._mats, self._weights


def dds_u32(data: bytes):
    """A material map's DDS (one u32 a texel, ``matid`` / ``matalpha``) as a [z, x] array."""
    header = 148 if data[84:88] == b"DX10" else 128
    w = int.from_bytes(data[16:20], "little")
    return np.frombuffer(data[header:], "<u4").reshape(-1, w).copy()


def painted_strokes(strokes: list[dict], order: dict[int, int]) -> list[dict]:
    """The strokes of the island's own materials (``order``: its set's IDs), the ones the paint step lays; a stroke
    without a point or without a number as its material (a hand-edited file) lays nothing and is left out, as
    the island build leaves it."""
    return [st for st in strokes if st.get("points") and stroke_material(st) in order and stroke_material(st) < 32]


def other_materials(strokes: list[dict], laid: list[dict]) -> int:
    """How many strokes with points are not among the ``laid`` ones: those of a material outside the island's set."""
    return sum(1 for st in strokes if st.get("points")) - len(laid)


def apply_paint(isl, gd: Gamedata, strokes: list[dict], order: dict[int, int], out: dict[str, bytes]) -> dict:
    """Paint the strokes of the island's own materials into every material map it has (117: the four levels of
    ``materialmaps/`` and the copy in rd3d; 1800: rd3d) and mark the materials used where they were painted."""
    ok = painted_strokes(strokes, order)
    report = {"strokes": len(ok), "other materials": other_materials(strokes, ok), "blocks": 0}
    if not ok:
        return report
    t = isl.rd.path("Terrain")
    for name in [n for n in gd.names if n.startswith("materialmaps/") and n.endswith("/matid.dds")]:
        alpha_name = name.replace("matid.dds", "matalpha.dds")
        ids_dds, al_dds = gd.read(name), gd.read(alpha_name)
        ids, alphas = dds_u32(ids_dds), dds_u32(al_dds)
        done = paint_map(ids, alphas, ok, alphas.shape[0] // isl.size, order)
        if done:
            out[name], out[alpha_name] = replace_dds_pixels(ids_dds, ids), replace_dds_pixels(al_dds, alphas)
            report["blocks"] += done
    ids_tag, al_tag = t.find("MaterialIDs2"), t.find("MaterialAlphas2")
    if ids_tag is not None and al_tag is not None:
        n = int(round((len(al_tag.data) // 4) ** 0.5))
        m = int(round((len(ids_tag.data) // 4) ** 0.5))
        ids = np.frombuffer(ids_tag.data, "<u4").reshape(m, m).copy()
        alphas = np.frombuffer(al_tag.data, "<u4").reshape(n, n).copy()
        done = paint_map(ids, alphas, ok, max(1, n // isl.size), order)
        if done:
            set_bytes(ids_tag, ids)
            set_bytes(al_tag, alphas)
            report["blocks"] += done
    _mark_used(t, gd, ok, out)
    return report


def stroke_box(strokes: list[dict]) -> tuple[float, float, float, float]:
    """The tiles (x0, z0, x1, z1) the strokes reach."""
    xs = [p[0] + s * float(st.get("radius", 4)) for st in strokes for p in st["points"] for s in (-1, 1)]
    zs = [p[1] + s * float(st.get("radius", 4)) for st in strokes for p in st["points"] for s in (-1, 1)]
    return min(xs), min(zs), max(xs), max(zs)


def paint_map(ids, alphas, strokes: list[dict], texels_per_tile: int, order: dict[int, int]) -> int:
    """Paint ``strokes`` into one material map (``ids`` per block, ``alphas`` per texel, changed in place): the
    ground under them is read as the game layers it, painted, encoded again by the island build's encoder and put back
    block by block. Returns the blocks changed."""
    n, block = alphas.shape[0], alphas.shape[0] // ids.shape[0]
    bx0, bz0, bx1, bz1 = stroke_box(strokes)
    lo = [max(0, int(v * texels_per_tile) // block * block) for v in (bx0, bz0)]
    side = max(int(np.ceil(bx1 * texels_per_tile)) - lo[0], int(np.ceil(bz1 * texels_per_tile)) - lo[1])
    side = min(n, -(-max(side, block) // block) * block)
    x0, z0 = (min(v, n - side) for v in lo)
    if bx1 < 0 or bz1 < 0 or bx0 * texels_per_tile >= n or bz0 * texels_per_tile >= n:
        return 0
    shift = [
        {**st, "points": [[p[0] - x0 / texels_per_tile, p[1] - z0 / texels_per_tile] for p in st["points"]]}
        for st in strokes
    ]
    cover = np.zeros((side, side), bool)
    for st in shift:
        cover |= stroke_cover(st, side, texels_per_tile) > 0
    blocks = cover.reshape(side // block, block, side // block, block).any(axis=(1, 3))
    if not blocks.any():
        return 0
    sl_b = np.s_[z0 // block : (z0 + side) // block, x0 // block : (x0 + side) // block]
    sl_t = np.s_[z0 : z0 + side, x0 : x0 + side]
    mats, weights = layered_weights(ids[sl_b], alphas[sl_t], order)
    mats, weights = painted_layers(weights, mats, shift)
    weights = paint_strokes(weights, mats, shift, texels_per_tile)
    new_ids, new_alphas, _ = Painted(mats, weights.astype(np.float32), order).encode_materials(
        texels_per_tile, {}, block
    )
    ids[sl_b][blocks] = new_ids[blocks]
    texels = np.repeat(np.repeat(blocks, block, 0), block, 1)
    alphas[sl_t][texels] = new_alphas[texels]
    return int(blocks.sum())


def layered_weights(ids, alphas, order: dict[int, int]) -> tuple[list[int], np.ndarray]:
    """The visible weight of each material per texel as the game lays them (rendering/terrain-materials 1.4): a
    material's coverage is its alphas summed over the slots, the materials go down in the set's order, and the
    first one drawn takes what the others leave (``islandground.slot_weights``, summed per material)."""
    block = alphas.shape[0] // ids.shape[0]
    up = np.repeat(np.repeat(ids, block, 0), block, 1)
    slot_ids = np.stack([(up >> np.uint32(5 * s)) & np.uint32(31) for s in range(SLOTS)]).astype(np.int64)
    slot_a = np.stack([((alphas >> np.uint32(5 * s)) & np.uint32(31)).astype(np.float32) / 31 for s in range(SLOTS)])
    bare = ~(slot_a > 0).any(axis=0)  # no alpha at all: the first slot's material stands
    mats = sorted({int(m) for m in np.unique(slot_ids[slot_a > 0])} | {int(m) for m in np.unique(slot_ids[0][bare])})
    w = slot_weights(slot_ids, slot_a, order)
    return mats, np.stack([np.where(slot_ids == m, w, 0).sum(axis=0) for m in mats])


def _mark_used(t, gd: Gamedata, strokes: list[dict], out: dict[str, bytes]) -> None:
    """Set the painted materials' bits in the used-material masks (rd3d and the meshes' nodes under the strokes)."""
    mask = used_mask(strokes)
    umb = t.find("UsedMaterialBits")
    if umb is not None and len(umb.data) % 4 == 0:
        umb.data = (np.frombuffer(umb.data, "<u4") | np.uint32(mask)).astype("<u4").tobytes()
    box = stroke_box(strokes)
    for name in gd.names:
        if not (name.startswith("meshes/") and name.endswith(".tmc")):
            continue
        level, file = name.split("/")[1:3]
        tx, tz = (int(v) for v in file[:-4].split("x"))
        node = 2 ** int(level)
        if not meets(
            box, tx * TMC_NODES * node, tz * TMC_NODES * node, (tx + 1) * TMC_NODES * node, (tz + 1) * TMC_NODES * node
        ):
            continue
        raw = _read(gd, out, name)
        doc, changed = filedb.read(raw), False
        for k, e in enumerate(c for c in doc.path("Meshes/map").children if isinstance(c, filedb.Tag)):
            x, z = (tx * TMC_NODES + k % TMC_NODES) * node, (tz * TMC_NODES + k // TMC_NODES) * node
            bits = e.find("UsedMaterialBits")
            if bits is None or len(bits.data) != 4 or not meets(box, x, z, x + node, z + node):
                continue
            value = struct.unpack("<I", bits.data)[0]
            if value | mask != value:
                bits.data = struct.pack("<I", value | mask)
                changed = True
        if changed:
            out[name] = filedb.write(doc, *filedb.name_ids(raw), version=filedb.version(raw))


# Rivers and lakes
def _water_objects(isl) -> tuple[list[filedb.Tag], list[filedb.Tag]]:
    """The island's river and lake editor objects, in the order ``water_network`` numbers them."""
    tags = (
        o
        for _area, doc, _ids in isl._areas_data()
        for o in (lambda objs: objs.children if objs is not None else [])(a7mread.area_objects(doc, "EditorObject"))
    )
    return water_objects(tags)


def water_objects(tags) -> tuple[list[filedb.Tag], list[filedb.Tag]]:
    """The rivers and the lakes among editor object tags (those with a Bezier path), each in file order."""
    rivers, lakes = [], []
    for o in tags:
        if isinstance(o, filedb.Tag) and o.path("BezierPath/Path/BezierCurve") is not None:
            if o.find("River") is not None:
                rivers.append(o)
            elif o.find("Lake") is not None:
                lakes.append(o)
    return rivers, lakes


def coast_objects(tags) -> list[filedb.Tag]:
    """The coast lines among editor object tags (a Bezier path with a ``CoastLine`` block), in file order."""
    return [
        o
        for o in tags
        if isinstance(o, filedb.Tag) and o.find("CoastLine") is not None and o.path("BezierPath/Path/BezierCurve")
    ]


def water_network(splines) -> dict | None:
    """The island's own rivers, lakes and coast lines (``IslandView.splines``) as the editor's water tool edits them,
    numbered in the island's order (a coast line by its x, z points); None for an island with none of them."""
    rivers = [{"points": [list(p) for p in pts], "widths": list(w)} for kind, pts, w in splines if kind == "River"]
    lakes = [
        {"points": [[p[0], p[2]] for p in pts], "level": float(pts[0][1]), "damHeight": 0.0}
        for kind, pts, _w in splines
        if kind == "Lake" and pts
    ]
    coasts = [{"points": [[p[0], p[2]] for p in pts]} for kind, pts, _w in splines if kind == "CoastLine" and pts]
    return {"rivers": rivers, "lakes": lakes, "coasts": coasts} if rivers or lakes or coasts else None


def apply_water(isl, edits: dict, objects=None, place=None) -> dict:
    """Write the edited rivers (their points, widths and flow speeds, the curve through them), lake levels and coast
    lines (``edits["coast"]``: ``"k"`` -> ``points`` x, z) into the island's editor objects; the gameplay river grid
    and the ground under the water stay as they are. ``objects``: the (rivers, lakes, coast lines) when the island
    does not keep them per area; ``place(obj, (x, y, z))`` sets an object's position where it is not three floats
    (2205)."""
    rivers, lakes, coasts = objects if objects is not None else (*_water_objects(isl), _coast_objects(isl))
    place = place or _place_floats
    report = {"rivers": 0, "lakes": 0, "unmatched": []}
    for k, river in sorted(edits.get("rivers", {}).items()):
        if not str(k).isdigit() or int(k) >= len(rivers) or not river_edit_ok(river):
            report["unmatched"].append(tr("islands.river_k", k=k))
            continue
        _set_river(
            rivers[int(k)], [tuple(map(float, p)) for p in river["points"]], [float(w) for w in river["widths"]], place
        )
        report["rivers"] += 1
    for k, level in sorted(edits.get("lakes", {}).items()):
        if not str(k).isdigit() or int(k) >= len(lakes):
            report["unmatched"].append(tr("islands.lake_k", k=k))
            continue
        _set_lake(lakes[int(k)], float(level), place)
        report["lakes"] += 1
    for k, outline in sorted(edits.get("lake_outlines", {}).items()):
        if not str(k).isdigit() or int(k) >= len(lakes) or len(outline["points"]) < 3:
            report["unmatched"].append(tr("islands.lake_outline_k", k=k))
            continue
        level = float(edits.get("lakes", {}).get(k, _lake_level(lakes[int(k)])))
        fill_lake(lakes[int(k)], [(float(x), level, float(z)) for x, z in outline["points"]], place)
        report["lake outlines"] = report.get("lake outlines", 0) + 1
    for k, line in sorted(edits.get("coast", {}).items()):
        if not str(k).isdigit() or int(k) >= len(coasts) or len(line["points"]) < 2:
            report["unmatched"].append(tr("islands.coast_line_k", k=k))
            continue
        _set_coast(coasts[int(k)], [tuple(map(float, p)) for p in line["points"]], place)
        report["coasts"] = report.get("coasts", 0) + 1
    return report


def river_edit_ok(change: dict) -> bool:
    """A river edit holds a width for each of at least two points (a shorter river would lose its line)."""
    points, widths = change.get("points"), change.get("widths")
    return points is not None and widths is not None and len(points) == len(widths) >= 2


def _lake_level(obj: filedb.Tag) -> float:
    """A lake's water level: the height of its first outline point."""
    return struct.unpack("<3f", obj.path("BezierPath/Path/BezierCurve").children[0].find("p").data)[1]


def fill_lake(obj: filedb.Tag, points: list[tuple[float, float, float]], place) -> None:
    """A lake's outline through new (x, level, z) points: straight sides, as a lake of a built island has them; the ground
    under it is not carved (shape the basin with the height brush)."""
    serial = struct.unpack_from("<I", obj.find("ID").data)[0]
    fill_spline(obj, Spline("Lake", points), serial, place or _place_floats)


def _coast_objects(isl) -> list[filedb.Tag]:
    """The island's coast line editor objects, in the order ``water_network`` numbers them."""
    return coast_objects(
        o
        for _area, doc, _ids in isl._areas_data()
        for o in (lambda objs: objs.children if objs is not None else [])(a7mread.area_objects(doc, "EditorObject"))
    )


def _set_coast(obj: filedb.Tag, points: list[tuple[float, float]], place) -> None:
    """A coast line's curve through new (x, z) points: each keeps the height of the old point nearest to it, and its
    normal (``d``, the side the coast faces) turns with the line, keeping the sense the old one had."""
    old = [p for p in obj.path("BezierPath/Path/BezierCurve").children if isinstance(p, filedb.Tag)]
    where = [struct.unpack("<3f", p.find("p").data) for p in old]
    was = [struct.unpack("<2f", p.find("d").data) if p.find("d") is not None else (1.0, 0.0) for p in old]
    near, normals = turned_normals([(w[0], w[2]) for w in where], was, points)
    pts = [(x, float(where[n][1]), z) for (x, z), n in zip(points, near, strict=True)]
    serial = struct.unpack_from("<I", obj.find("ID").data)[0]
    fill_spline(obj, Spline("CoastLine", pts, normals=normals), serial, place or _place_floats)


def _place_floats(obj: filedb.Tag, point) -> None:
    """Set an object's position; an axis given as None stays."""
    pos = obj.find("Position")
    old = struct.unpack("<3f", pos.data)
    pos.data = struct.pack("<3f", *(o if v is None else v for v, o in zip(point, old, strict=True)))


def _set_river(obj: filedb.Tag, points: list[tuple], widths: list[float], place) -> None:
    """A river's curve through new points (``fill_spline``), each point flowing as the old point nearest to it."""
    old = [p for p in obj.path("BezierPath/Path/BezierCurve").children if isinstance(p, filedb.Tag)]
    where = np.array([struct.unpack("<3f", p.find("p").data) for p in old])
    speeds = []
    for p in points:
        near = old[int(np.argmin(((where[:, [0, 2]] - (p[0], p[2])) ** 2).sum(axis=1)))]
        u0 = near.find("u0")
        speeds.append(struct.unpack("<3f", u0.data) if u0 is not None else (1.0, 1.0, 1.0))
    serial = struct.unpack_from("<I", obj.find("ID").data)[0]
    fill_spline(obj, Spline("River", points, widths=widths, speeds=speeds), serial, place)


def _set_lake(obj: filedb.Tag, level: float, place) -> None:
    """A lake's water at ``level``: every point of its outline, its place and its bounds."""
    for p in obj.path("BezierPath/Path/BezierCurve").children:
        if isinstance(p, filedb.Tag) and p.find("p") is not None:
            x, _, z = struct.unpack("<3f", p.find("p").data)
            p.find("p").data = struct.pack("<3f", x, level, z)
    place(obj, (None, level, None))
    path = obj.path("BezierPath/Path")
    for name in ("Minimum", "Maximum"):
        x, _, z = struct.unpack("<3f", path.find(name).data)
        path.find(name).data = struct.pack("<3f", x, level, z)


def unmatched(report: dict) -> list[str]:
    """What the ground edits could not place, for the edit report."""
    lost = report.get("paint", {}).get("other materials", 0)
    paint = [f"{lost} painted strokes of a material outside the island's set"] if lost else []
    grids = [m for key in ("water", "ford", "environment", "tint") for m in report.get(key, {}).get("unmatched", [])]
    return grids + paint
