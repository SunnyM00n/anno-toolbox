"""Pickers for the form's fields: an asset (a search that lists only what the value allows), a colour and a file.

``candidates(doc, spec, query)`` is the asset list a reference may take: the schema's allowed templates, the needed
property and the allowed properties applied, the search over GUID, name and template. ``pick_asset`` shows it in a
dialog. A colour is a signed 32-bit number with the alpha in the high byte (``to_argb`` / ``from_argb``; the games'
own values, such as 553648127 for a translucent white, read that way). A file name is picked from the game's files
that match the value's filter (``files_for``).
"""

from __future__ import annotations

import fnmatch

from PySide6.QtGui import QColor

from ..infra.i18n import alternatives, tr
from ..suite.widgets import pick_from_list, pick_qcolor
from .assetdoc import AssetDocument
from .schema import ValueSpec

LISTED = 300  # rows the asset picker lists at once


def allows(doc: AssetDocument, spec: ValueSpec, guid: int) -> bool:
    """Whether the asset may be named by a value of this definition (its template, its properties)."""
    if spec.allowed_templates and doc.template(guid) not in spec.allowed_templates:
        return False
    needs = {n for n in spec.needed_property.split(";") if n}
    allowed = set(spec.allowed_properties)
    if needs or allowed:
        have = set(doc.layers(guid).property_names())
        if (needs and not needs & have) or (allowed and not allowed & have):
            return False
    return True


def candidates(doc: AssetDocument, spec: ValueSpec, query: str = "", limit: int = LISTED) -> list[int]:
    """The assets a reference may take, matching ``query`` (GUID, name or template, case-insensitive), the change set's
    new assets first."""
    q = query.strip().lower()
    pool = [g for g in doc.changes.new if g not in doc.changes.removed] + [e.guid for e in doc.index.entries]
    out, seen = [], set()
    for guid in pool:
        if guid in seen or not doc.exists(guid):
            continue
        seen.add(guid)
        if q and q not in str(guid) and q not in doc.name(guid).lower() and q not in doc.template(guid).lower():
            continue
        if allows(doc, spec, guid):
            out.append(guid)
            if len(out) >= limit:
                break
    return out


def asset_hint(spec: ValueSpec) -> str:
    parts = []
    if spec.allowed_templates:
        parts.append(tr("templates: {names}", names=", ".join(spec.allowed_templates)))
    if spec.needed_property:
        parts.append(tr("needs: {names}", names=alternatives([n for n in spec.needed_property.split(";") if n])))
    return "; ".join(parts) or tr("any asset")


def pick_asset(parent, doc: AssetDocument, spec: ValueSpec) -> int | None:
    """The GUID chosen among the assets the value may name, None when cancelled."""

    def rows(query: str) -> list[tuple[str, object]]:
        return [
            (f"{doc.name(g) or tr('(no name)')}  ·  {g}  ·  {doc.template(g)}", g) for g in candidates(doc, spec, query)
        ]

    return pick_from_list(parent, tr("Pick an asset for {name}", name=spec.name), asset_hint(spec), rows)


def pick_file(parent, paths: list[str], spec: ValueSpec) -> str | None:
    """A game file chosen among those the value's filter allows, None when cancelled."""

    def rows(query: str) -> list[tuple[str, object]]:
        return [(p, p) for p in files_for(paths, spec.file_filter, query)]

    title = tr("Pick a file for {name}", name=spec.name)
    return pick_from_list(parent, title, spec.file_filter or tr("any file"), rows)


# Colours


def from_argb(text: str) -> QColor:
    """The colour a value means (a signed or unsigned 32-bit number, alpha in the high byte); black when it is none."""
    try:
        n = int(text.strip()) & 0xFFFFFFFF
    except ValueError:
        return QColor(0, 0, 0, 255)
    return QColor((n >> 16) & 255, (n >> 8) & 255, n & 255, n >> 24)


def to_argb(color: QColor) -> str:
    """The value text of a colour: the signed 32-bit number the games' files use."""
    n = (color.alpha() << 24) | (color.red() << 16) | (color.green() << 8) | color.blue()
    return str(n - 2**32 if n >= 2**31 else n)


def pick_colour(parent, text: str) -> str | None:
    """The value text of the colour chosen in the system's dialog (alpha included), None when cancelled."""
    chosen = pick_qcolor(parent, from_argb(text), alpha=True)
    return to_argb(chosen) if chosen is not None else None


# File names


def files_for(paths: list[str], file_filter: str, query: str = "", limit: int = LISTED) -> list[str]:
    """The game's files a file name value may take: those matching its filter (``*.png;*.dds``) and the query."""
    patterns = [p.strip().lower() for p in file_filter.replace(",", ";").split(";") if p.strip()]
    q = query.strip().lower()
    out = []
    for path in paths:
        low = path.lower()
        if (not patterns or any(fnmatch.fnmatchcase(low.rsplit("/", 1)[-1], p) for p in patterns)) and q in low:
            out.append(path)
            if len(out) >= limit:
                break
    return out
