"""The other game's ground materials within an engine generation, for an island's paint palette: 1800's on a 117
island and 117's on an 1800 one, each brought in as files of the island's own game.

``materials(files, game)`` lists a game's materials for the palette (their textures named as the build places them,
so the editor can draw them: ``COtherMaterials``); ``bring_in(files, game, entry, into)`` gives the
palette entry an own material set can take (``terrain2070.compose_set``) and the files it needs: the material file in
the island's game's style, its maps' textures and a set of the one entry, all under ``imported_path``. ``CWithFiles``
reads such files before the game's; ``bring_in_all`` does it for an edit file's palette. Rules: island-regions "Other games' materials".
"""

from __future__ import annotations

import io
import re

from ..islands.terrain2070 import VANILLA_SET_DIR, vanilla_materials
from .islandassets import finish_image

SET_DIRS = {"anno117": VANILLA_SET_DIR, "anno1800": "/config/engine/material_sets/"}
IMPORTED = "data/imported"  # where another game's files go in a mod (its own paths below it)
MAPS = ("DiffuseFileName", "HeightFileName", "SpecFileName", "NormalFileName")


def imported_path(game: str, path: str) -> str:
    """Where a file of ``game`` (a game path) goes in a mod of the other game: below ``IMPORTED``, its own path kept."""
    return f"{IMPORTED}/{game.removeprefix('anno')}/{path.replace(chr(92), '/').removeprefix('data/')}"


def materials(files, game: str) -> list[dict]:
    """Every ground material of ``game``'s material sets, once each, marked with ``game`` (``files``: ``list`` and
    ``read``, as ``islandview.CFiles``); not the entries of the games' test sets whose material file does not exist
    (71 of 204 in 1800: picking one would fail the build), which have no ``diffuse``."""
    out = []
    for material in (material for material in vanilla_materials(files, SET_DIRS[game]) if material.get("diffuse")):
        looks = {k: imported_path(game, material[k]) for k in ("diffuse", "normal") if material.get(k)}
        out.append({**material, **looks, "game": game})
    return out


def game_of(path: str) -> str | None:
    """The game whose file ``path`` is where ``imported_path`` puts it (``data/imported/1800/...``), else None."""
    match = re.match(rf"{IMPORTED}/(\d+)/", (path or "").replace("\\", "/").lower())
    return f"anno{match[1]}" if match else None


def original_path(path: str) -> str:
    """``imported_path``'s file as the other game names it."""
    return "data/" + re.sub(rf"^{IMPORTED}/\d+/", "", path.replace("\\", "/"), flags=re.I)


def texture_png(files, path: str, is_normal: bool = False, is_opaque: bool = False, use_alpha: bool = False) -> bytes:
    """An imported material's texture (``path``: a map of ``materials``) as PNG, read from the other game's ``files``
    (the first of its ``_<n>.dds`` files), shaped as ``islandassets.finish_png`` does."""
    from PIL import Image

    from ..models import fileresolve

    found = textures(files, original_path(path))
    if not found:
        raise FileNotFoundError(path)
    picture = finish_image(
        Image.fromarray(fileresolve.decode(files.read(found[0])), "RGBA"), is_normal, is_opaque, use_alpha
    )
    out = io.BytesIO()  # in memory: a temp folder a closing program left open raised at its exit
    picture.save(out, "PNG")
    return out.getvalue()


class COtherMaterials:
    """What an editor of a vanilla island adds to offer the other game's ground materials: ``other_games`` (the app
    sets it: game key -> that game's files) names the games, ``vanilla_materials`` lists their materials, and a
    texture of one is read through its game (a base class before the editor's own)."""

    m_other_games: dict = {}

    def vanilla_materials(self) -> list[dict]:
        return [material for key, files in self.m_other_games.items() for material in materials(files, key)]

    def texture_png(self, psd: str, is_normal: bool = False, is_opaque: bool = False, use_alpha: bool = False) -> bytes:
        files = self.m_other_games.get(game_of(psd) or "")
        if files is None:
            return super().texture_png(psd, is_normal, is_opaque, use_alpha)  # type: ignore[misc]
        return texture_png(files, psd, is_normal, is_opaque, use_alpha)


def restyle(text: str, game: str, into: str) -> str:
    """A material file of ``game`` with its maps' paths below ``imported_path`` and its closing tags as ``into`` writes
    them (117 by name, 1800 ``</>``)."""
    out = text
    for tag in MAPS:
        match = re.search(rf"<{tag}>([^<]*)</(?:{tag})?>", out)
        if match is None:
            continue
        close = f"</{tag}>" if into == "anno117" else "</>"
        value = imported_path(game, match.group(1)) if match.group(1) else ""
        out = out.replace(match.group(0), f"<{tag}>{value}{close}")
    return out


def textures(files, path: str) -> list[str]:
    """The texture files of a map path (``<stem>.psd`` is stored as ``<stem>_0.dds``, ``<stem>_1.dds``, ...)."""
    stem = re.sub(r"\.(psd|png|tga|dds)$", "", path.replace("\\", "/"), flags=re.I)
    folder = stem.rsplit("/", 1)[0] + "/"
    return sorted(
        file for file in files.list(folder) if re.fullmatch(re.escape(stem.lower()) + r"_\d+\.dds", file.lower())
    )


def bring_in(files, game: str, entry: dict, into: str) -> tuple[dict, dict[str, bytes]]:
    """A material of ``game`` (a palette entry: ``file``, ``set``) for an island of ``into``: the palette entry with the
    paths the island's own set names, and the files at those paths (material, textures, a set of its one entry)."""
    text = files.read(entry["file"]).decode("utf-8", "ignore")
    out = {imported_path(game, entry["file"]): restyle(text, game, into).encode("utf-8")}
    for tag in MAPS:
        match = re.search(rf"<{tag}>([^<]*)</", text)
        for texture_path in textures(files, match.group(1)) if match and match.group(1) else []:
            out[imported_path(game, texture_path)] = files.read(texture_path)
    source = files.read(entry["set"]).decode("utf-8", "ignore")
    found = next(
        (e for e in re.findall(r"<Material>.*?\r?\n</>", source, re.S) if entry["file"] in e.replace("\\", "/")), None
    )
    if found is None:
        raise KeyError(f"{entry['file']} is not in {entry['set']}")
    one = found.replace(entry["file"], imported_path(game, entry["file"]))
    set_path = imported_path(game, entry["set"])
    out[set_path] = f"<MaterialSet>\n{one}\n</>\n".encode()
    return {"file": imported_path(game, entry["file"]), "set": set_path, "game": game, "from": entry["file"]}, out


class CWithFiles:
    """``files`` (``read``, ``list``) with the files of ``extra`` (game path -> bytes) read first."""

    def __init__(self, files, extra: dict[str, bytes]) -> None:
        self.m_files, self.m_extra = files, {k.lower(): content for k, content in extra.items()}

    def read(self, path: str) -> bytes:
        key = path.replace("\\", "/").lower()
        return self.m_extra[key] if key in self.m_extra else self.m_files.read(path)

    def list(self, prefix: str) -> list[str]:
        low = prefix.lower()
        return sorted({*self.m_files.list(prefix), *(k for k in self.m_extra if k.startswith(low))})

    def exists(self, path: str) -> bool:
        key = path.replace("\\", "/").lower()
        return key in self.m_extra or self.m_files.exists(path)


def bring_in_all(
    files, materials: dict, game: str, others: dict | None
) -> tuple[dict, object, dict[str, bytes], list[str]]:
    """An edit file's palette (``"id"`` -> entry) for an island of ``game``: every entry of another game (``"game"``)
    brought in through that game's files in ``others``; returns the palette, ``files`` with the brought-in files in
    front, those files, and the entries whose game is not at hand."""
    out, extra, missing = {}, {}, []
    for mid, entry in materials.items():
        other = entry.get("game")
        if not other or other == game or "from" in entry:
            out[mid] = entry
            continue
        if not others or other not in others:
            missing.append(f"material {entry['file']} of {other}")
            continue
        out[mid], found = bring_in(others[other], other, entry, game)
        extra |= found
    return out, (CWithFiles(files, extra) if extra else files), extra, missing
