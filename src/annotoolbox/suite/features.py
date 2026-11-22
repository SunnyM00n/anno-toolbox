"""The editor's optional parts: the one place that asks whether a module is there.

This edition carries no extensions, so no extension point (``extensions``) has anything registered and
the baseline hides what needs one.
"""

from __future__ import annotations

import importlib
import importlib.util

from ..infra.i18n import N_

ROOT = __package__.rsplit(".", 1)[0]

# The extension points (the shapes registered at each are in ``extspecs``; docs/editor-spinoff.md, "Extension points").
BUILDERS = "island_builders"  # an island build into a mod for another game: Rebuild, the From-other-games list
ISLAND_TABS = "island_tabs"  # a tab of the Islands panel beyond one per game
CHECKS = "island_checks"  # a check over the open island, with its result list and overlay
TOOLS = "tools"  # a window of its own (Tools menu, ribbon item)
GUIDS = "island_guids"  # stable ids for islands saved as new ones
MODEL_IMPORTS = "model_imports"  # models of another game made placeable on an island
FOREST_TABLES = "forest_tables"  # the numbered trees stored beside an island a build wrote
APPS = "suite_apps"  # an app of the suite's window beyond the built-in ones (``extspecs.SAppSpec``)
ISLAND_CONVERSIONS = "island_ports"  # targets of "Port to region / game" beyond the generation
SAVE_EDITS = "save_edits"  # more values of a save the Saves app's Edit tab offers (``extspecs.SSaveEdit``)
CONVERSIONS = "conversions"  # more entries of the Converter app beyond its own (``extspecs.SConversion``)
MODEL_OPTIONS = "model_options"  # the --reskin option of the model commands (``extspecs.SModelOptions``)
CONVERTER_PAGES = "converter_pages"  # a page of the Converter app beside its entries (``extspecs.SConverterPage``)

UI_COMPARE = (
    "ui_compare"  # a panel of the UI editor that holds other values against the preview (``extspecs.SUiCompare``)
)
LINK_KINDS = "link_kinds"  # more kinds a game's welcome may list for the live link (a name each)
DEBUG_COMMANDS = "debug_commands"  # more commands of the debug palette for a game (``extspecs.SDebugCommands``)

EXTENSIONS = ""

NOT_IN_EDITION = N_("common.not_available_edition")  # shown through tr()

_g_cache: dict[str, object | None] = {}
_g_points: dict[str, dict[str, object]] = {}
_g_loaded: list[bool] = []


def has(name: str) -> bool:
    """Whether the module ``name`` (relative to the package) can be imported here."""
    if not name:
        return False
    if name not in _g_cache:
        try:
            _g_cache[name] = importlib.util.find_spec(f"{ROOT}.{name}") is not None
        except (ImportError, ValueError):
            _g_cache[name] = False
    return bool(_g_cache[name])


def module(name: str):
    """The module ``name`` (relative to the package), or None when this edition does not carry it."""
    if not has(name):
        return None
    try:
        return importlib.import_module(f"{ROOT}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name == ROOT or exc.name.startswith(ROOT + ".")):
            _g_cache[name] = False
            return None
        raise


# A feature is a baseline that asks for what the full source adds (CONTRIBUTING, "Public editions"); the full
# source registers it from ``EXTENSIONS``, a package the public edition does not carry.


def register(point: str, name: str, value: object) -> None:
    """Add ``value`` under ``name`` to the extension point ``point`` (called from the ``EXTENSIONS`` package)."""
    _g_points.setdefault(point, {})[name] = value


def extension(point: str, name: str) -> object | None:
    """What is registered at ``point`` under ``name``, or None."""
    return extensions(point).get(name)


def extensions(point: str) -> dict[str, object]:
    """What is registered at ``point``, by name: empty in an edition without extensions. The first call loads
    the ``EXTENSIONS`` package, whose ``register()`` fills the points."""
    if not _g_loaded:
        _g_loaded.append(True)
        found = module(EXTENSIONS)
        if found is not None:
            found.register()
    return dict(_g_points.get(point, {}))
