"""The data files of Anno 1701 that take the place of 117's asset file: ``property.ini`` (objects by GUID with numbered
values), ``balancingtabelle.txt`` (a tab separated table of numbers by GUID) and ``gamevalues.xml`` (game settings), read
and written back byte for byte.

``parse_props`` / ``render_props`` the objects, ``parse_table`` / ``render_table`` the table, ``game_values`` /
``set_game_values`` the settings by path. A file that is not read to its end raises ``ValueError``. Layout and the counts:
``docs/formats/anno1701-property-ini.md``; the asset editor's use: ``docs/asset-editor.md`` "Anno 1701".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TEXT = "latin-1"  # every byte is a character, so what the editor does not touch is written back as it was


@dataclass
class SValue:
    id: int
    type: int  # 6: a whole number (flags and counts), 1: text (numbers too)
    text: str
    count: int = 1  # the third number of the line; 1 in every shipped file


@dataclass
class SBlock:
    """A ``[PROPERTY]`` block: the property number and its values."""

    prop: int
    values: list[SValue] = field(default_factory=list)


@dataclass
class SPropObject:
    guid: int
    name: str
    values: list[SValue] = field(default_factory=list)  # the object's own values (ids 100 to 104)
    blocks: list[SBlock] = field(default_factory=list)


@dataclass
class SProps:
    objects: list[SPropObject] = field(default_factory=list)
    eol: str = "\r\n"
    tail: str = ""  # what follows the last line's end of line (empty in every shipped file)

    def by_guid(self) -> dict[int, SPropObject]:
        return {prop.guid: prop for prop in self.objects}

    def value_types(self) -> dict[int, int]:
        """Value id -> its type (the ids keep one type in the shipped file)."""
        found: dict[int, int] = {}
        for prop_object in self.objects:
            for value in [*prop_object.values, *(value for block in prop_object.blocks for value in block.values)]:
                found.setdefault(value.id, value.type)
        return found


def _value(line: str, number: int) -> SValue:
    parts = line.split(" ", 4)
    try:
        return SValue(int(parts[1]), int(parts[2]), parts[4] if len(parts) > 4 else "", int(parts[3]))
    except (ValueError, IndexError) as error:
        raise ValueError(f"line {number}: not a VALUEID line: {line[:60]!r}") from error


def _count(line: str, number: int) -> int:
    """The number after the key of a ``GUID`` or ``PROPID`` line."""
    try:
        return int(line.split(" ", 1)[1])
    except (ValueError, IndexError) as error:
        raise ValueError(f"line {number}: no number in {line[:60]!r}") from error


def parse_props(data: bytes) -> SProps:
    """The objects of a ``property.ini``; ValueError (with the line) for what does not fit its layout."""
    text = data.decode(TEXT)
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    tail = lines.pop() if lines and lines[-1] == "" else ""
    props = SProps(eol=eol, tail="" if tail == "" and text.endswith(eol) else tail)
    prop_object: SPropObject | None = None
    block: SBlock | None = None
    for number, line in enumerate(lines, 1):
        key = line.split(" ", 1)[0]
        if line == "[OBJECT]":
            prop_object, block = SPropObject(0, ""), None
            props.objects.append(prop_object)
        elif prop_object is None:
            raise ValueError(f"line {number}: {line[:60]!r} comes before the first [OBJECT]")
        elif key == "GUID":
            prop_object.guid = _count(line, number)
        elif key == "NAME":
            prop_object.name = line[5:]
        elif line == "[PROPERTY]":
            continue
        elif key == "PROPID":
            block = SBlock(_count(line, number))
            prop_object.blocks.append(block)
        elif key == "VALUEID":
            (block.values if block is not None else prop_object.values).append(_value(line, number))
        else:
            raise ValueError(f"line {number}: unknown line {line[:60]!r}")
    return props


def render_props(props: SProps) -> bytes:
    """The file's bytes for ``props``; what ``parse_props`` read gives its file back."""
    out: list[str] = []

    def values(row: list[SValue]) -> None:
        out.extend(f"VALUEID {value.id} {value.type} {value.count} {value.text}" for value in row)

    for prop_object in props.objects:
        out += ["[OBJECT]", f"GUID {prop_object.guid}", f"NAME {prop_object.name}"]
        values(prop_object.values)
        for block in prop_object.blocks:
            out += ["[PROPERTY]", f"PROPID {block.prop}"]
            values(block.values)
    return (props.eol.join(out) + props.eol + props.tail).encode(TEXT)


# The balancing table


@dataclass
class STable:
    header: list[str]
    rows: dict[int, list[str]]  # GUID -> the cells (the header's length)
    order: list[int] = field(default_factory=list)  # the GUIDs in file order
    eol: str = "\r\n"
    raw: dict[int, str] = field(default_factory=dict)  # a row's line as the file has it


def parse_table(data: bytes) -> STable:
    """The balancing table; a row shorter than the header is padded with empty cells, a longer one is refused."""
    text = data.decode(TEXT)
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    if lines and lines[-1] == "":
        lines.pop()
    header = lines[0].split("\t") if lines else []
    table = STable(header, {}, [], eol)
    for number, line in enumerate(lines[1:], 2):
        cells = line.split("\t")
        if not cells[0].strip().lstrip("-").isdigit() or len(cells) > len(header):
            raise ValueError(f"line {number}: not a row of the balancing table: {line[:60]!r}")
        guid = int(cells[0])
        table.rows[guid] = cells + [""] * (len(header) - len(cells))
        table.order.append(guid)
        table.raw[guid] = line
    return table


def render_table(table: STable) -> bytes:
    """The table's bytes: a row whose cells are as parsed keeps its line, a changed one is joined again."""
    lines = ["\t".join(table.header)]
    for guid in table.order:
        cells = table.rows[guid]
        raw = table.raw.get(guid)
        same = raw is not None and (raw.split("\t") + [""] * len(cells))[: len(cells)] == cells
        lines.append(raw if same else "\t".join(cells))
    return (table.eol.join(lines) + table.eol).encode(TEXT)


# The game values

COMMENT = re.compile(r"<!--.*?-->", re.S)  # the file has a comment ending in ``--->``: a strict parser refuses it
TAG = re.compile(r"<(/?)([A-Za-z_][\w.-]*)[^>]*?(/?)>")


def _leaves(text: str) -> list[tuple[tuple[str, ...], int, int]]:
    """(path, start, end) of every element without children: the span of its text."""
    blank = COMMENT.sub(lambda match: " " * len(match.group(0)), text)  # the same offsets, no comments
    stack: list[list] = []  # per open element: its name, where its text starts, whether it has a child element
    found = []
    for match in TAG.finditer(blank):
        if match.group(1):
            if stack:
                name, start, has_child = stack.pop()
                if not has_child:
                    found.append((tuple(entry[0] for entry in stack) + (name,), start, match.start()))
            continue
        if stack:
            stack[-1][2] = True
        if not match.group(3):
            stack.append([match.group(2), match.end(), False])
    return found


def game_values(data: bytes) -> dict[tuple[str, ...], str]:
    """Path (below the root) -> text of every setting of ``gamevalues.xml`` whose path is unique."""
    text = data.decode(TEXT)
    found: dict[tuple[str, ...], list[str]] = {}
    for path, start, end in _leaves(text):
        found.setdefault(path[1:], []).append(text[start:end])
    return {path: texts[0] for path, texts in found.items() if len(texts) == 1}


def set_game_values(data: bytes, changes: dict[tuple[str, ...], str]) -> bytes:
    """The file with the settings at ``changes`` (path below the root -> text) set; the rest as it was. ValueError for
    a path the file does not hold once."""
    text = data.decode(TEXT)
    spans: dict[tuple[str, ...], list[tuple[int, int]]] = {}
    for path, start, end in _leaves(text):
        spans.setdefault(path[1:], []).append((start, end))
    for path in changes:
        if len(spans.get(path, [])) != 1:
            raise ValueError(f"gamevalues.xml has no single setting {'/'.join(path)}")
    for path, value in sorted(changes.items(), key=lambda item: -spans[item[0]][0][0]):
        start, end = spans[path][0]
        text = text[:start] + value + text[end:]
    return text.encode(TEXT)
