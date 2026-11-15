"""What the property editor offers for a config's fields beyond text, a number and a switch: a closed list of choices,
a set of bits, a colour group and the game's files a file field may take. No Qt.

``choices`` and ``flag_names`` are the lists the notes state (the shader ids of 117, 1800 and 2205, a track element's
type, 117's condition bits); ``colour_groups``, ``colour_scale`` and ``colour_edits`` turn a group
``X.r X.g X.b [X.a]`` to and from 8-bit channels; ``file_suffixes``, ``file_candidates`` and ``file_value`` list and
write the files a field may name. docs/tool-suite.md section 23, "The typed editors" and "Choice lists for the other
games" (where the names come from).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from ..infra.i18n import N_, tr
from .cfgdoc import Field
from .cfgmcf import FILE_ENDINGS

# 117's shaders by id (engine-rendering "A model config names its shader by number"); the ids not named there are
# left out, a value the list lacks is kept as it is
SHADERS_117 = {
    2: "Glass", 4: "Volume Cut Out", 5: "Cut Out", 6: "Decal", 7: "Water", 8: "Physically-Based", 9: "Frame Cycle",
    11: "Portrait Skin", 12: "Portrait Hair", 14: "Icon", 18: "Low-Poly", 19: "Street", 20: "Houdini VAT 2",
    21: "Grass", 22: "Plant", 23: "Tree", 24: "Terrain", 25: "Houdini VAT 3", 26: "Ocean Decal",
    27: "Tree Experimental", 28: "Tech Artist Example", 29: "Tech Artist 01", 30: "Tech Artist 02",
    31: "Tech Artist 03", 32: "Tech Artist 04",
}  # fmt: skip
# The ids of 1800 and 2205 that a shader source of the install names by the parameters the materials of that id carry
# (tool-suite section 23); the ids the files give no evidence for stay numbers
SHADERS_1800 = {
    0: "Cloth", 2: "Glass", 5: "Cut Out", 7: "Water", 9: "Frame Cycle", 16: "Planet", 17: "Planet Water", 18: "Low-Poly",
}  # fmt: skip
SHADERS_2205 = {
    7: "Water", 9: "Frame Cycle", 10: "Interior Mapping", 12: "Hair", 13: "Arctic", 15: "Video", 16: "Planet",
}  # fmt: skip
TRACK_TYPES = {0: N_("Animation"), 1: N_("Effect"), 2: N_("State")}  # configtree.sequences: 0, 1, 2
# the newer games' fourth kind: a sound (an audio asset's GUID played at a bone of a model)
TRACK_TYPES_NEWER = {**TRACK_TYPES, 3: N_("Sound")}
# 117's condition bits, bit 0 first (engine-animation "Conditions")
CONDITION_BITS_117 = (
    "ColorTransform", "PhysicsTransform", "DestructionTransform", "Selected", "Unselected",
    *(f"Common{k:02d}" for k in range(1, 12)), "SkyDay", "EffectShield", "EffectRepair", "EffectStun", "EffectBurn",
    "ShowWarningLights", "Riot", "RiotSpecial01", "NoInhabitants", "Ruin", "Irrigated", "GroundDecalDefault",
    "GroundDecalVariation", "Electricity", "Common12", "EnableShipWave",
)  # fmt: skip
# bits the game sets on an object itself, besides the ones a sequence's condition track sets
ENGINE_STATE_BITS = 1 << 2 | 1 << 3 | 1 << 4  # destroyed, selected, unselected
SOURCE_SUFFIXES = (".psd", ".png", ".tga")  # the endings the newer games name a texture by
TEXTURES = (*SOURCE_SUFFIXES, ".dds")
MESHES = (".rdm", ".fbx", ".gr2")
CONFIGS = (".cfg", ".rdp", ".efc")
SKIPPED_LEVELS = re.compile(r"_(lod[1-9]|[1-4])\.(rdm|dds)$")  # lower levels of detail and quality: not picked by name
TEXTURE_FIELD = re.compile(r"Tex|Texture|Map|Mask", re.I)
KIND_SUFFIXES = {"FILE": CONFIGS, "PARTICLE": CONFIGS, "EFFECT": CONFIGS, "MODEL": MESHES, "CLOTH": MESHES,
                 "ANIMATION": MESHES}  # fmt: skip
CHANNELS = "rgba"


# the closed lists by (the game or "", the part kind or "", the field's name); the most specific key wins
CHOICES: dict[tuple[str, str, str], dict[int, str]] = {
    ("anno117", "", "ShaderID"): SHADERS_117,
    ("anno1800", "", "ShaderID"): SHADERS_1800,
    ("anno2205", "", "ShaderID"): SHADERS_2205,
    ("", "TrackElement", "Type"): TRACK_TYPES,
    **{(game, "TrackElement", "Type"): TRACK_TYPES_NEWER for game in ("anno117", "anno1800", "anno2205")},
}
TRANSLATED = (
    TRACK_TYPES,
    TRACK_TYPES_NEWER,
)  # the tables whose labels are marked ``N_``; the shaders' are the game's names
FLAG_BITS: dict[tuple[str, str], tuple[str, ...]] = {("anno117", "Conditions"): CONDITION_BITS_117}


def choices(name: str, part_kind: str, game: str) -> list[tuple[str, str]]:
    """The (value, label) pairs a field may take, ``[]`` when it is free: the shader ids of 117, 1800 and 2205, a track
    element's ``Type``."""
    for key in ((game, part_kind, name), (game, "", name), ("", part_kind, name)):
        table = CHOICES.get(key)
        if table is not None:
            translate = tr if any(table is own for own in TRANSLATED) else str
            return [(str(k), translate(v)) for k, v in table.items()]
    return []


def flag_names(name: str, game: str) -> tuple[str, ...]:
    """The names of a bit field's bits, the first for bit 0; empty when the field is no known bit set."""
    return FLAG_BITS.get((game, name), ())


def flags_value(text: str) -> int | None:
    """A bit field's number, None when the text is none."""
    try:
        return int(text.strip())
    except ValueError:
        return None


# Colours


def colour_groups(fields: list[Field]) -> dict[str, dict[str, Field]]:
    """The colours of a part's fields: the prefix of ``X.r X.g X.b`` (and ``X.a``) -> channel -> field, in the order
    the groups start. A prefix without all of r, g and b is no colour."""
    found: dict[str, dict[str, Field]] = {}
    for f in fields:
        prefix, _dot, channel = f.name.rpartition(".")
        if prefix and channel in CHANNELS and len(channel) == 1:
            found.setdefault(prefix, {})[channel] = f
    return {p: g for p, g in found.items() if all(c in g for c in "rgb")}


def colour_scale(group: dict[str, Field]) -> int | None:
    """1 for a colour of floats 0 to 1, 255 for one of whole numbers up to 255, None for no picker (a value that is no
    number, negative, above 1 and not whole, or above 255)."""
    values = []
    for f in group.values():
        try:
            values.append(float(f.value))
        except ValueError:
            return None
    if any(v < 0 for v in values):
        return None
    if all(v <= 1 for v in values):
        return 1
    return 255 if all(v <= 255 and v == int(v) for v in values) else None


def colour_channels(group: dict[str, Field], scale: int) -> list[int]:
    """The group's channels as 0 to 255 in the order r, g, b, a (the ones it has)."""
    return [round(float(group[c].value) / scale * 255) for c in CHANNELS if c in group]


def colour_edits(group: dict[str, Field], picked: Iterable[int], scale: int) -> list[tuple[Field, str]]:
    """(field, new text) for each channel the picked 8-bit colour changes; a channel it leaves keeps its text."""
    out = []
    for channel, new in zip((c for c in CHANNELS if c in group), picked, strict=False):
        f = group[channel]
        if round(float(f.value) / scale * 255) != new:
            out.append((f, format(new / 255, ".4g") if scale == 1 else str(new)))
    return out


# Files


def file_suffixes(name: str, value: str, part_kind: str) -> tuple[str, ...]:
    """The file endings a file field may name: those of the current value's kind, else by the field's name (a texture
    slot) or the part it is in; empty for no filter."""
    low = value.strip().lower()
    if name in FILE_ENDINGS:  # 1701's mesh configs name the kind of file in the field
        return FILE_ENDINGS[name]
    for group in (TEXTURES, MESHES, CONFIGS):
        if low.endswith(group):
            return group
    if TEXTURE_FIELD.search(name):
        return TEXTURES
    return KIND_SUFFIXES.get(part_kind, ())


def file_candidates(
    paths: Iterable[str], suffixes: tuple[str, ...], source_names: bool, query: str = "", limit: int = 300
) -> list[str]:
    """The values the game's files give a field: the newer games name a texture by its source file (``x.psd`` for
    ``x_0.dds``), never by a lower quality level or a lower level of detail of a mesh."""
    q, out, seen = query.strip().lower(), [], set()
    for path in paths:
        if suffixes and not path.endswith(suffixes):
            continue
        value = _named(path, suffixes, source_names)
        if value and q in value and value not in seen:
            seen.add(value)
            out.append(value)
            if len(out) >= limit:
                break
    return out


def _named(path: str, suffixes: tuple[str, ...], source_names: bool) -> str:
    if suffixes == TEXTURES and source_names and path.endswith(".dds"):
        if SKIPPED_LEVELS.search(path):
            return ""
        return path[: -len("_0.dds")] + ".psd" if path.endswith("_0.dds") else path
    if suffixes == MESHES and source_names and SKIPPED_LEVELS.search(path):
        return ""
    return path


def file_value(picked: str, current: str) -> str:
    """The text a pick is written as: a texture keeps the source ending of the current value, a mesh its ``.fbx``."""
    if "\\" in current and "/" not in current:  # 1701 writes its paths with backslashes: keep the style
        picked = picked.replace("/", "\\")
    low = current.strip().lower()
    source = next((s for s in SOURCE_SUFFIXES if low.endswith(s)), "")
    if picked.endswith(".psd") and source:
        return picked[: -len(".psd")] + source
    if picked.endswith(".rdm") and low.endswith(".fbx"):
        return re.sub(r"(_lod0)?\.rdm$", ".fbx", picked)
    return picked
