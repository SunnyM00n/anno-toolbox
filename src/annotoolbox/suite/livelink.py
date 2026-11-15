"""The live link's client interface (no transport): typed requests, events, the registry of the one link.

``LiveLink`` has a method per request (``files_changed``, ``reload``, ``select``, ``jump``, ``read``, ``subscribe``)
and an ``on_*`` per event; every answer is a ``Reply``. Not connected, it answers ``NOT_CONNECTED`` with the manual
route as the message. The transport is the extension point ``register_connector``; ``current``/``use`` are the
registry. docs/live-link.md, "Toolbox side".
"""

from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ..infra.i18n import N_, tr

log = logging.getLogger(__name__)

# What can be reloaded (``reload.<kind>``; docs/live-link.md section 4).
ASSETS, TEXTS, SCENES, AMBIENT = "assets", "texts", "scenes", "ambient"
MODELS, EFFECTS, BANKS, SESSION = "models", "effects", "banks", "session"
KINDS = (ASSETS, TEXTS, SCENES, AMBIENT, MODELS, EFFECTS, BANKS, SESSION)
DEBUG = "debug"  # not a reload: a game that takes the debug commands says so in its welcome (live_link_commands=1)
CAPABILITIES = (*KINDS, DEBUG)

# The events of the game and the topics ``subscribe`` takes for them.
SELECTION, EDIT_REQUEST, RELOAD, SESSION_EVENT, LOG = "selection", "edit.request", "reload", "session", "log"
STALLED = "stalled"  # the game drew no UI frame for a long while after an asset request (always sent, no topic)
EVENTS = (SELECTION, EDIT_REQUEST, RELOAD, SESSION_EVENT, LOG, STALLED)

# The answers (``Reply.status``).
OK, DONE, SKIPPED, BUSY, UNAVAILABLE, REFUSED, NOT_CONNECTED = (
    "ok", "done", "skipped", "busy", "unavailable", "refused", "not connected",
)  # fmt: skip

# What a modder does without the link, per kind (docs/live-link.md sections 1 and 6).
MANUAL = {
    ASSETS: N_("press Shift+F3 in the game (with the hot reload mod enabled)"),
    TEXTS: N_("press Shift+F3 in the game (with the hot reload mod enabled)"),
    SCENES: N_("restart the game"),
    AMBIENT: N_("restart the game"),
    MODELS: N_("restart the game, or switch on live_reload in the helper library and press Ctrl+Shift+R"),
    EFFECTS: N_("restart the game, or switch on live_reload in the helper library and press Ctrl+Shift+R"),
    BANKS: N_("restart the game"),
    SESSION: N_("save the game and load it again"),
}
# What an asset reload cannot do and what makes it safer (docs/live-link.md section 5, "Asset reloads").
STALE_NOTE = N_("objects already placed keep the data from before the reload: save and load the game for a clean state")
PAUSE_NOTE = N_(
    "in a session the game is paused for the reload and set going again; outside a session, pause it by hand: the mod"
    " pass rebuilds the mod file map while the game streams"
)
DEBUG_OFF = N_("this game takes no debug commands: set live_link_commands=1 in the helper library's settings")
TEXTURE_NOTE = N_(
    "a changed texture shows after Shift+F3 once something that uses it is placed again; a changed file at a path"
    " the game already loaded needs a restart"
)

_SAFE = re.compile(r"[A-Za-z0-9_./-]+")


@dataclass(frozen=True)
class Reply:
    """The answer to one request: ``status`` (one of the constants above), a sentence for the modder, the data."""

    status: str
    message: str = ""
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (OK, DONE)

    @property
    def sent(self) -> bool:
        """Whether the request reached a game (a refusal before sending and a missing link did not)."""
        return self.status not in (NOT_CONNECTED, REFUSED)


@dataclass(frozen=True)
class Sent:
    """One request as it goes (or would go) to the game: the protocol's name and its arguments."""

    name: str
    args: dict


@dataclass(frozen=True)
class LinkStatus:
    """What the suite's window shows: whether a game is connected, which one, its build and the reload kinds it takes."""

    connected: bool = False
    game: str = ""
    build: str = ""
    kinds: frozenset[str] = frozenset()
    note: str = ""  # a warning from the game (it seems to hang), shown after the connection until the next answer

    @property
    def text(self) -> str:
        if not self.connected:
            return tr("game link: not connected")
        text = tr("game link: {game} {build}", game=self.game, build=self.build).rstrip()
        return f"{text} - {self.note}" if self.note else text


def check_paths(paths: Iterable[str]) -> str:
    """ "" when every path is relative to a mod folder and made of safe characters, else why not (the link's own rule
    before anything is sent: docs/live-link.md section 5)."""
    for path in paths:
        if not _SAFE.fullmatch(path) or path.startswith(("/", "./")) or ".." in path.split("/") or ":" in path:
            return tr("not a path inside the mod's folder: {path}", path=repr(path))
    return ""


class LiveLink:
    """The interface. A transport subclasses it and sets ``_transmit`` (and ``set_status``); everything else is here.

    Methods are safe to call from any thread; events arrive on the transport's thread, so a window marshals them
    (a Qt signal) before it touches widgets.
    """

    def __init__(self) -> None:
        self._status = LinkStatus()
        self._handlers: dict[str, list[Callable]] = {name: [] for name in EVENTS}
        self._status_handlers: list[Callable[[LinkStatus], None]] = []
        self.sent: deque[Sent] = deque(maxlen=500)  # what was sent, or would have been (not connected)

    # status and capabilities
    def status(self) -> LinkStatus:
        return self._status

    @property
    def connected(self) -> bool:
        return self._status.connected

    def supports(self, kind: str) -> bool:
        """Whether the connected game takes ``reload(kind)``; False when not connected."""
        return self._status.connected and kind in self._status.kinds

    def capabilities(self) -> frozenset[str]:
        return self._status.kinds if self._status.connected else frozenset()

    def set_status(self, status: LinkStatus) -> None:
        """For a transport: the connection changed (connected, lost, a new game build)."""
        self._status = status
        for handler in list(self._status_handlers):
            handler(status)

    def on_status(self, handler: Callable[[LinkStatus], None]) -> Callable[[], None]:
        return _add(self._status_handlers, handler)

    # requests (docs/live-link.md section 4)
    def files_changed(self, paths: Iterable[str], mod: str = "") -> Reply:
        """The mod's files at ``paths`` (relative to its folder) were written; the answer says what each needs."""
        paths = list(paths)
        return self._request("files.changed", {"mod": mod, "paths": paths}, paths)

    def reload(self, kind: str, paths: Iterable[str] = (), mod: str = "", **args) -> Reply:
        """Ask for the reload ``kind`` (``KINDS``); ``paths`` for models, effects and scenes, ``banks`` and ``mode``
        (``full`` or ``rebuild``, for assets) in ``args``."""
        if kind not in KINDS:
            raise ValueError(f"unknown reload kind {kind!r}")
        paths = list(paths)
        if kind == BANKS and paths and "banks" not in args:  # a bank is named, not placed: its file's name is its name
            names = sorted({PurePosixPath(p).stem for p in paths if p.lower().endswith(".bnk")})
            if not names:
                return Reply(REFUSED, tr("a sound file reloads with its bank: name the bank's .bnk file"))
            args = {**args, "banks": names}
            paths = []
            body = {**args, **({"mod": mod} if mod else {})}
        else:
            body = {**args, **({"mod": mod, "paths": paths} if paths else {})}
        return self._request(f"reload.{kind}", body, paths, kind)

    def select(self, guid: int | None = None, object: int | None = None) -> Reply:
        """Select an object of the game: ``object`` an object id, or ``guid`` the first (next) object of an asset."""
        return self._request("select", _given(guid=guid, object=object))

    def jump(self, object: int | None = None, session: str = "", position: tuple | None = None) -> Reply:
        """Move the camera to an object, or to a ``position`` in a ``session``."""
        where = list(position) if position is not None else None  # a list is what the wire carries
        return self._request("jump", _given(object=object, session=session or None, position=where))

    def read(self, guid: int | None = None, fields: Iterable[str] = (), object: int | None = None) -> Reply:
        """The values the game holds now: ``fields`` (value paths such as ``Building/BuildCost``) of an asset
        ``guid``, or of an ``object``; they come back in ``Reply.data``."""
        fields = list(fields)
        return self._request("read", _given(guid=guid, object=object, fields=fields), fields)

    def debug_list(self) -> Reply:
        """Which debug commands the game binds right now (``data["commands"]``: id to ``available`` and ``state``) and
        the cheats it registers itself (``data["cheats"]``). docs/debug-palette.md."""
        return self._debug("debug.list", {})

    def debug_run(self, command: str, values: dict | None = None) -> Reply:
        """Run a command of the catalogue (``debugcommands``) with typed ``values``; a command or value the catalogue does
        not know is refused here and never sent. A toggle's new state comes back in ``data["state"]``."""
        from . import debugcommands

        found = debugcommands.commands(self.status().game or debugcommands.GAME).get(command)
        if found is None:
            return Reply(REFUSED, tr("{command} is not a debug command of the catalogue", command=command))
        typed, why = debugcommands.check_values(found, dict(values or {}))
        if why:
            return Reply(REFUSED, why)
        return self._debug("debug.run", _given(command=command, values=typed or None))

    def debug_cheat(self, name: str) -> Reply:
        """Trigger one of the cheats the game registers itself; the game checks the name against its own list."""
        return self._debug("debug.cheat", {"name": name})

    def modules_list(self) -> Reply:
        """The game side's modules (``data["modules"]``: each with ``name``, ``class`` - game, dev or experimental -,
        ``on``, ``switchable`` and ``why`` not); behind the debug commands' switch. docs/live-link.md section 14."""
        return self._debug("modules.list", {})

    def modules_set(self, module: str, on: bool) -> Reply:
        """Switch one module while the game runs; the game refuses one that cannot be switched, and says why."""
        return self._debug("modules.set", {"module": module, "on": bool(on)})

    def _debug(self, name: str, args: dict) -> Reply:
        if self.connected and not self.supports(DEBUG):
            return Reply(UNAVAILABLE, tr(DEBUG_OFF))
        return self._request(name, args)

    def ui_dump(self, states: Iterable[str] = ()) -> Reply:
        """The game writes the live widget tree of the open scenes of the UI ``states`` (none: every open scene) to
        its ``log/ui-dump`` folder; the file names come back in ``Reply.data["files"]``."""
        states = list(states)
        return self._request("ui.dump", {"states": states} if states else {})

    def subscribe(self, topics: Iterable[str]) -> Reply:
        return self._request("subscribe", {"topics": list(topics)})

    def unsubscribe(self, topics: Iterable[str]) -> Reply:
        return self._request("unsubscribe", {"topics": list(topics)})

    # events from the game
    def on(self, event: str, handler: Callable[[dict], None]) -> Callable[[], None]:
        """``handler(payload)`` for each event ``event`` of ``EVENTS`` (``SELECTION``: the selected objects, id, GUID,
        name, session; ``EDIT_REQUEST``: ``guid``, ``object``, ``game``, ``file``, ...); returns the remover."""
        if event not in self._handlers:
            raise ValueError(f"unknown event {event!r}")
        return _add(self._handlers[event], handler)

    def emit(self, event: str, payload: dict) -> None:
        """For a transport (and a test): an event came in from the game."""
        for handler in list(self._handlers.get(event, ())):
            _call(handler, payload, event)

    # the one place a transport differs
    def _request(self, name: str, args: dict, paths: Iterable[str] = (), kind: str = "") -> Reply:
        bad = (
            check_paths(paths) if name in ("files.changed", "reload.models", "reload.effects", "reload.scenes") else ""
        )
        if bad:
            return Reply(REFUSED, bad)
        if not self.connected:
            self.sent.append(Sent(name, args))
            log.info("live link not connected: would send %s %s", name, args)
            return Reply(NOT_CONNECTED, tr(MANUAL[kind]) if kind in MANUAL else tr("the game link is not connected"))
        if kind and not self.supports(kind):
            return Reply(
                UNAVAILABLE, tr("this game build cannot reload {kind}: {how}", kind=kind, how=tr(MANUAL[kind]))
            )
        self.sent.append(Sent(name, args))
        return self._transmit(Sent(name, args))

    def _transmit(self, request: Sent) -> Reply:
        """EXTENSION POINT (the transport, later): send ``request`` over the pipe after the handshake, return the
        game's answer as a ``Reply``. Called only when connected and the kind is supported."""
        return Reply(UNAVAILABLE, tr("no transport"))


class NotConnected(LiveLink):
    """The link of a suite with no game connected: nothing leaves, every request is recorded in ``sent`` and logged."""


def _given(**fields) -> dict:
    return {k: v for k, v in fields.items() if v is not None}


def _add(handlers: list, handler: Callable) -> Callable[[], None]:
    handlers.append(handler)

    def remove() -> None:
        if handler in handlers:
            handlers.remove(handler)

    return remove


# The registry: every app takes the same link object, and the window's indicator follows it.
_connectors: list[Callable[[], LiveLink | None]] = []
_state: dict = {"link": NotConnected(), "dispatch": []}
_listeners: dict[str, list[Callable]] = {name: [] for name in (*EVENTS, "status")}


def current() -> LiveLink:
    """The suite's one link (``NotConnected`` until a transport is connected)."""
    return _state["link"]


def use(link: LiveLink) -> LiveLink:
    """Make ``link`` the suite's link: the window's status and the apps' event handlers follow it from now on."""
    for remove in _state["dispatch"]:
        remove()
    _state["link"] = link
    _state["dispatch"] = [link.on(name, lambda payload, n=name: _dispatch(n, payload)) for name in EVENTS]
    _state["dispatch"].append(link.on_status(lambda status: _dispatch("status", status)))
    _dispatch("status", link.status())
    return link


def reset() -> LiveLink:
    """Back to a link that is not connected (the start, a closed game, a test)."""
    return use(NotConnected())


def on(event: str, handler: Callable) -> Callable[[], None]:
    """``handler`` for an event of whichever link is current (``EVENTS``), or ``"status"`` (a ``LinkStatus``)."""
    if event not in _listeners:
        raise ValueError(f"unknown event {event!r}")
    return _add(_listeners[event], handler)


def _dispatch(event: str, payload) -> None:
    for handler in list(_listeners[event]):
        _call(handler, payload, event)


def _call(handler: Callable, payload, event: str) -> None:
    """One handler of an event: a handler that fails is logged and the others still run (the transport's thread must
    not die on a bad request from the game)."""
    try:
        handler(payload)
    except Exception:
        log.exception("a handler of the live link event %s failed", event)


def register_connector(connector: Callable[[], LiveLink | None]) -> None:
    """EXTENSION POINT (the transport, later): ``connector()`` reads the game's session file, does the handshake and
    returns a connected ``LiveLink``, or None when no game is there."""
    if connector not in _connectors:
        _connectors.append(connector)


def connect() -> LiveLink:
    """Try the registered connectors; the first link that answers becomes current. Without one (today) the link stays
    not connected."""
    for connector in _connectors:
        found = connector()
        if found is not None and found.connected:
            return use(found)
    return current()


use(_state["link"])
