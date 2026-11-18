"""Whether every file a mod's Phoenix UI files reference exists: in the mod or in 117.

The game opens a referenced ``data/ui/studio/generated/<guid>`` when a scene loads, and a missing one fails the whole
scene ("Loading scene ... failed. Error code -1"), silently to the mod loader. ``missing_refs`` reads each generated
file of a mod with ``formats.phoenixread`` and lists the references that resolve nowhere; ``modlint`` reports them as U01.
A text style of the mod is also reported when its localized font has no glyph data (``FontDescriptor``): every shipped
style draws with one that has (engine-notes "A text style needs a font with glyph data").
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable
from pathlib import Path

from ..formats import phoenix, phoenixread

SINGLE = re.compile(r"\] (\S+) = ref flags (0x[0-9a-f]+) ([0-9a-f]{8}) ([0-9a-f-]{36})")
LISTED = re.compile(r"\] (\S+) = \[\d+\] (.+)")
ITEM = re.compile(r"([0-9a-f]{8}):([0-9a-f-]{36})")
IN_FILE = {
    "bb96607d",
    "8f8600a5",
    "2f208351",
}  # GUIDs of an object inside the file, or of editor templates 117 does not ship
# the shipped localized fonts with glyph data: LisboaSans, AlbertusNova, the hieroglyphs, Quattrocento (docs/ui/fonts.md)
GLYPH_FONTS = {
    "fedf1030-b114-42b4-b42d-0bab3bb067e3",
    "42ae008d-202e-4ad1-86f6-b71bd8ee0841",
    "0c5cd0f3-5f3c-45b5-825d-691c4053fab7",
    "b4a9f3b2-4918-4c05-8a81-64e68e761066",
}
OWN_DESCRIPTOR = "[LocalizedFontAsset] FontDescriptor = ref"  # the font's own entry, not a language exception's


def references(log: list[str]) -> list[tuple[str, str, str]]:
    """(field, type hash, guid) of every asset reference in a decoded file's log."""
    out = []
    for line in log:
        if m := SINGLE.search(line):
            field, _flags, hash_, guid = m.groups()
            out.append((field, hash_, guid))
        elif (m := LISTED.search(line)) and ITEM.search(m.group(2)):
            out += [(m.group(1), h, g) for h, g in ITEM.findall(m.group(2))]
    return out


def _has_glyph_data(mod: Path, localized: str, schema: dict) -> bool:
    """Whether the localized font ``localized`` (a shipped one or the mod's) names a glyph data file."""
    if localized in GLYPH_FONTS:
        return True
    path = mod / phoenix.GENERATED / localized
    if not path.is_file():
        return False  # a shipped font without glyph data (the Meta and Salford ones), or none at all
    return any(line.strip().startswith(OWN_DESCRIPTOR) for line in phoenixread.load(path.read_bytes(), schema).log)


def _glyphless(mod: Path, data: bytes, refs: list[tuple[str, str, str]], schema: dict) -> list[str]:
    """The messages for a text style of the mod (``data``) whose localized font has no glyph data."""
    if not data.startswith(b"PHXTS"):
        return []
    return [
        f"the text style draws with {guid}, a localized font without glyph data (no shipped style does)"
        for field, _, guid in refs
        if field == "LocalizedFont" and not _has_glyph_data(mod, guid, schema)
    ]


def missing_refs(mod: Path, has_base: Callable[[str], bool], schema: dict | None = None) -> list[tuple[str, str]]:
    """(file, message) for every reference of a generated file of ``mod`` that neither the mod nor 117 has."""
    folder = mod / phoenix.GENERATED
    if not folder.is_dir():
        return []
    schema = schema or phoenix.load_schema()
    found = []
    for path in sorted(p for p in folder.iterdir() if p.is_file()):
        data = path.read_bytes()
        try:
            refs = references(phoenixread.load(data, schema).log)
            found += [(f"{phoenix.GENERATED}{path.name}", m) for m in _glyphless(mod, data, refs, schema)]
        except (ValueError, EOFError, KeyError, struct.error) as exc:
            found.append((path.name, f"unreadable ({exc})"))
            continue
        seen = set()
        for field, type_hash, guid in refs:
            target = phoenix.GENERATED + guid
            if type_hash in IN_FILE or guid in seen or (mod / target).is_file() or has_base(target):
                continue
            seen.add(guid)
            found.append((f"{phoenix.GENERATED}{path.name}", f"{field} references {guid} ({type_hash}): no such file"))
    return found
