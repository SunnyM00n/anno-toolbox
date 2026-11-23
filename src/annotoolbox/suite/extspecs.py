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
class SBuildRequest:
    """One build of an island into a mod; ``extra`` holds the options a builder reads that others do not."""

    source_folder: Path
    anno117: Path
    out: Path
    island: str
    edits_dir: Path
    mod_id: str
    mod_name: str
    log: Callable[[str], None]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SBuilder:
    """An island build into a mod (``features.BUILDERS``): the (source game, target game) pairs it takes, the
    folders it looks an island up in, the edit files' default folder, and the build itself."""

    routes: frozenset[tuple[str, str]]
    island_dirs: tuple[str, ...]
    default_edits_dir: Callable[[], Path]
    run: Callable[[SBuildRequest], None]


@dataclass(frozen=True)
class SIslandTab:
    """A tab of the Islands panel (``features.ISLAND_TABS``): ``make(main, catalogue, routes)`` returns its widget."""

    title: str
    make: Callable[..., Any]


@dataclass(frozen=True)
class SCheck:
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
class STool:
    """A window of its own (``features.TOOLS``): ``open(main)`` returns it, the main window shows and keeps it."""

    open: Callable[[Any], Any]


class IGuids(Protocol):
    """Ids for islands saved as new ones (``features.GUIDS``): stable between runs and machines."""

    def get(self, source: str) -> int: ...

    def name_assets(self, xml: str) -> str: ...

    def save(self) -> None: ...


@dataclass(frozen=True)
class SModelImport:
    """Models of another game made placeable (``features.MODEL_IMPORTS``): ``make(tree, mod_root)`` returns an
    importer whose ``convert(source)`` returns the model's path in the mod, FileNotFoundError when it is not whole."""

    make: Callable[[Any, Path], Any]


@dataclass(frozen=True)
class SSettingsRows:
    """What an app adds to the settings window's page of its own (``SAppSpec.settings``): ``choices`` are drop-downs
    (settings key, label id, {value: shown text}), ``switches`` the dict-valued settings shown as tick boxes
    (settings key, {switch: (label id, hint id)}); a switch the dict does not hold is on."""

    choices: tuple = ()
    switches: tuple = ()


@dataclass(frozen=True)
class SAppSpec:
    """An app of the suite's window (``features.APPS`` for one an extension adds): ``make(shell)`` returns its page,
    made when it is first shown; ``prepare`` runs before the program's first window (the OpenGL format); ``links``:
    per ``suite.links`` name what the app answers, ``handler(page, *args)`` on its page (made when asked); ``game``:
    how it follows the suite's game selection (a ``suite.gamesync.SAppGame``), None for an app that has no game."""

    key: str
    title: str
    icon: str  # a key of ``suite.icons.ICONS``
    summary: str
    make: Callable[[Any], Any]
    prepare: Callable[[], None] | None = None
    links: dict[str, Callable[..., Any]] = field(default_factory=dict)
    quiet_links: frozenset[str] = frozenset()  # queries: answered without the page, which they get only if it is made
    game: Any = None
    window: bool = False  # ``make`` returns a window of its own the switcher opens, not a page of the stack
    settings: Callable[[], SSettingsRows] | None = None  # its page of the settings window (its modules load then)


@dataclass(frozen=True)
class SUiCompare:
    """A panel of the UI editor that holds a second set of values against the preview (``features.UI_COMPARE``):
    ``title`` is its dock's, ``make_panel()`` its widget (the window keeps it as ``compare_panel``),
    ``add_file_actions(window, menu)`` and ``add_view_action(window, menu, app)`` fill the File and View menus (the
    second returns the checkable action ``window.live_action``), ``texts(window)`` the label texts it holds by key
    for the preview, ``show(window)`` draws its overlay and rows after the preview was laid out."""

    title: str
    make_panel: Callable[[], Any]
    add_file_actions: Callable[[Any, Any], None]
    add_view_action: Callable[[Any, Any, str], Any]
    texts: Callable[[Any], dict[str, str]]
    show: Callable[[Any], None]


@dataclass(frozen=True)
class SForestTable:
    """The numbered trees beside an island a build wrote (``features.FOREST_TABLES``): ``read(files, stem)`` returns
    a table with ``tiles()`` and ``planting``, or None when the island has none."""

    read: Callable[[Any, str], Any]


@dataclass(frozen=True)
class SIslandConversion:
    """More targets of the Islands app's "Port to region / game" (``features.ISLAND_CONVERSIONS``): ``targets(game,
    path)`` returns (target id, label) pairs for an island, ``run(request, source_side, target_side, log)`` converts
    it (``editor.islandconvert``'s ``Request``; ``source_side`` and ``target_side`` its ``CGameSide`` objects, with
    their folders) and returns its ``Ported``. An island of another engine generation is offered here only: the first extension that
    lists the target takes it. ``sources``: the games whose islands it takes (the command line's choices);
    ``run_many(requests, source_side, target_side, log)``: several islands into one mod at once, a ``Ported`` each."""

    targets: Callable[[str, str], list[tuple[str, str]]]
    run: Callable[[Any, Any, Any, Callable[[str], None]], Any]
    sources: tuple[str, ...] = ()
    run_many: Callable[[list, Any, Any, Callable[[str], None]], list] | None = None


@dataclass(frozen=True)
class SSaveEdit:
    """More values the Saves app's Edit tab offers (``features.SAVE_EDITS``) for a save of one of ``games``:
    ``fields(save)`` returns the rows for the opened save (``saveedit.edits.SField``, keys of its own; none when it
    offers nothing), ``apply(save, parsed, changes, settings)`` returns the parsed save (the format module's) with
    the changes of those keys, raising ``ValueError`` for one it refuses; ``settings`` are the suite's."""

    games: frozenset[str]
    fields: Callable[[Any], list]
    apply: Callable[[Any, Any, dict, Any], Any]


@dataclass(frozen=True)
class SModelOptions:
    """An option of the model commands (``rdm relayout``, ``rdm from-granny``) an extension adds (``features.MODEL_OPTIONS``):
    ``add_options(parser)`` adds the flags, ``start(args)`` opens what the models are measured against and
    returns it (None: the option is off), ``configs(handle, name, data, target)`` gives the extra files of one model
    written and the notes lines, ``model_cfg(raw_mesh, layout, mesh, look, levels)`` 117's config for a static mesh."""

    add_options: Callable
    start: Callable
    configs: Callable
    model_cfg: Callable


@dataclass(frozen=True)
class SConversion:
    """An entry of the Converter app (``features.CONVERSIONS`` for one an extension adds): a command of the command
    line (``command``) run on single files, assets or islands, its form made from the command's own options.
    ``register(sub)`` adds the command's group to argparse sub-parsers, ``words`` walks from there to the command;
    ``inputs`` and ``output`` name its source and target arguments. The fields one by one: docs/converter-app.md,
    "An entry"."""

    key: str
    category: str  # a key of ``converterapp.catalogue.CATEGORIES``
    title: str
    summary: str
    command: tuple[str, ...]
    register: Callable[[Any], None]
    words: tuple[str, ...]
    inputs: str = "inputs"
    source: str = "files"
    output: str = "out"
    output_file: str = ""
    output_subfolder: bool = False
    suffixes: tuple[str, ...] = ()
    games: tuple[str, ...] = ()  # the games whose files it takes; empty: any
    fixed: tuple[str, ...] = ()
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    folders: dict[str, str] = field(default_factory=dict)
    hidden: frozenset[str] = frozenset()
    per_input: bool = True
    candidates: Callable[[Any], list[tuple[str, str]]] | None = None
    complete: Callable[[Any, dict, str], dict] | None = None
    prepare: Callable[[Any], None] | None = None


@dataclass(frozen=True)
class SConverterPage:
    """A page of the Converter app beside its entries (``features.CONVERTER_PAGES``): ``make(shell)`` returns its
    widget, made when the app's page is; ``title`` is its tab's."""

    title: str
    make: Callable[[Any], Any]


@dataclass(frozen=True)
class SDebugCommands:
    """More commands of the debug palette (``features.DEBUG_COMMANDS``): for ``game``, its categories in order as
    (key, title id) and its ``debugcommands.SCommand`` rows; an id or a category the game's own catalogue has wins."""

    game: str
    categories: tuple[tuple[str, str], ...]
    commands: tuple[Any, ...]
