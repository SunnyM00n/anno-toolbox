"""A game file as the mod loader patches it: an element tree under one document node, with the parents known, the
assets found by GUID and, for a large asset file, every asset kept as its bytes until an operation looks inside it.
Stdlib only.

``PatchDoc.load(data, assets)`` reads a file (``assets``: the byte range and GUID of each asset, from an index, so
only the touched ones are parsed); ``insert``, ``remove`` and ``changed`` are the only ways the engine changes it, so
the GUID index and the keyed lookups stay right. ``canonical`` is what two trees are compared by. The rules:
``docs/tool-suite.md`` section 17.
"""

from __future__ import annotations

import bisect
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable

from ..mod.modxml import close_tags

DOCUMENT = "#document"  # the tag of the node above the root element(s)
GUID_PATH = "Values/Standard/GUID"
LAZY = "__lazy__"
PLAIN_PATH = re.compile(r"[A-Za-z_][\w\-]*(?:/[A-Za-z_][\w\-]*)*\Z")  # a key of element names only
MAX_KEYED = 2000  # parents with keyed lookups kept at once
BOM = "﻿".encode()


def parse_roots(data: bytes | str) -> list[ET.Element]:
    """The root element(s) of a file: Anno's ``</>`` closed, a file with two roots (``camera.xml``) read whole."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = text.lstrip("﻿")
    try:
        return [ET.fromstring(text)]
    except ET.ParseError:
        closed = close_tags(text)
    try:
        return [ET.fromstring(closed)]
    except ET.ParseError:
        body = re.sub(r"^\s*<\?xml[^>]*\?>", "", closed)
    return list(ET.fromstring(f"<wrap>{body}</wrap>"))


def leaf_text(el: ET.Element) -> str:
    """An element's string value when it holds no lazy part: its text and its descendants' text."""
    return (el.text or "") if not len(el) else "".join(el.itertext())


class PatchDoc:
    """One patched file. ``top`` is the document node; ``roots`` its root elements."""

    def __init__(self, roots: list[ET.Element], data: bytes = b"", lazy: dict | None = None) -> None:
        self.top = ET.Element(DOCUMENT)
        self.top.extend(roots)
        self.data = bytearray(data)  # grows with the assets ``freeze`` keeps as bytes
        self.notes: dict[
            ET.Element, object
        ] = {}  # what the caller kept with a frozen asset (read before it was packed)
        self.lazy: dict[ET.Element, tuple[int, int]] = lazy or {}
        self._starts = sorted((s, e) for s, e in self.lazy.values())
        self._by_start = {s: el for el, (s, _e) in self.lazy.items()}
        self._needles: dict[bytes, tuple[set[int], int]] = {}  # needle -> (range starts holding it, bytes scanned)
        self.parents: dict[ET.Element, ET.Element] = {}
        self._link(self.top)
        self.by_guid: dict[str, list[ET.Element]] = {}
        self.guid_text: dict[ET.Element, str] = {}
        self._keys: dict[ET.Element, dict[tuple[str, str], dict[str, list]]] = {}
        self.std_guid: dict[str, list[ET.Element]] = {}  # every ``Standard/GUID`` element read, by its text
        self._std_key: dict[ET.Element, str] = {}
        self._scan_std(self.top)
        self.materialized = 0
        for assets in self.top.iter("Assets"):
            for asset in assets:
                self._index(asset)

    @classmethod
    def load(cls, data: bytes, assets: Iterable[tuple[int, int, int | str]] | None = None) -> PatchDoc:
        """A file's bytes; ``assets``: (start, end, GUID) of each asset directly in an ``<Assets>``, non-overlapping."""
        if not assets:
            return cls(parse_roots(data), data)
        pieces, pos, guids = [], 0, {}
        for start, end, guid in sorted(assets):
            pieces += [data[pos:start], f'<Asset {LAZY}="{start}" />'.encode()]
            guids[start] = (end, str(guid))
            pos = end
        pieces.append(data[pos:])
        roots = parse_roots(b"".join(pieces))
        lazy, known = {}, {}
        for root in roots:
            for el in root.iter("Asset"):
                mark = el.attrib.pop(LAZY, None)
                if mark is not None:
                    end, guid = guids[int(mark)]
                    lazy[el] = (int(mark), end)
                    known[el] = guid
        doc = cls(roots, data, lazy)
        for el, guid in known.items():
            doc._index_as(el, guid)
        return doc

    @classmethod
    def of(cls, *roots: ET.Element) -> PatchDoc:
        return cls(list(roots))

    @property
    def roots(self) -> list[ET.Element]:
        return list(self.top)

    # navigation
    def parent(self, el: ET.Element) -> ET.Element | None:
        return self.parents.get(el)

    def is_lazy(self, el) -> bool:
        return el in self.lazy

    def children(self, el: ET.Element) -> list[ET.Element]:
        if el in self.lazy:
            self.materialize(el)
        return list(el)

    def asset_of(self, node: ET.Element | None) -> ET.Element | None:
        """The asset (an ``<Asset>`` directly in an ``<Assets>``) that holds ``node`` or is it."""
        while node is not None:
            up = self.parents.get(node)
            if node.tag == "Asset" and up is not None and up.tag == "Assets":
                return node
            node = up
        return None

    def string(self, el: ET.Element) -> str:
        """The XPath string value: every text below, the lazy parts read first."""
        if el in self.lazy:
            self.materialize(el)
        elif self.lazy and len(el) and self.asset_of(el) is None:
            for sub in [e for e in el.iter() if e in self.lazy]:
                self.materialize(sub)
        return leaf_text(el)

    def descendants(self, ctx: ET.Element, tag: str | None, needles: tuple[bytes, ...] = ()) -> list[ET.Element]:
        """The elements below ``ctx`` named ``tag`` (any with None) in document order; with ``needles`` the lazy
        assets that lack one of them stay unread and are left out (they cannot hold a match)."""
        if self.lazy and self.asset_of(ctx) is None:
            if needles:
                self.materialize_with(needles)
            else:
                self.materialize_all()
        found = ctx.iter(tag) if tag else ctx.iter()
        return [e for e in found if e is not ctx and e not in self.lazy]

    def keyed(self, parent: ET.Element, tag: str, key: str) -> dict[str, list[ET.Element]]:
        """The children ``tag`` of ``parent`` by the string value of their ``key`` path (kept until a change)."""
        caches = self._keys.setdefault(parent, {})
        found = caches.get((tag, key))
        if found is None:
            if len(self._keys) > MAX_KEYED:
                self._keys = {parent: caches}
            found = {}
            for child in self.children(parent):
                if child.tag == tag:
                    for value in self.key_values(child, key):
                        found.setdefault(value, []).append(child)
            caches[(tag, key)] = found
        return found

    def key_values(self, child: ET.Element, key: str) -> list[str]:
        if child in self.lazy:
            self.materialize(child)
        if PLAIN_PATH.match(
            key
        ):  # names only: walked by hand, the element tree's path search costs three times as much
            nodes = [child]
            for name in key.split("/"):
                nodes = [c for n in nodes for c in n if c.tag == name]
            return [leaf_text(n) for n in nodes]
        if "@" in key:
            path, _, attr = key.rpartition("@")
            nodes = [child] if not path.strip("/") else list(child.iterfind(path.strip("/")))
            return [n.get(attr) for n in nodes if n.get(attr) is not None]
        return [leaf_text(n) for n in child.iterfind(key)]

    # lazy assets
    def materialize(self, el: ET.Element) -> None:
        start, end = self.lazy.pop(el)
        self._by_start.pop(start, None)
        self.notes.pop(el, None)
        parsed = parse_roots(bytes(self.data[start:end]))[0]
        el.attrib.update(parsed.attrib)
        el.text = parsed.text
        el.extend(list(parsed))
        self._link(el)
        self._scan_std(el)
        self.materialized += 1

    def freeze(self, asset: ET.Element, note: object = None) -> None:
        """Keep ``asset`` (one the loader added) as its bytes, like an asset of the game's file: its children go, it is
        read back when an operation looks inside, ``peek`` reads it without keeping it. ``note`` stays with it until then."""
        if asset in self.lazy or asset.find(".//Assets") is not None:
            return
        tail = asset.tail
        blob = ET.tostring(asset, encoding="utf-8")
        for node in asset.iter():
            if node is not asset:
                self.parents.pop(node, None)
                if node.tag == "GUID":
                    self._std_drop(node)
        asset.clear()
        asset.tail = tail
        start = len(self.data)
        self.data += blob
        self.lazy[asset] = (start, len(self.data))
        self._starts.append((start, len(self.data)))
        self._by_start[start] = asset
        self.notes[asset] = note

    def peek(self, el: ET.Element) -> ET.Element:
        """``el`` itself, or for a lazy asset a copy read from its bytes (the document is not changed)."""
        if el not in self.lazy:
            return el
        start, end = self.lazy[el]
        return parse_roots(bytes(self.data[start:end]))[0]

    def materialize_all(self) -> None:
        for el in list(self.lazy):
            self.materialize(el)

    def materialize_with(self, needles: tuple[bytes, ...]) -> None:
        """Read every lazy asset whose bytes hold all ``needles``."""
        hits = None
        for needle in needles:
            found = self._containing(needle)
            hits = found if hits is None else hits & found
        for start in sorted(hits or ()):
            el = self._by_start.get(start)
            if el is not None and el in self.lazy:
                self.materialize(el)

    def _containing(self, needle: bytes) -> set[int]:
        """The start offsets of the lazy ranges holding ``needle`` (one scan of the bytes per needle; what ``freeze``
        added since is scanned when the needle comes again)."""
        out, done = self._needles.get(needle, (set(), 0))
        if done < len(self.data):
            starts, pos = [s for s, _e in self._starts], self.data.find(needle, done)
            while pos >= 0:
                k = bisect.bisect_right(starts, pos) - 1
                if k >= 0 and pos + len(needle) <= self._starts[k][1]:
                    out.add(starts[k])
                pos = self.data.find(needle, pos + 1)
            self._needles[needle] = (out, len(self.data))
        return out

    # assets by GUID
    def guid_holders(self, guid: str, tags: tuple[str, ...]) -> list[ET.Element]:
        """The elements ``tags[0]`` whose ``tags[1:]`` path (ending ``Standard/GUID``) has the text ``guid``, as
        ``//Values[Standard/GUID='5']`` selects them: from the index, the lazy assets that hold one read first."""
        self.materialize_with((f"<GUID>{guid}</GUID>".encode(),))
        out = []
        for found in self.std_guid.get(guid, ()):
            node = found
            for tag in reversed(tags[:-1]):
                node = self.parents.get(node)
                if node is None or node.tag != tag:
                    break
            else:
                out.append(node)
        return out

    def _scan_std(self, el: ET.Element) -> None:
        """Every ``Standard/GUID`` at or below ``el`` filed under its text."""
        if el.tag == "GUID":
            up = self.parents.get(el)
            found = [el] if up is not None and up.tag == "Standard" else []
        else:
            found = [g for std in el.iter("Standard") for g in std if g.tag == "GUID"]
        for g in found:
            key = g.text or ""
            if self._std_key.get(g) != key:
                self._std_drop(g)
                self._std_key[g] = key
                self.std_guid.setdefault(key, []).append(g)

    def _std_drop(self, g: ET.Element) -> None:
        key = self._std_key.pop(g, None)
        if key is not None:
            users = self.std_guid.get(key, [])
            if g in users:
                users.remove(g)

    def assets(self, guid: str) -> list[ET.Element]:
        return list(self.by_guid.get(guid.strip(), ()))

    def _index(self, asset: ET.Element) -> None:
        if asset.tag != "Asset":
            return
        guid = asset.findtext(GUID_PATH) if asset not in self.lazy else None
        if guid is not None:
            self._index_as(asset, guid)

    def _index_as(self, asset: ET.Element, guid: str) -> None:
        self.guid_text[asset] = guid
        self.by_guid.setdefault(guid, []).append(asset)

    def _unindex(self, asset: ET.Element) -> None:
        guid = self.guid_text.pop(asset, None)
        if guid is not None:
            users = self.by_guid.get(guid, [])
            if asset in users:
                users.remove(asset)
            if not users:
                self.by_guid.pop(guid, None)

    # changes
    def _link(self, el: ET.Element) -> None:
        for node in el.iter():
            for child in node:
                self.parents[child] = node

    def index_of(self, el: ET.Element) -> int:
        return list(self.parents[el]).index(el)  # an element equals only itself

    def insert(self, parent: ET.Element, index: int, nodes: list[ET.Element]) -> None:
        """``nodes`` into ``parent`` at ``index`` (its end with ``len``)."""
        if parent in self.lazy:
            self.materialize(parent)
        at_end = index >= len(parent)
        for k, node in enumerate(nodes):
            parent.insert(index + k, node)
            self.parents[node] = parent
            self._link(node)
            self._scan_std(node)
            if parent.tag == "Assets":
                self._index(node)
            for assets in node.iter("Assets"):
                for asset in assets:
                    self._index(asset)
        self._children_changed(parent, nodes if at_end else None, {n.tag for n in nodes})

    def remove(self, el: ET.Element) -> None:
        parent = self.parents.get(el)
        if parent is None:
            return
        caches = self._keys.get(parent, {})
        for (tag, _key), table in caches.items():
            if el.tag == tag:
                for users in table.values():
                    if el in users:
                        users.remove(el)
        parent.remove(el)
        for node in [el, *el.iter("Asset")]:
            self._unindex(node)
        for guid in el.iter("GUID"):
            self._std_drop(guid)
        self.changed(parent, own=False, tags={el.tag})

    def _children_changed(self, parent: ET.Element, appended: list | None, tags: set[str] | None = None) -> None:
        caches = self._keys.get(parent)
        if caches:
            if appended is None:
                self._keys.pop(parent, None)
            else:
                for (tag, key), table in caches.items():
                    for node in appended:
                        if node.tag == tag:
                            for value in self.key_values(node, key):
                                table.setdefault(value, []).append(node)
        self.changed(parent, own=False, tags=tags)

    def changed(self, node: ET.Element, own: bool = True, tags: set[str] | None = None) -> None:
        """Tell the document that ``node``'s text, attributes or subtree changed (keyed lookups, GUIDs follow).
        ``own`` False: only its children changed, ``tags`` names them (a keyed lookup that reads none of those names
        under ``node`` stays)."""
        if own:
            self._scan_std(node)
            self._keys.pop(node, None)
            for cached in [p for p in self._keys if self._below(p, node)]:
                self._keys.pop(cached, None)
        path: list[str] = []
        child = node
        while child is not None:
            up = self.parents.get(child)
            caches = self._keys.get(up) if up is not None else None
            if caches:
                below = "/".join(reversed(path))
                narrow = tags is not None and not own and not path
                for tag, key in [
                    k for k in caches if k[0] == child.tag and (_reads(k[1], tags) if narrow else _overlap(below, k[1]))
                ]:
                    caches.pop((tag, key), None)
            path.append(child.tag)
            child = up
        asset = self.asset_of(node)
        if asset is not None and asset not in self.lazy:
            guid = asset.findtext(GUID_PATH)
            if guid != self.guid_text.get(asset):
                self._unindex(asset)
                if guid is not None:
                    self._index_as(asset, guid)

    def _below(self, el: ET.Element, ancestor: ET.Element) -> bool:
        node = self.parents.get(el)
        while node is not None:
            if node is ancestor:
                return True
            node = self.parents.get(node)
        return False

    def to_bytes(self) -> bytes:
        """The patched file (every lazy asset read back)."""
        self.materialize_all()
        return b"\n".join(ET.tostring(root, encoding="utf-8", xml_declaration=False) for root in self.top)


def _reads(key: str, tags: set[str]) -> bool:
    """Whether a keyed lookup's value can change when children named ``tags`` come or go under the keyed child: its
    path starts with one of them (a path that is no plain name is taken as reading everything)."""
    first = key.strip("/").split("/")[0]
    return not PLAIN_PATH.match(first) or first in tags


def _overlap(below: str, key: str) -> bool:
    """Whether a change at path ``below`` (from a keyed child) can change the child's ``key`` value."""
    key = key.split("@")[0].strip("/")
    return not below or not key or key == below or key.startswith(below + "/") or below.startswith(key + "/")


def canonical(el: ET.Element):
    """An element as a comparable value: tag, attributes, text (white space alone counts as none), children."""
    text = el.text if el.text and el.text.strip() else ""
    return (el.tag, tuple(sorted(el.attrib.items())), text, tuple(canonical(c) for c in el))
