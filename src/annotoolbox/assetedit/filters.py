"""The asset tree's filters, as ToolOne offered them: by template, and by a value under a constraint (equals, at
least, at most, contains, starts with, one of a set). Stdlib only.

``parse("Building/Upkeep >= 5")`` gives a ``ValueFilter``; ``ValueFilter.matches(doc, guid)`` tests an asset's value
as it resolves through its layers (own, base asset, template, defaults).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .assetdoc import AssetDocument

OPS = ("=", ">=", "<=", "~", "^", "in")  # equals, at least, at most, contains, starts with, one of (a;b;c)
PATTERN = re.compile(r"^\s*(?P<path>[\w/.-]+)\s*(?P<op>>=|<=|=|~|\^|\bin\b)\s*(?P<value>.*?)\s*$")


@dataclass(frozen=True)
class ValueFilter:
    path: tuple[str, ...]
    op: str
    value: str

    def matches(self, doc: AssetDocument, guid: int) -> bool:
        text = doc.layers(guid).lookup(self.path).text
        if self.op == "=":
            return text == self.value
        if self.op == "~":
            return self.value.lower() in text.lower()
        if self.op == "^":
            return text.lower().startswith(self.value.lower())
        if self.op == "in":
            return text in {v.strip() for v in self.value.split(";")}
        try:
            a, b = float(text), float(self.value)
        except ValueError:
            return False
        return a >= b if self.op == ">=" else a <= b


def parse(text: str) -> ValueFilter | None:
    """A filter from ``path op value`` (``OPS``), None when the text is not one."""
    m = PATTERN.match(text)
    if m is None or not m.group("value"):
        return None
    return ValueFilter(tuple(p for p in m.group("path").split("/") if p), m.group("op"), m.group("value"))
