"""A sample of a game's islands opened in the island editor the way it opens them, and every model their objects,
props and forest place drawn the editor's way: each config through the editor's ``model`` (nested configs followed),
each mesh and texture it names through the editor's own loaders (``texture_png`` decodes it). What does not draw
is the editor's ``file_problems``, by reason (``models.fileresolve``).

``check_islands(key, folder, cache, count)`` is the entry; the ``viewer-files --islands`` command prints its
``IslandsReport`` beside the viewer's files check. docs/island-editor.md "How the models find their files".
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MAX_DEPTH = 4  # nested configs followed this deep (render.modellibrary)


@dataclass
class IslandsReport:
    """The islands opened (and those that did not open), the configs they place, the parts and textures drawn, and
    what did not draw: (kind, reason) -> count, with examples."""

    game: str
    islands: list[str] = field(default_factory=list)
    unopened: list[tuple[str, str]] = field(default_factory=list)
    configs: set[str] = field(default_factory=set)
    unreadable: list[tuple[str, str]] = field(default_factory=list)
    parts: int = 0
    textures: int = 0
    failed: Counter = field(default_factory=Counter)
    examples: dict = field(default_factory=dict)

    def lines(self) -> list[str]:
        out = [
            f"{self.game}, island editor: {len(self.islands)} islands opened ({len(self.unopened)} did not open), "
            f"{len(self.configs)} configs placed, {self.parts} parts, {self.textures} textures drawn",
            f"  files that do not draw: {sum(self.failed.values())}",
        ]
        for reason, n in self.failed.most_common():
            out.append(f"    {reason}: {n}")
            out += [f"      {e}" for e in self.examples.get(reason, [])]
        out += [f"    did not open: {path}: {why}" for path, why in self.unopened[:6]]
        out += [f"    config does not read: {cfg}: {why}" for cfg, why in self.unreadable[:6]]
        return out


def island_paths(key: str, tree) -> list[str]:
    """The game's island files, as game paths (117's through ``Files``, which reads a mod first, the others by their
    record: ``installs.islands``)."""
    from ..othergames.installs import GAMES, islands

    if key == "anno117":
        return sorted(p for p in tree.list("data/") if p.endswith(GAMES[key].suffix) and GAMES[key].island_folder in p)
    return islands(GAMES[key], tree)


def game_tree(key: str, folder: Path, cache: Path):
    """The install's files as the island editor reads them (117's behind ``Files``, which a mod folder joins)."""
    from ..othergames.installs import GAMES

    index = GAMES[key].index(folder, cache / "index")
    if key == "anno117":
        from ..islands.islandview import Files

        return Files(index)
    return index


def open_island(key: str, tree, path: str, cache: Path):
    """An island in the editor class the app opens it with (read only: no edit file). 117 and 2070 have editors of
    their own (``Game.editor`` "117" / "2070"); the other games' are ``editorapp.viewer_class``."""
    from ..othergames.installs import GAMES

    game = GAMES[key]
    if game.editor == "117":
        from .formats.a7m.edit117 import VanillaEditor

        return VanillaEditor(tree, path, cache)
    if game.editor == "2070":
        from .formats.isd.edit import Island2070Editor

        return Island2070Editor(tree, path, cache)
    from .editorapp import viewer_class

    return viewer_class(game.editor)(game, tree, path, cache)


def placed_configs(editor) -> list[str]:
    """The model configs an open island places: its objects', its props' and its forest's."""
    found = [o.get("cfg") for o in editor.objects()]
    for name in ("props", "forest"):
        try:
            got = getattr(editor, name)()
        except Exception:  # an island kind without props or forest: its objects only
            continue
        rows = got.get("models", []) if isinstance(got, dict) else got
        found += [r.get("cfg") if isinstance(r, dict) else r for r in rows]
    return sorted({c for c in found if isinstance(c, str) and c})


def _draw(editor, cfg: str, report: IslandsReport, depth: int, seen: set[str]) -> None:
    """One config as the view's model library flattens it (``render.modellibrary``): its parts' meshes and maps
    loaded through the editor, its nested configs followed."""
    if cfg in seen:
        return
    seen.add(cfg)
    try:
        spec = editor.model(cfg)
    except Exception as exc:  # the library draws such a config as nothing; listed here
        report.unreadable.append((cfg, f"{type(exc).__name__}: {exc}"))
        return
    for part in spec.get("parts", ()):
        report.parts += 1
        for m in (part.get("materials") or {}).values() or [part]:
            for slot in ("diffuse", "normal"):
                name = m.get(slot) or ""
                if name and name not in seen:
                    seen.add(name)
                    report.textures += 1
                    try:
                        editor.texture_png(name, normal=slot == "normal")
                    except Exception:  # kept by the editor's texture_failures, counted below
                        continue
    if depth < MAX_DEPTH:
        for nested in spec.get("files", ()):
            _draw(editor, nested["cfg"], report, depth + 1, seen)


def check_islands(
    key: str, folder: Path, cache: Path, count: int = 3, progress: Callable[[str], None] | None = None
) -> IslandsReport:
    """Open ``count`` islands of the game (picked evenly), draw what each places the editor's way, count what
    does not draw."""
    report = IslandsReport(key)
    tree = game_tree(key, Path(folder), cache)
    paths = island_paths(key, tree)
    picked = [paths[int(k * len(paths) / count)] for k in range(min(count, len(paths)))]
    for path in picked:
        if progress is not None:
            progress(path)
        try:
            editor = open_island(key, tree, path, cache / "islands")
            cfgs = placed_configs(editor)
        except Exception as exc:  # an island the editor does not open: listed, the files check goes on
            report.unopened.append((path, f"{type(exc).__name__}: {exc}"))
            continue
        report.islands.append(path)
        report.configs.update(cfgs)
        seen: set[str] = set()
        for cfg in cfgs:
            _draw(editor, cfg, report, 0, seen)
        for name, why, owner in editor.file_problems():
            reason = why.replace(name, "<file>")[:160]  # a texture's failure names it: grouped by the rest
            report.failed[reason] += 1
            shown = report.examples.setdefault(reason, [])
            if len(shown) < 4:
                shown.append(f"{owner or path}: {name}: {why}"[:300])
    return report
