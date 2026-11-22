"""Ground edits of a vanilla new-engine island (a 117 island, an 1800 one): heights, painted ground, rivers and lakes
written into the island and its render data with the writers the island build uses.

``apply(island_file, gamedata_folder, edits, order)`` is the entry (``island_file``: a
``formats.a7m.edit117.CIslandFile``, ``gamedata_folder`` the island's ``_gamedata`` folder); it returns the
``_gamedata`` files it changed. Only what an edit reaches is rewritten, so an island without ground edits keeps every
byte. ``water_network`` gives the editor the island's own rivers, lakes and coast lines. Rules and what is left out:
island-editor "Ground edits of vanilla islands".
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
    CGroundMixin,
    paint_strokes,
    painted_layers,
    slot_weights,
    stroke_cover,
    stroke_material,
)
from ..islands.islandterrain import TMC_VERTEX, replace_dds_pixels, set_bytes
from ..islands.islandwater import SSpline, depth_code, fill_spline
from ..islands.terrainbuild import page_field, write_page
from . import islandgrids

CODES = 256  # height codes per world unit: coarse map, pages, stored meshes (rendering/terrain-geometry.md)
GAME_CODES = 128  # gamedata TerrainManager/HeightMap codes per world unit
LAND_ABOVE = 0.25 / HEIGHT_UNITS  # world units: a tile above this is land (the island build's rule)
TMC_NODES = 32  # nodes across one .tmc file
SLOTS = 6  # material slots per texel


@dataclass
class SGamedata:
    """An island's ``_gamedata/<name>/`` folder: its files (paths inside it) and how to read one."""

    names: list[str]
    read: Callable[[str], bytes]


def gamedata(files, a7m: str) -> SGamedata:
    """The ``_gamedata`` folder of the island ``a7m`` (a game path) in ``files`` (``list`` or ``iter_paths``)."""
    folder, stem = a7m.rsplit("/", 1)
    prefix = f"{folder}/_gamedata/{stem[:-4]}/"
    listing = getattr(files, "list", None) or files.iter_paths
    return SGamedata(sorted(path[len(prefix) :] for path in listing(prefix)), lambda rel: files.read(prefix + rel))


def apply(
    island_file, gamedata_folder: SGamedata, edits: dict, order: dict[int, int], colours: dict[int, tuple] | None = None
) -> tuple[dict[str, bytes], dict]:
    """Put the ground edits on ``island_file``; returns the changed ``_gamedata`` files and what was done. ``colours``: the
    painted materials' colours (material ID -> rgb) the map pictures lay the strokes in."""
    out: dict[str, bytes] = {}
    report: dict = {}
    deltas = corner_deltas(edits, island_file.size)
    strokes = edits.get("paint", [])
    before = coarse_heights(island_file) if deltas is not None or strokes else None
    if deltas is not None:
        report["heights"] = apply_heights(island_file, gamedata_folder, deltas, out)
    if strokes:
        report["paint"] = apply_paint(island_file, gamedata_folder, strokes, order, out)
    if before is not None:
        report["pictures"] = _pictures(
            island_file,
            gamedata_folder,
            before,
            coarse_heights(island_file),
            out,
            painted_strokes(strokes, order),
            colours or {},
        )
    if any(edits.get(edit_name) for edit_name in islandedits.WATER_EDITS):
        report["water"] = apply_water(island_file, edits)
    if edits.get("tint"):
        names = [name for name in gamedata_folder.names if tintpages.PAGE.search(name)]
        out_pages, report["tint"] = tintpages.apply(
            lambda name: _read(gamedata_folder, out, name), names, edits["tint"]
        )
        out.update(out_pages)
    if edits.get("ford"):
        report["ford"] = islandgrids.apply_ford(island_file, edits["ford"])
    if edits.get("environment"):
        report["environment"] = islandgrids.apply_environment(island_file, edits["environment"])
    return out, report


def corner_deltas(edits: dict, size_tiles: int):
    """The height edits (``"z,x"`` -> world units) as a corner grid, None when there are none."""
    grid = islandedits.height_grid(edits.get("heights", {}), size_tiles + 1, size_tiles + 1)
    return grid if np.abs(grid).max() > 0 else None


def coarse_heights(island_file) -> np.ndarray:
    """The island's ``CoarseHeightMap`` in world units, two samples a tile."""
    sample_count, steps = a7mread.coarse_steps(island_file.rd3d)
    return np.frombuffer(steps, np.int16).reshape(sample_count, sample_count).astype(np.float64) / CODES


def raise_coarse(island_file, deltas) -> tuple[np.ndarray, np.ndarray]:
    """Raise the island's ``CoarseHeightMap`` by the corner deltas; returns the samples (codes) before and after."""
    sample_count, steps = a7mread.coarse_steps(island_file.rd3d)
    old = np.frombuffer(steps, np.int16).reshape(sample_count, sample_count).astype(np.int64)
    half = np.arange(sample_count) / 2
    z_grid, x_grid = np.meshgrid(half, half, indexing="ij")
    new = np.clip(old + np.rint(ground_at(deltas, x_grid, z_grid) * CODES).astype(np.int64), -32768, 32767)
    island_file.rd3d.path("Terrain").find("CoarseHeightMap").find("map").data = new.astype("<i2").tobytes()
    return old, new


def raise_vertices(vertices: filedb.SAttrib | None, deltas, slopes) -> bool:
    """Raise the vertices of a stored terrain mesh (its ``Vertices`` attribute) by the edit at their place and light
    them by the new slope ``slopes`` (the gradient of the new heights, per half tile). False when none moves."""
    if vertices is None or not vertices.data:
        return False
    records = np.frombuffer(vertices.data, TMC_VERTEX).copy()
    world_x, world_z = records["x"] / 32.0, records["z"] / 32.0
    delta_codes = np.rint(ground_at(deltas, world_x, world_z) * CODES).astype(np.int64)
    moved = delta_codes != 0
    if not moved.any():
        return False
    records["y"] = np.clip(records["y"].astype(np.int64) + delta_codes, 0, 65535)
    slope_x, slope_z = slopes
    normals = np.stack(
        [
            -ground_at(slope_x, world_x * 2, world_z * 2),
            np.ones_like(world_x),
            -ground_at(slope_z, world_x * 2, world_z * 2),
        ],
        1,
    )
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    normal_bytes = np.clip(np.rint(128 + 127 * normals), 0, 255).astype(np.uint8)
    for k, axis in enumerate((2, 1, 0)):  # stored as z, y, x
        records["n"][moved, k] = normal_bytes[moved, axis]
    vertices.data = records.tobytes()
    return True


def used_mask(strokes: list[dict]) -> int:
    """The material bits (one per material id) of the strokes, as the used-material masks hold them."""
    mask = 0
    for stroke in strokes:
        if 0 <= (material := stroke_material(stroke)) < 32:  # a stroke that names none has no bit
            mask |= 1 << material
    return mask


def edit_box(mask) -> tuple[int, int, int, int]:
    """The corners (min_x, min_z, max_x, max_z) that hold a ``True`` of ``mask``, one corner wider."""
    z_indices, x_indices = np.nonzero(mask)
    return int(x_indices.min()) - 1, int(z_indices.min()) - 1, int(x_indices.max()) + 1, int(z_indices.max()) + 1


def meets(box, min_x: float, min_z: float, max_x: float, max_z: float) -> bool:
    return min_x <= box[2] and box[0] <= max_x and min_z <= box[3] and box[1] <= max_z


def _read(gamedata_folder: SGamedata, out: dict[str, bytes], name: str) -> bytes:
    return out[name] if name in out else gamedata_folder.read(name)


# Heights
def apply_heights(island_file, gamedata_folder: SGamedata, deltas, out: dict[str, bytes]) -> dict:
    """Raise the coarse and the game's heightmap by the corner deltas, then the node bounds, the land, the sea
    depth, the height pages and the stored meshes they reach."""
    box = edit_box(deltas != 0)
    terrain = island_file.rd3d.path("Terrain")
    old, new = raise_coarse(island_file, deltas)
    game_height_map = island_file.game_session_manager.path("TerrainManager/HeightMap")
    if game_height_map is not None:
        game_width = game_height_map.find("Width").u32()
        game_heights = (
            np.frombuffer(game_height_map.find("HeightMap").data, "<i2").reshape(-1, game_width).astype(np.int64)
        )
        sample_positions = np.arange(game_width) * island_file.size / (game_width - 1)
        grid_z, grid_x = np.meshgrid(sample_positions[: game_heights.shape[0]], sample_positions, indexing="ij")
        game_heights = np.clip(
            game_heights + np.rint(ground_at(deltas, grid_x, grid_z) * GAME_CODES).astype(np.int64), -32768, 32767
        )
        game_height_map.find("HeightMap").data = game_heights.astype("<i2").tobytes()
    report = {"corners": int((deltas != 0).sum()), "nodes": _node_bounds(terrain, new, box)}
    report["land tiles changed"] = follow_land(island_file, old / CODES, new / CODES)
    report["sea cells"] = _sea_depth(island_file, new / CODES, box)
    report["pages"] = _pages(gamedata_folder, deltas, box, out)
    report["meshes"] = _meshes(gamedata_folder, deltas, new / CODES, box, out)
    return report


def _pictures(
    island_file, gamedata_folder: SGamedata, old, new, out: dict[str, bytes], strokes: list[dict], colours: dict
) -> int:
    """Draw the island's map pictures again for the new heights and the painted strokes (``mappictures.redraw``), with
    their ``_0.dds``; returns how many pictures the island has."""
    have = {name: gamedata_folder.read(name) for name in mappictures.PICTURES if name in gamedata_folder.names}
    if not have:
        return 0
    window = islandinfo.read(island_file.a7minfo)["active_rect"] or (0, 0, island_file.size, island_file.size)
    drawn = mappictures.redraw(have, old, new, window, island_file.size, strokes, colours)
    out.update(drawn)
    out.update(mappictures.to_dds(drawn))
    return len(drawn)


def _node_bounds(terrain, height_codes, box) -> int:
    """MinY / MaxY of the terrain nodes the edit reaches (whole world units, as vanilla rounds them), only ever
    widened: they choose shadow cascades and cull, they place nothing."""
    changed = 0
    for level, level_tag in enumerate(
        terrain.path("Levels").findall("#1") if terrain.path("Levels") is not None else []
    ):
        min_y_array, max_y_array = level_tag.path("MinYArray"), level_tag.path("MaxYArray")
        if min_y_array is None or max_y_array is None:
            continue
        array_width, node = min_y_array.find("width").u32(), 2**level
        min_y = np.frombuffer(min_y_array.find("map").data, "<i2").reshape(array_width, array_width).copy()
        max_y = np.frombuffer(max_y_array.find("map").data, "<i2").reshape(array_width, array_width).copy()
        for j in range(max(0, box[1] // node), min(array_width, box[3] // node + 1)):
            for i in range(max(0, box[0] // node), min(array_width, box[2] // node + 1)):
                block = height_codes[j * 2 * node : (j + 1) * 2 * node + 1, i * 2 * node : (i + 1) * 2 * node + 1]
                if block.size:
                    min_y[j, i] = min(min_y[j, i], int(np.trunc(block.min() / CODES)))
                    max_y[j, i] = max(max_y[j, i], int(np.ceil(block.max() / CODES)))
                    changed += 1
        min_y_array.find("map").data = min_y.astype("<i2").tobytes()
        max_y_array.find("map").data = max_y.astype("<i2").tobytes()
    return changed


def tile_heights(heights):
    """The mean of each tile's four corners (a heightmap of two samples per tile, world units)."""
    corners = heights[::2, ::2]
    return (corners[:-1, :-1] + corners[:-1, 1:] + corners[1:, :-1] + corners[1:, 1:]) / 4


def follow_land(island_file, old, new) -> int:
    """Tiles the edit lifts out of the sea or sinks into it change in the land grid (``WorldManager/Water``); a
    tile that stays on its side keeps the island's own bit."""
    water = island_file.game_session_manager.path("WorldManager/Water")
    if water is None:
        return 0
    land = np.asarray(read_bits(water), np.int64)
    before, after = tile_heights(old) > LAND_ABOVE, tile_heights(new) > LAND_ABOVE
    flip = before != after
    land[flip] = after[flip]
    if flip.any():
        write_bits(water, land.tolist())
    return int(flip.sum())


def _sea_depth(island_file, heights, box) -> int:
    """The sea's depth byte (rd3d ``WaterInfos``) of the cells the edit reaches, as the island build writes it."""
    water_infos = island_file.rd3d.path("WaterInfos")
    if water_infos is None:
        return 0
    cell_count = water_infos.find("width").u32()
    cell = island_file.size / cell_count
    info_map = np.frombuffer(water_infos.find("map").data, np.uint8).reshape(cell_count, cell_count, -1).copy()
    centres = np.minimum(((np.arange(cell_count) + 0.5) * cell * 2).astype(int), heights.shape[0] - 1)
    min_column, max_column = max(0, int(box[0] / cell)), min(cell_count, int(box[2] / cell) + 1)
    min_row, max_row = max(0, int(box[1] / cell)), min(cell_count, int(box[3] / cell) + 1)
    depth = -heights[np.ix_(centres[min_row:max_row], centres[min_column:max_column])]
    info_map[min_row:max_row, min_column:max_column, 0] = depth_code(depth)
    water_infos.find("map").data = info_map.tobytes()
    return (max_row - min_row) * (max_column - min_column)


def _pages(gamedata_folder: SGamedata, deltas, box, out: dict[str, bytes]) -> int:
    """Every height page the edit reaches, re-encoded with the edit added (``raise_page``)."""
    count = 0
    for name in gamedata_folder.names:
        if not (name.startswith("normalmaps/") and name.endswith(".ctt")):
            continue
        level, file = name.split("/")[1:3]
        page_x, page_z = (int(part) for part in file[:-4].split("x"))
        tiles = PAGE_TILES * 2 ** int(level)
        rim = tiles / 32  # the page's border texels reach this far past it
        min_x, min_z = page_x * tiles, page_z * tiles
        if meets(box, min_x - rim, min_z - rim, min_x + tiles + rim, min_z + tiles + rim):
            out[name] = raise_page(gamedata_folder.read(name), deltas, min_x, min_z, tiles)
            count += 1
    return count


def raise_page(data: bytes, deltas, min_x: float, min_z: float, tiles: float) -> bytes:
    """A page file (``_CTT``, size, zlib FileDB) with the corner deltas added: re-encoded from its own surface (the
    coarse map with the island's detail) plus the edit, through the island build's page encoder
    (``terrainbuild``: one height field, pages generated last); untouched ground lands within one height code."""

    def lift(xs, zs):
        z_grid, x_grid = np.meshgrid(zs, xs, indexing="ij")
        return ground_at(deltas, x_grid, z_grid)

    return write_page(data, page_field(data, lift, min_x, min_z, tiles), min_x, min_z, tiles)


def _meshes(gamedata_folder: SGamedata, deltas, heights, box, out: dict[str, bytes]) -> int:
    """The stored terrain meshes (LOD 2 and up) under the edit: each vertex raised by the edit at its place and lit
    by the new slope; the meshes keep their vertices, so neighbouring nodes still meet."""
    slope_z, slope_x = np.gradient(heights, 0.5)
    count = 0
    for name in gamedata_folder.names:
        if not (name.startswith("meshes/") and name.endswith(".tmc")):
            continue
        level, file = name.split("/")[1:3]
        node_x, node_z = (int(part) for part in file[:-4].split("x"))
        span = TMC_NODES * 2 ** int(level)
        if not meets(box, node_x * span, node_z * span, (node_x + 1) * span, (node_z + 1) * span):
            continue
        raw = _read(gamedata_folder, out, name)
        doc, changed = filedb.read(raw), False
        for mesh_tag in doc.path("Meshes/map").children:
            if isinstance(mesh_tag, filedb.STag):
                changed |= raise_vertices(mesh_tag.find("Vertices"), deltas, (slope_x, slope_z))
        if changed:
            out[name] = filedb.write(doc, *filedb.name_ids(raw), version=filedb.version(raw))
            count += 1
    return count


# Paint
class CPainted(CGroundMixin):
    """The island build's encoder (``CGroundMixin.encode_materials``) over weights worked out here."""

    def __init__(self, materials: list[int], weights, rank: dict[int, int]) -> None:
        self._m_mats, self._m_weights, self.material_rank = materials, weights, rank

    def material_weights(self, texels_per_tile, material_of):
        return self._m_mats, self._m_weights


def dds_u32(data: bytes):
    """A material map's DDS (one u32 a texel, ``matid`` / ``matalpha``) as a [z, x] array."""
    header = 148 if data[84:88] == b"DX10" else 128
    width = int.from_bytes(data[16:20], "little")
    return np.frombuffer(data[header:], "<u4").reshape(-1, width).copy()


def painted_strokes(strokes: list[dict], order: dict[int, int]) -> list[dict]:
    """The strokes of the island's own materials (``order``: its set's IDs), the ones the paint step lays; a stroke
    without a point or without a number as its material (a hand-edited file) lays nothing and is left out, as
    the island build leaves it."""
    return [
        stroke
        for stroke in strokes
        if stroke.get("points") and stroke_material(stroke) in order and stroke_material(stroke) < 32
    ]


def other_materials(strokes: list[dict], laid: list[dict]) -> int:
    """How many strokes with points are not among the ``laid`` ones: those of a material outside the island's set."""
    return sum(1 for stroke in strokes if stroke.get("points")) - len(laid)


def apply_paint(
    island_file, gamedata_folder: SGamedata, strokes: list[dict], order: dict[int, int], out: dict[str, bytes]
) -> dict:
    """Paint the strokes of the island's own materials into every material map it has (117: the four levels of
    ``materialmaps/`` and the copy in rd3d; 1800: rd3d) and mark the materials used where they were painted."""
    laid = painted_strokes(strokes, order)
    report = {"strokes": len(laid), "other materials": other_materials(strokes, laid), "blocks": 0}
    if not laid:
        return report
    terrain = island_file.rd3d.path("Terrain")
    for name in [
        candidate
        for candidate in gamedata_folder.names
        if candidate.startswith("materialmaps/") and candidate.endswith("/matid.dds")
    ]:
        alpha_name = name.replace("matid.dds", "matalpha.dds")
        ids_dds, alpha_dds = gamedata_folder.read(name), gamedata_folder.read(alpha_name)
        ids, alphas = dds_u32(ids_dds), dds_u32(alpha_dds)
        done = paint_map(ids, alphas, laid, alphas.shape[0] // island_file.size, order)
        if done:
            out[name], out[alpha_name] = replace_dds_pixels(ids_dds, ids), replace_dds_pixels(alpha_dds, alphas)
            report["blocks"] += done
    ids_tag, alpha_tag = terrain.find("MaterialIDs2"), terrain.find("MaterialAlphas2")
    if ids_tag is not None and alpha_tag is not None:
        alpha_side = int(round((len(alpha_tag.data) // 4) ** 0.5))
        ids_side = int(round((len(ids_tag.data) // 4) ** 0.5))
        ids = np.frombuffer(ids_tag.data, "<u4").reshape(ids_side, ids_side).copy()
        alphas = np.frombuffer(alpha_tag.data, "<u4").reshape(alpha_side, alpha_side).copy()
        done = paint_map(ids, alphas, laid, max(1, alpha_side // island_file.size), order)
        if done:
            set_bytes(ids_tag, ids)
            set_bytes(alpha_tag, alphas)
            report["blocks"] += done
    _mark_used(terrain, gamedata_folder, laid, out)
    return report


def stroke_box(strokes: list[dict]) -> tuple[float, float, float, float]:
    """The tiles (min_x, min_z, max_x, max_z) the strokes reach."""
    xs = [
        point[0] + sign * float(stroke.get("radius", 4))
        for stroke in strokes
        for point in stroke["points"]
        for sign in (-1, 1)
    ]
    zs = [
        point[1] + sign * float(stroke.get("radius", 4))
        for stroke in strokes
        for point in stroke["points"]
        for sign in (-1, 1)
    ]
    return min(xs), min(zs), max(xs), max(zs)


def paint_map(ids, alphas, strokes: list[dict], texels_per_tile: int, order: dict[int, int]) -> int:
    """Paint ``strokes`` into one material map (``ids`` per block, ``alphas`` per texel, changed in place): the
    ground under them is read as the game layers it, painted, encoded again by the island build's encoder and put back
    block by block. Returns the blocks changed."""
    texel_count, block = alphas.shape[0], alphas.shape[0] // ids.shape[0]
    box_min_x, box_min_z, box_max_x, box_max_z = stroke_box(strokes)
    snapped = [max(0, int(coordinate * texels_per_tile) // block * block) for coordinate in (box_min_x, box_min_z)]
    side = max(
        int(np.ceil(box_max_x * texels_per_tile)) - snapped[0], int(np.ceil(box_max_z * texels_per_tile)) - snapped[1]
    )
    side = min(texel_count, -(-max(side, block) // block) * block)
    start_x, start_z = (min(coordinate, texel_count - side) for coordinate in snapped)
    if (
        box_max_x < 0
        or box_max_z < 0
        or box_min_x * texels_per_tile >= texel_count
        or box_min_z * texels_per_tile >= texel_count
    ):
        return 0
    shift = [
        {
            **stroke,
            "points": [
                [point[0] - start_x / texels_per_tile, point[1] - start_z / texels_per_tile]
                for point in stroke["points"]
            ],
        }
        for stroke in strokes
    ]
    cover = np.zeros((side, side), bool)
    for stroke in shift:
        cover |= stroke_cover(stroke, side, texels_per_tile) > 0
    blocks = cover.reshape(side // block, block, side // block, block).any(axis=(1, 3))
    if not blocks.any():
        return 0
    block_slice = np.s_[start_z // block : (start_z + side) // block, start_x // block : (start_x + side) // block]
    texel_slice = np.s_[start_z : start_z + side, start_x : start_x + side]
    materials, weights = layered_weights(ids[block_slice], alphas[texel_slice], order)
    materials, weights = painted_layers(weights, materials, shift)
    weights = paint_strokes(weights, materials, shift, texels_per_tile)
    new_ids, new_alphas, _ = CPainted(materials, weights.astype(np.float32), order).encode_materials(
        texels_per_tile, {}, block
    )
    ids[block_slice][blocks] = new_ids[blocks]
    texels = np.repeat(np.repeat(blocks, block, 0), block, 1)
    alphas[texel_slice][texels] = new_alphas[texels]
    return int(blocks.sum())


def layered_weights(ids, alphas, order: dict[int, int]) -> tuple[list[int], np.ndarray]:
    """The visible weight of each material per texel as the game lays them (rendering/terrain-materials 1.4): a
    material's coverage is its alphas summed over the slots, the materials go down in the set's order, and the
    first one drawn takes what the others leave (``islandground.slot_weights``, summed per material)."""
    block = alphas.shape[0] // ids.shape[0]
    texel_ids = np.repeat(np.repeat(ids, block, 0), block, 1)
    slot_ids = np.stack([(texel_ids >> np.uint32(5 * slot)) & np.uint32(31) for slot in range(SLOTS)]).astype(np.int64)
    slot_alphas = np.stack(
        [((alphas >> np.uint32(5 * slot)) & np.uint32(31)).astype(np.float32) / 31 for slot in range(SLOTS)]
    )
    bare = ~(slot_alphas > 0).any(axis=0)  # no alpha at all: the first slot's material stands
    materials = sorted(
        {int(material) for material in np.unique(slot_ids[slot_alphas > 0])}
        | {int(material) for material in np.unique(slot_ids[0][bare])}
    )
    weights = slot_weights(slot_ids, slot_alphas, order)
    return materials, np.stack([np.where(slot_ids == material, weights, 0).sum(axis=0) for material in materials])


def _mark_used(terrain, gamedata_folder: SGamedata, strokes: list[dict], out: dict[str, bytes]) -> None:
    """Set the painted materials' bits in the used-material masks (rd3d and the meshes' nodes under the strokes)."""
    mask = used_mask(strokes)
    used_material_bits = terrain.find("UsedMaterialBits")
    if used_material_bits is not None and len(used_material_bits.data) % 4 == 0:
        used_material_bits.data = (
            (np.frombuffer(used_material_bits.data, "<u4") | np.uint32(mask)).astype("<u4").tobytes()
        )
    box = stroke_box(strokes)
    for name in gamedata_folder.names:
        if not (name.startswith("meshes/") and name.endswith(".tmc")):
            continue
        level, file = name.split("/")[1:3]
        node_x, node_z = (int(part) for part in file[:-4].split("x"))
        node = 2 ** int(level)
        if not meets(
            box,
            node_x * TMC_NODES * node,
            node_z * TMC_NODES * node,
            (node_x + 1) * TMC_NODES * node,
            (node_z + 1) * TMC_NODES * node,
        ):
            continue
        raw = _read(gamedata_folder, out, name)
        doc, changed = filedb.read(raw), False
        for k, node_tag in enumerate(
            child for child in doc.path("Meshes/map").children if isinstance(child, filedb.STag)
        ):
            x, z = (node_x * TMC_NODES + k % TMC_NODES) * node, (node_z * TMC_NODES + k // TMC_NODES) * node
            bits = node_tag.find("UsedMaterialBits")
            if bits is None or len(bits.data) != 4 or not meets(box, x, z, x + node, z + node):
                continue
            value = struct.unpack("<I", bits.data)[0]
            if value | mask != value:
                bits.data = struct.pack("<I", value | mask)
                changed = True
        if changed:
            out[name] = filedb.write(doc, *filedb.name_ids(raw), version=filedb.version(raw))


# Rivers and lakes
def _water_objects(island_file) -> tuple[list[filedb.STag], list[filedb.STag]]:
    """The island's river and lake editor objects, in the order ``water_network`` numbers them."""
    tags = (
        tag
        for _area, doc, _ids in island_file._areas_data()
        for tag in (lambda objects: objects.children if objects is not None else [])(
            a7mread.area_objects(doc, "EditorObject")
        )
    )
    return water_objects(tags)


def water_objects(tags) -> tuple[list[filedb.STag], list[filedb.STag]]:
    """The rivers and the lakes among editor object tags (those with a Bezier path), each in file order."""
    rivers, lakes = [], []
    for tag in tags:
        if isinstance(tag, filedb.STag) and tag.path("BezierPath/Path/BezierCurve") is not None:
            if tag.find("River") is not None:
                rivers.append(tag)
            elif tag.find("Lake") is not None:
                lakes.append(tag)
    return rivers, lakes


def coast_objects(tags) -> list[filedb.STag]:
    """The coast lines among editor object tags (a Bezier path with a ``CoastLine`` block), in file order."""
    return [
        tag
        for tag in tags
        if isinstance(tag, filedb.STag)
        and tag.find("CoastLine") is not None
        and tag.path("BezierPath/Path/BezierCurve")
    ]


def water_network(splines) -> dict | None:
    """The island's own rivers, lakes and coast lines (``CIslandView.splines``) as the editor's water tool edits them,
    numbered in the island's order (a coast line by its x, z points); None for an island with none of them."""
    rivers = [
        {"points": [list(point) for point in points], "widths": list(widths)}
        for kind, points, widths in splines
        if kind == "River"
    ]
    lakes = [
        {"points": [[point[0], point[2]] for point in points], "level": float(points[0][1]), "damHeight": 0.0}
        for kind, points, _widths in splines
        if kind == "Lake" and points
    ]
    coasts = [
        {"points": [[point[0], point[2]] for point in points]}
        for kind, points, _widths in splines
        if kind == "CoastLine" and points
    ]
    return {"rivers": rivers, "lakes": lakes, "coasts": coasts} if rivers or lakes or coasts else None


def apply_water(island_file, edits: dict, objects=None, place=None) -> dict:
    """Write the edited rivers (their points, widths and flow speeds, the curve through them), lake levels and coast
    lines (``edits["coast"]``: ``"k"`` -> ``points`` x, z) into the island's editor objects; the gameplay river grid
    and the ground under the water stay as they are. ``objects``: the (rivers, lakes, coast lines) when the island
    does not keep them per area; ``place(tag, (x, y, z))`` sets an object's position where it is not three floats
    (2205)."""
    rivers, lakes, coasts = (
        objects if objects is not None else (*_water_objects(island_file), _coast_objects(island_file))
    )
    place = place or _place_floats
    report = {"rivers": 0, "lakes": 0, "unmatched": []}
    for k, river in sorted(edits.get("rivers", {}).items()):
        if not str(k).isdigit() or int(k) >= len(rivers) or not river_edit_ok(river):
            report["unmatched"].append(tr("islands.river_k", k=k))
            continue
        _set_river(
            rivers[int(k)],
            [tuple(map(float, point)) for point in river["points"]],
            [float(width) for width in river["widths"]],
            place,
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
        _set_coast(coasts[int(k)], [tuple(map(float, point)) for point in line["points"]], place)
        report["coasts"] = report.get("coasts", 0) + 1
    return report


def river_edit_ok(change: dict) -> bool:
    """A river edit holds a width for each of at least two points (a shorter river would lose its line)."""
    points, widths = change.get("points"), change.get("widths")
    return points is not None and widths is not None and len(points) == len(widths) >= 2


def _lake_level(tag: filedb.STag) -> float:
    """A lake's water level: the height of its first outline point."""
    return struct.unpack("<3f", tag.path("BezierPath/Path/BezierCurve").children[0].find("p").data)[1]


def fill_lake(tag: filedb.STag, points: list[tuple[float, float, float]], place) -> None:
    """A lake's outline through new (x, level, z) points: straight sides, as a lake of a built island has them; the ground
    under it is not carved (shape the basin with the height brush)."""
    serial = struct.unpack_from("<I", tag.find("ID").data)[0]
    fill_spline(tag, SSpline("Lake", points), serial, place or _place_floats)


def _coast_objects(island_file) -> list[filedb.STag]:
    """The island's coast line editor objects, in the order ``water_network`` numbers them."""
    return coast_objects(
        tag
        for _area, doc, _ids in island_file._areas_data()
        for tag in (lambda objects: objects.children if objects is not None else [])(
            a7mread.area_objects(doc, "EditorObject")
        )
    )


def _set_coast(tag: filedb.STag, points: list[tuple[float, float]], place) -> None:
    """A coast line's curve through new (x, z) points: each keeps the height of the old point nearest to it, and its
    normal (``d``, the side the coast faces) turns with the line, keeping the sense the old one had."""
    old = [point for point in tag.path("BezierPath/Path/BezierCurve").children if isinstance(point, filedb.STag)]
    old_points = [struct.unpack("<3f", point.find("p").data) for point in old]
    old_normals = [
        struct.unpack("<2f", point.find("d").data) if point.find("d") is not None else (1.0, 0.0) for point in old
    ]
    near, normals = turned_normals([(position[0], position[2]) for position in old_points], old_normals, points)
    lifted = [(x, float(old_points[nearest][1]), z) for (x, z), nearest in zip(points, near, strict=True)]
    serial = struct.unpack_from("<I", tag.find("ID").data)[0]
    fill_spline(tag, SSpline("CoastLine", lifted, normals=normals), serial, place or _place_floats)


def _place_floats(tag: filedb.STag, point) -> None:
    """Set an object's position; an axis given as None stays."""
    position = tag.find("Position")
    old = struct.unpack("<3f", position.data)
    position.data = struct.pack(
        "<3f", *(old_value if new_value is None else new_value for new_value, old_value in zip(point, old, strict=True))
    )


def _set_river(tag: filedb.STag, points: list[tuple], widths: list[float], place) -> None:
    """A river's curve through new points (``fill_spline``), each point flowing as the old point nearest to it."""
    old = [point for point in tag.path("BezierPath/Path/BezierCurve").children if isinstance(point, filedb.STag)]
    where = np.array([struct.unpack("<3f", point.find("p").data) for point in old])
    speeds = []
    for point in points:
        near = old[int(np.argmin(((where[:, [0, 2]] - (point[0], point[2])) ** 2).sum(axis=1)))]
        flow_tag = near.find("u0")
        speeds.append(struct.unpack("<3f", flow_tag.data) if flow_tag is not None else (1.0, 1.0, 1.0))
    serial = struct.unpack_from("<I", tag.find("ID").data)[0]
    fill_spline(tag, SSpline("River", points, widths=widths, speeds=speeds), serial, place)


def _set_lake(tag: filedb.STag, level: float, place) -> None:
    """A lake's water at ``level``: every point of its outline, its place and its bounds."""
    for point in tag.path("BezierPath/Path/BezierCurve").children:
        if isinstance(point, filedb.STag) and point.find("p") is not None:
            x, _, z = struct.unpack("<3f", point.find("p").data)
            point.find("p").data = struct.pack("<3f", x, level, z)
    place(tag, (None, level, None))
    path = tag.path("BezierPath/Path")
    for name in ("Minimum", "Maximum"):
        x, _, z = struct.unpack("<3f", path.find(name).data)
        path.find(name).data = struct.pack("<3f", x, level, z)


def unmatched(report: dict) -> list[str]:
    """What the ground edits could not place, for the edit report."""
    lost = report.get("paint", {}).get("other materials", 0)
    paint = [f"{lost} painted strokes of a material outside the island's set"] if lost else []
    grids = [
        line for key in ("water", "ford", "environment", "tint") for line in report.get(key, {}).get("unmatched", [])
    ]
    return grids + paint
