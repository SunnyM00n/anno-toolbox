"""A game's texts of one language as a table of entries: which languages a game has, which files hold one, and the
rules by which a key that occurs twice is read (docs/text-editor.md, "What each game's text files are").

``languages(files, layout)`` lists them, ``read_language`` reads one into a ``Loaded`` (the table and the files it came
from, which a rewrite needs), ``read_names`` reads 117's editor names, groups and variations. ``files`` is a
``assetedit.gamefiles.GameFiles``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..assetedit.assetgame import line_files
from ..assetedit.gamefiles import GameFiles, Layout
from ..sources import loca2070
from . import textfiles

NOT_LANGUAGES = {"metadata"}  # files of the same name pattern that hold no texts of a language
_ENTRY = re.compile(r"<Text>(.*?)</Text>", re.S)
_FIELD = re.compile(r"<(LineId|Name|FolderId|MasterId|VariationId)>(.*?)</\1>", re.S)


@dataclass(frozen=True)
class Entry:
    id: str
    text: str
    file: str  # the path in the game it is read from
    name: str = ""  # 117: the editor's name of the line
    group: str = ""  # the first word of the name; the file's name for the games with several files
    folder: str = ""  # 117: the line's text group (``FolderId``), an id the game's data gives no name
    master: str = ""  # 117: the line this one is a variation of (the other portrait's or the gamepad's twin)
    kind: str = ""  # 117: what the variation is for (``VariationId``)


@dataclass(frozen=True)
class Meta:
    """What 117's ``texts_metadata.xml`` says of a line."""

    name: str = ""
    folder: str = ""
    master: str = ""
    kind: str = ""

    @property
    def group(self) -> str:
        return self.name.split(" ", 1)[0] if self.name else ""


@dataclass
class Table:
    """The entries of one language in file order, by id (``get``); ``twins``: a line's variations by the line's id."""

    entries: list[Entry] = field(default_factory=list)
    by_id: dict[str, Entry] = field(default_factory=dict)
    twins: dict[str, list[str]] = field(default_factory=dict)

    def get(self, key: str) -> Entry | None:
        return self.by_id.get(key)

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, key: str) -> bool:
        return key in self.by_id


@dataclass
class Loaded:
    """One language read: the table and the parsed files (path -> ``TextFile``, in the order the game reads them)."""

    language: str
    table: Table
    files: dict[str, textfiles.TextFile]


def style_of(layout: Layout) -> str:
    return textfiles.LINES if layout.text_format == "lines" else textfiles.XML


def languages(files: GameFiles, layout: Layout) -> dict[str, str]:
    """Language -> its name in the game's paths, for those the install holds (the layout's own list where it has one:
    2205 has two files that are no language)."""
    found: dict[str, str] = {}
    if not layout.texts:
        return found
    if style_of(layout) == textfiles.LINES:
        for name, code in layout.languages:
            if line_files(files, layout, name):
                found[name] = code
        return found
    allowed = {n for n, _ in layout.languages}
    for path in files.glob(layout.texts.format(language="*")):
        token = path.rsplit("/", 1)[-1].removeprefix("texts_").removesuffix(".xml")
        if token not in NOT_LANGUAGES and (not allowed or token in allowed):
            found[token] = token
    return dict(sorted(found.items()))


def paths_of(files: GameFiles, layout: Layout, language: str) -> list[str]:
    """The files of a language in the order the game reads them (the developer files of 2070 first: they only fill gaps)."""
    if style_of(layout) == textfiles.LINES:
        found = line_files(files, layout, language)
        return sorted(found, key=lambda p: p.rsplit("/", 1)[-1] not in loca2070.DEVELOPER_TEXT_FILES)
    path = layout.texts.format(language=layout.language_code(language))
    return [path] if files.exists(path) else []


def read_language(files: GameFiles, layout: Layout, language: str, names: dict[str, Meta] | None = None) -> Loaded:
    """A language's table. A key twice: the first entry wins in the XML files (the game's table keeps the first), the
    last in the line files (a later file overrides). ``names``: ``read_names``' answer, for the editor names and the
    variations."""
    style, loaded = style_of(layout), {}
    for path in paths_of(files, layout, language):
        loaded[path] = textfiles.parse(path, files.read(path), style)
    table = Table()
    for path, parsed in loaded.items():
        stem = path.rsplit("/", 1)[-1].removesuffix(".txt")
        for slot in parsed.slots:
            meta = (names or {}).get(slot.key, Meta())
            group = meta.group or (stem if style == textfiles.LINES else "")
            entry = Entry(slot.key, slot.value, path, meta.name, group, meta.folder, meta.master, meta.kind)
            table.entries.append(entry)
            if style == textfiles.LINES or slot.key not in table.by_id:
                table.by_id[slot.key] = entry
                if meta.master:
                    table.twins.setdefault(meta.master, []).append(slot.key)
    return Loaded(language, table, loaded)


def read_names(files: GameFiles, layout: Layout) -> dict[str, Meta]:
    """117's ``texts_metadata.xml`` by line id; empty for the games without one."""
    path = layout.texts.format(language="metadata") if layout.texts else ""
    if not path or style_of(layout) != textfiles.XML or not files.exists(path):
        return {}
    out: dict[str, Meta] = {}
    for block in _ENTRY.finditer(files.read(path).decode("utf-8", "replace")):
        fields = {k: v.strip() for k, v in _FIELD.findall(block.group(1))}
        if "LineId" in fields:
            out[fields["LineId"]] = Meta(
                fields.get("Name", ""),
                fields.get("FolderId", ""),
                fields.get("MasterId", ""),
                fields.get("VariationId", ""),
            )
    return out
