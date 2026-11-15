"""Editing Anno 2205's UI movies: texts, images, fonts, placements and the code's string constants, in place.

Each function changes the tags of a ``feuread.Movie`` and leaves the rest as stored, so ``feuwrite.write`` gives
the game's bytes back with only the edit in them. ``Edit`` is one change as an edit file keeps it (``apply`` runs a
list of them), the form the Flash UI editor saves (docs/formats/anno2205-ui-format.md, "Editing").
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import feuabc, feuread, feuwrite
from .feuread import ATLAS_IMAGE, DEFINE_EDIT_TEXT, DO_ABC, FONT, IMAGE, PLACE_OBJECT2, PLACE_OBJECT3, Movie, Tag


class EditError(ValueError):
    """An edit that names something the movie does not have."""


def _defining(movie: Movie, codes: tuple[int, ...], cid: int) -> Tag:
    tag = next((t for t in movie.tags if t.code in codes and feuread.char_id(t) == cid), None)
    if tag is None:
        raise EditError(f"the movie has no {'/'.join(feuread.NAMES[c] for c in codes)} with id {cid}")
    return tag


def texts(movie: Movie) -> list[feuread.EditText]:
    return [feuread.read_edit_text(t) for t in movie.tags if t.code == DEFINE_EDIT_TEXT]


def images(movie: Movie) -> list[tuple[int, feuread.ExternalImage]]:
    """The images the movie names (tag code, image), in file order."""
    return [(t.code, feuread.read_image(t)) for t in movie.tags if t.code in (IMAGE, ATLAS_IMAGE)]


def fonts(movie: Movie) -> list[feuread.FontRef]:
    return [feuread.read_font(t) for t in movie.tags if t.code == FONT]


def set_text(movie: Movie, cid: int, text: str | None = None, color: tuple | None = None) -> None:
    """A text field's starting text and colour (RGBA); None leaves a part as it is."""
    tag = _defining(movie, (DEFINE_EDIT_TEXT,), cid)
    old = feuread.read_edit_text(tag)
    new = dataclasses.replace(
        old, text=old.text if text is None else text, color=old.color if color is None else tuple(color)
    )
    tag.body = feuwrite.encode_edit_text(new, old.rect_raw)


def set_image(movie: Movie, cid: int, path: str | None = None, size: tuple[int, int] | None = None) -> None:
    """Point an image at another file of the game, or give it the size of a replaced picture."""
    tag = _defining(movie, (IMAGE, ATLAS_IMAGE), cid)
    old = feuread.read_image(tag)
    w, h = size or (old.width, old.height)
    tag.body = feuwrite.encode_image(dataclasses.replace(old, path=path or old.path, width=w, height=h), tag.code)


def resize_images(movie: Movie, path: str, size: tuple[int, int]) -> int:
    """Give every image that names ``path`` (any case) a new size; returns how many there were."""
    count = 0
    for _, img in images(movie):
        if img.path.lower() == path.lower():
            set_image(movie, img.char_id, size=size)
            count += 1
    return count


def set_font(movie: Movie, font_id: int, file: str, name: str | None = None) -> None:
    """Point a font reference (and its language entries) at another font file of the game."""
    tag = _defining(movie, (FONT,), font_id)
    old = feuread.read_font(tag)
    langs = tuple((lang, file) for lang, _ in old.languages)
    tag.body = feuwrite.encode_font(dataclasses.replace(old, file=file, name=name or old.name, languages=langs))


def placements(movie: Movie) -> list[tuple[int, int, feuread.Placement]]:
    """Every placement: (sprite id or 0 for the main timeline, its index among that timeline's tags, placement)."""
    out = []
    for owner, tags in [(0, movie.tags)] + [(feuread.char_id(t), t.children) for t in movie.tags if t.children]:
        for i, tag in enumerate(tags):
            if tag.code in (PLACE_OBJECT2, PLACE_OBJECT3):
                out.append((owner, i, feuread.read_placement(tag)))
    return out


def move(movie: Movie, sprite: int, index: int, tx: int, ty: int) -> None:
    """Put a placement's character at ``tx``, ``ty`` (twips), its scale and rotation kept."""
    tags = movie.tags if sprite == 0 else _defining(movie, (feuread.DEFINE_SPRITE,), sprite).children
    if not 0 <= index < len(tags) or tags[index].code not in (PLACE_OBJECT2, PLACE_OBJECT3):
        raise EditError(f"timeline {sprite} has no placement at tag {index}")
    p = feuread.read_placement(tags[index])
    tags[index].body = feuwrite.encode_placement(p, dataclasses.replace(p.matrix or feuread.Matrix(), tx=tx, ty=ty))


def abc_tags(movie: Movie) -> list[Tag]:
    return [t for t in movie.tags if t.code == DO_ABC]


def set_strings(movie: Movie, changes: dict[int, str], block: int = 0) -> None:
    """Change string constants of the movie's code (``block``: which DoABC tag, in file order)."""
    tags = abc_tags(movie)
    if not 0 <= block < len(tags):
        raise EditError(f"the movie has no code block {block}")
    abc = feuread.read_abc(tags[block])
    tags[block].body = tags[block].body[: abc.offset] + feuabc.replace_strings(abc.abc, changes)


@dataclass(frozen=True)
class Edit:
    """One change of an edit file: ``op`` is text, image, font, move or string; ``target`` the id or index."""

    op: str
    target: int
    values: dict

    def to_json(self) -> dict:
        return {"op": self.op, "target": self.target, **self.values}

    @classmethod
    def from_json(cls, data: dict) -> Edit:
        rest = {k: v for k, v in data.items() if k not in ("op", "target")}
        return cls(str(data["op"]), int(data["target"]), rest)


def apply(movie: Movie, edits: list[Edit]) -> None:
    """Run an edit file's changes on a movie, in order."""
    for e in edits:
        v = e.values
        if e.op == "text":
            set_text(movie, e.target, v.get("text"), v.get("color"))
        elif e.op == "image":
            size = tuple(v["size"]) if v.get("size") else None
            set_image(movie, e.target, v.get("path"), size)
        elif e.op == "font":
            set_font(movie, e.target, v["file"], v.get("name"))
        elif e.op == "move":
            move(movie, int(v.get("sprite", 0)), e.target, int(v["x"]), int(v["y"]))
        elif e.op == "string":
            set_strings(movie, {e.target: v["text"]}, int(v.get("block", 0)))
        else:
            raise EditError(f"unknown edit {e.op!r}")
