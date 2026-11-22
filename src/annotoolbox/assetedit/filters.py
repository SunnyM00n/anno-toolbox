"""The asset tree's filters, as ToolOne offered them: by template, and by a value under a constraint (equals, at
least, at most, contains, starts with, one of a set). Stdlib only.

``parse("Building/Upkeep >= 5")`` gives a ``SValueFilter``; ``SValueFilter.matches(doc, guid)`` tests an asset's value
as it resolves through its layers (own, base asset, template, defaults).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .assetdoc import CAssetDocument

OPS = ("=", ">=", "<=", "~", "^", "in")  # equals, at least, at most, contains, starts with, one of (a;b;c)
PATTERN = re.compile(r"^\s*(?P<path>[\w/.-]+)\s*(?P<op>>=|<=|=|~|\^|\bin\b)\s*(?P<value>.*?)\s*$")


@dataclass(frozen=True)
class SValueFilter:
    path: tuple[str, ...]
    op: str
    value: str

    def matches(self, doc: CAssetDocument, guid: int) -> bool:
        text = doc.layers(guid).lookup(self.path).text
        if self.op == "=":
            return text == self.value
        if self.op == "~":
            return self.value.lower() in text.lower()
        if self.op == "^":
            return text.lower().startswith(self.value.lower())
        if self.op == "in":
            return text in {item.strip() for item in self.value.split(";")}
        try:
            shown, wanted = float(text), float(self.value)
        except ValueError:
            return False
        return shown >= wanted if self.op == ">=" else shown <= wanted


def parse(text: str) -> SValueFilter | None:
    """A filter from ``path op value`` (``OPS``), None when the text is not one."""
    match = PATTERN.match(text)
    if match is None or not match.group("value"):
        return None
    return SValueFilter(
        tuple(part for part in match.group("path").split("/") if part), match.group("op"), match.group("value")
    )
