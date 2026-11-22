"""What a change set changes, and who uses an asset: the diff of an edited asset against the game's, the summary of a
whole change set, and the GUID usage (the assets whose values name a GUID, the game's and the change set's own).
Stdlib only. ``docs/tool-suite.md`` A8.
"""

from __future__ import annotations

from dataclasses import dataclass

from .assetdoc import CAssetDocument
from .values import values_of
from .xmltree import leaves


@dataclass(frozen=True)
class SDifference:
    path: str
    before: str | None  # None: not there
    after: str | None


def _flat(values) -> dict[str, str]:
    """Leaves by path, a repeated name numbered (``Item[2]``) so lists compare item by item."""
    out: dict[str, str] = {}
    for path, text in leaves(values):
        key = "/".join(path)
        number, base = 1, key
        while key in out:
            number += 1
            key = f"{base}[{number}]"
        out[key] = text
    return out


def diff_asset(doc: CAssetDocument, guid: int) -> list[SDifference]:
    """The asset's own values as edited against the game's (a new asset against nothing)."""
    before = _flat(values_of(doc.vanilla(guid))) if doc.vanilla(guid) is not None else {}
    after = _flat(values_of(doc.asset(guid))) if doc.exists(guid) else {}
    return [
        SDifference(path, before.get(path), after.get(path))
        for path in sorted({*before, *after})
        if before.get(path) != after.get(path)
    ]


def summary(doc: CAssetDocument) -> list[str]:
    """One line per asset the change set touches."""
    changes, out = doc.m_changes, []
    for guid in changes.touched():
        name = doc.name(guid) or "?"
        if guid in changes.m_removed:
            out.append(f"- {guid} {name}: removed")
        elif guid in changes.m_new:
            added = changes.m_new[guid]
            how = (
                f"from base {added.base}"
                if added.base
                else f"copy of {added.copy_of}"
                if added.copy_of
                else f"template {added.template}"
            )
            out.append(f"+ {guid} {name}: new ({how})")
        else:
            out.append(f"~ {guid} {name}: {len(diff_asset(doc, guid))} values changed")
    return out + [f"texts {lang}: {len(lang_texts)}" for lang, lang_texts in changes.m_texts.items() if lang_texts]


def usage(doc: CAssetDocument, guid: int) -> list[int]:
    """The assets that name ``guid`` in their values: the game's (without the removed) and the change set's."""
    removed = set(doc.m_changes.m_removed)
    users = [user for user in doc.m_index.references(guid) if user not in removed]
    needle = str(guid)
    for other in doc.m_changes.touched():
        if other in removed or other == guid or other in users or not doc.exists(other):
            continue
        if any(text == needle for _path, text in leaves(values_of(doc.asset(other)))):
            users.append(other)
    return users
