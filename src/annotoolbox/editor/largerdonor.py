"""An island carried onto a donor with a larger grid than its own: the source sits in the donor's middle and the rest is
sea. For the sizes one game has no pool island of (117's 448 to 960 tiles in 1800; 1800's 64 and 128 in 117).

``place_for`` says where the source lies, ``choose`` picks the donor whose active area holds the source's land,
``embed_*`` write the source's heights, grids, sea maps and tint pages into the donor's window, ``shift_object``
and ``shift_props`` move what stands on the island. Rules and limits: docs/island-regions.md "A larger donor".
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from ..formats import filedb
from ..infra.i18n import tr
from ..islands import objectpath, tintpages
from ..islands.islandgrid import bits_array, write_bits
from . import groundedit, regionconvert

ALIGN = 32  # tiles: the source's offset is a multiple, so every page, node and map block of it stays whole
TINT_SIDE = 512  # texels of a tint page's side, both games (island-regions "Tint pages")
EDGE = 8.0  # tiles along the donor's grid edge blended back to the donor's own sea bed
COVER = 0.99  # the share of the source's land the donor's active area must hold
GRIDS = (  # bit grids (x, y, bits) of the donor that take the source's, one bit a tile
    "WorldManager/Water",
    "WorldManager/RiverGrid",
    "WorldManager/FordGrid",
    "RegrowManager/TreeMap",
)


@dataclass(frozen=True)
class SPlace:
    """Where a source island of ``size_tiles`` tiles lies in a donor of ``grid`` tiles (``x``, ``z``: its corner, tiles)."""

    size_tiles: int
    grid: int
    x: int = 0
    z: int = 0

    @property
    def padded(self) -> bool:
        return self.grid != self.size_tiles

    def window(self, cells_per_tile: float = 1.0) -> tuple[slice, slice]:
        """The source's cells in a map of ``per`` cells a tile over the donor's grid (rows z, columns x)."""
        z0, x0, cell_count = (
            round(self.z * cells_per_tile),
            round(self.x * cells_per_tile),
            round(self.size_tiles * cells_per_tile),
        )
        return slice(z0, z0 + cell_count), slice(x0, x0 + cell_count)

    def shift(self) -> tuple[int, int]:
        return self.x, self.z


def place_for(size_tiles: int, grid: int) -> SPlace:
    """The source in the middle of the donor, its corner on a multiple of ``ALIGN`` tiles."""
    offset_tiles = (grid - size_tiles) // 2 // ALIGN * ALIGN
    return SPlace(size_tiles, grid, offset_tiles, offset_tiles)


def uncovered(land: np.ndarray, active: np.ndarray, place: SPlace) -> float:
    """The share of the source's land tiles (``land``, rows z) that lie in a cell the donor draws (``active``: its
    active map, cells of grid / cells tiles)."""
    cells = active.shape[0]
    tiles_per_cell = place.grid // cells
    zs, xs = np.nonzero(land)
    if not len(zs):
        return 0.0
    hit = active[(zs + place.z) // tiles_per_cell, (xs + place.x) // tiles_per_cell]
    return 1.0 - float(hit.mean())


def choose(
    pool: list[tuple[int, int, str]], size_tiles: int, land: np.ndarray, active_of: Callable[[str], np.ndarray]
) -> tuple[str, SPlace]:
    """The donor for a source of ``size`` tiles with the land ``land``: of the pool's (grid, active area, path) the
    smallest grid above ``size`` (then the nearest active area) whose active map holds the land (``COVER``)."""
    best = None
    for grid, _area, path in sorted(pool):
        if grid <= size_tiles:
            continue
        place = place_for(size_tiles, grid)
        miss = uncovered(land, active_of(path), place)
        if 1 - miss >= COVER:
            return path, place
        best = (miss, grid) if best is None or miss < best[0] else best
    if best is None:
        raise ValueError(tr("islands.no_pool_island_larger_tiles", size=size_tiles))
    raise ValueError(
        tr(
            "islands.no_larger_pool_island_draws",
            share=round(best[0] * 100, 1),
            grid=best[1],
        )
    )


def corner_targets(source_corners: np.ndarray, donor_corners: np.ndarray, place: SPlace) -> np.ndarray:
    """The donor's corner heights with the source in it: the source's heights inside, its edge sea bed carried on
    outside and blended back to the donor's own sea bed over the last ``EDGE`` tiles of the grid (not where the donor's
    edge is land: a continental island's)."""
    grid_tiles = place.grid
    rest = grid_tiles - place.size_tiles
    wide = np.pad(source_corners, ((place.z, rest - place.z), (place.x, rest - place.x)), mode="edge")
    row_index, column_index = np.mgrid[0 : grid_tiles + 1, 0 : grid_tiles + 1]
    keep = np.clip(
        1
        - np.minimum(np.minimum(column_index, grid_tiles - column_index), np.minimum(row_index, grid_tiles - row_index))
        / EDGE,
        0,
        1,
    )
    out = wide * (1 - keep) + np.minimum(donor_corners, wide) * keep  # the donor's own land is never kept
    inside = np.zeros_like(out, bool)
    inside[place.z : place.z + place.size_tiles + 1, place.x : place.x + place.size_tiles + 1] = True
    return np.where(inside, wide, out)  # the source's own ground is never blended


def embed_heights(source, donor, donor_gamedata: groundedit.SGamedata, place: SPlace, out: dict[str, bytes]) -> dict:
    """The source's corner heights written into the donor by an ordinary height edit (``groundedit.apply_heights``:
    the heightmaps, node bounds, land, sea depth, pages and stored meshes the edit reaches)."""
    source_heights = groundedit.coarse_heights(source)[::2, ::2]
    old = groundedit.coarse_heights(donor)[::2, ::2]
    deltas = corner_targets(source_heights, old, place) - old
    return groundedit.apply_heights(donor, donor_gamedata, deltas, out) if np.abs(deltas).max() > 0 else {}


def embed_bits(donor_gm: filedb.STag, source_gm: filedb.STag, place: SPlace) -> list[str]:
    """Each bit grid of ``GRIDS`` both have: empty over the donor's grid, the source's bits in its window."""
    done = []
    for path in GRIDS:
        donor_tag, source_tag = donor_gm.path(path), source_gm.path(path)
        if donor_tag is None or source_tag is None:
            continue
        grid = np.zeros((place.grid, place.grid), np.uint8)
        grid[place.window()] = bits_array(source_tag)
        write_bits(donor_tag, grid.tolist())
        done.append(path)
    return done


def embed_cells(
    donor_tag: filedb.STag | None, source_tag: filedb.STag | None, place: SPlace, key: str, keep: bool = False
) -> bool:
    """A map node of cells (``width``, ``height``, and ``key`` the bytes: ``map`` or ``val``) with the source's cells in
    its window; the rest is the donor's own (``keep``) or zero."""
    if donor_tag is None or source_tag is None or donor_tag.find(key) is None or source_tag.find(key) is None:
        return False
    side = "x" if key == "val" else "width"
    donor_side, source_side = donor_tag.find(side).u32(), source_tag.find(side).u32()
    cell_bytes = len(donor_tag.find(key).data) // (donor_side * donor_side)
    cells_per_tile = donor_side / place.grid
    cells = (
        np.frombuffer(donor_tag.find(key).data, np.uint8).reshape(donor_side, donor_side, cell_bytes).copy()
        if keep
        else np.zeros((donor_side, donor_side, cell_bytes), np.uint8)
    )
    cells[place.window(cells_per_tile)] = np.frombuffer(source_tag.find(key).data, np.uint8).reshape(
        source_side, source_side, cell_bytes
    )
    donor_tag.find(key).data = cells.tobytes()
    return True


def embed_sea(source, donor, place: SPlace) -> list[str]:
    """The sea maps: ``WaterInfos`` keeps the donor's depth byte (the height edit wrote it) and takes the source's
    cells in its window, ``WaterFlow`` is the source's in an empty map, ``GritMap2`` the donor's with the source's."""
    done = []
    for path, keep in (
        ("WaterInfos", True),
        ("WaterFlow", False),
        ("Terrain/GritMap2", True),
    ):
        if embed_cells(donor.rd3d.path(path), source.rd3d.path(path), place, "map", keep):
            done.append(path)
    return done


def embed_environment(source, donor, place: SPlace) -> bool:
    return embed_cells(
        donor.game_session_manager.path("WorldManager/EnvironmentGrid/EnvironmentGRid"),
        source.game_session_manager.path("WorldManager/EnvironmentGrid/EnvironmentGRid"),
        place,
        "val",
    )


def shift_rect(
    island_info: filedb.STag, gm: filedb.STag, source_info: filedb.STag, source_gm: filedb.STag, place: SPlace
) -> None:
    """The source's active rectangle and playable area, moved to where the source lies (tiles x0, z0, x1, z1)."""
    for target_tag, source_tag in (
        (island_info.find("ActiveMapRect"), source_info.find("ActiveMapRect")),
        (gm.path("SessionSettings/PlayableArea"), source_gm.path("SessionSettings/PlayableArea")),
    ):
        if target_tag is not None and source_tag is not None and len(source_tag.data) == 16:
            x0, z0, x1, z1 = struct.unpack("<4i", source_tag.data)
            target_tag.data = struct.pack("<4i", x0 + place.x, z0 + place.z, x1 + place.x, z1 + place.z)


def shift_object(placed: filedb.STag, place: SPlace) -> None:
    """A placed object moved with the source (its position, and its path: a river's or a work area's points)."""
    position = placed.find("Position")
    if position is None or len(position.data) != 12:
        return
    x, y, z = struct.unpack("<3f", position.data)
    new = (x + place.x, y, z + place.z)
    objectpath.follow(placed, (x, y, z), new, 0.0)
    position.data = struct.pack("<3f", *new)


def shift_props(rd: filedb.STag, place: SPlace) -> int:
    """Every baked prop of ``rd``'s ``PropGrid`` moved with the source; returns how many."""
    instances = rd.path("PropGrid/Instances")
    count = 0
    for prop in instances.children if instances is not None else []:
        position = prop.find("Position") if isinstance(prop, filedb.STag) else None
        if position is not None and len(position.data) == 12:
            x, y, z = struct.unpack("<3f", position.data)
            position.data = struct.pack("<3f", x + place.x, y, z + place.z)
            count += 1
    return count


def source_land(source) -> np.ndarray:
    """The source's land tiles (``WorldManager/Water`` bits, rows z)."""
    return bits_array(source.game_session_manager.path("WorldManager/Water"))


def active_cells(island) -> np.ndarray:
    """An island's active map as a boolean array (rows z)."""
    return bits_array(island.rd3d.path("Terrain/ActiveMap"))


def _blocks(raw: bytes) -> tuple[int, np.ndarray] | None:
    """(header size, the BC1 blocks as rows by columns of 8 bytes) of a 512 x 512 tint page, None for another file."""
    header = 148 if raw[84:88] == b"DX10" else 128
    if len(raw) < header + TINT_SIDE * TINT_SIDE // 2 or struct.unpack_from("<II", raw, 12) != (TINT_SIDE, TINT_SIDE):
        return None
    block_count = TINT_SIDE // 4
    return header, np.frombuffer(raw, np.uint8, block_count * block_count * 8, header).reshape(
        block_count, block_count, 8
    )


def embed_tints(
    source_gamedata: groundedit.SGamedata, donor_gamedata: groundedit.SGamedata, place: SPlace
) -> dict[str, bytes]:
    """The donor's tint pages with the source's tints in the window, block for block: the offset is a multiple of
    ``ALIGN`` tiles, so at every level it moves whole BC1 blocks and nothing is encoded again (island-regions "A larger
    donor"). A block whose source page the source lacks keeps the donor's."""
    found = {
        match: page
        for match, page in ((tintpages.PAGE.search(page_name), page_name) for page_name in source_gamedata.names)
        if match
    }
    source = {tuple(int(group) for group in match.groups()): name for match, name in found.items()}
    out: dict[str, bytes] = {}
    for name in donor_gamedata.names:
        match = tintpages.PAGE.search(name)
        if match is None:
            continue
        level, px, pz = (int(group) for group in match.groups())
        span = tintpages.NODE_TILES << level
        x0, z0 = px * span - place.x, pz * span - place.z  # the page's corner in the source's tiles
        if x0 >= place.size_tiles or z0 >= place.size_tiles or x0 + span <= 0 or z0 + span <= 0:
            continue
        raw = donor_gamedata.read(name)
        parsed = _blocks(raw)
        if parsed is None:
            continue
        header, blocks = parsed[0], parsed[1].copy()
        if _graft_blocks(blocks, source_gamedata, source, (level, x0, z0, span), place.size_tiles):
            out[name] = raw[:header] + blocks.tobytes() + raw[header + blocks.size :]
    return out


def _graft_blocks(
    blocks: np.ndarray, source_gamedata, source: dict, page: tuple[int, int, int, int], size_tiles: int
) -> bool:
    """The source's blocks laid into one donor page's ``blocks`` (``page``: level, its corner in source tiles, its
    span); whether any came."""
    level, x0, z0, span = page
    block_count = blocks.shape[0]
    per = block_count / span  # blocks a tile
    changed = False
    for sz in range(max(z0, 0) // span, min(z0 + span, size_tiles) // span + 1):
        for sx in range(max(x0, 0) // span, min(x0 + span, size_tiles) // span + 1):
            name = source.get((level, sx, sz))
            parsed = _blocks(source_gamedata.read(name)) if name else None
            if parsed is None:
                continue
            theirs = parsed[1]
            # the source page's tiles that lie in this donor page and inside the source island
            tx0, tz0 = max(sx * span, x0, 0), max(sz * span, z0, 0)
            tx1, tz1 = min((sx + 1) * span, x0 + span, size_tiles), min((sz + 1) * span, z0 + span, size_tiles)
            if tx1 <= tx0 or tz1 <= tz0:
                continue
            donor_cells = (
                slice(round((tz0 - z0) * per), round((tz1 - z0) * per)),
                slice(round((tx0 - x0) * per), round((tx1 - x0) * per)),
            )
            source_cells = (slice(round((tz0 - sz * span) * per), round((tz1 - sz * span) * per)),
                 slice(round((tx0 - sx * span) * per), round((tx1 - sx * span) * per)))  # fmt: skip
            blocks[donor_cells] = theirs[source_cells]
            changed = True
    return changed


def mark_meshes(files: dict[str, bytes], used: int) -> int:
    """The donor's stored meshes with every node's used-material mask set to ``used`` (the ground of the island now
    on them), in ``files``; returns the meshes changed."""
    count = 0
    for name in [file_name for file_name in files if file_name.startswith("meshes/") and file_name.endswith(".tmc")]:
        new = regionconvert.rewrite_mesh_bits(files[name], lambda _mask: used)
        if new is not None:
            files[name] = new
            count += 1
    return count
