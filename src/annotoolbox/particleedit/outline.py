"""An open effect as the editor's tree: one item per emitter (or, in an ``.rdp``, the header, each material and the
tick data), its fields as rows, its curves as items of their own, and the model config's blocks that place it. No Qt.

``outline(effect)`` is the entry; ``set_row(effect, row, text)`` returns the edit a typed value makes (or why it is
refused). The rows' kinds and meanings are ``fieldinfo``'s. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..formats import efc1701edit
from ..formats.efc1701edit import BraceDoc, Entry, Group, Line
from ..infra.i18n import tr
from ..models import cfgdoc, cfgparts
from ..models.particles2070 import KIND_NAMES
from . import curves, fieldinfo
from .effectdoc import BRACE, RDP, XML, EffectDoc
from .fieldinfo import CHOICE, FILE, FIXED, NUMBER, SWITCH, TEXT, WHOLE, Info

BLOBS = ("P_Starts", "P_Ends", "P_Materials", "T_Positions", "T_Rotations", "T_Scales", "T_Colors", "T_Occlusion")
_QUOTED = re.compile(r'^"[^"]*"$')


@dataclass
class Row:
    """A field as the form shows it: its name, value, how it is edited, and what it stands on (a tag of the tagged
    text, or a line of a 1701 file)."""

    name: str
    value: str
    info: Info
    target: object
    syntax: str

    @property
    def read_only(self) -> bool:
        return self.info.kind == FIXED


@dataclass
class Item:
    """A row of the tree: its title, a key that stays the same across edits, its fields, its curve (a
    ``curves`` model, None for none) and its children."""

    title: str
    key: str
    rows: list[Row] = field(default_factory=list)
    curve: object | None = None
    children: list[Item] = field(default_factory=list)
    note: str = ""

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def outline(effect: EffectDoc) -> list[Item]:
    """The tree of the open effect: the file's items, then the model config's blocks that place it."""
    if effect.doc is None:
        return [Item(effect.title, "file", note=tr("an empty file"))]
    build = {RDP: _rdp, XML: _xml, BRACE: _brace}[effect.syntax]
    items = build(effect)
    if effect.model is not None:
        for k, block in enumerate(effect.model.blocks):
            rows = [_config_row(f) for f in cfgdoc.fields(block)]
            title = tr("in {config}: {name}", config=effect.model.asked.rsplit("/", 1)[-1], name=cfgdoc.kind_of(block))
            items.append(Item(title, f"model/{k}", rows))
    return items


def _tag_row(f: cfgdoc.Field, syntax: str) -> Row:
    found = fieldinfo.info(syntax, f.name)
    if f.binary:
        found = Info(FIXED, found.meaning)
    value = tr("{n} bytes", n=len(f.node.data)) if f.binary else cfgdoc.shown(f.value)
    return Row(f.name, value, found, f.node, syntax)


def _config_row(f: cfgdoc.Field) -> Row:
    """A field of the config block that places the effect, of the kind the object builder gives it."""
    kind = cfgparts.field_kind(f.name, f.value)
    found = {"switch": SWITCH, "number": NUMBER, "colour": NUMBER, "vector": NUMBER, "file": FILE, "kind": FIXED}
    info = Info(FIXED) if f.binary else Info(found.get(kind, TEXT))
    return Row(
        f.name, cfgdoc.shown(f.value) if not f.binary else tr("{n} bytes", n=len(f.node.data)), info, f.node, RDP
    )


def _rdp(effect: EffectDoc) -> list[Item]:
    root = effect.doc.root
    header = [_tag_row(f, RDP) for f in cfgdoc.fields(root) if f.name.split(".")[0] not in BLOBS]
    items = [Item(tr("header"), "header", header)]
    materials = root.find("Materials")
    for k, m in enumerate(materials.children if materials is not None else []):
        items.append(Item(tr("material {n}", n=k), f"material/{k}", [_tag_row(f, RDP) for f in cfgdoc.fields(m)]))
    ticks = [_tag_row(f, RDP) for f in cfgdoc.fields(root) if f.name in BLOBS]
    items.append(Item(tr("tick data"), "ticks", ticks, note=tr("baked by the studio's tool: Tint and Fade change it")))
    return items


def _xml(effect: EffectDoc) -> list[Item]:
    items = []
    for k, node in enumerate(c for c in effect.doc.root.children if c.children):
        names = curves.xml_curves(node)
        rows = [_tag_row(f, XML) for f in cfgdoc.fields(node) if not curves.is_curve_tag(f.name, names)]
        kind = KIND_NAMES.get(int(curves.finite(node.value("EffectType", "0"))), "")
        title = f"{node.name}  {cfgdoc.shown(node.value('Name', ''))}  ({kind})" if kind else node.name
        item = Item(title, f"emitter/{k}", rows)
        for name in names:
            curve = curves.XmlCurve(node, name)
            parts = [_tag_row(cfgdoc.Field(part, node.find(name + part)), XML)
                     for part in ("ValueMin", "ValueMax", "ValueDef", "Varianz", "Size") if node.find(name + part)]  # fmt: skip
            note = fieldinfo.curve_meaning(name)
            item.children.append(Item(tr("{name} (curve)", name=name), f"emitter/{k}/{name}", parts, curve, note=note))
        items.append(item)
    return items


def _brace(effect: EffectDoc) -> list[Item]:
    doc: BraceDoc = effect.doc
    top = next((g for g in doc.tree().groups("Effect")), None)
    if top is None:
        return [Item(effect.title, "file", note=tr("no Effect block"))]
    return [_brace_item(doc, g, f"emitter/{k}", g.name) for k, g in enumerate(top.groups())]


def _brace_item(doc: BraceDoc, group: Group, key: str, title: str) -> Item:
    seen: dict[str, int] = {}
    rows = []
    for e in group.entries():
        seen[e.name] = seen.get(e.name, 0) + 1
        shown = e.name if seen[e.name] == 1 else tr("{name} (line {n})", name=e.name, n=seen[e.name])
        rows.append(Row(shown, _brace_value(e), fieldinfo.info(BRACE, e.name), e, BRACE))
    name = next((e for e in group.entries("Name")), None)
    if name is not None:
        title = f"{title}  {_brace_value(name)}"
    item = Item(title, key, rows)
    for k, sub in enumerate(group.groups()):
        sub_key = f"{key}/{sub.name}#{k}"
        if any(c.name == "Color" for c in sub.groups()):
            item.children.append(Item(tr("{name} (colours)", name=sub.name), sub_key, [], curves.BraceColors(doc, sub)))
        elif sub.entries("Minimum") or sub.entries("Single"):
            child = _brace_item(doc, sub, sub_key, tr("{name} (graph)", name=sub.name))
            child.children, child.curve = [], curves.BraceGraph(doc, sub)
            item.children.append(child)
        else:
            item.children.append(_brace_item(doc, sub, sub_key, sub.name))
    return item


def _brace_value(entry: Entry) -> str:
    if len(entry.values) == 1 and _QUOTED.match(entry.values[0]):
        return entry.values[0][1:-1]
    return " ".join(entry.values)


# Edits


def _number_problem(info: Info, text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return tr("{value} is no number", value=repr(text))
    if not math.isfinite(value):
        return tr("{value} is no number a file can hold", value=repr(text))
    if info.kind == WHOLE and value != int(value):
        return tr("{value} is no whole number", value=repr(text))
    if info.kind == SWITCH:
        return tr("0 or 1")
    if info.limits and not info.limits[0] <= value <= info.limits[1]:
        least, most = info.limits
        if math.isinf(most):
            return tr("{value} is below {least}", value=repr(text), least=f"{least:g}")
        return tr("{value} is outside {least} to {most}", value=repr(text), least=f"{least:g}", most=f"{most:g}")
    return ""


def problem(info: Info, text: str, syntax: str = "") -> str:
    """Why a typed value is refused for a field ("" when it is taken); ``syntax`` is the file's (``BRACE`` has no markup)."""
    if info.kind == FIXED:
        return tr("this value is made with the rest of the file and cannot be typed")
    if any(ord(c) < 32 for c in text):
        return tr("a line break or another control character would break the file")
    if info.kind in (NUMBER, WHOLE) or (info.kind == SWITCH and text not in ("0", "1")):
        if why := _number_problem(info, text):
            return why
    if info.kind == CHOICE and info.choices and text not in {v for v, _n in info.choices}:
        return tr("{value} is not one of {choices}", value=repr(text), choices=", ".join(v for v, _n in info.choices))
    if any(c in text for c in "<>{}") or "CDATA[" in text:
        return tr("< and > or a brace would break the file; leave them out")
    if syntax != BRACE and cfgdoc.BARE_AMPERSAND.search(text):
        return tr("a bare & would break the file; write &amp; for it")
    if info.kind in (FILE, TEXT) and '"' in text:
        return tr('a " would break the file; leave it out')
    return ""


def set_row(effect: EffectDoc, row: Row, text: str) -> cfgdoc.Edit | str:
    """The edit that sets ``row`` to ``text``, or why it is refused."""
    why = problem(row.info, text, row.syntax)
    if why:
        return why
    if row.syntax != BRACE:
        return cfgdoc.set_value(row.target, text, f"{row.name} = {text}")
    return brace_edit(effect.doc, [(row.target, text)])


def brace_edit(doc: BraceDoc, changes: list[tuple[Entry, str]]) -> cfgdoc.Edit:
    """Lines of a 1701 file set as one edit: a quoted value stays quoted."""
    new_text: dict[int, str] = {}
    for entry, text in changes:
        quoted = len(entry.values) == 1 and bool(_QUOTED.match(entry.values[0]))
        values = [f'"{text}"'] if quoted else text.split()
        new_text[id(entry.line)] = efc1701edit.field_text(entry, values)
    lines = [Line(new_text[id(x)], x.end) if id(x) in new_text else x for x in doc.lines]
    label = ", ".join(f"{e.name} = {t}" for e, t in changes)
    return curves.swap(doc.lines, lines, label)
