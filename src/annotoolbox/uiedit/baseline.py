"""The preview's baseline: the values the game really shows, derived from layout dumps of many sessions and shipped as
``data/ui-baseline-117.json`` (references and plain values only: a text id, a picture path, a flag).

Per widget file and widget (its name CRC), and per placing context (the container that places the file) where that
changes the value, the file holds the value the game held most often, with its share. ``CBaseline.apply`` hands the
layout what replaces a value the preview would make up: a label's filler text, an image's stand-in picture, a
visibility a binding computed from made-up flags. Live values win over it; a value that does not resolve on this
install (a text id or picture the game does not have) is skipped. docs/ui-editor.md, "The baseline".
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from . import pictures, placeholders, previewdefaults

DATA = Path(__file__).parent / "data" / "ui-baseline-117.json"
VISIBLE, IMAGE_ID, TEXT = (0x80000007,), (0x8000002A,), (0x80000008,)  # the built-in properties it fills (``layout``)
BASELINE_TEXT = (0xBA5E1E01,)  # a label's baseline text, handed to ``labels.label_text`` among its values


class CBaseline:
    """The shipped values; ``for_install`` binds them to an install (which texts and pictures it has)."""

    def __init__(self, data: dict, files=None) -> None:
        self.m_data, self.m_files = data, files
        self.m_entries: dict[str, dict] = data.get("files", {})
        self.m_contexts: dict[str, dict] = data.get("contexts", {})
        self.m_exists: dict[str, bool] = {}  # picture path -> whether the install has it

    @property
    def build(self) -> int:
        return int(self.m_data.get("game_build") or 0)

    def counts(self) -> dict:
        return dict(self.m_data.get("counts") or {})

    def for_install(self, files) -> CBaseline:
        """The same values over the files of an install."""
        return CBaseline(self.m_data, files)

    def entry(self, file: str, crc: str, context: str = "") -> dict:
        """What the game held for the widget ``crc`` of the widget file ``file``, as the placing ``context`` changes
        it: ``{field: [value, share %]}`` (fields ``t`` text id, ``n`` plain text, ``i`` picture, ``v`` visibility)."""
        found = self.m_entries.get(file, {}).get(crc, {})
        if context:
            found = {**found, **self.m_contexts.get(file, {}).get(context, {}).get(crc, {})}
        return found

    def text(self, ctx, entry: dict) -> str | None:
        """The text the baseline gives, in the language of ``ctx``: a text id's line, a character's name from the
        preview settings' cast, or a plain text (a number, "" for a label the game left empty) as it is; None where
        the install has no line for the id."""
        if "t" in entry:
            return self.m_files.text_of(entry["t"][0], ctx.m_language) if self.m_files is not None else None
        if "r" in entry:
            return _person(ctx, entry["r"][0]).name
        return entry["n"][0] if "n" in entry else None

    def picture(self, ctx, entry: dict) -> str | None:
        """The picture the baseline gives: its path, "" where the game showed none, a character's portrait or logo
        from the preview settings' cast; None where the install has no file for it."""
        if "p" in entry:
            who = _person(ctx, entry["p"][0][:2])
            return who.emblem_icon if entry["p"][0][2] == "emblem" else who.portrait_icon
        held = entry.get("i")
        if not held:
            return None
        value = held[0]
        if value == "":
            return ""
        tree = getattr(self.m_files, "m_tree", None)
        if tree is None:
            return None
        if value not in self.m_exists:
            self.m_exists[value] = any(tree.exists(path) for path in pictures.candidates(value))
        return value if self.m_exists[value] else None

    def apply(self, ctx, file: str, crc: str, context: str, kind: str, values: dict, written: dict) -> dict:
        """What the baseline sets over an instance's ``values`` (``written``: what the scope's bindings wrote into
        it): the label's baseline text where its own is a filler or none, the image's picture where it is a
        stand-in, the visibility a binding wrote. Empty where nothing applies."""
        entry = self.entry(file, crc, context)
        if not entry:
            return {}
        out: dict = {}
        if kind == "label":
            current = values.get(TEXT)
            if current is None or current in placeholders.FILLERS:
                text = self.text(ctx, entry)
                if text is not None:
                    out[BASELINE_TEXT] = text
        elif kind == "image":
            current = values.get(IMAGE_ID)
            if isinstance(current, str) and current.startswith(placeholders.PICTURE):
                shown = self.picture(ctx, entry)
                if shown is not None:
                    out[IMAGE_ID] = shown
        held = entry.get("v")
        if held and VISIBLE in written and isinstance(held[0], bool) and values.get(VISIBLE) != held[0]:
            out[VISIBLE] = held[0]
        return out


def _person(ctx, who: list) -> previewdefaults.SCharacter:
    """The preview settings' character of a role and its place in the role's list (the first where the cast is
    shorter)."""
    cast = previewdefaults.current(ctx).cast(who[0])
    return cast[who[1]] if who[1] < len(cast) else cast[0]


@lru_cache(maxsize=1)
def shipped() -> CBaseline | None:
    """The shipped file, None where it is missing or not this format."""
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return CBaseline(data) if isinstance(data, dict) and data.get("format") == 1 else None
