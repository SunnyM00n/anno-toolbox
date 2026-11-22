"""Anno 1701's effect files (``.efc``) as an editable text that writes back byte for byte: its lines, and the blocks
and fields they make.

``CBraceDoc.read(data)`` is the entry; ``tree()`` gives the ``Effect`` block's ``SGroup`` of ``SEntry`` lines and nested
groups (each holding the ``SLine`` objects it is made of), ``to_bytes()`` the file. A change replaces a line's text
(``field_text``) or a run of lines (``replace_lines``); the reader of the values is ``efc1701``.
docs/formats/anno1701-effects.md, docs/particle-editor.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .efc1701 import CEfcError

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r|$)")
_VALUE = re.compile(r'"[^"]*"|\S+')
_INDENT = re.compile(r"[ \t]*")


@dataclass(eq=False)
class SLine:
    """One line of the file: its text without its end, and the end as written (CRLF, LF or none at the end)."""

    text: str
    end: str = "\r\n"


@dataclass(eq=False)
class SEntry:
    """A field: its line, its name and its values as written (a string keeps its quotes)."""

    line: SLine
    name: str
    values: list[str]


@dataclass(eq=False)
class SGroup:
    """A block: the line with its name, the lines of its braces, and what it holds in file order."""

    name: str
    head: SLine | None = None
    open: SLine | None = None
    close: SLine | None = None
    items: list[SEntry | SGroup] = field(default_factory=list)

    def entries(self, name: str = "") -> list[SEntry]:
        return [i for i in self.items if isinstance(i, SEntry) and (not name or i.name == name)]

    def groups(self, name: str = "") -> list[SGroup]:
        return [i for i in self.items if isinstance(i, SGroup) and (not name or i.name == name)]

    def lines(self) -> list[SLine]:
        """Every line of the block, its name and braces included, in file order."""
        out = [x for x in (self.head, self.open) if x is not None]
        for item in self.items:
            out += item.lines() if isinstance(item, SGroup) else [item.line]
        return out + ([self.close] if self.close is not None else [])


class CBraceDoc:
    """The lines of a 1701 effect file; ``tree()`` is parsed again after each change."""

    def __init__(self, lines: list[SLine]) -> None:
        self.m_lines = lines
        self._m_tree: SGroup | None = None

    @classmethod
    def read(cls, data: bytes) -> CBraceDoc:
        """The file's lines (Latin-1); raises ``CEfcError`` when its braces do not hold together."""
        text = data.decode("latin-1")
        lines = []
        for match in _LINE.finditer(text):
            raw = match.group(0)
            if not raw:
                break
            body = raw.rstrip("\r\n")
            lines.append(SLine(body, raw[len(body) :]))
        document = cls(lines)
        document.tree()
        return document

    def to_bytes(self) -> bytes:
        return "".join(line.text + line.end for line in self.m_lines).encode("latin-1")

    def tree(self) -> SGroup:
        """The file as blocks: the root holds the top-level blocks (one ``Effect``)."""
        if self._m_tree is None:
            self._m_tree = _parse(self.m_lines)
        return self._m_tree

    def changed(self) -> None:
        """Say that lines were changed (the tree is parsed again when asked)."""
        self._m_tree = None

    def index(self, line: SLine) -> int:
        return next(i for i, x in enumerate(self.m_lines) if x is line)


def _parse(lines: list[SLine]) -> SGroup:
    root = SGroup("")
    stack = [root]
    pending: SEntry | None = None
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        if text == "{":
            if pending is None or pending.values:
                raise CEfcError("a brace without a block name")
            parent = stack[-1]
            parent.items.remove(pending)
            group = SGroup(pending.name, pending.line, line)
            parent.items.append(group)
            stack.append(group)
            pending = None
        elif text == "}":
            if len(stack) == 1:
                raise CEfcError("a closing brace too many")
            stack.pop().close = line
            pending = None
        else:
            name, *values = _VALUE.findall(text)
            pending = SEntry(line, name, values)
            stack[-1].items.append(pending)
    if len(stack) != 1:
        raise CEfcError("a block is not closed")
    return root


def field_text(entry: SEntry, values: list[str]) -> str:
    """The line of ``entry`` with ``values`` in place of its own: its indent and the space after its name kept."""
    text = entry.line.text
    indent = _INDENT.match(text).group(0)
    rest = text[len(indent) + len(entry.name) :]
    gap = rest[: len(rest) - len(rest.lstrip())] or " "
    return f"{indent}{entry.name}{gap}{' '.join(values)}" if values else f"{indent}{entry.name}"


def indent_of(line: SLine) -> str:
    return _INDENT.match(line.text).group(0)


def number(value: float) -> str:
    """A number as the files write it: six decimals."""
    text = f"{value:.6f}"
    return "0.000000" if text == "-0.000000" else text
