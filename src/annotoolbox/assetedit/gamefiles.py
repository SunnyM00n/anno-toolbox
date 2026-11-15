"""Where each game keeps its asset data, and the game's files as the asset editor reads them.

``LAYOUTS`` names per game the schema files, the templates, the data sets, the asset files, the file a ModOps patch
names and the forms of output; ``open_files`` gives a ``GameFiles`` over ``sources/gametree`` (the one reader of the
viewer too: an install, or a plain folder of loose files, a test or an unpacked tree). ``local`` hands out a real
file to index and slice. ``docs/tool-suite.md`` section 2 has the table.
"""

from __future__ import annotations

import fnmatch
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..sources.gametree import GameTree, game_stamp, loose_tree, open_game, stamp_of
from ..sources.overlay import normalize


@dataclass(frozen=True)
class Layout:
    key: str
    name: str
    properties: tuple[str, ...]  # definitions first (the full schema), then the stripped export (defaults)
    templates: str
    data_sets: str
    assets: tuple[str, ...]  # file patterns, in load order
    patch_target: str = ""  # the asset file a ModOps patch names (games with a mod loader)
    outputs: tuple[str, ...] = ("native",)  # "modops" (a mod for the mod loader), "native" (the game's own files)
    donor: str = ""  # the game whose schema lends descriptions where this one has none
    texts: str = ""  # the text file of a language (``{language}``); ``text_format`` "lines": patterns joined by "|"
    text_format: str = "xml"  # "xml": ``<Text>`` entries with a GUID or LineId; "lines": UTF-16 ``id=text`` lines
    languages: tuple[tuple[str, str], ...] = ()  # (name shown, name in ``texts``) where the files are named otherwise
    follow_links: bool = False  # a group's ``FileName`` is the root of another asset file: one tree (2205)
    atlas_icons: bool = False  # icons are tiles of atlas textures named by ``icons.xml`` (2070, 1404, 1701)
    virtual: bool = False  # the asset files are generated from the game's data (1701: ``game1701``)

    @property
    def language_names(self) -> tuple[str, ...]:
        """The languages the text tab offers: the layout's own, else the common ones of the XML text files."""
        return tuple(n for n, _ in self.languages) or XML_LANGUAGES

    def language_code(self, language: str) -> str:
        return dict(self.languages).get(language, language)


XML_LANGUAGES = (
    "english",
    "german",
    "french",
    "italian",
    "polish",
    "russian",
    "spanish",
    "chinese",
    "japanese",
    "korean",
)
LINE_LANGUAGES = (  # 2070's and 1404's folder names under data/loca
    ("english", "eng"), ("german", "ger"), ("french", "fra"), ("italian", "ita"), ("polish", "pol"),
    ("russian", "rus"), ("spanish", "esp"), ("czech", "cze"),
)  # fmt: skip
LINE_TEXTS = "data/loca/{language}/txt/*.txt|addondata/loca/{language}/txt/*.txt"
LINE_LANGUAGES_1701 = (
    ("english", "eng"), ("german", "ger"), ("french", "fra"), ("italian", "ita"), ("polish", "pol"),
    ("spanish", "spa"), ("czech", "cze"), ("hungarian", "hun"),
)  # fmt: skip
GENERATED_1701 = "data/config/game/property.ini."  # the prefix of the files ``game1701`` generates
EXPORT_117 = "data/base/config/export/"
EXPORT_1800 = "data/config/export/main/asset/"
LAYOUTS = {
    lay.key: lay
    for lay in (
        Layout(
            "anno117", "Anno 117",
            (EXPORT_117 + "properties-meta.xml", EXPORT_117 + "properties.xml"),
            EXPORT_117 + "templates.xml", "data/base/config/game/datasets.xml",
            (EXPORT_117 + "assets.xml",), EXPORT_117 + "assets.xml", ("modops", "native"), "anno1800",
            "data/base/config/gui/texts_{language}.xml",
        ),
        Layout(
            "anno1800", "Anno 1800",
            (EXPORT_1800 + "properties-toolone.xml", EXPORT_1800 + "properties.xml"),
            EXPORT_1800 + "templates.xml", EXPORT_1800 + "datasets.xml",
            (EXPORT_1800 + "assets.xml",), EXPORT_1800 + "assets.xml", ("modops", "native"), "",
            "data/config/gui/texts_{language}.xml",
        ),
        Layout(
            "anno2205", "Anno 2205",
            ("data/config/game/asset/properties.xml",), "data/config/game/asset/templates.xml",
            "data/config/game/datasets.xml", ("data/config/game/asset/*.xml",),
            texts="data/config/gui/texts_{language}.xml", follow_links=True,
            languages=tuple((n, n) for n in ("english", "french", "german", "polish", "russian", "spanish")),
        ),
        Layout(
            "anno2070", "Anno 2070",
            ("data/config/game/properties.xml",), "data/config/game/templates.xml", "data/config/game/datasets.xml",
            ("data/config/game/assets.xml", "data/config/dlc_*/assets.xml", "addondata/config/balancing/*_assets.xml"),
            texts=LINE_TEXTS, text_format="lines", languages=LINE_LANGUAGES, atlas_icons=True,
        ),
        Layout(
            "anno1404", "Anno 1404",
            ("data/config/game/properties.xml",), "data/config/game/templates.xml", "data/config/game/datasets.xml",
            ("data/config/game/assets.xml", "addondata/config/game/assets.xml"),
            texts=LINE_TEXTS, text_format="lines", languages=LINE_LANGUAGES, atlas_icons=True,
        ),
        Layout(
            "anno1701", "Anno 1701",
            (GENERATED_1701 + "properties.xml",), GENERATED_1701 + "templates.xml", GENERATED_1701 + "datasets.xml",
            (GENERATED_1701 + "assets.xml",), texts="data/loca/{language}/txt/*.txt", text_format="lines",
            languages=LINE_LANGUAGES_1701, atlas_icons=True, virtual=True,
        ),
    )
}  # fmt: skip
NOT_ASSETS = ("properties", "templates", "datasets", "properties-toolone", "properties-meta")  # beside the assets


class GameFiles:
    """A game's files by path (lower case, forward slashes): ``read``, ``glob``, ``local`` and a ``stamp``, over the
    core's ``sources/gametree`` (``tree``). The game's archives are opened on the first ``read``, ``glob`` or ``tree``:
    a ``local`` copy made before needs none of them."""

    def __init__(self, opener: Callable[[], GameTree], stamp: str, cache: Path) -> None:
        """``opener()``: the game's ``GameTree``, called once when a file is first needed."""
        self._opener, self.stamp, self.cache = opener, stamp, cache
        self._tree: GameTree | None = None
        self._opening = threading.Lock()  # the window and its background jobs may both be the first to ask

    @property
    def tree(self) -> GameTree:
        if self._tree is None:
            with self._opening:
                if self._tree is None:
                    self._tree = self._opener()
        return self._tree

    @property
    def paths(self) -> list[str]:
        return self.tree.paths

    def exists(self, path: str) -> bool:
        return self.tree.exists(path)

    def read(self, path: str) -> bytes:
        return self.tree.read(path)

    def glob(self, pattern: str) -> list[str]:
        pat = normalize(pattern)
        return [p for p in self.paths if fnmatch.fnmatchcase(p, pat)]

    def local(self, path: str) -> Path:
        """A real file with the content of ``path`` (the file itself where the game is loose files, else a copy made
        in the cache once per stamp), so an index can slice it without holding the whole file; FileNotFoundError when
        the game has no such file."""
        key = normalize(path)
        digest = hashlib.sha1(f"{self.stamp}|{key}".encode()).hexdigest()[:16]
        dest = self.cache / "files" / f"{digest}-{Path(path).name}"
        if dest.is_file():
            return dest
        if not self.exists(key):
            raise FileNotFoundError(path)
        if (loose := self.tree.local(key)) is not None:
            return loose
        write_atomic(dest, self.read(key), sync=False)
        return dest


def folder_files(root: Path, cache: Path) -> GameFiles:
    """Loose files under ``root`` (a test tree, an unpacked game)."""
    root = Path(root)
    return GameFiles(lambda: loose_tree(root), stamp_of(root.rglob("*.xml")), cache)


def open_files(key: str, folder: Path, cache: Path) -> GameFiles:
    """The installed game ``key`` (``anno117``, ``anno1800``, ...) at ``folder``; a folder without the game's
    archives is read as loose files."""
    folder = Path(folder)
    return GameFiles(lambda: open_game(key, folder, cache), game_stamp(key, folder), cache)


def asset_files(layout: Layout, files: GameFiles) -> list[str]:
    """The game's asset files in load order (each pattern's matches sorted; the schema files beside them left out).
    A pattern without a wildcard is looked for in the cache first, so a cached game opens no archive."""
    out: list[str] = []
    for pattern in layout.assets:
        if not any(c in pattern for c in "*?["):
            try:
                files.local(pattern)
            except FileNotFoundError:
                continue
            found = [pattern]
        else:
            found = files.glob(pattern)
        out += [p for p in found if Path(p).stem.lower() not in NOT_ASSETS and p not in out]
    return out
