"""What kind a game file is, and which forms it can be exported in.

``kind_of(path)`` sorts a game path by its suffix (and, for ``.txt``, its folder) into one of ``KINDS``;
``formats_for(kind)`` lists the forms: always "as stored" first, then what can be made from that kind. The keys
are global (``png`` means the same for a texture and a picture), so one choice in a dialog applies to a whole
selection and each file takes the keys its kind knows. What each form is: docs/exporting.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..infra.i18n import N_

KINDS = (
    "model",
    "texture",
    "picture",
    "movie",
    "sound",
    "bank",
    "data",
    "ui",
    "effect",
    "island",
    "font",
    "text",
    "other",
)


@dataclass(frozen=True)
class Format:
    """One form a file can be exported in: its key, its label for a menu, the suffix it writes ("" for as stored)."""

    key: str
    label: str
    suffix: str
    default: bool = False


RAW = Format("raw", N_("As stored"), "", True)
PNG = Format("png", N_("PNG (8 bit)"), ".png")
PNG16 = Format("png16", N_("PNG (16 bit)"), ".png")
EXR = Format("exr", N_("OpenEXR (float)"), ".exr")
HDR = Format("hdr", N_("Radiance HDR"), ".hdr")
GLB = Format("glb", N_("glTF binary (.glb)"), ".glb")
GLTF = Format("gltf", N_("glTF with a .bin (.gltf)"), ".gltf")
WAV = Format("wav", N_("WAV"), ".wav")
OGG = Format("ogg", N_("Ogg Vorbis"), ".ogg")
MP4 = Format("mp4", N_("MP4 (needs ffmpeg)"), ".mp4")
WEBM = Format("webm", N_("WebM (needs ffmpeg)"), ".webm")
JSON = Format("json", N_("JSON"), ".json")
CSV = Format("csv", N_("CSV"), ".csv")

SUFFIX_KIND = {
    **dict.fromkeys((".cfg", ".rdm", ".gr2", ".mcf", ".ocf"), "model"),
    ".dds": "texture",
    **dict.fromkeys((".png", ".tga", ".jpg", ".jpeg", ".bmp", ".gif"), "picture"),
    **dict.fromkeys((".bik", ".bk2", ".webm", ".mp4", ".avi", ".ogv", ".mov", ".mkv"), "movie"),
    **dict.fromkeys((".wem", ".wav", ".mp3", ".ogg"), "sound"),
    ".bnk": "bank",
    **dict.fromkeys(
        (".xml", ".json", ".lua", ".ifo", ".fc", ".ctt", ".bfg", ".csv", ".ini", ".csl", ".fx", ".fxh"), "data"
    ),
    ".ui": "ui",
    **dict.fromkeys((".rdp", ".efc"), "effect"),
    **dict.fromkeys((".a7m", ".a6m", ".a7t", ".a7te", ".isd", ".www"), "island"),
    **dict.fromkeys((".ttf", ".otf", ".fnt", ".fon"), "font"),
    ".txt": "data",
}

FORMATS = {
    "model": (GLB, GLTF),
    "texture": (PNG, PNG16, EXR, HDR),
    "picture": (PNG,),
    "movie": (MP4, WEBM),
    "sound": (WAV, OGG),
    "bank": (WAV, OGG, JSON),
    "data": (JSON, CSV),
    "ui": (JSON,),
    "effect": (JSON,),
    "island": (JSON, CSV, PNG16, EXR),
    "font": (),
    "text": (CSV,),
    "other": (),
}


def kind_of(path: str) -> str:
    """The kind of a game path: ``KINDS``. A ``.txt`` under a ``loca`` folder is a language text, any other is data."""
    lower = path.lower().replace("\\", "/")
    suffix = lower[lower.rindex(".") :] if "." in lower.rsplit("/", 1)[-1] else ""
    if suffix == ".txt" and "/loca/" in "/" + lower:
        return "text"
    return SUFFIX_KIND.get(suffix, "other")


def suffixes(*wanted: str) -> tuple[str, ...]:
    """The file suffixes of the given kinds, in the table's order."""
    return tuple(s for s, kind in SUFFIX_KIND.items() if kind in wanted)


def formats_for(kind: str) -> tuple[Format, ...]:
    """The forms a kind exports in: "as stored" first, then the made ones."""
    return (RAW, *FORMATS.get(kind, ()))
