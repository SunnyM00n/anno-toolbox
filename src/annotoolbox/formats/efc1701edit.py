"""Anno 1701's effect files (``.efc``) as an editable text that writes back byte for byte: its lines, and the blocks
and fields they make.

``BraceDoc.read(data)`` is the entry; ``tree()`` gives the ``Effect`` block's ``Group`` of ``Entry`` lines and nested
groups (each holding the ``Line`` objects it is made of), ``to_bytes()`` the file. A change replaces a line's text
(``field_text``) or a run of lines (``replace_lines``); the reader of the values is ``efc1701``.
docs/formats/anno1701-effects.md, docs/particle-editor.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .efc1701 import EfcError

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r|$)")
_VALUE = re.compile(r'"[^"]*"|\S+')
_INDENT = re.compile(r"[ \t]*")


@dataclass(eq=False)
class Line:
    """One line of the file: its text without its end, and the end as written (CRLF, LF or none at the end)."""

    text: str
    end: str = "\r\n"


@dataclass(eq=False)
class Entry:
    """A field: its line, its name and its values as written (a string keeps its quotes)."""

    line: Line
    name: str
    values: list[str]


@dataclass(eq=False)
class Group:
    """A block: the line with its name, the lines of its braces, and what it holds in file order."""

    name: str
    head: Line | None = None
    open: Line | None = None
    close: Line | None = None
    items: list[Entry | Group] = field(default_factory=list)

    def entries(self, name: str = "") -> list[Entry]:
        return [i for i in self.items if isinstance(i, Entry) and (not name or i.name == name)]

    def groups(self, name: str = "") -> list[Group]:
        return [i for i in self.items if isinstance(i, Group) and (not name or i.name == name)]

    def lines(self) -> list[Line]:
        """Every line of the block, its name and braces included, in file order."""
        out = [x for x in (self.head, self.open) if x is not None]
        for item in self.items:
            out += item.lines() if isinstance(item, Group) else [item.line]
        return out + ([self.close] if self.close is not None else [])


class BraceDoc:
    """The lines of a 1701 effect file; ``tree()`` is parsed again after each change."""

    def __init__(self, lines: list[Line]) -> None:
        self.lines = lines
        self._tree: Group | None = None

    @classmethod
    def read(cls, data: bytes) -> BraceDoc:
        """The file's lines (Latin-1); raises ``EfcError`` when its braces do not hold together."""
        text = data.decode("latin-1")
        lines = []
        for m in _LINE.finditer(text):
            raw = m.group(0)
            if not raw:
                break
            body = raw.rstrip("\r\n")
            lines.append(Line(body, raw[len(body) :]))
        doc = cls(lines)
        doc.tree()
        return doc

    def to_bytes(self) -> bytes:
        return "".join(line.text + line.end for line in self.lines).encode("latin-1")

    def tree(self) -> Group:
        """The file as blocks: the root holds the top-level blocks (one ``Effect``)."""
        if self._tree is None:
            self._tree = _parse(self.lines)
        return self._tree

    def changed(self) -> None:
        """Say that lines were changed (the tree is parsed again when asked)."""
        self._tree = None

    def index(self, line: Line) -> int:
        return next(i for i, x in enumerate(self.lines) if x is line)


def _parse(lines: list[Line]) -> Group:
    root = Group("")
    stack = [root]
    pending: Entry | None = None
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if text == "{":
            if pending is None or pending.values:
                raise EfcError("a brace without a block name")
            parent = stack[-1]
            parent.items.remove(pending)
            group = Group(pending.name, pending.line, line)
            parent.items.append(group)
            stack.append(group)
            pending = None
        elif text == "}":
            if len(stack) == 1:
                raise EfcError("a closing brace too many")
            stack.pop().close = line
            pending = None
        else:
            name, *values = _VALUE.findall(text)
            pending = Entry(line, name, values)
            stack[-1].items.append(pending)
    if len(stack) != 1:
        raise EfcError("a block is not closed")
    return root


def field_text(entry: Entry, values: list[str]) -> str:
    """The line of ``entry`` with ``values`` in place of its own: its indent and the space after its name kept."""
    text = entry.line.text
    indent = _INDENT.match(text).group(0)
    rest = text[len(indent) + len(entry.name) :]
    gap = rest[: len(rest) - len(rest.lstrip())] or " "
    return f"{indent}{entry.name}{gap}{' '.join(values)}" if values else f"{indent}{entry.name}"


def indent_of(line: Line) -> str:
    return _INDENT.match(line.text).group(0)


def number(value: float) -> str:
    """A number as the files write it: six decimals."""
    text = f"{value:.6f}"
    return "0.000000" if text == "-0.000000" else text
