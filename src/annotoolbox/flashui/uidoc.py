"""A UI movie open in the Flash UI editor: its elements as a tree, their properties, the edits made to it.

``MovieDoc`` keeps the movie as the game has it and the working copy with the edit file's edits on top
(``uiproject.Project``); ``tree`` is what the editor's element tree shows (the timeline with its named instances,
then images, atlas icons, fonts, texts, code), ``properties`` what its property panel shows for one element,
``edit`` / ``undo`` / ``redo`` change it (the suite's undo stack, one command per edit of the edit file). No Qt here:
the window (``uiwindow``) and the commands share it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats import feuabc, feuedit, feuread, feuwrite
from ..infra.i18n import number, tr
from ..othergames import ui2205
from ..suite.undo import Command, UndoStack
from .uiproject import Project


@dataclass
class Node:
    """One row of the element tree: ``kind`` says what ``key`` is (an id, a tag index, a string number)."""

    kind: str  # movie, timeline, place, image, icon, font, text, code, string, group
    label: str
    key: tuple = ()
    children: list[Node] = field(default_factory=list)


class MovieDoc:
    def __init__(self, path: str, original: bytes, project: Project, ui: ui2205.UiFiles | None = None) -> None:
        self.path, self.original, self.project, self.ui = path, original, project, ui
        self.movie = feuread.read(original)
        self.refresh()
        self.history = UndoStack()  # the edit file's edits are undoable too: one command each, saved point after them
        for edit in project.edits.get(path, []):
            self.history.push(self._command(edit))
        self.history.mark_clean()

    @classmethod
    def from_game(cls, ui: ui2205.UiFiles, path: str, project: Project) -> MovieDoc:
        return cls(path, project.original(ui, path), project, ui)

    def refresh(self) -> None:
        """The working copy: the movie as loaded, with this movie's edits applied again in order."""
        self.movie = feuread.read(self.original)
        feuedit.apply(self.movie, self.project.edits.get(self.path, []))
        self.defs = feuread.definitions(self.movie)

    def edit(self, edit: feuedit.Edit) -> None:
        feuedit.apply(self.movie, [edit])  # raises before the edit file learns of a bad edit
        self.project.add(self.path, edit)
        self.defs = feuread.definitions(self.movie)
        self.history.push(self._command(edit))

    def _command(self, edit: feuedit.Edit) -> Command:
        def undo() -> None:
            self.project.undo(self.path)  # the edits are a list: the last one is the one this command made
            self.refresh()

        def redo() -> None:
            self.project.add(self.path, edit)
            self.refresh()

        return Command(edit.op, undo, redo)

    def undo(self) -> str | None:
        """Take back the last edit; its name, None when there is none."""
        return self.history.undo()

    def redo(self) -> str | None:
        """Make the last undone edit again; its name, None when there is none."""
        return self.history.redo()

    def data(self) -> bytes:
        return feuwrite.write(self.movie)

    # The tree

    def tree(self) -> Node:
        root = Node("movie", self.path.rsplit("/", 1)[-1])
        root.children.append(self._timeline(0, self.movie.tags, tr("ui.stage"), set()))
        groups = (
            (tr("ui.images"), self._images()),
            (tr("common.fonts"), [Node("font", f"{f.font_id}: {f.name} ({f.file})", (f.font_id,)) for f in feuedit.fonts(self.movie)]),
            (tr("ui.texts"), [Node("text", _text_label(t), (t.char_id,)) for t in feuedit.texts(self.movie)]),
            (tr("ui.classes"), self._symbols()),
            (tr("ui.code"), self._code()),
        )  # fmt: skip
        root.children += [Node("group", f"{name} ({len(items)})", (), items) for name, items in groups if items]
        return root

    def _timeline(self, owner: int, tags: list[feuread.Tag], label: str, seen: set[int]) -> Node:
        node = Node("timeline", label, (owner,))
        for i, tag in enumerate(tags):
            if tag.code == feuread.SHOW_FRAME:
                break  # the first frame: what the preview draws
            if tag.code not in (feuread.PLACE_OBJECT2, feuread.PLACE_OBJECT3):
                continue
            p = feuread.read_placement(tag)
            child = Node("place", self._place_label(p), (owner, i))
            sprite = self.defs.get(p.char_id) if p.char_id is not None else None
            if sprite is not None and sprite.children is not None and p.char_id not in seen:
                child.children = self._timeline(p.char_id, sprite.children, "", seen | {p.char_id}).children
            node.children.append(child)
        return node

    def _place_label(self, p: feuread.Placement) -> str:
        what = self.defs.get(p.char_id)
        kind = what.name if what is not None else (tr("ui.change") if p.char_id is None else tr("ui.missing"))
        name = f'"{p.name}" ' if p.name else ""
        which = f"{name}{kind} {p.char_id if p.char_id is not None else ''}".rstrip()
        return tr("ui.what_at_depth", what=which, depth=p.depth)

    def _images(self) -> list[Node]:
        out = []
        for code, img in feuedit.images(self.movie):
            kind = "icon" if code == feuread.ATLAS_IMAGE else "image"
            out.append(
                Node(kind, f"{img.char_id}: {img.path.rsplit(chr(92), 1)[-1]} {img.width}x{img.height}", (img.char_id,))
            )
        return out  # fmt: skip

    def _symbols(self) -> list[Node]:
        out = []
        for tag in self.movie.tags:
            if tag.code in (feuread.SYMBOL_CLASS, feuread.EXPORT_ASSETS):
                out += [Node("symbol", f"{cid}: {name}", (cid,)) for cid, name in feuread.read_symbols(tag)]
        return out

    def _code(self) -> list[Node]:
        out = []
        for block, tag in enumerate(feuedit.abc_tags(self.movie)):
            abc = feuread.read_abc(tag)
            strings = feuabc.strings(abc.abc)
            label = tr(
                "ui.code_block_bytes_strings",
                block=block,
                size=number(len(abc.abc)),
                strings=number(len(strings)),
            )
            out.append(Node("code", label, (block,)))
        return out

    def picture_ref(self, node: Node | None) -> feuread.ExternalImage | None:
        """The picture a tree row stands for: an image or icon row, or a placement of one; else None."""
        if node is None:
            return None
        char_id = node.key[0] if node.kind in ("image", "icon") and node.key else None
        if node.kind == "place":
            owner, index = node.key
            tags = self.movie.tags if owner == 0 else self.defs[owner].children
            char_id = feuread.read_placement(tags[index]).char_id
        tag = self.defs.get(char_id) if char_id is not None else None
        if tag is not None and tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):
            return feuread.read_image(tag)
        return None

    # Properties

    def properties(self, node: Node) -> dict:
        """The values the property panel shows (and may change) for a tree row."""
        if node.kind in ("image", "icon"):
            img = feuread.read_image(self.defs[node.key[0]])
            found = self.ui.atlas_entry(img.atlas_name or img.path) if self.ui and img.atlas_name else None
            return {
                "id": img.char_id, "path": img.path, "width": img.width, "height": img.height,
                "game path": ui2205.image_path(img.path) if node.kind == "image" else (found[0] if found else "-"),
                "flags": img.flags,
            }  # fmt: skip
        if node.kind == "font":
            f = feuread.read_font(self.defs[node.key[0]])
            return {
                "id": f.font_id,
                "file": f.file,
                "name": f.name,
                "languages": ", ".join(lang for lang, _ in f.languages),
            }
        if node.kind == "text":
            t = feuread.read_edit_text(self.defs[node.key[0]])
            return {
                "id": t.char_id, "text": t.text or "", "variable": t.variable, "font": t.font_id,
                "height": (t.height or 0) / 20, "color": t.color, "box": tuple(v / 20 for v in t.bounds),
                "html": bool(t.flags & feuread.EDIT_HTML),
            }  # fmt: skip
        if node.kind == "place":
            owner, index = node.key
            tags = self.movie.tags if owner == 0 else self.defs[owner].children
            p = feuread.read_placement(tags[index])
            m = p.matrix or feuread.Matrix()
            return {"name": p.name or "", "character": p.char_id, "depth": p.depth, "x": m.tx / 20, "y": m.ty / 20,
                    "scale": (round(m.a, 4), round(m.d, 4)), "class": p.class_name or ""}  # fmt: skip
        if node.kind == "code":
            tag = feuedit.abc_tags(self.movie)[node.key[0]]
            return {"block": node.key[0], "strings": feuabc.strings(feuread.read_abc(tag).abc)}
        return {}


def _text_label(t: feuread.EditText) -> str:
    text = (t.text or "").replace("\n", " ").replace("\r", " ")
    return f"{t.char_id}: {text[:40] or tr('ui.empty')}" + (f" [{t.variable}]" if t.variable else "")
