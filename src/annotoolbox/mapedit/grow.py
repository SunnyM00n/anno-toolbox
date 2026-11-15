"""A map grown on any of its four sides: the template, its session, its level header, the horizon islands of its
``MapTemplate`` asset, and a scan of the scripts for numbers that may be positions, with one report of it all.

``grow_map(parts, sides)`` is the entry (``GrowParts`` in, ``Grown`` out); every step adds to the ``Report``
(``references``). docs/map-editor.md "Growing a map on any side" and the inventory beside it.
"""

from __future__ import annotations

import re
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


@dataclass
class Grown:
    files: dict[str, bytes]  # suffix (".a7tinfo", ".a7t", ".a7te") -> bytes
    asset_ops: list[str]  # ModOps on the asset list (117's horizon islands)
    report: Report
    asset_text: str = ""  # the MapTemplate asset with its horizons moved (a further growth starts from it)


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
    scan_scripts(parts.scripts, old, report)
    report.add("", tr("the island files"), KEPT, tr("their coordinates are their own"))
    report.add("", tr("the camera's start and the generator's spacing"), KEPT, tr("held by the program, not by a file"))
    files[".a7tinfo"] = t.render()
    report.size = t.size
    return Grown(files, ops, report, asset if ops else parts.asset)


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
