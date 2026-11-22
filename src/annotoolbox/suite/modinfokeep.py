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


class CModinfoError(ValueError):
    """The text is not a JSON object the patch can read."""


@dataclass
class _SMember:
    key: str
    start: int  # the key's first character (its quote)
    value: int  # where the value begins
    end: int  # one past the value


class _CText:
    """Positions in JSON text with comments: just enough scanning to find a member's value and replace it."""

    def __init__(self, text: str) -> None:
        self.m_text = text

    def skip(self, i: int) -> int:
        """Past whitespace and comments."""
        text, length = self.m_text, len(self.m_text)
        while i < length:
            if text[i] in " \t\r\n﻿":
                i += 1
            elif text.startswith("//", i):
                j = text.find("\n", i)
                i = length if j < 0 else j + 1
            elif text.startswith("/*", i):
                j = text.find("*/", i + 2)
                i = length if j < 0 else j + 2
            else:
                break
        return i

    def string_end(self, i: int) -> int:
        """One past the string whose opening quote is at ``i``."""
        text, length = self.m_text, len(self.m_text)
        j = i + 1
        while j < length and text[j] != '"':
            j += 2 if text[j] == "\\" else 1
        if j >= length:
            raise CModinfoError("a string is not closed")
        return j + 1

    def value_end(self, i: int) -> int:
        """One past the value that begins at ``i``."""
        text, length = self.m_text, len(self.m_text)
        if i >= length:
            raise CModinfoError("a value is missing")
        if text[i] == '"':
            return self.string_end(i)
        if text[i] in "{[":
            depth, j = 0, i
            while j < length:
                character = text[j]
                if character == '"':
                    j = self.string_end(j)
                    continue
                if text.startswith(("//", "/*"), j):
                    j = self.skip(j)
                    continue
                depth += character in "{["
                depth -= character in "}]"
                j += 1
                if depth == 0:
                    return j
            raise CModinfoError("an object or list is not closed")
        j = i
        while j < length and text[j] not in ",}] \t\r\n/":
            j += 1
        return j

    def members(self, object_start: int) -> tuple[list[_SMember], int]:
        """The members of the object whose ``{`` is at ``obj``, and the position of its ``}``."""
        text = self.m_text
        if text[object_start] != "{":
            raise CModinfoError("an object was expected")
        out: list[_SMember] = []
        i = self.skip(object_start + 1)
        while i < len(text) and text[i] != "}":
            if text[i] != '"':
                raise CModinfoError("a member name was expected")
            key_end = self.string_end(i)
            key = json.loads(text[i:key_end])
            colon = self.skip(key_end)
            if text[colon : colon + 1] != ":":
                raise CModinfoError("a colon was expected")
            value = self.skip(colon + 1)
            end = self.value_end(value)
            out.append(_SMember(key, i, value, end))
            i = self.skip(end)
            if text[i : i + 1] == ",":
                i = self.skip(i + 1)
        if i >= len(text):
            raise CModinfoError("an object is not closed")
        return out, i

    def line_indent(self, position: int) -> str:
        """The whitespace that begins the line ``pos`` is on."""
        begin = self.m_text.rfind("\n", 0, position) + 1
        i = begin
        while i < position and self.m_text[i] in " \t":
            i += 1
        return self.m_text[begin:i]


def _render(value, indent: str, multiline: bool) -> str:
    """A value as JSON text: a list on one line unless the one it replaces spread over several."""
    if isinstance(value, list) and multiline and value:
        inner = ",\n".join(f"{indent}{INDENT}{json.dumps(item, ensure_ascii=False)}" for item in value)
        return f"[\n{inner}\n{indent}]"
    return json.dumps(value, ensure_ascii=False)


def _nested(path: tuple[str, ...], value):
    for key in reversed(path):
        value = {key: value}
    return value


def _set(doc: _CText, object_start: int, path: tuple[str, ...], value) -> str:
    """The text with the member at ``path`` below the object at ``obj`` set to ``value`` (made when missing)."""
    members, close = doc.members(object_start)
    found = next((candidate for candidate in members if candidate.key == path[0]), None)
    document_text = doc.m_text
    if found is not None and len(path) > 1 and document_text[found.value] == "{":
        return _set(doc, found.value, path[1:], value)  # positions are absolute: the whole new text comes back
    newline = "\r\n" if "\r\n" in document_text else "\n"
    if found is not None:
        indent = doc.line_indent(found.start)
        text = _render(_nested(path[1:], value), indent, "\n" in document_text[found.value : found.end])
        return document_text[: found.value] + text + document_text[found.end :]
    base = doc.line_indent(members[-1].start) if members else doc.line_indent(object_start) + INDENT
    member = f"{json.dumps(path[0])}: {_render(_nested(path[1:], value), base, isinstance(value, list))}"
    if members:
        return document_text[: members[-1].end] + f",{newline}{base}{member}" + document_text[members[-1].end :]
    return (
        document_text[: object_start + 1]
        + f"{newline}{base}{member}{newline}{doc.line_indent(object_start)}"
        + document_text[close:]
    )


def _where(doc: _CText, root: int, kind: str, game: str) -> tuple[str, ...]:
    """Where the original keeps the dependency kind (either shape), else where ``game`` keeps it."""
    members, _ = doc.members(root)
    top = {member.key: member for member in members}
    if LEGACY_KEYS[kind] in top:
        return (LEGACY_KEYS[kind],)
    deps = top.get("Dependencies")
    if deps is not None and doc.m_text[deps.value] == "{":
        if any(member.key == kind for member in doc.members(deps.value)[0]):
            return ("Dependencies", kind)
    return (LEGACY_KEYS[kind],) if game == "anno1800" else ("Dependencies", kind)


def _text_path(doc: _CText, root: int, field: str) -> tuple[str, ...]:
    """A localized field: the member itself when it holds one string, else its English entry."""
    found = next((member for member in doc.members(root)[0] if member.key == field), None)
    if found is not None and doc.m_text[found.value] == '"':
        return (field,)
    return (field, "English")


def patched(original: bytes, changes: dict[str, object], game: str) -> bytes:
    """``original`` with the fields of ``changes`` (``FIELDS`` -> new value: a string, or a list of ModIDs for a
    dependency kind) set in place; the same bytes when there is nothing to change."""
    if not changes:
        return original
    text = original.decode("utf-8")
    doc = _CText(text)
    root = doc.skip(0)
    for field, value in changes.items():
        if field not in FIELDS:
            raise CModinfoError(f"{field} is not a field the project patches")
        if field in DEPENDENCY_KINDS:
            path = _where(doc, root, field, game)
        elif field in TEXT_FIELDS:
            path = _text_path(doc, root, field)
        else:
            path = (field,)
        text = _set(doc, root, path, value)
        doc = _CText(text)
        root = doc.skip(0)
    return text.encode("utf-8")
