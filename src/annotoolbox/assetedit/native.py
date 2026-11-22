"""A change set written in the game's own form: every asset file it touches written whole, with the edited assets in
place, the new ones beside their anchors and the removed ones gone; the rest of the file byte for byte as shipped.

``render(doc)`` returns the files by game path; ``write_tree`` puts them under a folder in the game's layout (the
loose files 117, 1800 and 2205 read over their archives with ``PreferLocalFiles``, engine-notes "Anno 117"), and
``write_archive`` packs them into one archive for the games that load an archive over their own (2070, 1404; the
install step is open, ``docs/tool-suite.md`` A9). None of these was loaded by a game yet.
"""

from __future__ import annotations

from pathlib import Path

from ..formats.rda import write_v20
from ..sources import icons2070
from ..textedit import render as textrender
from ..textedit import textfiles, texttable
from . import game1701, modops
from .assetdoc import CAssetDocument
from .gamefiles import LAYOUTS
from .xmltree import to_text


def placements(doc: CAssetDocument) -> tuple[dict[int, list[int]], dict[int, list[int]], list[int]]:
    """Where the new assets go, as the loader places them: the ones before and after each game asset, in file order
    (an ``addNextSibling`` puts its asset right after the anchor, ahead of the ones put there before), and those
    without an anchor."""
    before: dict[int, list[int]] = {}
    after: dict[int, list[int]] = {}
    loose: list[int] = []
    home: dict[int, list[int]] = {}  # a placed new asset -> the list it is in
    for guid in modops.new_order(doc.m_changes):
        record = doc.m_changes.m_new[guid]
        anchor = record.anchor
        if anchor in home:
            row = home[anchor]
            row.insert(row.index(anchor) + (0 if record.place == "before" else 1), guid)
        elif anchor in doc.m_index.m_by_guid and doc.exists(anchor):
            row = (before if record.place == "before" else after).setdefault(anchor, [])
            row.insert(len(row) if record.place == "before" else 0, guid)
        else:
            loose.append(guid)
            continue
        home[guid] = row
    return before, after, loose


def _asset_bytes(doc: CAssetDocument, guids: list[int]) -> bytes:
    return b"".join(to_text(doc.asset(guid)).encode("utf-8") + b"\n" for guid in guids)


def _pieces(doc: CAssetDocument, file: int, before: dict, after: dict) -> list[tuple[int, int, bytes]]:
    """(start, end, replacement) of every asset of the file the change set changes, removes or anchors."""
    out = []
    removed = set(doc.m_changes.m_removed)
    for entry in doc.m_index.m_entries:
        if entry.file != file or doc.m_index.m_by_guid.get(entry.guid) is not entry:
            continue
        changed = entry.guid in doc.m_changes.m_assets and not doc.m_changes.m_assets[entry.guid].empty
        if entry.guid not in removed and not changed and entry.guid not in before and entry.guid not in after:
            continue
        text = "" if entry.guid in removed else (to_text(doc.asset(entry.guid)) if changed else None)
        body = doc.m_index.raw(entry.guid) if text is None else text.encode("utf-8")
        ahead = _asset_bytes(doc, before.get(entry.guid, []))
        behind = b"".join(b"\n" + to_text(doc.asset(guid)).encode("utf-8") for guid in after.get(entry.guid, ()))
        out.append((entry.start, entry.end, ahead + body + behind))
    return out


def render(doc: CAssetDocument) -> dict[str, bytes]:
    """The asset files the change set touches, whole, by their game path, and the files it carries."""
    files = game1701.render_files(doc) if game1701.is_1701(doc) else _asset_files(doc)
    files |= render_texts(doc)
    files |= render_icons(doc)
    for relative_path, source in modops.carried_files(
        doc.m_changes
    ).items():  # an imported mod's other files, as they are
        files.setdefault(relative_path, source.read_bytes())
    return files


def render_icons(doc: CAssetDocument) -> dict[str, bytes]:
    """The ``icons.xml`` the change set's icons change, whole: the file that holds each GUID's entry (a new one goes
    to the last icon file of the game, 1404's add-on one)."""
    if not doc.m_changes.m_icons or doc.m_files is None:
        return {}
    have = [path for path in icons2070.ICONS_FILES if doc.m_files.exists(path)]
    if not have:
        return {}
    texts = {path: doc.m_files.read(path) for path in have}
    for guid, tiles in doc.m_changes.m_icons.items():
        home = next((path for path in have if icons2070.has_entry(texts[path], guid)), have[-1])
        texts[home] = icons2070.set_icons(texts[home], {guid: tiles})
    return {path: data for path, data in texts.items() if data != doc.m_files.read(path)}


def _asset_files(doc: CAssetDocument) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    before, after, loose = placements(doc)
    last_file = max((entry.file for entry in doc.m_index.m_entries), default=0)
    for k, path in enumerate(doc.m_index.m_files):
        pieces = _pieces(doc, k, before, after)
        if k == last_file and loose:  # new assets without an anchor: after the file's last asset
            last = max(
                (entry for entry in doc.m_index.m_entries if entry.file == k), key=lambda entry: entry.end, default=None
            )
            if last is not None:
                extra = b"".join(b"\n" + to_text(doc.asset(guid)).encode("utf-8") for guid in loose)
                hit = next((piece for piece in pieces if piece[0] == last.start), None)
                pieces = [piece for piece in pieces if piece is not hit]
                pieces.append((last.start, last.end, (hit[2] if hit else doc.m_index.raw(last.guid)) + extra))
        if not pieces:
            continue
        data = doc.m_index.m_local[k].read_bytes()
        out, offset = [], 0
        for start, end, body in sorted(pieces):
            out += [data[offset:start], body]
            offset = end
        out.append(data[offset:])
        files[path] = b"".join(out)
    return files


def render_texts(doc: CAssetDocument) -> dict[str, bytes]:
    """The text files the change set's texts change, whole, by the Texts app's writer (``textedit.textrender.whole_files``:
    an edited text replaced where its file has the id, a new one appended, a line break kept off the line games'
    lines)."""
    layout = LAYOUTS[doc.m_changes.m_game]
    files: dict[str, bytes] = {}
    for language, texts in doc.m_changes.m_texts.items():
        if not (texts and layout.texts and doc.m_files is not None):
            continue
        loaded = texttable.read_language(doc.m_files, layout, language)
        if not loaded.files and texttable.style_of(layout) == textfiles.XML:
            doc.m_files.read(layout.texts.format(language=layout.language_code(language)))  # says which file is missing
        files |= textrender.whole_files(loaded, layout, texts, doc.m_changes.m_mod_id)
    return files


def write_tree(files: dict[str, bytes], folder: Path) -> list[Path]:
    """The files under ``folder`` at their game paths."""
    written = []
    for path, data in files.items():
        destination = Path(folder) / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        written.append(destination)
    return written


def write_archive(files: dict[str, bytes], destination: Path) -> Path:
    """The files packed into one archive (the format 2070 and 1404 read, V2.0)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(write_v20(files))
    return destination
