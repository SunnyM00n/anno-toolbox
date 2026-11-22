"""Saving an edited object into the project: the three ways (a new file, the game's file replaced, a patch) with the
reason each is open or not - 117's ``shared/`` rule among them - and the files each way writes.

``plan(object_doc, way, new_path)`` is the entry (no Qt: the files by game path, the notes, what stops it);
``CSaveDialog`` shows the ways and their reasons and returns the chosen plan. docs/tool-suite.md section 23,
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
from ..models.cfgdoc import SDoc
from ..models.cfgpatch import NEW, PATCH, REPLACE
from ..sources.overlay import normalize
from .objdoc import CObjectDoc

TITLES = {NEW: N_("objects.new_file"), REPLACE: N_("objects.replace_games_file"), PATCH: N_("objects.patch_modops")}


@dataclass
class SPlan:
    """What a save writes (game path -> bytes), what to tell the person, and what stops it."""

    way: str
    files: dict[str, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)  # files an earlier save made in the other way: the save removes them


def ways(object_doc: CObjectDoc) -> list[cfgpatch.SWay]:
    """The ways the object's main file can be saved, with their reasons."""
    main = object_doc.main
    return cfgpatch.ways(object_doc.m_game, main.asked, main.read, object_doc.m_files.m_base.exists)


def held(object_doc: CObjectDoc, saved: Iterable[str]) -> list[str]:
    """Of the project's saved files (game paths), the ones that belong to this object: its files, their patches and
    its baked data file."""
    own = {opened.read for opened in object_doc.m_opened.values()}
    baked = {bfg_path(opened.read) for opened in object_doc.m_opened.values()}
    return [path for path in saved if path in own or path in baked or (path.endswith(".xml") and path[:-4] in own)]


def bfg_path(config: str) -> str:
    """The baked data file beside a model config (``x.cfg`` -> ``x.bfg``)."""
    return config[: -len(".cfg")] + ".bfg" if config.endswith(".cfg") else ""


def plan(object_doc: CObjectDoc, way: str, new_path: str = "", saved: Iterable[str] = ()) -> SPlan:
    """The files a save writes: a new object (every file of it, renamed with ``new_path``), or the changed files at
    their own paths, whole or as patches (an ``.ifo`` is always whole). ``saved``: what the project holds of the
    object (``held``): a file in the other way is removed by the save, so the mod never carries both."""
    out = SPlan(way)
    if any(not opened.document.exact for opened in object_doc.m_opened.values()):
        out.problems.append(tr("objects.file_not_read_back_byte"))
    elif way == NEW:
        _plan_new(object_doc, new_path, out)
    else:
        if _plan_changed(object_doc, way, out):
            _note_project_files(object_doc, way, saved, out)
    return out


def _plan_new(object_doc: CObjectDoc, new_path: str, out: SPlan) -> None:
    stem = normalize(new_path, studio=True).rsplit(".", 1)[0]
    if not stem or stem == object_doc.main.asked.rsplit(".", 1)[0]:
        out.problems.append(tr("objects.name_path_your_own_new"))
    elif any(x in ("", ".", "..") or ":" in x for x in stem.split("/")):
        out.problems.append(tr("objects.new_path_must_stay_inside"))
    else:
        for opened in object_doc.m_opened.values():
            out.files[f"{stem}.{opened.read.rsplit('.', 1)[-1]}"] = opened.document.to_bytes()
        out.notes.append(tr("objects.game_reads_only_when_asset", stem=stem))


def _plan_changed(object_doc: CObjectDoc, way: str, out: SPlan) -> bool:
    """The changed files whole (a replacement, or a file that cannot be patched) or as patches; False when the way is
    not open."""
    open_ways = {item.way: item for item in ways(object_doc)}
    if not open_ways[way].allowed:
        out.problems.append(open_ways[way].why)
        return False
    for role in object_doc.dirty():
        opened = object_doc.m_opened[role]
        if way == REPLACE or not opened.read.endswith(cfgpatch.PATCHED):
            out.files[opened.read] = opened.document.to_bytes()
            if way == PATCH:
                out.notes.append(tr("objects.written_whole_cannot_patched", path=opened.read))
            continue
        found = cfgpatch.render_patch(object_doc.base_bytes(role), opened.document)
        out.problems += found.problems
        if found.text:
            out.files[opened.read + ".xml"] = found.text.encode("utf-8")
            out.notes.append(tr("objects.path_count_operations", path=opened.read + ".xml", count=found.count))
    if not out.problems:
        _follow_bfg(object_doc, out)
    if not out.files and not out.problems:
        out.problems.append(tr("common.nothing_changed"))
    return True


def _note_project_files(object_doc: CObjectDoc, way: str, saved: Iterable[str], out: SPlan) -> None:
    """What the save does to the files the project already holds of the object: replaces them or removes them."""
    for path in held(object_doc, saved):
        if path in out.files:
            out.notes.append(tr("objects.replaces_what_project_holds", path=path))
        elif _stale(object_doc, path, way):
            out.stale.append(path)
            out.notes.append(tr("objects.removed_earlier_save_other_way", path=path))


def _follow_bfg(object_doc: CObjectDoc, out: SPlan) -> None:
    """Anno 117's ``shared/`` config has a baked data file whose entries follow its parts by position: when parts
    were added, taken out or moved the file is written to follow (``models.cfgbfg``), else the game's stays."""
    found = object_doc.m_opened.get("cfg")
    if (
        found is None
        or object_doc.m_game != "anno117"
        or "cfg" not in object_doc.dirty()
        or not found.read.startswith("shared/")
    ):
        return
    path = bfg_path(found.read)
    if not path or not object_doc.m_files.m_base.exists(path):
        return
    follow = cfgbfg.reindex(
        SDoc.read(object_doc.base_bytes("cfg"), found.read), found.document, object_doc.m_files.m_base.read(path)
    )
    if follow.problems:
        out.notes.append(
            tr(
                "objects.games_file_stays_cannot_follow",
                path=path,
                problem=follow.problems[0],
            )
        )
    elif follow.changed:
        out.files[path] = follow.data
        out.notes.append(tr("objects.baked_data_follows_changed_parts", path=path))


def _role_of(object_doc: CObjectDoc, path: str) -> str:
    return next(
        role
        for role, opened in object_doc.m_opened.items()
        if opened.read == path.removesuffix(".xml") or bfg_path(opened.read) == path
    )


def _stale(object_doc: CObjectDoc, path: str, way: str) -> bool:
    """A saved whole file is stale after a patch, a saved patch after a replacement (of a file that is dirty); a baked
    data file the new save does not write is stale whichever way it saves."""
    role = _role_of(object_doc, path)
    if path.endswith(".bfg"):
        return role in object_doc.dirty()
    return role in object_doc.dirty() and path.endswith(".xml") == (way == REPLACE)


class CWaysDialog(QDialog):
    """The ways a file can be saved with their reasons (``ways``: each with ``way``, ``allowed`` and ``why``; ``titles``
    names them), a path for a new file, and what the chosen way writes (``_plan``, which a subclass gives: a plan with
    ``files``, ``notes`` and ``problems``). ``result_plan`` is the plan chosen. The object builder's and the particle
    editor's save dialogs."""

    def __init__(self, header: str, ways: list, titles: dict, path: str, size_px: tuple[int, int], parent=None,
                 first: int = 0) -> None:  # fmt: skip
        super().__init__(parent)
        self.setWindowTitle(tr("objects.title.save_project"))
        self.m_result_plan, self.m_ways = None, ways
        self.m_group = QButtonGroup(self)
        self.m_path = QLineEdit(path)
        self.m_preview = QPlainTextEdit(readOnly=True)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(header))
        for k, item in enumerate(ways):
            button = QRadioButton(tr(titles[item.way]), enabled=item.allowed)
            self.m_group.addButton(button, k)
            lay.addWidget(button)
            why = QLabel(item.why if item.allowed else tr("objects.not_open_why", why=item.why), wordWrap=True)
            why.setIndent(24)
            lay.addWidget(why)
            if item.way == NEW:
                lay.addWidget(self.m_path)
        lay.addWidget(self.m_preview)
        buttons = QDialogButtonBox()
        self.m_save_button = buttons.addButton(tr("common.save"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("common.cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.m_group.idToggled.connect(lambda _id, is_on: is_on and self._show())
        self.m_path.textChanged.connect(lambda _text: self._show())
        self.m_group.button(first).setChecked(True)
        self.resize(*size_px)

    def _plan(self, way: str, path: str):
        raise NotImplementedError

    def chosen(self) -> str:
        return self.m_ways[max(0, self.m_group.checkedId())].way

    def _show(self) -> None:
        found = self._plan(self.chosen(), self.m_path.text())
        lines = [tr("objects.writes_path", path=path) for path in found.files] + found.notes
        lines += [tr("objects.cannot_problem", problem=path) for path in found.problems]
        patch = next((content for path, content in found.files.items() if path.endswith(".xml")), b"")
        self.m_preview.setPlainText("\n".join(lines) + ("\n\n" + patch.decode("utf-8") if patch else ""))
        self.m_save_button.setEnabled(not found.problems)

    def _save(self) -> None:
        self.m_result_plan = self._plan(self.chosen(), self.m_path.text())
        self.accept()


class CSaveDialog(CWaysDialog):
    """The ways of saving the object, a path for a new file, and what the chosen way writes."""

    def __init__(self, object_doc: CObjectDoc, parent=None, saved: Iterable[str] = ()) -> None:
        self.m_object_doc, self.m_saved = object_doc, list(saved)
        found = ways(object_doc)
        stem, extension = object_doc.main.asked.rsplit(".", 1)[0], object_doc.main.asked.rsplit(".", 1)[-1]
        changed = ", ".join(object_doc.dirty()) or tr("objects.nothing")
        first = next((k for k, way in enumerate(found) if way.allowed and way.way != NEW), 0)
        super().__init__(tr("objects.title_changed_what", title=object_doc.title, what=changed), found, TITLES,
                         f"{stem}_mod.{extension}", (720, 560), parent, first)  # fmt: skip

    def _plan(self, way: str, path: str) -> SPlan:
        return plan(self.m_object_doc, way, path, self.m_saved)
