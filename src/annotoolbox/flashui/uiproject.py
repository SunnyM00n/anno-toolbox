"""The edit file of the Flash UI editor: what a mod changes in the game's UI, kept apart from the game's files.

A ``Project`` holds, per movie, the edits (``feuedit.Edit``) and whole files that replace the game's (a movie
changed in a Flash tool, a picture, an atlas icon); ``changes`` applies them to the install's own files and returns
every file that changes, ready for ``ui2205.build``. Kept as JSON next to the files it names, so a build can
always be redone from the game's originals (docs/formats/anno2205-ui-format.md, "The edit file").
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import feuassets, feuedit, feuread, feuwrite
from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..othergames import ui2205

FORMAT = 1


@dataclass
class Project:
    """Edits per movie, replacement files per game path (relative to the edit file's folder or absolute)."""

    path: Path | None = None
    edits: dict[str, list[feuedit.Edit]] = field(default_factory=dict)  # movie path -> its edits, in order
    files: dict[str, str] = field(default_factory=dict)  # game path -> a file that replaces it (movie or picture)
    icons: dict[str, str] = field(default_factory=dict)  # atlas image name -> a picture of the same size
    saved: dict = field(default_factory=dict, compare=False, repr=False)  # ``to_json`` as last read or written

    def __post_init__(self) -> None:
        self.saved = self.to_json()

    @property
    def unsaved(self) -> bool:
        """Whether the edits differ from the edit file as last read or written."""
        return self.to_json() != self.saved

    @classmethod
    def load(cls, path: Path) -> Project:
        """The edit file at ``path``; a ``ValueError`` when it is not one (or not JSON)."""
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError(tr("ui.not_flash_ui_edit_file", path=path, format=FORMAT))
        edits = {m: [feuedit.Edit.from_json(e) for e in es] for m, es in data.get("movies", {}).items()}
        return cls(path, edits, dict(data.get("files", {})), dict(data.get("icons", {})))

    def to_json(self) -> dict:
        return {
            "format": FORMAT,
            "game": "anno2205",
            "movies": {m: [e.to_json() for e in es] for m, es in sorted(self.edits.items()) if es},
            "files": dict(sorted(self.files.items())),
            "icons": dict(sorted(self.icons.items())),
        }

    def save(self, path: Path | None = None) -> Path:
        self.path = path or self.path
        if self.path is None:
            raise ValueError(tr("ui.edit_file_no_path_yet"))
        data = self.to_json()
        write_atomic(self.path, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        self.saved = data
        return self.path

    def add(self, movie: str, edit: feuedit.Edit) -> None:
        self.edits.setdefault(movie, []).append(edit)

    def undo(self, movie: str) -> feuedit.Edit | None:
        edits = self.edits.get(movie) or []
        return edits.pop() if edits else None

    def source(self, name: str) -> Path:
        """A replacement file as named in the edit file, resolved against its folder."""
        p = Path(name)
        return p if p.is_absolute() or self.path is None else self.path.parent / p

    def original(self, ui: ui2205.UiFiles, movie: str) -> bytes:
        """A movie before the edits: the game's file, or the file that replaces it (signed as the game's)."""
        raw = self.source(self.files[movie]).read_bytes() if movie in self.files else ui.file(movie)
        return feuwrite.resign(raw, feuread.SIGNATURE) if raw[:3] == b"FWS" else raw  # FWS: saved by a Flash tool

    def movie_bytes(self, ui: ui2205.UiFiles, movie: str) -> bytes:
        """A movie as the game's file (or its replacement), with the edits applied."""
        raw = self.original(ui, movie)
        if not self.edits.get(movie):
            return raw
        m = feuread.read(raw)
        feuedit.apply(m, self.edits[movie])
        return feuwrite.write(m)


def changes(project: Project, ui: ui2205.UiFiles) -> dict[str, bytes]:
    """Every file of the game the edit file changes, as it must be written."""
    out: dict[str, bytes] = {}
    sizes = ui.sizes()
    resized: dict[str, tuple[int, int]] = {}
    for path, name in project.files.items():
        if path.endswith(".png"):
            out |= ui2205.image_files(path, project.source(name).read_bytes(), sizes)
            resized[path] = sizes.get(path)
    for movie in sorted(set(project.edits) | {p for p in project.files if p.endswith(".feu")}):
        out[movie] = project.movie_bytes(ui, movie)
    if resized:
        _resize_users(out, ui, resized)
        out[feuassets.SIZES_PATH] = sizes.write()
    out |= _icons(project, ui)
    return {p: data for p, data in out.items() if ui.read(p) != data}


def _resize_users(out: dict[str, bytes], ui: ui2205.UiFiles, resized: dict[str, tuple[int, int]]) -> None:
    """Every movie naming a replaced picture gets its new size (the movies store it beside the name)."""
    for movie in ui.movies():
        m = feuread.read(out[movie]) if movie in out else ui.movie(movie)
        hits = [(ui2205.image_path(img.path), img) for _, img in feuedit.images(m)]
        hits = [(p, img) for p, img in hits if p in resized and (img.width, img.height) != resized[p]]
        for p, img in hits:
            feuedit.set_image(m, img.char_id, size=resized[p])
        if hits:
            out[movie] = feuwrite.write(m)


def _icons(project: Project, ui: ui2205.UiFiles) -> dict[str, bytes]:
    """Atlas icons pasted into their atlas pictures (same size only: the atlas tables stay as they are)."""
    try:
        from PIL import Image
    except ImportError as e:
        if project.icons:
            raise RuntimeError(tr("ui.replacing_atlas_icon_needs_pillow")) from e
        return {}
    pictures: dict[str, Image.Image] = {}
    for name, file in project.icons.items():
        found = ui.atlas_entry(name)
        if found is None:
            raise ValueError(tr("ui.no_atlas_icon", name=name))
        atlas, entry = found
        icon = Image.open(project.source(file)).convert("RGBA")
        if icon.size != (entry.width, entry.height):
            raise ValueError(
                tr(
                    "ui.x_icon",
                    file=file,
                    width=icon.size[0],
                    height=icon.size[1],
                    name=name,
                    icon_width=entry.width,
                    icon_height=entry.height,
                )
            )
        if atlas not in pictures:
            pictures[atlas] = Image.open(io.BytesIO(ui.file(atlas))).convert("RGBA")
        pictures[atlas].paste(icon, entry.origin)
    out = {}
    for atlas, img in pictures.items():
        buf = io.BytesIO()
        img.save(buf, "PNG")
        out[atlas] = buf.getvalue()
        out[ui2205.texture_path(atlas)] = feuassets.rgba_dds(img.width, img.height, img.tobytes())
    return out
