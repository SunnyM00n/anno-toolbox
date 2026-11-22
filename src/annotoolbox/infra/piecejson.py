"""JSON made and read in pieces, for large documents read off the window's thread.

One ``json`` call holds the interpreter until it returns, and every other thread (the window's) waits for it: 0.2 to
0.35 s for a continental island's 30 MB, four times that on a busy machine. ``dumps`` and ``loads`` give the same
text and the same data as ``json.dumps`` and ``json.loads``, but walk the outer objects and lists in Python and code
only what lies below them in one call, so the other threads get their turns.
"""

from __future__ import annotations

import json
import re

ITEMS = 1024  # list items coded per call
OBJECT_DEPTH = 2  # objects walked at the outer two levels
LIST_DEPTH = 3  # lists at the outer three
_DECODER = json.JSONDecoder()
_SPACE = re.compile(r"[ \t\n\r]*")


def dumps(doc, depth: int = 0) -> str:
    """``json.dumps(doc)``, the same text, made in pieces: the outer objects walked, long lists ``ITEMS`` at a call."""
    if isinstance(doc, dict) and depth < OBJECT_DEPTH:
        return "{" + ", ".join(f"{json.dumps(str(k))}: {dumps(value, depth + 1)}" for k, value in doc.items()) + "}"
    if isinstance(doc, list) and depth < LIST_DEPTH and len(doc) > ITEMS:
        runs = (json.dumps(doc[k : k + ITEMS])[1:-1] for k in range(0, len(doc), ITEMS))
        return "[" + ", ".join(runs) + "]"
    return json.dumps(doc)


def loads(text: str):
    """``json.loads(text)``, read in pieces: the outer objects and lists walked, a call for each value below them
    (any JSON text, not only ``dumps``'s)."""
    value, end = _value(text, _SPACE.match(text, 0).end(), 0)
    if _SPACE.match(text, end).end() != len(text):
        raise ValueError(f"extra data at {end}")
    return value


def _value(text: str, i: int, depth: int):
    """The JSON value at ``i`` and the offset after it."""
    opening = text[i : i + 1]
    if not (opening == "{" and depth < OBJECT_DEPTH or opening == "[" and depth < LIST_DEPTH):
        return _DECODER.raw_decode(text, i)
    out, closing = ({}, "}") if opening == "{" else ([], "]")
    i = _SPACE.match(text, i + 1).end()
    while text[i : i + 1] != closing:
        if opening == "{":
            key, i = _DECODER.raw_decode(text, i)
            i = _SPACE.match(text, i).end()
            if text[i : i + 1] != ":" or not isinstance(key, str):
                raise ValueError(f"a key and ':' expected at {i}")
            out[key], i = _value(text, _SPACE.match(text, i + 1).end(), depth + 1)
        else:
            item, i = _value(text, i, depth + 1)
            out.append(item)
        i = _SPACE.match(text, i).end()
        if text[i : i + 1] == ",":
            i = _SPACE.match(text, i + 1).end()
            if text[i : i + 1] == closing:
                raise ValueError(f"a value expected at {i}")
        elif text[i : i + 1] != closing:
            raise ValueError(f"',' or {closing!r} expected at {i}")
    return out, i + 1
