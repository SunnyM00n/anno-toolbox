"""The rules that turn "no file names it" into a verdict: files the game finds without a name written in the data
(entry points), files that belong to a referenced one (the same texture at a lower quality, a mesh's other levels,
a config's baked and info files, an island's folder of baked pages) and files the tools generate.

``reachable`` follows the names from the entry points (a file named only by an unused file is not live) and
``classify(paths, named, named_live)`` is the entry: path -> one of ``CLASSES``. What each rule rests on, per game:
docs/references.md.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable

REFERENCED = "referenced"  # a live file of the data names it
IMPLIED = "implied"  # not named itself, but part of a live file (see ``implied_by``)
ENTRY = "entry"  # the game finds it by its place or its name, not through the data
GENERATED = "generated"  # a baked or compiled copy the tools make
ORPHANED = "orphaned"  # only named by unused files ("only named by unused files")
UNREFERENCED = "unreferenced"  # no reference found in the data (the program may still load it)
CLASSES = (REFERENCED, IMPLIED, ENTRY, GENERATED, ORPHANED, UNREFERENCED)
UNUSED = (ORPHANED, UNREFERENCED)  # the classes that make the "not referenced" list

# The files the program reads by place or name: the folders of the data root (the base game's, an add-on's or a
# content pack's) that hold configuration, scripts, shaders, texts and the UI, and the scenarios' folders.
ENTRY_PLACES = re.compile(
    r"^[^/]+/(?:(?:base|c?dlc\w*|fcp\w*|season\w*)/)?"
    r"(?:config|script|scripts|shaders|shader|system|loca|ui|gui|menu|fonts|cursors|startscreen|ai)/"
    r"|^[^/]+/levels/(?:scenarios|campaign|world_events|dlc_\w+)/|\.(?:exe|checksum|md)$"
    r"|/pool/|^(?:filebrowsercache|log|logs)/"  # a pool's islands are picked by their folder, not by a name
    r"|^guidata/"  # 2070's interface data beside the data folder
    r"|^[^/]+/levels/(?:multiplayer|tutorial|addon01|kidmode|presets|demo_\w+)/"  # 1404's and 1701's game modes
    # opened by their name, and they name what the game plays or builds from them (docs/references.md, "Live files")
    r"|/(?:sounddatabase\.xml|soundbanksinfo\.(?:xml|json)|ocflist\.ini|guid_ocflist\.ini)$"
)

# Files whose names a tool makes from another file's: the game ships them, nothing names them.
GENERATED_SUFFIXES = ("fxo", "ctt", "tmc", "cfgcache", "pyc", "dectest", "checksum")

# A referenced file brings its same-stem partners (a config's baked data, info, dummies; an island's other files).
PARTNER_SUFFIXES = (
    "bfg", "ifo", "fc", "cf7", "a7minfo", "a7me", "a7mpreview", "a7tinfo", "a7te", "info", "json", "preview", "isc",
    "isd", "ocf", "prp", "brp", "rdp", "ani", "py", "png", "jpg", "snd", "abm", "txt", "tfo",
)  # fmt: skip
GRANNY = (".gr2", ".bg2")  # the older games' meshes
BANK_PARTNERS = ("json", "xml", "txt")  # what the sound tool writes beside a bank: its info files

_TEXTURE = re.compile(r"(_\d)?\.(dds|tga|png|psd|bmp|jpg)$")  # a source name and the quality levels it is baked to
_LEVEL = re.compile(r"(_lod\d+)?\.(rdm|gr2)$")
_DATA_FOLDERS = ("/_gamedata/", "/_a6mdata/")  # an island's or a session's baked pages
OWNER_SUFFIXES = (".a7m", ".a6m", ".a7t")
SHARED = "shared/"  # 117 reads a data/ config through its shared/ copy (engine-modloader "the shared/ copies")


def stem_of(path: str) -> str:
    """The path without its last suffix."""
    head, dot, tail = path.rpartition(".")
    return head if dot and "/" not in tail else path


def family_of(path: str) -> str:
    """What a texture's quality levels or a mesh's levels of detail share: ``x_0.dds``, ``x_2.dds``, ``x.dds`` and the
    source name ``x.tga`` are one texture; ``x.rdm`` and ``x_lod2.rdm`` one mesh; any other file is its own family."""
    if _TEXTURE.search(path):
        return _TEXTURE.sub("", path) + ".texture"
    if _LEVEL.search(path):
        return _LEVEL.sub("", path) + ".mesh"
    return path


def owner_of(path: str) -> str:
    """The folder a baked page belongs to: everything below ``<island>/_gamedata/`` is part of ``<island>``'s files."""
    for marker in _DATA_FOLDERS:
        at = path.find(marker)
        if at >= 0:
            return path[:at]
    return ""


def data_form(path: str) -> str:
    """A ``shared/`` copy as the ``data/`` file it stands for (117 reads a ``data/`` config through its copy)."""
    return "data/" + path[len(SHARED) :] if path.startswith(SHARED) else path


def is_entry(path: str) -> bool:
    """Whether the program finds the file by its place (``ENTRY_PLACES``)."""
    return ENTRY_PLACES.search(path) is not None


def implied_by(paths: Iterable[str], named: Collection[str]) -> set[str]:
    """The files that belong to a named one: its texture or mesh family, its same-stem partners, the pages below the
    folder of a named island. A ``shared/`` file and its ``data/`` file are one file here, whichever is named."""
    kept = {data_form(p) for p in named}
    # a Granny mesh brings the textures that carry its name (``x.gr2`` and ``x_0.dds`` to ``x_2.dds``)
    families = {family_of(p) for p in kept} | {stem_of(p) + ".texture" for p in kept if p.endswith(GRANNY)}
    # a named texture also names its picture's other files (the 2070 ``x.abm`` beside ``x_0.dds``)
    stems = {stem_of(p) for p in kept} | {f[: -len(".texture")] for f in families if f.endswith(".texture")}
    bank_stems = {stem_of(p) for p in kept if p.endswith(".bnk")}
    owners = {p.rsplit("/", 1)[0] for p in kept if p.endswith(OWNER_SUFFIXES)}
    out = set()
    for p in paths:
        if p in named:
            continue
        suffix = p.rsplit(".", 1)[-1]
        key = data_form(p)
        if (
            key in kept
            or (family_of(key) != key and family_of(key) in families)
            or (suffix in PARTNER_SUFFIXES and stem_of(key) in stems)
            or (suffix in BANK_PARTNERS and stem_of(key) in bank_stems)
            or (suffix == "png" and stem_of(key) in kept)  # ``x.bkg.png``: the picture of ``x.bkg``
            or ((owner := owner_of(key)) and owner in owners)
        ):
            out.add(p)
    return out


def reachable(paths: Iterable[str], names_of: Callable[[str], Iterable[str]]) -> set[str]:
    """The files named by a live file. Live: an entry, or named by a live file, or implied by a live file; each round
    takes what the files that became live name, and what belongs to those, until nothing new is live."""
    paths = list(paths)
    live = {p for p in paths if is_entry(p)}
    named: set[str] = set()
    frontier = set(live)
    while frontier:
        new = {t for f in frontier for t in names_of(f)} - named
        named |= new
        frontier = (new | implied_by(paths, new)) - live if new else set()
        live |= frontier
    return named


def classify(paths: Iterable[str], named: Collection[str], named_live: Collection[str] | None = None) -> dict[str, str]:
    """Every path's class. ``named`` are the files some other file names (``RefGraph.named``), ``named_live`` those a
    live file names (``reachable``; the same as ``named`` when it is not given: the verdict looks one step)."""
    paths = list(paths)
    named_live = named if named_live is None else named_live
    implied = implied_by(paths, named_live)
    dead = implied_by(paths, named) if named_live is not named else set()  # the partners of an unused file
    out = {}
    for p in paths:
        if p in named_live:
            out[p] = REFERENCED
        elif p in implied:
            out[p] = IMPLIED
        elif is_entry(p):
            out[p] = ENTRY
        elif p.endswith(tuple("." + s for s in GENERATED_SUFFIXES)):
            out[p] = GENERATED
        elif p in named or p in dead:
            out[p] = ORPHANED
        else:
            out[p] = UNREFERENCED
    return out
