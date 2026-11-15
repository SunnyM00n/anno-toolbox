"""The live link's wire protocol, version 1: frames, the handshake, the session file and the message set.

One module is the specification both ends are written against: ``encode_frame`` / ``FrameReader`` (a 4-byte length,
then one JSON object), ``proof`` / ``verify`` (the token handshake), ``read_session`` (where the game says it
listens), ``REQUESTS`` with ``check_request`` (the closed message set and its typed arguments) and ``needs_of`` (what a
changed file needs). The client is ``linkclient``. docs/live-link.md sections 3 to 5.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..sources import gametree
from . import livelink

PROTOCOL = 1
MAX_FRAME = 1 << 20  # a frame's JSON, bytes
SESSION_FOLDER = "live-link"
SESSION_FILE = "session.json"
PIPE_PATTERN = re.compile(r"\\\\\.\\pipe\\anno117-live-link-[A-Za-z0-9_-]{1,64}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")  # a mod id, a bank name, a UI state name
FIELD = re.compile(r"[A-Za-z0-9_./-]{1,128}")  # a value path such as Building/BuildCost
TOPICS = (livelink.SELECTION, livelink.RELOAD, livelink.SESSION_EVENT, livelink.LOG)
MODES = ("full", "rebuild")  # reload.assets
UNKNOWN, ERROR = "unknown", "error"  # answers beyond livelink's: a message not in the set, a bad request

# What the game needs per kind of file, besides the ``reload`` kinds: a file the game never reads again, and the one
# that follows the asset reload (a texture). docs/live-link.md section 1.
RESTART, TEXTURE = "restart", "textures"

_MODEL_SUFFIXES = {".rdm": livelink.MODELS, ".cfg": livelink.MODELS, ".fx": livelink.EFFECTS, ".fxh": livelink.EFFECTS}
_BANK_SUFFIXES = (".bnk", ".wem")
_SESSION_SUFFIXES = (".a7m", ".a7t", ".a7minfo", ".a7me")
_INCLUDE = re.compile(r'^\s*#\s*include\s+["<]([^">]+)[">]', re.MULTILINE)


class FrameError(ValueError):
    """A frame that breaks the protocol: too long, not JSON, not an object."""


class SessionError(ValueError):
    """A session file that is not the game's (wrong shape, a pipe name outside the link's own)."""


# frames


def encode_frame(message: dict) -> bytes:
    """The bytes of one frame: a 4-byte little-endian length, then the object as compact UTF-8 JSON."""
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise FrameError(f"a frame of {len(body)} bytes is over the {MAX_FRAME} a frame may hold")
    return struct.pack("<I", len(body)) + body


class FrameReader:
    """Collects the bytes a channel delivers and hands out the whole frames in them, in order."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._buffer += data
        frames = []
        while len(self._buffer) >= 4:
            (size,) = struct.unpack_from("<I", self._buffer)
            if size > MAX_FRAME:
                raise FrameError(f"a frame announces {size} bytes, over the {MAX_FRAME} a frame may hold")
            if len(self._buffer) < 4 + size:
                break
            body = bytes(self._buffer[4 : 4 + size])
            del self._buffer[: 4 + size]
            frames.append(_decode(body))
        return frames


def _decode(body: bytes) -> dict:
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrameError(f"a frame that is not JSON: {error}") from error
    if not isinstance(message, dict):
        raise FrameError("a frame must be one JSON object")
    return message


# the handshake (the token never travels: only keyed hashes of fresh values do)


def new_secret() -> str:
    """A fresh random value (the token, a challenge, a nonce) as hex."""
    return secrets.token_hex(16)


def proof(token: str, purpose: str, value: str) -> str:
    """HMAC-SHA256 keyed with the token over ``purpose:value``, as lowercase hex. The client answers the game's
    challenge with ``proof(token, "client", challenge)``; the game answers the client's nonce with ``"game"``."""
    return hmac.new(token.encode("utf-8"), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def verify(token: str, purpose: str, value: str, given: object) -> bool:
    return isinstance(given, str) and hmac.compare_digest(proof(token, purpose, value), given.lower())


# the session file


@dataclass(frozen=True)
class SessionInfo:
    """What the game writes at its start into ``live-link/session.json`` and deletes at its end."""

    pipe: str
    token: str
    protocol: int = PROTOCOL
    game: str = ""
    build: str = ""
    library: str = ""
    pid: int = 0


def session_folder(user_dir: Path | None = None) -> Path:
    """Where the session file is: ``live-link`` in the game's folder under Documents (``user_dir`` for a test)."""
    return (user_dir or gametree.user_dir("anno117")) / SESSION_FOLDER


def parse_session(text: str) -> SessionInfo:
    """The session file's content; ``SessionError`` when it is not one the link would trust."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise SessionError(f"not JSON: {error}") from error
    if not isinstance(data, dict):
        raise SessionError("not an object")
    pipe, token = data.get("pipe"), data.get("token")
    if not isinstance(pipe, str) or not PIPE_PATTERN.fullmatch(pipe):
        raise SessionError("the pipe is not one of the live link's")
    if not isinstance(token, str) or not 16 <= len(token) <= 128:
        raise SessionError("no token")
    if data.get("protocol") != PROTOCOL:
        raise SessionError(f"protocol {data.get('protocol')!r}, this client speaks {PROTOCOL}")
    fields = {k: str(data.get(k, "")) for k in ("game", "build", "library")}
    pid = data.get("pid")
    return SessionInfo(pipe, token, PROTOCOL, **fields, pid=pid if isinstance(pid, int) else 0)


def read_session(user_dir: Path | None = None) -> SessionInfo | None:
    """The running game's session, or None when there is no file or it is not a usable one."""
    path = session_folder(user_dir) / SESSION_FILE
    try:
        return parse_session(path.read_text(encoding="utf-8"))
    except (OSError, SessionError):
        return None


def session_text(info: SessionInfo) -> str:
    """The session file as the game writes it (a test double writes the same)."""
    return json.dumps(
        {
            "pipe": info.pipe,
            "token": info.token,
            "protocol": info.protocol,
            "game": info.game,
            "build": info.build,
            "library": info.library,
            "pid": info.pid,
        },
        indent=2,
    )


# messages


@dataclass(frozen=True)
class Arg:
    """One argument of a request: ``kind`` is ``int``, ``name``, ``str``, ``names`` (a list of ``NAME``),
    ``paths`` (relative paths inside a mod), ``fields`` (value paths), ``numbers``, ``topics``, ``mode``, ``bool`` or ``values``
    (an object of ``NAME`` to a number or a bool, at most ``MAX_VALUES``)."""

    name: str
    kind: str
    required: bool = False


REQUESTS: dict[str, tuple[Arg, ...]] = {
    "hello": (Arg("protocol", "int", True), Arg("client", "str", True), Arg("nonce", "name", True), Arg("proof", "str", True)),
    "files.changed": (Arg("mod", "name"), Arg("paths", "paths", True)),
    "reload.assets": (Arg("mode", "mode"),),
    "reload.texts": (),
    "reload.scenes": (Arg("mod", "name"), Arg("states", "names"), Arg("paths", "paths")),
    "reload.ambient": (),
    "reload.models": (Arg("mod", "name"), Arg("paths", "paths", True)),
    "reload.effects": (Arg("mod", "name"), Arg("paths", "paths", True)),
    "reload.banks": (Arg("mod", "name"), Arg("banks", "names", True)),
    "reload.session": (),
    "select": (Arg("object", "int"), Arg("guid", "int")),
    "jump": (Arg("object", "int"), Arg("session", "name"), Arg("position", "numbers")),
    "read": (Arg("guid", "int"), Arg("object", "int"), Arg("fields", "fields", True)),
    "ui.dump": (Arg("states", "names"),),
    "debug.list": (),
    "debug.run": (Arg("command", "name", True), Arg("values", "values")),
    "debug.cheat": (Arg("name", "name", True),),
    "modules.list": (),
    "modules.set": (Arg("module", "name", True), Arg("on", "bool", True)),
    "subscribe": (Arg("topics", "topics", True),),
    "unsubscribe": (Arg("topics", "topics", True),),
    "ping": (),
    "bye": (),
}  # fmt: skip
MAX_VALUES = 8  # the typed values of a debug command
MAX_PATHS = 256  # paths in one request; a longer list goes in parts (``linkclient``)
MAX_PATH_LEN = 199
CHUNKED = ("files.changed", "reload.models", "reload.effects", "reload.scenes")


def check_request(name: str, args: dict) -> str:
    """ "" when ``args`` are the typed arguments of the request ``name``, else why not: the closed message set the
    game also applies (unknown names, missing or extra arguments, characters outside the safe set)."""
    spec = REQUESTS.get(name)
    if spec is None:
        return f"unknown request {name!r}"
    known = {a.name: a for a in spec}
    for key in args:
        if key not in known:
            return f"{name}: unexpected argument {key!r}"
    for arg in spec:
        if arg.name not in args:
            if arg.required:
                return f"{name}: {arg.name} is missing"
            continue
        why = _check_value(arg.kind, args[arg.name])
        if why:
            return f"{name}: {arg.name} {why}"
    return ""


def _check_value(kind: str, value: object) -> str:
    checks = {
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 1 << 53,
        "name": lambda v: isinstance(v, str) and NAME.fullmatch(v) is not None,
        "str": lambda v: isinstance(v, (str, dict)),
        "mode": lambda v: v in MODES,
        "bool": lambda v: isinstance(v, bool),
        "numbers": lambda v: _list(v, 3, lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)),
        "names": lambda v: _list(v, 64, lambda x: isinstance(x, str) and NAME.fullmatch(x) is not None),
        "fields": lambda v: _list(v, 64, lambda x: isinstance(x, str) and FIELD.fullmatch(x) is not None),
        "topics": lambda v: _list(v, len(TOPICS), lambda x: x in TOPICS),
        "values": lambda v: (
            isinstance(v, dict)
            and 0 < len(v) <= MAX_VALUES
            and all(NAME.fullmatch(k) and isinstance(x, (int, float)) and abs(x) < 1 << 53 for k, x in v.items())
        ),
        "paths": lambda v: _list(
            v, MAX_PATHS, lambda x: isinstance(x, str) and len(x) <= MAX_PATH_LEN and not livelink.check_paths([x])
        ),
    }
    return "" if checks[kind](value) else f"is not a valid {kind}"


def _list(value: object, most: int, item) -> bool:
    return isinstance(value, list) and 0 < len(value) <= most and all(item(x) for x in value)


def request_frame(request_id: int, name: str, args: dict) -> dict:
    return {"id": request_id, "type": name, "args": args}


def answer_frame(request_id: int, status: str, message: str = "", data: dict | None = None) -> dict:
    return {"id": request_id, "status": status, "message": message, "data": data or {}}


def event_frame(event: str, data: dict) -> dict:
    return {"event": event, "data": data}


def needs_of(path: str) -> str:
    """What the game needs for a changed mod file at ``path`` (relative, forward slashes): a reload kind of
    ``livelink.KINDS``, ``TEXTURE`` (a texture: the asset reload, then a new request), or ``RESTART``
    (docs/live-link.md section 1). The answer of ``files.changed`` carries it per path."""
    p = PurePosixPath(path.lower())
    name, suffix = p.name, p.suffix
    if suffix in _MODEL_SUFFIXES:
        return _MODEL_SUFFIXES[suffix]
    if suffix == ".dds":
        return TEXTURE
    if suffix in _BANK_SUFFIXES:
        return livelink.BANKS
    if suffix in _SESSION_SUFFIXES:
        return livelink.SESSION
    if path.lower().startswith("data/ui/") or suffix in (".phx", ".phxl"):
        return livelink.SCENES
    if suffix == ".xml":
        if "ambient" in p.parts:
            return livelink.AMBIENT
        return livelink.TEXTS if name.startswith("texts") else livelink.ASSETS
    return RESTART


def effects_including(folder: Path, includes: Iterable[str]) -> list[str]:
    """The effect files (``.fx``, relative to the mod's ``folder``) whose ``#include`` lines name one of ``includes``
    (relative paths of changed ``.fxh`` files): the game follows no include, so the link reloads the effects that
    pull it in. docs/live-link.md section 1.1."""
    wanted = {PurePosixPath(i.lower()).name for i in includes}
    found = []
    for fx in sorted(Path(folder).rglob("*.fx")):
        try:
            text = fx.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(PurePosixPath(m.lower()).name in wanted for m in _INCLUDE.findall(text)):
            found.append(fx.relative_to(folder).as_posix())
    return found
