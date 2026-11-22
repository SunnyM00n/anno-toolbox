"""What an island archive (``.a7m``, Anno 117 and Anno 1800) holds that a person asks for: its height map and its
objects. Standard library only, so the export and the tools read an island without the island editor.

The one reader of the archive's layout: ``members`` (the files inside, from bytes or an opened archive),
``coarse_steps`` and ``heights`` (the height map), ``area_docs``, ``area_objects`` and ``object_tags`` (the areas and
their object lists), ``objects`` (the placed objects). The island editor reads further (materials, rivers) on top of
these, with the game's files beside the archive; this module needs only the bytes. Units: the heights are 117 height
units / 4 = world units; a position is already in world units.
"""

from __future__ import annotations

import struct
from array import array
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from . import filedb, rda

HEIGHT_STEPS = 64  # the stored height map's steps per render unit
HEIGHT_UNITS = 4.0  # render units per world unit (the same number as ``islands.islandgrid.HEIGHT_UNITS``)
OBJECT_GROUPS = ("GameObject", "EditorObject", "NaturePreset", "Prop")


class CIslandError(ValueError):
    """The archive lacks a member or a block the reader needs."""


@dataclass
class SPlacedObject:
    """One placed object: its group, asset GUID, variation, world position, rotation (quaternion x, y, z, w), scale
    (one number, or per axis for a prop) and its own model config where the island names one."""

    group: str
    guid: int
    variation: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]
    scale: float | tuple[float, float, float]
    config: str


def members(source: bytes | rda.CRdaArchive, *names: str) -> list[bytes]:
    """The named members of an island archive (its bytes, or one opened), read in order: a name is the member's exact
    path, else the one file a path ending in it names (any folder, any case)."""
    archive = rda.CRdaArchive.from_bytes(source) if isinstance(source, bytes | bytearray) else source
    by_name: dict[str, str] = {}
    for path in archive.entries:
        by_name.setdefault(path.replace("\\", "/").lower().rsplit("/", 1)[-1], path)
    out = []
    for name in names:
        path = name if name in archive.entries else by_name.get(name.lower().rsplit("/", 1)[-1])
        if path is None:
            raise CIslandError(f"no {name} in the archive")
        out.append(archive.read(path))
    return out


def coarse_steps(rd3d: filedb.STag) -> tuple[int, array]:
    """(samples across, the int16 steps, rows by z) of ``rd3d.data``'s ``Terrain/CoarseHeightMap``; each step is
    ``1 / HEIGHT_STEPS`` of a render unit. The width is ``2 * size + 1``."""
    terrain = rd3d.path("Terrain")
    coarse = terrain.find("CoarseHeightMap") if isinstance(terrain, filedb.STag) else None
    if not isinstance(coarse, filedb.STag) or coarse.find("width") is None or coarse.find("map") is None:
        raise CIslandError("rd3d.data has no Terrain/CoarseHeightMap")
    width, raw = coarse.find("width").u32(), coarse.find("map").data
    if width < 2 or len(raw) != width * width * 2:
        raise CIslandError(f"the height map is {len(raw)} bytes for {width} x {width} samples")
    steps = array("h")
    steps.frombytes(raw)
    return width, steps


def heights(source: bytes | rda.CRdaArchive) -> tuple[int, list[float]]:
    """(samples across, the heights in world units, rows by z) of an island archive's coarse height map."""
    width, steps = coarse_steps(filedb.read(members(source, "rd3d.data")[0]))
    scale = HEIGHT_STEPS * HEIGHT_UNITS
    return width, [step / scale for step in steps]


def area_docs(session: filedb.STag) -> Iterator[tuple[filedb.STag, filedb.STag]]:
    """Each area of a ``GameSessionManager`` tag with data, in file order: (the area tag, its ``Data`` read as a
    FileDB document)."""
    areas = session.path("AreaManagerData")
    for area in areas.findall("#1") if areas is not None else []:
        data = area.find("Data")
        if data is not None:
            yield area, filedb.read(data.data)


def area_objects(document: filedb.STag, group: str) -> filedb.STag | None:
    """The ``objects`` list of an area document's object group (``GameObject``, ``Prop``, ``EditorObject``), which
    vanilla nests under ``AreaObjectManager`` or not."""
    return document.path(f"AreaObjectManager/{group}/objects") or document.path(f"{group}/objects")


def object_tags(session: filedb.STag, groups: Sequence[str] = OBJECT_GROUPS) -> Iterator[tuple[str, filedb.STag]]:
    """Every object tag of a session's areas as (its group, the tag): by area, then by group, then in file order."""
    for _area, document in area_docs(session):
        for group in groups:
            block = area_objects(document, group)
            for child in block.children if block is not None else []:
                if isinstance(child, filedb.STag):
                    yield group, child


def _floats(tag, count: int) -> tuple[float, ...] | None:
    if tag is None or len(tag.data) != 4 * count:
        return None
    return struct.unpack(f"<{count}f", tag.data)


def _u32(tag) -> int:
    return struct.unpack_from("<I", tag.data)[0] if tag is not None and len(tag.data) >= 4 else 0


def placed(object_tag: filedb.STag, group: str) -> SPlacedObject | None:
    """One object tag as a ``SPlacedObject``; None when it has no position."""
    position = _floats(object_tag.find("Position"), 3)
    if position is None:
        return None
    turn = (
        _floats(object_tag.path("Mesh/Orientation"), 4)
        or _floats(object_tag.path("Prop/Rotation"), 4)
        or (0.0, 0.0, 0.0, 1.0)
    )
    per_axis, uniform = _floats(object_tag.path("Prop/Scale"), 3), _floats(object_tag.path("Mesh/Scale"), 1)
    config = object_tag.path("DynamicVariation/ConfigFilename")
    return SPlacedObject(
        group,
        _u32(object_tag.find("guid")),
        _u32(object_tag.find("Variation")),
        position,
        turn,
        per_axis or (uniform[0] if uniform else 1.0),
        config.data.decode("utf-16-le").replace("\\", "/") if config is not None else "",
    )


def objects(source: bytes | rda.CRdaArchive) -> list[SPlacedObject]:
    """Every placed object of ``gamedata.data``'s areas, in file order."""
    session = filedb.read(members(source, "gamedata.data")[0]).path("GameSessionManager")
    found = (placed(object_tag, group) for group, object_tag in object_tags(session)) if session is not None else ()
    return [item for item in found if item]
