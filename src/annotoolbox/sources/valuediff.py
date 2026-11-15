"""The value diff of two data trees (dicts, lists, text), field by field: a leaf that differs becomes ``(path, old,
new)``. Lists compare as sets of entries (a reorder is no change), the rest pair by position. Pure functions, no files.

Shared by the version diff (``scripts/data_diff.py``, ``docs/2070-versions.md`` "How the diff compares") and the Mod diff
app (``moddiff/``, ``docs/mod-diff.md``). ``brief`` shortens a value for a report, ``canon`` is the comparable text of
one.
"""

from __future__ import annotations

import json
from collections import Counter

from .assetdb import as_list

LIST_KEYS = ("Item", "i")  # the repeating entries of ToolOne's, 2070's and 117's config lists
BRIEF = 160


def brief(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return text if len(text) <= BRIEF else text[:BRIEF] + "..."


def canon(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def is_list(key: str, old, new) -> bool:
    return key in LIST_KEYS or isinstance(old, list) or isinstance(new, list)


def shown(value, full: bool):
    """A value as it is reported: shortened by ``brief``, or whole (``full``, for a comparison that must tell two long
    values apart by what is reported)."""
    return (value if isinstance(value, str) else canon(value)) if full else brief(value)


def diff_data(old, new, path: str, out: list[tuple], full: bool = False) -> None:
    """Append ``(path, old, new)`` for every leaf that differs between two data trees (dicts, lists, text); the values
    are shortened by ``brief`` unless ``full``."""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            sub = f"{path}/{key}" if path else key
            if key not in old:
                out.append((sub, None, shown(new[key], full)))
            elif key not in new:
                out.append((sub, shown(old[key], full), None))
            elif is_list(key, old[key], new[key]):
                diff_lists(as_list(old[key]), as_list(new[key]), sub, out, full)
            else:
                diff_data(old[key], new[key], sub, out, full)
    elif canon(old) != canon(new):
        out.append((path, shown(old, full), shown(new, full)))


def diff_lists(old: list, new: list, path: str, out: list[tuple], full: bool = False) -> None:
    """Entries present in both lists cancel (a reorder is no change); the rest pair in order, each pair named by the
    new entry's position in the new list (``Item[3]``, as a set value names it)."""
    balance = Counter(canon(x) for x in old)
    balance.subtract(Counter(canon(x) for x in new))
    gone, came = _unmatched(old, balance, 1), _unmatched(new, balance, -1)
    for (_i, a), (j, b) in zip(gone, came, strict=False):
        diff_data(a, b, f"{path}[{j}]", out, full)
    for _i, a in gone[len(came) :]:
        out.append((f"{path}[-]", shown(a, full), None))
    for _j, b in came[len(gone) :]:
        out.append((f"{path}[+]", None, shown(b, full)))


def _unmatched(items: list, balance: Counter, sign: int) -> list[tuple[int, object]]:
    """``(position, entry)`` of the entries of ``items`` the other list lacks: ``balance`` counts old minus new,
    ``sign`` picks the side."""
    spare = Counter({k: v * sign for k, v in balance.items() if v * sign > 0})
    out = []
    for k, item in enumerate(items):
        key = canon(item)
        if spare[key] > 0:
            spare[key] -= 1
            out.append((k, item))
    return out
