"""The object the builder edits: a model config with the info and feedback files of the same name, each a
``models.cfgdoc.SDoc``, one undo stack over them, and the scene the viewer draws of it with the edits laid over the
game's files. No Qt.

``CObjectDoc.open(files, path, game)`` is the entry; ``apply`` makes an edit (undoable), ``scene`` builds what the 3D
view draws (``viewer.modelscene``), ``files_for_save`` what a save writes. docs/tool-suite.md section 23.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..infra.i18n import tr
from ..models import cfgpatch, info1701
from ..models.cfgdoc import SDoc, SEdit
from ..models.configtree import shared_config
from ..sources.overlay import normalize
from ..suite.undo import CUndoStack, SCommand
from ..viewer import modelscene

Held = Callable[[str], bytes | None]  # what the project holds at a game path
WHOLE, PATCH = "whole", "patch"  # how a file was restored from the project
MAIN = (".cfg", ".mcf", ".prp")  # the file an object is named by
SIDES = {"ifo": ".ifo", "fc": ".fc"}  # the files of the same name beside it
SIDES_1701 = {"ifo": ".info"}  # 1701: an .info beside each .mcf, no feedback (granny.md "1701's info files")
ROLES = ("cfg", *SIDES)


class CEditedFiles:
    """A game's files with the open documents' edited bytes in place of their own (the viewer reads through it)."""

    def __init__(self, base) -> None:
        self.m_base = base
        self.m_edited: dict[str, bytes] = {}

    @property
    def game(self) -> str:
        return self.m_base.game

    def exists(self, path: str) -> bool:
        return normalize(path) in self.m_edited or self.m_base.exists(path)

    def read(self, path: str) -> bytes:
        found = self.m_edited.get(normalize(path))
        return found if found is not None else self.m_base.read(path)

    def __getattr__(self, name: str):  # origin, origins, paths, folder ...: the game's own
        return getattr(self.m_base, name)


@dataclass
class SOpened:
    """One file of the object: its ``role`` (``cfg``, ``ifo``, ``fc``), the path asked for and the one read (117's
    ``shared/`` copy of a ``data/`` config), its document."""

    role: str
    asked: str
    read: str
    document: SDoc


def object_paths(path: str, game: str = "") -> dict[str, str]:
    """The paths of an object's files by role, from any of them (``x.ifo`` -> ``x.cfg``, ``x.ifo``, ``x.fc``; 1701:
    ``x.info`` -> ``x.mcf``, ``x.info``)."""
    path = normalize(path, studio=True)
    stem = path.rsplit(".", 1)[0]
    sides = SIDES_1701 if game == "anno1701" else SIDES
    main = path if path.endswith(MAIN) else stem + (".mcf" if game == "anno1701" else ".cfg")
    return {"cfg": main, **{role: stem + extension for role, extension in sides.items()}}


def first_mesh_config(files, path: str) -> str:
    """A 1701 object config's (``.ocf``) first mesh config, the path itself for any other file."""
    if not path.endswith(".ocf") or not files.exists(path):
        return path
    found = re.search(r"<MeshConfig>([^<]+)</MeshConfig>", files.read(path).decode("utf-8", "replace"))
    return normalize(found.group(1)) if found else path


def _saved(document: SDoc, read: str, held: Held, notes: list[str]) -> tuple[SDoc, str]:
    """The game's document with the project's saved copy over it: a whole file replaces the tree, a patch is applied
    to it. The document keeps the game's bytes (``data``), so what a save compares against stays the game's.
    Returns it and ``whole`` / ``patch`` / "" (nothing held, or what is held does not open: ``notes`` says why)."""
    whole, patch = held(read), held(read + ".xml")
    if whole is not None:
        if patch is not None:
            notes.append(tr("objects.project_holds_patch_beside_whole", path=read))
        try:
            saved = SDoc.read(whole, read)
            if not saved.root.children:
                raise ValueError(tr("objects.no_config"))
        except ValueError as exc:
            notes.append(tr("objects.project_not_open", path=read, error=exc))
            return document, ""
        return replace(document, root=saved.root, exact=saved.exact, newline=saved.newline), WHOLE
    if patch is not None and read.endswith(cfgpatch.PATCHED) and document.exact:
        patched, warnings = cfgpatch.apply_patch(document, patch)
        notes += [tr("objects.path_xml_warning", path=read, warning=warning) for warning in warnings]
        return patched, PATCH
    return document, ""


class CObjectDoc:
    """The open object. ``changed`` is called after every edit, undo and redo with the roles it touched.
    ``restored`` says which roles were opened from the project's copy (``whole`` or ``patch``), ``notes`` what of
    it did not apply."""

    def __init__(self, files, game: str, opened: dict[str, SOpened], is_shared: bool = True) -> None:
        self.m_files, self.m_game, self.m_opened, self.m_shared = CEditedFiles(files), game, opened, is_shared
        self.m_undo = CUndoStack()
        self.m_listeners: list[Callable[[set[str]], None]] = []
        self.m_restored: dict[str, str] = {}
        self.m_notes: list[str] = []
        self.m_origin = (0.0, 0.0)  # 1701: the object's place past its info's grid point (tiles), from the .ocf

    @classmethod
    def open(cls, files, path: str, game: str, is_shared: bool = True, held: Held | None = None) -> CObjectDoc:
        """The object a path belongs to; a ``data/`` config of 117 is read from its ``shared/`` copy when ``is_shared``
        and the game has one, as the game reads it. ``held`` gives what the project already holds (game path -> bytes
        or None): a whole file or a patch of the object's files is laid over the game's, so a later save keeps the
        earlier edits. Raises ``FileNotFoundError`` when none of its files exist."""
        opened, restored, notes = {}, {}, []
        for role, asked in object_paths(first_mesh_config(files, path), game).items():
            copy = shared_config(asked) if role == "cfg" and is_shared and game == "anno117" else ""
            read = copy if copy and files.exists(copy) else asked
            if files.exists(read):
                document = SDoc.read(files.read(read), read)
                if held is not None:
                    document, how = _saved(document, read, held, notes)
                    if how:
                        restored[role] = how
                opened[role] = SOpened(role, asked, read, document)
        if not opened:
            raise FileNotFoundError(path)
        object_doc = cls(files, game, opened, is_shared)
        object_doc.m_restored, object_doc.m_notes = restored, notes
        if game == "anno1701" and path.endswith(".ocf") and files.exists(path):
            object_doc.m_origin = info1701.ocf_offset(files.read(path).decode("utf-8", "replace"))
        object_doc._changed(set(restored))
        return object_doc

    def document(self, role: str) -> SDoc | None:
        found = self.m_opened.get(role)
        return found.document if found is not None else None

    @property
    def main(self) -> SOpened:
        return self.m_opened.get("cfg") or next(iter(self.m_opened.values()))

    @property
    def title(self) -> str:
        main = self.main
        is_shared = tr("objects.baked_copy_name", name=main.read) if main.read != main.asked else ""
        return main.asked.rsplit("/", 1)[-1] + is_shared

    def apply(self, role: str, *edits: SEdit) -> None:
        """Make edits as one undoable step."""
        edits = tuple(edit for edit in edits if edit is not None)
        if not edits:
            return
        for edit in edits:
            edit.do()

        def undo() -> None:
            for edit in reversed(edits):
                edit.undo()
            self._changed({role})

        def redo() -> None:
            for edit in edits:
                edit.do()
            self._changed({role})

        self.m_undo.push(SCommand(edits[-1].label if len(edits) == 1 else f"{edits[0].label} (+{len(edits) - 1})",
                               undo, redo))  # fmt: skip
        self._changed({role})

    def sync(self, roles: set[str]) -> None:
        """Put the documents' bytes where the view reads them, without telling the listeners (a drag's preview)."""
        for role in roles:
            found = self.m_opened[role]
            if found.document.changed:
                self.m_files.m_edited[normalize(found.read)] = found.document.to_bytes()
            else:
                self.m_files.m_edited.pop(normalize(found.read), None)

    def _changed(self, roles: set[str]) -> None:
        self.sync(roles)
        for listener in list(self.m_listeners):
            listener(roles)

    def dirty(self) -> list[str]:
        """The roles whose file differs from the game's."""
        return [role for role, opened in self.m_opened.items() if opened.document.changed]

    def scene(self, options: modelscene.SOptions) -> modelscene.CScene:
        """What the 3D view draws of the object as it is now (the config read as it was opened: its shared/ copy or
        not)."""
        cfg = self.m_opened.get("cfg")
        if cfg is None or not modelscene.is_model(cfg.asked):
            return modelscene.CScene(self.main.asked, self.m_game)
        return modelscene.build(self.m_files, cfg.asked, self.m_game, replace(options, shared=self.m_shared))

    def base_bytes(self, role: str) -> bytes:
        """The game's own bytes of a file of the object (what a patch is made against)."""
        return self.m_opened[role].document.data
