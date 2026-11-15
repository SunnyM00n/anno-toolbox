"""An asset change set open in the editor, without a window: the game's schema and asset index, the change set, one
undo stack, and every edit the window offers (set, reset, replace a list, new from a template, copy, remove, rename).

``AssetDocument.layers(guid)`` gives an asset's values in their layers with the edits applied, ``asset(guid)`` the
edited ``<Asset>``; each edit is one ``suite.undo.Command``. The window (``assetwindow``) draws it, the tests drive it
on synthetic files. Plan: ``docs/tool-suite.md`` A3-A5.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

from ..infra.i18n import tr
from ..suite.undo import Command, UndoStack
from .assetindex import AssetIndex
from .changeset import AssetChange, ChangeSet, NewAsset
from .schema import Schema
from .values import BASE, OWN, Layer, Layers, apply_change, defaults_layer, template_values, values_of
from .xmltree import find_path, parse, to_text

DEPTH = 32  # base asset chains deeper than this are a loop


class AssetDocument:
    def __init__(self, schema: Schema, index: AssetIndex, changes: ChangeSet, path: Path | None = None) -> None:
        self.schema, self.index, self.changes, self.path = schema, index, changes, path
        self.undo = UndoStack()
        self.listeners: list = []  # called with the GUID an edit touched (None: the tree changed)
        self.game_texts = None  # language -> the game's texts by id (``Game.texts``), for the writers
        self.files = None  # the game's files (``gamefiles.GameFiles``), for the native text writer
        self._parsed: dict[int, ET.Element] = {}

    def snapshot(self) -> AssetDocument:
        """A read-only copy of the document as it is now, for a background job (check, build, write): the window
        goes on editing the original while the job reads this one. Shares the game's files and schema, which no
        edit touches; the change set and the parsed assets are the copy's own."""
        copy_ = AssetDocument(self.schema, self.index, copy.deepcopy(self.changes), self.path)
        copy_.game_texts, copy_.files = self.game_texts, self.files
        copy_._parsed = dict(self._parsed)
        return copy_

    # reading
    def exists(self, guid: int) -> bool:
        return (guid in self.index.by_guid or guid in self.changes.new) and guid not in self.changes.removed

    def vanilla(self, guid: int) -> ET.Element | None:
        """The asset as the game's files have it (parsed once), None for a new one."""
        if guid not in self.index.by_guid:
            return None
        if guid not in self._parsed:
            self._parsed[guid] = self.index.element(guid)
        return self._parsed[guid]

    def asset(self, guid: int) -> ET.Element:
        """The asset with this change set's edits."""
        base = self.vanilla(guid)
        if base is None:
            base = self._new_element(guid)
        change = self.changes.assets.get(guid)
        return apply_change(base, change) if change and not change.empty else copy.deepcopy(base)

    def _new_element(self, guid: int) -> ET.Element:
        n = self.changes.new[guid]
        if n.xml:
            return parse(n.xml)
        asset = ET.Element("Asset")
        if n.base is None:
            ET.SubElement(asset, "Template").text = n.template
        else:
            ET.SubElement(asset, "BaseAssetGUID").text = str(n.base)
        values = ET.SubElement(asset, "Values")
        if n.copy_of is not None and self.exists(n.copy_of):
            values.extend(copy.deepcopy(list(values_of(self.asset(n.copy_of)))))
        elif n.base is None:  # a new asset lists its template's properties, empty, as the games' own do
            t = self.schema.templates.get(n.template)
            values.extend(ET.Element(name) for name in (t.property_names if t else []))
        standard = values.find("Standard")
        if standard is None:
            standard = ET.Element("Standard")
            values.insert(0, standard)
        for tag, text in (("GUID", str(guid)), ("Name", n.name)):
            el = standard.find(tag)
            (el if el is not None else ET.SubElement(standard, tag)).text = text
        return asset

    def name(self, guid: int) -> str:
        if guid in self.changes.new:
            return self.changes.new[guid].name
        e = self.index.by_guid.get(guid)
        return e.name if e else ""

    def template(self, guid: int) -> str:
        if guid in self.changes.new:
            n = self.changes.new[guid]
            return n.template or self.template(n.base) if n.base is not None else n.template
        e = self.index.by_guid.get(guid)
        return e.template if e else ""

    def base_of(self, guid: int) -> int | None:
        if guid in self.changes.new:
            return self.changes.new[guid].base
        e = self.index.by_guid.get(guid)
        return e.base if e else None

    def layers(self, guid: int, original: bool = False) -> Layers:
        """The asset's values: its own (edited), each base asset's up the chain, the template's, the defaults.
        ``original``: as the game's files have them, no edit of the change set (a new asset has none: its own)."""

        def element(g: int) -> ET.Element:
            return self.vanilla(g) if original and self.vanilla(g) is not None else self.asset(g)

        out = [Layer(OWN, values_of(element(guid)))]
        seen, node = {guid}, self.base_of(guid)
        while node is not None and node not in seen and len(seen) < DEPTH and self.exists(node):
            seen.add(node)
            out.append(Layer(BASE, values_of(element(node)), node))
            node = self.base_of(node)
        t = self.schema.templates.get(self.template(guid))
        out.append(Layer("template", template_values(t.properties if t else None)))
        layers = Layers(out)
        out.append(Layer("default", defaults_layer(self.schema.defaults, layers.property_names())))
        return layers

    @property
    def dirty(self) -> bool:
        return self.undo.dirty

    # editing
    def _edit(self, guid: int | None, label: str, do, undo) -> None:
        do()
        self.undo.push(Command(label, lambda: (undo(), self._tell(guid)), lambda: (do(), self._tell(guid))))
        self._tell(guid)

    def _tell(self, guid: int | None) -> None:
        for f in list(self.listeners):
            f(guid)

    def _snapshot(self, guid: int):
        had = guid in self.changes.assets
        before = copy.deepcopy(self.changes.assets.get(guid))

        def restore():
            if had:
                self.changes.assets[guid] = copy.deepcopy(before)
            else:
                self.changes.assets.pop(guid, None)

        return restore

    def _change_edit(self, guid: int, label: str, mutate) -> None:
        restore = self._snapshot(guid)
        after: list[AssetChange] = []

        def do():
            if after:
                self.changes.assets[guid] = copy.deepcopy(after[0])
                return
            mutate(self.changes.change(guid))
            after.append(copy.deepcopy(self.changes.assets[guid]))

        self._edit(guid, label, do, restore)

    def set_value(self, guid: int, path: str, text: str) -> None:
        """Set a leaf value; inside a replaced block the block itself changes."""
        block = self._block_of(guid, path)
        if block is not None:
            el = ET.fromstring(self.changes.assets[guid].blocks[block])
            inner = path[len(block) + 1 :].split("/")
            target = find_path(el, inner) if inner else el
            if target is None:
                return
            target.text = text
            return self.set_block(
                guid, block, el, label=tr("{name} = {value}", name=path.rsplit("/", 1)[-1], value=text)
            )

        def mutate(c: AssetChange) -> None:
            c.set[path] = text
            if path in c.reset:
                c.reset.remove(path)

        self._change_edit(guid, tr("{name} = {value}", name=path.rsplit("/", 1)[-1], value=text), mutate)

    def reset_value(self, guid: int, path: str) -> None:
        """Back to what the asset inherits: the edit dropped and, where the game's file sets it, its own value too."""
        vanilla = self.vanilla(guid)
        own = find_path(values_of(vanilla), path) if vanilla is not None else None

        def mutate(c: AssetChange) -> None:
            c.set.pop(path, None)
            c.blocks.pop(path, None)
            for p in [p for p in c.set if p.startswith(path + "/")]:
                c.set.pop(p)
            if own is not None and path not in c.reset:
                c.reset.append(path)

        self._change_edit(guid, tr("reset {name}", name=path.rsplit("/", 1)[-1]), mutate)

    def set_block(self, guid: int, path: str, element: ET.Element, label: str = "") -> None:
        """Replace a whole block (a list, an array, an inline asset) at ``path``; values set below it fold in."""

        def mutate(c: AssetChange) -> None:
            c.blocks[path] = to_text(element)
            for p in [p for p in c.set if p.startswith(path + "/")]:
                c.set.pop(p)
            if path in c.reset:
                c.reset.remove(path)

        self._change_edit(guid, label or tr("{name} changed", name=path.rsplit("/", 1)[-1]), mutate)

    def _block_of(self, guid: int, path: str) -> str | None:
        c = self.changes.assets.get(guid)
        return next((b for b in (c.blocks if c else ()) if path.startswith(b + "/")), None)

    def new_asset(
        self,
        template: str,
        name: str,
        *,
        base: int | None = None,
        copy_of: int | None = None,
        anchor: int | None = None,
    ) -> int:
        """A new asset from a template, from a base asset or as a copy; its GUID from the change set's range, or from
        the range of the group of ``anchor`` (the asset it is written beside; by default its base or original)."""
        anchor = anchor if anchor is not None else copy_of if copy_of is not None else base
        if anchor is None:
            anchor = next((e.guid for e in self.index.entries if e.template == template), None)
        guid = self.changes.next_guid(self.index.by_guid, self.group_range(anchor))
        record = NewAsset(template, name, base, copy_of, anchor if anchor in self.index.by_guid else None)
        if copy_of is not None and not template:
            record.template = self.template(copy_of)
        self._edit(None, tr("new {name}", name=name), lambda: self.changes.new.__setitem__(guid, record),
                   lambda: self.changes.new.pop(guid, None))  # fmt: skip
        return guid

    def group_range(self, anchor: int | None) -> tuple[int, int] | None:
        """The GUID range of the group an asset is in, or of the nearest group above it that has one, where the game's
        file gives them (2205's ``GUIDRangeMin`` and ``GUIDRangeMax``); new assets beside it take their numbers from it."""
        entry = self.index.by_guid.get(anchor) if anchor is not None else None
        if entry is None:
            return None
        key = self.index.groups[entry.group]
        while key:  # a group that gives none uses its parent's (``UseParentGUIDRange``)
            if key in self.index.group_ranges:
                return self.index.group_ranges[key]
            key = key[:-1]
        return None

    def move_new(self, guid: int, anchor: int, place: str = "after") -> None:
        """A new asset of this change set to the group of ``anchor`` (a cut and paste that keeps the GUID)."""
        record = self.changes.new[guid]
        old = (record.anchor, record.place)

        def put(anchor_to, place_to):
            record.anchor, record.place = anchor_to, place_to

        self._edit(guid, tr("move {name}", name=record.name), lambda: put(anchor, place), lambda: put(*old))

    def paste(self, guids: list[int], anchor: int, cut: bool = False) -> list[int]:
        """Copies of ``guids`` beside ``anchor``, in its group, each with a GUID of the group's range. ``cut``: a
        new asset of this change set is moved (its GUID kept), a game asset is copied and removed, which changes its
        GUID: whatever names it needs the new one. Returns the GUIDs now holding the assets."""
        out = []
        for guid in guids:
            if cut and guid in self.changes.new:
                self.move_new(guid, anchor)
                out.append(guid)
                continue
            made = self.new_asset("", self.name(guid), copy_of=guid, anchor=anchor)
            if cut:
                self.remove_asset(guid)
            out.append(made)
        return out

    def remove_asset(self, guid: int) -> None:
        if guid in self.changes.new:
            record, change = self.changes.new[guid], self.changes.assets.get(guid)

            def undo():
                self.changes.new[guid] = record
                if change is not None:
                    self.changes.assets[guid] = change

            return self._edit(None, tr("delete {name}", name=record.name), lambda: self._drop_new(guid), undo)
        if guid in self.changes.removed:
            return
        self._edit(None, tr("delete {name}", name=self.name(guid)), lambda: self.changes.removed.append(guid),
                   lambda: self.changes.removed.remove(guid))  # fmt: skip

    def _drop_new(self, guid: int) -> None:
        self.changes.new.pop(guid, None)
        self.changes.assets.pop(guid, None)

    def rename(self, guid: int, name: str) -> None:
        if guid in self.changes.new:
            record, old = self.changes.new[guid], self.changes.new[guid].name
            return self._edit(guid, tr("rename to {name}", name=name), lambda: setattr(record, "name", name),
                              lambda: setattr(record, "name", old))  # fmt: skip
        self.set_value(guid, "Standard/Name", name)

    def set_guid_range(self, span: tuple[int, int], choice: str, source: str = "") -> None:
        """The range new assets take their numbers from, and how it was chosen (one undoable step)."""
        c = self.changes
        old = (c.guid_range, c.guid_choice, c.guid_source)

        def put(value):
            c.guid_range, c.guid_choice, c.guid_source = value

        self._edit(None, tr("GUID range"), lambda: put((tuple(span), choice, source)), lambda: put(old))

    def set_icon(self, guid: int, file_id: int, index: int, position: int = 0) -> None:
        """The asset's icon number ``position`` (0: the first) is tile ``index`` of atlas ``file_id`` (written to
        ``icons.xml``); one undoable step."""
        icons, old = self.changes.icons, copy.deepcopy(self.changes.icons.get(guid))

        def do() -> None:
            tiles = icons.setdefault(guid, [])
            tiles += [[] for _ in range(position + 1 - len(tiles))]
            tiles[position] = [file_id, index]

        def undo() -> None:
            if old is None:
                icons.pop(guid, None)
            else:
                icons[guid] = copy.deepcopy(old)

        self._edit(guid, tr("icon of {guid}", guid=guid), do, undo)

    def set_text(self, language: str, text_id: str, text: str) -> None:
        texts = self.changes.texts.setdefault(language, {})
        old = texts.get(text_id)

        def undo():
            if old is None:
                texts.pop(text_id, None)
            else:
                texts[text_id] = old

        self._edit(None, tr("text {id}", id=text_id), lambda: texts.__setitem__(text_id, text), undo)

    # the file
    def save(self, path: Path | None = None) -> Path:
        self.path = path or self.path
        if self.path is None:
            raise ValueError("no file to save the change set to")
        self.changes.save(self.path)
        self.undo.mark_clean()
        return self.path

    def guids(self) -> list[int]:
        """Every asset there is with this change set: the game's (not removed) and the new ones."""
        removed = set(self.changes.removed)
        return [e.guid for e in self.index.entries if e.guid not in removed] + list(self.changes.new)
