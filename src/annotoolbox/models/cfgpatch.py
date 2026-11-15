"""Where an edited model config (or info, feedback file) goes in a mod, and the ModOps patch (``.cfg.xml``,
``.fc.xml``) that turns the game's file into the edited one.

``ways(game, opened, read, exists)`` lists the three ways a save can take with the reason each is open or not;
``render_patch(base, doc)`` is the patch; ``apply_patch(base, patch)`` the way back (a saved patch applied by the
loader's rules, untouched parts keeping their bytes). docs/tool-suite.md section 23, "Writing back";
docs/engine-modloader.md "What a mod can change" and "the ``shared/`` copies".
"""

from __future__ import annotations

import copy
import difflib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from xml.sax.saxutils import quoteattr

from ..formats import isd2070
from ..formats.isd2070 import Node
from ..modloader.engine import WARNING, run_text
from ..modloader.patchdoc import PatchDoc
from ..othergames import installs
from .cfgdoc import Doc, shown, stored
from .configtree import BARE_AMP, shared_config

PATCH_GAMES = installs.keys(lambda g: g.mod_loader)  # the games whose mod loader patches a config with ModOps
PATCHED = (".cfg", ".fc")  # an .ifo has no patch rule: it is replaced whole
ARCHIVE_GAMES = installs.keys(lambda g: not g.mod_loader)  # their mods are whole files in an archive
XML_NAME = re.compile(r"^[A-Za-z_][\w.\-]*$")
NEW, REPLACE, PATCH = "new", "replace", "patch"


@dataclass(frozen=True)
class Way:
    """One way to save: ``way`` (``NEW``, ``REPLACE``, ``PATCH``), the game path written (a new file's is chosen
    later), whether it is open and why (or why not)."""

    way: str
    path: str
    allowed: bool
    why: str


def ways(game: str, opened: str, read: str, exists: Callable[[str], bool]) -> list[Way]:
    """The ways a file can be saved: ``opened`` the path asked for, ``read`` the one read (117's ``shared/`` copy of a
    ``data/`` config, or the same), ``exists`` whether the game has a path."""
    shared = shared_config(read) if game == "anno117" and read.endswith(".cfg") else ""
    hidden = bool(shared) and exists(shared)  # a data/ config the game never reads: its shared/ copy wins
    new = Way(NEW, "", True, "a file at a path of your own: the game reads it as written once an asset or another "
                             "config names it; nothing of the game is replaced")  # fmt: skip
    if hidden:
        why = (f"the game reads {shared} in place of {read} (its baked shared/ copy): a file or a patch at {read} "
               "is never read; open the shared/ copy (View > Read the baked shared/ copy) to change what the game "
               "draws")  # fmt: skip
        return [new, Way(REPLACE, read, False, why), Way(PATCH, read + ".xml", False, why)]
    note = (" It is the baked shared/ copy the game reads in place of " + opened + "; its .bfg is still read on top "
            "and matches parts by position: when parts are added, taken out or moved the save also writes a .bfg "
            "that follows." if read != opened else "")  # fmt: skip
    replace = Way(REPLACE, read, True, f"the game's {read} replaced by the whole edited file.{note}")
    if game not in PATCH_GAMES or not read.endswith(PATCHED):
        why = ("an .ifo cannot be patched (the mod loader has no rule for it): it is replaced whole"
               if read.endswith(".ifo") else "this game's mods carry whole files in an archive, no patches")  # fmt: skip
        return [new, replace, Way(PATCH, read + ".xml", False, why)]
    checked = "" if game == "anno117" else " (1800's loader is taken to patch configs as 117's does; not checked)"
    patch = Way(PATCH, read + ".xml", True, f"ModOps that turn the game's {read} into the edited one: a later game "
                                            f"update of the rest of the file is kept.{note}{checked}")  # fmt: skip
    return [new, replace, patch]


@dataclass
class Patch:
    """A ModOps document (``text``), the number of operations, and what it could not say (then write the whole file)."""

    text: str = ""
    count: int = 0
    problems: list[str] = field(default_factory=list)


@dataclass
class _Run:
    ops: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    sig: dict[int, bytes] = field(default_factory=dict)

    def signature(self, node: Node) -> tuple[str, bytes]:
        key = id(node)
        if key not in self.sig:
            self.sig[key] = isd2070.serialize(Node("", children=[_bare(node)]))
        return node.name, self.sig[key]


def render_patch(base: bytes, doc: Doc) -> Patch:
    """The ModOps that turn ``base`` (the game's file) into ``doc``; empty when nothing changed."""
    if not doc.exact:
        return Patch(problems=[f"{doc.path} does not read back byte for byte: write the whole file"])
    run = _Run()
    _children(isd2070.parse(base), doc.root, "", run)
    body = "".join(f"  {op}\n" for op in run.ops)
    text = f"<ModOps>\n{body}</ModOps>\n" if run.ops else ""
    return Patch(text, len(run.ops), run.problems)


def _bare(node: Node) -> Node:
    """A node without its trailing white space: two parts that differ only in what follows them are equal."""
    return Node(node.name, node.text, node.data, node.children, node.short, b"", node.head, node.closed, node.empty,
                node.trail)  # fmt: skip


def _children(base: Node, new: Node, path: str, run: _Run) -> None:
    """The operations for one list of children, in the order the loader runs them (``current``: the list as the loader
    holds it after each operation, so every path counts the siblings it then has)."""
    current: list[Node] = list(base.children)
    placed = -1  # the index in ``current`` of the last child set in its place
    for kind, b, n in _line_up(base.children, new.children, run):
        if kind == "remove":
            run.ops.append(f'<ModOp Type="remove" Path={quoteattr(_path(path, current, b, run))} />')
            current.pop(_index(current, b))
        elif kind == "add":
            _add(path, current, placed, n, run)
            current.insert(placed + 1, n)
            placed += 1
        else:
            placed = _index(current, b)
            if kind == "change":
                _change(b, n, _path(path, current, b, run), run)


def pairs(
    base: list[Node], new: list[Node], key: Callable[[Node], str] = lambda c: c.name
) -> list[tuple[Node | None, Node | None]]:
    """The children of a list lined up as a patch lines them (equal parts first, the rest by ``key`` in order):
    (base child, new child), None on the side where a part was removed or is new."""
    return [(b, n) for _kind, b, n in _line_up(base, new, _Run(), key)]


def _index(nodes: list[Node], node: Node) -> int:
    return next(i for i, c in enumerate(nodes) if c is node)


def _line_up(base: list[Node], new: list[Node], run: _Run, key: Callable[[Node], str] = lambda c: c.name):
    """(``keep`` | ``change`` | ``remove`` | ``add``, base child, new child) in order: children equal byte for byte
    pair first, the others pair by ``key`` (the name) in order."""
    sb, sn = [run.signature(c) for c in base], [run.signature(c) for c in new]
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sb, sn, autojunk=False).get_opcodes():
        if op == "equal":
            yield from (("keep", base[i1 + k], new[j1 + k]) for k in range(i2 - i1))
            continue
        bs, ns = base[i1:i2], new[j1:j2]
        names = difflib.SequenceMatcher(None, [key(c) for c in bs], [key(c) for c in ns], autojunk=False)
        for op2, a1, a2, c1, c2 in names.get_opcodes():
            if op2 == "equal":
                yield from (("change", bs[a1 + k], ns[c1 + k]) for k in range(a2 - a1))
            else:
                yield from (("remove", b, None) for b in bs[a1:a2])
                yield from (("add", None, n) for n in ns[c1:c2])


def _path(parent: str, current: list[Node], node: Node, run: _Run) -> str:
    if not XML_NAME.match(node.name):
        run.problems.append(f"{parent}/{node.name}: a tag with spaces or brackets has no ModOps path")
    same = [c for c in current if c.name == node.name]
    step = node.name if len(same) == 1 else f"{node.name}[{_index(same, node) + 1}]"
    return f"{parent}/{step}"


def _add(path: str, current: list[Node], placed: int, node: Node, run: _Run) -> None:
    content = _xml(node, f"{path}/{node.name}", run)
    if placed >= 0 and XML_NAME.match(current[placed].name):
        op, anchor = "addNextSibling", _path(path, current, current[placed], run)
    elif placed + 1 < len(current):
        op, anchor = "addPrevSibling", _path(path, current, current[placed + 1], run)
    elif not current:
        op, anchor = "add", path or "/"
    else:
        op, anchor = "addNextSibling", _path(path, current, current[placed], run)
    run.ops.append(f"<ModOp Type={quoteattr(op)} Path={quoteattr(anchor)}>{content}</ModOp>")


def _change(b: Node, n: Node, path: str, run: _Run) -> None:
    if not b.children and not n.children and (b.data is not None or n.data is not None):
        run.problems.append(f"{path}: a binary value changed; a patch carries text only")
    elif (b.text, b.data) == (n.text, n.data) and (b.children or n.children):
        _children(b, n, path, run)
    else:
        run.ops.append(f'<ModOp Type="replace" Path={quoteattr(path)}>{_xml(n, path, run)}</ModOp>')


def _text(raw: str) -> str:
    """The file's raw text as XML text: an entity already in it stays one (``escape`` would write ``&amp;amp;``)."""
    return BARE_AMP.sub("&amp;", raw).replace("<", "&lt;").replace(">", "&gt;")


def _xml(node: Node, where: str, run: _Run) -> str:
    """A part as well-formed XML for a patch's content (the loader wants its own files well-formed)."""
    if not XML_NAME.match(node.name):
        run.problems.append(f"{where}: the tag {node.name!r} cannot be written as XML")
        return ""
    if node.data is not None:
        run.problems.append(f"{where}: a binary value cannot be written into a patch")
        return ""
    inner = _text(shown(node.text)) if node.text is not None else ""
    inner += "".join(_xml(c, f"{where}/{c.name}", run) for c in node.children)
    return f"<{node.name}>{inner}</{node.name}>"


# Applying a saved patch
def as_element(node: Node, made: dict[int, Node] | None = None) -> ET.Element:
    """A tree node as an XML element (a binary value or a tag the XML cannot name keeps no text or its own tag);
    ``made`` learns which node each element came from."""
    el = ET.Element(node.name if XML_NAME.match(node.name) else "_group")
    el.text = shown(node.text) if node.text else None
    el.extend(as_element(c, made) for c in node.children)
    if made is not None:
        made[id(el)] = node
    return el


def apply_patch(base: Doc, patch: bytes | str) -> tuple[Doc, list[str]]:
    """The game's document with a saved ``patch`` applied as the mod loader does (``modloader.engine``), and what the
    loader would have warned about (a patch the game's file no longer fits). A part the patch left alone is the
    base's own, so it keeps its bytes; a part it made is written in the file's line ends. ``base`` stays as it is."""
    root = copy.deepcopy(base.root)
    made: dict[int, Node] = {}
    doc = PatchDoc.of(*(as_element(c, made) for c in root.children))
    events = run_text(doc, patch)
    root.children[:] = [_rebuilt(el, made, base.newline) for el in doc.roots]
    return replace(base, root=root), [e.line() for e in events if e.level == WARNING]


def _rebuilt(el: ET.Element, made: dict[int, Node], newline: bytes) -> Node:
    """The node of a patched element: its own when the patch left it, else one made from the element."""
    node = made.get(id(el))
    if node is None:
        return isd2070.parse(_markup(el, newline.decode()).encode("latin-1")).children[0]
    node.children[:] = [_rebuilt(c, made, newline) for c in el]
    if not node.children and node.data is None and (el.text or "") != shown(node.text or ""):
        node.text = stored(el.text) if el.text else None
    return node


def _markup(el: ET.Element, newline: str) -> str:
    """An element as the file writes a part: no indentation, a line end after each close."""
    inner = stored(el.text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    inner += "".join(_markup(c, newline) for c in el)
    return f"<{el.tag}>{inner}</{el.tag}>{newline}"
