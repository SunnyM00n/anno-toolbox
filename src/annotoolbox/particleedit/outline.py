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
from ..formats.efc1701edit import CBraceDoc, SEntry, SGroup, SLine
from ..infra.i18n import tr
from ..models import cfgdoc, cfgparts
from ..models.particles2070 import KIND_NAMES
from . import curves, fieldinfo
from .effectdoc import BRACE, RDP, XML, CEffectDoc
from .fieldinfo import CHOICE, FILE, FIXED, NUMBER, SWITCH, TEXT, WHOLE, SInfo

BLOBS = ("P_Starts", "P_Ends", "P_Materials", "T_Positions", "T_Rotations", "T_Scales", "T_Colors", "T_Occlusion")
_QUOTED = re.compile(r'^"[^"]*"$')


@dataclass
class SRow:
    """A field as the form shows it: its name, value, how it is edited, and what it stands on (a tag of the tagged
    text, or a line of a 1701 file)."""

    name: str
    value: str
    field_info: SInfo
    target: object
    syntax: str

    @property
    def read_only(self) -> bool:
        return self.field_info.kind == FIXED


@dataclass
class SItem:
    """A row of the tree: its title, a key that stays the same across edits, its fields, its curve (a
    ``curves`` model, None for none) and its children."""

    title: str
    key: str
    rows: list[SRow] = field(default_factory=list)
    curve: object | None = None
    children: list[SItem] = field(default_factory=list)
    note: str = ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def outline(effect: CEffectDoc) -> list[SItem]:
    """The tree of the open effect: the file's items, then the model config's blocks that place it."""
    if effect.m_doc is None:
        return [SItem(effect.title, "file", note=tr("particles.empty_file"))]
    build = {RDP: _rdp, XML: _xml, BRACE: _brace}[effect.m_syntax]
    items = build(effect)
    if effect.m_model is not None:
        for k, block in enumerate(effect.m_model.blocks):
            rows = [_config_row(field) for field in cfgdoc.fields(block)]
            title = tr(
                "particles.in_config_name", config=effect.m_model.asked.rsplit("/", 1)[-1], name=cfgdoc.kind_of(block)
            )
            items.append(SItem(title, f"model/{k}", rows))
    return items


def _tag_row(field: cfgdoc.SField, syntax: str) -> SRow:
    found = fieldinfo.info(syntax, field.name)
    if field.binary:
        found = SInfo(FIXED, found.meaning)
    value = tr("viewer.n_bytes", n=len(field.node.data)) if field.binary else cfgdoc.shown(field.value)
    return SRow(field.name, value, found, field.node, syntax)


def _config_row(field: cfgdoc.SField) -> SRow:
    """A field of the config block that places the effect, of the kind the object builder gives it."""
    kind = cfgparts.field_kind(field.name, field.value)
    found = {"switch": SWITCH, "number": NUMBER, "colour": NUMBER, "vector": NUMBER, "file": FILE, "kind": FIXED}
    field_info = SInfo(FIXED) if field.binary else SInfo(found.get(kind, TEXT))
    return SRow(
        field.name,
        cfgdoc.shown(field.value) if not field.binary else tr("viewer.n_bytes", n=len(field.node.data)),
        field_info,
        field.node,
        RDP,
    )


def _rdp(effect: CEffectDoc) -> list[SItem]:
    root = effect.m_doc.root
    header = [_tag_row(field, RDP) for field in cfgdoc.fields(root) if field.name.split(".")[0] not in BLOBS]
    items = [SItem(tr("particles.header"), "header", header)]
    materials = root.find("Materials")
    for k, material in enumerate(materials.children if materials is not None else []):
        items.append(
            SItem(
                tr("particles.material_n", n=k),
                f"material/{k}",
                [_tag_row(field, RDP) for field in cfgdoc.fields(material)],
            )
        )
    ticks = [_tag_row(field, RDP) for field in cfgdoc.fields(root) if field.name in BLOBS]
    items.append(SItem(tr("particles.tick_data"), "ticks", ticks, note=tr("particles.baked_studios_tool_tint_fade")))
    return items


def _xml(effect: CEffectDoc) -> list[SItem]:
    items = []
    for k, node in enumerate(child for child in effect.m_doc.root.children if child.children):
        names = curves.xml_curves(node)
        rows = [_tag_row(field, XML) for field in cfgdoc.fields(node) if not curves.is_curve_tag(field.name, names)]
        kind = KIND_NAMES.get(int(curves.finite(node.value("EffectType", "0"))), "")
        title = f"{node.name}  {cfgdoc.shown(node.value('Name', ''))}  ({kind})" if kind else node.name
        item = SItem(title, f"emitter/{k}", rows)
        for name in names:
            curve = curves.SXmlCurve(node, name)
            parts = [_tag_row(cfgdoc.SField(part, node.find(name + part)), XML)
                     for part in ("ValueMin", "ValueMax", "ValueDef", "Varianz", "Size") if node.find(name + part)]  # fmt: skip
            meaning = fieldinfo.curve_meaning(name)
            note = tr(meaning) if meaning else ""
            item.children.append(
                SItem(tr("particles.name_curve", name=name), f"emitter/{k}/{name}", parts, curve, note=note)
            )
        items.append(item)
    return items


def _brace(effect: CEffectDoc) -> list[SItem]:
    doc: CBraceDoc = effect.m_doc
    top = next((group for group in doc.tree().groups("Effect")), None)
    if top is None:
        return [SItem(effect.title, "file", note=tr("particles.no_effect_block"))]
    return [_brace_item(doc, group, f"emitter/{k}", group.name) for k, group in enumerate(top.groups())]


def _brace_item(doc: CBraceDoc, group: SGroup, key: str, title: str) -> SItem:
    seen: dict[str, int] = {}
    rows = []
    for entry in group.entries():
        seen[entry.name] = seen.get(entry.name, 0) + 1
        shown = (
            entry.name if seen[entry.name] == 1 else tr("particles.name_line_n", name=entry.name, n=seen[entry.name])
        )
        rows.append(SRow(shown, _brace_value(entry), fieldinfo.info(BRACE, entry.name), entry, BRACE))
    name = next((entry for entry in group.entries("Name")), None)
    if name is not None:
        title = f"{title}  {_brace_value(name)}"
    item = SItem(title, key, rows)
    for k, sub in enumerate(group.groups()):
        sub_key = f"{key}/{sub.name}#{k}"
        if any(group.name == "Color" for group in sub.groups()):
            item.children.append(
                SItem(tr("particles.name_colours", name=sub.name), sub_key, [], curves.SBraceColors(doc, sub))
            )
        elif sub.entries("Minimum") or sub.entries("Single"):
            child = _brace_item(doc, sub, sub_key, tr("particles.name_graph", name=sub.name))
            child.children, child.curve = [], curves.SBraceGraph(doc, sub)
            item.children.append(child)
        else:
            item.children.append(_brace_item(doc, sub, sub_key, sub.name))
    return item


def _brace_value(entry: SEntry) -> str:
    if len(entry.values) == 1 and _QUOTED.match(entry.values[0]):
        return entry.values[0][1:-1]
    return " ".join(entry.values)


# Edits


def _number_problem(field_info: SInfo, text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return tr("objects.value_no_number", value=repr(text))
    if not math.isfinite(value):
        return tr("particles.no_number_file_hold", value=repr(text))
    if field_info.kind == WHOLE and value != int(value):
        return tr("particles.no_whole_number", value=repr(text))
    if field_info.kind == SWITCH:
        return tr("particles.0_or_1")
    if field_info.limits and not field_info.limits[0] <= value <= field_info.limits[1]:
        least, most = field_info.limits
        if math.isinf(most):
            return tr("particles.value_is_below_least", value=repr(text), least=f"{least:g}")
        return tr("particles.value_outside_least_to_most", value=repr(text), least=f"{least:g}", most=f"{most:g}")
    return ""


def problem(field_info: SInfo, text: str, syntax: str = "") -> str:
    """Why a typed value is refused for a field ("" when it is taken); ``syntax`` is the file's (``BRACE`` has no markup)."""
    if field_info.kind == FIXED:
        return tr("particles.value_made_rest_file_cannot")
    if any(ord(child) < 32 for child in text):
        return tr("particles.line_break_another_control_character")
    if field_info.kind in (NUMBER, WHOLE) or (field_info.kind == SWITCH and text not in ("0", "1")):
        if why := _number_problem(field_info, text):
            return why
    if field_info.kind == CHOICE and field_info.choices and text not in {value for value, _label in field_info.choices}:
        return tr(
            "particles.value_not_one_choices",
            value=repr(text),
            choices=", ".join(value for value, _label in field_info.choices),
        )
    if any(child in text for child in "<>{}") or "CDATA[" in text:
        return tr("particles.brace_break_file_leave_out")
    if syntax != BRACE and cfgdoc.BARE_AMPERSAND.search(text):
        return tr("particles.bare_break_file_write_amp")
    if field_info.kind in (FILE, TEXT) and '"' in text:
        return tr("particles.break_file_leave_out")
    return ""


def set_row(effect: CEffectDoc, row: SRow, text: str) -> cfgdoc.SEdit | str:
    """The edit that sets ``row`` to ``text``, or why it is refused."""
    why = problem(row.field_info, text, row.syntax)
    if why:
        return why
    if row.syntax != BRACE:
        return cfgdoc.set_value(row.target, text, f"{row.name} = {text}")
    return brace_edit(effect.m_doc, [(row.target, text)])


def brace_edit(doc: CBraceDoc, changes: list[tuple[SEntry, str]]) -> cfgdoc.SEdit:
    """Lines of a 1701 file set as one edit: a quoted value stays quoted."""
    new_text: dict[int, str] = {}
    for entry, text in changes:
        quoted = len(entry.values) == 1 and bool(_QUOTED.match(entry.values[0]))
        values = [f'"{text}"'] if quoted else text.split()
        new_text[id(entry.line)] = efc1701edit.field_text(entry, values)
    lines = [SLine(new_text[id(x)], x.end) if id(x) in new_text else x for x in doc.m_lines]
    label = ", ".join(f"{entry.name} = {new_text}" for entry, new_text in changes)
    return curves.swap(doc.m_lines, lines, label)
