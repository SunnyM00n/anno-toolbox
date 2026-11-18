"""The QRhi path's compiled game shaders a phase no longer needs, found and removed: the compiles of a phase the native
backend now draws, and those of an earlier game build. A manifest per game build and phase lists every build cache
entry the QRhi path wrote or read (``record``, from ``models.shaderview``); only those entries are removed, and only
from the QRhi path's own namespace (never the native backend's fallback compiles, never another game's cache).

``offer(build, native)`` says what could go and its size, ``remove(offer)`` removes it. docs/build-cache.md, "The QRhi
path's game shaders"; docs/tool-suite.md section 21, "The start-up stage".
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from ..infra import buildcache

SHADERS = "game-shaders"  # models.shaderview.NAMESPACE: the QRhi path's compiles (variant records and shaders)
MANIFESTS = "game-shaders-manifest"  # <build>/<phase>.txt: one entry key a line
MODELS, OTHER = "models", "other"  # a material's variants; a compile of no fixed phase (the view's own passes)
_lock = threading.Lock()
_written: set[tuple[str, str]] = set()  # (manifest file, key) recorded this session


def phase_of(path: str, what: str) -> str:
    """The phase a compile belongs to: a fixed pass by its effect and technique (``shaderstart.phases``), a material's
    variant by its role (``gbuffer``, ``shadow``, ...), else ``OTHER``."""
    from .shaderstart import phases

    table = _table(phases)
    if "/" not in what:
        return MODELS
    return table.get((path, what), OTHER)


_tables: dict = {}


def _table(phases) -> dict:
    found = _tables.get("fixed")
    if found is None:
        found = _tables["fixed"] = {(spec[0], f"{spec[1]}/{spec[2]}"): p.name for p in phases()
                                    for spec in p.stages.values()}  # fmt: skip
    return found


def record(build: str, phase: str, keys: list[str]) -> None:
    """``keys`` (entries of ``SHADERS``) written or read for ``phase`` under the game ``build``, appended to its
    manifest once a session."""
    if not buildcache.enabled() or not build:
        return
    path = buildcache.root() / MANIFESTS / build / f"{phase}.txt"
    with _lock:
        new = [k for k in keys if (str(path), k) not in _written]
        if not new:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="ascii", newline="\n") as f:
                f.write("".join(f"{k}\n" for k in new))
        except OSError:  # a full disk or a folder that cannot be written: the cleaner only knows less
            return
        _written.update((str(path), k) for k in new)


def manifests() -> dict[str, dict[str, set[str]]]:
    """build -> phase -> the entry keys its manifest lists."""
    out: dict[str, dict[str, set[str]]] = {}
    base = buildcache.root() / MANIFESTS
    for folder in sorted(base.iterdir()) if base.is_dir() else ():
        if folder.is_dir():
            for f in folder.glob("*.txt"):
                keys = {ln.strip() for ln in f.read_text(encoding="ascii", errors="ignore").splitlines()}
                out.setdefault(folder.name, {})[f.stem] = {k for k in keys if _is_key(k)}
    return out


def _is_key(text: str) -> bool:
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


@dataclass
class Offer:
    """What could be removed: the phases of the current build drawn natively now, the earlier game builds, their
    entries (keys of ``SHADERS`` that exist) and bytes."""

    build: str = ""
    phases: list[str] = field(default_factory=list)
    builds: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    size: int = 0
    natives: list[str] = field(default_factory=list)  # the native phases that draw ``phases`` now (said to the user)

    def __bool__(self) -> bool:
        return bool(self.keys)


def offer(build: str, native: frozenset[str] | set[str]) -> Offer:
    """The entries no longer needed: those of the ``native`` phases of ``build`` and every entry of another game
    build, but none that a needed phase of ``build`` lists too (two variants can share one compile)."""
    found = manifests()
    current = found.get(build, {})
    keep = set().union(*(keys for phase, keys in current.items() if phase not in native))
    out = Offer(build, sorted(p for p in current if p in native), sorted(b for b in found if b != build))
    candidates = set().union(*(current[p] for p in out.phases), *(k for b in out.builds for k in found[b].values()))
    for key in sorted(candidates - keep):
        entry = buildcache._entry(SHADERS, key)
        if entry.is_file():
            out.keys.append(key)
            out.size += entry.stat().st_size
    return out


def signature(what: Offer) -> str:
    """What an offer is about (its build, phases and earlier builds): a new one is asked about again."""
    return buildcache.digest(what.build, *what.phases, "|", *what.builds)


def should_ask(state: dict, what: Offer, version: str) -> bool:
    """Whether the pop-up asks: something to remove, not "don't ask again" for it, and not asked for this program
    version and offer already ("not now" asks again after the next update, or when a game update adds to it)."""
    if not what or signature(what) in state.get("never", []):
        return False
    return state.get("asked") != [version, signature(what)]


def answered(state: dict, what: Offer, version: str, choice: str) -> dict:
    """The remembered state after a choice: "remove" or "later" (asked for this version and offer), "never" (this
    offer is never asked about again; the Help menu's entry still offers it)."""
    out = {**state, "asked": [version, signature(what)]}
    if choice == "never":
        out["never"] = sorted({*state.get("never", []), signature(what)})
    return out


@dataclass
class Removed:
    entries: int = 0
    size: int = 0
    failed: list[str] = field(default_factory=list)


def remove(what: Offer, progress: Callable[[int, int], None] | None = None) -> Removed:
    """Remove the offer's entries from ``SHADERS`` and the manifests it covered; a file that will not go is named in
    the result (the rest goes on)."""
    out = Removed()
    for n, key in enumerate(what.keys, 1):
        entry = buildcache._entry(SHADERS, key)
        try:
            size = entry.stat().st_size
            entry.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            out.failed.append(f"{key[:12]}: {exc}")
        else:
            out.entries += 1
            out.size += size
        if progress is not None:
            progress(n, len(what.keys))
    base = buildcache.root() / MANIFESTS
    for build in what.builds:
        if _is_key(build):  # a manifest folder of ours, by its build's name (a digest): nothing else
            shutil.rmtree(base / build, ignore_errors=True)
    for phase in what.phases:
        (base / what.build / f"{phase}.txt").unlink(missing_ok=True)
    with _lock:
        _written.clear()  # a compile asked for again is listed anew
    return out
