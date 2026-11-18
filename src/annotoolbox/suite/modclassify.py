"""Which app owns a file of a mod: the sort the mod import makes (docs/mod-import.md, sections 1 and 2). Stdlib only.

``classify(game, rel)`` gives the loader's kind of the file and the app that edits that kind (``CARRIED`` when none
does). ``kind`` is how the loader treats the file, ``owner`` the key of the suite's app (``suite.apps``) that is to
take it in. Phase 1 only records the owner; every file stays in the project's carried folder until the app learns it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..modloader.mods import MODINFO_NAMES

CARRIED = "carried"
# the owners: the keys of the suite's apps, and the words the report uses for the files no app edits
ASSETS, TEXTS, OBJECTS, UI, SOUND, PARTICLES, ISLANDS, MAPS = (
    "assets",
    "texts",
    "objects",
    "ui",
    "sound",
    "particles",
    "islands",
    "maps",
)
UI_FOLDER = "data/ui/studio/generated/"  # 117's Phoenix scenes (files without an extension)

# a file's extension (lower case, with the dot; ``.cfg.xml`` style double endings first) -> (kind, owner)
_BY_SUFFIX: dict[str, tuple[str, str]] = {
    ".cfg.xml": ("config patch", OBJECTS),
    ".fc.xml": ("config patch", OBJECTS),
    ".include.xml": ("include", CARRIED),
    ".cfg": ("model config", OBJECTS),
    ".fc": ("model config", OBJECTS),
    ".ifo": ("model config", OBJECTS),
    ".bfg": ("model config", OBJECTS),
    ".rdm": ("model", CARRIED),
    ".dds": ("texture", CARRIED),
    ".png": ("texture", CARRIED),
    ".a7m": ("island", ISLANDS),
    ".a7minfo": ("island", ISLANDS),
    ".a7me": ("terrain", CARRIED),
    ".ctt": ("terrain", CARRIED),
    ".tmc": ("terrain", CARRIED),
    ".a7t": ("map", MAPS),
    ".a7tinfo": ("map", MAPS),
    ".a7te": ("map", CARRIED),
    ".fx": ("shader", CARRIED),
    ".ttf": ("font", CARRIED),
    ".otf": ("font", CARRIED),
    ".bnk": ("sound", SOUND),
    ".wem": ("sound", SOUND),
    ".rdp": ("effect", PARTICLES),
    ".ani": ("animation", CARRIED),
    ".bk2": ("movie", CARRIED),
    ".exr": ("image", CARRIED),
    ".lua": ("script", CARRIED),
    ".json": ("data", CARRIED),
    ".jsonc": ("data", CARRIED),
    ".rda": ("archive", CARRIED),
    ".lnk": ("shortcut", CARRIED),
}
# a patch file by its name; any other .xml (properties, templates, camera, engine settings) has no form: carried
_PATCH_OWNERS = {"assets.xml": ASSETS}


@dataclass(frozen=True)
class Class:
    """What a file is: ``kind`` the loader's, ``owner`` the app that edits it or ``CARRIED``, ``understood`` whether
    the table knows the kind (else the file is carried and reported as not understood)."""

    kind: str
    owner: str
    understood: bool = True


OTHER = Class("other", CARRIED, False)


def extension(rel: str) -> str:
    """The extension a report counts a file under: lower case with the dot (``.cfg.xml``, ``.include.xml``), the
    folder's scene files (no extension) as ``(no extension)``."""
    name = rel.rsplit("/", 1)[-1].lower()
    for double in (".cfg.xml", ".fc.xml", ".include.xml", ".bin.xml"):
        if name.endswith(double):
            return double
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else "(none)"


def classify(game: str, rel: str) -> Class:
    """The class of the mod-relative path ``rel`` (forward slashes) for ``game``."""
    low = rel.replace("\\", "/").lower()
    name = low.rsplit("/", 1)[-1]
    if low in MODINFO_NAMES:
        return Class("modinfo", CARRIED)
    if name.endswith(".xml"):
        return _xml(low, name)
    if game == "anno117" and low.startswith(UI_FOLDER) and "." not in name:
        return Class("ui scene", UI)
    for suffix, (kind, owner) in _BY_SUFFIX.items():
        if name.endswith(suffix):
            return Class(kind, owner)
    return OTHER


def _xml(low: str, name: str) -> Class:
    """A ``.xml`` of a mod: a patch of the game file of the same path, a config patch or an include."""
    for suffix in (".cfg.xml", ".fc.xml", ".include.xml"):
        if name.endswith(suffix):
            kind, owner = _BY_SUFFIX[suffix]
            return Class(kind, owner)
    if name.startswith("texts_"):
        return Class("patch", TEXTS)
    if name in _PATCH_OWNERS:
        return Class("patch", _PATCH_OWNERS[name])
    if name == "export.bin.xml":
        return Class("infotips", CARRIED)
    return Class("patch", CARRIED)
