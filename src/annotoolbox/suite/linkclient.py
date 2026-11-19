"""The live link's transport on the suite's side: the pipe, the handshake, requests with timeouts, events, reconnect.

``PipeLink`` is a ``livelink.LiveLink`` over a ``Channel`` (the named pipe of the running game, or any other byte
channel: tests use a socket). ``connect_game`` reads the session file and returns a connected link; it is registered as
the suite's connector, and ``AutoConnect`` retries it every few seconds so a game started later is found and a lost
one is found again. The messages and the framing are ``linkprotocol``. docs/live-link.md sections 3 and 8.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from ..infra.i18n import tr
from . import features, linkscenes, livelink
from . import linkprotocol as proto
from .livelink import LinkStatus, Reply, Sent

log = logging.getLogger(__name__)

CLIENT = {"name": "anno-toolbox", "version": "1"}
REQUEST_TIMEOUT = 10.0  # seconds a request may wait for its answer
RELOAD_TIMEOUT = 180.0  # a reload answers after the game's confirmation: a big mod's pass takes half a minute
HANDSHAKE_TIMEOUT = 5.0
PING_AFTER = 15.0  # idle seconds before a ping
PING_WAIT = 8.0  # seconds a ping may stay unanswered before the link counts as lost
POLL = 0.005  # seconds between looks at an idle pipe
RETRY = 3.0  # seconds between AutoConnect's attempts


class ChannelClosed(OSError):
    """The other end is gone (or the channel was closed here)."""


class Channel(Protocol):
    """A byte stream both ends can use from two threads (one reading, any writing)."""

    def read(self, timeout: float) -> bytes:
        """Up to a buffer of bytes; ``b""`` when none came within ``timeout``; ``ChannelClosed`` at the end."""

    def write(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class PipeCalls(Protocol):
    """What ``PipeChannel`` asks of the system for a handle (a test stands in for it)."""

    def available(self, handle: int) -> int | None:
        """The bytes that wait to be read, without waiting; None when the pipe is broken."""

    def read(self, handle: int, count: int) -> bytes | None: ...

    def write(self, handle: int, data: bytes) -> bool: ...

    def close(self, handle: int) -> None: ...


class Win32Pipe:
    """``PipeCalls`` on kernel32."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._byref, self._buffer, self._dword = ctypes.byref, ctypes.create_string_buffer, wintypes.DWORD
        self._kernel = kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        count = ctypes.POINTER(wintypes.DWORD)
        kernel.PeekNamedPipe.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, count, wintypes.LPVOID,
        ]  # fmt: skip
        kernel.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, count, wintypes.LPVOID]
        kernel.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, count, wintypes.LPVOID]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]  # fmt: skip

    def open(self, name: str) -> int:
        """The pipe ``name`` opened as a client; ``ChannelClosed`` when there is none."""
        import ctypes
        from ctypes import wintypes

        generic_read_write, open_existing = 0xC0000000, 3
        # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION: the game may learn who we are, never act as us
        flags = 0x00100000 | 0x00010000
        handle = self._kernel.CreateFileW(name, generic_read_write, 0, None, open_existing, flags, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            raise ChannelClosed(f"cannot open {name}: error {ctypes.get_last_error()}")
        return handle

    def available(self, handle: int) -> int | None:
        left = self._dword(0)
        ok = self._kernel.PeekNamedPipe(handle, None, 0, None, self._byref(left), None)
        return left.value if ok else None

    def read(self, handle: int, count: int) -> bytes | None:
        buffer, got = self._buffer(count), self._dword(0)
        ok = self._kernel.ReadFile(handle, buffer, count, self._byref(got), None)
        return buffer.raw[: got.value] if ok else None

    def write(self, handle: int, data: bytes) -> bool:
        written = self._dword(0)
        return bool(self._kernel.WriteFile(handle, data, len(data), self._byref(written), None))

    def close(self, handle: int) -> None:
        self._kernel.CloseHandle(handle)


class PipeChannel:
    """A Windows named pipe as a ``Channel``. A pipe handle opened without overlapped I/O serialises its calls, so a
    read waiting in one thread would block every write: the reader asks how many bytes are there first and reads only
    those, which never waits (``POLL`` seconds is the latency of an idle pipe). ``close`` waits for the call that is
    under way: a closed handle's number is given out again, and a late read would take another object's bytes."""

    def __init__(self, handle: int, calls: PipeCalls | None = None) -> None:
        self._handle = handle
        self._calls = calls or Win32Pipe()
        self._write_lock = threading.Lock()
        self._read_lock = threading.Lock()  # held for a look and the read after it, never while waiting
        self._closed = False

    @classmethod
    def connect(cls, name: str) -> PipeChannel:
        """Open the pipe ``name`` as a client; ``ChannelClosed`` when there is none (the game is not running)."""
        calls = Win32Pipe()
        return cls(calls.open(name), calls)

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            data = self._read_ready()
            if data:
                return data
            if time.monotonic() >= deadline:
                return b""
            time.sleep(POLL)

    def _read_ready(self) -> bytes:
        """The bytes that are there now (``b""`` for none); ``ChannelClosed`` once closed, here or by the game."""
        with self._read_lock:
            if self._closed:
                raise ChannelClosed("the pipe is closed")
            ready = self._calls.available(self._handle)
            if ready is None:
                raise ChannelClosed("the pipe is closed by the other end")
            if not ready:
                return b""
            data = self._calls.read(self._handle, min(ready, 1 << 16))
            if data is None:
                raise ChannelClosed("the pipe broke while reading")
            return data

    def write(self, data: bytes) -> None:
        with self._write_lock:
            if self._closed:
                raise ChannelClosed("the pipe is closed")
            if not self._calls.write(self._handle, data):
                raise ChannelClosed("the pipe broke while writing")

    def close(self) -> None:
        with self._write_lock, self._read_lock:
            if not self._closed:
                self._closed = True
                self._calls.close(self._handle)


@dataclass
class _Pending:
    done: threading.Event = field(default_factory=threading.Event)
    reply: Reply | None = None


class PipeLink(livelink.LiveLink):
    """A link to one running game over a ``Channel``. ``open`` does the handshake and starts the reader thread; the
    request methods are the interface's. The reader emits the game's events on its own thread."""

    def __init__(self, channel: Channel) -> None:
        super().__init__()
        self._channel = channel
        self._ids = itertools.count(1)
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self.mods: list[dict] = []  # the enabled mods the game named: {"id", "folder"}
        self.welcome: dict = {}
        self._last_rx = time.monotonic()
        self._early: list[dict] = []  # frames read past the one the handshake wanted

    # connecting

    def open(self, info: proto.SessionInfo, timeout: float = HANDSHAKE_TIMEOUT) -> bool:
        """Do the handshake (the game's challenge, our proof, the game's proof of the token in turn); True when the link
        is up. On any failure the channel is closed and the status stays not connected."""
        try:
            reader = proto.FrameReader()
            first = self._next_frame(reader, timeout)
            challenge = first.get("challenge")
            if first.get("protocol") != proto.PROTOCOL or not isinstance(challenge, str):
                raise proto.FrameError("not the live link's greeting")
            nonce = proto.new_secret()
            args = {
                "protocol": proto.PROTOCOL,
                "client": CLIENT,
                "nonce": nonce,
                "proof": proto.proof(info.token, "client", challenge),
            }
            self._channel.write(proto.encode_frame(proto.request_frame(0, "hello", args)))
            answer = self._next_frame(reader, timeout)
            data = _object(answer.get("data"))
            if answer.get("status") != livelink.OK or not proto.verify(info.token, "game", nonce, data.get("proof")):
                raise proto.FrameError(answer.get("message") or "the game refused the handshake")
            if info.game and data.get("game") != info.game:  # the welcome names the game: it must be the session's
                raise proto.FrameError(f"the session file says {info.game}, the game says {data.get('game')!r}")
        except (OSError, proto.FrameError) as error:
            log.info("live link: no handshake: %s", error)
            self._channel.close()
            return False
        self.welcome = data
        self.mods = [m for m in _array(data.get("mods")) if isinstance(m, dict)]
        known = {*livelink.CAPABILITIES, *features.extensions(features.LINK_KINDS).values()}
        kinds = frozenset(k for k in _array(data.get("kinds")) if isinstance(k, str) and k in known)
        self._reader = threading.Thread(target=self._read_loop, args=(reader,), name="live-link", daemon=True)
        self._reader.start()
        self.set_status(LinkStatus(True, str(data.get("game", "")), str(data.get("build", "")), kinds))
        self.subscribe([livelink.SELECTION, livelink.RELOAD, livelink.SESSION_EVENT])
        return True

    def close(self) -> None:
        """Say goodbye and drop the link (the game closes the pipe on its side too)."""
        if self.connected:
            self._send(proto.request_frame(next(self._ids), "bye", {}))
        self._lost("closed")

    # the transport

    def _transmit(self, request: Sent) -> Reply:
        request = Sent(request.name, self._with_effect_files(request))
        if request.name == "reload.scenes":
            request = Sent(request.name, self._with_scene_states(request.args))
            if not request.args.get("states"):
                return Reply(livelink.SKIPPED, tr("common.no_ui_state_game_shows"))
        paths = request.args.get("paths")
        if request.name in proto.CHUNKED and isinstance(paths, list) and len(paths) > proto.MAX_PATHS:
            step = (
                proto.MAX_PATHS
            )  # a first install names thousands of files: the game takes a request's worth at a time
            parts = [{**request.args, "paths": paths[i : i + step]} for i in range(0, len(paths), step)]
            return _merge([self._exchange(Sent(request.name, part)) for part in parts])
        return self._exchange(request)

    def _exchange(self, request: Sent) -> Reply:
        why = proto.check_request(request.name, request.args)
        if why:
            return Reply(livelink.REFUSED, why)
        slot = _Pending()
        request_id = next(self._ids)
        with self._lock:
            self._pending[request_id] = slot
        try:
            self._channel.write(proto.encode_frame(proto.request_frame(request_id, request.name, request.args)))
        except (OSError, proto.FrameError) as error:
            self._forget(request_id)
            self._lost(str(error))
            return Reply(livelink.NOT_CONNECTED, tr("common.game_link_not_connected_lower"))
        limit = RELOAD_TIMEOUT if request.name.startswith("reload.") else REQUEST_TIMEOUT
        limit += request.args.get("delay_ms", 0) / 1000  # a request with a delay answers after it
        if not slot.done.wait(limit):
            self._forget(request_id)
            return Reply(livelink.UNAVAILABLE, tr("common.game_not_answer_s", seconds=int(limit)))
        return slot.reply or Reply(livelink.NOT_CONNECTED, tr("common.game_link_not_connected_lower"))

    def _with_effect_files(self, request: Sent) -> dict:
        """An include named for an effect reload becomes the effect files that include it."""
        args = request.args
        if request.name != "reload.effects" or not any(p.endswith(".fxh") for p in args.get("paths", [])):
            return args
        folder = next((m["folder"] for m in self.mods if m.get("id") == args.get("mod") and "folder" in m), None)
        paths = [p for p in args["paths"] if not p.endswith(".fxh")]
        if folder:
            paths += proto.effects_including(Path(folder), [p for p in args["paths"] if p.endswith(".fxh")])
        return {**args, "paths": list(dict.fromkeys(paths))}

    @staticmethod
    def _with_scene_states(args: dict) -> dict:
        """The UI states whose scenes show the changed files (``suite/linkscenes``), added to the request."""
        states = list(dict.fromkeys([*args.get("states", []), *linkscenes.states_for(args.get("paths", []))]))
        return {**args, "states": states}

    def _send(self, message: dict) -> None:
        try:
            self._channel.write(proto.encode_frame(message))
        except (OSError, proto.FrameError):
            pass

    def _next_frame(self, reader: proto.FrameReader, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while not self._early and time.monotonic() < deadline:
            self._early.extend(reader.feed(self._channel.read(min(0.2, timeout))))
        if not self._early:
            raise proto.FrameError("the game did not answer in time")
        return self._early.pop(0)

    def _read_loop(self, reader: proto.FrameReader) -> None:
        ping_sent = 0.0
        try:
            for frame in self._early:  # sent in the same read as the welcome
                self._frame(frame)
            self._early.clear()
            while True:
                data = self._channel.read(0.25)
                now = time.monotonic()
                if data:
                    self._last_rx, ping_sent = now, 0.0
                    for frame in reader.feed(data):
                        self._frame(frame)
                elif ping_sent and now - ping_sent > PING_WAIT:
                    raise ChannelClosed("no answer to a ping")
                elif not ping_sent and now - self._last_rx > PING_AFTER:
                    ping_sent = now
                    self._send(proto.request_frame(next(self._ids), "ping", {}))
        except (OSError, proto.FrameError) as error:
            self._lost(str(error))
        except Exception as error:  # a reader that ended any other way must not leave the link "connected"
            log.exception("live link: the reader failed")
            self._lost(str(error))

    def _frame(self, frame: dict) -> None:
        """One frame of the game: an event to the handlers, an answer to the request that waits for it. A frame of
        another shape (an id that is no number, data that is no object) is dropped, never the reader's end."""
        data = _object(frame.get("data"))
        if "event" in frame:
            if frame["event"] == livelink.STALLED:
                self._note(tr("common.game_seems_hang_asset_load"))
            self.emit(str(frame["event"]), data)
            return
        self._note("")  # the game answers: it lives
        request_id = frame.get("id")
        with self._lock:
            slot = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
        if slot is not None:
            slot.reply = Reply(str(frame.get("status", proto.ERROR)), str(frame.get("message", "")), data)
            slot.done.set()

    def _note(self, text: str) -> None:
        """Put the game's warning after the connection in the status (empty: take it away)."""
        status = self.status()
        if status.connected and status.note != text:
            self.set_status(replace(status, note=text))

    def _forget(self, request_id: int) -> None:
        with self._lock:
            self._pending.pop(request_id, None)

    def _lost(self, why: str) -> None:
        """The link ended: every waiting request is answered, the channel closed, the status not connected."""
        was = self.connected
        self._channel.close()
        with self._lock:
            waiting, self._pending = list(self._pending.values()), {}
        for slot in waiting:
            slot.reply = Reply(livelink.NOT_CONNECTED, tr("common.game_link_not_connected_lower"))
            slot.done.set()
        if was:
            log.info("live link lost: %s", why)
            self.set_status(LinkStatus())


def _object(value: object) -> dict:
    """``value`` when it is a JSON object, else an empty one (what the game sends is never trusted to have a shape)."""
    return value if isinstance(value, dict) else {}


def _array(value: object) -> list:
    return value if isinstance(value, list) else []


def _merge(replies: list[Reply]) -> Reply:
    """One answer for the parts of a long request: the first that was not taken decides the status, what the parts
    report (``needs`` per path, ``files``) is put together."""
    failed = next((r for r in replies if not r.ok), None)
    lead = failed or replies[-1]
    data: dict = {}
    for reply in replies:
        for key, value in reply.data.items():
            if isinstance(value, dict):
                data.setdefault(key, {}).update(value)
            elif isinstance(value, list):
                data.setdefault(key, []).extend(value)
            else:
                data[key] = value
    return Reply(lead.status, lead.message, data)


# finding the game


def connect_game(
    user_dir: Path | None = None, opener: Callable[[str], Channel] | None = None, game: str = ""
) -> livelink.LiveLink | None:
    """The connector: read the session file of ``game`` (none: of each of ``LINK_GAMES`` in turn, the first that
    answers wins; ``user_dir``, a test's, belongs to the one game named or Anno 117), open its pipe, do the handshake;
    None when no game answers (and with the default pipe opener always off Windows: ``supported``)."""
    if opener is None:
        if not supported():
            return None
        opener = PipeChannel.connect
    for each in (game,) if game else (proto.DEFAULT_GAME,) if user_dir else proto.LINK_GAMES:
        link = _connect_one(each, user_dir, opener)
        if link is not None:
            return link
    return None


def _connect_one(game: str, user_dir: Path | None, opener: Callable[[str], Channel]) -> livelink.LiveLink | None:
    info = proto.read_session(user_dir, game)
    if info is None:
        return None
    try:
        channel = opener(info.pipe)
    except OSError:
        return None  # a session file left by a game that crashed: no pipe behind it
    link = PipeLink(channel)
    return link if link.open(info) else None


class AutoConnect:
    """Tries the registered connectors every ``RETRY`` seconds while the suite has no game connected, so a game that
    starts later is found, and one that was lost is found again. One daemon thread; ``stop`` ends it."""

    def __init__(self, interval: float = RETRY) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="live-link-connect", daemon=True)

    def start(self) -> AutoConnect:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if not livelink.current().connected:
                try:
                    livelink.connect()
                except Exception:
                    log.exception("live link: a connector failed")


def supported() -> bool:
    """Whether the live link can run here: its transport is a Windows named pipe, so only on Windows (docs/linux.md
    section 5: the link is off in the Toolbox on Linux)."""
    return os.name == "nt"


def autoconnect() -> AutoConnect | None:
    """Start the retry thread for the suite's window; None when ``ANNO_LIVE_LINK=0`` (a machine or a test that must
    not look for a game) or the link is not supported here (``supported``)."""
    if os.environ.get("ANNO_LIVE_LINK", "1") == "0" or not supported():
        return None
    return AutoConnect().start()


def _register() -> None:
    """Make ``connect_game`` the suite's connector (once)."""
    livelink.register_connector(connect_game)


_register()
