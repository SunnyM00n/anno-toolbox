"""Following the running game's UI scenes (no Qt): which scenes the game has open, as the live link's ``ui.scene``
events say, stacked in the order the game draws them; which values to ask for next without flooding the game; and
the pushes of edited files into the installed mod.

``GameScenes.apply(event)`` keeps the open scenes (``stack`` bottom to top), ``Poller`` picks the next ``ui.values``
request, ``Pushes`` holds the edits that wait for their push, ``installed_mod`` and ``write_into`` are the push's
file side. The window is ``mirror``. docs/ui-editor.md "The game mirror"; the event is docs/live-link.md section 18.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..infra.buildcache import write_atomic

NAME = re.compile(r"[A-Za-z0-9_]{1,63}")  # a state's and a scene file's name, as the event carries them
OPENED, CLOSED = "opened", "closed"
WINDOW = (1920, 1080)  # the size a scene is laid out at until the game names its window's
MAX_PARTS = 8  # the widget files a scene places directly whose values are asked besides the scene's own


@dataclass
class OpenScene:
    """One scene the game has open: its state, its file's name (the editor's key of it), where it sits."""

    state: str
    scene: str
    parent: str = ""
    depth: int = 0
    order: int = 0
    hidden: bool = False  # hidden in the mirror for a look under it; the game is not told


class GameScenes:
    """The scenes the game has open, as its ``ui.scene`` events told them; ``window`` the game window's size."""

    def __init__(self) -> None:
        self.scenes: dict[str, OpenScene] = {}
        self.window: tuple[int, int] = WINDOW
        self.last = ""  # the state opened last (the status line names it)

    def apply(self, event: dict) -> str:
        """Take one event; the state it changed, "" for an event of another shape (dropped, never trusted)."""
        change, state, scene = event.get("change"), event.get("state"), event.get("scene")
        if change not in (OPENED, CLOSED) or not _name(state) or not _name(scene):
            return ""
        size = event.get("window")
        if isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and 0 < v < 1 << 15 for v in size):
            self.window = (size[0], size[1])
        if change == CLOSED:
            return state if self.scenes.pop(state, None) is not None else ""
        order, depth = event.get("order"), event.get("depth")
        hidden = self.scenes[state].hidden if state in self.scenes else False
        parent = event.get("parent") if _name(event.get("parent")) else ""
        self.scenes[state] = OpenScene(state, scene.lower(), parent, _int(depth), _int(order), hidden)
        self.last = state
        return state

    def stack(self) -> list[OpenScene]:
        """The open scenes from the bottom to the top: the one the game opened later is drawn over the earlier."""
        return sorted(self.scenes.values(), key=lambda s: (s.order, s.depth, s.state))

    def shown(self) -> list[OpenScene]:
        return [s for s in self.stack() if not s.hidden]

    def layer(self, state: str) -> int:
        """The scene's layer, 1 the bottom; 0 for a scene that is not open."""
        return next((i + 1 for i, s in enumerate(self.stack()) if s.state == state), 0)

    def hide(self, state: str, hidden: bool) -> None:
        if state in self.scenes:
            self.scenes[state].hidden = hidden

    def clear(self) -> None:
        self.scenes.clear()
        self.last = ""


def _name(value: object) -> bool:
    return isinstance(value, str) and NAME.fullmatch(value) is not None


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass(frozen=True)
class Part:
    """What one ``ui.values`` request asks: a scene's own widgets (``file`` "") or a widget file it places."""

    state: str
    file: str = ""


@dataclass
class _Due:
    at: float = 0.0
    every: float = 0.0


@dataclass
class Poller:
    """Which values to ask next: one request at a time, ``GAP`` seconds at least between two (four a second in all),
    a part asked every ``FAST`` seconds while its values change and slower up to ``SLOW`` while they do not."""

    GAP = 0.25
    FAST = 0.5
    SLOW = 1.0
    due: dict[Part, _Due] = field(default_factory=dict)
    waiting: Part | None = None
    last: float = -1e9

    def follow(self, parts: list[Part]) -> None:
        """The parts to ask from now on (the shown scenes'); a new one is asked first, a gone one no more."""
        self.due = {p: self.due.get(p) or _Due() for p in parts}

    def next(self, now: float) -> Part | None:
        """The part to ask now, None while one waits, too soon after the last, or none is due."""
        if self.waiting is not None or now - self.last < self.GAP or not self.due:
            return None
        part, due = min(self.due.items(), key=lambda kv: kv[1].at)
        if due.at > now:
            return None
        self.waiting, self.last = part, now
        return part

    def answered(self, part: Part, now: float, changed: bool) -> None:
        """The answer came: a part whose values changed is asked again soon, one that stays the same slower."""
        if self.waiting == part:
            self.waiting = None
        due = self.due.get(part)
        if due is not None:
            due.every = self.FAST if changed or not due.every else min(self.SLOW, due.every * 1.5)
            due.at = now + due.every

    def refresh(self, state: str) -> None:
        """Ask the parts of a scene again at once (the game reloaded it)."""
        for part, due in self.due.items():
            if part.state == state:
                due.at, due.every = 0.0, self.FAST


@dataclass
class Pushes:
    """Edited files that wait for their push: each is pushed once no edit came for ``DELAY`` seconds."""

    DELAY = 1.5
    edited: dict[str, float] = field(default_factory=dict)  # file key -> the time of its last edit

    def edit(self, key: str, now: float) -> None:
        self.edited[key] = now

    def due(self, now: float, at_once: bool = False) -> list[str]:
        """The files to push now (all with ``at_once``: Push now); they leave the queue."""
        ready = [k for k, t in self.edited.items() if at_once or now - t >= self.DELAY]
        for k in ready:
            del self.edited[k]
        return ready


def parts_of(state: str, root: dict | None, templates) -> list[Part]:
    """What is asked of one open scene: its own widgets, then the widget files its elements place (the first
    ``MAX_PARTS``), whose widgets the game answers per file. ``templates(root)``: the files the scene places."""
    found = list(dict.fromkeys(t for t in (templates(root) if root is not None else []) if t))
    return [Part(state), *(Part(state, t) for t in found[:MAX_PARTS])]


def installed_mod(mods: Path | None, mod_id: str, read_id) -> Path | None:
    """The installed copy of the project's mod: ``<mods>/<mod id>`` when it holds that mod (``read_id(folder)`` names
    the mod a folder holds); None when there is none, so nothing is written."""
    if mods is None or not mod_id:
        return None
    folder = Path(mods) / mod_id
    return folder if folder.is_dir() and read_id(folder) == mod_id else None


def write_into(folder: Path, game_path: str, data: bytes) -> Path:
    """A UI file written at its game path inside the mod's folder, whole or not at all; never outside it."""
    rel = PurePosixPath(game_path.replace("\\", "/"))
    # a part with a drive letter would take the folder's place when joined (``D:/mods/m`` / ``C:x`` is ``C:x``)
    if rel.is_absolute() or not rel.parts or any(part == ".." or ":" in part for part in rel.parts):
        raise ValueError(f"not a path inside the mod: {game_path!r}")
    dest = Path(folder) / Path(*rel.parts)
    dest.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(dest, data)
    return dest
