"""An archive mod (a community ``.rda`` for 2070, 1404, 2205) opened in the asset editor: the files it replaces compared
with the game's, the difference turned into a change set, and a summary of what was imported.

``import_archive(game, archive, carry_to)`` returns a ``modimport.SImported``. The asset files the archive holds are
read as the game reads them and compared asset by asset (``modimport.assets_against``); its text files become texts
per language; every other member is written under ``carry_to`` and carried (``changeset.SCarried``), so a write
puts it back into the archive the build makes. ``docs/asset-editor.md``, "Open an archive mod".
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from ..formats.rda import CRdaArchive, CRdaError
from ..infra.i18n import tr
from ..othergames.index2205 import FILE_DB, CArchiveIndex
from ..sources import loca2070
from ..sources.overlay import normalize
from . import assetdiff, modimport
from .assetgame import TEXT_KEYS, CGame
from .changeset import CChangeSet, SCarried
from .gamefiles import SLayout


def text_matcher(layout: SLayout) -> Callable[[str], str | None]:
    """A function from a path to the language (its name in the editor) of the text file it is, else None."""
    patterns = [
        re.compile(
            "^" + re.escape(file_pattern).replace(r"\{language\}", "(?P<lang>[^/]+)").replace(r"\*", "[^/]*") + "$"
        )
        for file_pattern in layout.texts.lower().split("|")
        if file_pattern
    ]
    names = {code: name for name, code in layout.languages}

    def language(path: str) -> str | None:
        for pattern in patterns:
            if (found := pattern.match(path)) is not None:
                code = found.group("lang")
                return None if code == "metadata" else names.get(code, code)
        return None

    return language


def index_for(game: CGame, archive: Path) -> CArchiveIndex | None:
    """The archive index that names a 2205 patch's members: a ``file.db`` beside the archive, else the game's own
    (which has the entry once the patch is installed); None for the other games or an index that does not read."""
    if game.m_layout.key != "anno2205":
        return None
    beside = Path(archive).with_name("file.db")
    try:
        if beside.is_file():
            return CArchiveIndex(beside.read_bytes())
        return CArchiveIndex(game.m_files.read(FILE_DB)) if game.m_files.exists(FILE_DB) else None
    except (ValueError, OSError, KeyError):
        return None


def _members(archive: Path, index: CArchiveIndex | None = None) -> dict[str, bytes]:
    """The archive's files by normalized path, read whole (a mod archive is small next to the game's). With the
    game's archive index (2205: ``maindata/file.db``, whose entry for a patch points into the archive) a member the
    index names by position takes that path."""
    try:
        rda = CRdaArchive(archive)
        named = index.paths_in(archive.name) if index is not None else {}
        return {normalize(named.get(entry.offset, path)): rda.read_entry(entry) for path, entry in rda.entries.items()}
    except (CRdaError, OSError, ValueError) as error:
        raise ValueError(tr("assets.not_archive_reads", path=archive, error=error)) from error


def _line_texts(game: CGame, language: str, files: dict[str, bytes], result: modimport.SImported) -> None:
    """Texts of ``id=text`` files: every id a member has that the game's language lacks or says otherwise."""
    vanilla = game.texts(language)
    changed = {
        str(i): text
        for data in files.values()
        for i, text in loca2070.parse_guid_text(loca2070.decode_text_file(data)).items()
        if vanilla.get(str(i)) != text
    }
    if changed:
        result.changes.m_texts[language] = changed
        result.texts[language] = len(changed)


def _xml_texts(game: CGame, language: str, path: str, data: bytes, result: modimport.SImported) -> None:
    from .xmltree import parse

    old = game.m_files.read(path) if game.m_files.exists(path) else None
    changed, lost = assetdiff.text_changes(parse(old) if old else None, parse(data), TEXT_KEYS[1])
    if changed:
        result.changes.m_texts[language] = changed
        result.texts[language] = len(changed)
    result.lost += [f"{language}: {line}" for line in lost]


def _texts(game: CGame, members: dict[str, bytes], result: modimport.SImported) -> set[str]:
    """The texts of the archive's language files into the change set; the paths used."""
    layout, used = game.m_layout, set()
    if not layout.texts:
        return used
    which = text_matcher(layout)
    by_language: dict[str, dict[str, bytes]] = {}
    for path, data in members.items():
        if (language := which(path)) is not None:
            by_language.setdefault(language, {})[path] = data
            used.add(path)
    for language, files in sorted(by_language.items()):
        if layout.text_format == "lines":
            _line_texts(game, language, files, result)
        else:
            for path, data in files.items():
                _xml_texts(game, language, path, data, result)
    return used


def import_archive(
    game: CGame,
    archive: Path,
    carry_to: Path,
    progress: Callable[[str], None] | None = None,
    index: CArchiveIndex | None = None,
) -> modimport.SImported:
    """``archive`` compared with the game's files as a change set for ``game``; the members the change set cannot hold
    are written under ``carry_to`` (a folder of the workspace) and carried; ``index`` is the game's archive index for
    a 2205 patch (its entry names the members). ValueError when it is no archive."""
    say = progress or (lambda _text: None)
    archive = Path(archive)
    say(tr("assets.reading_name", name=archive.name))
    members = _members(archive, index if index is not None else index_for(game, archive))
    changes = CChangeSet(game.m_layout.key, _mod_id(archive), archive.stem, "0.1.0")
    result = modimport.SImported(changes, [], archive=archive.name)
    asset_files = {file_path.lower() for file_path in game.m_index.m_files}
    say(tr("assets.comparing_game"))
    for path in sorted(members.keys() & asset_files):
        doc = modimport.load_or_report(members[path], path, result)
        if doc is not None:
            modimport.assets_against(game, doc, result, path)
    used = _texts(game, members, result) | (members.keys() & asset_files)
    if not (members.keys() & asset_files):
        result.lost.append(tr("assets.archive_holds_none_games_asset"))
    _carry(members, used, carry_to, result)
    return result


def _mod_id(archive: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", archive.stem).strip("-") or "archive-mod"


def _carry(members: dict[str, bytes], used: set[str], folder: Path, result: modimport.SImported) -> None:
    """Every member the change set does not write itself, kept as a file under ``folder``, which is emptied first:
    the whole folder is carried, so a file an earlier import of the archive left would be built into the mod."""
    shutil.rmtree(folder, ignore_errors=True)
    rest = {member_path: member_data for member_path, member_data in members.items() if member_path not in used}
    for path in [
        member_path for member_path in rest if ".." in member_path.split("/") or ":" in member_path
    ]:  # a member must stay inside the folder
        result.lost.append(tr("assets.left_out_names_place_outside", path=path))
        del rest[path]
    for path, data in rest.items():
        destination = folder / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    result.carried += sorted(rest)
    if rest:
        result.changes.m_carry.append(SCarried(str(folder), sorted(used)))
