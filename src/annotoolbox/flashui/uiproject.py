"""The edit file of the Flash UI editor: what a mod changes in the game's UI, kept apart from the game's files.

A ``CProject`` holds, per movie, the edits (``feuedit.SEdit``) and whole files that replace the game's (a movie
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
class CProject:
    """Edits per movie, replacement files per game path (relative to the edit file's folder or absolute)."""

    m_path: Path | None = None
    m_edits: dict[str, list[feuedit.SEdit]] = field(default_factory=dict)  # movie path -> its edits, in order
    m_files: dict[str, str] = field(default_factory=dict)  # game path -> a file that replaces it (movie or picture)
    m_icons: dict[str, str] = field(default_factory=dict)  # atlas image name -> a picture of the same size
    m_saved: dict = field(default_factory=dict, compare=False, repr=False)  # ``to_json`` as last read or written

    def __post_init__(self) -> None:
        self.m_saved = self.to_json()

    @property
    def unsaved(self) -> bool:
        """Whether the edits differ from the edit file as last read or written."""
        return self.to_json() != self.m_saved

    @classmethod
    def load(cls, path: Path) -> CProject:
        """The edit file at ``path``; a ``ValueError`` when it is not one (or not JSON)."""
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError(tr("ui.not_flash_ui_edit_file", path=path, format=FORMAT))
        edits = {
            movie: [feuedit.SEdit.from_json(edit) for edit in movie_edits]
            for movie, movie_edits in data.get("movies", {}).items()
        }
        return cls(path, edits, dict(data.get("files", {})), dict(data.get("icons", {})))

    def to_json(self) -> dict:
        return {
            "format": FORMAT,
            "game": "anno2205",
            "movies": {
                movie: [edit.to_json() for edit in movie_edits]
                for movie, movie_edits in sorted(self.m_edits.items())
                if movie_edits
            },
            "files": dict(sorted(self.m_files.items())),
            "icons": dict(sorted(self.m_icons.items())),
        }

    def save(self, path: Path | None = None) -> Path:
        self.m_path = path or self.m_path
        if self.m_path is None:
            raise ValueError(tr("ui.edit_file_no_path_yet"))
        data = self.to_json()
        write_atomic(self.m_path, (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
        self.m_saved = data
        return self.m_path

    def add(self, movie: str, edit: feuedit.SEdit) -> None:
        self.m_edits.setdefault(movie, []).append(edit)

    def undo(self, movie: str) -> feuedit.SEdit | None:
        edits = self.m_edits.get(movie) or []
        return edits.pop() if edits else None

    def source(self, name: str) -> Path:
        """A replacement file as named in the edit file, resolved against its folder."""
        path = Path(name)
        return path if path.is_absolute() or self.m_path is None else self.m_path.parent / path

    def original(self, ui_files: ui2205.CUiFiles, movie: str) -> bytes:
        """A movie before the edits: the game's file, or the file that replaces it (signed as the game's)."""
        raw = self.source(self.m_files[movie]).read_bytes() if movie in self.m_files else ui_files.file(movie)
        return feuwrite.resign(raw, feuread.SIGNATURE) if raw[:3] == b"FWS" else raw  # FWS: saved by a Flash tool

    def movie_bytes(self, ui_files: ui2205.CUiFiles, movie: str) -> bytes:
        """A movie as the game's file (or its replacement), with the edits applied."""
        raw = self.original(ui_files, movie)
        if not self.m_edits.get(movie):
            return raw
        parsed = feuread.read(raw)
        feuedit.apply(parsed, self.m_edits[movie])
        return feuwrite.write(parsed)


def changes(project: CProject, ui_files: ui2205.CUiFiles) -> dict[str, bytes]:
    """Every file of the game the edit file changes, as it must be written."""
    out: dict[str, bytes] = {}
    sizes = ui_files.sizes()
    resized: dict[str, tuple[int, int]] = {}
    for path, name in project.m_files.items():
        if path.endswith(".png"):
            out |= ui2205.image_files(path, project.source(name).read_bytes(), sizes)
            resized[path] = sizes.get(path)
    for movie in sorted(
        set(project.m_edits) | {movie_path for movie_path in project.m_files if movie_path.endswith(".feu")}
    ):
        out[movie] = project.movie_bytes(ui_files, movie)
    if resized:
        _resize_users(out, ui_files, resized)
        out[feuassets.SIZES_PATH] = sizes.write()
    out |= _icons(project, ui_files)
    return {movie_path: data for movie_path, data in out.items() if ui_files.read(movie_path) != data}


def _resize_users(out: dict[str, bytes], ui_files: ui2205.CUiFiles, resized: dict[str, tuple[int, int]]) -> None:
    """Every movie naming a replaced picture gets its new size (the movies store it beside the name)."""
    for movie in ui_files.movies():
        movie_file = feuread.read(out[movie]) if movie in out else ui_files.movie(movie)
        hits = [(ui2205.image_path(image.path), image) for _, image in feuedit.images(movie_file)]
        hits = [
            (path, image) for path, image in hits if path in resized and (image.width, image.height) != resized[path]
        ]
        for path, image in hits:
            feuedit.set_image(movie_file, image.char_id, size_px=resized[path])
        if hits:
            out[movie] = feuwrite.write(movie_file)


def _icons(project: CProject, ui_files: ui2205.CUiFiles) -> dict[str, bytes]:
    """Atlas icons pasted into their atlas pictures (same size only: the atlas tables stay as they are)."""
    try:
        from PIL import Image
    except ImportError as e:
        if project.m_icons:
            raise RuntimeError(tr("ui.replacing_atlas_icon_needs_pillow")) from e
        return {}
    pictures: dict[str, Image.Image] = {}
    for name, file in project.m_icons.items():
        found = ui_files.atlas_entry(name)
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
            pictures[atlas] = Image.open(io.BytesIO(ui_files.file(atlas))).convert("RGBA")
        pictures[atlas].paste(icon, entry.origin)
    out = {}
    for atlas, image in pictures.items():
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        out[atlas] = buffer.getvalue()
        out[ui2205.texture_path(atlas)] = feuassets.rgba_dds(image.width, image.height, image.tobytes())
    return out
