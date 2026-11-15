"""Export the pictures of Anno 2205's UI as PNG files with an index: one picture, a movie's, every movie's, the
shared ones, the atlas icons.

``Exporter`` plans (``movie_items``, ``all_movie_items``, ``shared_items``, ``atlas_items``, ``picture_item``) and
writes (``export``) ``Item`` lists; the commands (``uicommands``) and the window (``uiexportdialog``) share it. A
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


class ExportError(Exception):
    """A picture that cannot be turned into a PNG (a texture layout the readers do not know)."""


class MissingPicture(ExportError):
    """The game has no file for the picture."""


@dataclass
class Item:
    """One picture to write: ``name`` is its path below the export folder."""

    name: str
    source: str  # the game path of its file (an atlas icon: the atlas picture)
    kind: str  # picture or icon
    key: str  # the picture's identity for "which movies use it": the game path, or the icon's name as a game path
    atlas: tuple[str, feuassets.AtlasEntry] | None = None
    movies: list[str] = field(default_factory=list)
    width: int = 0
    height: int = 0
    status: str = "ok"  # ok, missing (the game has no file), or failed: why


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise ExportError("not a PNG file")
    return struct.unpack(">II", png[16:24])


def crop(width: int, rgba: bytes, x: int, y: int, w: int, h: int) -> bytes:
    """The ``w`` x ``h`` pixels at ``x``, ``y`` of an RGBA picture ``width`` wide."""
    return b"".join(rgba[((y + r) * width + x) * 4 : ((y + r) * width + x + w) * 4] for r in range(h))


def relative(game_path: str) -> str:
    """A UI game path without ``data/ui/`` and the pictures' common ``extractedfirefiles/`` folder."""
    path = game_path[len(ui2205.UI) :] if game_path.startswith(ui2205.UI) else game_path
    return path[len("extractedfirefiles/") :] if path.startswith("extractedfirefiles/") else path


def safe_name(name: str) -> str:
    """A path that stays below the export folder."""
    return "/".join(p for p in name.replace("\\", "/").split("/") if p not in ("", ".", ".."))


class Exporter:
    """Plans and writes picture exports of one install's UI."""

    def __init__(self, ui: ui2205.UiFiles) -> None:
        self.ui = ui
        self._textures: dict[str, tuple[int, int, bytes]] = {}
        self._usage: dict[str, list[str]] | None = None

    # Who uses what

    def pictures_of(self, movie_path: str) -> list[feuread.ExternalImage]:
        tags = self.ui.movie(movie_path).tags
        return [feuread.read_image(t) for t in tags if t.code in (feuread.IMAGE, feuread.ATLAS_IMAGE)]

    def usage(self) -> dict[str, list[str]]:
        """Picture key -> the movies that name it, over every movie of the install."""
        if self._usage is None:
            out: dict[str, set[str]] = {}
            for movie in self.ui.movies():
                for img in self.pictures_of(movie):
                    out.setdefault(ui2205.image_path(img.atlas_name or img.path), set()).add(movie)
            self._usage = {k: sorted(v) for k, v in out.items()}
        return self._usage

    # Plans

    def item_for(self, img: feuread.ExternalImage, name: str | None = None) -> Item:
        """The export of one image tag of a movie: an atlas icon cut out, or the picture's file."""
        if img.atlas_name:
            found = self.ui.atlas_entry(img.atlas_name)
            key = ui2205.image_path(img.atlas_name)
            if found is None:
                return Item(name or relative(key), "", "icon", key, status="missing")
            return self._icon(found[0], found[1], name)
        path = ui2205.image_path(img.path)
        return Item(name or relative(path), path, "picture", path)

    def item_by_name(self, ref: str) -> Item:
        """A picture by a game path (``data/ui/...``), a movie's image name, or an atlas entry's name."""
        flat = ref.replace("\\", "/")
        if flat.lower().startswith(ui2205.UI):
            ref = flat[len(ui2205.UI) :]
        path = ui2205.image_path(ref)
        if not self.ui.files.exists(path):
            entry = next(
                (e for _, a in self.ui.atlases().items() for e in a.entries if ui2205.image_path(e.name) == path), None
            )
            if entry is not None:
                return self.picture_item(feuread.ExternalImage(0, 0, 0, entry.name, 1, 0, entry.name))
        return self.picture_item(feuread.ExternalImage(0, 0, 0, ref, 0, 0))

    def _icon(self, atlas_picture: str, entry: feuassets.AtlasEntry, name: str | None = None) -> Item:
        key = ui2205.image_path(entry.name)
        return Item(name or relative(key), atlas_picture, "icon", key, (atlas_picture, entry), width=entry.width,
                    height=entry.height)  # fmt: skip

    def picture_item(self, img: feuread.ExternalImage) -> Item:
        item = self.item_for(img)
        item.movies = self.usage().get(item.key, [])
        return item

    def movie_items(self, movie_path: str, prefix: str = "") -> list[Item]:
        """Every picture and atlas icon a movie names, once each, below ``prefix`` (a folder named for the movie)."""
        seen: dict[str, Item] = {}
        for img in self.pictures_of(movie_path):
            item = self.item_for(img)
            if item.key not in seen:
                item.name = safe_name(prefix + item.name)
                item.movies = [movie_path]
                seen[item.key] = item
        return list(seen.values())

    def all_movie_items(self) -> list[Item]:
        return [i for m in self.ui.movies() for i in self.movie_items(m, movie_stem(m) + "/")]

    def shared_items(self) -> list[Item]:
        """Every PNG below ``data/ui`` that is not an atlas picture (``shared/...``: the movies' pictures, the
        portraits, news and session images, the font glyph pictures), with the movies that use each."""
        usage = self.usage()
        atlas_pictures = {t[: -len(".bfd")] + ".png" for t in self.ui.atlases()}
        paths = [p for p in self.ui.paths(".png") if p not in atlas_pictures]
        return [Item("shared/" + relative(p), p, "picture", p, movies=usage.get(p, [])) for p in paths]

    def atlas_items(self) -> list[Item]:
        """Every icon of every atlas, cut out and named by its entry: ``atlases/<atlas>/<entry>``."""
        usage, out = self.usage(), []
        for table, atlas in self.ui.atlases().items():
            stem = table.rsplit("/", 1)[-1][: -len(".bfd")]
            for entry in atlas.entries:
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
        if picture not in self._textures:
            dds = self.ui.read(ui2205.texture_path(picture))
            try:
                if dds is not None:
                    self._textures[picture] = feuassets.dds_rgba(dds)
                else:
                    png = self.ui.read(picture)
                    if png is None:
                        raise MissingPicture(f"{picture}: no file")
                    self._textures[picture] = ui2205.decode_png(png)
            except (feuread.FeuError, RuntimeError) as e:
                raise ExportError(f"{picture}: {e}") from e
        return self._textures[picture]

    def render(self, item: Item) -> bytes:
        """The PNG of an item; raises ``ExportError`` when the game has none."""
        if item.atlas is not None:
            picture, entry = item.atlas
            width, _height, rgba = self.texture(picture)
            x, y = entry.origin
            return imagewrite.png_bytes(entry.width, entry.height, crop(width, rgba, x, y, entry.width, entry.height))
        png = self.ui.read(item.source)
        if png is not None:
            return png
        dds = self.ui.read(ui2205.texture_path(item.source))
        if dds is None:
            raise MissingPicture(f"{item.source}: the game has no such file")
        try:
            width, height, rgba = feuassets.dds_rgba(dds)
        except feuread.FeuError as e:
            raise ExportError(f"{ui2205.texture_path(item.source)}: {e}") from e
        return imagewrite.png_bytes(width, height, rgba)

    def export(self, items: list[Item], out: Path, log: Callable[[int, int, str], None] | None = None) -> list[Item]:
        """Write every item below ``out`` and the index (``index.csv``, ``index.json``); returns the items with
        their size and status filled in. A picture that cannot be had is listed, not fatal."""
        done: list[Item] = []
        seen: set[str] = set()
        for n, item in enumerate(items):
            if item.name.lower() in seen:
                continue
            seen.add(item.name.lower())
            if log is not None:
                log(n, len(items), item.name)
            if item.status == "ok":
                try:
                    png = self.render(item)
                    item.width, item.height = png_size(png)
                    target = out / item.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(png)
                except MissingPicture:
                    item.status = "missing"
                except ExportError as e:
                    item.status = f"failed: {e}"
            done.append(item)
        write_index(done, out)
        return done


def movie_stem(movie_path: str) -> str:
    return movie_path.rsplit("/", 1)[-1][: -len(".feu")]


def write_index(items: list[Item], out: Path) -> None:
    """``index.csv`` and ``index.json``: name, source, kind, size, the movies that use it, status."""
    out.mkdir(parents=True, exist_ok=True)
    rows = [
        {"name": i.name, "source": i.source, "kind": i.kind, "width": i.width, "height": i.height,
         "movies": [m.rsplit("/", 1)[-1] for m in i.movies], "status": i.status}
        for i in items
    ]  # fmt: skip
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(INDEX_FIELDS)
        writer.writerows([*(r[k] if k != "movies" else ";".join(r[k]) for k in INDEX_FIELDS)] for r in rows)
    (out / "index.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", "utf-8", newline="\n")


def summary(items: list[Item]) -> str:
    ok = sum(1 for i in items if i.status == "ok")
    bad = [i for i in items if i.status != "ok"]
    if bad:
        return tr("{ok} pictures written, {bad} not available (listed in the index)", ok=ok, bad=len(bad))
    return tr("{ok} pictures written", ok=ok)
