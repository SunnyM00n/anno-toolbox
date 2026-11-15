"""An asset as a page to print, and the words of its texts: the effective values of an asset (its own, its base
assets', its template's and the defaults', as the form shows them), each with where it comes from and, where this
change set changed it, the game's own value; the texts its text values name in one language with a word count; the
balancing table as a printable page. HTML text only: the window prints it (``printing.py``). Stdlib only.

``effective_rows`` is the values, ``asset_html`` the page, ``asset_texts`` / ``words_of`` the texts and their words,
``table_html`` a balancing table. ``docs/asset-editor.md``, "Print and word counts".
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import escape

from ..infra.i18n import tr
from .assetdoc import AssetDocument
from .balancing import Cell
from .values import Layers
from .xmltree import leaves


@dataclass(frozen=True)
class Row:
    path: str
    value: str
    origin: str
    was: str | None = None  # the game's own value, where it differs


def _overlay(into: ET.Element, source: ET.Element) -> None:
    """``source`` laid over ``into``: a leaf with text replaces, a list (``Item`` children) replaces whole, any other
    block merges; an empty leaf sets nothing."""
    for child in source:
        mine = into.find(child.tag)
        if not len(child):
            if (child.text or "").strip() or mine is None:
                if mine is not None:
                    into.remove(mine)
                into.append(copy.deepcopy(child))
        elif mine is None or any(c.tag == "Item" for c in child) or not len(mine):
            if mine is not None:
                into.remove(mine)
            into.append(copy.deepcopy(child))
        else:
            _overlay(mine, child)


def _effective(layers: Layers, prop: str) -> ET.Element:
    """The merged block of one property over all the layers (the lowest first, the asset's own last)."""
    merged = ET.Element(prop)
    for layer in reversed(layers.layers):
        block = layer.values.find(prop)
        if block is not None:
            _overlay(merged, block)
    return merged


def _indexed(el: ET.Element, prefix: tuple[str, ...]):
    """(path, leaf text) below ``el``; the k-th ``Item`` of a list is ``Item[k]``."""
    seen: dict[str, int] = {}
    for child in el:
        k = seen[child.tag] = seen.get(child.tag, 0) + 1
        name = f"{child.tag}[{k}]" if child.tag == "Item" else child.tag
        here = (*prefix, name)
        if len(child):
            yield from _indexed(child, here)
        else:
            yield here, (child.text or "").strip()


def effective_rows(doc: AssetDocument, guid: int, original: bool = False) -> list[Row]:
    """Every value the asset has, in its template's property order, with its origin; ``original``: the game's own
    value too, where this change set changed it."""
    layers = doc.layers(guid)
    before = doc.layers(guid, original=True) if original else None
    rows = []
    for prop in layers.property_names():
        for path, value in _indexed(_effective(layers, prop), (prop,)):
            plain = tuple(p.split("[")[0] for p in path)
            in_list = "Item" in plain
            at = plain[: plain.index("Item")] if in_list else plain
            origin = layers.lookup(at).origin
            was = None
            if before is not None:
                old = _leaf(before, plain, in_list, path)
                was = old if old != value else None
            rows.append(Row("/".join(path), value, origin, was))
    return rows


def _leaf(layers: Layers, plain: tuple[str, ...], in_list: bool, path: tuple[str, ...]) -> str:
    """The value at ``path`` in ``layers``: a plain lookup, or the same ``Item[k]`` of the list it is in."""
    if not in_list:
        return layers.lookup(plain).text
    head = plain[: plain.index("Item")]
    found = layers.lookup(head)
    items = {"/".join(p): v for p, v in _indexed(found.element, head)} if found.element is not None else {}
    return items.get("/".join(path), "")


def asset_texts(doc: AssetDocument, guid: int, game: dict[str, str], edited: dict[str, str] | None = None):
    """(value path, text id, text) for each text value of the asset: the edited text over the game's."""
    values = doc.asset(guid).find("Values")
    out = []
    for path, ident in leaves(values) if values is not None else ():
        spec = doc.schema.spec_at(path)
        if ident and spec is not None and spec.type == "Text":
            out.append(("/".join(path), ident, (edited or {}).get(ident, game.get(ident, ""))))
    return out


def words_of(texts) -> int:
    """The words of ``asset_texts`` rows (white-space separated, as ``balancing.words`` counts)."""
    return sum(len(text.split()) for _path, _ident, text in texts)


def group_words(
    doc: AssetDocument, guids: list[int], game: dict[str, str], edited: dict[str, str] | None = None
) -> int:
    """The words in the texts of all these assets."""
    return sum(words_of(asset_texts(doc, g, game, edited)) for g in guids if doc.exists(g))


STYLE = (
    "body{font-family:sans-serif;font-size:9pt}h1{font-size:15pt}h2{font-size:11pt;margin-bottom:2px}"
    "table{border-collapse:collapse}td,th{border:1px solid #999;padding:2px 5px;text-align:left}"
    ".own{font-weight:bold}.was{color:#666}"
)


def _table(headers: list[str], rows: list[list[str]], classes: list[str] | None = None) -> str:
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join(
        f'<tr class="{classes[k] if classes else ""}">' + "".join(f"<td>{escape(c)}</td>" for c in row) + "</tr>"
        for k, row in enumerate(rows)
    )
    return f"<table><tr>{head}</tr>{body}</table>"


def asset_html(
    doc: AssetDocument, guid: int, game_texts: dict[str, str] | None = None, language: str = "english",
    original: bool = True,
) -> str:  # fmt: skip
    """The asset as a page: its name and ids, every value with its origin (and the game's value where changed), and
    its texts with their words."""
    rows = effective_rows(doc, guid, original)
    base = doc.base_of(guid)
    parts = [
        f"<html><head><style>{STYLE}</style></head><body>",
        f"<h1>{escape(doc.name(guid) or tr('(no name)'))}</h1>",
        "<p>"
        + tr("GUID {guid} · template {template}", guid=guid, template=escape(doc.template(guid)))
        + (" · " + tr("base asset {guid}", guid=base) if base else "")
        + f" · {doc.changes.game}</p>",
    ]
    wide = any(r.was is not None for r in rows)
    headers = [tr("Value"), tr("Now"), tr("From")] + ([tr("Game's")] if wide else [])
    cells = [[r.path, r.value, r.origin] + ([r.was if r.was is not None else ""] if wide else []) for r in rows]
    parts.append("<h2>" + tr("Values ({n})", n=len(rows)) + "</h2>")
    parts.append(_table(headers, cells, ["own" if r.origin == "own" else "" for r in rows]))
    if game_texts is not None:
        texts = asset_texts(doc, guid, game_texts, doc.changes.texts.get(language))
        parts.append(
            "<h2>" + tr("Texts in {language} ({n} words)", language=escape(language), n=words_of(texts)) + "</h2>"
        )
        parts.append(
            _table([tr("Value"), tr("Text id"), tr("Text")], [list(t) for t in texts])
            if texts
            else "<p>" + tr("none") + "</p>"
        )
    parts.append("</body></html>")
    return "".join(parts)


def table_html(title: str, columns: list[str], rows: list[tuple[int, str, list[Cell]]]) -> str:
    """A balancing table (``balancing.grid``) as a page; a changed cell shows the game's value in brackets."""
    cells = [
        [f"{name} ({guid})"]
        + [c.text + (" " + tr("[game: {value}]", value=c.was) if c.was is not None else "") for c in row]
        for guid, name, row in rows
    ]
    return (
        f"<html><head><style>{STYLE}</style></head><body><h1>{escape(title)}</h1>"
        + _table([tr("Asset"), *columns], cells)
        + "<p>"
        + tr("{n} assets", n=len(rows))
        + "</p></body></html>"
    )
