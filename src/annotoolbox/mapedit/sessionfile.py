"""A map template's session (``.a7t``: an RDA archive holding ``gamedata.data``) of Anno 117 or Anno 1800, and its
growth: every grid moved and padded, every object, camera and camera flight moved, each reference reported.

``read(data)`` gives a ``Session``; ``grow(session, sides, report)`` changes it in place (sides: west, south, east,
north); ``render()`` writes the archive. The inventory this follows: docs/map-editor.md "Where 117 and 1800 keep
absolute session positions". Grids are rows of z (``islandgrid.bits_array``; the height map's rows checked against the
vanilla sessions, map-editor "What the vanilla data settles"); numpy is imported where it is used.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

from ..formats import filedb, rda
from .references import KEPT, MOVED, Report

MEMBER = "gamedata.data"
# managers that hold times, states or ids only (every vanilla template read 2026-11-13)
STILL = frozenset({
    "WeatherManager", "SeasonManager", "VolcanoEruptionManager", "SessionUnitManager", "ItemSessionManager",
    "GameObjectManager", "SelectionManager", "SessionSoundManager", "SessionCooldownManager", "IncidentManager",
})  # fmt: skip
POSITION_WORDS = ("Position", "Pos", "Rect", "Point", "Path")  # names that may hold a place
FILL_EMPTY = frozenset({"RiverGrid", "FordGrid", "StreetID"})  # new strips of these start empty, others copy the edge
CAMERA_CHANNELS = (0, 2)  # the camera flight channels that hold x and z (map-editor "What the vanilla data settles")


@dataclass
class Session:
    doc: filedb.Tag
    ids: tuple[dict, dict]
    version: int
    others: dict[str, bytes] = field(default_factory=dict)  # further archive members, kept

    @property
    def manager(self) -> filedb.Tag:
        return self.doc.find("GameSessionManager")

    @property
    def size(self) -> tuple[int, int]:
        ws = self.manager.path("TerrainManager/WorldSize")
        return struct.unpack("<2i", ws.data[:8]) if ws is not None else (0, 0)

    def render(self) -> bytes:
        members = {MEMBER: filedb.write(self.doc, *self.ids, version=self.version), **self.others}
        return rda.write_v22(members)


def read(data: bytes) -> Session:
    archive = rda.RdaArchive.from_bytes(data)
    raw = archive.read(MEMBER)
    others = {name: archive.read(name) for name in archive.entries if name != MEMBER}
    return Session(filedb.read(raw), filedb.name_ids(raw), filedb.version(raw), others)


@dataclass
class _Grow:
    """One growth: the shift (west, south) and the sizes before and after."""

    dx: int
    dz: int
    old: tuple[int, int]
    new: tuple[int, int]
    report: Report
    file: str
    widen: bool = True  # the playable area widened with the map (else only moved)
    touched: set = field(default_factory=set)  # the managers a step moved something in

    def note(self, manager: str, where: str, status: str, detail: str = "", count: int = 1) -> None:
        self.touched.add(manager)
        self.report.add(self.file, where, status, detail, count)


def grow(
    session: Session, sides: tuple[int, int, int, int], report: Report, file: str = "", widen: bool = True
) -> None:
    """Grow the session by ``sides`` (west, south, east, north; tiles, multiples of 16): every grid, object, camera
    and flight moved; the playable area moved and, with ``widen``, widened."""
    west, south, east, north = sides
    old = session.size
    g = _Grow(west, south, old, (old[0] + west + east, old[1] + south + north), report, file, widen)
    gsm = session.manager
    for mgr in gsm.children:
        if isinstance(mgr, filedb.Tag):
            _walk_grids(mgr, mgr.name, mgr.name, g)
    _sizes(gsm, g)
    _objects(gsm.find("AreaManagerData"), g)
    _cameras(gsm, g)
    _flights(gsm.find("CameraSequenceManager"), g)
    _unknown(gsm, g)


def _u32(tag: filedb.Tag, name: str) -> int:
    node = tag.find(name)
    return struct.unpack("<I", node.data[:4])[0] if isinstance(node, filedb.Attrib) else 0


def _attrs(tag: filedb.Tag) -> set[str]:
    return {c.name for c in tag.children if isinstance(c, filedb.Attrib)}


def _walk_grids(tag: filedb.Tag, manager: str, where: str, g: _Grow) -> None:
    names = _attrs(tag)
    if "SparseEnabled" in names or (tag.findall("block") and "mode" in _attrs(tag.findall("block")[0])):
        _sparse(tag, manager, where, g)
        return
    if {"x", "y"} <= names and ({"val", "bits"} & names):
        _dense(tag, manager, where, g)
        return
    if {"Width", "Height", "HeightMap"} <= names:
        _heights(tag, manager, where, g)
        return
    for c in tag.children:
        if isinstance(c, filedb.Tag):
            _walk_grids(c, manager, f"{where}/{c.name}", g)


def _pads(g: _Grow, scale: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """The (low, high) padding of the rows (z) and the columns (x) of a grid at ``scale`` tiles per cell."""
    (w0, h0), (w1, h1) = g.old, g.new
    return (g.dz // scale, (h1 - h0 - g.dz) // scale), (g.dx // scale, (w1 - w0 - g.dx) // scale)


def _dense(tag: filedb.Tag, manager: str, where: str, g: _Grow) -> None:
    import numpy as np

    x, y = _u32(tag, "x"), _u32(tag, "y")
    if not x or g.old[0] % x or g.old[0] // x != g.old[1] // max(y, 1):
        g.note(manager, where, KEPT, f"a grid of {x} x {y} that is not the map's size")
        return
    scale = g.old[0] // x
    rows, cols = _pads(g, scale)
    mode = "constant" if tag.name in FILL_EMPTY else "edge"
    nx, ny = x + sum(cols), y + sum(rows)
    if tag.find("bits") is not None:
        bits = tag.find("bits")
        stride = (x + 31) // 32 * 32
        flat = np.unpackbits(np.frombuffer(bits.data, np.uint8), bitorder="little")
        grid = flat[: y * stride].reshape(y, stride)[:, :x]
        grid = np.pad(grid, (rows, cols), mode=mode)
        out = np.zeros((ny, (nx + 31) // 32 * 32), np.uint8)
        out[:, :nx] = grid
        bits.data = np.packbits(out, axis=1, bitorder="little").tobytes()
    else:
        val = tag.find("val")
        cell = len(val.data) // (x * y) if x * y else 0
        if not cell or cell * x * y != len(val.data):
            g.note(manager, where, KEPT, "a grid whose cells are not whole bytes")
            return
        grid = np.frombuffer(val.data, np.uint8).reshape(y, x, cell)
        val.data = np.pad(grid, (rows, cols, (0, 0)), mode=mode).tobytes()
    tag.find("x").data, tag.find("y").data = struct.pack("<I", nx), struct.pack("<I", ny)
    g.note(
        manager,
        where,
        MOVED,
        f"{x} x {y} -> {nx} x {ny}, the new strip {'empty' if mode == 'constant' else 'the edge'}",
    )


def _heights(tag: filedb.Tag, manager: str, where: str, g: _Grow) -> None:
    import numpy as np

    w, h = _u32(tag, "Width"), _u32(tag, "Height")
    data = tag.find("HeightMap")
    if w != 2 * g.old[0] + 1 or len(data.data) != 2 * w * h:
        g.note(manager, where, KEPT, f"a height map of {w} x {h} that is not the map's")
        return
    rows, cols = _pads(g, 1)
    grid = np.frombuffer(data.data, "<u2").reshape(h, w)
    grid = np.pad(grid, ((2 * rows[0], 2 * rows[1]), (2 * cols[0], 2 * cols[1])), mode="edge")
    data.data = grid.astype("<u2").tobytes()
    tag.find("Width").data = struct.pack("<I", grid.shape[1])
    tag.find("Height").data = struct.pack("<I", grid.shape[0])
    g.note(manager, where, MOVED, f"{w} x {h} -> {grid.shape[1]} x {grid.shape[0]} samples, the new strip the edge")


def _block_xy(b: filedb.Tag) -> tuple[int, int]:
    x, y = b.find("x"), b.find("y")
    return (
        struct.unpack("<H", x.data[:2])[0] if x is not None else 0,
        struct.unpack("<H", y.data[:2])[0] if y is not None else 0,
    )


def _placed(b: filedb.Tag, x: int, y: int) -> filedb.Tag:
    """A block at (x, y): its mode first, then x and y where not 0 (the shipped layout), then the rest."""
    rest = [c for c in b.children if c.name not in ("mode", "x", "y")]
    head = [c for c in b.children if c.name == "mode"]
    if x:
        head.append(filedb.Attrib("x", struct.pack("<H", x)))
    if y:
        head.append(filedb.Attrib("y", struct.pack("<H", y)))
    return filedb.Tag("block", head + rest)


def _sparse(tag: filedb.Tag, manager: str, where: str, g: _Grow) -> None:
    """A sparse grid: a header block (its x the block size), data blocks at their corner, the closing block."""
    blocks = tag.findall("block")
    g.touched.add(manager)
    if not blocks:
        return
    header, body = blocks[0], blocks[1:]
    closing = body and [c.name for c in body[-1].children] == ["mode"]
    end = body.pop() if closing else None
    bs = _block_xy(header)[0] or 16
    if g.dx % bs or g.dz % bs:
        g.note(manager, where, KEPT, f"blocks of {bs} tiles: the growth is not a multiple")
        return
    moved = [_placed(b, x + g.dx, y + g.dz) for b in body for x, y in [_block_xy(b)]]
    added = 0
    (w0, h0), (w1, h1) = g.old, g.new
    uniform = [b for b in body if b.find("values") is None and isinstance(b.find("default"), filedb.Attrib)]
    if uniform and len(body) == (w0 // bs) * (h0 // bs):  # every block there: the new strip gets the sea's block
        common = Counter(b.find("default").data for b in uniform).most_common(1)[0][0]
        model = next(b for b in uniform if b.find("default").data == common)
        have = {_block_xy(b) for b in moved}
        for by in range(0, h1, bs):
            for bx in range(0, w1, bs):
                if (bx, by) not in have:
                    moved.append(_placed(model, bx, by))
                    added += 1
        moved.sort(key=lambda b: _block_xy(b)[::-1])
    for name in ("x", "y"):
        if tag.find(name) is not None:
            tag.find(name).data = struct.pack("<I", g.new[0] if name == "x" else g.new[1])
    others = [c for c in tag.children if c.name != "block"]
    tag.children = others + [header, *moved] + ([end] if end is not None else [])
    if body or added:
        g.note(manager, where, MOVED, f"{len(body)} blocks moved, {added} added", len(body))


def _sizes(gsm: filedb.Tag, g: _Grow) -> None:
    ws = gsm.path("TerrainManager/WorldSize")
    if ws is not None:
        ws.data = struct.pack("<2i", *g.new)
        g.note("TerrainManager", "TerrainManager/WorldSize", MOVED, f"{g.old} -> {g.new}")
    pa = gsm.path("SessionSettings/PlayableArea")
    if pa is not None:
        x0, z0, x1, z1 = struct.unpack("<4i", pa.data[:16])
        east, north = g.new[0] - g.old[0] - g.dx, g.new[1] - g.old[1] - g.dz
        new = grown_area((x0, z0, x1, z1), (g.dx, g.dz, east, north), g.widen)
        pa.data = struct.pack("<4i", *new)
        g.note("SessionSettings", "SessionSettings/PlayableArea", MOVED, f"{(x0, z0, x1, z1)} -> {new}")
    spawn = gsm.find("SpawnAreaPoints")
    for item in spawn.findall("#1") if isinstance(spawn, filedb.Tag) else []:
        if item.find("m_XSize") is not None:
            item.find("m_XSize").data = struct.pack("<I", g.new[0])
            item.find("m_YSize").data = struct.pack("<I", g.new[1])
        held = [c for c in item.children if isinstance(c, filedb.Tag) and c.children]
        if held:
            g.note("SpawnAreaPoints", "SpawnAreaPoints", KEPT, "a spawn grid holds points the scan does not move")
    if isinstance(spawn, filedb.Tag) and spawn.findall("#1"):
        g.note("SpawnAreaPoints", "SpawnAreaPoints/m_XSize", MOVED, "the spawn grids' size", len(spawn.findall("#1")))


def grown_area(area: tuple[int, int, int, int], sides: tuple[int, int, int, int], widen: bool = True):
    """A playable area after growing by ``sides`` (west, south, east, north): widened by every amount when
    ``widen`` (it keeps its margins to the new edges), else moved by (west, south)."""
    west, south, east, north = sides
    x0, z0, x1, z1 = area
    if widen:
        return (x0, z0, x1 + west + east, z1 + south + north)
    return (x0 + west, z0 + south, x1 + west, z1 + south)


def _shift_position(attr: filedb.Attrib, dx: float, dz: float) -> None:
    x, y, z = struct.unpack("<3f", attr.data[:12])
    attr.data = struct.pack("<3f", x + dx, y, z + dz) + attr.data[12:]


def _objects(amd: filedb.Tag | None, g: _Grow) -> None:
    """The session's objects (one nested document per area): every ``Position`` moved, other places listed."""
    if not isinstance(amd, filedb.Tag):
        return
    g.touched.add(amd.name)
    moved, other = 0, {}
    for area in amd.children:
        data = area.find("Data") if isinstance(area, filedb.Tag) else None
        if data is None:
            continue
        inner, ids, version = filedb.read(data.data), filedb.name_ids(data.data), filedb.version(data.data)
        here = 0
        for node in inner.iter():
            if not isinstance(node, filedb.Attrib):
                continue
            if node.name == "Position" and len(node.data) == 12:
                _shift_position(node, g.dx, g.dz)
                here += 1
            elif any(w in node.name for w in POSITION_WORDS) and node.data:
                other[node.name] = other.get(node.name, 0) + 1
        moved += here
        if here:  # an area with nothing to move keeps its bytes
            data.data = filedb.write(inner, *ids, version=version)
            count = area.find("ByteCount")
            if count is not None:
                count.data = struct.pack("<I", len(data.data))
    if moved:
        g.note("AreaManagerData", "AreaManagerData objects' Position", MOVED, "x and z", moved)
    for name, n in sorted(other.items()):
        g.note(
            "AreaManagerData", f"AreaManagerData objects' {name}", KEPT, "a place-like field the scan does not move", n
        )


def _cameras(gsm: filedb.Tag, g: _Grow) -> None:
    for name in ("CameraManager", "SessionCameraManager"):
        mgr = gsm.find(name)
        if not isinstance(mgr, filedb.Tag):
            continue
        views = [n for n in mgr.iter() if isinstance(n, filedb.Tag) and n.name == "View" and n.find("From") is not None]
        for view in views:
            _shift_position(view.find("From"), g.dx, g.dz)
        if views:
            g.note(name, f"{name} saved cameras' From", MOVED, "x and z", len(views))


def _flights(seq: filedb.Tag | None, g: _Grow) -> None:
    """Camera flights: the keyframes of channels 0 and 2 of each camera and object track (x and z)."""
    if not isinstance(seq, filedb.Tag):
        return
    moved = kept = 0
    for track in seq.iter():
        if not isinstance(track, filedb.Tag) or track.name not in ("Cam", "Asset", "Action"):
            continue
        frames = track.find("AllKeyframes")
        if not isinstance(frames, filedb.Tag):
            continue
        if track.name == "Action":
            kept += 1
            continue
        channels = [c for c in frames.children if isinstance(c, filedb.Tag)]
        for index, delta in zip(CAMERA_CHANNELS, (g.dx, g.dz), strict=True):
            for key in channels[index].children if index < len(channels) else []:
                value = key.find("Value") if isinstance(key, filedb.Tag) else None
                if value is not None and len(value.data) == 4:
                    value.data = struct.pack("<f", struct.unpack("<f", value.data)[0] + delta)
                    moved += 1
    if moved:
        g.note("CameraSequenceManager", "CameraSequenceManager keyframes of channels 0 and 2", MOVED, "x and z", moved)
    if kept:
        g.note(
            "CameraSequenceManager", "CameraSequenceManager action keyframes", KEPT, "not places as far as read", kept
        )


def _unknown(gsm: filedb.Tag, g: _Grow) -> None:
    """Managers that hold data no step touched and that are not known to hold no place."""
    for mgr in gsm.children:
        if not isinstance(mgr, filedb.Tag) or mgr.name in g.touched or mgr.name in STILL:
            continue
        if any(isinstance(n, filedb.Attrib) and n.data for n in mgr.iter()):
            g.note(mgr.name, mgr.name, KEPT, "holds data the scan does not know")
