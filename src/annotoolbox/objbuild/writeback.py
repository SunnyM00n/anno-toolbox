"""Saving an edited object into the project: the three ways (a new file, the game's file replaced, a patch) with the
reason each is open or not - 117's ``shared/`` rule among them - and the files each way writes.

``plan(obj, way, new_path)`` is the entry (no Qt: the files by game path, the notes, what stops it);
``SaveDialog`` shows the ways and their reasons and returns the chosen plan. docs/tool-suite.md section 23,
"Writing back"; docs/object-builder.md "Saving".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
)

from ..infra.i18n import N_, tr
from ..models import cfgbfg, cfgpatch
from ..models.cfgdoc import Doc
from ..models.cfgpatch import NEW, PATCH, REPLACE
from ..sources.overlay import normalize
from .objdoc import ObjectDoc

TITLES = {NEW: N_("A new file"), REPLACE: N_("Replace the game's file"), PATCH: N_("A patch (ModOps)")}


@dataclass
class Plan:
    """What a save writes (game path -> bytes), what to tell the person, and what stops it."""

    way: str
    files: dict[str, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)  # files an earlier save made in the other way: the save removes them


def ways(obj: ObjectDoc) -> list[cfgpatch.Way]:
    """The ways the object's main file can be saved, with their reasons."""
    main = obj.main
    return cfgpatch.ways(obj.game, main.asked, main.read, obj.files.base.exists)


def held(obj: ObjectDoc, saved: Iterable[str]) -> list[str]:
    """Of the project's saved files (game paths), the ones that belong to this object: its files, their patches and
    its baked data file."""
    own = {o.read for o in obj.opened.values()}
    baked = {bfg_path(o.read) for o in obj.opened.values()}
    return [p for p in saved if p in own or p in baked or (p.endswith(".xml") and p[:-4] in own)]


def bfg_path(config: str) -> str:
    """The baked data file beside a model config (``x.cfg`` -> ``x.bfg``)."""
    return config[: -len(".cfg")] + ".bfg" if config.endswith(".cfg") else ""


def plan(obj: ObjectDoc, way: str, new_path: str = "", saved: Iterable[str] = ()) -> Plan:
    """The files a save writes: a new object (every file of it, renamed with ``new_path``), or the changed files at
    their own paths, whole or as patches (an ``.ifo`` is always whole). ``saved``: what the project holds of the
    object (``held``): a file in the other way is removed by the save, so the mod never carries both."""
    out = Plan(way)
    if any(not o.doc.exact for o in obj.opened.values()):
        out.problems.append(tr("a file does not read back byte for byte, so it is shown but not saved"))
    elif way == NEW:
        _plan_new(obj, new_path, out)
    else:
        if _plan_changed(obj, way, out):
            _note_project_files(obj, way, saved, out)
    return out


def _plan_new(obj: ObjectDoc, new_path: str, out: Plan) -> None:
    stem = normalize(new_path, studio=True).rsplit(".", 1)[0]
    if not stem or stem == obj.main.asked.rsplit(".", 1)[0]:
        out.problems.append(tr("name a path of your own for the new file"))
    elif any(x in ("", ".", "..") or ":" in x for x in stem.split("/")):
        out.problems.append(tr("the new path must stay inside the game's folders (no .., no drive)"))
    else:
        for o in obj.opened.values():
            out.files[f"{stem}.{o.read.rsplit('.', 1)[-1]}"] = o.doc.to_bytes()
        out.notes.append(tr("the game reads {stem}.* only when an asset or another config names it", stem=stem))


def _plan_changed(obj: ObjectDoc, way: str, out: Plan) -> bool:
    """The changed files whole (a replacement, or a file that cannot be patched) or as patches; False when the way is
    not open."""
    open_ways = {w.way: w for w in ways(obj)}
    if not open_ways[way].allowed:
        out.problems.append(open_ways[way].why)
        return False
    for role in obj.dirty():
        o = obj.opened[role]
        if way == REPLACE or not o.read.endswith(cfgpatch.PATCHED):
            out.files[o.read] = o.doc.to_bytes()
            if way == PATCH:
                out.notes.append(tr("{path}: written whole (it cannot be patched)", path=o.read))
            continue
        found = cfgpatch.render_patch(obj.base_bytes(role), o.doc)
        out.problems += found.problems
        if found.text:
            out.files[o.read + ".xml"] = found.text.encode("utf-8")
            out.notes.append(tr("{path}: {count} operations", path=o.read + ".xml", count=found.count))
    if not out.problems:
        _follow_bfg(obj, out)
    if not out.files and not out.problems:
        out.problems.append(tr("nothing changed"))
    return True


def _note_project_files(obj: ObjectDoc, way: str, saved: Iterable[str], out: Plan) -> None:
    """What the save does to the files the project already holds of the object: replaces them or removes them."""
    for path in held(obj, saved):
        if path in out.files:
            out.notes.append(tr("{path}: replaces what the project holds", path=path))
        elif _stale(obj, path, way):
            out.stale.append(path)
            out.notes.append(tr("{path}: removed (an earlier save in the other way)", path=path))


def _follow_bfg(obj: ObjectDoc, out: Plan) -> None:
    """Anno 117's ``shared/`` config has a baked data file whose entries follow its parts by position: when parts
    were added, taken out or moved the file is written to follow (``models.cfgbfg``), else the game's stays."""
    found = obj.opened.get("cfg")
    if found is None or obj.game != "anno117" or "cfg" not in obj.dirty() or not found.read.startswith("shared/"):
        return
    path = bfg_path(found.read)
    if not path or not obj.files.base.exists(path):
        return
    follow = cfgbfg.reindex(Doc.read(obj.base_bytes("cfg"), found.read), found.doc, obj.files.base.read(path))
    if follow.problems:
        out.notes.append(
            tr(
                "{path}: the game's file stays, it cannot follow the change: {problem}",
                path=path,
                problem=follow.problems[0],
            )
        )
    elif follow.changed:
        out.files[path] = follow.data
        out.notes.append(
            tr("{path}: the baked data follows the changed parts (none for new ones, no far-view model)", path=path)
        )


def _role_of(obj: ObjectDoc, path: str) -> str:
    return next(r for r, o in obj.opened.items() if o.read == path.removesuffix(".xml") or bfg_path(o.read) == path)


def _stale(obj: ObjectDoc, path: str, way: str) -> bool:
    """A saved whole file is stale after a patch, a saved patch after a replacement (of a file that is dirty); a baked
    data file the new save does not write is stale whichever way it saves."""
    role = _role_of(obj, path)
    if path.endswith(".bfg"):
        return role in obj.dirty()
    return role in obj.dirty() and path.endswith(".xml") == (way == REPLACE)


class SaveDialog(QDialog):
    """The ways with their reasons, a path for a new file, and what the chosen way writes."""

    def __init__(self, obj: ObjectDoc, parent=None, saved: Iterable[str] = ()) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Save into the project"))
        self.obj, self.result_plan, self.saved = obj, None, list(saved)
        self.group = QButtonGroup(self)
        self.path = QLineEdit(obj.main.asked.rsplit(".", 1)[0] + "_mod." + obj.main.asked.rsplit(".", 1)[-1])
        self.preview = QPlainTextEdit(readOnly=True)
        lay = QVBoxLayout(self)
        changed = ", ".join(obj.dirty()) or tr("nothing")
        lay.addWidget(QLabel(tr("<b>{title}</b>: changed {what}", title=obj.title, what=changed)))
        for k, w in enumerate(ways(obj)):
            button = QRadioButton(tr(TITLES[w.way]), enabled=w.allowed)
            self.group.addButton(button, k)
            lay.addWidget(button)
            why = QLabel(w.why if w.allowed else tr("Not open: {why}", why=w.why), wordWrap=True)
            why.setIndent(24)
            lay.addWidget(why)
            if w.way == NEW:
                lay.addWidget(self.path)
        lay.addWidget(self.preview)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(tr("Save"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("Cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.group.idToggled.connect(lambda _id, on: on and self._show())
        self.path.textChanged.connect(lambda _t: self._show())
        first = next((k for k, w in enumerate(ways(obj)) if w.allowed and w.way != NEW), 0)
        self.group.button(first).setChecked(True)
        self.resize(720, 560)

    def chosen(self) -> str:
        return ways(self.obj)[max(0, self.group.checkedId())].way

    def _show(self) -> None:
        found = plan(self.obj, self.chosen(), self.path.text(), self.saved)
        lines = [tr("writes {path}", path=p) for p in found.files] + found.notes
        lines += [tr("cannot: {problem}", problem=p) for p in found.problems]
        patch = next((d for p, d in found.files.items() if p.endswith(".xml")), b"")
        self.preview.setPlainText("\n".join(lines) + ("\n\n" + patch.decode("utf-8") if patch else ""))
        self.save_button.setEnabled(not found.problems)

    def _save(self) -> None:
        self.result_plan = plan(self.obj, self.chosen(), self.path.text(), self.saved)
        self.accept()
