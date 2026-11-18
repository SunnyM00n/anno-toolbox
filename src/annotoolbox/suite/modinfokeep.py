"""An imported mod's ``modinfo`` kept as the author wrote it (docs/mod-import.md, section 3.2). Stdlib only.

``patched(original, changes, game)``: the original bytes when nothing changed, else the original text with only the
edited fields (``FIELDS``: ``ModID``, ``Version``, ``ModName``, ``Category``, ``Description``, the five dependency
kinds) replaced in place, so ``.jsonc`` comments and layout stay. A dependency kind is written where the original
keeps it (117's ``Dependencies``, 1800's top-level lists), a new one in the shape of ``game``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..modloader.mods import DEPENDENCY_KINDS

# 1800's top-level list of each dependency kind (``mods.mod_from_info`` reads both shapes)
LEGACY_KEYS = {
    "Require": "ModDependencies",
    "Optional": "OptionalDependencies",
    "LoadAfter": "LoadAfterIds",
    "Incompatible": "IncompatibleIds",
    "Deprecate": "DeprecateIds",
}
TEXT_FIELDS = {"ModName": "name", "Category": "category", "Description": "description"}  # modinfo field -> project
FIELDS = ("ModID", "Version", *TEXT_FIELDS, *DEPENDENCY_KINDS)
INDENT = "  "


class ModinfoError(ValueError):
    """The text is not a JSON object the patch can read."""


@dataclass
class _Member:
    key: str
    start: int  # the key's first character (its quote)
    value: int  # where the value begins
    end: int  # one past the value


class _Text:
    """Positions in JSON text with comments: just enough scanning to find a member's value and replace it."""

    def __init__(self, text: str) -> None:
        self.text = text

    def skip(self, i: int) -> int:
        """Past whitespace and comments."""
        t, n = self.text, len(self.text)
        while i < n:
            if t[i] in " \t\r\n﻿":
                i += 1
            elif t.startswith("//", i):
                j = t.find("\n", i)
                i = n if j < 0 else j + 1
            elif t.startswith("/*", i):
                j = t.find("*/", i + 2)
                i = n if j < 0 else j + 2
            else:
                break
        return i

    def string_end(self, i: int) -> int:
        """One past the string whose opening quote is at ``i``."""
        t, n = self.text, len(self.text)
        j = i + 1
        while j < n and t[j] != '"':
            j += 2 if t[j] == "\\" else 1
        if j >= n:
            raise ModinfoError("a string is not closed")
        return j + 1

    def value_end(self, i: int) -> int:
        """One past the value that begins at ``i``."""
        t, n = self.text, len(self.text)
        if i >= n:
            raise ModinfoError("a value is missing")
        if t[i] == '"':
            return self.string_end(i)
        if t[i] in "{[":
            depth, j = 0, i
            while j < n:
                c = t[j]
                if c == '"':
                    j = self.string_end(j)
                    continue
                if t.startswith(("//", "/*"), j):
                    j = self.skip(j)
                    continue
                depth += c in "{["
                depth -= c in "}]"
                j += 1
                if depth == 0:
                    return j
            raise ModinfoError("an object or list is not closed")
        j = i
        while j < n and t[j] not in ",}] \t\r\n/":
            j += 1
        return j

    def members(self, obj: int) -> tuple[list[_Member], int]:
        """The members of the object whose ``{`` is at ``obj``, and the position of its ``}``."""
        t = self.text
        if t[obj] != "{":
            raise ModinfoError("an object was expected")
        out: list[_Member] = []
        i = self.skip(obj + 1)
        while i < len(t) and t[i] != "}":
            if t[i] != '"':
                raise ModinfoError("a member name was expected")
            key_end = self.string_end(i)
            key = json.loads(t[i:key_end])
            colon = self.skip(key_end)
            if t[colon : colon + 1] != ":":
                raise ModinfoError("a colon was expected")
            value = self.skip(colon + 1)
            end = self.value_end(value)
            out.append(_Member(key, i, value, end))
            i = self.skip(end)
            if t[i : i + 1] == ",":
                i = self.skip(i + 1)
        if i >= len(t):
            raise ModinfoError("an object is not closed")
        return out, i

    def line_indent(self, pos: int) -> str:
        """The whitespace that begins the line ``pos`` is on."""
        begin = self.text.rfind("\n", 0, pos) + 1
        i = begin
        while i < pos and self.text[i] in " \t":
            i += 1
        return self.text[begin:i]


def _render(value, indent: str, multiline: bool) -> str:
    """A value as JSON text: a list on one line unless the one it replaces spread over several."""
    if isinstance(value, list) and multiline and value:
        inner = ",\n".join(f"{indent}{INDENT}{json.dumps(v, ensure_ascii=False)}" for v in value)
        return f"[\n{inner}\n{indent}]"
    return json.dumps(value, ensure_ascii=False)


def _nested(path: tuple[str, ...], value):
    for key in reversed(path):
        value = {key: value}
    return value


def _set(doc: _Text, obj: int, path: tuple[str, ...], value) -> str:
    """The text with the member at ``path`` below the object at ``obj`` set to ``value`` (made when missing)."""
    members, close = doc.members(obj)
    found = next((m for m in members if m.key == path[0]), None)
    t = doc.text
    if found is not None and len(path) > 1 and t[found.value] == "{":
        return _set(doc, found.value, path[1:], value)  # positions are absolute: the whole new text comes back
    nl = "\r\n" if "\r\n" in t else "\n"
    if found is not None:
        indent = doc.line_indent(found.start)
        text = _render(_nested(path[1:], value), indent, "\n" in t[found.value : found.end])
        return t[: found.value] + text + t[found.end :]
    base = doc.line_indent(members[-1].start) if members else doc.line_indent(obj) + INDENT
    member = f"{json.dumps(path[0])}: {_render(_nested(path[1:], value), base, isinstance(value, list))}"
    if members:
        return t[: members[-1].end] + f",{nl}{base}{member}" + t[members[-1].end :]
    return t[: obj + 1] + f"{nl}{base}{member}{nl}{doc.line_indent(obj)}" + t[close:]


def _where(doc: _Text, root: int, kind: str, game: str) -> tuple[str, ...]:
    """Where the original keeps the dependency kind (either shape), else where ``game`` keeps it."""
    members, _ = doc.members(root)
    top = {m.key: m for m in members}
    if LEGACY_KEYS[kind] in top:
        return (LEGACY_KEYS[kind],)
    deps = top.get("Dependencies")
    if deps is not None and doc.text[deps.value] == "{":
        if any(m.key == kind for m in doc.members(deps.value)[0]):
            return ("Dependencies", kind)
    return (LEGACY_KEYS[kind],) if game == "anno1800" else ("Dependencies", kind)


def _text_path(doc: _Text, root: int, field: str) -> tuple[str, ...]:
    """A localized field: the member itself when it holds one string, else its English entry."""
    found = next((m for m in doc.members(root)[0] if m.key == field), None)
    if found is not None and doc.text[found.value] == '"':
        return (field,)
    return (field, "English")


def patched(original: bytes, changes: dict[str, object], game: str) -> bytes:
    """``original`` with the fields of ``changes`` (``FIELDS`` -> new value: a string, or a list of ModIDs for a
    dependency kind) set in place; the same bytes when there is nothing to change."""
    if not changes:
        return original
    text = original.decode("utf-8")
    doc = _Text(text)
    root = doc.skip(0)
    for field, value in changes.items():
        if field not in FIELDS:
            raise ModinfoError(f"{field} is not a field the project patches")
        if field in DEPENDENCY_KINDS:
            path = _where(doc, root, field, game)
        elif field in TEXT_FIELDS:
            path = _text_path(doc, root, field)
        else:
            path = (field,)
        text = _set(doc, root, path, value)
        doc = _Text(text)
        root = doc.skip(0)
    return text.encode("utf-8")
