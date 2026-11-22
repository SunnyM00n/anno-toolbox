"""The island lists of the application (``editorapp.CApp`` takes them in as ``CIslandCatalogue``): per game its own
islands, those with an edit file and those built for it from another game, their groups, which routes between games
have an island build, and the island a game's edit request names. Start at ``catalogue``; the Islands dock shows it
(``docs/island-editor.md``).
"""

from __future__ import annotations

from pathlib import Path

from ..infra.i18n import N_, tr
from ..islands import islandedits
from ..othergames import installs
from ..suite import extspecs, features
from . import islandfiles, islandgroups
from .formats.a7m import edit117 as a7m_edit117
from .formats.isd import islandlist as isd_islandlist

BUILDABLE = (
    "anno117",
    "anno2070",
    "anno1800",
    "anno2205",
    "anno1404",
    "anno1701",
)  # the games whose edits a build reads
NATIVE = ("anno117", "anno2070")  # the editor's own games (the rest are "other games": ``gameviewers``)
BUILD_NOTES = {
    "anno1404": N_("islands.edits_built_last_data_archive"),
    "anno1701": N_("islands.edits_built_new_data_archive"),
    "anno2205": N_("islands.edits_built_archive_games_file"),
}
BUILD_NOTE_DEFAULT = N_("islands.edits_built_mod_replaces_island")
ISLANDS_2070 = "data/levels/islands/normal/"  # the islands the list names by their stem (the others by path)
RELEASE_ORDER = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")  # newest first: the tabs
POOL = "data/anno2070/islands/pool"  # where a built island lies in the mod


def builder_for(source_game: str, target_game: str) -> extspecs.SBuilder | None:
    """The island build registered for that route (``features.BUILDERS``), or None: this edition has none."""
    found = (
        builder
        for builder in features.extensions(features.BUILDERS).values()
        if (source_game, target_game) in builder.routes
    )
    return next(found, None)


class CIslandCatalogue:
    """The lists, over what ``App`` holds: ``settings``, ``games``, ``wants``, ``problems``, the folders
    (``edits_dir``, ``mod_dir``) and the games' files (``tree2070``, ``game117``, ``game_tree``, ``files``)."""

    _m_list2070: tuple[str, dict, dict] | None = None  # (install folder, name -> path, name -> group)

    def native_groups(self, key: str, names: list[str]) -> dict[str, str]:
        """The group of each native island of a game (``islandgroups``), by the name its list shows; empty when the
        game's files cannot be read (every island is then listed under Other)."""
        if not names:  # nothing to group: the game's files need not be there
            return {}
        try:
            if key == "anno2070":
                groups = self.islands2070()[1]
                return {name: groups[name] for name in names if name in groups}
            return islandgroups.island_groups(key, names, self.game_tree(key) if key == "anno1800" else None)
        except (OSError, KeyError, ValueError):
            return {}

    def islands2070(self) -> tuple[dict[str, str], dict[str, str]]:
        """Every 2070 island, read once per install: ``{list name: path}`` and ``{list name: group}``. The list names
        an island under ``ISLANDS_2070`` (the normal pool and its variants) by its stem, any other by its path."""
        folder = self.m_settings.anno2070
        if self._m_list2070 is None or self._m_list2070[0] != folder:
            tree = self.tree2070()
            paths = isd_islandlist.island_paths(tree)
            names = {(Path(path).stem if path.startswith(ISLANDS_2070) else path): path for path in paths}
            by_path = isd_islandlist.island_groups(tree, paths)
            self._m_list2070 = (folder, names, {name: by_path[path] for name, path in names.items()})
        return self._m_list2070[1], self._m_list2070[2]

    def find_island(self, game: str, island: str) -> tuple[str, str] | None:
        """The ``(kind, name)`` that ``open`` takes for the island a game's edit request names (its game path, or its file
        name), None when the game is not in this application, not installed or has no such island."""
        if game not in installs.GAMES or not self.wants(game) or not getattr(self.m_settings, game, ""):
            return None
        try:
            names = self.islands2070()[0] if game == "anno2070" else {}
            paths = list(names.values()) if names else islandfiles.island_paths(game, self.island_tree(game))
        except (OSError, KeyError, ValueError, RuntimeError):
            return None
        want = island.replace("\\", "/").strip().lower()
        found = next((path for path in paths if path.lower() == want), None) or next(
            (path for path in paths if Path(path).stem.lower() == Path(want).stem), None
        )
        if found is None:
            return None
        return game, next((name for name, path in names.items() if path == found), found)

    def island_tree(self, game: str):
        """The files a game's island list is read from: 117's behind ``Files`` (a mod's islands count), else its tree."""
        return self.files() if game == "anno117" else self.game_tree(game)

    def other_games(self) -> dict:
        """Per other game: its name, whether an editor reads its islands yet, and those islands."""
        out = {}
        for key, game in installs.GAMES.items():
            if key in NATIVE or not self.wants(key):
                continue
            folder = getattr(self.m_settings, key)
            entry = {"name": game.name, "engine": game.engine, "family": game.family, "folder": folder}
            entry |= {"readable": game.editor is not None, "islands": []}
            if folder and game.is_install(Path(folder)):
                try:
                    entry["islands"] = installs.islands(game, self.game_tree(key))
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                ) as exc:  # damaged, or ours without a backup: listed, with why
                    entry["problem"] = str(exc)
            elif folder:
                entry["problem"] = tr("islands.no_exe_folder", exe=game.exes[0])
            out[key] = entry
        return out

    def islands(self) -> dict:
        """What can be opened: islands built in the workspace, those with an edit file, 2070's islands, 117's
        own, the other games'."""
        out = {"built": [], "edited117": [], "edited2070": [], "anno2070": [], "anno117": []}
        out["games"] = self.other_games()
        if self.wants("anno117"):
            out["built"] = self._built()
            for path in sorted(self.edits_dir.glob(f"{a7m_edit117.EDIT_PREFIX}*.json")):
                edits = islandedits.load(path)
                if edits.get("source"):
                    row = {"source": edits["source"], "target": edits.get("target", "replace")}
                    named = {**row, "name": edits["name"], "open": edits["name"]} if edits.get("name") else row
                    out["edited117"].append(named)
            if not self.m_settings.missing("anno117"):  # each game's list needs only its own install
                game = self.game117()
                out["anno117"] = sorted(
                    island_path
                    for island_path in game.iter_paths("data/")
                    if island_path.endswith(".a7m") and "/islands/" in island_path
                )
        if self.wants("anno2070"):
            from .formats.isd import edit as isd_edit

            out["edited2070"] = self._edited(isd_edit.EDIT_PREFIX)
            if not self.m_settings.missing("anno2070"):
                out["anno2070"] = list(self.islands2070()[0])
        return out

    def _built(self) -> list[dict]:
        """The islands built into the workspace's mod: the name, whether an edit file exists, when it was built."""
        built = []
        pool = self.mod_dir / POOL
        for folder in sorted(pool.iterdir()) if pool.is_dir() else []:
            if folder.name.endswith("_base") or not (folder / f"{folder.name}.a7m").is_file():
                continue
            island = folder.name.removeprefix("a2070_")
            built.append(
                {
                    "name": island,
                    "edited": (self.edits_dir / f"{folder.name}.json").is_file(),
                    "built": folder.stat().st_mtime,
                }
            )
        return built

    def _edited(self, prefix: str) -> list[dict]:
        """The islands with an edit file of ``prefix`` in the edits folder: its ``source`` and the name it shows."""
        out = []
        for path in sorted(self.edits_dir.glob(f"{prefix}*.json")):
            source = islandedits.load(path).get("source")
            if source:
                out.append({"source": source, "name": Path(source).stem})
        return out

    def catalogue(self) -> dict:
        """Per game (117, 2070, then the others): its own islands (``native``), those with an edit file of
        their own (``edited``) and those built for it from another game (``converted``), with whether
        its edits can be built into a mod. The Islands dock's lists (docs/island-editor.md)."""
        data = self.islands()
        out = {}
        out |= self._catalogue_117_2070(data)
        for key, game_entry in data["games"].items():
            edited = self._edited(installs.GAMES[key].edit_prefix)
            note = tr("islands.engine_family", engine=game_entry["engine"], family=game_entry["family"])
            if game_entry.get("problem") or not game_entry["folder"]:
                note += " · " + (game_entry.get("problem") or tr("islands.not_found_set_folder"))
            buildable = key in BUILDABLE
            out[key] = {
                "name": game_entry["name"],
                "native": game_entry["islands"] if game_entry["readable"] else [],
                "edited": edited,
                "converted": [],
                "buildable": buildable,
                "note": note
                + (
                    tr(BUILD_NOTES.get(key, BUILD_NOTE_DEFAULT))
                    if buildable
                    else tr("islands.edits_kept_edit_files_they")
                ),
            }
        out = {k: out[k] for k in sorted(out, key=lambda k: RELEASE_ORDER.index(k) if k in RELEASE_ORDER else 99)}
        self._add_groups(out)
        return out

    def _add_groups(self, catalogue: dict) -> None:
        """The lists' grouping, a step of its own over whatever rows the entries hold: ``groups`` (native name ->
        group), ``group_order``, and for an island built from another game ``group`` (its source island's)."""
        for key, entry in catalogue.items():
            entry["groups"] = self.native_groups(key, entry["native"])
            entry["group_order"] = islandgroups.ORDER.get(key, ())
        build = builder_for("anno2070", "anno117")
        if (
            build and "anno2070" in catalogue and catalogue["anno2070"]["native"]
        ):  # what the build tab takes, and why not
            names = self.islands2070()[0]
            why = isd_islandlist.convertible(
                [names[name] for name in catalogue["anno2070"]["native"]], build.island_dirs
            )
            catalogue["anno2070"]["unconvertible"] = {name: why[names[name]] for name in names if why.get(names[name])}
            catalogue["anno2070"]["group_order"] = isd_islandlist.group_order(catalogue["anno2070"]["groups"].values())
        sources = catalogue.get("anno2070", {}).get("groups", {})
        for converted_entry in catalogue.get("anno117", {}).get("converted", []):
            converted_entry["group"] = sources.get(converted_entry["source"], islandgroups.OTHER)

    def _catalogue_117_2070(self, data: dict) -> dict:
        """The editor's own games' entries (the wanted ones); a game that is not installed says so in its note."""
        out = {}
        built = [
            {
                **built_island,
                "source_game": "anno2070",
                "source": tr("islands.new_island") if built_island["name"].startswith("new") else built_island["name"],
            }
            for built_island in data["built"]
        ]
        if self.wants("anno117"):
            out["anno117"] = {
                "name": "Anno 117",
                "native": data["anno117"],
                "edited": [{"name": Path(edit["source"]).stem, **edit} for edit in data["edited117"]],
                "converted": built,
                "buildable": True,
                "note": self.m_settings.missing("anno117"),
            }
        if self.wants("anno2070"):
            missing = self.m_settings.missing("anno2070")
            out["anno2070"] = {
                "name": "Anno 2070",
                "native": data["anno2070"],
                "edited": data["edited2070"],
                "converted": [],
                "buildable": True,
                "note": tr("islands.edits_built_anno_2070s_patch9") + (f" · {missing}" if missing else ""),
            }
        return out

    def routes(self) -> list[dict]:
        """Every source and target game pair, and whether that island build exists in this edition."""
        builders = features.extensions(features.BUILDERS).values()
        if self.m_games is not None or not builders:
            return []
        keys = [*RELEASE_ORDER, *(k for k in installs.GAMES if k not in RELEASE_ORDER)]
        return [
            {
                "source": source_game,
                "target": target_game,
                "available": any((source_game, target_game) in builder.routes for builder in builders),
            }
            for source_game in keys
            for target_game in keys
            if source_game != target_game
        ]
