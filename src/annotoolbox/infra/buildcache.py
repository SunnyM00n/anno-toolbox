"""Content-addressed caches of the slow, deterministic build steps (docs/build-cache.md).

A step (a texconv run, an ffmpeg encode, a whole island) is keyed by a hash of everything
that decides its output: the input bytes, its parameters, the identity of the external tool and the source of
the modules that make the step (``code_version``). A hit returns the bytes the step produced before, so a
cached build is byte-identical to a fresh one. ``--no-cache`` (or ``ANNO2070_NO_CACHE=1``) bypasses it.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from ..paths import PACKAGE_DIR
from ..sources.overlay import cache_base

ENV_DISABLE = "ANNO2070_NO_CACHE"
PACKAGE = __name__.partition(".")[0]
STEPS_DIR = "steps"
BLOBS_DIR = "blobs"
# what `cache clear` knows: name -> folders under the cache root; "sources" also covers each install's own folder,
# `<root name>-<10 hex>` (overlay.default_cache_dir), where a tree without --cache keeps its files and indexes
GROUPS = {
    "bc7": ("bc7",),
    "steps": (STEPS_DIR, BLOBS_DIR),
    "lists": ("lists",),  # infra/listcache.py: the lists the apps keep between starts
    "sources": ("files", "index", "index117"),
}
SOURCE_FOLDER = re.compile(r".+-[0-9a-f]{10}")

_g_stats: Counter[str] = Counter()
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get(ENV_DISABLE, "") in ("", "0")


def disable() -> None:
    """Bypass every cache for this process and the processes it starts."""
    os.environ[ENV_DISABLE] = "1"


def root() -> Path:
    return cache_base() / STEPS_DIR


# Keys


def digest(*parts: bytes | str | int | float | None) -> str:
    """SHA-256 over the parts, each length-prefixed so that ("ab", "c") and ("a", "bc") differ."""
    hasher = hashlib.sha256()
    for part in parts:
        raw = b"\0none" if part is None else part if isinstance(part, bytes) else str(part).encode("utf-8")
        hasher.update(struct.pack("<Q", len(raw)))
        hasher.update(raw)
    return hasher.hexdigest()


@functools.lru_cache(maxsize=64)
def _file_digest(path: str, size_bytes: int, mtime_ns: int) -> str:
    return file_sha(Path(path))


def file_digest(path: Path) -> str:
    """The content hash of a file (an external tool), remembered while its size and time stay."""
    st = Path(path).stat()
    return _file_digest(str(path), st.st_size, st.st_mtime_ns)


def _imports(source: str, module: str) -> set[str]:
    """Dotted names (without the package) of the package modules the source of ``module`` imports, relative
    (``from ..mod import x``) or absolute (``from annotoolbox.mod import x``); a name may also be an object
    of a module, the caller sorts that out."""
    package = module.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.partition(".")[2] for alias in node.names if alias.name.startswith(PACKAGE + "."))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:  # relative: climb level-1 packages from the module's own, then descend node.module
            if node.level - 1 > len(package):
                continue
            base = package[: len(package) - (node.level - 1)] + (node.module.split(".") if node.module else [])
        elif node.module and node.module.split(".")[0] == PACKAGE:
            base = node.module.split(".")[1:]
        else:
            continue
        if base:
            found.add(".".join(base))
        found.update(".".join([*base, alias.name]) for alias in node.names)
    return found


def _package_dir() -> Path:
    return PACKAGE_DIR


@functools.cache
def code_version(*modules: str) -> str:
    """A hash of the source of ``modules`` and of every module of this package they import, however deep:
    an edit to any of them makes the cached outputs of the step stale. Names are dotted module names without
    the package (``islands.islandbatch``). A frozen program has no source to hash: ``frozen_version`` stands for it."""
    if getattr(sys, "frozen", False):
        return frozen_version(*modules)
    folder = _package_dir()
    todo, seen = list(modules), set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        path = folder / (name.replace(".", "/") + ".py")
        if not path.is_file():  # a subpackage, or a name imported from a module
            path = folder / name.replace(".", "/") / "__init__.py"
            if not path.is_file():
                continue
        todo += _imports(path.read_text(encoding="utf-8"), name)
    hasher = hashlib.sha256()
    for name in sorted(seen):
        path = folder / (name.replace(".", "/") + ".py")
        if path.is_file():
            hasher.update(name.encode() + b"\0" + path.read_bytes().replace(b"\r\n", b"\n"))
    return hasher.hexdigest()[:20]


def frozen_version(*modules: str) -> str:
    """The code version of a frozen program: its package version and the content of its executable, which carries
    the compiled modules (so every build differs, an upgrade to the same version number included)."""
    from .. import __version__

    return digest("frozen", __version__, file_digest(Path(sys.executable)), *modules)[:20]


def fingerprint(paths: list[Path]) -> str:
    """Names, sizes and times of the files an input tree is made of (a game's archives): it changes with a patch."""
    parts: list[str | int] = []
    for path in sorted(paths):
        st = Path(path).stat()
        parts += [Path(path).name, st.st_size, st.st_mtime_ns]
    return digest(*parts)


@functools.cache
def libs_version() -> str:
    """The versions of the libraries whose arithmetic a cached image or grid depends on."""
    found = []
    for name in ("PIL", "numpy", "scipy"):
        try:
            found.append(f"{name}={__import__(name).__version__}")
        except ImportError:
            found.append(f"{name}=none")
    return ",".join(found)


# Entries


def pack(files: dict[str, bytes]) -> bytes:
    out = [struct.pack("<I", len(files))]
    for name, data in files.items():
        raw = name.encode("utf-8")
        out += [struct.pack("<II", len(raw), len(data)), raw, data]
    return b"".join(out)


def unpack(blob: bytes) -> dict[str, bytes]:
    (count,) = struct.unpack_from("<I", blob)
    at, out = 4, {}
    for _ in range(count):
        name_length, size_bytes = struct.unpack_from("<II", blob, at)
        at += 8
        name = blob[at : at + name_length].decode("utf-8")
        out[name] = blob[at + name_length : at + name_length + size_bytes]
        at += name_length + size_bytes
    return out


def _entry(namespace: str, key: str) -> Path:
    return root() / namespace / key[:2] / key


def _replace(source: str, dest: Path) -> None:
    """``os.replace`` that waits out a reader (or a virus scanner) holding the target open on Windows."""
    for attempt in range(8):
        try:
            os.replace(source, dest)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def read_kept(path: Path, match: dict) -> dict | None:
    """A JSON index kept by ``write_kept``, or None when there is none, it is unreadable or it was made for other
    inputs: ``match`` holds the keys (a version, a stamp, parameters) that must equal the stored ones."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or any(key not in data or data[key] != value for key, value in match.items()):
        return None
    return data


def write_kept(path: Path, data: dict) -> None:
    """Keep a JSON index (whole or not at all, folder made)."""
    write_atomic(Path(path), json.dumps(data).encode("utf-8"))


def write_atomic(path: Path, data: bytes, is_synchronous: bool = True) -> None:
    """Whole or not at all, and a name of its own per process: two runs on one file never meet half a file.
    ``sync`` False skips the flush to disk, for files a later run makes again (extracted cache entries)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=path.parent, suffix=".part")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            if is_synchronous:
                file.flush()
                os.fsync(file.fileno())  # a crash must not leave a renamed but empty file
        _replace(part, path)
    except BaseException:
        Path(part).unlink(missing_ok=True)
        raise


def copy_atomic(source: Path, dest: Path) -> None:
    """``source`` copied to ``dest`` whole or not at all (a copy of its own name beside it, then moved over it); for
    files too big to hold in memory."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=dest.parent, suffix=".part")
    os.close(fd)
    try:
        shutil.copy2(source, part)
        _replace(part, dest)
    except BaseException:
        Path(part).unlink(missing_ok=True)
        raise


def _count(namespace: str, what: str) -> None:
    with _lock:
        _g_stats[f"{namespace}/{what}"] += 1


def get(namespace: str, key: str) -> bytes | None:
    if not enabled():
        return None
    try:
        data = _entry(namespace, key).read_bytes()
    except OSError:
        _count(namespace, "miss")
        return None
    _count(namespace, "hit")
    return data


def put(namespace: str, key: str, data: bytes) -> None:
    if enabled():
        try:
            write_atomic(_entry(namespace, key), data)
        except OSError:  # a full disk or a folder that cannot be written is no reason to fail a build
            pass


def get_files(namespace: str, key: str) -> dict[str, bytes] | None:
    blob = get(namespace, key)
    if blob is None:
        return None
    try:
        return unpack(blob)
    except (struct.error, UnicodeDecodeError):  # a damaged entry is a miss, like a lost one
        _count(namespace, "stale")
        return None


def put_files(namespace: str, key: str, files: dict[str, bytes]) -> None:
    if enabled():
        put(namespace, key, pack(files))


# Trees: a manifest of files kept as blobs by content, so that identical files of different entries are stored once


def _blob(sha: str) -> Path:
    return cache_base() / BLOBS_DIR / sha[:2] / sha


def put_tree(namespace: str, key: str, files: dict[str, tuple[str, Path]]) -> None:
    """Remember ``files`` (relative name -> (content hash, file on disk)) under ``key``."""
    if not enabled():
        return
    manifest = {}
    for rel, (sha, path) in files.items():
        blob = _blob(sha)
        if not blob.is_file():
            try:
                write_atomic(blob, Path(path).read_bytes())
            except OSError:
                return
        manifest[rel] = [sha, blob.stat().st_size]
    put(namespace, key, json.dumps(manifest, sort_keys=True).encode("utf-8"))


def _blob_intact(blob: Path, size_bytes: int) -> bool:
    try:
        return blob.stat().st_size == size_bytes
    except OSError:
        return False


def get_tree(namespace: str, key: str) -> dict[str, tuple[str, Path]] | None:
    """relative name -> (content hash, blob) of an entry whose blobs are all still there."""
    raw = get(namespace, key)
    if raw is None:
        return None
    try:
        manifest = json.loads(raw)
        out = {rel: (sha, _blob(sha)) for rel, (sha, _size_bytes) in manifest.items()}
        intact = all(_blob_intact(blob, manifest[rel][1]) for rel, (_, blob) in out.items())
    except (ValueError, TypeError, AttributeError):  # not a manifest (a damaged entry)
        intact = False
    if not intact:
        _count(namespace, "stale")
        return None
    return out


def file_sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


# Numbers


def stats() -> dict[str, int]:
    with _lock:
        return dict(_g_stats)


def add_stats(other: dict[str, int]) -> None:
    with _lock:
        _g_stats.update(other)


def summary() -> str:
    """ "dds2bc7 412 hits / 38 misses, ..." of this run, or "" when no cache was asked."""
    spaces = sorted({k.rpartition("/")[0] for k in _g_stats})
    parts = []
    for ns in spaces:
        hit, miss = _g_stats[f"{ns}/hit"], _g_stats[f"{ns}/miss"]
        parts.append(f"{ns} {hit} hits / {miss} misses")
    return ", ".join(parts)


def group_folders(group: str) -> list[Path]:
    """The folders under the cache root a group covers (``GROUPS``; the installs' own folders for "sources")."""
    base = cache_base()
    folders = [base / sub for sub in GROUPS[group]]
    if group == "sources" and base.is_dir():
        folders += sorted(
            folder for folder in base.iterdir() if folder.is_dir() and SOURCE_FOLDER.fullmatch(folder.name)
        )
    return folders


def folder_sizes() -> dict[str, tuple[int, int]]:
    """group -> (files, bytes) under the cache root."""
    out = {}
    for group in GROUPS:
        files = size_bytes = 0
        for folder in group_folders(group):
            for path in folder.rglob("*") if folder.is_dir() else ():
                if path.is_file():
                    files += 1
                    size_bytes += path.stat().st_size
        out[group] = (files, size_bytes)
    return out


def namespace_sizes() -> dict[str, tuple[int, int]]:
    """step -> (entries, bytes) of each folder under the steps cache, and of the blobs the island entries share."""
    out = {}
    for folder in sorted([*root().glob("*"), cache_base() / BLOBS_DIR]):
        if folder.is_dir():
            sizes = [path.stat().st_size for path in folder.rglob("*") if path.is_file()]
            out[folder.name] = (len(sizes), sum(sizes))
    return out


def clear(groups: list[str] | None = None) -> dict[str, int]:
    """Delete the caches of ``groups`` (all of ``GROUPS`` by default); returns group -> bytes freed."""
    before = folder_sizes()
    freed = {}
    for group in groups or list(GROUPS):
        for folder in group_folders(group):
            shutil.rmtree(folder, ignore_errors=True)
        freed[group] = before[group][1]
    return freed
