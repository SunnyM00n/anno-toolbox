"""A map grown on any of its four sides: the template, its session, its level header, the horizon islands of its
``MapTemplate`` asset, the position blocks of the assets that belong to the map, and a scan of the scripts for
numbers that may be positions, with one report of it all.

``grow_map(parts, sides)`` is the entry (``GrowParts`` in, ``Grown`` out); every step adds to the ``Report``
(``references``). docs/map-editor.md "Growing a map on any side" and the inventory beside it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..infra.i18n import tr
from ..mod import modxml
from . import levelfile, sessionfile
from .references import KEPT, MOVED, Report
from .template import MapTemplate

STEP = levelfile.CHUNK  # a side grows by whole chunks of the level header
LUA_NUMBERS = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?:\s*,\s*(-?\d+(?:\.\d+)?))?")


def check_sides(sides: tuple[int, int, int, int]) -> None:
    """``ValueError`` unless every side is a whole number of chunks and something grows."""
    if any(s < 0 or s % STEP for s in sides):
        raise ValueError(tr("each side grows by a multiple of {step} tiles", step=STEP))
    if not any(sides):
        raise ValueError(tr("no side grows"))


@dataclass
class GrowParts:
    """What a growth reads: the template (edited in place), and what else the map has (None where it has not)."""

    template: MapTemplate
    path: str  # the template's .a7tinfo game path
    session: bytes | None = None  # the .a7t
    level: bytes | None = None  # the .a7te
    asset: str = ""  # the MapTemplate asset as the asset list has it
    asset_guid: int = 0
    scripts: dict[str, str] = field(default_factory=dict)  # game path -> text of the scripts to scan
    widen: bool = True  # the playable area grows with the map (else it is only moved)
    enlarged: bool = False  # the template is the asset's enlarged one (its horizon list is the enlarged one)
    positions: AssetPositions | None = None  # the asset lists' position blocks (None: not scanned)
    templates: frozenset[int] = frozenset()  # the GUIDs of every MapTemplate asset of the game
    moved_assets: dict[int, str] = field(default_factory=dict)  # asset GUID -> its text after earlier growths


@dataclass
class Grown:
    files: dict[str, bytes]  # suffix (".a7tinfo", ".a7t", ".a7te") -> bytes
    asset_ops: list[str]  # ModOps on the asset list (the horizon islands, the assets that hold the map's positions)
    report: Report
    asset_text: str = ""  # the MapTemplate asset with its horizons moved (a further growth starts from it)
    moved_assets: dict[int, str] = field(default_factory=dict)  # the other assets moved so far (same use)


def grow_template(t: MapTemplate, sides: tuple[int, int, int, int], report: Report, file: str, widen: bool) -> None:
    west, south, east, north = sides
    old = t.size
    t.size = (old[0] + west + east, old[1] + south + north)
    report.add(file, "MapTemplate/Size", MOVED, f"{old} -> {t.size}")
    pa = sessionfile.grown_area(t.playable, sides, widen)
    report.add(file, "MapTemplate/PlayableArea", MOVED, f"{t.playable} -> {pa}")
    t.playable = pa
    if t.initial is not None and any(t.initial):
        ipa = (t.initial[0] + west, t.initial[1] + south, t.initial[2] + west, t.initial[3] + south)
        report.add(file, "MapTemplate/InitialPlayableArea", MOVED, f"{t.initial} -> {ipa}")
        t.initial = ipa
    for e in t.elements:
        e.x, e.z = e.x + west, e.z + south
    if t.elements:
        report.add(file, "MapTemplate/TemplateElement/Element/Position", MOVED, "x and z", len(t.elements))
    for name in ("EnlargementOffset", "IslandShift"):
        if t.value(name) is not None:
            report.add(file, f"MapTemplate/{name}", KEPT, tr("kept: its meaning past 0, 0 is not known"))


HORIZONS, ENLARGED_HORIZONS = "HorizonIslands", "EnlargedHorizonIslands"


def horizon_list(asset: str, name: str) -> str:
    """The asset's horizon list ``name`` as its text ("" when it has none)."""
    m = re.search(rf"<{name}>.*?</{name}>", asset, re.S)
    return m.group(0) if m else ""


def _moved_position(m: re.Match, west: int, south: int) -> str:
    """One ``Position`` block moved by (west, south); a value the asset leaves out (0) is written."""
    inner = m.group(1)
    gap = re.match(r"\s*", inner).group(0)
    for axis, delta in (("X", west), ("Z", south)):
        value = re.search(rf"<{axis}>(-?[\d.eE+]+)</{axis}>", inner)
        new = f"<{axis}>{_num((float(value.group(1)) if value else 0.0) + delta)}</{axis}>"
        if value:
            inner = inner[: value.start()] + new + inner[value.end() :]
        elif axis == "X":
            inner = gap + new + inner
        else:
            inner = inner.rstrip() + gap + new + inner[len(inner.rstrip()) :]
    return f"<Position>{inner}</Position>"


def grow_horizons(
    asset: str, guid: int, west: int, south: int, report: Report, enlarged: bool = False
) -> tuple[list[str], str]:
    """The ModOps that move each horizon island of the template's list (the enlarged map's list for an enlarged
    template: the asset serves both maps, the other list is kept) by (west, south), and the asset with them moved."""
    name, other = (ENLARGED_HORIZONS, HORIZONS) if enlarged else (HORIZONS, ENLARGED_HORIZONS)
    block = horizon_list(asset, name)
    if not block or not (west or south):
        return [], asset
    moved, count = re.subn(r"<Position>(.*?)</Position>", lambda m: _moved_position(m, west, south), block, flags=re.S)
    report.add("assets", f"MapTemplate {guid} {name}/Item/Position", MOVED, "X and Z", count)
    if horizon_list(asset, other):
        report.add("assets", f"MapTemplate {guid} {other}", KEPT, tr("the other map of the same asset uses it"))
    path = f"//Asset[Values/Standard/GUID='{guid}']/Values/MapTemplate/{name}"
    return [modxml.modop(path, moved, "replace")], asset.replace(block, moved, 1)


def _num(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:g}"


@dataclass(frozen=True)
class PositionAsset:
    """An asset (not a ``MapTemplate``) with a block that has an ``X`` and a ``Z``: its text and the numbers it holds."""

    guid: int
    template: str
    text: str
    numbers: frozenset[int]  # every whole number a value of the asset holds (its GUID references among them)


@dataclass
class AssetPositions:
    assets: list[PositionAsset] = field(default_factory=list)
    sessions: dict[int, int] = field(default_factory=dict)  # a session asset's GUID -> its MapTemplate's GUID


def _asset_around(text: str, at: int) -> tuple[int, int]:
    """The span of the top-level asset that holds offset ``at`` (an ``<Asset>`` is followed by its ``<Template>``)."""
    start = text.rfind("<Asset>", 0, max(text.rfind("<Template>", 0, at), 0))
    return start, text.find("</Asset>", at) + len("</Asset>")


def scan_assets(texts: Iterable[str]) -> AssetPositions:
    """The assets of the asset lists ``texts`` that hold an ``X``/``Z`` block (the ``MapTemplate`` assets apart: their
    horizon islands are moved with their own step) and each session asset's template. One pass of finds, not a parse."""
    found = AssetPositions()
    seen: set[tuple[int, int]] = set()
    for index, text in enumerate(texts):
        for m in re.finditer(r"<MapTemplate>(\d+)</MapTemplate>", text):
            start, _end = _asset_around(text, m.start())
            guid = re.search(r"<GUID>(\d+)</GUID>", text[start : m.start()])
            if guid:
                found.sessions[int(guid.group(1))] = int(m.group(1))
        covered = 0  # the hits inside the asset just read are the same asset
        for hit in re.finditer(r"<Z>", text):  # world positions have a Z; screen positions have X and Y
            if hit.start() < covered:
                continue
            start, covered = _asset_around(text, hit.start())
            if (index, start) not in seen and start >= 0:
                seen.add((index, start))
                block = text[start:covered]
                template, guid = (
                    re.search(r"<Template>([^<]*)</Template>", block),
                    re.search(r"<GUID>(\d+)</GUID>", block),
                )
                if guid and template and template.group(1) != "MapTemplate":
                    numbers = frozenset(int(n) for n in re.findall(r">(\d+)<", block)) - {int(guid.group(1))}
                    found.assets.append(PositionAsset(int(guid.group(1)), template.group(1), block, numbers))
    return found


def _position_elements(root: ET.Element) -> list[tuple[str, ET.Element]]:
    """Each element of an asset's ``Values`` with an ``X`` and a ``Z`` child (a ``...Position`` may leave out the X: a
    value of 0; one without a Z is a screen position), with its path (``Item[2]`` where a tag repeats among its siblings)."""
    values = root.find("Values")
    out: list[tuple[str, ET.Element]] = []

    def walk(e: ET.Element, path: str) -> None:
        tags = {c.tag for c in e}
        if "Z" in tags and ("X" in tags or e.tag.endswith("Position")):
            out.append((path, e))
        for c in e:
            same = [s for s in e if s.tag == c.tag]
            walk(c, f"{path}/{c.tag}" + (f"[{same.index(c) + 1}]" if len(same) > 1 else ""))

    if values is not None:
        walk(values, "Values")
    return out


def _set_axis(e: ET.Element, axis: str, value: float) -> None:
    child = e.find(axis)
    if child is None:  # a value of 0 may be left out
        child = ET.Element(axis)
        e.insert(0, child) if axis == "X" else e.append(child)
    child.text = _num(value)


def _axis(e: ET.Element, axis: str) -> float | None:
    child = e.find(axis)
    try:
        return float(child.text) if child is not None and child.text and child.text.strip() else 0.0
    except ValueError:
        return None


def _map_of(asset: PositionAsset, found: AssetPositions, templates: frozenset[int]) -> set[int]:
    """The maps (``MapTemplate`` GUIDs) an asset names: a template it holds, or one a session it holds belongs to."""
    return (asset.numbers & templates) | {found.sessions[n] for n in asset.numbers if n in found.sessions}


def grow_assets(
    found: AssetPositions,
    guid: int,
    templates: frozenset[int],
    west: int,
    south: int,
    report: Report,
    earlier: dict[int, str] | None = None,
) -> tuple[list[str], dict[int, str]]:
    """The ModOps that move the position blocks of the assets that belong to the map of ``MapTemplate`` ``guid`` by
    (west, south), and those assets' texts with them moved. An asset belongs to the map when it names that template or
    a session of it and no other map; one that names more maps, other maps only or none is kept and listed.
    ``earlier`` holds the texts of earlier growths (the moves add up)."""
    moved = dict(earlier or {})
    if not guid:
        return [], moved
    ops: list[str] = []
    done: set[int] = set()
    for asset in found.assets:
        if asset.guid in done:  # the same asset in a second list: moved once (its moved text is in ``moved``)
            continue
        done.add(asset.guid)
        try:
            root = ET.fromstring(moved.get(asset.guid, asset.text))
        except ET.ParseError:
            report.add("assets", f"{asset.template} {asset.guid}", KEPT, tr("the asset cannot be read"))
            continue
        blocks = _position_elements(root)
        maps = _map_of(asset, found, templates)
        where = f"{asset.template} {asset.guid}"
        if not blocks:
            continue
        if maps != {guid}:
            if not maps:
                reason = tr("names no map: not moved")
            elif guid in maps:
                reason = tr("names other maps too: whose position it is is not known")
            else:
                reason = tr("belongs to another map")
            report.add("assets", where, KEPT, reason, len(blocks))
            continue
        for path, e in blocks:
            ops.extend(_move_block(asset.guid, where, path, e, west, south, report))
        moved[asset.guid] = ET.tostring(root, encoding="unicode")
    return ops, moved


def _move_block(guid: int, where: str, path: str, e: ET.Element, west: int, south: int, report: Report) -> list[str]:
    """One position block moved in place; its ModOp (none, and a ``kept`` line, when a value is not a number)."""
    old = (_axis(e, "X"), _axis(e, "Z"))
    if None in old:
        report.add("assets", f"{where} {path}", KEPT, tr("X or Z is not a number"))
        return []
    new = (_num(old[0] + west), _num(old[1] + south))
    _set_axis(e, "X", old[0] + west)
    _set_axis(e, "Z", old[1] + south)
    report.add("assets", f"{where} {path}", MOVED, f"X {_num(old[0])}, Z {_num(old[1])} -> X {new[0]}, Z {new[1]}")
    tail, e.tail = e.tail, None
    op = modxml.modop(f"//Asset[Values/Standard/GUID='{guid}']/{path}", ET.tostring(e, encoding="unicode"), "replace")
    e.tail = tail
    return [op]


def scan_scripts(scripts: dict[str, str], size: tuple[int, int], report: Report) -> None:
    """Numbers in pairs or triples inside the old map that a script holds: listed, never changed."""
    for path, text in sorted(scripts.items()):
        hits = []
        for n, line in enumerate(text.splitlines(), 1):
            for m in LUA_NUMBERS.finditer(line):
                x, z = float(m.group(1)), float(m.group(3) or m.group(2))
                if 0 < x < size[0] and 0 < z < size[1] and (x >= 16 or z >= 16):
                    hits.append(n)
                    break
        if hits:
            lines = ", ".join(str(h) for h in hits[:8]) + (" ..." if len(hits) > 8 else "")
            report.add(
                path, tr("lines {lines}", lines=lines), KEPT, tr("numbers in the map's range: check by hand"), len(hits)
            )


def grow_map(parts: GrowParts, sides: tuple[int, int, int, int]) -> Grown:
    """Grow every part of a map by ``sides`` (west, south, east, north); returns the new files and the report."""
    check_sides(sides)
    west, south, east, north = sides
    t, stem = parts.template, parts.path.rsplit(".", 1)[0]
    old = t.size
    report = Report(sides)
    files: dict[str, bytes] = {}
    grow_template(t, sides, report, parts.path, parts.widen)
    if parts.session is not None:
        session = sessionfile.read(parts.session)
        sessionfile.grow(session, sides, report, f"{stem}.a7t", parts.widen)
        files[".a7t"] = session.render()
    else:
        report.add(f"{stem}.a7t", "", KEPT, tr("the template has no session file"))
    level = parts.level if parts.level is not None else levelfile.editor_level(*old)
    files[".a7te"] = levelfile.regrown(level, *t.size)
    report.add(f"{stem}.a7te", "Dimensions, Chunks", MOVED, f"{old} -> {t.size}")
    ops, asset = grow_horizons(parts.asset, parts.asset_guid, west, south, report, parts.enlarged)
    moved_assets = dict(parts.moved_assets)
    if parts.positions is not None and (west or south):
        more, moved_assets = grow_assets(
            parts.positions, parts.asset_guid, parts.templates, west, south, report, moved_assets
        )
        ops = [*ops, *more]
    scan_scripts(parts.scripts, old, report)
    report.add("", tr("the island files"), KEPT, tr("their coordinates are their own"))
    report.add("", tr("the camera's start and the generator's spacing"), KEPT, tr("held by the program, not by a file"))
    files[".a7tinfo"] = t.render()
    report.size = t.size
    return Grown(files, ops, report, asset, moved_assets)


def scripts_naming(tree, names: list[str], suffix: str = ".lua", limit: int = 4000) -> dict[str, str]:
    """The scripts of a game that name the template or its session (its file stem, its GUIDs)."""
    out = {}
    wanted = [n.lower() for n in names if n]
    for path in [p for p in tree.paths if p.endswith(suffix)][:limit]:
        text = tree.read(path).decode("utf-8", "replace")
        low = text.lower()
        if any(w in low for w in wanted):
            out[path] = text
    return out
