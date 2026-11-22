"""An asset change set open in the editor, without a window: the game's schema and asset index, the change set, one
undo stack, and every edit the window offers (set, reset, replace a list, new from a template, copy, remove, rename).

``CAssetDocument.layers(guid)`` gives an asset's values in their layers with the edits applied, ``asset(guid)`` the
edited ``<Asset>``; each edit is one ``suite.undo.SCommand``. The window (``assetwindow``) draws it, the tests drive it
on synthetic files. Plan: ``docs/tool-suite.md`` A3-A5.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

from ..infra.i18n import tr
from ..suite.undo import CUndoStack, SCommand
from .assetindex import CAssetIndex
from .changeset import CChangeSet, SAssetChange, SNewAsset
from .origins import GAME, PERSON
from .schema import SSchema
from .values import BASE, OWN, CLayers, SLayer, apply_change, defaults_layer, template_values, values_of
from .xmltree import find_path, parse, to_text

DEPTH = 32  # base asset chains deeper than this are a loop


class CAssetDocument:
    def __init__(self, schema: SSchema, index: CAssetIndex, changes: CChangeSet, path: Path | None = None) -> None:
        self.m_schema, self.m_index, self.m_changes, self.m_path = schema, index, changes, path
        self.m_undo = CUndoStack()
        self.m_listeners: list = []  # called with the GUID an edit touched (None: the tree changed)
        self.m_game_texts = None  # language -> the game's texts by id (``Game.texts``), for the writers
        self.m_files = None  # the game's files (``gamefiles.CGameFiles``), for the native text writer
        self.m_layer = None  # the mod laid under the edits as their base (``origins.SLayer``), None: the game itself
        self._m_parsed: dict[int, ET.Element] = {}

    def snapshot(self) -> CAssetDocument:
        """A read-only copy of the document as it is now, for a background job (check, build, write): the window
        goes on editing the original while the job reads this one. Shares the game's files and schema, which no
        edit touches; the change set and the parsed assets are the copy's own."""
        copy_ = CAssetDocument(self.m_schema, self.m_index, copy.deepcopy(self.m_changes), self.m_path)
        copy_.m_game_texts, copy_.m_files, copy_.m_layer = self.m_game_texts, self.m_files, self.m_layer
        copy_._m_parsed = dict(self._m_parsed)
        return copy_

    # reading
    def exists(self, guid: int) -> bool:
        return (guid in self.m_index.m_by_guid or guid in self.m_changes.m_new) and guid not in self.m_changes.m_removed

    def vanilla(self, guid: int) -> ET.Element | None:
        """The asset as the game's files have it (parsed once), None for a new one."""
        if guid not in self.m_index.m_by_guid:
            return None
        if guid not in self._m_parsed:
            self._m_parsed[guid] = self.m_index.element(guid)
        return self._m_parsed[guid]

    def asset(self, guid: int) -> ET.Element:
        """The asset with this change set's edits."""
        base = self.vanilla(guid)
        if base is None:
            base = self._new_element(guid)
        change = self.m_changes.m_assets.get(guid)
        return apply_change(base, change) if change and not change.empty else copy.deepcopy(base)

    def _new_element(self, guid: int) -> ET.Element:
        added = self.m_changes.m_new[guid]
        if added.xml:
            return parse(added.xml)
        asset = ET.Element("Asset")
        if added.base is None:
            ET.SubElement(asset, "Template").text = added.template
        else:
            ET.SubElement(asset, "BaseAssetGUID").text = str(added.base)
        values = ET.SubElement(asset, "Values")
        if added.copy_of is not None and self.exists(added.copy_of):
            values.extend(copy.deepcopy(list(values_of(self.asset(added.copy_of)))))
        elif added.base is None:  # a new asset lists its template's properties, empty, as the games' own do
            template_spec = self.m_schema.templates.get(added.template)
            values.extend(ET.Element(name) for name in (template_spec.property_names if template_spec else []))
        standard = values.find("Standard")
        if standard is None:
            standard = ET.Element("Standard")
            values.insert(0, standard)
        for tag, text in (("GUID", str(guid)), ("Name", added.name)):
            element = standard.find(tag)
            (element if element is not None else ET.SubElement(standard, tag)).text = text
        return asset

    def origin(self, guid: int) -> str:
        """Where the asset comes from: ``origins.PERSON`` (added in this change set), ``MOD_NEW`` and ``MOD_CHANGED``
        (the mod laid under the edits adds or changes it), else ``GAME``."""
        if guid in self.m_changes.m_new:
            return PERSON
        return self.m_layer.origin(guid) if self.m_layer is not None else GAME

    def name(self, guid: int) -> str:
        if guid in self.m_changes.m_new:
            return self.m_changes.m_new[guid].name
        renamed = self.m_changes.m_assets.get(guid)
        if renamed is not None and "Standard/Name" in renamed.set:  # a rename of the game's asset (``rename``)
            return renamed.set["Standard/Name"]
        entry = self.m_index.m_by_guid.get(guid)
        return entry.name if entry else ""

    def template(self, guid: int) -> str:
        if guid in self.m_changes.m_new:
            added = self.m_changes.m_new[guid]
            return added.template or self.template(added.base) if added.base is not None else added.template
        entry = self.m_index.m_by_guid.get(guid)
        return entry.template if entry else ""

    def base_of(self, guid: int) -> int | None:
        if guid in self.m_changes.m_new:
            return self.m_changes.m_new[guid].base
        entry = self.m_index.m_by_guid.get(guid)
        return entry.base if entry else None

    def layers(self, guid: int, original: bool = False) -> CLayers:
        """The asset's values: its own (edited), each base asset's up the chain, the template's, the defaults.
        ``original``: as the game's files have them, no edit of the change set (a new asset has none: its own)."""

        def element(asset: int) -> ET.Element:
            return self.vanilla(asset) if original and self.vanilla(asset) is not None else self.asset(asset)

        out = [SLayer(OWN, values_of(element(guid)))]
        seen, node = {guid}, self.base_of(guid)
        while node is not None and node not in seen and len(seen) < DEPTH and self.exists(node):
            seen.add(node)
            out.append(SLayer(BASE, values_of(element(node)), node))
            node = self.base_of(node)
        template_spec = self.m_schema.templates.get(self.template(guid))
        out.append(SLayer("template", template_values(template_spec.properties if template_spec else None)))
        layers = CLayers(out)
        out.append(SLayer("default", defaults_layer(self.m_schema.defaults, layers.property_names())))
        return layers

    @property
    def dirty(self) -> bool:
        return self.m_undo.dirty

    # editing
    def _edit(self, guid: int | None, label: str, redo, undo) -> None:
        redo()
        self.m_undo.push(SCommand(label, lambda: (undo(), self._tell(guid)), lambda: (redo(), self._tell(guid))))
        self._tell(guid)

    def _tell(self, guid: int | None) -> None:
        for listener in list(self.m_listeners):
            listener(guid)

    def _snapshot(self, guid: int):
        had = guid in self.m_changes.m_assets
        before = copy.deepcopy(self.m_changes.m_assets.get(guid))

        def restore():
            if had:
                self.m_changes.m_assets[guid] = copy.deepcopy(before)
            else:
                self.m_changes.m_assets.pop(guid, None)

        return restore

    def _change_edit(self, guid: int, label: str, mutate) -> None:
        restore = self._snapshot(guid)
        after: list[SAssetChange] = []

        def redo():
            if after:
                self.m_changes.m_assets[guid] = copy.deepcopy(after[0])
                return
            mutate(self.m_changes.change(guid))
            after.append(copy.deepcopy(self.m_changes.m_assets[guid]))

        self._edit(guid, label, redo, restore)

    def set_value(self, guid: int, path: str, text: str) -> None:
        """Set a leaf value; inside a replaced block the block itself changes."""
        block = self._block_of(guid, path)
        if block is not None:
            element = ET.fromstring(self.m_changes.m_assets[guid].blocks[block])
            inner = path[len(block) + 1 :].split("/")
            target = find_path(element, inner) if inner else element
            if target is None:
                return
            target.text = text
            return self.set_block(
                guid, block, element, label=tr("assets.name_eq_value", name=path.rsplit("/", 1)[-1], value=text)
            )

        def mutate(change: SAssetChange) -> None:
            change.set[path] = text
            if path in change.reset:
                change.reset.remove(path)

        self._change_edit(guid, tr("assets.name_eq_value", name=path.rsplit("/", 1)[-1], value=text), mutate)

    def reset_value(self, guid: int, path: str) -> None:
        """Back to what the asset inherits: the edit dropped and, where the game's file sets it, its own value too."""
        vanilla = self.vanilla(guid)
        own = find_path(values_of(vanilla), path) if vanilla is not None else None

        def mutate(change: SAssetChange) -> None:
            change.set.pop(path, None)
            change.blocks.pop(path, None)
            for child_path in [child_path for child_path in change.set if child_path.startswith(path + "/")]:
                change.set.pop(child_path)
            if own is not None and path not in change.reset:
                change.reset.append(path)

        self._change_edit(guid, tr("assets.reset_name", name=path.rsplit("/", 1)[-1]), mutate)

    def set_block(self, guid: int, path: str, element: ET.Element, label: str = "") -> None:
        """Replace a whole block (a list, an array, an inline asset) at ``path``; values set below it fold in."""

        def mutate(change: SAssetChange) -> None:
            change.blocks[path] = to_text(element)
            for child_path in [child_path for child_path in change.set if child_path.startswith(path + "/")]:
                change.set.pop(child_path)
            if path in change.reset:
                change.reset.remove(path)

        self._change_edit(guid, label or tr("assets.name_changed", name=path.rsplit("/", 1)[-1]), mutate)

    def _block_of(self, guid: int, path: str) -> str | None:
        change = self.m_changes.m_assets.get(guid)
        return next((block for block in (change.blocks if change else ()) if path.startswith(block + "/")), None)

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
            anchor = next((entry.guid for entry in self.m_index.m_entries if entry.template == template), None)
        guid = self.m_changes.next_guid(self.m_index.m_by_guid, self.group_range(anchor))
        record = SNewAsset(template, name, base, copy_of, anchor if anchor in self.m_index.m_by_guid else None)
        if copy_of is not None and not template:
            record.template = self.template(copy_of)
        self._edit(None, tr("assets.new_name_lower", name=name), lambda: self.m_changes.m_new.__setitem__(guid, record),
                   lambda: self.m_changes.m_new.pop(guid, None))  # fmt: skip
        return guid

    def group_range(self, anchor: int | None) -> tuple[int, int] | None:
        """The GUID range of the group an asset is in, or of the nearest group above it that has one, where the game's
        file gives them (2205's ``GUIDRangeMin`` and ``GUIDRangeMax``); new assets beside it take their numbers from it."""
        entry = self.m_index.m_by_guid.get(anchor) if anchor is not None else None
        if entry is None:
            return None
        key = self.m_index.m_groups[entry.group]
        while key:  # a group that gives none uses its parent's (``UseParentGUIDRange``)
            if key in self.m_index.m_group_ranges:
                return self.m_index.m_group_ranges[key]
            key = key[:-1]
        return None

    def move_new(self, guid: int, anchor: int, place: str = "after") -> None:
        """A new asset of this change set to the group of ``anchor`` (a cut and paste that keeps the GUID)."""
        record = self.m_changes.m_new[guid]
        old = (record.anchor, record.place)

        def put(anchor_to, place_to):
            record.anchor, record.place = anchor_to, place_to

        self._edit(guid, tr("assets.move_name", name=record.name), lambda: put(anchor, place), lambda: put(*old))

    def paste(self, guids: list[int], anchor: int, cut: bool = False) -> list[int]:
        """Copies of ``guids`` beside ``anchor``, in its group, each with a GUID of the group's range. ``cut``: a
        new asset of this change set is moved (its GUID kept), a game asset is copied and removed, which changes its
        GUID: whatever names it needs the new one. Returns the GUIDs now holding the assets."""
        out = []
        for guid in guids:
            if cut and guid in self.m_changes.m_new:
                self.move_new(guid, anchor)
                out.append(guid)
                continue
            made = self.new_asset("", self.name(guid), copy_of=guid, anchor=anchor)
            if cut:
                self.remove_asset(guid)
            out.append(made)
        return out

    def remove_asset(self, guid: int) -> None:
        if guid in self.m_changes.m_new:
            record, change = self.m_changes.m_new[guid], self.m_changes.m_assets.get(guid)

            def undo():
                self.m_changes.m_new[guid] = record
                if change is not None:
                    self.m_changes.m_assets[guid] = change

            return self._edit(None, tr("assets.delete_name", name=record.name), lambda: self._drop_new(guid), undo)
        if guid in self.m_changes.m_removed:
            return
        self._edit(None, tr("assets.delete_name", name=self.name(guid)), lambda: self.m_changes.m_removed.append(guid),
                   lambda: self.m_changes.m_removed.remove(guid))  # fmt: skip

    def _drop_new(self, guid: int) -> None:
        self.m_changes.m_new.pop(guid, None)
        self.m_changes.m_assets.pop(guid, None)

    def rename(self, guid: int, name: str) -> None:
        if guid in self.m_changes.m_new:
            record, old = self.m_changes.m_new[guid], self.m_changes.m_new[guid].name
            return self._edit(guid, tr("assets.rename_to_name", name=name), lambda: setattr(record, "name", name),
                              lambda: setattr(record, "name", old))  # fmt: skip
        self.set_value(guid, "Standard/Name", name)

    def set_guid_range(self, span: tuple[int, int], choice: str, source: str = "") -> None:
        """The range new assets take their numbers from, and how it was chosen (one undoable step)."""
        changes = self.m_changes
        old = (changes.m_guid_range, changes.m_guid_choice, changes.m_guid_source)

        def put(value):
            changes.m_guid_range, changes.m_guid_choice, changes.m_guid_source = value

        self._edit(None, tr("common.guid_range"), lambda: put((tuple(span), choice, source)), lambda: put(old))

    def set_icon(self, guid: int, file_id: int, index: int, position: int = 0) -> None:
        """The asset's icon number ``position`` (0: the first) is tile ``index`` of atlas ``file_id`` (written to
        ``icons.xml``); one undoable step."""
        icons, old = self.m_changes.m_icons, copy.deepcopy(self.m_changes.m_icons.get(guid))

        def redo() -> None:
            tiles = icons.setdefault(guid, [])
            tiles += [[] for _ in range(position + 1 - len(tiles))]
            tiles[position] = [file_id, index]

        def undo() -> None:
            if old is None:
                icons.pop(guid, None)
            else:
                icons[guid] = copy.deepcopy(old)

        self._edit(guid, tr("assets.icon_of_guid", guid=guid), redo, undo)

    def set_text(self, language: str, text_id: str, text: str) -> None:
        texts = self.m_changes.m_texts.setdefault(language, {})
        old = texts.get(text_id)

        def undo():
            if old is None:
                texts.pop(text_id, None)
            else:
                texts[text_id] = old

        self._edit(None, tr("assets.text_id_lower", id=text_id), lambda: texts.__setitem__(text_id, text), undo)

    # the file
    def save(self, path: Path | None = None) -> Path:
        self.m_path = path or self.m_path
        if self.m_path is None:
            raise ValueError("no file to save the change set to")
        self.m_changes.save(self.m_path)
        self.m_undo.mark_clean()
        return self.m_path

    def guids(self) -> list[int]:
        """Every asset there is with this change set: the game's (not removed) and the new ones."""
        removed = set(self.m_changes.m_removed)
        return [entry.guid for entry in self.m_index.m_entries if entry.guid not in removed] + list(
            self.m_changes.m_new
        )
