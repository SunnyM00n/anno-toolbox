"""What an extension registers at each point of ``features``: the shapes the baseline asks for.

The baseline reads these and nothing else of an extension (docs/editor-spinoff.md, "Extension points"); an edition with
nothing registered has no builds, no extra tabs, checks or tools, and ids it makes itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class BuildRequest:
    """One build of an island into a mod; ``extra`` holds the options a builder reads that others do not."""

    src: Path
    anno117: Path
    out: Path
    island: str
    edits_dir: Path
    mod_id: str
    mod_name: str
    log: Callable[[str], None]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Builder:
    """An island build into a mod (``features.BUILDERS``): the (source game, target game) pairs it takes, the
    folders it looks an island up in, the edit files' default folder, and the build itself."""

    routes: frozenset[tuple[str, str]]
    island_dirs: tuple[str, ...]
    default_edits_dir: Callable[[], Path]
    run: Callable[[BuildRequest], None]


@dataclass(frozen=True)
class IslandTab:
    """A tab of the Islands panel (``features.ISLAND_TABS``): ``make(main, catalogue, routes)`` returns its widget."""

    title: str
    make: Callable[..., Any]


@dataclass(frozen=True)
class Check:
    """A check over the open island (``features.CHECKS``), named by its key in the Checks tab and the 3D view.

    ``run(session, editor)`` returns the result (kept as ``view.checks[key]`` for the overlay), on a background
    thread when ``background``; ``report(result)`` gives the rows of the Checks tab (text, place or None) and the
    message for the status line; ``failure`` begins the message when ``run`` raised.
    """

    run: Callable[[Any, Any], Any]
    report: Callable[[Any], tuple[list[tuple[str, Any]], str]]
    saying: str = ""
    failure: str = "the check failed"
    background: bool = False


@dataclass(frozen=True)
class Tool:
    """A window of its own (``features.TOOLS``): ``open(main)`` returns it, the main window shows and keeps it."""

    open: Callable[[Any], Any]


class Guids(Protocol):
    """Ids for islands saved as new ones (``features.GUIDS``): stable between runs and machines."""

    def get(self, source: str) -> int: ...

    def name_assets(self, xml: str) -> str: ...

    def save(self) -> None: ...


@dataclass(frozen=True)
class ModelImport:
    """Models of another game made placeable (``features.MODEL_IMPORTS``): ``make(tree, mod_root)`` returns an
    importer whose ``convert(source)`` returns the model's path in the mod, FileNotFoundError when it is not whole."""

    make: Callable[[Any, Path], Any]


@dataclass(frozen=True)
class AppSpec:
    """An app of the suite's window (``features.APPS`` for one an extension adds): ``make(shell)`` returns its page,
    made when it is first shown; ``prepare`` runs before the program's first window (the OpenGL format); ``links``:
    per ``suite.links`` name what the app answers, ``handler(page, *args)`` on its page (made when asked)."""

    key: str
    title: str
    icon: str  # a key of ``suite.icons.ICONS``
    summary: str
    make: Callable[[Any], Any]
    prepare: Callable[[], None] | None = None
    links: dict[str, Callable[..., Any]] = field(default_factory=dict)
    quiet_links: frozenset[str] = frozenset()  # queries: answered without the page, which they get only if it is made


@dataclass(frozen=True)
class ForestTable:
    """The numbered trees beside an island a build wrote (``features.FOREST_TABLES``): ``read(files, stem)`` returns
    a table with ``tiles()`` and ``planting``, or None when the island has none."""

    read: Callable[[Any, str], Any]


@dataclass(frozen=True)
class IslandConversion:
    """More targets of the Islands app's "Port to region / game" (``features.ISLAND_CONVERSIONS``): ``targets(game,
    path)`` returns (target id, label) pairs for an island, ``run(request, src, dst, log)`` converts it
    (``editor.islandconvert``'s ``Request``; ``src`` and ``dst`` its ``GameSide`` objects, with their folders) and
    returns its ``Ported``. An island of another engine generation is offered here only: the first extension that
    lists the target takes it. ``sources``: the games whose islands it takes (the command line's choices);
    ``run_many(requests, src, dst, log)``: several islands into one mod at once, a ``Ported`` each."""

    targets: Callable[[str, str], list[tuple[str, str]]]
    run: Callable[[Any, Any, Any, Callable[[str], None]], Any]
    sources: tuple[str, ...] = ()
    run_many: Callable[[list, Any, Any, Callable[[str], None]], list] | None = None


@dataclass(frozen=True)
class SaveEdit:
    """More values the Saves app's Edit tab offers (``features.SAVE_EDITS``) for a save of one of ``games``:
    ``fields(save)`` returns the rows for the opened save (``saveedit.edits.Field``, keys of its own; none when it
    offers nothing), ``apply(save, parsed, changes, settings)`` returns the parsed save (the format module's) with
    the changes of those keys, raising ``ValueError`` for one it refuses; ``settings`` are the suite's."""

    games: frozenset[str]
    fields: Callable[[Any], list]
    apply: Callable[[Any, Any, dict, Any], Any]
