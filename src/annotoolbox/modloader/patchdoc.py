"""A game file as the mod loader patches it: an element tree under one document node, with the parents known, the
assets found by GUID and, for a large asset file, every asset kept as its bytes until an operation looks inside it.
Stdlib only.

``CPatchDoc.load(data, assets)`` reads a file (``assets``: the byte range and GUID of each asset, from an index, so
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


def leaf_text(element: ET.Element) -> str:
    """An element's string value when it holds no lazy part: its text and its descendants' text."""
    return (element.text or "") if not len(element) else "".join(element.itertext())


class CPatchDoc:
    """One patched file. ``top`` is the document node; ``roots`` its root elements."""

    def __init__(self, roots: list[ET.Element], data: bytes = b"", lazy: dict | None = None) -> None:
        self.m_top = ET.Element(DOCUMENT)
        self.m_top.extend(roots)
        self.m_data = bytearray(data)  # grows with the assets ``freeze`` keeps as bytes
        self.m_notes: dict[
            ET.Element, object
        ] = {}  # what the caller kept with a frozen asset (read before it was packed)
        self.m_lazy: dict[ET.Element, tuple[int, int]] = lazy or {}
        self._m_starts = sorted((start, element) for start, element in self.m_lazy.values())
        self._m_by_start = {start: element for element, (start, _end) in self.m_lazy.items()}
        self._m_needles: dict[bytes, tuple[set[int], int]] = {}  # needle -> (range starts holding it, bytes scanned)
        self.m_parents: dict[ET.Element, ET.Element] = {}
        self._link(self.m_top)
        self.m_by_guid: dict[str, list[ET.Element]] = {}
        self.m_guid_text: dict[ET.Element, str] = {}
        self._m_keys: dict[ET.Element, dict[tuple[str, str], dict[str, list]]] = {}
        self.m_std_guid: dict[str, list[ET.Element]] = {}  # every ``Standard/GUID`` element read, by its text
        self._m_std_key: dict[ET.Element, str] = {}
        self._scan_std(self.m_top)
        self.m_materialized = 0
        for assets in self.m_top.iter("Assets"):
            for asset in assets:
                self._index(asset)

    @classmethod
    def load(cls, data: bytes, assets: Iterable[tuple[int, int, int | str]] | None = None) -> CPatchDoc:
        """A file's bytes; ``assets``: (start, end, GUID) of each asset directly in an ``<Assets>``, non-overlapping."""
        if not assets:
            return cls(parse_roots(data), data)
        pieces, position, guids = [], 0, {}
        for start, end, guid in sorted(assets):
            pieces += [data[position:start], f'<Asset {LAZY}="{start}" />'.encode()]
            guids[start] = (end, str(guid))
            position = end
        pieces.append(data[position:])
        roots = parse_roots(b"".join(pieces))
        lazy, known = {}, {}
        for root in roots:
            for element in root.iter("Asset"):
                mark = element.attrib.pop(LAZY, None)
                if mark is not None:
                    end, guid = guids[int(mark)]
                    lazy[element] = (int(mark), end)
                    known[element] = guid
        document = cls(roots, data, lazy)
        for element, guid in known.items():
            document._index_as(element, guid)
        return document

    @classmethod
    def of(cls, *roots: ET.Element) -> CPatchDoc:
        return cls(list(roots))

    @property
    def roots(self) -> list[ET.Element]:
        return list(self.m_top)

    # navigation
    def parent(self, element: ET.Element) -> ET.Element | None:
        return self.m_parents.get(element)

    def is_lazy(self, element) -> bool:
        return element in self.m_lazy

    def children(self, element: ET.Element) -> list[ET.Element]:
        if element in self.m_lazy:
            self.materialize(element)
        return list(element)

    def asset_of(self, node: ET.Element | None) -> ET.Element | None:
        """The asset (an ``<Asset>`` directly in an ``<Assets>``) that holds ``node`` or is it."""
        while node is not None:
            parent = self.m_parents.get(node)
            if node.tag == "Asset" and parent is not None and parent.tag == "Assets":
                return node
            node = parent
        return None

    def string(self, element: ET.Element) -> str:
        """The XPath string value: every text below, the lazy parts read first."""
        if element in self.m_lazy:
            self.materialize(element)
        elif self.m_lazy and len(element) and self.asset_of(element) is None:
            for sub in [element for element in element.iter() if element in self.m_lazy]:
                self.materialize(sub)
        return leaf_text(element)

    def descendants(self, ctx: ET.Element, tag: str | None, needles: tuple[bytes, ...] = ()) -> list[ET.Element]:
        """The elements below ``ctx`` named ``tag`` (any with None) in document order; with ``needles`` the lazy
        assets that lack one of them stay unread and are left out (they cannot hold a match)."""
        if ctx in self.m_lazy:
            self.materialize(ctx)
        elif self.m_lazy and self.asset_of(ctx) is None:
            if needles:
                self.materialize_with(needles)
            else:
                self.materialize_all()
        found = ctx.iter(tag) if tag else ctx.iter()
        return [element for element in found if element is not ctx and element not in self.m_lazy]

    def keyed(self, parent: ET.Element, tag: str, key: str) -> dict[str, list[ET.Element]]:
        """The children ``tag`` of ``parent`` by the string value of their ``key`` path (kept until a change)."""
        caches = self._m_keys.setdefault(parent, {})
        found = caches.get((tag, key))
        if found is None:
            if len(self._m_keys) > MAX_KEYED:
                self._m_keys = {parent: caches}
            found = {}
            for child in self.children(parent):
                if child.tag == tag:
                    for value in self.key_values(child, key):
                        found.setdefault(value, []).append(child)
            caches[(tag, key)] = found
        return found

    def key_values(self, child: ET.Element, key: str) -> list[str]:
        if child in self.m_lazy:
            self.materialize(child)
        if PLAIN_PATH.match(
            key
        ):  # names only: walked by hand, the element tree's path search costs three times as much
            nodes = [child]
            for name in key.split("/"):
                nodes = [kid for node in nodes for kid in node if kid.tag == name]
            return [leaf_text(node) for node in nodes]
        if "@" in key:
            path, _, attribute = key.rpartition("@")
            nodes = [child] if not path.strip("/") else list(child.iterfind(path.strip("/")))
            return [node.get(attribute) for node in nodes if node.get(attribute) is not None]
        return [leaf_text(node) for node in child.iterfind(key)]

    # lazy assets
    def materialize(self, element: ET.Element) -> None:
        start, end = self.m_lazy.pop(element)
        self._m_by_start.pop(start, None)
        self.m_notes.pop(element, None)
        parsed = parse_roots(bytes(self.m_data[start:end]))[0]
        element.attrib.update(parsed.attrib)
        element.text = parsed.text
        element.extend(list(parsed))
        self._link(element)
        self._scan_std(element)
        self.m_materialized += 1

    def freeze(self, asset: ET.Element, note: object = None) -> None:
        """Keep ``asset`` (one the loader added) as its bytes, like an asset of the game's file: its children go, it is
        read back when an operation looks inside, ``peek`` reads it without keeping it. ``note`` stays with it until then."""
        if asset in self.m_lazy or asset.find(".//Assets") is not None:
            return
        tail = asset.tail
        blob = ET.tostring(asset, encoding="utf-8")
        for node in asset.iter():
            if node is not asset:
                self.m_parents.pop(node, None)
                if node.tag == "GUID":
                    self._std_drop(node)
        asset.clear()
        asset.tail = tail
        start = len(self.m_data)
        self.m_data += blob
        self.m_lazy[asset] = (start, len(self.m_data))
        self._m_starts.append((start, len(self.m_data)))
        self._m_by_start[start] = asset
        self.m_notes[asset] = note

    def _forget_lazy(self, element: ET.Element) -> None:
        """A removed element's bytes are not read again (a search by GUID would find the asset that is gone)."""
        span = self.m_lazy.pop(element, None)
        if span is not None:
            self._m_by_start.pop(span[0], None)
            self.m_notes.pop(element, None)

    def peek(self, element: ET.Element) -> ET.Element:
        """``element`` itself, or for a lazy asset a copy read from its bytes (the document is not changed)."""
        if element not in self.m_lazy:
            return element
        start, end = self.m_lazy[element]
        return parse_roots(bytes(self.m_data[start:end]))[0]

    def materialize_all(self) -> None:
        for element in list(self.m_lazy):
            self.materialize(element)

    def materialize_with(self, needles: tuple[bytes, ...]) -> None:
        """Read every lazy asset whose bytes hold all ``needles``."""
        hits = None
        for needle in needles:
            found = self._containing(needle)
            hits = found if hits is None else hits & found
        for start in sorted(hits or ()):
            element = self._m_by_start.get(start)
            if element is not None and element in self.m_lazy:
                self.materialize(element)

    def _containing(self, needle: bytes) -> set[int]:
        """The start offsets of the lazy ranges holding ``needle`` (one scan of the bytes per needle; what ``freeze``
        added since is scanned when the needle comes again)."""
        out, done = self._m_needles.get(needle, (set(), 0))
        if done < len(self.m_data):
            starts, position = [start for start, _end in self._m_starts], self.m_data.find(needle, done)
            while position >= 0:
                k = bisect.bisect_right(starts, position) - 1
                if k >= 0 and position + len(needle) <= self._m_starts[k][1]:
                    out.add(starts[k])
                position = self.m_data.find(needle, position + 1)
            self._m_needles[needle] = (out, len(self.m_data))
        return out

    # assets by GUID
    def guid_holders(self, guid: str, tags: tuple[str, ...]) -> list[ET.Element]:
        """The elements ``tags[0]`` whose ``tags[1:]`` path (ending ``Standard/GUID``) has the text ``guid``, as
        ``//Values[Standard/GUID='5']`` selects them: from the index, the lazy assets that hold one read first."""
        self.materialize_with((f"<GUID>{guid}</GUID>".encode(),))
        out = []
        for found in self.m_std_guid.get(guid, ()):
            node = found
            for tag in reversed(tags[:-1]):
                node = self.m_parents.get(node)
                if node is None or node.tag != tag:
                    break
            else:
                out.append(node)
        return out

    def _scan_std(self, element: ET.Element) -> None:
        """Every ``Standard/GUID`` at or below ``element`` filed under its text."""
        if element.tag == "GUID":
            parent = self.m_parents.get(element)
            found = [element] if parent is not None and parent.tag == "Standard" else []
        else:
            found = [guid_node for std in element.iter("Standard") for guid_node in std if guid_node.tag == "GUID"]
        for guid_node in found:
            key = guid_node.text or ""
            if self._m_std_key.get(guid_node) != key:
                self._std_drop(guid_node)
                self._m_std_key[guid_node] = key
                self.m_std_guid.setdefault(key, []).append(guid_node)

    def _std_drop(self, guid_node: ET.Element) -> None:
        key = self._m_std_key.pop(guid_node, None)
        if key is not None:
            users = self.m_std_guid.get(key, [])
            if guid_node in users:
                users.remove(guid_node)

    def assets(self, guid: str) -> list[ET.Element]:
        return list(self.m_by_guid.get(guid.strip(), ()))

    def _index(self, asset: ET.Element) -> None:
        if asset.tag != "Asset":
            return
        guid = asset.findtext(GUID_PATH) if asset not in self.m_lazy else None
        if guid is not None:
            self._index_as(asset, guid)

    def _index_as(self, asset: ET.Element, guid: str) -> None:
        self.m_guid_text[asset] = guid
        self.m_by_guid.setdefault(guid, []).append(asset)

    def _unindex(self, asset: ET.Element) -> None:
        guid = self.m_guid_text.pop(asset, None)
        if guid is not None:
            users = self.m_by_guid.get(guid, [])
            if asset in users:
                users.remove(asset)
            if not users:
                self.m_by_guid.pop(guid, None)

    # changes
    def _link(self, element: ET.Element) -> None:
        for node in element.iter():
            for child in node:
                self.m_parents[child] = node

    def index_of(self, element: ET.Element) -> int:
        return list(self.m_parents[element]).index(element)  # an element equals only itself

    def insert(self, parent: ET.Element, index: int, nodes: list[ET.Element]) -> None:
        """``nodes`` into ``parent`` at ``index`` (its end with ``len``)."""
        if parent in self.m_lazy:
            self.materialize(parent)
        at_end = index >= len(parent)
        for k, node in enumerate(nodes):
            parent.insert(index + k, node)
            self.m_parents[node] = parent
            self._link(node)
            self._scan_std(node)
            if parent.tag == "Assets":
                self._index(node)
            for assets in node.iter("Assets"):
                for asset in assets:
                    self._index(asset)
        self._children_changed(parent, nodes if at_end else None, {inserted.tag for inserted in nodes})

    def remove(self, element: ET.Element) -> None:
        parent = self.m_parents.get(element)
        if parent is None:
            return
        caches = self._m_keys.get(parent, {})
        for (tag, _key), table in caches.items():
            if element.tag == tag:
                for users in table.values():
                    if element in users:
                        users.remove(element)
        parent.remove(element)
        for node in [element, *element.iter("Asset")]:
            self._unindex(node)
            self._forget_lazy(node)
        for guid in element.iter("GUID"):
            self._std_drop(guid)
        self.changed(parent, is_own=False, tags={element.tag})

    def _children_changed(self, parent: ET.Element, appended: list | None, tags: set[str] | None = None) -> None:
        caches = self._m_keys.get(parent)
        if caches:
            if appended is None:
                self._m_keys.pop(parent, None)
            else:
                for (tag, key), table in caches.items():
                    for node in appended:
                        if node.tag == tag:
                            for value in self.key_values(node, key):
                                table.setdefault(value, []).append(node)
        self.changed(parent, is_own=False, tags=tags)

    def changed(self, node: ET.Element, is_own: bool = True, tags: set[str] | None = None) -> None:
        """Tell the document that ``node``'s text, attributes or subtree changed (keyed lookups, GUIDs follow).
        ``is_own`` False: only its children changed, ``tags`` names them (a keyed lookup that reads none of those names
        under ``node`` stays)."""
        if is_own:
            self._scan_std(node)
            self._m_keys.pop(node, None)
            for cached in [key for key in self._m_keys if self._below(key, node)]:
                self._m_keys.pop(cached, None)
        path: list[str] = []
        child = node
        while child is not None:
            parent = self.m_parents.get(child)
            caches = self._m_keys.get(parent) if parent is not None else None
            if caches:
                below = "/".join(reversed(path))
                narrow = tags is not None and not is_own and not path
                for tag, key in [
                    k for k in caches if k[0] == child.tag and (_reads(k[1], tags) if narrow else _overlap(below, k[1]))
                ]:
                    caches.pop((tag, key), None)
            path.append(child.tag)
            child = parent
        asset = self.asset_of(node)
        if asset is not None and asset not in self.m_lazy:
            guid = asset.findtext(GUID_PATH)
            if guid != self.m_guid_text.get(asset):
                self._unindex(asset)
                if guid is not None:
                    self._index_as(asset, guid)

    def _below(self, element: ET.Element, ancestor: ET.Element) -> bool:
        node = self.m_parents.get(element)
        while node is not None:
            if node is ancestor:
                return True
            node = self.m_parents.get(node)
        return False

    def to_bytes(self) -> bytes:
        """The patched file (every lazy asset read back)."""
        self.materialize_all()
        return b"\n".join(ET.tostring(root, encoding="utf-8", xml_declaration=False) for root in self.m_top)


def _reads(key: str, tags: set[str]) -> bool:
    """Whether a keyed lookup's value can change when children named ``tags`` come or go under the keyed child: its
    path starts with one of them (a path that is no plain name is taken as reading everything)."""
    first = key.strip("/").split("/")[0]
    return not PLAIN_PATH.match(first) or first in tags


def _overlap(below: str, key: str) -> bool:
    """Whether a change at path ``below`` (from a keyed child) can change the child's ``key`` value."""
    key = key.split("@")[0].strip("/")
    return not below or not key or key == below or key.startswith(below + "/") or below.startswith(key + "/")


def canonical(element: ET.Element):
    """An element as a comparable value: tag, attributes, text (white space alone counts as none), children."""
    text = element.text if element.text and element.text.strip() else ""
    return (element.tag, tuple(sorted(element.attrib.items())), text, tuple(canonical(child) for child in element))
