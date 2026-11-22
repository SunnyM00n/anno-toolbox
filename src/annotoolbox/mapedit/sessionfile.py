"""A map template's session (``.a7t``: an RDA archive holding ``gamedata.data``) of Anno 117 or Anno 1800, and its
growth: every grid moved and padded, every object, camera and camera flight moved, each reference reported.

``read(data)`` gives a ``SSession``; ``grow(session, sides, report)`` changes it in place (sides: west, south, east,
north); ``render()`` writes the archive. The inventory this follows: docs/map-editor.md "Where 117 and 1800 keep
absolute session positions". Grids are rows of z (``islandgrid.bits_array``; the height map's rows checked against the
vanilla sessions, map-editor "What the vanilla data settles"); numpy is imported where it is used.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

from ..formats import filedb, rda
from .references import KEPT, MOVED, CReport

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
class SSession:
    doc: filedb.STag
    ids: tuple[dict, dict]
    version: int
    others: dict[str, bytes] = field(default_factory=dict)  # further archive members, kept

    @property
    def manager(self) -> filedb.STag:
        return self.doc.find("GameSessionManager")

    @property
    def size(self) -> tuple[int, int]:
        ws = self.manager.path("TerrainManager/WorldSize")
        return struct.unpack("<2i", ws.data[:8]) if ws is not None else (0, 0)

    def render(self) -> bytes:
        members = {MEMBER: filedb.write(self.doc, *self.ids, version=self.version), **self.others}
        return rda.write_v22(members)


def read(data: bytes) -> SSession:
    archive = rda.CRdaArchive.from_bytes(data)
    raw = archive.read(MEMBER)
    others = {name: archive.read(name) for name in archive.entries if name != MEMBER}
    return SSession(filedb.read(raw), filedb.name_ids(raw), filedb.version(raw), others)


@dataclass
class _CGrow:
    """One growth: the shift (west, south) and the sizes before and after."""

    m_dx: int
    m_dz: int
    m_old: tuple[int, int]
    m_new: tuple[int, int]
    m_report: CReport
    m_file: str
    m_widen: bool = True  # the playable area widened with the map (else only moved)
    m_touched: set = field(default_factory=set)  # the managers a step moved something in

    def note(self, manager: str, where: str, status: str, detail: str = "", count: int = 1) -> None:
        self.m_touched.add(manager)
        self.m_report.add(self.m_file, where, status, detail, count)


def grow(
    session: SSession, sides: tuple[int, int, int, int], report: CReport, file: str = "", widen: bool = True
) -> None:
    """Grow the session by ``sides`` (west, south, east, north; tiles, multiples of 16): every grid, object, camera
    and flight moved; the playable area moved and, with ``widen``, widened."""
    west, south, east, north = sides
    old = session.size
    grow = _CGrow(west, south, old, (old[0] + west + east, old[1] + south + north), report, file, widen)
    gsm = session.manager
    for mgr in gsm.children:
        if isinstance(mgr, filedb.STag):
            _walk_grids(mgr, mgr.name, mgr.name, grow)
    _sizes(gsm, grow)
    _objects(gsm.find("AreaManagerData"), grow)
    _cameras(gsm, grow)
    _flights(gsm.find("CameraSequenceManager"), grow)
    _unknown(gsm, grow)


def _u32(tag: filedb.STag, name: str) -> int:
    node = tag.find(name)
    return struct.unpack("<I", node.data[:4])[0] if isinstance(node, filedb.SAttrib) else 0


def _attrs(tag: filedb.STag) -> set[str]:
    return {child.name for child in tag.children if isinstance(child, filedb.SAttrib)}


def _walk_grids(tag: filedb.STag, manager: str, where: str, grow: _CGrow) -> None:
    names = _attrs(tag)
    if "SparseEnabled" in names or (tag.findall("block") and "mode" in _attrs(tag.findall("block")[0])):
        _sparse(tag, manager, where, grow)
        return
    if {"x", "y"} <= names and ({"val", "bits"} & names):
        _dense(tag, manager, where, grow)
        return
    if {"Width", "Height", "HeightMap"} <= names:
        _heights(tag, manager, where, grow)
        return
    for child in tag.children:
        if isinstance(child, filedb.STag):
            _walk_grids(child, manager, f"{where}/{child.name}", grow)


def _pads(grow: _CGrow, scale: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """The (low, high) padding of the rows (z) and the columns (x) of a grid at ``scale`` tiles per cell."""
    (w0, h0), (w1, h1) = grow.m_old, grow.m_new
    return (grow.m_dz // scale, (h1 - h0 - grow.m_dz) // scale), (grow.m_dx // scale, (w1 - w0 - grow.m_dx) // scale)


def _dense(tag: filedb.STag, manager: str, where: str, grow: _CGrow) -> None:
    import numpy as np

    x, y = _u32(tag, "x"), _u32(tag, "y")
    if not x or grow.m_old[0] % x or grow.m_old[0] // x != grow.m_old[1] // max(y, 1):
        grow.note(manager, where, KEPT, f"a grid of {x} x {y} that is not the map's size")
        return
    scale = grow.m_old[0] // x
    rows, cols = _pads(grow, scale)
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
        value_tag = tag.find("val")
        cell = len(value_tag.data) // (x * y) if x * y else 0
        if not cell or cell * x * y != len(value_tag.data):
            grow.note(manager, where, KEPT, "a grid whose cells are not whole bytes")
            return
        grid = np.frombuffer(value_tag.data, np.uint8).reshape(y, x, cell)
        value_tag.data = np.pad(grid, (rows, cols, (0, 0)), mode=mode).tobytes()
    tag.find("x").data, tag.find("y").data = struct.pack("<I", nx), struct.pack("<I", ny)
    grow.note(
        manager,
        where,
        MOVED,
        f"{x} x {y} -> {nx} x {ny}, the new strip {'empty' if mode == 'constant' else 'the edge'}",
    )


def _heights(tag: filedb.STag, manager: str, where: str, grow: _CGrow) -> None:
    import numpy as np

    width, height = _u32(tag, "Width"), _u32(tag, "Height")
    data = tag.find("HeightMap")
    if width != 2 * grow.m_old[0] + 1 or len(data.data) != 2 * width * height:
        grow.note(manager, where, KEPT, f"a height map of {width} x {height} that is not the map's")
        return
    rows, cols = _pads(grow, 1)
    grid = np.frombuffer(data.data, "<u2").reshape(height, width)
    grid = np.pad(grid, ((2 * rows[0], 2 * rows[1]), (2 * cols[0], 2 * cols[1])), mode="edge")
    data.data = grid.astype("<u2").tobytes()
    tag.find("Width").data = struct.pack("<I", grid.shape[1])
    tag.find("Height").data = struct.pack("<I", grid.shape[0])
    grow.note(
        manager,
        where,
        MOVED,
        f"{width} x {height} -> {grid.shape[1]} x {grid.shape[0]} samples, the new strip the edge",
    )


def _block_xy(block: filedb.STag) -> tuple[int, int]:
    x, y = block.find("x"), block.find("y")
    return (
        struct.unpack("<H", x.data[:2])[0] if x is not None else 0,
        struct.unpack("<H", y.data[:2])[0] if y is not None else 0,
    )


def _placed(block: filedb.STag, x: int, y: int) -> filedb.STag:
    """A block at (x, y): its mode first, then x and y where not 0 (the shipped layout), then the rest."""
    rest = [child for child in block.children if child.name not in ("mode", "x", "y")]
    head = [child for child in block.children if child.name == "mode"]
    if x:
        head.append(filedb.SAttrib("x", struct.pack("<H", x)))
    if y:
        head.append(filedb.SAttrib("y", struct.pack("<H", y)))
    return filedb.STag("block", head + rest)


def _sparse(tag: filedb.STag, manager: str, where: str, grow: _CGrow) -> None:
    """A sparse grid: a header block (its x the block size), data blocks at their corner, the closing block."""
    blocks = tag.findall("block")
    grow.m_touched.add(manager)
    if not blocks:
        return
    header, body = blocks[0], blocks[1:]
    closing = body and [child.name for child in body[-1].children] == ["mode"]
    end = body.pop() if closing else None
    bs = _block_xy(header)[0] or 16
    if grow.m_dx % bs or grow.m_dz % bs:
        grow.note(manager, where, KEPT, f"blocks of {bs} tiles: the growth is not a multiple")
        return
    moved = [_placed(block, x + grow.m_dx, y + grow.m_dz) for block in body for x, y in [_block_xy(block)]]
    added = 0
    (w0, h0), (w1, h1) = grow.m_old, grow.m_new
    uniform = [
        block for block in body if block.find("values") is None and isinstance(block.find("default"), filedb.SAttrib)
    ]
    if uniform and len(body) == (w0 // bs) * (h0 // bs):  # every block there: the new strip gets the sea's block
        common = Counter(block.find("default").data for block in uniform).most_common(1)[0][0]
        model = next(block for block in uniform if block.find("default").data == common)
        have = {_block_xy(block) for block in moved}
        for by in range(0, h1, bs):
            for bx in range(0, w1, bs):
                if (bx, by) not in have:
                    moved.append(_placed(model, bx, by))
                    added += 1
        moved.sort(key=lambda block: _block_xy(block)[::-1])
    for name in ("x", "y"):
        if tag.find(name) is not None:
            tag.find(name).data = struct.pack("<I", grow.m_new[0] if name == "x" else grow.m_new[1])
    others = [child for child in tag.children if child.name != "block"]
    tag.children = others + [header, *moved] + ([end] if end is not None else [])
    if body or added:
        grow.note(manager, where, MOVED, f"{len(body)} blocks moved, {added} added", len(body))


def _sizes(gsm: filedb.STag, grow: _CGrow) -> None:
    ws = gsm.path("TerrainManager/WorldSize")
    if ws is not None:
        ws.data = struct.pack("<2i", *grow.m_new)
        grow.note("TerrainManager", "TerrainManager/WorldSize", MOVED, f"{grow.m_old} -> {grow.m_new}")
    pa = gsm.path("SessionSettings/PlayableArea")
    if pa is not None:
        x0, z0, x1, z1 = struct.unpack("<4i", pa.data[:16])
        east, north = grow.m_new[0] - grow.m_old[0] - grow.m_dx, grow.m_new[1] - grow.m_old[1] - grow.m_dz
        new = grown_area((x0, z0, x1, z1), (grow.m_dx, grow.m_dz, east, north), grow.m_widen)
        pa.data = struct.pack("<4i", *new)
        grow.note("SessionSettings", "SessionSettings/PlayableArea", MOVED, f"{(x0, z0, x1, z1)} -> {new}")
    spawn = gsm.find("SpawnAreaPoints")
    for item in spawn.findall("#1") if isinstance(spawn, filedb.STag) else []:
        if item.find("m_XSize") is not None:
            item.find("m_XSize").data = struct.pack("<I", grow.m_new[0])
            item.find("m_YSize").data = struct.pack("<I", grow.m_new[1])
        held = [child for child in item.children if isinstance(child, filedb.STag) and child.children]
        if held:
            grow.note("SpawnAreaPoints", "SpawnAreaPoints", KEPT, "a spawn grid holds points the scan does not move")
    if isinstance(spawn, filedb.STag) and spawn.findall("#1"):
        grow.note(
            "SpawnAreaPoints", "SpawnAreaPoints/m_XSize", MOVED, "the spawn grids' size", len(spawn.findall("#1"))
        )


def grown_area(area: tuple[int, int, int, int], sides: tuple[int, int, int, int], widen: bool = True):
    """A playable area after growing by ``sides`` (west, south, east, north): widened by every amount when
    ``widen`` (it keeps its margins to the new edges), else moved by (west, south)."""
    west, south, east, north = sides
    x0, z0, x1, z1 = area
    if widen:
        return (x0, z0, x1 + west + east, z1 + south + north)
    return (x0 + west, z0 + south, x1 + west, z1 + south)


def _shift_position(attr: filedb.SAttrib, dx: float, dz: float) -> None:
    x, y, z = struct.unpack("<3f", attr.data[:12])
    attr.data = struct.pack("<3f", x + dx, y, z + dz) + attr.data[12:]


def _objects(amd: filedb.STag | None, grow: _CGrow) -> None:
    """The session's objects (one nested document per area): every ``Position`` moved, other places listed."""
    if not isinstance(amd, filedb.STag):
        return
    grow.m_touched.add(amd.name)
    moved, other = 0, {}
    for area in amd.children:
        data = area.find("Data") if isinstance(area, filedb.STag) else None
        if data is None:
            continue
        inner, ids, version = filedb.read(data.data), filedb.name_ids(data.data), filedb.version(data.data)
        here = 0
        for node in inner.iter():
            if not isinstance(node, filedb.SAttrib):
                continue
            if node.name == "Position" and len(node.data) == 12:
                _shift_position(node, grow.m_dx, grow.m_dz)
                here += 1
            elif any(word in node.name for word in POSITION_WORDS) and node.data:
                other[node.name] = other.get(node.name, 0) + 1
        moved += here
        if here:  # an area with nothing to move keeps its bytes
            data.data = filedb.write(inner, *ids, version=version)
            count = area.find("ByteCount")
            if count is not None:
                count.data = struct.pack("<I", len(data.data))
    if moved:
        grow.note("AreaManagerData", "AreaManagerData objects' Position", MOVED, "x and z", moved)
    for name, field_count in sorted(other.items()):
        grow.note(
            "AreaManagerData",
            f"AreaManagerData objects' {name}",
            KEPT,
            "a place-like field the scan does not move",
            field_count,
        )


def _cameras(gsm: filedb.STag, grow: _CGrow) -> None:
    for name in ("CameraManager", "SessionCameraManager"):
        mgr = gsm.find(name)
        if not isinstance(mgr, filedb.STag):
            continue
        views = [
            node
            for node in mgr.iter()
            if isinstance(node, filedb.STag) and node.name == "View" and node.find("From") is not None
        ]
        for view in views:
            _shift_position(view.find("From"), grow.m_dx, grow.m_dz)
        if views:
            grow.note(name, f"{name} saved cameras' From", MOVED, "x and z", len(views))


def _flights(seq: filedb.STag | None, grow: _CGrow) -> None:
    """Camera flights: the keyframes of channels 0 and 2 of each camera and object track (x and z)."""
    if not isinstance(seq, filedb.STag):
        return
    moved = kept = 0
    for track in seq.iter():
        if not isinstance(track, filedb.STag) or track.name not in ("Cam", "Asset", "Action"):
            continue
        frames = track.find("AllKeyframes")
        if not isinstance(frames, filedb.STag):
            continue
        if track.name == "Action":
            kept += 1
            continue
        channels = [channel for channel in frames.children if isinstance(channel, filedb.STag)]
        for index, delta in zip(CAMERA_CHANNELS, (grow.m_dx, grow.m_dz), strict=True):
            for key in channels[index].children if index < len(channels) else []:
                value = key.find("Value") if isinstance(key, filedb.STag) else None
                if value is not None and len(value.data) == 4:
                    value.data = struct.pack("<f", struct.unpack("<f", value.data)[0] + delta)
                    moved += 1
    if moved:
        grow.note(
            "CameraSequenceManager", "CameraSequenceManager keyframes of channels 0 and 2", MOVED, "x and z", moved
        )
    if kept:
        grow.note(
            "CameraSequenceManager", "CameraSequenceManager action keyframes", KEPT, "not places as far as read", kept
        )


def _unknown(gsm: filedb.STag, grow: _CGrow) -> None:
    """Managers that hold data no step touched and that are not known to hold no place."""
    for mgr in gsm.children:
        if not isinstance(mgr, filedb.STag) or mgr.name in grow.m_touched or mgr.name in STILL:
            continue
        if any(isinstance(node, filedb.SAttrib) and node.data for node in mgr.iter()):
            grow.note(mgr.name, mgr.name, KEPT, "holds data the scan does not know")
