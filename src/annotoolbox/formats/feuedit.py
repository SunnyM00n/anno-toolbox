"""Editing Anno 2205's UI movies: texts, images, fonts, placements and the code's string constants, in place.

Each function changes the tags of a ``feuread.SMovie`` and leaves the rest as stored, so ``feuwrite.write`` gives
the game's bytes back with only the edit in them. ``SEdit`` is one change as an edit file keeps it (``apply`` runs a
list of them), the form the Flash UI editor saves (docs/formats/anno2205-ui-format.md, "Editing").
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import feuabc, feuread, feuwrite
from .feuread import ATLAS_IMAGE, DEFINE_EDIT_TEXT, DO_ABC, FONT, IMAGE, PLACE_OBJECT2, PLACE_OBJECT3, SMovie, STag


class CEditError(ValueError):
    """An edit that names something the movie does not have."""


def _defining(movie: SMovie, codes: tuple[int, ...], cid: int) -> STag:
    tag = next(
        (candidate for candidate in movie.tags if candidate.code in codes and feuread.char_id(candidate) == cid), None
    )
    if tag is None:
        raise CEditError(f"the movie has no {'/'.join(feuread.NAMES[code] for code in codes)} with id {cid}")
    return tag


def texts(movie: SMovie) -> list[feuread.SEditText]:
    return [feuread.read_edit_text(tag) for tag in movie.tags if tag.code == DEFINE_EDIT_TEXT]


def images(movie: SMovie) -> list[tuple[int, feuread.SExternalImage]]:
    """The images the movie names (tag code, image), in file order."""
    return [(tag.code, feuread.read_image(tag)) for tag in movie.tags if tag.code in (IMAGE, ATLAS_IMAGE)]


def fonts(movie: SMovie) -> list[feuread.SFontRef]:
    return [feuread.read_font(tag) for tag in movie.tags if tag.code == FONT]


def set_text(movie: SMovie, cid: int, text: str | None = None, color: tuple | None = None) -> None:
    """A text field's starting text and colour (RGBA); None leaves a part as it is."""
    tag = _defining(movie, (DEFINE_EDIT_TEXT,), cid)
    old = feuread.read_edit_text(tag)
    new = dataclasses.replace(
        old, text=old.text if text is None else text, color=old.color if color is None else tuple(color)
    )
    tag.body = feuwrite.encode_edit_text(new, old.rect_raw)


def set_image(movie: SMovie, cid: int, path: str | None = None, size_px: tuple[int, int] | None = None) -> None:
    """Point an image at another file of the game, or give it the size of a replaced picture."""
    tag = _defining(movie, (IMAGE, ATLAS_IMAGE), cid)
    old = feuread.read_image(tag)
    width, height = size_px or (old.width, old.height)
    tag.body = feuwrite.encode_image(
        dataclasses.replace(old, path=path or old.path, width=width, height=height), tag.code
    )


def set_font(movie: SMovie, font_id: int, file: str, name: str | None = None) -> None:
    """Point a font reference (and its language entries) at another font file of the game."""
    tag = _defining(movie, (FONT,), font_id)
    old = feuread.read_font(tag)
    langs = tuple((lang, file) for lang, _ in old.languages)
    tag.body = feuwrite.encode_font(dataclasses.replace(old, file=file, name=name or old.name, languages=langs))


def placements(movie: SMovie) -> list[tuple[int, int, feuread.SPlacement]]:
    """Every placement: (sprite id or 0 for the main timeline, its index among that timeline's tags, placement)."""
    out = []
    for owner, tags in [(0, movie.tags)] + [
        (feuread.char_id(child_tag), child_tag.children) for child_tag in movie.tags if child_tag.children
    ]:
        for i, tag in enumerate(tags):
            if tag.code in (PLACE_OBJECT2, PLACE_OBJECT3):
                out.append((owner, i, feuread.read_placement(tag)))
    return out


def move(movie: SMovie, sprite: int, index: int, offset_x: int, offset_y: int) -> None:
    """Put a placement's character at ``offset_x``, ``offset_y`` (twips), its scale and rotation kept."""
    tags = movie.tags if sprite == 0 else _defining(movie, (feuread.DEFINE_SPRITE,), sprite).children
    if not 0 <= index < len(tags) or tags[index].code not in (PLACE_OBJECT2, PLACE_OBJECT3):
        raise CEditError(f"timeline {sprite} has no placement at tag {index}")
    placement = feuread.read_placement(tags[index])
    tags[index].body = feuwrite.encode_placement(
        placement, dataclasses.replace(placement.matrix or feuread.SMatrix(), tx=offset_x, ty=offset_y)
    )


def abc_tags(movie: SMovie) -> list[STag]:
    return [tag for tag in movie.tags if tag.code == DO_ABC]


def set_strings(movie: SMovie, changes: dict[int, str], block: int = 0) -> None:
    """Change string constants of the movie's code (``block``: which DoABC tag, in file order)."""
    tags = abc_tags(movie)
    if not 0 <= block < len(tags):
        raise CEditError(f"the movie has no code block {block}")
    abc = feuread.read_abc(tags[block])
    tags[block].body = tags[block].body[: abc.offset] + feuabc.replace_strings(abc.abc, changes)


@dataclass(frozen=True)
class SEdit:
    """One change of an edit file: ``op`` is text, image, font, move or string; ``target`` the id or index."""

    op: str
    target: int
    values: dict

    def to_json(self) -> dict:
        return {"op": self.op, "target": self.target, **self.values}

    @classmethod
    def from_json(cls, data: dict) -> SEdit:
        rest = {k: edit_values for k, edit_values in data.items() if k not in ("op", "target")}
        return cls(str(data["op"]), int(data["target"]), rest)


def apply(movie: SMovie, edits: list[SEdit]) -> None:
    """Run an edit file's changes on a movie, in order."""
    for edit in edits:
        edit_values = edit.values
        if edit.op == "text":
            set_text(movie, edit.target, edit_values.get("text"), edit_values.get("color"))
        elif edit.op == "image":
            size = tuple(edit_values["size"]) if edit_values.get("size") else None
            set_image(movie, edit.target, edit_values.get("path"), size)
        elif edit.op == "font":
            set_font(movie, edit.target, edit_values["file"], edit_values.get("name"))
        elif edit.op == "move":
            move(movie, int(edit_values.get("sprite", 0)), edit.target, int(edit_values["x"]), int(edit_values["y"]))
        elif edit.op == "string":
            set_strings(movie, {edit.target: edit_values["text"]}, int(edit_values.get("block", 0)))
        else:
            raise CEditError(f"unknown edit {edit.op!r}")
