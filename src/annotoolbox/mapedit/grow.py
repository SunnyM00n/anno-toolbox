"""A map grown on any of its four sides: the template, its session, its level header, the horizon islands of its
``SMapTemplate`` asset, the position blocks of the assets that belong to the map, and a scan of the scripts for
numbers that may be positions, with one report of it all.

``grow_map(parts, sides)`` is the entry (``SGrowParts`` in, ``SGrown`` out); every step adds to the ``CReport``
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
from .references import KEPT, MOVED, CReport
from .template import SMapTemplate

STEP = levelfile.CHUNK  # a side grows by whole chunks of the level header
LUA_NUMBERS = re.compile(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)(?:\s*,\s*(-?\d+(?:\.\d+)?))?")


def check_sides(sides: tuple[int, int, int, int]) -> None:
    """``ValueError`` unless every side is a whole number of chunks and something grows."""
    if any(side < 0 or side % STEP for side in sides):
        raise ValueError(tr("maps.side_grows_multiple_tiles", step=STEP))
    if not any(sides):
        raise ValueError(tr("maps.no_side_grows"))


@dataclass
class SGrowParts:
    """What a growth reads: the template (edited in place), and what else the map has (None where it has not)."""

    template: SMapTemplate
    path: str  # the template's .a7tinfo game path
    session: bytes | None = None  # the .a7t
    level: bytes | None = None  # the .a7te
    asset: str = ""  # the MapTemplate asset as the asset list has it
    asset_guid: int = 0
    scripts: dict[str, str] = field(default_factory=dict)  # game path -> text of the scripts to scan
    widen: bool = True  # the playable area grows with the map (else it is only moved)
    enlarged: bool = False  # the template is the asset's enlarged one (its horizon list is the enlarged one)
    positions: SAssetPositions | None = None  # the asset lists' position blocks (None: not scanned)
    templates: frozenset[int] = frozenset()  # the GUIDs of every MapTemplate asset of the game
    moved_assets: dict[int, str] = field(default_factory=dict)  # asset GUID -> its text after earlier growths


@dataclass
class SGrown:
    files: dict[str, bytes]  # suffix (".a7tinfo", ".a7t", ".a7te") -> bytes
    asset_ops: list[str]  # ModOps on the asset list (the horizon islands, the assets that hold the map's positions)
    report: CReport
    asset_text: str = ""  # the MapTemplate asset with its horizons moved (a further growth starts from it)
    moved_assets: dict[int, str] = field(default_factory=dict)  # the other assets moved so far (same use)


def grow_template(
    map_template: SMapTemplate, sides: tuple[int, int, int, int], report: CReport, file: str, widen: bool
) -> None:
    west, south, east, north = sides
    old = map_template.size
    map_template.size = (old[0] + west + east, old[1] + south + north)
    report.add(file, "MapTemplate/Size", MOVED, f"{old} -> {map_template.size}")
    pa = sessionfile.grown_area(map_template.playable, sides, widen)
    report.add(file, "MapTemplate/PlayableArea", MOVED, f"{map_template.playable} -> {pa}")
    map_template.playable = pa
    if map_template.initial is not None and any(map_template.initial):
        ipa = (
            map_template.initial[0] + west,
            map_template.initial[1] + south,
            map_template.initial[2] + west,
            map_template.initial[3] + south,
        )
        report.add(file, "MapTemplate/InitialPlayableArea", MOVED, f"{map_template.initial} -> {ipa}")
        map_template.initial = ipa
    for element in map_template.elements:
        element.x, element.z = element.x + west, element.z + south
    if map_template.elements:
        report.add(file, "MapTemplate/TemplateElement/Element/Position", MOVED, "x and z", len(map_template.elements))
    for name in ("EnlargementOffset", "IslandShift"):
        if map_template.value(name) is not None:
            report.add(file, f"MapTemplate/{name}", KEPT, tr("maps.kept_meaning_past_0_not"))


HORIZONS, ENLARGED_HORIZONS = "HorizonIslands", "EnlargedHorizonIslands"


def horizon_list(asset: str, name: str) -> str:
    """The asset's horizon list ``name`` as its text ("" when it has none)."""
    match = re.search(rf"<{name}>.*?</{name}>", asset, re.S)
    return match.group(0) if match else ""


def _moved_position(match: re.Match, west: int, south: int) -> str:
    """One ``Position`` block moved by (west, south); a value the asset leaves out (0) is written."""
    inner = match.group(1)
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
    asset: str, guid: int, west: int, south: int, report: CReport, enlarged: bool = False
) -> tuple[list[str], str]:
    """The ModOps that move each horizon island of the template's list (the enlarged map's list for an enlarged
    template: the asset serves both maps, the other list is kept) by (west, south), and the asset with them moved."""
    name, other = (ENLARGED_HORIZONS, HORIZONS) if enlarged else (HORIZONS, ENLARGED_HORIZONS)
    block = horizon_list(asset, name)
    if not block or not (west or south):
        return [], asset
    moved, count = re.subn(
        r"<Position>(.*?)</Position>", lambda match: _moved_position(match, west, south), block, flags=re.S
    )
    report.add("assets", f"MapTemplate {guid} {name}/Item/Position", MOVED, "X and Z", count)
    if horizon_list(asset, other):
        report.add("assets", f"MapTemplate {guid} {other}", KEPT, tr("maps.other_map_same_asset_uses"))
    path = f"//Asset[Values/Standard/GUID='{guid}']/Values/MapTemplate/{name}"
    return [modxml.modop(path, moved, "replace")], asset.replace(block, moved, 1)


def _num(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


@dataclass(frozen=True)
class SPositionAsset:
    """An asset (not a ``SMapTemplate``) with a block that has an ``X`` and a ``Z``: its text and the numbers it holds."""

    guid: int
    template: str
    text: str
    numbers: frozenset[int]  # every whole number a value of the asset holds (its GUID references among them)


@dataclass
class SAssetPositions:
    assets: list[SPositionAsset] = field(default_factory=list)
    sessions: dict[int, int] = field(default_factory=dict)  # a session asset's GUID -> its MapTemplate's GUID


def _asset_around(text: str, at: int) -> tuple[int, int]:
    """The span of the top-level asset that holds offset ``at`` (an ``<Asset>`` is followed by its ``<Template>``)."""
    start = text.rfind("<Asset>", 0, max(text.rfind("<Template>", 0, at), 0))
    return start, text.find("</Asset>", at) + len("</Asset>")


def scan_assets(texts: Iterable[str]) -> SAssetPositions:
    """The assets of the asset lists ``texts`` that hold an ``X``/``Z`` block (the ``SMapTemplate`` assets apart: their
    horizon islands are moved with their own step) and each session asset's template. One pass of finds, not a parse."""
    found = SAssetPositions()
    seen: set[tuple[int, int]] = set()
    for index, text in enumerate(texts):
        for match in re.finditer(r"<MapTemplate>(\d+)</MapTemplate>", text):
            start, _end = _asset_around(text, match.start())
            guid = re.search(r"<GUID>(\d+)</GUID>", text[start : match.start()])
            if guid:
                found.sessions[int(guid.group(1))] = int(match.group(1))
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
                    numbers = frozenset(int(number) for number in re.findall(r">(\d+)<", block)) - {int(guid.group(1))}
                    found.assets.append(SPositionAsset(int(guid.group(1)), template.group(1), block, numbers))
    return found


def _position_elements(root: ET.Element) -> list[tuple[str, ET.Element]]:
    """Each element of an asset's ``Values`` with an ``X`` and a ``Z`` child (a ``...Position`` may leave out the X: a
    value of 0; one without a Z is a screen position), with its path (``Item[2]`` where a tag repeats among its siblings)."""
    values = root.find("Values")
    out: list[tuple[str, ET.Element]] = []

    def walk(node: ET.Element, path: str) -> None:
        tags = {child.tag for child in node}
        if "Z" in tags and ("X" in tags or node.tag.endswith("Position")):
            out.append((path, node))
        for child in node:
            same = [sibling for sibling in node if sibling.tag == child.tag]
            walk(child, f"{path}/{child.tag}" + (f"[{same.index(child) + 1}]" if len(same) > 1 else ""))

    if values is not None:
        walk(values, "Values")
    return out


def _set_axis(node: ET.Element, axis: str, value: float) -> None:
    child = node.find(axis)
    if child is None:  # a value of 0 may be left out
        child = ET.Element(axis)
        node.insert(0, child) if axis == "X" else node.append(child)
    child.text = _num(value)


def _axis(node: ET.Element, axis: str) -> float | None:
    child = node.find(axis)
    try:
        return float(child.text) if child is not None and child.text and child.text.strip() else 0.0
    except ValueError:
        return None


def _map_of(asset: SPositionAsset, found: SAssetPositions, templates: frozenset[int]) -> set[int]:
    """The maps (``SMapTemplate`` GUIDs) an asset names: a template it holds, or one a session it holds belongs to."""
    return (asset.numbers & templates) | {
        found.sessions[number] for number in asset.numbers if number in found.sessions
    }


def grow_assets(
    found: SAssetPositions,
    guid: int,
    templates: frozenset[int],
    west: int,
    south: int,
    report: CReport,
    earlier: dict[int, str] | None = None,
) -> tuple[list[str], dict[int, str]]:
    """The ModOps that move the position blocks of the assets that belong to the map of ``SMapTemplate`` ``guid`` by
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
            report.add("assets", f"{asset.template} {asset.guid}", KEPT, tr("maps.asset_cannot_read"))
            continue
        blocks = _position_elements(root)
        maps = _map_of(asset, found, templates)
        where = f"{asset.template} {asset.guid}"
        if not blocks:
            continue
        if maps != {guid}:
            if not maps:
                reason = tr("maps.names_no_map_not_moved")
            elif guid in maps:
                reason = tr("maps.names_other_maps_too_whose")
            else:
                reason = tr("maps.belongs_to_another_map")
            report.add("assets", where, KEPT, reason, len(blocks))
            continue
        for path, node in blocks:
            ops.extend(_move_block(asset.guid, where, path, node, west, south, report))
        moved[asset.guid] = ET.tostring(root, encoding="unicode")
    return ops, moved


def _move_block(
    guid: int, where: str, path: str, node: ET.Element, west: int, south: int, report: CReport
) -> list[str]:
    """One position block moved in place; its ModOp (none, and a ``kept`` line, when a value is not a number)."""
    old = (_axis(node, "X"), _axis(node, "Z"))
    if None in old:
        report.add("assets", f"{where} {path}", KEPT, tr("maps.x_z_not_number"))
        return []
    new = (_num(old[0] + west), _num(old[1] + south))
    _set_axis(node, "X", old[0] + west)
    _set_axis(node, "Z", old[1] + south)
    report.add("assets", f"{where} {path}", MOVED, f"X {_num(old[0])}, Z {_num(old[1])} -> X {new[0]}, Z {new[1]}")
    tail, node.tail = node.tail, None
    op = modxml.modop(
        f"//Asset[Values/Standard/GUID='{guid}']/{path}", ET.tostring(node, encoding="unicode"), "replace"
    )
    node.tail = tail
    return [op]


def scan_scripts(scripts: dict[str, str], size_tiles: tuple[int, int], report: CReport) -> None:
    """Numbers in pairs or triples inside the old map that a script holds: listed, never changed."""
    for path, text in sorted(scripts.items()):
        hits = []
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in LUA_NUMBERS.finditer(line):
                x, z = float(match.group(1)), float(match.group(3) or match.group(2))
                if 0 < x < size_tiles[0] and 0 < z < size_tiles[1] and (x >= 16 or z >= 16):
                    hits.append(line_number)
                    break
        if hits:
            lines = ", ".join(str(hit) for hit in hits[:8]) + (" ..." if len(hits) > 8 else "")
            report.add(path, tr("maps.lines", lines=lines), KEPT, tr("maps.numbers_maps_range_check_hand"), len(hits))


def grow_map(parts: SGrowParts, sides: tuple[int, int, int, int]) -> SGrown:
    """Grow every part of a map by ``sides`` (west, south, east, north); returns the new files and the report."""
    check_sides(sides)
    west, south, east, north = sides
    map_template, stem = parts.template, parts.path.rsplit(".", 1)[0]
    old = map_template.size
    report = CReport(sides)
    files: dict[str, bytes] = {}
    grow_template(map_template, sides, report, parts.path, parts.widen)
    if parts.session is not None:
        session = sessionfile.read(parts.session)
        sessionfile.grow(session, sides, report, f"{stem}.a7t", parts.widen)
        files[".a7t"] = session.render()
    else:
        report.add(f"{stem}.a7t", "", KEPT, tr("maps.template_no_session_file"))
    level = parts.level if parts.level is not None else levelfile.editor_level(*old)
    files[".a7te"] = levelfile.regrown(level, *map_template.size)
    report.add(f"{stem}.a7te", "Dimensions, Chunks", MOVED, f"{old} -> {map_template.size}")
    ops, asset = grow_horizons(parts.asset, parts.asset_guid, west, south, report, parts.enlarged)
    moved_assets = dict(parts.moved_assets)
    if parts.positions is not None and (west or south):
        more, moved_assets = grow_assets(
            parts.positions, parts.asset_guid, parts.templates, west, south, report, moved_assets
        )
        ops = [*ops, *more]
    scan_scripts(parts.scripts, old, report)
    report.add("", tr("maps.island_files"), KEPT, tr("maps.coordinates_own"))
    report.add("", tr("maps.cameras_start_generators_spacing"), KEPT, tr("maps.held_program_not_file"))
    files[".a7tinfo"] = map_template.render()
    report.m_size = map_template.size
    return SGrown(files, ops, report, asset, moved_assets)


def scripts_naming(tree, names: list[str], suffix: str = ".lua", limit: int = 4000) -> dict[str, str]:
    """The scripts of a game that name the template or its session (its file stem, its GUIDs)."""
    out = {}
    wanted = [name.lower() for name in names if name]
    for path in [candidate for candidate in tree.m_paths if candidate.endswith(suffix)][:limit]:
        text = tree.read(path).decode("utf-8", "replace")
        low = text.lower()
        if any(word in low for word in wanted):
            out[path] = text
    return out
