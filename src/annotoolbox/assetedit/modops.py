"""A change set as a mod for the mod loader (117, 1800): ModOps on the game's asset file and on its text files, and
the ``modinfo.json``.

``render(doc)`` returns the patch files by path; ``write_mod(doc, folder)`` writes the mod folder and returns what
the mod check (``suite/modcheck``) finds. A changed value is a ``merge`` below the asset's ``Values`` (it makes the
missing parents), a replaced list a ``replace`` (a merge never shortens a list), an asset kept whole a ``replace`` of
the asset, a new asset an ``addNextSibling`` (``addPrevSibling``) beside its anchor (engine-modloader "ModOps"); an
imported mod's other files are copied along. ``docs/tool-suite.md`` A4 and section 17.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from ..infra.i18n import tr
from ..mod.asset117 import set_value
from ..mod.modxml import modinfo, modops
from ..modloader.mods import read_mod
from ..modloader.modswap import STAGING, InstallError, Trash, check_folder_name, recycle, swap_in
from ..suite import modcheck
from .assetdoc import AssetDocument
from .changeset import AssetChange, ChangeSet
from .gamefiles import LAYOUTS, Layout
from .values import apply_change, values_of
from .xmltree import find_path, parse, to_text

COMMENT = "written by the asset editor"


def _attr(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


def asset_ops(doc: AssetDocument, guid: int) -> list[str]:
    """The operations that turn the game's asset ``guid`` into the edited one."""
    change = doc.changes.assets.get(guid)
    if change is None or change.empty:
        return []
    return change_ops(guid, doc.vanilla(guid), change)


def change_ops(guid: int, asset: ET.Element, change: AssetChange) -> list[str]:
    """The operations that turn the game's ``asset`` into the one ``change`` makes of it."""
    if change.whole:
        return [f'<ModOp Type="replace" GUID="{guid}">\n{to_text(apply_change(asset, change))}\n</ModOp>']
    vanilla = values_of(asset)
    ops, merge = [], ET.Element("Values")
    for path, xml in change.blocks.items():
        if find_path(vanilla, path) is not None:
            ops.append(f'<ModOp Type="replace" GUID="{guid}" Path="/Values/{path}">\n{xml}\n</ModOp>')
        else:
            set_value(merge, path, parse(xml))
    for path, value in change.set.items():
        set_value(merge, path, value)
    if len(merge):
        body = "\n".join(to_text(c) for c in merge)
        ops.insert(0, f'<ModOp Type="merge" GUID="{guid}" Path="/Values">\n{body}\n</ModOp>')
    ops += [f'<ModOp Type="remove" GUID="{guid}" Path="/Values/{p}" AllowNoMatch="1" />' for p in change.reset]
    return ops


def new_asset_op(doc: AssetDocument, guid: int) -> str:
    asset = to_text(doc.asset(guid))
    record = doc.changes.new[guid]
    if record.anchor is not None and doc.exists(record.anchor):
        kind = "addPrevSibling" if record.place == "before" else "addNextSibling"
        return f'<ModOp Type="{kind}" GUID="{record.anchor}">\n{asset}\n</ModOp>'
    return asset  # appended to the asset file's last group (engine-modloader "Where new assets go")


def render(doc: AssetDocument, layout: Layout | None = None) -> dict[str, str]:
    """The patch files of the change set, by their path in the game's tree."""
    layout = layout or LAYOUTS[doc.changes.game]
    if not layout.patch_target:
        raise ValueError(tr("assets.no_mod_loader_write_files", game=layout.name))
    ops = [new_asset_op(doc, g) for g in new_order(doc.changes)]
    for guid in sorted(doc.changes.assets):
        if guid in doc.index.by_guid and guid not in doc.changes.removed:
            ops += asset_ops(doc, guid)
    ops += [f"<ModOp Type=\"remove\" Path=\"//Asset[Values/Standard/GUID='{g}']\" />" for g in doc.changes.removed
            if g in doc.index.by_guid]  # fmt: skip
    out = {layout.patch_target: modops(ops, COMMENT)} if ops else {}
    for language, texts in doc.changes.texts.items():
        if texts and layout.texts:
            known = doc.game_texts(language) if doc.game_texts else None
            out[layout.texts.format(language=language)] = modops(text_ops(texts, layout, known), COMMENT)
    return out


def text_ops(texts: dict[str, str], layout: Layout, known=None) -> list[str]:
    """A text per id: replaced where the game has it, added where it does not. ``known``: the game's text ids;
    without them each text is a ``replace`` with ``AllowNoMatch`` and an ``add`` on the condition that it is absent,
    with them a ``replace`` per known text and one ``add`` of all the new ones (each lookup costs the loader a scan)."""
    key = "LineId" if layout.key == "anno117" else "GUID"
    ops, new = [], []
    for tid, value in texts.items():
        el = ET.Element("Text")
        el.text = value
        entry = f"<Text><{key}>{tid}</{key}>{to_text(el)}</Text>"
        cond = f"/TextExport/Texts/Text[{key}='{_attr(tid)}']"
        if known is not None:
            if tid in known:
                ops.append(f'<ModOp Type="replace" Path="{cond}/Text">\n{to_text(el)}\n</ModOp>')
            else:
                new.append(entry)
            continue
        ops.append(f'<ModOp Type="replace" Path="{cond}/Text" AllowNoMatch="1">\n{to_text(el)}\n</ModOp>')
        ops.append(f'<ModOp Type="add" Path="/TextExport/Texts" Condition="!{cond}">\n{entry}\n</ModOp>')
    if new:
        ops.append('<ModOp Type="add" Path="/TextExport/Texts">\n' + "\n".join(new) + "\n</ModOp>")
    return ops


def info(doc: AssetDocument, layout: Layout) -> str:
    c = doc.changes
    description = f"Asset changes for {layout.name}, written by the asset editor."
    if layout.key == "anno117":
        needs = {"Require": list(c.requires)} if c.requires else None
        return modinfo(c.mod_id, "Balancing", c.mod_name, description, version=c.version, dependencies=needs)
    data = {
        "ModID": c.mod_id,
        "Version": c.version,
        "ModName": {"English": c.mod_name},
        "Category": {"English": "Balancing"},
        "Description": {"English": description},
    }
    return json.dumps(data, indent=2) + "\n"


def write_mod(
    doc: AssetDocument, mods: Path, layout: Layout | None = None, game=None, trash: Trash = recycle
) -> tuple[Path, list[str]]:
    """The mod folder ``<mods>/<mod id>`` with the patches and ``modinfo.json``, written beside it and swapped in
    (the older copy to ``trash``, the recycle bin, ``modloader.modswap``); returns the folder and the mod check's
    findings (``game``: the game's files, to check the patched paths). Another mod's folder is never replaced."""
    layout = layout or LAYOUTS[doc.changes.game]
    mod_id = check_folder_name(doc.changes.mod_id)
    folder = Path(mods) / mod_id
    if folder.resolve() in {Path(c.folder).resolve() for c in doc.changes.carry}:
        raise ValueError(tr("assets.folder_change_set_imported_write", folder=folder))
    _check_ours(folder, mod_id)
    written = {**render(doc, layout), "modinfo.json": info(doc, layout)}
    folder.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix=STAGING, dir=folder.parent) as stage:
        staged = Path(stage) / mod_id
        for path, text in written.items():
            dest = staged / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8", newline="\n")
        carry(doc.changes, staged, {p.lower() for p in written})
        swap_in(staged, folder, trash)
    return folder, modcheck.check(folder, modcheck.readable(game) if game is not None else None)


def _check_ours(folder: Path, mod_id: str) -> None:
    """An ``InstallError`` unless ``folder`` is absent, empty or the mod ``mod_id``: a build replaces it whole."""
    if not os.path.lexists(folder) or (folder.is_dir() and not any(folder.iterdir())):
        return
    old, _ = read_mod(folder) if folder.is_dir() else (None, "")
    if old is None or old.mod_id != mod_id:
        raise InstallError(
            tr(
                "common.folder_mods_holds_another_mod",
                folder=folder.name,
                mod=old.mod_id if old else tr("common.no_mod"),
            )
        )


def new_order(changes: ChangeSet) -> list[int]:
    """The new assets (not removed), each after the new asset it is anchored to."""
    pending = [g for g in changes.new if g not in changes.removed]
    done: set[int] = set()
    out: list[int] = []
    while pending:
        ready = [g for g in pending if changes.new[g].anchor not in changes.new or changes.new[g].anchor in done]
        for g in ready or pending[:1]:  # a loop of anchors: the rest in their own order
            done.add(g)
            out.append(g)
        pending = [g for g in pending if g not in done]
    return out


def carried_files(changes: ChangeSet) -> dict[str, Path]:
    """Mod-relative path -> source file of every file a build copies from the imported mods (a later one wins)."""
    out: dict[str, Path] = {}
    for c in changes.carry:
        root = Path(c.folder)
        skip = {s.lower() for s in c.skip} | {"modinfo.json", "modinfo.jsonc"}
        for path in _own_files(root):
            rel = path.relative_to(root).as_posix()
            if rel.lower() not in skip:
                out[rel] = path
    return out


def _own_files(root: Path) -> list[Path]:
    """The files under ``root`` that are the mod's own: the folders of mods nested in it (they have a ``modinfo``) are
    left out, an import carries those as mods of their own."""
    found: list[Path] = []
    for here, dirs, names in os.walk(root) if root.is_dir() else ():
        dirs[:] = sorted(
            d for d in dirs if not any((Path(here, d, n)).is_file() for n in ("modinfo.json", "modinfo.jsonc"))
        )
        found += [Path(here, n) for n in sorted(names)]
    return sorted(found)


def carry(changes: ChangeSet, folder: Path, written: set[str]) -> int:
    """The imported mods' other files copied into ``folder`` (never over a file the build wrote); the count."""
    n = 0
    for rel, src in carried_files(changes).items():
        if rel.lower() not in written:
            dest = folder / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
            n += 1
    return n
