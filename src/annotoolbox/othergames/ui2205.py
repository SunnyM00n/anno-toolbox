"""Anno 2205's Flash UI in the install: its movies, the images and fonts they name, and changed files put back.

``UiFiles`` reads the UI from the game's files (``installs.GAMES["anno2205"].tree``, or any reader with ``read``,
``exists`` and ``iter_paths``); ``image_path`` turns a movie's image reference into the game path.
``image_files`` makes the files a replaced picture needs (the PNG, its ``_0.dds`` texture, the size table);
``build`` writes changed files into an archive of our own and the game's archive index pointed at them
(``index2205``); ``install`` / ``uninstall`` go through ``delivery2205``, which owns the index the sessions share
(docs/formats/anno2205-ui-format.md, "Getting a changed file into the game").
"""

from __future__ import annotations

import io
import shutil
from collections.abc import Callable
from pathlib import Path

from ..formats import feuassets, feuread
from ..formats.rda import RdaArchive, write_v22_stored
from . import delivery2205
from .index2205 import FILE_DB, ArchiveIndex

UI = "data/ui/"
ARCHIVE = delivery2205.UI  # our archive, next to the game's data*.rda
OUT_NAME = "anno2205-flash-ui"  # the build's folder: only a folder of this name is ever removed


def image_path(ref: str) -> str:
    """The game path of an image a movie names: lower case, below ``data/ui/``, without the ``PNGs`` folder."""
    parts = [p for p in ref.replace("\\", "/").lower().split("/") if p not in ("", ".", "pngs")]
    return UI + "/".join(parts)


def texture_path(png: str) -> str:
    """The texture the game ships beside a UI picture."""
    return png[: -len(".png")] + "_0.dds" if png.endswith(".png") else png


def font_path(file: str) -> str:
    return UI + file.lower()


class UiFiles:
    """The UI of an install: movies, images, atlases, fonts, the size table."""

    def __init__(self, files) -> None:
        self.files = files
        self._read = getattr(files, "read_bytes", None) or files.read  # an archive tree, or a reader with ``read``
        self._atlases: dict[str, feuassets.Atlas] | None = None

    def file(self, path: str) -> bytes:
        return self._read(path)

    def paths(self, suffix: str) -> list[str]:
        """The UI's files with a suffix (``.feu``, ``.bfd``, ``.ffd``), sorted."""
        return sorted(p for p in self.files.iter_paths(UI) if p.endswith(suffix))

    def movies(self) -> list[str]:
        return self.paths(".feu")

    def movie(self, path: str) -> feuread.Movie:
        return feuread.read(self.file(path))

    def read(self, path: str) -> bytes | None:
        return self.file(path) if self.files.exists(path) else None

    def image(self, ref: str) -> bytes | None:
        """The PNG of an image reference, None when the game has no such file."""
        return self.read(image_path(ref))

    def sizes(self) -> feuassets.PngSizes:
        return feuassets.PngSizes(self.read(feuassets.SIZES_PATH) or b"")

    def atlases(self) -> dict[str, feuassets.Atlas]:
        """Atlas table path -> table, for every atlas of the UI."""
        if self._atlases is None:
            self._atlases = {p: feuassets.Atlas(self.file(p)) for p in self.paths(".bfd")}
        return self._atlases

    def atlas_entry(self, name: str) -> tuple[str, feuassets.AtlasEntry] | None:
        """Where an atlas image sits: (the atlas picture's path, its entry)."""
        for table, atlas in self.atlases().items():
            entry = atlas.find(name)
            if entry is not None:
                return table[: -len(".bfd")] + ".png", entry
        return None

    def users(self, ref: str) -> list[str]:
        """The movies that name an image (any spelling of the same game path)."""
        target, out = image_path(ref), []
        for path in self.movies():
            movie = self.movie(path)
            refs = (feuread.read_image(t).path for t in movie.tags if t.code in (feuread.IMAGE, feuread.ATLAS_IMAGE))
            if any(image_path(r) == target for r in refs):
                out.append(path)
        return out


def decode_png(png: bytes) -> tuple[int, int, bytes]:
    """A PNG's size and RGBA pixels (Pillow, the ``images`` extra)."""
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("replacing a picture needs Pillow: pip install pillow") from e
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    return img.width, img.height, img.tobytes()


def image_files(path: str, png: bytes, sizes: feuassets.PngSizes) -> dict[str, bytes]:
    """The files that replace the picture at ``path`` (a game path): the PNG and its texture; its size goes into
    ``sizes``, which the caller writes once after the last picture (``feuassets.SIZES_PATH``)."""
    width, height, rgba = decode_png(png)
    sizes.set(path, (width, height))
    return {path: png, texture_path(path): feuassets.rgba_dds(width, height, rgba)}


def build(changes: dict[str, bytes], index_raw: bytes, out: Path, log: Callable[[str], None] = print) -> Path:
    """Write ``changes`` (game path -> bytes) into ``out``: our archive and the archive index pointing at it, in the
    install's layout (``out/maindata``). ``index_raw``: the index the game has now. Returns the archive."""
    if not out.is_absolute() or out.name != OUT_NAME:
        raise ValueError(f"refusing to rebuild {out}: not an absolute path ending in {OUT_NAME!r}")
    index = ArchiveIndex(index_raw)
    missing = [p for p in changes if index.entry(p) is None]
    if missing:
        raise ValueError(
            f"the game's archive index has no entry for {', '.join(missing)}: only files it has can change"
        )
    if out.exists():
        shutil.rmtree(out)
    (out / "maindata").mkdir(parents=True)
    archive = out / ARCHIVE
    archive.write_bytes(write_v22_stored(changes))  # stored, flags 0: as the game keeps its UI files
    entries = RdaArchive(archive).entries
    for path in changes:
        index.point(path, ARCHIVE, entries[path])
    (out / FILE_DB).write_bytes(index.write())
    log(f"{len(changes)} files into {archive}, the archive index pointed at them")
    return archive


def is_ours(index_raw: bytes) -> bool:
    return ARCHIVE in ArchiveIndex(index_raw).archive_names()


def base_index(game: Path, old_backup: Path | None = None) -> bytes:
    """The index to build on: the game's own (``delivery2205.game_index``); ``old_backup``: where an earlier version
    kept it."""
    return delivery2205.game_index(game, (old_backup,) if old_backup else ())


def install(built: Path, game: Path, old_backup: Path | None = None, log: Callable[[str], None] = print) -> None:
    """Put the built archive into ``game`` through the shared delivery, which keeps the game's own index first;
    ``old_backup``: where an earlier version kept that index."""
    delivery2205.install(game, ARCHIVE, built / ARCHIVE, log, (old_backup,) if old_backup else ())


def uninstall(game: Path, old_backup: Path | None = None, log: Callable[[str], None] = print) -> bool:
    """Take our archive out again; the index keeps pointing at the edited sessions when they are installed."""
    return delivery2205.uninstall(game, ARCHIVE, log, (old_backup,) if old_backup else ())


def write_loose(changes: dict[str, bytes], out: Path) -> list[Path]:
    """The changed files as loose files under ``out`` (``out/data/ui/...``), for a game set to prefer them."""
    written = []
    for path, data in changes.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written
