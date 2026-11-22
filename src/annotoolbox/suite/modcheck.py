"""A check of a mod folder the editor wrote, for what the mod loader drops without a word, before the game starts.

``check(folder)`` returns one sentence per problem: no ``modinfo.json`` or one that does not read, a patch file
without a ``ModOps`` root or with an operation that names no asset, an island archive not packed at the fastest
zlib level (the game fails to load it), an island without its ``.a7minfo``, a ``RandomIsland`` asset whose file is
in neither the mod nor (when given) the game. The rules: island-editor-tools "Game rules".
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from ..formats.rda import FLAG_COMPRESSED, FLAG_ENCRYPTED, CRdaArchive
from ..infra.i18n import tr
from ..modloader.mods import MODINFO_NAMES, strip_jsonc

FASTEST = b"\x78\x01"  # the zlib header of level 1, the only one the game loads islands at


def _modinfo(folder: Path) -> list[str]:
    path = next(
        (folder / name for name in MODINFO_NAMES if (folder / name).is_file()), None
    )  # ``.jsonc`` too (an imported mod)
    if path is None:
        return [tr("common.no_modinfo_json_mod_loader")]
    try:
        modinfo = json.loads(strip_jsonc(path.read_text(encoding="utf-8-sig")))
    except (ValueError, UnicodeDecodeError) as exc:
        return [tr("common.modinfo_json_not_read_mod", error=exc)]
    named = isinstance(modinfo, dict) and modinfo.get("ModID")  # a list or a number reads as JSON and names nothing
    return [] if named else [tr("common.modinfo_json_names_no_modid")]


def _patches(folder: Path) -> list[str]:
    out = []
    for path in sorted(folder.rglob("*.xml")):
        relative_path = path.relative_to(folder).as_posix()
        try:
            root = ET.fromstring(path.read_text(encoding="utf-8"))
        except (ET.ParseError, UnicodeDecodeError) as exc:
            out.append(tr("common.not_xml_loader_ignores_file", file=relative_path, error=exc))
            continue
        if not relative_path.endswith("assets.xml"):
            continue
        if root.tag != "ModOps":
            out.append(tr("common.root_not_no_operation_runs", file=relative_path, tag=root.tag))
            continue
        for operation_number, operation in enumerate(root.iter("ModOp"), 1):
            if not (operation.get("GUID") or operation.get("Path")):
                out.append(
                    tr(
                        "common.operation_names_no_guid_path",
                        file=relative_path,
                        n=operation_number,
                        type=operation.get("Type"),
                    )
                )
    return out


def _compressed_at_fastest(path: Path) -> bool:
    """Whether every packed member of the island archive starts with the fastest zlib header."""
    archive = CRdaArchive(path)
    with path.open("rb") as file:
        for entry in archive.entries.values():
            if not entry.flags & FLAG_COMPRESSED or entry.flags & FLAG_ENCRYPTED:
                continue
            file.seek(entry.blob_offset if entry.contiguous else entry.offset)
            if file.read(2) != FASTEST:
                return False
    return True


def _islands(folder: Path, in_game: Callable[[str], bool] | None) -> list[str]:
    out = []
    for path in sorted(folder.rglob("*.a7m")):
        relative_path = path.relative_to(folder).as_posix()
        info_path = relative_path[:-4] + ".a7minfo"  # an island replaced in place keeps the game's own
        if not path.with_suffix(".a7minfo").is_file() and not (in_game is not None and in_game(info_path)):
            out.append(tr("common.no_a7minfo_beside_game_cannot", file=relative_path))
        try:
            if not _compressed_at_fastest(path):
                out.append(tr("common.packed_another_zlib_level_fastest", file=relative_path))
        except (OSError, ValueError) as exc:
            out.append(tr("common.archive_not_read", file=relative_path, error=exc))
    return out


def _file_paths(folder: Path, in_game: Callable[[str], bool] | None) -> list[str]:
    out = []
    for path in sorted(folder.rglob("assets.xml")):
        for file_path in re.findall(r"<FilePath>([^<]+)</FilePath>", path.read_text(encoding="utf-8", errors="ignore")):
            if not (folder / file_path).is_file() and not (in_game is not None and in_game(file_path)):
                where = tr("common.the_mod") if in_game is None else tr("common.mod_game")
                out.append(
                    tr(
                        "common.randomisland_asset_names_not_map",
                        file=file_path,
                        where=where,
                    )
                )
    return out


def check(folder: Path, in_game: Callable[[str], bool] | None = None) -> list[str]:
    """Every problem of the mod ``folder``; ``in_game(path)`` says whether the game has a file (to accept an island
    the mod registers but does not carry)."""
    folder = Path(folder)
    return _modinfo(folder) + _patches(folder) + _islands(folder, in_game) + _file_paths(folder, in_game)


def readable(files) -> Callable[[str], bool]:
    """``in_game`` for a reader of the game's files (anything with ``read(path)``)."""

    def has(path: str) -> bool:
        try:
            files.read(path)
        except (KeyError, FileNotFoundError, OSError):
            return False
        return True

    return has
