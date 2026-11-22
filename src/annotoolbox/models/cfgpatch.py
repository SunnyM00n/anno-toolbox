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
from ..formats.isd2070 import SNode
from ..modloader.engine import WARNING, run_text
from ..modloader.patchdoc import CPatchDoc
from ..othergames import installs
from .cfgdoc import SDoc, shown, stored
from .configtree import BARE_AMP, shared_config

PATCH_GAMES = installs.keys(lambda g: g.mod_loader)  # the games whose mod loader patches a config with ModOps
PATCHED = (".cfg", ".fc")  # an .ifo has no patch rule: it is replaced whole
ARCHIVE_GAMES = installs.keys(lambda g: not g.mod_loader)  # their mods are whole files in an archive
XML_NAME = re.compile(r"^[A-Za-z_][\w.\-]*$")
NEW, REPLACE, PATCH = "new", "replace", "patch"


@dataclass(frozen=True)
class SWay:
    """One way to save: ``way`` (``NEW``, ``REPLACE``, ``PATCH``), the game path written (a new file's is chosen
    later), whether it is open and why (or why not)."""

    way: str
    path: str
    allowed: bool
    why: str


def ways(game: str, opened: str, read: str, exists: Callable[[str], bool]) -> list[SWay]:
    """The ways a file can be saved: ``opened`` the path asked for, ``read`` the one read (117's ``shared/`` copy of a
    ``data/`` config, or the same), ``exists`` whether the game has a path."""
    shared = shared_config(read) if game == "anno117" and read.endswith(".cfg") else ""
    hidden = bool(shared) and exists(shared)  # a data/ config the game never reads: its shared/ copy wins
    new = SWay(NEW, "", True, "a file at a path of your own: the game reads it as written once an asset or another "
                             "config names it; nothing of the game is replaced")  # fmt: skip
    if hidden:
        why = (f"the game reads {shared} in place of {read} (its baked shared/ copy): a file or a patch at {read} "
               "is never read; open the shared/ copy (View > Read the baked shared/ copy) to change what the game "
               "draws")  # fmt: skip
        return [new, SWay(REPLACE, read, False, why), SWay(PATCH, read + ".xml", False, why)]
    note = (" It is the baked shared/ copy the game reads in place of " + opened + "; its .bfg is still read on top "
            "and matches parts by position: when parts are added, taken out or moved the save also writes a .bfg "
            "that follows." if read != opened else "")  # fmt: skip
    replace = SWay(REPLACE, read, True, f"the game's {read} replaced by the whole edited file.{note}")
    if game not in PATCH_GAMES or not read.endswith(PATCHED):
        why = ("an .ifo cannot be patched (the mod loader has no rule for it): it is replaced whole"
               if read.endswith(".ifo") else "this game's mods carry whole files in an archive, no patches")  # fmt: skip
        return [new, replace, SWay(PATCH, read + ".xml", False, why)]
    checked = "" if game == "anno117" else " (1800's loader is taken to patch configs as 117's does; not checked)"
    patch = SWay(PATCH, read + ".xml", True, f"ModOps that turn the game's {read} into the edited one: a later game "
                                            f"update of the rest of the file is kept.{note}{checked}")  # fmt: skip
    return [new, replace, patch]


@dataclass
class SPatch:
    """A ModOps document (``text``), the number of operations, and what it could not say (then write the whole file)."""

    text: str = ""
    count: int = 0
    problems: list[str] = field(default_factory=list)


@dataclass
class _CRun:
    m_ops: list[str] = field(default_factory=list)
    m_problems: list[str] = field(default_factory=list)
    m_sig: dict[int, bytes] = field(default_factory=dict)

    def signature(self, node: SNode) -> tuple[str, bytes]:
        key = id(node)
        if key not in self.m_sig:
            self.m_sig[key] = isd2070.serialize(SNode("", children=[_bare(node)]))
        return node.name, self.m_sig[key]


def render_patch(base: bytes, doc: SDoc) -> SPatch:
    """The ModOps that turn ``base`` (the game's file) into ``doc``; empty when nothing changed."""
    if not doc.exact:
        return SPatch(problems=[f"{doc.path} does not read back byte for byte: write the whole file"])
    run = _CRun()
    _children(isd2070.parse(base), doc.root, "", run)
    body = "".join(f"  {op}\n" for op in run.m_ops)
    text = f"<ModOps>\n{body}</ModOps>\n" if run.m_ops else ""
    return SPatch(text, len(run.m_ops), run.m_problems)


def _bare(node: SNode) -> SNode:
    """A node without its trailing white space: two parts that differ only in what follows them are equal."""
    return SNode(node.name, node.text, node.data, node.children, node.short, b"", node.head, node.closed, node.empty,
                node.trail)  # fmt: skip


def _children(base: SNode, new: SNode, path: str, run: _CRun) -> None:
    """The operations for one list of children, in the order the loader runs them (``current``: the list as the loader
    holds it after each operation, so every path counts the siblings it then has)."""
    current: list[SNode] = list(base.children)
    placed = -1  # the index in ``current`` of the last child set in its place
    for kind, base_node, new_node in _line_up(base.children, new.children, run):
        if kind == "remove":
            run.m_ops.append(f'<ModOp Type="remove" Path={quoteattr(_path(path, current, base_node, run))} />')
            current.pop(_index(current, base_node))
        elif kind == "add":
            _add(path, current, placed, new_node, run)
            current.insert(placed + 1, new_node)
            placed += 1
        else:
            placed = _index(current, base_node)
            if kind == "change":
                _change(base_node, new_node, _path(path, current, base_node, run), run)


def pairs(
    base: list[SNode], new: list[SNode], key: Callable[[SNode], str] = lambda child: child.name
) -> list[tuple[SNode | None, SNode | None]]:
    """The children of a list lined up as a patch lines them (equal parts first, the rest by ``key`` in order):
    (base child, new child), None on the side where a part was removed or is new."""
    return [(base_node, new_node) for _kind, base_node, new_node in _line_up(base, new, _CRun(), key)]


def _index(nodes: list[SNode], node: SNode) -> int:
    return next(i for i, child in enumerate(nodes) if child is node)


def _line_up(base: list[SNode], new: list[SNode], run: _CRun, key: Callable[[SNode], str] = lambda child: child.name):
    """(``keep`` | ``change`` | ``remove`` | ``add``, base child, new child) in order: children equal byte for byte
    pair first, the others pair by ``key`` (the name) in order."""
    sb, sn = [run.signature(child) for child in base], [run.signature(child) for child in new]
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sb, sn, autojunk=False).get_opcodes():
        if op == "equal":
            yield from (("keep", base[i1 + k], new[j1 + k]) for k in range(i2 - i1))
            continue
        bs, ns = base[i1:i2], new[j1:j2]
        names = difflib.SequenceMatcher(
            None, [key(child) for child in bs], [key(child) for child in ns], autojunk=False
        )
        for op2, a1, a2, c1, c2 in names.get_opcodes():
            if op2 == "equal":
                yield from (("change", bs[a1 + k], ns[c1 + k]) for k in range(a2 - a1))
            else:
                yield from (("remove", base_node, None) for base_node in bs[a1:a2])
                yield from (("add", None, new_node) for new_node in ns[c1:c2])


def _path(parent: str, current: list[SNode], node: SNode, run: _CRun) -> str:
    if not XML_NAME.match(node.name):
        run.m_problems.append(f"{parent}/{node.name}: a tag with spaces or brackets has no ModOps path")
    same = [child for child in current if child.name == node.name]
    step = node.name if len(same) == 1 else f"{node.name}[{_index(same, node) + 1}]"
    return f"{parent}/{step}"


def _add(path: str, current: list[SNode], placed: int, node: SNode, run: _CRun) -> None:
    content = _xml(node, f"{path}/{node.name}", run)
    if placed >= 0 and XML_NAME.match(current[placed].name):
        op, anchor = "addNextSibling", _path(path, current, current[placed], run)
    elif placed + 1 < len(current):
        op, anchor = "addPrevSibling", _path(path, current, current[placed + 1], run)
    elif not current:
        op, anchor = "add", path or "/"
    else:
        op, anchor = "addNextSibling", _path(path, current, current[placed], run)
    run.m_ops.append(f"<ModOp Type={quoteattr(op)} Path={quoteattr(anchor)}>{content}</ModOp>")


def _change(base_node: SNode, new_node: SNode, path: str, run: _CRun) -> None:
    if not base_node.children and not new_node.children and (base_node.data is not None or new_node.data is not None):
        run.m_problems.append(f"{path}: a binary value changed; a patch carries text only")
    elif (base_node.text, base_node.data) == (new_node.text, new_node.data) and (
        base_node.children or new_node.children
    ):
        _children(base_node, new_node, path, run)
    else:
        run.m_ops.append(f'<ModOp Type="replace" Path={quoteattr(path)}>{_xml(new_node, path, run)}</ModOp>')


def _text(raw: str) -> str:
    """The file's raw text as XML text: an entity already in it stays one (``escape`` would write ``&amp;amp;``)."""
    return BARE_AMP.sub("&amp;", raw).replace("<", "&lt;").replace(">", "&gt;")


def _xml(node: SNode, where: str, run: _CRun) -> str:
    """A part as well-formed XML for a patch's content (the loader wants its own files well-formed)."""
    if not XML_NAME.match(node.name):
        run.m_problems.append(f"{where}: the tag {node.name!r} cannot be written as XML")
        return ""
    if node.data is not None:
        run.m_problems.append(f"{where}: a binary value cannot be written into a patch")
        return ""
    inner = _text(shown(node.text)) if node.text is not None else ""
    inner += "".join(_xml(child, f"{where}/{child.name}", run) for child in node.children)
    return f"<{node.name}>{inner}</{node.name}>"


# Applying a saved patch
def as_element(node: SNode, made: dict[int, SNode] | None = None) -> ET.Element:
    """A tree node as an XML element (a binary value or a tag the XML cannot name keeps no text or its own tag);
    ``made`` learns which node each element came from."""
    element = ET.Element(node.name if XML_NAME.match(node.name) else "_group")
    element.text = shown(node.text) if node.text else None
    element.extend(as_element(child, made) for child in node.children)
    if made is not None:
        made[id(element)] = node
    return element


def apply_patch(base: SDoc, patch: bytes | str) -> tuple[SDoc, list[str]]:
    """The game's document with a saved ``patch`` applied as the mod loader does (``modloader.engine``), and what the
    loader would have warned about (a patch the game's file no longer fits). A part the patch left alone is the
    base's own, so it keeps its bytes; a part it made is written in the file's line ends. ``base`` stays as it is."""
    root = copy.deepcopy(base.root)
    made: dict[int, SNode] = {}
    doc = CPatchDoc.of(*(as_element(child, made) for child in root.children))
    events = run_text(doc, patch)
    root.children[:] = [_rebuilt(element, made, base.newline) for element in doc.roots]
    return replace(base, root=root), [event.line() for event in events if event.level == WARNING]


def _rebuilt(element: ET.Element, made: dict[int, SNode], newline: bytes) -> SNode:
    """The node of a patched element: its own when the patch left it, else one made from the element."""
    node = made.get(id(element))
    if node is None:
        return isd2070.parse(_markup(element, newline.decode()).encode("latin-1")).children[0]
    node.children[:] = [_rebuilt(child, made, newline) for child in element]
    if not node.children and node.data is None and (element.text or "") != shown(node.text or ""):
        node.text = stored(element.text) if element.text else None
    return node


def _markup(element: ET.Element, newline: str) -> str:
    """An element as the file writes a part: no indentation, a line end after each close."""
    inner = stored(element.text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    inner += "".join(_markup(child, newline) for child in element)
    return f"<{element.tag}>{inner}</{element.tag}>{newline}"
