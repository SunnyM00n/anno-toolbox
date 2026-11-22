"""A UI movie open in the Flash UI editor: its elements as a tree, their properties, the edits made to it.

``CMovieDoc`` keeps the movie as the game has it and the working copy with the edit file's edits on top
(``uiproject.CProject``); ``tree`` is what the editor's element tree shows (the timeline with its named instances,
then images, atlas icons, fonts, texts, code), ``properties`` what its property panel shows for one element,
``edit`` / ``undo`` / ``redo`` change it (the suite's undo stack, one command per edit of the edit file). No Qt here:
the window (``uiwindow``) and the commands share it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats import feuabc, feuedit, feuread, feuwrite
from ..infra.i18n import number, tr
from ..othergames import ui2205
from ..suite.undo import CUndoStack, SCommand
from .uiproject import CProject


@dataclass
class SNode:
    """One row of the element tree: ``kind`` says what ``key`` is (an id, a tag index, a string number)."""

    kind: str  # movie, timeline, place, image, icon, font, text, code, string, group
    label: str
    key: tuple = ()
    children: list[SNode] = field(default_factory=list)


class CMovieDoc:
    def __init__(self, path: str, original: bytes, project: CProject, ui_files: ui2205.CUiFiles | None = None) -> None:
        self.m_path, self.m_original, self.m_project, self.m_ui = path, original, project, ui_files
        self.m_movie = feuread.read(original)
        self.refresh()
        self.m_history = (
            CUndoStack()
        )  # the edit file's edits are undoable too: one command each, saved point after them
        for edit in project.m_edits.get(path, []):
            self.m_history.push(self._command(edit))
        self.m_history.mark_clean()

    @classmethod
    def from_game(cls, ui_files: ui2205.CUiFiles, path: str, project: CProject) -> CMovieDoc:
        return cls(path, project.original(ui_files, path), project, ui_files)

    def refresh(self) -> None:
        """The working copy: the movie as loaded, with this movie's edits applied again in order."""
        self.m_movie = feuread.read(self.m_original)
        feuedit.apply(self.m_movie, self.m_project.m_edits.get(self.m_path, []))
        self.m_defs = feuread.definitions(self.m_movie)

    def edit(self, edit: feuedit.SEdit) -> None:
        feuedit.apply(self.m_movie, [edit])  # raises before the edit file learns of a bad edit
        self.m_project.add(self.m_path, edit)
        self.m_defs = feuread.definitions(self.m_movie)
        self.m_history.push(self._command(edit))

    def _command(self, edit: feuedit.SEdit) -> SCommand:
        def undo() -> None:
            self.m_project.undo(self.m_path)  # the edits are a list: the last one is the one this command made
            self.refresh()

        def redo() -> None:
            self.m_project.add(self.m_path, edit)
            self.refresh()

        return SCommand(edit.op, undo, redo)

    def undo(self) -> str | None:
        """Take back the last edit; its name, None when there is none."""
        return self.m_history.undo()

    def redo(self) -> str | None:
        """Make the last undone edit again; its name, None when there is none."""
        return self.m_history.redo()

    def data(self) -> bytes:
        return feuwrite.write(self.m_movie)

    # The tree

    def tree(self) -> SNode:
        root = SNode("movie", self.m_path.rsplit("/", 1)[-1])
        root.children.append(self._timeline(0, self.m_movie.tags, tr("ui.stage"), set()))
        groups = (
            (tr("ui.images"), self._images()),
            (tr("common.fonts"), [SNode("font", f"{font.font_id}: {font.name} ({font.file})", (font.font_id,)) for font in feuedit.fonts(self.m_movie)]),
            (tr("ui.texts"), [SNode("text", _text_label(text), (text.char_id,)) for text in feuedit.texts(self.m_movie)]),
            (tr("ui.classes"), self._symbols()),
            (tr("ui.code"), self._code()),
        )  # fmt: skip
        root.children += [SNode("group", f"{name} ({len(items)})", (), items) for name, items in groups if items]
        return root

    def _timeline(self, owner: int, tags: list[feuread.STag], label: str, seen: set[int]) -> SNode:
        node = SNode("timeline", label, (owner,))
        for i, tag in enumerate(tags):
            if tag.code == feuread.SHOW_FRAME:
                break  # the first frame: what the preview draws
            if tag.code not in (feuread.PLACE_OBJECT2, feuread.PLACE_OBJECT3):
                continue
            placement = feuread.read_placement(tag)
            child = SNode("place", self._place_label(placement), (owner, i))
            sprite = self.m_defs.get(placement.char_id) if placement.char_id is not None else None
            if sprite is not None and sprite.children is not None and placement.char_id not in seen:
                child.children = self._timeline(
                    placement.char_id, sprite.children, "", seen | {placement.char_id}
                ).children
            node.children.append(child)
        return node

    def _place_label(self, placement: feuread.SPlacement) -> str:
        what = self.m_defs.get(placement.char_id)
        kind = what.name if what is not None else (tr("ui.change") if placement.char_id is None else tr("ui.missing"))
        name = f'"{placement.name}" ' if placement.name else ""
        which = f"{name}{kind} {placement.char_id if placement.char_id is not None else ''}".rstrip()
        return tr("ui.what_at_depth", what=which, depth=placement.depth)

    def _images(self) -> list[SNode]:
        out = []
        for code, image in feuedit.images(self.m_movie):
            kind = "icon" if code == feuread.ATLAS_IMAGE else "image"
            out.append(
                SNode(
                    kind,
                    f"{image.char_id}: {image.path.rsplit(chr(92), 1)[-1]} {image.width}x{image.height}",
                    (image.char_id,),
                )
            )
        return out  # fmt: skip

    def _symbols(self) -> list[SNode]:
        out = []
        for tag in self.m_movie.tags:
            if tag.code in (feuread.SYMBOL_CLASS, feuread.EXPORT_ASSETS):
                out += [SNode("symbol", f"{cid}: {name}", (cid,)) for cid, name in feuread.read_symbols(tag)]
        return out

    def _code(self) -> list[SNode]:
        out = []
        for block, tag in enumerate(feuedit.abc_tags(self.m_movie)):
            abc = feuread.read_abc(tag)
            strings = feuabc.strings(abc.abc)
            label = tr(
                "ui.code_block_bytes_strings",
                block=block,
                size=number(len(abc.abc)),
                strings=number(len(strings)),
            )
            out.append(SNode("code", label, (block,)))
        return out

    def picture_ref(self, node: SNode | None) -> feuread.SExternalImage | None:
        """The picture a tree row stands for: an image or icon row, or a placement of one; else None."""
        if node is None:
            return None
        char_id = node.key[0] if node.kind in ("image", "icon") and node.key else None
        if node.kind == "place":
            owner, index = node.key
            tags = self.m_movie.tags if owner == 0 else self.m_defs[owner].children
            char_id = feuread.read_placement(tags[index]).char_id
        tag = self.m_defs.get(char_id) if char_id is not None else None
        if tag is not None and tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE):
            return feuread.read_image(tag)
        return None

    # Properties

    def properties(self, node: SNode) -> dict:
        """The values the property panel shows (and may change) for a tree row."""
        if node.kind in ("image", "icon"):
            image = feuread.read_image(self.m_defs[node.key[0]])
            found = self.m_ui.atlas_entry(image.atlas_name or image.path) if self.m_ui and image.atlas_name else None
            return {
                "id": image.char_id, "path": image.path, "width": image.width, "height": image.height,
                "game path": ui2205.image_path(image.path) if node.kind == "image" else (found[0] if found else "-"),
                "flags": image.flags,
            }  # fmt: skip
        if node.kind == "font":
            font = feuread.read_font(self.m_defs[node.key[0]])
            return {
                "id": font.font_id,
                "file": font.file,
                "name": font.name,
                "languages": ", ".join(lang for lang, _ in font.languages),
            }
        if node.kind == "text":
            edit_text = feuread.read_edit_text(self.m_defs[node.key[0]])
            return {
                "id": edit_text.char_id, "text": edit_text.text or "", "variable": edit_text.variable, "font": edit_text.font_id,
                "height": (edit_text.height or 0) / 20, "color": edit_text.color, "box": tuple(value / 20 for value in edit_text.bounds),
                "html": bool(edit_text.flags & feuread.EDIT_HTML),
            }  # fmt: skip
        if node.kind == "place":
            owner, index = node.key
            tags = self.m_movie.tags if owner == 0 else self.m_defs[owner].children
            placement = feuread.read_placement(tags[index])
            matrix = placement.matrix or feuread.SMatrix()
            return {"name": placement.name or "", "character": placement.char_id, "depth": placement.depth, "x": matrix.tx / 20, "y": matrix.ty / 20,
                    "scale": (round(matrix.a, 4), round(matrix.d, 4)), "class": placement.class_name or ""}  # fmt: skip
        if node.kind == "code":
            tag = feuedit.abc_tags(self.m_movie)[node.key[0]]
            return {"block": node.key[0], "strings": feuabc.strings(feuread.read_abc(tag).abc)}
        return {}


def _text_label(edit_text: feuread.SEditText) -> str:
    text = (edit_text.text or "").replace("\n", " ").replace("\r", " ")
    return f"{edit_text.char_id}: {text[:40] or tr('ui.empty')}" + (
        f" [{edit_text.variable}]" if edit_text.variable else ""
    )
