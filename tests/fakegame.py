"""A stand-in for the game's side of the live link: the same protocol (``suite/linkprotocol``) served from Python.

``FakeGame`` answers over any ``Channel``: ``pair()`` gives a client channel joined to one by a socket (every platform),
``pipe_server()`` serves a real named pipe (Windows). It checks the handshake with the token, applies the closed message
set, keeps every request in ``requests``, lets a test choose the answer per request (``answers``), delay it (``delay``),
push events and cut the connection. What the real game side does with a request is the helper library's; this proves
the client against the protocol.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

from annotoolbox.suite import linkprotocol as proto
from annotoolbox.suite import livelink
from annotoolbox.suite.linkclient import ChannelClosed, PipeChannel

TOKEN = "0123456789abcdef0123456789abcdef"


class SocketChannel:
    """A ``Channel`` over a socket (the portable double of the pipe)."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def read(self, timeout: float) -> bytes:
        self._sock.settimeout(max(timeout, 0.001))
        try:
            data = self._sock.recv(65536)
        except TimeoutError:
            return b""
        except OSError as error:
            raise ChannelClosed(str(error)) from error
        if not data:
            raise ChannelClosed("end of stream")
        return data

    def write(self, data: bytes) -> None:
        try:
            self._sock.sendall(data)
        except OSError as error:
            raise ChannelClosed(str(error)) from error

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()


class FakeGame:
    """The game's end of one connection (``serve`` on a thread of its own)."""

    def __init__(self, token: str = TOKEN, kinds=livelink.KINDS, mods=None, game="anno117", build="3.0") -> None:
        self.token, self.kinds, self.game, self.build = token, tuple(kinds), game, build
        self.mods = mods if mods is not None else [{"id": "mymod", "folder": ""}]
        self.requests: list[tuple[str, dict]] = []
        self.answers: dict[str, tuple[str, str, dict]] = {}  # request name -> (status, message, data)
        self.delay: dict[str, float] = {}  # request name -> seconds before the answer
        self.topics: set[str] = set()
        self.welcomed = threading.Event()
        self.channel = None
        self.silent = False  # answer nothing (a hung game)
        self.debug_table: dict[
            str, dict
        ] = {}  # debug.list: id -> {"available": bool, "state": bool | None}, set by a test
        self.cheats: list[str] = []  # debug.list's cheats; debug.cheat accepts only these
        self.modules: list[dict] = []  # modules.list's entries; modules.set switches a switchable one
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def attach(self, channel) -> FakeGame:
        self.channel = channel
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def push(self, event: str, data: dict) -> None:
        if event in self.topics or event in (livelink.EDIT_REQUEST, livelink.STALLED):  # these two need no topic
            self.channel.write(proto.encode_frame(proto.event_frame(event, data)))

    def drop(self) -> None:
        self._stop.set()
        self.channel.close()

    def names(self) -> list[str]:
        return [name for name, _ in self.requests]

    def _serve(self) -> None:
        reader = proto.FrameReader()
        challenge = proto.new_secret()
        try:
            self.channel.write(proto.encode_frame({"challenge": challenge, "protocol": proto.PROTOCOL}))
            while not self._stop.is_set():
                for frame in reader.feed(self.channel.read(0.05)):
                    if not self._handle(frame, challenge):
                        self.channel.close()
                        return
        except (OSError, proto.FrameError):
            return

    def _handle(self, frame: dict, challenge: str) -> bool:
        name, args, request_id = frame.get("type", ""), frame.get("args") or {}, frame.get("id", 0)
        if not self.welcomed.is_set():
            return self._hello(request_id, name, args, challenge)
        self.requests.append((name, args))
        why = proto.check_request(name, args)
        if name not in proto.REQUESTS:
            self._answer(request_id, proto.UNKNOWN, why)
        elif why:
            self._answer(request_id, proto.ERROR, why)
        elif name == "bye":
            return False
        else:
            if name == "subscribe":
                self.topics.update(args["topics"])
            time.sleep(self.delay.get(name, 0))
            status, message, data = self.answers.get(name) or self._default(name, args)
            if not self.silent:
                self._answer(request_id, status, message, data)
        return True

    def _hello(self, request_id: int, name: str, args: dict, challenge: str) -> bool:
        if name != "hello" or not proto.verify(self.token, "client", challenge, args.get("proof")):
            self._answer(request_id, livelink.REFUSED, "the handshake failed")
            return False
        data = {
            "game": self.game, "build": self.build, "library": "fake 1", "protocol": proto.PROTOCOL,
            "kinds": list(self.kinds), "mods": self.mods,
            "proof": proto.proof(self.token, "game", args["nonce"]),
        }  # fmt: skip
        self._answer(request_id, livelink.OK, "", data)
        self.welcomed.set()
        return True

    def _default(self, name: str, args: dict) -> tuple[str, str, dict]:
        if name == "files.changed":
            return livelink.OK, "", {"needs": {p: proto.needs_of(p) for p in args["paths"]}}
        if name.startswith("reload."):
            return livelink.DONE, "", {"ms": 1}
        if name == "ping":
            return livelink.OK, "", {"pong": True}
        if name == "debug.list":
            return livelink.OK, "", {"commands": self.debug_table, "cheats": list(self.cheats)}
        if name == "debug.run":
            entry = self.debug_table.get(args["command"])
            if not entry or not entry.get("available"):
                return livelink.UNAVAILABLE, "the game does not bind that command", {}
            return livelink.DONE, "", {} if entry.get("state") is None else {"state": entry["state"]}
        if name == "modules.list":
            return livelink.OK, "", {"modules": [dict(m) for m in self.modules]}
        if name == "modules.set":
            found = next((m for m in self.modules if m["name"] == args["module"]), None)
            if not found or not found.get("switchable"):
                return livelink.REFUSED, found.get("why", "") if found else "no module of that name", {}
            found["on"] = args["on"]
            return livelink.DONE, "", {"module": args["module"], "on": args["on"]}
        if name == "debug.cheat":
            ok = not self.cheats or args["name"] in self.cheats  # no list set: every name is one the game knows
            return (livelink.DONE, "", {}) if ok else (livelink.REFUSED, "not a cheat the game lists", {})
        return livelink.OK, "", {}

    def _answer(self, request_id: int, status: str, message: str = "", data: dict | None = None) -> None:
        self.channel.write(proto.encode_frame(proto.answer_frame(request_id, status, message, data)))


def pair(game: FakeGame | None = None) -> tuple[SocketChannel, FakeGame]:
    """A client channel and the game serving its other end."""
    near, far = socket.socketpair()
    game = (game or FakeGame()).attach(SocketChannel(far))
    return SocketChannel(near), game


def session_for(game: FakeGame, pipe: str) -> proto.SessionInfo:
    return proto.SessionInfo(pipe, game.token, proto.PROTOCOL, game.game, game.build, "fake 1", 1)


def write_session(user_dir: Path, info: proto.SessionInfo) -> Path:
    folder = proto.session_folder(user_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / proto.SESSION_FILE
    path.write_text(proto.session_text(info), encoding="utf-8")
    return path


def pipe_server(game: FakeGame, name: str) -> threading.Thread:
    """Serve ``game`` on the named pipe ``name`` (Windows): one client, then the thread ends. Returns once the pipe
    exists, so a client can open it at once."""
    import ctypes
    from ctypes import wintypes

    assert sys.platform == "win32"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateNamedPipeW.restype = wintypes.HANDLE
    kernel.CreateNamedPipeW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    ]  # fmt: skip
    kernel.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    # PIPE_ACCESS_DUPLEX; byte type, byte read, wait, reject remote clients; one instance
    handle = kernel.CreateNamedPipeW(name, 3, 0 | 0 | 0 | 8, 1, 65536, 65536, 0, None)
    assert handle not in (None, wintypes.HANDLE(-1).value), ctypes.get_last_error()

    def accept() -> None:
        kernel.ConnectNamedPipe(handle, None)
        game.attach(PipeChannel(handle))

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    return thread
