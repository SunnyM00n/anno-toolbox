"""The open effect: one particle file of any game as a document that writes back byte for byte, the model config it
was opened from (with the blocks that place it), one undo stack over both, and the files the preview reads. No Qt.

``EffectDoc.open(files, game, path, model)`` is the entry; ``apply`` makes edits (``cfgdoc.Edit``) as one undoable
step, ``to_bytes`` is the effect's file, ``preview_path`` the name the edited bytes are drawn under. A ``.rdp`` and a
2070 or 1404 ``.efc`` are ``models.cfgdoc`` documents (the tagged text keeps every byte), a 1701 ``.efc`` is a
``formats.efc1701edit.BraceDoc``. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats.efc1701 import EfcError, is_efc1701
from ..formats.efc1701edit import BraceDoc
from ..infra.i18n import tr
from ..models import cfgpatch
from ..models.cfgdoc import Doc, Edit
from ..models.configtree import shared_config
from ..objbuild.objdoc import EditedFiles
from ..sources.overlay import normalize
from ..suite.undo import Command, UndoStack
from .curves import finite

EFFECT_ENDINGS = (".rdp", ".efc")
RDP, XML, BRACE = "rdp", "xml", "brace"  # the three syntaxes: 117/1800/2205, 2070/1404, 1701
PREVIEWS_KEPT = 3  # earlier versions of the edited bytes the preview may still be reading


def syntax_of(path: str, data: bytes) -> str:
    """``rdp``, ``xml`` (2070's and 1404's ``.efc``) or ``brace`` (1701's)."""
    if path.lower().endswith(".rdp"):
        return RDP
    return BRACE if is_efc1701(data[:64].decode("latin-1")) else XML


def read_document(path: str, data: bytes) -> tuple[Doc | BraceDoc, bool]:
    """The document of an effect file and whether it reads back byte for byte; raises ``ValueError`` for bytes that
    are no effect file."""
    if syntax_of(path, data) == BRACE:
        try:
            doc = BraceDoc.read(data)
        except EfcError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        return doc, doc.to_bytes() == data
    doc = Doc.read(data, path)
    return doc, doc.exact


def config_read_path(files, game: str, cfg: str) -> str:
    """The config the game reads for ``cfg``: 117's baked ``shared/`` copy of a ``data/`` config when it exists."""
    copy = shared_config(cfg) if game == "anno117" else ""
    return copy if copy and files.exists(copy) else normalize(cfg)


def naming_leaves(doc: Doc, effect: str) -> list:
    """The leaves of a config that name the effect file (any slashes or case)."""
    wanted = normalize(effect)
    return [n for n in doc.root.iter() if not n.children and n.text and normalize(n.text) == wanted]


@dataclass
class Model:
    """The model config an effect was opened from: the path asked, the one read, its document, the game's bytes, and
    the blocks that place the effect (the parents of the leaves naming it)."""

    asked: str
    read: str
    doc: Doc
    base: bytes
    blocks: list = field(default_factory=list)


class EffectDoc:
    """The open effect. ``changed`` listeners are called after every edit, undo and redo."""

    def __init__(
        self, files, game: str, path: str, data: bytes, model: Model | None = None, shipped: bytes | None = None
    ) -> None:
        self.files, self.game, self.path, self.base = EditedFiles(files), game, normalize(path), data
        self.shipped = (
            data if shipped is None else shipped
        )  # the game's own file, which the preview reads unless an edit differs
        self._bytes: tuple[int, bytes] | None = None  # the document's bytes at a version
        self.syntax = syntax_of(path, data) if data else RDP
        self.doc, self.exact = read_document(path, data) if data else (None, True)
        self.model = model
        self.undo = UndoStack()
        self.listeners: list[Callable[[], None]] = []
        self.version = 0
        self.notes: list[str] = []

    @classmethod
    def open(cls, files, game: str, path: str, model: str = "", held: Callable[[str], bytes | None] = lambda _p: None):
        """The effect at ``path`` of ``files``, with the config ``model`` it was opened from ("" for none); ``held``
        gives what the open project already holds of a game path (its saved copy is opened instead). Raises
        ``FileNotFoundError`` and ``ValueError`` (no effect file)."""
        path = normalize(path)
        if not path.endswith(EFFECT_ENDINGS):
            raise ValueError(tr("particles.no_effect_file_rdp_efc", path=path))
        saved = held(path)
        data = saved if saved is not None else files.read(path)
        found = _model(files, game, path, model, held) if model else None
        shipped = (files.read(path) if files.exists(path) else b"") if saved is not None else None
        doc = cls(files, game, path, data, found, shipped)
        if saved is not None:
            doc.notes.append(tr("particles.opened_your_saved_edits_project"))
        if not data:
            doc.notes.append(tr("particles.empty_shown_not_saved", path=path))
        doc.sync()
        return doc

    @property
    def title(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def editable(self) -> bool:
        return self.doc is not None and self.exact

    def to_bytes(self) -> bytes:
        """The effect's file now (kept per version: an edit costs one write of the document)."""
        if self.doc is None:
            return self.base
        if self._bytes is None or self._bytes[0] != self.version:
            self._bytes = (self.version, self.doc.to_bytes())
        return self._bytes[1]

    @property
    def changed(self) -> bool:
        """Different from the file the effect was opened as (a saved copy of the project, else the game's)."""
        return self.to_bytes() != self.base

    @property
    def differs_from_game(self) -> bool:
        return self.to_bytes() != self.shipped

    @property
    def model_changed(self) -> bool:
        return self.model is not None and self.model.doc.to_bytes() != self.model.base

    def apply(self, *edits: Edit | None) -> None:
        """Make edits as one undoable step; edits that change nothing (a click on a graph's point) make none."""
        edits = tuple(e for e in edits if e is not None)
        if not edits:
            return
        before = self._state()
        for e in edits:
            e.do()
        self.version += 1
        if self._state() == before:
            for e in reversed(edits):
                e.undo()
            self.version += 1
            return

        def undo() -> None:
            for e in reversed(edits):
                e.undo()
            self._changed()

        def redo() -> None:
            for e in edits:
                e.do()
            self._changed()

        label = edits[-1].label if len(edits) == 1 else f"{edits[0].label} (+{len(edits) - 1})"
        self.undo.push(Command(label, undo, redo))
        self._changed()

    def _state(self) -> tuple[bytes, bytes]:
        if isinstance(self.doc, BraceDoc):
            self.doc.changed()
        return self.to_bytes(), self.model.doc.to_bytes() if self.model is not None else b""

    def _changed(self) -> None:
        if isinstance(self.doc, BraceDoc):
            self.doc.changed()
        self.version += 1
        self.sync()
        for listener in list(self.listeners):
            listener()

    def seconds(self) -> float:
        """One pass of the effect: an ``.rdp``'s strip, else the latest an emitter starts plus how long it emits and
        (2070, 1404) a particle lives."""
        if self.doc is None:
            return 1.0
        if self.syntax == RDP:
            root = self.doc.root
            return finite(root.value("FrameCount", "1")) * finite(root.value("FrameTime", "33.3")) / 1000.0
        ends = []
        if self.syntax == XML:
            for e in (c for c in self.doc.root.children if c.children):
                ends.append(sum(finite(e.value(n, "0")) for n in ("Delay", "Duration", "LifeTime")))
        else:
            for top in self.doc.tree().groups("Effect"):
                for e in top.groups():
                    first = [x.values[0] for n in ("StartDelay", "Duration") for x in e.entries(n)[:1] if x.values]
                    ends.append(sum(finite(v) for v in first))
        return max([1000.0, *ends]) / 1000.0

    def preview_path(self) -> str:
        """The path the preview reads the effect under: its own while it is the game's file, a new name per edit (the
        view keeps what it read by name)."""
        if not self.differs_from_game:
            return self.path
        stem, dot, ext = self.path.rpartition(".")
        return f"{stem}~edit{self.version}{dot}{ext}"

    def sync(self) -> None:
        """Put the edited bytes where the preview reads them, in one swap: the view's loading threads read the files
        meanwhile, and the last few versions stay readable for a load still on its way."""
        edited = {k: v for k, v in self.files.edited.items() if "~edit" in k}
        edited = dict(list(edited.items())[-PREVIEWS_KEPT:])
        if self.differs_from_game:
            edited[normalize(self.preview_path())] = self.to_bytes()
        if self.model is not None and self.model_changed:
            edited[normalize(self.model.read)] = self.model.doc.to_bytes()
        self.files.edited = edited


def _model(files, game: str, effect: str, cfg: str, held: Callable[[str], bytes | None]) -> Model:
    """The config an effect was opened from, as the game reads it, with the project's saved copy or patch laid over."""
    read = config_read_path(files, game, cfg)
    base = files.read(read)
    doc = Doc.read(base, read)
    whole, patch = held(read), held(read + ".xml")
    if whole is not None:
        doc = Doc.read(whole, read)
        doc.data = base
    elif patch is not None and doc.exact:
        doc, _warnings = cfgpatch.apply_patch(doc, patch)
    blocks = []
    for leaf in naming_leaves(doc, effect):
        parent = doc.parent_of(leaf)
        if parent is not None and all(parent is not b for b in blocks):
            blocks.append(parent)
    return Model(normalize(cfg), read, doc, base, blocks)
