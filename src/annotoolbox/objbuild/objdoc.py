"""The object the builder edits: a model config with the info and feedback files of the same name, each a
``models.cfgdoc.Doc``, one undo stack over them, and the scene the viewer draws of it with the edits laid over the
game's files. No Qt.

``ObjectDoc.open(files, path, game)`` is the entry; ``apply`` makes an edit (undoable), ``scene`` builds what the 3D
view draws (``viewer.modelscene``), ``files_for_save`` what a save writes. docs/tool-suite.md section 23.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..infra.i18n import tr
from ..models import cfgpatch, info1701
from ..models.cfgdoc import Doc, Edit
from ..models.configtree import shared_config
from ..sources.overlay import normalize
from ..suite.undo import Command, UndoStack
from ..viewer import modelscene

Held = Callable[[str], bytes | None]  # what the project holds at a game path
WHOLE, PATCH = "whole", "patch"  # how a file was restored from the project
MAIN = (".cfg", ".mcf", ".prp")  # the file an object is named by
SIDES = {"ifo": ".ifo", "fc": ".fc"}  # the files of the same name beside it
SIDES_1701 = {"ifo": ".info"}  # 1701: an .info beside each .mcf, no feedback (granny.md "1701's info files")
ROLES = ("cfg", *SIDES)


class EditedFiles:
    """A game's files with the open documents' edited bytes in place of their own (the viewer reads through it)."""

    def __init__(self, base) -> None:
        self.base = base
        self.edited: dict[str, bytes] = {}

    @property
    def game(self) -> str:
        return self.base.game

    def exists(self, path: str) -> bool:
        return normalize(path) in self.edited or self.base.exists(path)

    def read(self, path: str) -> bytes:
        found = self.edited.get(normalize(path))
        return found if found is not None else self.base.read(path)

    def __getattr__(self, name: str):  # origin, origins, paths, folder ...: the game's own
        return getattr(self.base, name)


@dataclass
class Opened:
    """One file of the object: its ``role`` (``cfg``, ``ifo``, ``fc``), the path asked for and the one read (117's
    ``shared/`` copy of a ``data/`` config), its document."""

    role: str
    asked: str
    read: str
    doc: Doc


def object_paths(path: str, game: str = "") -> dict[str, str]:
    """The paths of an object's files by role, from any of them (``x.ifo`` -> ``x.cfg``, ``x.ifo``, ``x.fc``; 1701:
    ``x.info`` -> ``x.mcf``, ``x.info``)."""
    path = normalize(path, studio=True)
    stem = path.rsplit(".", 1)[0]
    sides = SIDES_1701 if game == "anno1701" else SIDES
    main = path if path.endswith(MAIN) else stem + (".mcf" if game == "anno1701" else ".cfg")
    return {"cfg": main, **{role: stem + ext for role, ext in sides.items()}}


def first_mesh_config(files, path: str) -> str:
    """A 1701 object config's (``.ocf``) first mesh config, the path itself for any other file."""
    if not path.endswith(".ocf") or not files.exists(path):
        return path
    found = re.search(r"<MeshConfig>([^<]+)</MeshConfig>", files.read(path).decode("utf-8", "replace"))
    return normalize(found.group(1)) if found else path


def _saved(doc: Doc, read: str, held: Held, notes: list[str]) -> tuple[Doc, str]:
    """The game's document with the project's saved copy over it: a whole file replaces the tree, a patch is applied
    to it. The document keeps the game's bytes (``data``), so what a save compares against stays the game's.
    Returns it and ``whole`` / ``patch`` / "" (nothing held, or what is held does not open: ``notes`` says why)."""
    whole, patch = held(read), held(read + ".xml")
    if whole is not None:
        if patch is not None:
            notes.append(
                tr("{path}: the project holds a patch beside the whole file; the whole file is read", path=read)
            )
        try:
            saved = Doc.read(whole, read)
            if not saved.root.children:
                raise ValueError(tr("no config in it"))
        except ValueError as exc:
            notes.append(tr("{path} in the project does not open: {error}", path=read, error=exc))
            return doc, ""
        return replace(doc, root=saved.root, exact=saved.exact, newline=saved.newline), WHOLE
    if patch is not None and read.endswith(cfgpatch.PATCHED) and doc.exact:
        patched, warnings = cfgpatch.apply_patch(doc, patch)
        notes += [tr("{path}.xml: {warning}", path=read, warning=w) for w in warnings]
        return patched, PATCH
    return doc, ""


class ObjectDoc:
    """The open object. ``changed`` is called after every edit, undo and redo with the roles it touched.
    ``restored`` says which roles were opened from the project's copy (``whole`` or ``patch``), ``notes`` what of
    it did not apply."""

    def __init__(self, files, game: str, opened: dict[str, Opened], shared: bool = True) -> None:
        self.files, self.game, self.opened, self.shared = EditedFiles(files), game, opened, shared
        self.undo = UndoStack()
        self.listeners: list[Callable[[set[str]], None]] = []
        self.restored: dict[str, str] = {}
        self.notes: list[str] = []
        self.origin = (0.0, 0.0)  # 1701: the object's place past its info's grid point (tiles), from the .ocf

    @classmethod
    def open(cls, files, path: str, game: str, shared: bool = True, held: Held | None = None) -> ObjectDoc:
        """The object a path belongs to; a ``data/`` config of 117 is read from its ``shared/`` copy when ``shared``
        and the game has one, as the game reads it. ``held`` gives what the project already holds (game path -> bytes
        or None): a whole file or a patch of the object's files is laid over the game's, so a later save keeps the
        earlier edits. Raises ``FileNotFoundError`` when none of its files exist."""
        opened, restored, notes = {}, {}, []
        for role, asked in object_paths(first_mesh_config(files, path), game).items():
            copy = shared_config(asked) if role == "cfg" and shared and game == "anno117" else ""
            read = copy if copy and files.exists(copy) else asked
            if files.exists(read):
                doc = Doc.read(files.read(read), read)
                if held is not None:
                    doc, how = _saved(doc, read, held, notes)
                    if how:
                        restored[role] = how
                opened[role] = Opened(role, asked, read, doc)
        if not opened:
            raise FileNotFoundError(path)
        obj = cls(files, game, opened, shared)
        obj.restored, obj.notes = restored, notes
        if game == "anno1701" and path.endswith(".ocf") and files.exists(path):
            obj.origin = info1701.ocf_offset(files.read(path).decode("utf-8", "replace"))
        obj._changed(set(restored))
        return obj

    def doc(self, role: str) -> Doc | None:
        found = self.opened.get(role)
        return found.doc if found is not None else None

    @property
    def main(self) -> Opened:
        return self.opened.get("cfg") or next(iter(self.opened.values()))

    @property
    def title(self) -> str:
        main = self.main
        shared = tr(" (the baked copy {name})", name=main.read) if main.read != main.asked else ""
        return main.asked.rsplit("/", 1)[-1] + shared

    def apply(self, role: str, *edits: Edit) -> None:
        """Make edits as one undoable step."""
        edits = tuple(e for e in edits if e is not None)
        if not edits:
            return
        for e in edits:
            e.do()

        def undo() -> None:
            for e in reversed(edits):
                e.undo()
            self._changed({role})

        def redo() -> None:
            for e in edits:
                e.do()
            self._changed({role})

        self.undo.push(Command(edits[-1].label if len(edits) == 1 else f"{edits[0].label} (+{len(edits) - 1})",
                               undo, redo))  # fmt: skip
        self._changed({role})

    def sync(self, roles: set[str]) -> None:
        """Put the documents' bytes where the view reads them, without telling the listeners (a drag's preview)."""
        for role in roles:
            found = self.opened[role]
            if found.doc.changed:
                self.files.edited[normalize(found.read)] = found.doc.to_bytes()
            else:
                self.files.edited.pop(normalize(found.read), None)

    def _changed(self, roles: set[str]) -> None:
        self.sync(roles)
        for listener in list(self.listeners):
            listener(roles)

    def dirty(self) -> list[str]:
        """The roles whose file differs from the game's."""
        return [role for role, o in self.opened.items() if o.doc.changed]

    def scene(self, options: modelscene.Options) -> modelscene.Scene:
        """What the 3D view draws of the object as it is now (the config read as it was opened: its shared/ copy or
        not)."""
        cfg = self.opened.get("cfg")
        if cfg is None or not modelscene.is_model(cfg.asked):
            return modelscene.Scene(self.main.asked, self.game)
        return modelscene.build(self.files, cfg.asked, self.game, replace(options, shared=self.shared))

    def base_bytes(self, role: str) -> bytes:
        """The game's own bytes of a file of the object (what a patch is made against)."""
        return self.opened[role].doc.data
