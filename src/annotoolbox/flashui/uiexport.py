"""Export the pictures of Anno 2205's UI as PNG files with an index: one picture, a movie's, every movie's, the
shared ones, the atlas icons.

``CExporter`` plans (``movie_items``, ``all_movie_items``, ``shared_items``, ``atlas_items``, ``picture_item``) and
writes (``export``) ``SItem`` lists; the commands (``uicommands``) and the window (``uiexportdialog``) share it. A
picture comes from the game's PNG where there is one, else from its ``_0.dds`` texture; an atlas icon is cut out of
the atlas texture by its table entry and named by that entry. No Qt, no Pillow: PNG files are written with ``zlib``
(format: docs/formats/anno2205-ui-format.md, "From a movie's name to a game file"; use: docs/ui/2205/flash-ui-editor.md).
"""

from __future__ import annotations

import csv
import json
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import feuassets, feuread, imagewrite
from ..infra.i18n import tr
from ..othergames import ui2205

INDEX_FIELDS = ("name", "source", "kind", "width", "height", "movies", "status")


class CExportError(Exception):
    """A picture that cannot be turned into a PNG (a texture layout the readers do not know)."""


class CMissingPicture(CExportError):
    """The game has no file for the picture."""


@dataclass
class SItem:
    """One picture to write: ``name`` is its path below the export folder."""

    name: str
    source: str  # the game path of its file (an atlas icon: the atlas picture)
    kind: str  # picture or icon
    key: str  # the picture's identity for "which movies use it": the game path, or the icon's name as a game path
    atlas: tuple[str, feuassets.SAtlasEntry] | None = None
    movies: list[str] = field(default_factory=list)
    width_px: int = 0
    height_px: int = 0
    status: str = "ok"  # ok, missing (the game has no file), or failed: why


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise CExportError("not a PNG file")
    return struct.unpack(">II", png[16:24])


def crop(sheet_width_px: int, rgba: bytes, x: int, y: int, crop_width_px: int, crop_height_px: int) -> bytes:
    """The ``w`` x ``h`` pixels at ``x``, ``y`` of an RGBA picture ``width`` wide."""
    return b"".join(
        rgba[((y + row) * sheet_width_px + x) * 4 : ((y + row) * sheet_width_px + x + crop_width_px) * 4]
        for row in range(crop_height_px)
    )


def relative(game_path: str) -> str:
    """A UI game path without ``data/ui/`` and the pictures' common ``extractedfirefiles/`` folder."""
    path = game_path[len(ui2205.UI) :] if game_path.startswith(ui2205.UI) else game_path
    return path[len("extractedfirefiles/") :] if path.startswith("extractedfirefiles/") else path


def safe_name(name: str) -> str:
    """A path that stays below the export folder: no step up, no drive (a movie names its pictures as it likes)."""
    parts = (part.replace(":", "_") for part in name.replace("\\", "/").split("/"))
    return "/".join(part for part in parts if part not in ("", ".", ".."))


class CExporter:
    """Plans and writes picture exports of one install's UI."""

    def __init__(self, ui_files: ui2205.CUiFiles) -> None:
        self.m_ui = ui_files
        self._m_textures: dict[str, tuple[int, int, bytes]] = {}
        self._m_usage: dict[str, list[str]] | None = None

    # Who uses what

    def pictures_of(self, movie_path: str) -> list[feuread.SExternalImage]:
        tags = self.m_ui.movie(movie_path).tags
        return [feuread.read_image(tag) for tag in tags if tag.code in (feuread.IMAGE, feuread.ATLAS_IMAGE)]

    def usage(self) -> dict[str, list[str]]:
        """Picture key -> the movies that name it, over every movie of the install."""
        if self._m_usage is None:
            out: dict[str, set[str]] = {}
            for movie in self.m_ui.movies():
                for image in self.pictures_of(movie):
                    out.setdefault(ui2205.image_path(image.atlas_name or image.path), set()).add(movie)
            self._m_usage = {k: sorted(paths) for k, paths in out.items()}
        return self._m_usage

    # Plans

    def item_for(self, image: feuread.SExternalImage, name: str | None = None) -> SItem:
        """The export of one image tag of a movie: an atlas icon cut out, or the picture's file."""
        if image.atlas_name:
            found = self.m_ui.atlas_entry(image.atlas_name)
            key = ui2205.image_path(image.atlas_name)
            if found is None:
                return SItem(name or safe_name(relative(key)), "", "icon", key, status="missing")
            return self._icon(found[0], found[1], name)
        path = ui2205.image_path(image.path)
        return SItem(name or safe_name(relative(path)), path, "picture", path)

    def item_by_name(self, ref: str) -> SItem:
        """A picture by a game path (``data/ui/...``), a movie's image name, or an atlas entry's name."""
        flat = ref.replace("\\", "/")
        if flat.lower().startswith(ui2205.UI):
            ref = flat[len(ui2205.UI) :]
        path = ui2205.image_path(ref)
        if not self.m_ui.m_files.exists(path):
            entry = next(
                (
                    atlas_entry
                    for _, atlas in self.m_ui.atlases().items()
                    for atlas_entry in atlas.m_entries
                    if ui2205.image_path(atlas_entry.name) == path
                ),
                None,
            )
            if entry is not None:
                return self.picture_item(feuread.SExternalImage(0, 0, 0, entry.name, 1, 0, entry.name))
        return self.picture_item(feuread.SExternalImage(0, 0, 0, ref, 0, 0))

    def _icon(self, atlas_picture: str, entry: feuassets.SAtlasEntry, name: str | None = None) -> SItem:
        key = ui2205.image_path(entry.name)
        return SItem(name or safe_name(relative(key)), atlas_picture, "icon", key, (atlas_picture, entry), width_px=entry.width,
                    height_px=entry.height)  # fmt: skip

    def picture_item(self, image: feuread.SExternalImage) -> SItem:
        item = self.item_for(image)
        item.movies = self.usage().get(item.key, [])
        return item

    def movie_items(self, movie_path: str, prefix: str = "") -> list[SItem]:
        """Every picture and atlas icon a movie names, once each, below ``prefix`` (a folder named for the movie)."""
        seen: dict[str, SItem] = {}
        for image in self.pictures_of(movie_path):
            item = self.item_for(image)
            if item.key not in seen:
                item.name = safe_name(prefix + item.name)
                item.movies = [movie_path]
                seen[item.key] = item
        return list(seen.values())

    def all_movie_items(self) -> list[SItem]:
        return [i for movie in self.m_ui.movies() for i in self.movie_items(movie, movie_stem(movie) + "/")]

    def shared_items(self) -> list[SItem]:
        """Every PNG below ``data/ui`` that is not an atlas picture (``shared/...``: the movies' pictures, the
        portraits, news and session images, the font glyph pictures), with the movies that use each."""
        usage = self.usage()
        atlas_pictures = {atlas_path[: -len(".bfd")] + ".png" for atlas_path in self.m_ui.atlases()}
        paths = [path for path in self.m_ui.paths(".png") if path not in atlas_pictures]
        return [SItem("shared/" + relative(path), path, "picture", path, movies=usage.get(path, [])) for path in paths]

    def atlas_items(self) -> list[SItem]:
        """Every icon of every atlas, cut out and named by its entry: ``atlases/<atlas>/<entry>``."""
        usage, out = self.usage(), []
        for table, atlas in self.m_ui.atlases().items():
            stem = table.rsplit("/", 1)[-1][: -len(".bfd")]
            for entry in atlas.m_entries:
                item = self._icon(
                    table[: -len(".bfd")] + ".png",
                    entry,
                    safe_name(f"atlases/{stem}/{relative(ui2205.image_path(entry.name))}"),
                )
                item.movies = usage.get(item.key, [])
                out.append(item)
        return out

    # Writing

    def texture(self, picture: str) -> tuple[int, int, bytes]:
        """An atlas picture's pixels (its ``_0.dds``; the PNG through Pillow when it has no texture)."""
        if picture not in self._m_textures:
            dds = self.m_ui.read(ui2205.texture_path(picture))
            try:
                if dds is not None:
                    self._m_textures[picture] = feuassets.dds_rgba(dds)
                else:
                    png = self.m_ui.read(picture)
                    if png is None:
                        raise CMissingPicture(f"{picture}: no file")
                    self._m_textures[picture] = ui2205.decode_png(png)
            except (feuread.CFeuError, RuntimeError) as e:
                raise CExportError(f"{picture}: {e}") from e
        return self._m_textures[picture]

    def render(self, item: SItem) -> bytes:
        """The PNG of an item; raises ``CExportError`` when the game has none."""
        if item.atlas is not None:
            picture, entry = item.atlas
            width, height, rgba = self.texture(picture)
            x, y = entry.origin
            if min(x, y) < 0 or x + entry.width > width or y + entry.height > height:
                raise CExportError(f"{entry.name}: its place lies outside the {width} x {height} atlas {picture}")
            return imagewrite.png_bytes(entry.width, entry.height, crop(width, rgba, x, y, entry.width, entry.height))
        png = self.m_ui.read(item.source)
        if png is not None:
            return png
        dds = self.m_ui.read(ui2205.texture_path(item.source))
        if dds is None:
            raise CMissingPicture(f"{item.source}: the game has no such file")
        try:
            width, height, rgba = feuassets.dds_rgba(dds)
        except feuread.CFeuError as e:
            raise CExportError(f"{ui2205.texture_path(item.source)}: {e}") from e
        return imagewrite.png_bytes(width, height, rgba)

    def export(self, items: list[SItem], out: Path, log: Callable[[int, int, str], None] | None = None) -> list[SItem]:
        """Write every item below ``out`` and the index (``index.csv``, ``index.json``); returns the items with
        their size and status filled in. A picture that cannot be had is listed, not fatal."""
        done: list[SItem] = []
        seen: set[str] = set()
        for number, item in enumerate(items):
            if item.name.lower() in seen:
                continue
            seen.add(item.name.lower())
            if log is not None:
                log(number, len(items), item.name)
            if item.status == "ok":
                try:
                    png = self.render(item)
                    item.width_px, item.height_px = png_size(png)
                    target = out / item.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(png)
                except CMissingPicture:
                    item.status = "missing"
                except CExportError as e:
                    item.status = f"failed: {e}"
            done.append(item)
        write_index(done, out)
        return done


def movie_stem(movie_path: str) -> str:
    return movie_path.rsplit("/", 1)[-1][: -len(".feu")]


def write_index(items: list[SItem], out: Path) -> None:
    """``index.csv`` and ``index.json``: name, source, kind, size, the movies that use it, status."""
    out.mkdir(parents=True, exist_ok=True)
    rows = [
        {"name": i.name, "source": i.source, "kind": i.kind, "width": i.width_px, "height": i.height_px,
         "movies": [movie_path.rsplit("/", 1)[-1] for movie_path in i.movies], "status": i.status}
        for i in items
    ]  # fmt: skip
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as index_file:
        writer = csv.writer(index_file, lineterminator="\n")
        writer.writerow(INDEX_FIELDS)
        writer.writerows([*(row[k] if k != "movies" else ";".join(row[k]) for k in INDEX_FIELDS)] for row in rows)
    (out / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", "utf-8", newline="\n")


def summary(items: list[SItem]) -> str:
    ok_count = sum(1 for i in items if i.status == "ok")
    bad = [i for i in items if i.status != "ok"]
    if bad:
        return tr("ui.pictures_written_not_available_listed", ok=ok_count, bad=len(bad))
    return tr("ui.ok_pictures_written", ok=ok_count)
