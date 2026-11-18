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
class Place:
    """Where a source island of ``size`` tiles lies in a donor of ``grid`` tiles (``x``, ``z``: its corner, tiles)."""

    size: int
    grid: int
    x: int = 0
    z: int = 0

    @property
    def padded(self) -> bool:
        return self.grid != self.size

    def window(self, per: float = 1.0) -> tuple[slice, slice]:
        """The source's cells in a map of ``per`` cells a tile over the donor's grid (rows z, columns x)."""
        z0, x0, n = round(self.z * per), round(self.x * per), round(self.size * per)
        return slice(z0, z0 + n), slice(x0, x0 + n)

    def shift(self) -> tuple[int, int]:
        return self.x, self.z


def place_for(size: int, grid: int) -> Place:
    """The source in the middle of the donor, its corner on a multiple of ``ALIGN`` tiles."""
    off = (grid - size) // 2 // ALIGN * ALIGN
    return Place(size, grid, off, off)


def uncovered(land: np.ndarray, active: np.ndarray, place: Place) -> float:
    """The share of the source's land tiles (``land``, rows z) that lie in a cell the donor draws (``active``: its
    active map, cells of grid / cells tiles)."""
    cells = active.shape[0]
    per = place.grid // cells
    zs, xs = np.nonzero(land)
    if not len(zs):
        return 0.0
    hit = active[(zs + place.z) // per, (xs + place.x) // per]
    return 1.0 - float(hit.mean())


def choose(
    pool: list[tuple[int, int, str]], size: int, land: np.ndarray, active_of: Callable[[str], np.ndarray]
) -> tuple[str, Place]:
    """The donor for a source of ``size`` tiles with the land ``land``: of the pool's (grid, active area, path) the
    smallest grid above ``size`` (then the nearest active area) whose active map holds the land (``COVER``)."""
    best = None
    for grid, _area, path in sorted(pool):
        if grid <= size:
            continue
        place = place_for(size, grid)
        miss = uncovered(land, active_of(path), place)
        if 1 - miss >= COVER:
            return path, place
        best = (miss, grid) if best is None or miss < best[0] else best
    if best is None:
        raise ValueError(tr("islands.no_pool_island_larger_tiles", size=size))
    raise ValueError(
        tr(
            "islands.no_larger_pool_island_draws",
            share=round(best[0] * 100, 1),
            grid=best[1],
        )
    )


def corner_targets(source_corners: np.ndarray, donor_corners: np.ndarray, place: Place) -> np.ndarray:
    """The donor's corner heights with the source in it: the source's heights inside, its edge sea bed carried on
    outside and blended back to the donor's own sea bed over the last ``EDGE`` tiles of the grid (not where the donor's
    edge is land: a continental island's)."""
    n = place.grid
    rest = n - place.size
    wide = np.pad(source_corners, ((place.z, rest - place.z), (place.x, rest - place.x)), mode="edge")
    zz, xx = np.mgrid[0 : n + 1, 0 : n + 1]
    keep = np.clip(1 - np.minimum(np.minimum(xx, n - xx), np.minimum(zz, n - zz)) / EDGE, 0, 1)
    out = wide * (1 - keep) + np.minimum(donor_corners, wide) * keep  # the donor's own land is never kept
    inside = np.zeros_like(out, bool)
    inside[place.z : place.z + place.size + 1, place.x : place.x + place.size + 1] = True
    return np.where(inside, wide, out)  # the source's own ground is never blended


def embed_heights(source, donor, dgd: groundedit.Gamedata, place: Place, out: dict[str, bytes]) -> dict:
    """The source's corner heights written into the donor by an ordinary height edit (``groundedit.apply_heights``:
    the heightmaps, node bounds, land, sea depth, pages and stored meshes the edit reaches)."""
    src = groundedit.coarse_heights(source)[::2, ::2]
    old = groundedit.coarse_heights(donor)[::2, ::2]
    deltas = corner_targets(src, old, place) - old
    return groundedit.apply_heights(donor, dgd, deltas, out) if np.abs(deltas).max() > 0 else {}


def embed_bits(donor_gm: filedb.Tag, source_gm: filedb.Tag, place: Place) -> list[str]:
    """Each bit grid of ``GRIDS`` both have: empty over the donor's grid, the source's bits in its window."""
    done = []
    for path in GRIDS:
        dst, src = donor_gm.path(path), source_gm.path(path)
        if dst is None or src is None:
            continue
        grid = np.zeros((place.grid, place.grid), np.uint8)
        grid[place.window()] = bits_array(src)
        write_bits(dst, grid.tolist())
        done.append(path)
    return done


def embed_cells(dst: filedb.Tag | None, src: filedb.Tag | None, place: Place, key: str, keep: bool = False) -> bool:
    """A map node of cells (``width``, ``height``, and ``key`` the bytes: ``map`` or ``val``) with the source's cells in
    its window; the rest is the donor's own (``keep``) or zero."""
    if dst is None or src is None or dst.find(key) is None or src.find(key) is None:
        return False
    side = "x" if key == "val" else "width"
    n, m = dst.find(side).u32(), src.find(side).u32()
    width = len(dst.find(key).data) // (n * n)
    per = n / place.grid
    cells = (
        np.frombuffer(dst.find(key).data, np.uint8).reshape(n, n, width).copy()
        if keep
        else np.zeros((n, n, width), np.uint8)
    )
    cells[place.window(per)] = np.frombuffer(src.find(key).data, np.uint8).reshape(m, m, width)
    dst.find(key).data = cells.tobytes()
    return True


def embed_sea(source, donor, place: Place) -> list[str]:
    """The sea maps: ``WaterInfos`` keeps the donor's depth byte (the height edit wrote it) and takes the source's
    cells in its window, ``WaterFlow`` is the source's in an empty map, ``GritMap2`` the donor's with the source's."""
    done = []
    for path, keep in (
        ("WaterInfos", True),
        ("WaterFlow", False),
        ("Terrain/GritMap2", True),
    ):
        if embed_cells(donor.rd.path(path), source.rd.path(path), place, "map", keep):
            done.append(path)
    return done


def embed_environment(source, donor, place: Place) -> bool:
    return embed_cells(
        donor.gm.path("WorldManager/EnvironmentGrid/EnvironmentGRid"),
        source.gm.path("WorldManager/EnvironmentGrid/EnvironmentGRid"),
        place,
        "val",
    )


def shift_rect(info: filedb.Tag, gm: filedb.Tag, source_info: filedb.Tag, source_gm: filedb.Tag, place: Place) -> None:
    """The source's active rectangle and playable area, moved to where the source lies (tiles x0, z0, x1, z1)."""
    for dst, src in (
        (info.find("ActiveMapRect"), source_info.find("ActiveMapRect")),
        (gm.path("SessionSettings/PlayableArea"), source_gm.path("SessionSettings/PlayableArea")),
    ):
        if dst is not None and src is not None and len(src.data) == 16:
            x0, z0, x1, z1 = struct.unpack("<4i", src.data)
            dst.data = struct.pack("<4i", x0 + place.x, z0 + place.z, x1 + place.x, z1 + place.z)


def shift_object(o: filedb.Tag, place: Place) -> None:
    """A placed object moved with the source (its position, and its path: a river's or a work area's points)."""
    pos = o.find("Position")
    if pos is None or len(pos.data) != 12:
        return
    x, y, z = struct.unpack("<3f", pos.data)
    new = (x + place.x, y, z + place.z)
    objectpath.follow(o, (x, y, z), new, 0.0)
    pos.data = struct.pack("<3f", *new)


def shift_props(rd: filedb.Tag, place: Place) -> int:
    """Every baked prop of ``rd``'s ``PropGrid`` moved with the source; returns how many."""
    inst = rd.path("PropGrid/Instances")
    count = 0
    for p in inst.children if inst is not None else []:
        pos = p.find("Position") if isinstance(p, filedb.Tag) else None
        if pos is not None and len(pos.data) == 12:
            x, y, z = struct.unpack("<3f", pos.data)
            pos.data = struct.pack("<3f", x + place.x, y, z + place.z)
            count += 1
    return count


def source_land(source) -> np.ndarray:
    """The source's land tiles (``WorldManager/Water`` bits, rows z)."""
    return bits_array(source.gm.path("WorldManager/Water"))


def active_cells(island) -> np.ndarray:
    """An island's active map as a boolean array (rows z)."""
    return bits_array(island.rd.path("Terrain/ActiveMap"))


def _blocks(raw: bytes) -> tuple[int, np.ndarray] | None:
    """(header size, the BC1 blocks as rows by columns of 8 bytes) of a 512 x 512 tint page, None for another file."""
    header = 148 if raw[84:88] == b"DX10" else 128
    if len(raw) < header + TINT_SIDE * TINT_SIDE // 2 or struct.unpack_from("<II", raw, 12) != (TINT_SIDE, TINT_SIDE):
        return None
    n = TINT_SIDE // 4
    return header, np.frombuffer(raw, np.uint8, n * n * 8, header).reshape(n, n, 8)


def embed_tints(sgd: groundedit.Gamedata, dgd: groundedit.Gamedata, place: Place) -> dict[str, bytes]:
    """The donor's tint pages with the source's tints in the window, block for block: the offset is a multiple of
    ``ALIGN`` tiles, so at every level it moves whole BC1 blocks and nothing is encoded again (island-regions "A larger
    donor"). A block whose source page the source lacks keeps the donor's."""
    found = {k: v for k, v in ((tintpages.PAGE.search(n), n) for n in sgd.names) if k}
    source = {tuple(int(g) for g in m.groups()): name for m, name in found.items()}
    out: dict[str, bytes] = {}
    for name in dgd.names:
        m = tintpages.PAGE.search(name)
        if m is None:
            continue
        level, px, pz = (int(g) for g in m.groups())
        span = tintpages.NODE_TILES << level
        x0, z0 = px * span - place.x, pz * span - place.z  # the page's corner in the source's tiles
        if x0 >= place.size or z0 >= place.size or x0 + span <= 0 or z0 + span <= 0:
            continue
        raw = dgd.read(name)
        parsed = _blocks(raw)
        if parsed is None:
            continue
        header, blocks = parsed[0], parsed[1].copy()
        if _graft_blocks(blocks, sgd, source, (level, x0, z0, span), place.size):
            out[name] = raw[:header] + blocks.tobytes() + raw[header + blocks.size :]
    return out


def _graft_blocks(blocks: np.ndarray, sgd, source: dict, page: tuple[int, int, int, int], size: int) -> bool:
    """The source's blocks laid into one donor page's ``blocks`` (``page``: level, its corner in source tiles, its
    span); whether any came."""
    level, x0, z0, span = page
    n = blocks.shape[0]
    per = n / span  # blocks a tile
    changed = False
    for sz in range(max(z0, 0) // span, min(z0 + span, size) // span + 1):
        for sx in range(max(x0, 0) // span, min(x0 + span, size) // span + 1):
            name = source.get((level, sx, sz))
            parsed = _blocks(sgd.read(name)) if name else None
            if parsed is None:
                continue
            theirs = parsed[1]
            # the source page's tiles that lie in this donor page and inside the source island
            tx0, tz0 = max(sx * span, x0, 0), max(sz * span, z0, 0)
            tx1, tz1 = min((sx + 1) * span, x0 + span, size), min((sz + 1) * span, z0 + span, size)
            if tx1 <= tx0 or tz1 <= tz0:
                continue
            d = (
                slice(round((tz0 - z0) * per), round((tz1 - z0) * per)),
                slice(round((tx0 - x0) * per), round((tx1 - x0) * per)),
            )
            s = (slice(round((tz0 - sz * span) * per), round((tz1 - sz * span) * per)),
                 slice(round((tx0 - sx * span) * per), round((tx1 - sx * span) * per)))  # fmt: skip
            blocks[d] = theirs[s]
            changed = True
    return changed


def mark_meshes(files: dict[str, bytes], used: int) -> int:
    """The donor's stored meshes with every node's used-material mask set to ``used`` (the ground of the island now
    on them), in ``files``; returns the meshes changed."""
    count = 0
    for name in [n for n in files if n.startswith("meshes/") and n.endswith(".tmc")]:
        new = regionconvert.rewrite_mesh_bits(files[name], lambda _mask: used)
        if new is not None:
            files[name] = new
            count += 1
    return count
