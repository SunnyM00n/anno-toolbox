"""A game opened for the asset editor: its files, its schema (with another game's descriptions borrowed where its
own has none), its asset index and its texts.

``open_game(key, folder, cache)`` returns a ``Game``; ``Game.document(changes)`` the ``AssetDocument`` to edit. The
donor (117 borrows from 1800) is read from the user's own install at run time, never shipped
(``docs/tool-suite.md``, "Decisions (2026-11-07)").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from ..infra import i18n
from ..infra.i18n import tr
from ..sources import icons2070, loca2070
from . import descriptions, game1701
from .assetdoc import AssetDocument
from .assetindex import AssetIndex, build_index
from .changeset import ChangeSet
from .gamefiles import LAYOUTS, GameFiles, Layout, asset_files, open_files
from .schema import Schema, borrow, read_schema
from .xmltree import parse, text

TEXT_KEYS = ("LineId", "GUID")  # the id element of a text entry: 117's, 1800's
CACHE = "asset-cache"  # the workspace's folder of the asset index (``cache_dir``)
GAME_LANGUAGES = {"en": "english", "de": "german"}  # an interface language -> the language of the games' text tables


@dataclass
class Game:
    layout: Layout
    files: GameFiles
    schema: Schema
    index: AssetIndex
    notes: list[str] = field(default_factory=list)
    icon_data: tuple[dict, dict] = field(default_factory=lambda: ({}, {}))  # the atlases and GUID -> tile (icons2070)
    _texts: dict[str, dict[str, str]] = field(default_factory=dict)

    def document(self, changes: ChangeSet | None = None, path: Path | None = None) -> AssetDocument:
        doc = AssetDocument(self.schema, self.index, changes or ChangeSet(self.layout.key), path)
        doc.game_texts = self.texts
        doc.files = self.files
        return doc

    @cached_property
    def _numbers(self) -> dict[str, int]:
        return {p.lower(): k for k, p in reversed(list(enumerate(self.index.files)))}

    def file_number(self, path: str) -> int | None:
        """The index number of an asset file (``path`` lower case with forward slashes), None for any other path."""
        return self._numbers.get(path)

    def read_base(self, path: str) -> bytes | None:
        """A game file's bytes as the mod loader's session reads it (``modloader.engine.Session``): an asset file from
        the index's local copy, any other file from the game's tree; None where the game has none."""
        k = self.file_number(path)
        if k is not None:
            return self.index.local[k].read_bytes()
        return self.files.read(path) if self.files.exists(path) else None

    def lazy_ranges(self, path: str) -> list | None:
        """The byte range and GUID of each asset of an indexed asset file (so a session parses only the assets an
        operation reaches); None for a file that is not one."""
        k = self.file_number(path)
        return None if k is None else [(e.start, e.end, e.guid) for e in self.index.entries if e.file == k]

    def texts(self, language: str = "english") -> dict[str, str]:
        """Text id -> text of one language (read once); empty where the game keeps no such file."""
        if language not in self._texts:
            self._texts[language] = read_texts(self.files, self.layout, language)
        return self._texts[language]


def read_schema_of(layout: Layout, files: GameFiles) -> Schema:
    def maybe(path: str) -> bytes | None:
        try:
            return files.local(path).read_bytes() if path else None
        except FileNotFoundError:
            return None

    props = [data for p in layout.properties if (data := maybe(p)) is not None]
    return read_schema(layout.key, props, maybe(layout.templates), maybe(layout.data_sets))


def line_files(files: GameFiles, layout: Layout, language: str) -> list[str]:
    """The text files of a language for a layout of ``id=text`` lines (2070, 1404), in the order they are read."""
    code = layout.language_code(language)
    return [p for pattern in layout.texts.split("|") for p in files.glob(pattern.format(language=code))]


def read_line_texts(files: GameFiles, layout: Layout, language: str) -> dict[str, str]:
    out = {}
    for path in line_files(files, layout, language):
        out |= {str(k): v for k, v in loca2070.parse_guid_text(loca2070.decode_text_file(files.read(path))).items()}
    return out


def read_texts(files: GameFiles, layout: Layout, language: str) -> dict[str, str]:
    if layout.text_format == "lines" and layout.texts:
        return read_line_texts(files, layout, language)
    path = layout.texts.format(language=layout.language_code(language)) if layout.texts else ""
    try:
        data = files.local(path).read_bytes() if path else b""
    except FileNotFoundError:
        return {}
    out = {}
    for entry in parse(data).iter("Text") if data else ():
        key = next((text(entry, k) for k in TEXT_KEYS if text(entry, k)), "")
        if key:
            out[key] = text(entry, "Text")
    return out


def open_game(
    key: str,
    folder: Path,
    cache: Path,
    donor: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> Game:
    """The game ``key`` at ``folder``: schema, index (cached in ``cache``); ``donor``: the install of the game whose
    descriptions this one borrows (``Layout.donor``), None to borrow none."""
    say = progress or (lambda _text: None)
    layout = LAYOUTS[key]
    files = game1701.open_files(folder, cache) if layout.virtual else open_files(key, folder, cache)
    say(tr("reading the schema of {game}", game=layout.name))
    schema = read_schema_of(layout, files)
    notes = []
    if not schema.properties:
        notes.append(
            tr(
                "no property definitions found ({files}): forms show raw values",
                files=", ".join(layout.properties),
            )
        )
    if donor is not None and layout.donor:
        dl = LAYOUTS[layout.donor]
        try:
            gained = borrow(schema, read_schema_of(dl, open_files(dl.key, donor, cache)))
            notes.append(tr("{n} descriptions borrowed from {game}", n=gained, game=dl.name))
        except (OSError, ValueError) as exc:
            notes.append(tr("no descriptions from {game}: {reason}", game=dl.name, reason=exc))
    written = descriptions.apply(schema, descriptions.load(key))
    if written:
        notes.append(tr("{n} descriptions written for this editor", n=written))
    paths = asset_files(layout, files)
    if not paths:
        raise FileNotFoundError(
            tr(
                "no asset files of {game} under {folder} ({files})",
                game=layout.name,
                folder=folder,
                files=", ".join(layout.assets),
            )
        )
    index = build_index(files, paths, say, layout.follow_links)
    game = Game(layout, files, schema, index, notes + index.problems)
    if layout.atlas_icons:
        say(tr("reading the icon tables of {game}", game=layout.name))
        game.icon_data = icons2070.load_icon_data(files.read, files.exists)
    return game


def cache_dir(workspace: str | Path) -> Path:
    """The workspace's asset index cache, one for every app that opens a game's assets (a game indexed once opens
    at once in the others)."""
    return Path(workspace) / CACHE


def installed_folder(settings, key: str) -> Path | None:
    """The install folder of game ``key`` in the suite's settings, None when it is not set or missing (an empty
    setting is no folder: ``Path("")`` is the current one)."""
    named = str(getattr(settings, key, "") or "").strip()
    return Path(named) if named and Path(named).is_dir() else None


def open_installed(settings, key: str, progress: Callable[[str], None] | None = None, *, borrow: bool = False) -> Game:
    """The installed game ``key`` of the suite's settings, indexed into the shared cache; ``borrow``: with the donor's
    descriptions (the forms need them, a comparison or a build does not). FileNotFoundError when a folder is unset."""
    folder = installed_folder(settings, key)
    if folder is None:
        raise FileNotFoundError(tr("the folder of {game} is not set or is missing in the settings", game=key))
    workspace = str(getattr(settings, "workspace", "") or "").strip()
    if not workspace:
        raise FileNotFoundError(tr("the workspace is not set in the settings"))
    donor_key = LAYOUTS[key].donor
    donor = installed_folder(settings, donor_key) if borrow and donor_key else None
    return open_game(key, folder, cache_dir(workspace), donor, progress)


def interface_language() -> str:
    """The games' language of the suite's interface language (English for one the games' text tables lack)."""
    return GAME_LANGUAGES.get(i18n.current(), "english")
