"""Emitters added to and removed from an open effect: a new one is a copy of an emitter of the file or of another
effect file of the game (so it is of that kind, with its textures, curves and colours), made ready to start with the
effect at its origin. No Qt.

``kinds(effect)`` lists the kinds the file holds, ``add_like(effect, k)`` copies emitter ``k``, ``game_kinds`` and
``add_donor`` bring a kind the file lacks, ``remove(effect, k)`` drops one (each a ``cfgdoc.SEdit``, or why not). An
``.rdp`` has no emitters (the effect is baked). docs/particle-editor.md, "New emitters".
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

from ..formats import efc1701edit
from ..formats.efc1701edit import CBraceDoc, SGroup, SLine
from ..infra.i18n import tr
from ..models import cfgdoc
from . import curves, fieldinfo
from .effectdoc import BRACE, XML, CEffectDoc

_INDEX = re.compile(r"\d+")
_QUOTED = re.compile(r'^"(.*)"$')
COPY_OF = "Copy of "  # the studio tool's own name for a copied emitter (1701's files hold them)
ORIGIN_2070 = ("Delay", "OffsetX", "OffsetY", "OffsetZ")  # a copy starts with the effect, at its origin
ORIGIN_1701 = {"StartDelay": "0", "Position": "0.000000 0.000000 0.000000"}


def emitter_index(key: str) -> int | None:
    """The number of the emitter an outline key (``emitter/3`` or one of its curves) stands for."""
    parts = key.split("/")
    return int(parts[1]) if len(parts) > 1 and parts[0] == "emitter" and parts[1].isdigit() else None


def _xml_emitters(effect: CEffectDoc) -> list:
    return [child for child in effect.m_doc.root.children if child.children]


def _brace_top(effect: CEffectDoc) -> SGroup | None:
    return next(iter(effect.m_doc.tree().groups("Effect")), None)


def count(effect: CEffectDoc) -> int:
    """How many emitters the file holds."""
    if effect.m_doc is None:
        return 0
    if effect.m_syntax == XML:
        return len(_xml_emitters(effect))
    top = _brace_top(effect) if effect.m_syntax == BRACE else None
    return len(top.groups()) if top is not None else 0


def kinds(effect: CEffectDoc) -> list[tuple[str, int]]:
    """(the kind's name, the first emitter of it) for each kind the file holds, in file order."""
    out: dict[str, int] = {}
    if effect.m_doc is None:
        return []
    if effect.m_syntax == XML:
        names = dict(fieldinfo.KINDS_2070)
        for k, node in enumerate(_xml_emitters(effect)):
            label = names.get(str(int(curves.finite(node.value("EffectType", "0")))), "")
            out.setdefault(tr(label) if label else node.value("EffectType", "?"), k)
    elif effect.m_syntax == BRACE and (top := _brace_top(effect)) is not None:
        for k, group in enumerate(top.groups()):
            out.setdefault(group.name, k)
    return list(out.items())


def add_like(effect: CEffectDoc, k: int) -> cfgdoc.SEdit | str:
    """The edit that appends a copy of emitter ``k`` at the end of the file, or why that cannot be."""
    return add_from(effect, effect, k)


def add_from(effect: CEffectDoc, donor: CEffectDoc, k: int) -> cfgdoc.SEdit | str:
    """The edit that appends a copy of ``donor``'s emitter ``k`` (another file of the game, or the effect itself) at
    the end of ``effect``, or why that cannot be."""
    if not effect.editable:
        return tr("objects.file_read_only_not_back")
    if donor.m_syntax != effect.m_syntax or not 0 <= k < count(donor):
        return tr("particles.effect_no_emitter_copy")
    if effect.m_syntax == XML and count(effect):
        return _add_xml(effect, _xml_emitters(donor)[k])
    if effect.m_syntax == BRACE and _brace_top(effect) is not None:
        return _add_brace(effect, _brace_top(donor).groups()[k])
    return tr("particles.effect_no_emitter_copy")


def remove(effect: CEffectDoc, k: int) -> cfgdoc.SEdit | str:
    """The edit that removes emitter ``k`` (an effect keeps one at least), or why that cannot be. 2070's and 1404's
    later emitters take the tag of the one before (``Effect0`` ... stay without a gap, as every file of the games)."""
    if not effect.editable:
        return tr("objects.file_read_only_not_back")
    if not 0 <= k < count(effect):
        return tr("particles.effect_no_such_emitter")
    if count(effect) == 1:
        return tr("particles.effect_keeps_one_emitter_least")
    if effect.m_syntax == XML:
        return _remove_xml(effect, k)
    group = _brace_top(effect).groups()[k]
    gone = {id(line) for line in group.lines()}
    lines = [line for line in effect.m_doc.m_lines if id(line) not in gone]
    return curves.swap(effect.m_doc.m_lines, lines, tr("particles.remove_emitter_name", name=group.name))


def _remove_xml(effect: CEffectDoc, k: int) -> cfgdoc.SEdit:
    root, emitters = effect.m_doc.root, _xml_emitters(effect)
    node, later = emitters[k], emitters[k + 1 :]
    names = [
        (emitter, emitter.name, emitters[k + i].name) for i, emitter in enumerate(later)
    ]  # (node, its tag, the tag it takes)
    insert_index = next(i for i, child in enumerate(root.children) if child is node)

    def do() -> None:
        root.children[:] = [child for child in root.children if child is not node]
        for emitter, _old, new in names:
            emitter.name = new

    def undo() -> None:
        for emitter, old, _new in names:
            emitter.name = old
        root.children.insert(insert_index, node)

    return cfgdoc.SEdit(tr("particles.remove_emitter_name", name=cfgdoc.shown(node.value("Name", ""))), do, undo)


@dataclass(frozen=True)
class SDonor:
    """The first emitter of a kind in the game's effect files: the kind's name, the file, the emitter's number."""

    kind: str
    path: str
    k: int
    syntax: str


def game_kinds(files, game: str, paths: list[str]) -> list[SDonor]:
    """The first emitter of each kind in the effect files ``paths`` of the game (``.efc``; a file that does not read
    is passed over), in path order: where a kind the open file lacks is copied from (about a second for a game)."""
    out: dict[tuple[str, str], SDonor] = {}
    for path in sorted(candidate_path for candidate_path in paths if candidate_path.endswith(".efc")):
        try:
            doc = CEffectDoc(files, game, path, files.read(path))
        except (OSError, ValueError, KeyError):
            continue
        for name, k in kinds(doc):
            out.setdefault((doc.m_syntax, name), SDonor(name, path, k, doc.m_syntax))
    return list(out.values())


def lacking(effect: CEffectDoc, donors: list[SDonor]) -> list[SDonor]:
    """The donors of the kinds the effect lacks, of its own syntax (a 2070 file takes no 1701 emitter)."""
    have = {name for name, _k in kinds(effect)}
    return [donor for donor in donors if donor.syntax == effect.m_syntax and donor.kind not in have]


def add_donor(effect: CEffectDoc, donor: SDonor) -> cfgdoc.SEdit | str:
    """The edit that appends a copy of the donor's emitter (read from the game now), or why that cannot be."""
    try:
        other = CEffectDoc(effect.m_files, effect.m_game, donor.path, effect.m_files.read(donor.path))
    except (OSError, ValueError, KeyError) as exc:
        return str(exc)
    return add_from(effect, other, donor.k)


def _unused(name: str, taken: set[str]) -> str:
    named, copy_number = COPY_OF + name, 2
    while named in taken:
        named, copy_number = f"{COPY_OF}{name} {copy_number}", copy_number + 1
    return named


def _zero_like(text: str) -> str:
    return "0.000000" if "." in text else "0"


def _add_xml(effect: CEffectDoc, template) -> cfgdoc.SEdit:
    root, emitters = effect.m_doc.root, _xml_emitters(effect)
    new = copy.deepcopy(template)
    new.name = f"Effect{max(int(_INDEX.search(copy_number.name).group(0)) for copy_number in emitters if _INDEX.search(copy_number.name)) + 1}"
    taken = {cfgdoc.shown(copy_number.value("Name", "")) for copy_number in emitters}
    name = new.find("Name")
    if name is not None:
        name.text = cfgdoc.stored(_unused(cfgdoc.shown(name.text or ""), taken))
    for tag in ORIGIN_2070:
        leaf = new.find(tag)
        if leaf is not None and leaf.text is not None:
            leaf.text = _zero_like(leaf.text)
    insert_index = next(i for i, child in enumerate(root.children) if child is emitters[-1]) + 1
    return cfgdoc.insert(
        root, insert_index, [new], tr("particles.add_emitter_like", name=cfgdoc.shown(template.value("Name", "")))
    )


def _add_brace(effect: CEffectDoc, template: SGroup) -> cfgdoc.SEdit:
    doc: CBraceDoc = effect.m_doc
    top = _brace_top(effect)
    made = {id(line): SLine(line.text, line.end) for line in template.lines()}
    taken = {_unquoted(emitter.values) for group in top.groups() for emitter in group.entries("Name")}
    for entry in template.entries():
        if entry.name == "Name":
            values = [f'"{_unused(_unquoted(entry.values), taken)}"']
        elif entry.name in ORIGIN_1701:
            values = ORIGIN_1701[entry.name].split()
        else:
            continue
        made[id(entry.line)].text = efc1701edit.field_text(entry, values)
    insert_index = doc.index(top.close)
    lines = [*doc.m_lines[:insert_index], *(made[id(line)] for line in template.lines()), *doc.m_lines[insert_index:]]
    return curves.swap(doc.m_lines, lines, tr("particles.add_emitter_like", name=template.name))


def _unquoted(values: list[str]) -> str:
    found = _QUOTED.match(values[0]) if values else None
    return found.group(1) if found else " ".join(values)
