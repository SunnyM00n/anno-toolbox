"""The live link's wire protocol, version 1: frames, the handshake, the session file and the message set.

One module is the specification both ends are written against: ``encode_frame`` / ``CFrameReader`` (a 4-byte length,
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
DEFAULT_GAME = "anno117"
LINK_GAMES = ("anno117", "anno1800", "anno2205")  # the games whose helper speaks this protocol, each with its own pipe
PIPE_PATTERN = re.compile(r"\\\\\.\\pipe\\(anno\d{3,4})-live-link-[A-Za-z0-9_-]{1,64}")
NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")  # a mod id, a bank name, a UI state name
FIELD = re.compile(r"[A-Za-z0-9_./-]{1,128}")  # a value path such as Building/BuildCost
TOPICS = (livelink.SELECTION, livelink.RELOAD, livelink.SESSION_EVENT, livelink.LOG, livelink.SCENE)
MODES = ("full", "rebuild")  # reload.assets
UNKNOWN, ERROR = "unknown", "error"  # answers beyond livelink's: a message not in the set, a bad request

# What the game needs per kind of file, besides the ``reload`` kinds: a file the game never reads again, and the one
# that follows the asset reload (a texture). docs/live-link.md section 1.
RESTART, TEXTURE = "restart", "textures"

_MODEL_SUFFIXES = {".rdm": livelink.MODELS, ".cfg": livelink.MODELS, ".fx": livelink.EFFECTS, ".fxh": livelink.EFFECTS}
_BANK_SUFFIXES = (".bnk", ".wem")
_SESSION_SUFFIXES = (".a7m", ".a7t", ".a7minfo", ".a7me")
_INCLUDE = re.compile(r'^\s*#\s*include\s+["<]([^">]+)[">]', re.MULTILINE)


class CFrameError(ValueError):
    """A frame that breaks the protocol: too long, not JSON, not an object."""


class CSessionError(ValueError):
    """A session file that is not the game's (wrong shape, a pipe name outside the link's own)."""


# frames


def encode_frame(message: dict) -> bytes:
    """The bytes of one frame: a 4-byte little-endian length, then the object as compact UTF-8 JSON."""
    body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_FRAME:
        raise CFrameError(f"a frame of {len(body)} bytes is over the {MAX_FRAME} a frame may hold")
    return struct.pack("<I", len(body)) + body


class CFrameReader:
    """Collects the bytes a channel delivers and hands out the whole frames in them, in order."""

    def __init__(self) -> None:
        self._m_buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self._m_buffer += data
        frames = []
        while len(self._m_buffer) >= 4:
            (size,) = struct.unpack_from("<I", self._m_buffer)
            if size > MAX_FRAME:
                raise CFrameError(f"a frame announces {size} bytes, over the {MAX_FRAME} a frame may hold")
            if len(self._m_buffer) < 4 + size:
                break
            body = bytes(self._m_buffer[4 : 4 + size])
            del self._m_buffer[: 4 + size]
            frames.append(_decode(body))
        return frames


def _decode(body: bytes) -> dict:
    try:
        message = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CFrameError(f"a frame that is not JSON: {error}") from error
    if not isinstance(message, dict):
        raise CFrameError("a frame must be one JSON object")
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
class SSessionInfo:
    """What the game writes at its start into ``live-link/session.json`` and deletes at its end."""

    pipe: str
    token: str
    protocol: int = PROTOCOL
    game: str = ""
    build: str = ""
    library: str = ""
    pid: int = 0


def session_folder(user_dir: Path | None = None, game: str = DEFAULT_GAME) -> Path:
    """Where the session file of ``game`` is: ``live-link`` in its folder under Documents (``user_dir`` for a test)."""
    return (user_dir or gametree.user_dir(game)) / SESSION_FOLDER


def parse_session(text: str, game: str = "") -> SSessionInfo:
    """The session file's content; ``CSessionError`` when it is not one the link would trust. ``game``: the game the file
    is expected to belong to (its pipe and its own ``game`` field must say so); none, any of ``LINK_GAMES``."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CSessionError(f"not JSON: {error}") from error
    if not isinstance(data, dict):
        raise CSessionError("not an object")
    pipe, token = data.get("pipe"), data.get("token")
    found = PIPE_PATTERN.fullmatch(pipe) if isinstance(pipe, str) else None
    if found is None or found[1] not in LINK_GAMES:
        raise CSessionError("the pipe is not one of the live link's")
    if game and (found[1] != game or str(data.get("game", game)) != game):
        raise CSessionError(f"the session is not {game}'s")
    if not isinstance(token, str) or not 16 <= len(token) <= 128:
        raise CSessionError("no token")
    if data.get("protocol") != PROTOCOL:
        raise CSessionError(f"protocol {data.get('protocol')!r}, this client speaks {PROTOCOL}")
    fields = {k: str(data.get(k, "")) for k in ("game", "build", "library")}
    process_id = data.get("pid")
    return SSessionInfo(pipe, token, PROTOCOL, **fields, pid=process_id if isinstance(process_id, int) else 0)


def read_session(user_dir: Path | None = None, game: str = DEFAULT_GAME) -> SSessionInfo | None:
    """The running ``game``'s session, or None when there is no file or it is not a usable one."""
    path = session_folder(user_dir, game) / SESSION_FILE
    try:
        return parse_session(path.read_text(encoding="utf-8"), game)
    except (OSError, CSessionError):
        return None


def session_text(session: SSessionInfo) -> str:
    """The session file as the game writes it (a test double writes the same)."""
    return json.dumps(
        {
            "pipe": session.pipe,
            "token": session.token,
            "protocol": session.protocol,
            "game": session.game,
            "build": session.build,
            "library": session.library,
            "pid": session.pid,
        },
        indent=2,
    )


# messages


@dataclass(frozen=True)
class SArg:
    """One argument of a request: ``kind`` is ``int``, ``name``, ``str``, ``names`` (a list of ``NAME``),
    ``paths`` (relative paths inside a mod), ``fields`` (value paths), ``numbers``, ``topics``, ``mode``, ``bool`` or ``values``
    (an object of ``NAME`` to a number or a bool, at most ``MAX_VALUES``)."""

    name: str
    kind: str
    required: bool = False


REQUESTS: dict[str, tuple[SArg, ...]] = {
    "hello": (SArg("protocol", "int", True), SArg("client", "str", True), SArg("nonce", "name", True), SArg("proof", "str", True)),
    "files.changed": (SArg("mod", "name"), SArg("paths", "paths", True)),
    "reload.assets": (SArg("mode", "mode"),),
    "reload.texts": (),
    "reload.scenes": (SArg("mod", "name"), SArg("states", "names"), SArg("paths", "paths")),
    "reload.ambient": (),
    "reload.models": (SArg("mod", "name"), SArg("paths", "paths", True)),
    "reload.effects": (SArg("mod", "name"), SArg("paths", "paths", True)),
    "reload.banks": (SArg("mod", "name"), SArg("banks", "names", True)),
    "reload.session": (),
    "select": (SArg("object", "int"), SArg("guid", "int")),
    "jump": (SArg("object", "int"), SArg("session", "name"), SArg("position", "numbers")),
    "read": (SArg("guid", "int"), SArg("object", "int"), SArg("fields", "fields", True)),
    "ui.values": (SArg("states", "names", True), SArg("file", "name")),
    "debug.list": (),
    "debug.run": (SArg("command", "name", True), SArg("values", "values")),
    "debug.cheat": (SArg("name", "name", True),),
    "modules.list": (),
    "modules.set": (SArg("module", "name", True), SArg("on", "bool", True)),
    "subscribe": (SArg("topics", "topics", True),),
    "unsubscribe": (SArg("topics", "topics", True),),
    "ping": (),
    "bye": (),
}  # fmt: skip
LONG_REQUESTS: set[str] = set()  # requests a module answers only after a wait in the game (a capture): the reload time
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
        "int": lambda candidate: (
            isinstance(candidate, int) and not isinstance(candidate, bool) and 0 <= candidate < 1 << 53
        ),
        "name": lambda candidate: isinstance(candidate, str) and NAME.fullmatch(candidate) is not None,
        "str": lambda candidate: isinstance(candidate, (str, dict)),
        "mode": lambda candidate: candidate in MODES,
        "bool": lambda candidate: isinstance(candidate, bool),
        "numbers": lambda candidate: _list(
            candidate, 3, lambda element: isinstance(element, (int, float)) and not isinstance(element, bool)
        ),
        "names": lambda candidate: _list(
            candidate, 64, lambda element: isinstance(element, str) and NAME.fullmatch(element) is not None
        ),
        "fields": lambda candidate: _list(
            candidate, 64, lambda element: isinstance(element, str) and FIELD.fullmatch(element) is not None
        ),
        "topics": lambda candidate: _list(candidate, len(TOPICS), lambda element: element in TOPICS),
        "values": lambda candidate: (
            isinstance(candidate, dict)
            and 0 < len(candidate) <= MAX_VALUES
            and all(
                NAME.fullmatch(k) and isinstance(element, (int, float)) and abs(element) < 1 << 53
                for k, element in candidate.items()
            )
        ),
        "paths": lambda candidate: _list(
            candidate,
            MAX_PATHS,
            lambda element: (
                isinstance(element, str) and len(element) <= MAX_PATH_LEN and not livelink.check_paths([element])
            ),
        ),
    }
    return "" if checks[kind](value) else f"is not a valid {kind}"


def _list(value: object, most: int, item) -> bool:
    return isinstance(value, list) and 0 < len(value) <= most and all(item(element) for element in value)


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
    file_path = PurePosixPath(path.lower())
    name, suffix = file_path.name, file_path.suffix
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
        if "ambient" in file_path.parts:
            return livelink.AMBIENT
        return livelink.TEXTS if name.startswith("texts") else livelink.ASSETS
    return RESTART


def effects_including(folder: Path, includes: Iterable[str]) -> list[str]:
    """The effect files (``.fx``, relative to the mod's ``folder``) whose ``#include`` lines name one of ``includes``
    (relative paths of changed ``.fxh`` files): the game follows no include, so the link reloads the effects that
    pull it in. docs/live-link.md section 1.1."""
    wanted = {PurePosixPath(include_path.lower()).name for include_path in includes}
    found = []
    for fx in sorted(Path(folder).rglob("*.fx")):
        try:
            text = fx.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(PurePosixPath(included.lower()).name in wanted for included in _INCLUDE.findall(text)):
            found.append(fx.relative_to(folder).as_posix())
    return found
