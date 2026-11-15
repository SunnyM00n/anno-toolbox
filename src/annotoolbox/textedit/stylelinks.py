"""Which text style draws a text: the labels of the UI files that name a text id and a text style
(docs/text-editor.md, "Text styles of a text"). No Qt.

``StyleLinks(files, chain).fonts_of(text id)`` is the localized fonts (``StyleFaces.key``) of the styles of the labels
that show the text. A label names its text in ``LocalizationId`` (117: 16 hex digits, 1800: the decimal GUID); most labels get their text from
code, so a text has a link only where the UI file names it. The scan reads every widget file (about 30 seconds for 117), so
the result is kept in the game's cache folder per game build.
"""

from __future__ import annotations

import json

from ..assetedit.gamefiles import GameFiles
from ..formats import phoenix, phoenixedit, phoenixread
from ..infra.buildcache import write_atomic
from ..uiedit import fields
from .fontchain import FontChain

WIDGET_KINDS = (b"WI", b"CP")
FIELD = "LocalizationId"


def text_id(raw: str) -> str:
    """A label's ``LocalizationId`` as the id the text files use: 117's 16 hex digits (``A018 4AB6 6641 96B8``) as the
    signed 64-bit number, 1800's decimal GUID as it is; '' for none."""
    if raw.isdigit() and 0 < len(raw) < 16:
        return raw.lstrip("0")
    digits = raw.replace(" ", "")
    if len(digits) != 16:
        return ""
    try:
        n = int(digits, 16)
    except ValueError:
        return ""
    return str(n - (1 << 64) if n >= 1 << 63 else n)


class StyleLinks:
    def __init__(self, files: GameFiles, chain: FontChain) -> None:
        self.files, self.chain = files, chain
        self._links: dict[str, tuple[str, ...]] | None = None

    @property
    def loaded(self) -> bool:
        return self._links is not None

    def cache_path(self):
        return self.files.cache / f"textstyle-links-{self.files.stamp}.json"

    def load(self) -> dict[str, tuple[str, ...]]:
        """Text id -> localized font keys, from the cache or by reading the widget files (the slow step)."""
        if self._links is None:
            path = self.cache_path()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._links = {k: tuple(v) for k, v in data.items()}
            except (OSError, ValueError, AttributeError):
                self._links = self.scan()
                write_atomic(path, json.dumps(self._links, sort_keys=True).encode("utf-8"))
        return self._links

    def scan(self) -> dict[str, tuple[str, ...]]:
        if not self.chain.available:
            return {}
        fonts = self.style_fonts()
        schema, found = phoenix.load_schema(), {}
        tree = self.files.tree
        for path in tree.under(phoenix.GENERATED):
            data = tree.read(path)
            if data[3:5] not in WIDGET_KINDS or not data.startswith(b"PHX"):
                continue
            try:
                root = phoenixread.load(data, schema).root
            except (ValueError, EOFError, KeyError, IndexError):
                continue
            for node in phoenixedit.walk(root):
                if phoenixedit.get_entry(node, FIELD) is None:
                    continue
                key = text_id(str(fields.get(node, FIELD) or ""))
                font = fonts.get(phoenixedit.get_ref(node, "TextStyle") or "")
                if key and font:
                    found.setdefault(key, set()).add(font)
        return {k: tuple(sorted(v)) for k, v in found.items()}

    def style_fonts(self) -> dict[str, str]:
        """Text style asset id -> the localized font it names."""
        return {
            guid: font
            for guid, style in self.chain._read()[b"TS"].items()
            if (font := fields.get(style, "LocalizedFont"))
        }

    def fonts_of(self, key: str) -> tuple[str, ...]:
        """The localized fonts of the styles that draw the text (empty where no UI file names it); call ``load`` first."""
        return (self._links or {}).get(key, ())
