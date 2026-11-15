"""Emitters added to and removed from an open effect: a new one is a copy of an emitter of the file or of another
effect file of the game (so it is of that kind, with its textures, curves and colours), made ready to start with the
effect at its origin. No Qt.

``kinds(effect)`` lists the kinds the file holds, ``add_like(effect, k)`` copies emitter ``k``, ``game_kinds`` and
``add_donor`` bring a kind the file lacks, ``remove(effect, k)`` drops one (each a ``cfgdoc.Edit``, or why not). An
``.rdp`` has no emitters (the effect is baked). docs/particle-editor.md, "New emitters".
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from ..formats import efc1701edit
from ..formats.efc1701edit import BraceDoc, Group, Line
from ..infra.i18n import tr
from ..models import cfgdoc
from . import curves, fieldinfo
from .effectdoc import BRACE, XML, EffectDoc

_INDEX = re.compile(r"\d+")
_QUOTED = re.compile(r'^"(.*)"$')
COPY_OF = "Copy of "  # the studio tool's own name for a copied emitter (1701's files hold them)
ORIGIN_2070 = ("Delay", "OffsetX", "OffsetY", "OffsetZ")  # a copy starts with the effect, at its origin
ORIGIN_1701 = {"StartDelay": "0", "Position": "0.000000 0.000000 0.000000"}


def emitter_index(key: str) -> int | None:
    """The number of the emitter an outline key (``emitter/3`` or one of its curves) stands for."""
    parts = key.split("/")
    return int(parts[1]) if len(parts) > 1 and parts[0] == "emitter" and parts[1].isdigit() else None


def _xml_emitters(effect: EffectDoc) -> list:
    return [c for c in effect.doc.root.children if c.children]


def _brace_top(effect: EffectDoc) -> Group | None:
    return next(iter(effect.doc.tree().groups("Effect")), None)


def count(effect: EffectDoc) -> int:
    """How many emitters the file holds."""
    if effect.doc is None:
        return 0
    if effect.syntax == XML:
        return len(_xml_emitters(effect))
    top = _brace_top(effect) if effect.syntax == BRACE else None
    return len(top.groups()) if top is not None else 0


def kinds(effect: EffectDoc) -> list[tuple[str, int]]:
    """(the kind's name, the first emitter of it) for each kind the file holds, in file order."""
    out: dict[str, int] = {}
    if effect.doc is None:
        return []
    if effect.syntax == XML:
        names = dict(fieldinfo.KINDS_2070)
        for k, node in enumerate(_xml_emitters(effect)):
            label = names.get(str(int(curves.finite(node.value("EffectType", "0")))), "")
            out.setdefault(tr(label) if label else node.value("EffectType", "?"), k)
    elif effect.syntax == BRACE and (top := _brace_top(effect)) is not None:
        for k, group in enumerate(top.groups()):
            out.setdefault(group.name, k)
    return list(out.items())


def add_like(effect: EffectDoc, k: int) -> cfgdoc.Edit | str:
    """The edit that appends a copy of emitter ``k`` at the end of the file, or why that cannot be."""
    return add_from(effect, effect, k)


def add_from(effect: EffectDoc, donor: EffectDoc, k: int) -> cfgdoc.Edit | str:
    """The edit that appends a copy of ``donor``'s emitter ``k`` (another file of the game, or the effect itself) at
    the end of ``effect``, or why that cannot be."""
    if not effect.editable:
        return tr("this file is read only (it does not read back byte for byte)")
    if donor.syntax != effect.syntax or not 0 <= k < count(donor):
        return tr("this effect has no emitter to copy")
    if effect.syntax == XML and count(effect):
        return _add_xml(effect, _xml_emitters(donor)[k])
    if effect.syntax == BRACE and _brace_top(effect) is not None:
        return _add_brace(effect, _brace_top(donor).groups()[k])
    return tr("this effect has no emitter to copy")


def remove(effect: EffectDoc, k: int) -> cfgdoc.Edit | str:
    """The edit that removes emitter ``k`` (an effect keeps one at least), or why that cannot be. 2070's and 1404's
    later emitters take the tag of the one before (``Effect0`` ... stay without a gap, as every file of the games)."""
    if not effect.editable:
        return tr("this file is read only (it does not read back byte for byte)")
    if not 0 <= k < count(effect):
        return tr("this effect has no such emitter")
    if count(effect) == 1:
        return tr("an effect keeps one emitter at least")
    if effect.syntax == XML:
        return _remove_xml(effect, k)
    group = _brace_top(effect).groups()[k]
    gone = {id(line) for line in group.lines()}
    lines = [line for line in effect.doc.lines if id(line) not in gone]
    return curves.swap(effect.doc.lines, lines, tr("remove the emitter {name}", name=group.name))


def _remove_xml(effect: EffectDoc, k: int) -> cfgdoc.Edit:
    root, emitters = effect.doc.root, _xml_emitters(effect)
    node, later = emitters[k], emitters[k + 1 :]
    names = [(e, e.name, emitters[k + i].name) for i, e in enumerate(later)]  # (node, its tag, the tag it takes)
    at = next(i for i, c in enumerate(root.children) if c is node)

    def do() -> None:
        root.children[:] = [c for c in root.children if c is not node]
        for e, _old, new in names:
            e.name = new

    def undo() -> None:
        for e, old, _new in names:
            e.name = old
        root.children.insert(at, node)

    return cfgdoc.Edit(tr("remove the emitter {name}", name=cfgdoc.shown(node.value("Name", ""))), do, undo)


@dataclass(frozen=True)
class Donor:
    """The first emitter of a kind in the game's effect files: the kind's name, the file, the emitter's number."""

    kind: str
    path: str
    k: int
    syntax: str


def game_kinds(files, game: str, paths: list[str]) -> list[Donor]:
    """The first emitter of each kind in the effect files ``paths`` of the game (``.efc``; a file that does not read
    is passed over), in path order: where a kind the open file lacks is copied from (about a second for a game)."""
    out: dict[tuple[str, str], Donor] = {}
    for path in sorted(p for p in paths if p.endswith(".efc")):
        try:
            doc = EffectDoc(files, game, path, files.read(path))
        except (OSError, ValueError, KeyError):
            continue
        for name, k in kinds(doc):
            out.setdefault((doc.syntax, name), Donor(name, path, k, doc.syntax))
    return list(out.values())


def lacking(effect: EffectDoc, donors: list[Donor]) -> list[Donor]:
    """The donors of the kinds the effect lacks, of its own syntax (a 2070 file takes no 1701 emitter)."""
    have = {name for name, _k in kinds(effect)}
    return [d for d in donors if d.syntax == effect.syntax and d.kind not in have]


def add_donor(effect: EffectDoc, donor: Donor) -> cfgdoc.Edit | str:
    """The edit that appends a copy of the donor's emitter (read from the game now), or why that cannot be."""
    try:
        other = EffectDoc(effect.files, effect.game, donor.path, effect.files.read(donor.path))
    except (OSError, ValueError, KeyError) as exc:
        return str(exc)
    return add_from(effect, other, donor.k)


def _unused(name: str, taken: set[str]) -> str:
    named, n = COPY_OF + name, 2
    while named in taken:
        named, n = f"{COPY_OF}{name} {n}", n + 1
    return named


def _zero_like(text: str) -> str:
    return "0.000000" if "." in text else "0"


def _add_xml(effect: EffectDoc, template) -> cfgdoc.Edit:
    root, emitters = effect.doc.root, _xml_emitters(effect)
    new = copy.deepcopy(template)
    new.name = f"Effect{max(int(_INDEX.search(n.name).group(0)) for n in emitters if _INDEX.search(n.name)) + 1}"
    taken = {cfgdoc.shown(n.value("Name", "")) for n in emitters}
    name = new.find("Name")
    if name is not None:
        name.text = cfgdoc.stored(_unused(cfgdoc.shown(name.text or ""), taken))
    for tag in ORIGIN_2070:
        leaf = new.find(tag)
        if leaf is not None and leaf.text is not None:
            leaf.text = _zero_like(leaf.text)
    at = next(i for i, c in enumerate(root.children) if c is emitters[-1]) + 1
    return cfgdoc.insert(
        root, at, [new], tr("add an emitter like {name}", name=cfgdoc.shown(template.value("Name", "")))
    )


def _add_brace(effect: EffectDoc, template: Group) -> cfgdoc.Edit:
    doc: BraceDoc = effect.doc
    top = _brace_top(effect)
    made = {id(line): Line(line.text, line.end) for line in template.lines()}
    taken = {_unquoted(e.values) for g in top.groups() for e in g.entries("Name")}
    for entry in template.entries():
        if entry.name == "Name":
            values = [f'"{_unused(_unquoted(entry.values), taken)}"']
        elif entry.name in ORIGIN_1701:
            values = ORIGIN_1701[entry.name].split()
        else:
            continue
        made[id(entry.line)].text = efc1701edit.field_text(entry, values)
    at = doc.index(top.close)
    lines = [*doc.lines[:at], *(made[id(line)] for line in template.lines()), *doc.lines[at:]]
    return curves.swap(doc.lines, lines, tr("add an emitter like {name}", name=template.name))


def _unquoted(values: list[str]) -> str:
    found = _QUOTED.match(values[0]) if values else None
    return found.group(1) if found else " ".join(values)
