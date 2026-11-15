"""What a change set changes, and who uses an asset: the diff of an edited asset against the game's, the summary of a
whole change set, and the GUID usage (the assets whose values name a GUID, the game's and the change set's own).
Stdlib only. ``docs/tool-suite.md`` A8.
"""

from __future__ import annotations

from dataclasses import dataclass

from .assetdoc import AssetDocument
from .values import values_of
from .xmltree import leaves


@dataclass(frozen=True)
class Difference:
    path: str
    before: str | None  # None: not there
    after: str | None


def _flat(values) -> dict[str, str]:
    """Leaves by path, a repeated name numbered (``Item[2]``) so lists compare item by item."""
    out: dict[str, str] = {}
    for path, text in leaves(values):
        key = "/".join(path)
        n, base = 1, key
        while key in out:
            n += 1
            key = f"{base}[{n}]"
        out[key] = text
    return out


def diff_asset(doc: AssetDocument, guid: int) -> list[Difference]:
    """The asset's own values as edited against the game's (a new asset against nothing)."""
    before = _flat(values_of(doc.vanilla(guid))) if doc.vanilla(guid) is not None else {}
    after = _flat(values_of(doc.asset(guid))) if doc.exists(guid) else {}
    return [
        Difference(p, before.get(p), after.get(p)) for p in sorted({*before, *after}) if before.get(p) != after.get(p)
    ]


def summary(doc: AssetDocument) -> list[str]:
    """One line per asset the change set touches."""
    c, out = doc.changes, []
    for guid in c.touched():
        name = doc.name(guid) or "?"
        if guid in c.removed:
            out.append(f"- {guid} {name}: removed")
        elif guid in c.new:
            r = c.new[guid]
            how = f"from base {r.base}" if r.base else f"copy of {r.copy_of}" if r.copy_of else f"template {r.template}"
            out.append(f"+ {guid} {name}: new ({how})")
        else:
            out.append(f"~ {guid} {name}: {len(diff_asset(doc, guid))} values changed")
    return out + [f"texts {lang}: {len(t)}" for lang, t in c.texts.items() if t]


def usage(doc: AssetDocument, guid: int) -> list[int]:
    """The assets that name ``guid`` in their values: the game's (without the removed) and the change set's."""
    removed = set(doc.changes.removed)
    users = [u for u in doc.index.references(guid) if u not in removed]
    needle = str(guid)
    for other in doc.changes.touched():
        if other in removed or other == guid or other in users or not doc.exists(other):
            continue
        if any(text == needle for _path, text in leaves(values_of(doc.asset(other)))):
            users.append(other)
    return users
