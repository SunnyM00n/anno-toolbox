"""Finding the apps other people add to the Toolbox, and making them the suite's apps without letting one hurt it.

Three sources: entry points of the group ``appapi.ENTRY_POINT_GROUP``, the apps folder's subfolders (``apps_dir``:
a manifest and Python code, for the installed program) and its manifests of standalone programs (``appprograms``).
``discover`` returns a ``SFound`` per candidate, failed or refused ones with their reason; ``specs`` and ``parts``
make the switcher's entries and the project's parts. A disabled app's code never runs. docs/app-api.md.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import re
import sys
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..paths import config_dir
from . import appapi, appprograms
from . import project as projects
from .extspecs import SAppSpec
from .gamesync import SAppGame

APPS_ENV = "ANNO_TOOLBOX_APPS_DIR"  # another apps folder (a test, a portable copy)
APPS_FOLDER = "apps"  # in the config folder
MANIFEST = "app.json"
DISABLED_FILE = "disabled.json"  # the ids the person switched off, a JSON list
DEFAULT_FUNCTION = "register"  # what a manifest's ``entry`` calls when it names only a file
OK, FAILED, REFUSED, DISABLED = "ok", "failed", "refused", "disabled"
PYTHON, PROGRAM = "python", "program"


@dataclass
class SFound:
    """One candidate app. ``id``: ``entry:<name>``, ``folder:<name>`` or ``file:<name>`` (what ``disabled.json``
    holds); ``status``: ``OK``, ``FAILED`` (``error`` says why: code that raised, a bad manifest, a key in use),
    ``REFUSED`` (the API version, ``error`` says which) or ``DISABLED``."""

    id: str
    key: str
    title: str
    kind: str
    status: str
    source: str
    error: str = ""
    app: appapi.SExternalApp | None = None
    program: appprograms.SProgram | None = None


_g_found: list[SFound] | None = None


def apps_dir() -> Path:
    """The apps folder: ``$ANNO_TOOLBOX_APPS_DIR``, else ``apps`` in the config folder."""
    return Path(os.environ[APPS_ENV]) if os.environ.get(APPS_ENV) else config_dir() / APPS_FOLDER


def disabled(folder: Path | None = None) -> set[str]:
    """The ids switched off (empty when the file is missing or does not read)."""
    path = (folder or apps_dir()) / DISABLED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return set()
    return {entry for entry in data if isinstance(entry, str)} if isinstance(data, list) else set()


def set_disabled(app_id: str, is_off: bool, folder: Path | None = None) -> None:
    """Switch an app off or on; it takes effect at the next start."""
    folder = folder or apps_dir()
    ids = disabled(folder)
    ids = ids | {app_id} if is_off else ids - {app_id}
    folder.mkdir(parents=True, exist_ok=True)
    write_atomic(folder / DISABLED_FILE, json.dumps(sorted(ids), indent=1).encode("utf-8"))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")


def _failed(app_id: str, name: str, kind: str, source: str, exc: BaseException | str, status: str = FAILED) -> SFound:
    reason = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return SFound(app_id, slug(name) or "app", name, kind, status, source, reason)


def _declared(raw: object) -> appapi.SExternalApp:
    """The ``SExternalApp`` an entry point or a manifest's function returned (the object, or a dict of its fields)."""
    if callable(raw) and not isinstance(raw, appapi.SExternalApp):
        raw = raw()
    if isinstance(raw, dict):
        raw = appapi.SExternalApp(**raw)
    if not isinstance(raw, appapi.SExternalApp):
        raise TypeError(tr("appapi.returned_wrong", got=type(raw).__name__))
    return raw


def _checked(app_id: str, name: str, source: str, raw: object) -> SFound:
    """A python app's ``SFound`` from what its code returned: refused for another API, failed for a bad declaration."""
    try:
        app = _declared(raw)
    except (Exception, SystemExit) as exc:  # the app's own code ran here (a function it registered)
        return _failed(app_id, name, PYTHON, source, exc)
    reason = appapi.refusal(app.api_version)
    if reason:
        return SFound(app_id, app.key, app.title or name, PYTHON, REFUSED, source, reason, app)
    if not appapi.KEY_PATTERN.fullmatch(app.key or ""):
        return _failed(app_id, name, PYTHON, source, tr("appapi.bad_key", key=app.key))
    if not callable(app.make) or not isinstance(app.title, str) or not app.title:
        return _failed(app_id, name, PYTHON, source, tr("appapi.bad_declaration"))
    return SFound(app_id, app.key, app.title, PYTHON, OK, source, "", app)


def from_entry_points(entry_points: Collection[Any], disabled_ids: set[str]) -> list[SFound]:
    """The apps of the installed packages' entry points; each is loaded alone, so one that raises is only itself
    failed."""
    out = []
    for entry_point in entry_points:
        app_id, source = f"entry:{entry_point.name}", tr("appapi.source_entry", name=entry_point.name)
        if app_id in disabled_ids:
            out.append(SFound(app_id, slug(entry_point.name), entry_point.name, PYTHON, DISABLED, source))
            continue
        try:
            raw = entry_point.load()
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            out.append(_failed(app_id, entry_point.name, PYTHON, source, exc))
            continue
        out.append(_checked(app_id, entry_point.name, source, raw))
    return out


def _load_entry(folder: Path, entry: str, name: str) -> object:
    """The object a manifest's ``entry`` (``file.py`` or ``file.py:function``) names: the file is run as a module of
    its own with its folder on ``sys.path`` (its own imports), and the function (``register`` unless named) called."""
    file, _, function = entry.partition(":")
    path = (folder / file).resolve()
    if folder.resolve() not in path.parents or path.suffix != ".py" or not path.is_file():
        raise ValueError(tr("appapi.entry_not_found", entry=entry))
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    module_name = f"anno_toolbox_app_{slug(name).replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(tr("appapi.entry_not_found", entry=entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return getattr(module, function or DEFAULT_FUNCTION)


def _manifest(path: Path, folder: Path, name: str, app_id: str, disabled_ids: set[str]) -> SFound:
    source = str(path)
    if app_id in disabled_ids:
        return SFound(app_id, slug(name), name, PYTHON, DISABLED, source)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError(tr("appapi.manifest_not_object"))
    except (OSError, ValueError) as exc:
        return _failed(app_id, name, PYTHON, source, exc)
    shown = str(data.get("name") or data.get("title") or name)
    if reason := appapi.refusal(data.get("api_version")):  # before any of its code runs
        return SFound(app_id, slug(str(data.get("key") or name)), shown, PYTHON, REFUSED, source, reason)
    if data.get("entry"):
        try:
            raw = _load_entry(folder, str(data["entry"]), name)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            return _failed(app_id, shown, PYTHON, source, exc)
        return _checked(app_id, shown, source, raw)
    try:
        program = appprograms.from_manifest(data, folder, slug(name))
    except ValueError as exc:
        return _failed(app_id, shown, PROGRAM, source, exc)
    return SFound(app_id, program.key, program.title, PROGRAM, OK, source, "", None, program)


def from_folder(folder: Path, disabled_ids: set[str]) -> list[SFound]:
    """The apps in the apps folder: a subfolder with an ``app.json`` (code or a program), a ``*.json`` of a program."""
    out = []
    if not folder.is_dir():
        return out
    for entry in sorted(folder.iterdir()):
        if entry.is_dir() and (entry / MANIFEST).is_file():
            out.append(_manifest(entry / MANIFEST, entry, entry.name, f"folder:{entry.name}", disabled_ids))
        elif entry.is_file() and entry.suffix == ".json" and entry.name != DISABLED_FILE:
            out.append(_manifest(entry, folder, entry.stem, f"file:{entry.stem}", disabled_ids))
    return out


def discover(
    taken: Collection[str] = (),
    folder: Path | None = None,
    entry_points: Callable[[], Collection[Any]] | None = None,
) -> list[SFound]:
    """Every candidate app, the failed ones included. ``taken``: the keys of the built-in apps (an external app may
    not use one; a second app with a key is failed)."""
    folder = folder or apps_dir()
    disabled_ids = disabled(folder)
    points = entry_points() if entry_points else importlib.metadata.entry_points(group=appapi.ENTRY_POINT_GROUP)
    found = [*from_entry_points(list(points), disabled_ids), *from_folder(folder, disabled_ids)]
    used = set(taken)
    for item in found:
        if item.status == DISABLED:
            continue
        if item.key in used:
            item.status, item.error = FAILED, tr("appapi.key_in_use", key=item.key)
            item.app = item.program = None
        used.add(item.key)
    return found


def loaded(taken: Collection[str] = ()) -> list[SFound]:
    """``discover`` once per run (the apps' code is imported once); ``reset`` forgets it."""
    global _g_found
    if _g_found is None:
        _g_found = discover(taken)
    return _g_found


def reset() -> None:
    global _g_found
    _g_found = None


def _refuse(error: str):
    def make(_shell):
        raise RuntimeError(error)  # the shell shows the reason on the app's page; the other apps stay usable

    return make


def _game_side(app: appapi.SExternalApp) -> SAppGame | None:
    """The app's side of the suite's one game selection: its page's ``game_key()`` and ``open_game(key)``."""
    if not app.games:
        return None
    return SAppGame(
        app.key,
        lambda: app.games,
        lambda page: page.game_key() if hasattr(page, "game_key") else "",
        lambda page, key: page.open_game(key) if hasattr(page, "open_game") else None,
    )


def spec_of(found: SFound) -> SAppSpec:
    """The switcher's entry for a candidate: the working app, or a failed or refused one that shows its reason on its
    page."""
    if found.status == OK and found.app is not None:
        app = found.app

        def make(shell):
            return app.make(appapi.CAppContext(app.key, shell.m_settings, shell.m_settings_path, shell))

        return SAppSpec(
            app.key, app.title, app.icon or appapi.DEFAULT_ICON, app.summary, make, links=dict(app.links),
            game=_game_side(app), window=app.window,
        )  # fmt: skip
    if found.status == OK and found.program is not None:
        program = found.program
        return SAppSpec(program.key, program.title, program.icon, program.summary, appprograms.launcher_maker(program))
    return SAppSpec(found.key, found.title, appapi.DEFAULT_ICON, found.error, _refuse(found.error))


def specs(found: list[SFound]) -> list[SAppSpec]:
    """The switcher's entries: every working app and every failed or refused one (a disabled one is not listed)."""
    return [spec_of(candidate) for candidate in found if candidate.status != DISABLED]


def _part_of(app: appapi.SExternalApp) -> projects.SPart:
    part = app.part
    assert part is not None

    def render(project, settings, log):
        from .settings import settings_file

        try:
            files = part.render(project, appapi.CAppContext(app.key, settings, settings_file()), log)
        except Exception as exc:
            raise RuntimeError(tr("appapi.part_failed", title=app.title, error=f"{type(exc).__name__}: {exc}")) from exc
        for path in files:
            pieces = path.replace("\\", "/").split("/") if isinstance(path, str) else [""]
            if not pieces[0] or any(piece in ("", ".", "..") or ":" in piece for piece in pieces):
                raise RuntimeError(tr("appapi.part_bad_path", title=app.title, path=repr(path)))
        return files

    def has_work(project) -> bool:
        try:
            return bool(part.has_work(project))
        except Exception as exc:  # the app's own code; the project panel and the build catch a ValueError
            raise ValueError(tr("appapi.part_failed", title=app.title, error=f"{type(exc).__name__}: {exc}")) from exc

    return projects.SPart(app.key, app.title, has_work, render)


def parts(found: list[SFound]) -> list[projects.SPart]:
    """The project parts of the working apps that declare one, after the built-in parts."""
    return [
        _part_of(candidate.app)
        for candidate in found
        if candidate.status == OK and candidate.app is not None and candidate.app.part is not None
    ]


def programs(found: list[SFound]) -> list[appprograms.SProgram]:
    return [candidate.program for candidate in found if candidate.status == OK and candidate.program is not None]
