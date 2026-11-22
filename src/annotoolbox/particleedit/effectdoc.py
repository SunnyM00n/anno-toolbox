"""The open effect: one particle file of any game as a document that writes back byte for byte, the model config it
was opened from (with the blocks that place it), one undo stack over both, and the files the preview reads. No Qt.

``CEffectDoc.open(files, game, path, model)`` is the entry; ``apply`` makes edits (``cfgdoc.SEdit``) as one undoable
step, ``to_bytes`` is the effect's file, ``preview_path`` the name the edited bytes are drawn under. A ``.rdp`` and a
2070 or 1404 ``.efc`` are ``models.cfgdoc`` documents (the tagged text keeps every byte), a 1701 ``.efc`` is a
``formats.efc1701edit.CBraceDoc``. docs/particle-editor.md, "The app".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats.efc1701 import CEfcError, is_efc1701
from ..formats.efc1701edit import CBraceDoc
from ..infra.i18n import tr
from ..models import cfgpatch
from ..models.cfgdoc import SDoc, SEdit
from ..models.configtree import shared_config
from ..objbuild.objdoc import CEditedFiles
from ..sources.overlay import normalize
from ..suite.undo import CUndoStack, SCommand
from .curves import finite

EFFECT_ENDINGS = (".rdp", ".efc")
RDP, XML, BRACE = "rdp", "xml", "brace"  # the three syntaxes: 117/1800/2205, 2070/1404, 1701
PREVIEWS_KEPT = 3  # earlier versions of the edited bytes the preview may still be reading


def syntax_of(path: str, data: bytes) -> str:
    """``rdp``, ``xml`` (2070's and 1404's ``.efc``) or ``brace`` (1701's)."""
    if path.lower().endswith(".rdp"):
        return RDP
    return BRACE if is_efc1701(data[:64].decode("latin-1")) else XML


def read_document(path: str, data: bytes) -> tuple[SDoc | CBraceDoc, bool]:
    """The document of an effect file and whether it reads back byte for byte; raises ``ValueError`` for bytes that
    are no effect file."""
    if syntax_of(path, data) == BRACE:
        try:
            doc = CBraceDoc.read(data)
        except CEfcError as exc:
            raise ValueError(f"{path}: {exc}") from exc
        return doc, doc.to_bytes() == data
    doc = SDoc.read(data, path)
    return doc, doc.exact


def config_read_path(files, game: str, cfg: str) -> str:
    """The config the game reads for ``cfg``: 117's baked ``shared/`` copy of a ``data/`` config when it exists."""
    copy = shared_config(cfg) if game == "anno117" else ""
    return copy if copy and files.exists(copy) else normalize(cfg)


def naming_leaves(doc: SDoc, effect: str) -> list:
    """The leaves of a config that name the effect file (any slashes or case)."""
    wanted = normalize(effect)
    return [node for node in doc.root.iter() if not node.children and node.text and normalize(node.text) == wanted]


@dataclass
class SModel:
    """The model config an effect was opened from: the path asked, the one read, its document, the game's bytes, and
    the blocks that place the effect (the parents of the leaves naming it)."""

    asked: str
    read: str
    doc: SDoc
    base: bytes
    blocks: list = field(default_factory=list)


class CEffectDoc:
    """The open effect. ``changed`` listeners are called after every edit, undo and redo."""

    def __init__(
        self, files, game: str, path: str, data: bytes, model: SModel | None = None, shipped: bytes | None = None
    ) -> None:
        self.m_files, self.m_game, self.m_path, self.m_base = CEditedFiles(files), game, normalize(path), data
        self.m_shipped = (
            data if shipped is None else shipped
        )  # the game's own file, which the preview reads unless an edit differs
        self._m_bytes: tuple[int, bytes] | None = None  # the document's bytes at a version
        self.m_syntax = syntax_of(path, data) if data else RDP
        self.m_doc, self.m_exact = read_document(path, data) if data else (None, True)
        self.m_model = model
        self.m_undo = CUndoStack()
        self.m_listeners: list[Callable[[], None]] = []
        self.m_version = 0
        self.m_notes: list[str] = []

    @classmethod
    def open(
        cls, files, game: str, path: str, model: str = "", held: Callable[[str], bytes | None] = lambda _path: None
    ):
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
            doc.m_notes.append(tr("particles.opened_your_saved_edits_project"))
        if not data:
            doc.m_notes.append(tr("particles.empty_shown_not_saved", path=path))
        doc.sync()
        return doc

    @property
    def title(self) -> str:
        return self.m_path.rsplit("/", 1)[-1]

    @property
    def editable(self) -> bool:
        return self.m_doc is not None and self.m_exact

    def to_bytes(self) -> bytes:
        """The effect's file now (kept per version: an edit costs one write of the document)."""
        if self.m_doc is None:
            return self.m_base
        if self._m_bytes is None or self._m_bytes[0] != self.m_version:
            self._m_bytes = (self.m_version, self.m_doc.to_bytes())
        return self._m_bytes[1]

    @property
    def changed(self) -> bool:
        """Different from the file the effect was opened as (a saved copy of the project, else the game's)."""
        return self.to_bytes() != self.m_base

    @property
    def differs_from_game(self) -> bool:
        return self.to_bytes() != self.m_shipped

    @property
    def model_changed(self) -> bool:
        return self.m_model is not None and self.m_model.doc.to_bytes() != self.m_model.base

    def apply(self, *edits: SEdit | None) -> None:
        """Make edits as one undoable step; edits that change nothing (a click on a graph's point) make none."""
        edits = tuple(edit for edit in edits if edit is not None)
        if not edits:
            return
        before = self._state()
        for edit in edits:
            edit.do()
        self.m_version += 1
        if self._state() == before:
            for edit in reversed(edits):
                edit.undo()
            self.m_version += 1
            return

        def undo() -> None:
            for edit in reversed(edits):
                edit.undo()
            self._changed()

        def redo() -> None:
            for edit in edits:
                edit.do()
            self._changed()

        label = edits[-1].label if len(edits) == 1 else f"{edits[0].label} (+{len(edits) - 1})"
        self.m_undo.push(SCommand(label, undo, redo))
        self._changed()

    def _state(self) -> tuple[bytes, bytes]:
        if isinstance(self.m_doc, CBraceDoc):
            self.m_doc.changed()
        return self.to_bytes(), self.m_model.doc.to_bytes() if self.m_model is not None else b""

    def _changed(self) -> None:
        if isinstance(self.m_doc, CBraceDoc):
            self.m_doc.changed()
        self.m_version += 1
        self.sync()
        for listener in list(self.m_listeners):
            listener()

    def seconds(self) -> float:
        """One pass of the effect: an ``.rdp``'s strip, else the latest an emitter starts plus how long it emits and
        (2070, 1404) a particle lives."""
        if self.m_doc is None:
            return 1.0
        if self.m_syntax == RDP:
            root = self.m_doc.root
            return finite(root.value("FrameCount", "1")) * finite(root.value("FrameTime", "33.3")) / 1000.0
        ends = []
        if self.m_syntax == XML:
            for edit in (child for child in self.m_doc.root.children if child.children):
                ends.append(sum(finite(edit.value(node, "0")) for node in ("Delay", "Duration", "LifeTime")))
        else:
            for top in self.m_doc.tree().groups("Effect"):
                for edit in top.groups():
                    first = [
                        x.values[0] for node in ("StartDelay", "Duration") for x in edit.entries(node)[:1] if x.values
                    ]
                    ends.append(sum(finite(value) for value in first))
        return max([1000.0, *ends]) / 1000.0

    def preview_path(self) -> str:
        """The path the preview reads the effect under: its own while it is the game's file, a new name per edit (the
        view keeps what it read by name)."""
        if not self.differs_from_game:
            return self.m_path
        stem, dot, extension = self.m_path.rpartition(".")
        return f"{stem}~edit{self.m_version}{dot}{extension}"

    def sync(self) -> None:
        """Put the edited bytes where the preview reads them, in one swap: the view's loading threads read the files
        meanwhile, and the last few versions stay readable for a load still on its way."""
        edited = {k: value for k, value in self.m_files.m_edited.items() if "~edit" in k}
        edited = dict(list(edited.items())[-PREVIEWS_KEPT:])
        if self.differs_from_game:
            edited[normalize(self.preview_path())] = self.to_bytes()
        if self.m_model is not None and self.model_changed:
            edited[normalize(self.m_model.read)] = self.m_model.doc.to_bytes()
        self.m_files.m_edited = edited


def _model(files, game: str, effect: str, cfg: str, held: Callable[[str], bytes | None]) -> SModel:
    """The config an effect was opened from, as the game reads it, with the project's saved copy or patch laid over."""
    read = config_read_path(files, game, cfg)
    base = files.read(read)
    doc = SDoc.read(base, read)
    whole, patch = held(read), held(read + ".xml")
    if whole is not None:
        doc = SDoc.read(whole, read)
        doc.data = base
    elif patch is not None and doc.exact:
        doc, _warnings = cfgpatch.apply_patch(doc, patch)
    blocks = []
    for leaf in naming_leaves(doc, effect):
        parent = doc.parent_of(leaf)
        if parent is not None and all(parent is not block for block in blocks):
            blocks.append(parent)
    return SModel(normalize(cfg), read, doc, base, blocks)
