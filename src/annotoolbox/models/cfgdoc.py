"""A model config, info or feedback file of any game (``.cfg``, ``.mcf``, ``.ifo``, ``.fc``, ``.prp``) as an editable
document that writes back byte for byte: its parts and fields, and the edits that change it, each one undoable.

``Doc.read(data, path)`` is the entry; ``parts`` and ``fields`` are what an editor lists, ``set_value``, ``insert``,
``remove``, ``move`` and ``duplicate`` return an ``Edit`` (``do`` / ``undo``). The tree is ``formats.isd2070``'s, which
keeps the white space around every tag, so what was not touched keeps its bytes. docs/tool-suite.md section 23.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import isd2070
from ..formats.isd2070 import Node

CONFIG_TAGS = ("Config", "m_Config")
KIND_TAGS = ("ConfigType", "m_ConfigType")
VECTOR_PARTS = frozenset("x y z w xf yf zf wf r g b a Direction".split())  # a value block shown as fields


@dataclass
class Edit:
    """One change of a document: ``do`` makes it, ``undo`` takes it back (both may run again and again)."""

    label: str
    do: Callable[[], None]
    undo: Callable[[], None]


@dataclass
class Part:
    """A node with children as an editor lists it: its ``kind`` (a config's ``ConfigType``, else the node's name),
    the list it sits in and its place there, a ``path`` stable for the viewer (``Models/0/Materials/2``, the paths
    of ``models.configtree``), and its sub-parts."""

    node: Node
    parent: Node | None
    kind: str
    section: str = ""
    index: int = 0
    path: str = ""
    children: list[Part] = field(default_factory=list)
    is_list: bool = False  # a list of configs (``Models``): its parts are its children

    @property
    def name(self) -> str:
        """The part's ``Name`` or file name, for its label."""
        for child in self.node.children:
            if strip(child.name) in ("Name", "FileName") and child.text:
                return shown(child.text).replace("\\", "/").rsplit("/", 1)[-1]
        return ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class Field:
    """A value of a part: its name as the editor shows it (a value block's ``Position.x``), the leaf node that holds
    it, the heading it stands under (117's materials), and whether it is binary (kept as bytes)."""

    name: str
    node: Node
    group: str = ""

    @property
    def binary(self) -> bool:
        return self.node.data is not None

    @property
    def value(self) -> str:
        if self.node.data is not None:
            return self.node.data.hex(" ")
        return shown(self.node.text or "")


BARE_AMPERSAND = re.compile(
    r"&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z]\w*;)"
)  # an & that starts no entity: it breaks a config


def strip(name: str) -> str:
    """A tag without 2070's and 1404's ``m_`` prefix."""
    return name[2:] if name.startswith("m_") else name


def shown(text: str) -> str:
    """A value as the editor shows it: the tree holds bytes as Latin-1, the games write UTF-8 (or plain ASCII)."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def number_text(value: float, decimals: int = 4) -> str:
    """A number as a config holds it: ``decimals`` at most, no trailing zeros, no ``-0``."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def stored(value: str) -> str:
    """A value typed in the editor as the tree holds it (UTF-8 bytes as Latin-1)."""
    return value.encode("utf-8").decode("latin-1")


def is_config_tag(tag: str) -> bool:
    """Whether a tag is a config's (``Config``, 2070's ``m_Config``)."""
    return tag in CONFIG_TAGS


def is_config(node: Node) -> bool:
    return is_config_tag(node.name)


def kind_of(node: Node) -> str:
    """A config's ``ConfigType`` (``MODEL``, ``MATERIAL`` ...), 1701's ``m_MCClassType``, else the node's name."""
    for child in node.children:
        if child.name in KIND_TAGS and child.text:
            return child.text.strip()
    if node.name == "m_MeshConfig" or node.name.startswith("m_Mesh"):
        found = next((c.text for c in node.children if c.name == "m_MCClassType" and c.text), None)
        if found is not None:
            return f"{strip(node.name)} {found.strip()}"
    return strip(node.name)


def is_heading(node: Node) -> bool:
    """A heading of 117's materials (``<Common [ModelCommon]>``): a field that only names the group after it."""
    return "[" in node.name and not node.children


def is_vector(node: Node) -> bool:
    """A small value block (a position, a colour, a rotation) shown as fields of its part, not as a part."""
    return bool(node.children) and all(not c.children and c.name in VECTOR_PARTS for c in node.children)


def is_part(node: Node) -> bool:
    return bool(node.children) and not is_vector(node)


@dataclass
class Doc:
    """One file as its tree. ``exact`` is False for a file that does not read back to its bytes (it is then shown,
    not saved); ``newline`` is the file's line end, for parts made new."""

    path: str
    root: Node
    data: bytes
    exact: bool = True
    newline: bytes = b"\r\n"

    @classmethod
    def read(cls, data: bytes, path: str = "") -> Doc:
        """A file's document; raises ``ValueError`` for bytes that are no tagged text."""
        try:
            root = isd2070.parse(data)
        except (UnicodeDecodeError, IndexError, ValueError) as exc:
            raise ValueError(f"{path or 'the file'} is no config: {exc}") from exc
        newline = b"\r\n" if b"\r\n" in data[:4096] or b"\n" not in data[:4096] else b"\n"
        return cls(path, root, data, isd2070.serialize(root) == data, newline)

    def to_bytes(self) -> bytes:
        return isd2070.serialize(self.root)

    @property
    def changed(self) -> bool:
        return self.to_bytes() != self.data

    def parts(self) -> Part:
        """The file as parts: its one top element (a config's ``Config``, an info file's ``Info``) with the path ""
        as ``models.configtree`` gives it, or the document when it has several (a feedback file); below, every node
        with children."""
        tops = [c for c in self.root.children if is_part(c)]
        if len(tops) == 1:
            return Part(tops[0], self.root, kind_of(tops[0]), children=_parts(tops[0], ""))
        return Part(self.root, None, self.path.rsplit("/", 1)[-1] or "document", children=_parts(self.root, ""))

    def find(self, path: str) -> Part | None:
        return next((p for p in self.parts().walk() if p.path == path), None)

    def parent_of(self, node: Node) -> Node | None:
        return next((n for n in self.root.iter() if any(c is node for c in n.children)), None)


def _parts(node: Node, path: str) -> list[Part]:
    """The parts below a node: a list of configs (``Models``) gives each config the path ``Models/k`` as the viewer
    counts them; any other node with children is ``Name#k`` (k among the siblings of that name)."""
    out, seen = [], {}
    for child in node.children:
        if not is_part(child):
            continue
        if all(is_config(c) for c in child.children):
            section = strip(child.name)
            folder = Part(child, node, section, section, 0, f"{path}/{section}".lstrip("/"), is_list=True)
            for k, sub in enumerate(child.children):
                sub_path = f"{folder.path}/{k}"
                part = Part(sub, child, kind_of(sub), section, k, sub_path, _parts(sub, sub_path))
                folder.children.append(part)
            out.append(folder)
            continue
        k = seen[child.name] = seen.get(child.name, -1) + 1
        own = f"{path}/{strip(child.name)}#{k}".lstrip("/")
        out.append(Part(child, node, kind_of(child), strip(node.name), k, own, _parts(child, own)))
    return out


def fields(node: Node) -> list[Field]:
    """A part's values in file order: its leaves, and the leaves of its value blocks (``Position.x``); a field after
    one of 117's headings carries that heading as its group."""
    out, group = [], ""
    for child in node.children:
        if is_heading(child):
            group = child.name.split(" [", 1)[0]
            continue
        if not child.children:
            out.append(Field(child.name, child, group))
        elif is_vector(child):
            out += [Field(f"{child.name}.{c.name}", c, group) for c in child.children]
    return out


# Edits


def set_value(leaf: Node, value: str, label: str = "") -> Edit:
    """A field set to ``value`` (as typed); a binary field takes hex digits."""
    if leaf.data is not None:
        new_data, old_data = bytes.fromhex(value), leaf.data

        def put(data: bytes) -> Callable[[], None]:
            return lambda: setattr(leaf, "data", data)

        return Edit(label or f"{leaf.name} = {len(new_data)} bytes", put(new_data), put(old_data))
    old = (leaf.text, leaf.trail)
    text = stored(value) if value != "" else None

    def put_text(t: str | None, trail: bytes) -> Callable[[], None]:
        def run() -> None:
            leaf.text, leaf.trail = t, trail

        return run

    return Edit(label or f"{leaf.name} = {value}", put_text(text, old[1] if text is not None else b""), put_text(*old))


def insert(parent: Node, index: int, nodes: list[Node], label: str = "") -> Edit:
    """``nodes`` put into ``parent``'s children at ``index`` (an empty list, ``<Files></Files>``, takes them too)."""
    nodes = list(nodes)

    def do() -> None:
        parent.children[index:index] = nodes

    def undo() -> None:
        for n in nodes:
            parent.children[:] = [c for c in parent.children if c is not n]

    return Edit(label or f"add {', '.join(kind_of(n) for n in nodes)}", do, undo)


def remove(parent: Node, node: Node, label: str = "") -> Edit:
    index = next(i for i, c in enumerate(parent.children) if c is node)

    def do() -> None:
        del parent.children[index]

    def undo() -> None:
        parent.children.insert(index, node)

    return Edit(label or f"remove {kind_of(node)}", do, undo)


def move(parent: Node, node: Node, step: int, label: str = "") -> Edit | None:
    """The part moved ``step`` places among its siblings (None when it would leave the list)."""
    old = next(i for i, c in enumerate(parent.children) if c is node)
    new = old + step
    if not 0 <= new < len(parent.children):
        return None

    def place(at: int) -> Callable[[], None]:
        def run() -> None:  # by identity: two parts may be equal field for field
            parent.children[:] = [c for c in parent.children if c is not node]
            parent.children.insert(at, node)

        return run

    return Edit(label or f"move {kind_of(node)}", place(new), place(old))


def duplicate(parent: Node, node: Node, label: str = "") -> Edit:
    """A copy of the part right after it."""
    index = next(i for i, c in enumerate(parent.children) if c is node)
    return insert(parent, index + 1, [copy.deepcopy(node)], label or f"duplicate {kind_of(node)}")


def restyled(node: Node, newline: bytes) -> Node:
    """A copy of a part (from another file, a template) with its line ends made ``newline``."""
    out = copy.deepcopy(node)
    for n in out.iter():
        n.head, n.trail, n.tail = (_eol(b, newline) for b in (n.head, n.trail, n.tail))
    return out


def _eol(text: bytes, newline: bytes) -> bytes:
    plain = text.replace(b"\r\n", b"\n")
    return plain.replace(b"\n", newline) if newline != b"\n" else plain
