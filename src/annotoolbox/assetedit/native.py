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
from .assetdoc import AssetDocument
from .gamefiles import LAYOUTS
from .xmltree import to_text


def placements(doc: AssetDocument) -> tuple[dict[int, list[int]], dict[int, list[int]], list[int]]:
    """Where the new assets go, as the loader places them: the ones before and after each game asset, in file order
    (an ``addNextSibling`` puts its asset right after the anchor, ahead of the ones put there before), and those
    without an anchor."""
    before: dict[int, list[int]] = {}
    after: dict[int, list[int]] = {}
    loose: list[int] = []
    home: dict[int, list[int]] = {}  # a placed new asset -> the list it is in
    for guid in modops.new_order(doc.changes):
        record = doc.changes.new[guid]
        anchor = record.anchor
        if anchor in home:
            row = home[anchor]
            row.insert(row.index(anchor) + (0 if record.place == "before" else 1), guid)
        elif anchor in doc.index.by_guid and doc.exists(anchor):
            row = (before if record.place == "before" else after).setdefault(anchor, [])
            row.insert(len(row) if record.place == "before" else 0, guid)
        else:
            loose.append(guid)
            continue
        home[guid] = row
    return before, after, loose


def _asset_bytes(doc: AssetDocument, guids: list[int]) -> bytes:
    return b"".join(to_text(doc.asset(g)).encode("utf-8") + b"\n" for g in guids)


def _pieces(doc: AssetDocument, file: int, before: dict, after: dict) -> list[tuple[int, int, bytes]]:
    """(start, end, replacement) of every asset of the file the change set changes, removes or anchors."""
    out = []
    removed = set(doc.changes.removed)
    for e in doc.index.entries:
        if e.file != file or doc.index.by_guid.get(e.guid) is not e:
            continue
        changed = e.guid in doc.changes.assets and not doc.changes.assets[e.guid].empty
        if e.guid not in removed and not changed and e.guid not in before and e.guid not in after:
            continue
        text = "" if e.guid in removed else (to_text(doc.asset(e.guid)) if changed else None)
        body = doc.index.raw(e.guid) if text is None else text.encode("utf-8")
        ahead = _asset_bytes(doc, before.get(e.guid, []))
        behind = b"".join(b"\n" + to_text(doc.asset(g)).encode("utf-8") for g in after.get(e.guid, ()))
        out.append((e.start, e.end, ahead + body + behind))
    return out


def render(doc: AssetDocument) -> dict[str, bytes]:
    """The asset files the change set touches, whole, by their game path, and the files it carries."""
    files = game1701.render_files(doc) if game1701.is_1701(doc) else _asset_files(doc)
    files |= render_texts(doc)
    files |= render_icons(doc)
    for rel, src in modops.carried_files(doc.changes).items():  # an imported mod's other files, as they are
        files.setdefault(rel, src.read_bytes())
    return files


def render_icons(doc: AssetDocument) -> dict[str, bytes]:
    """The ``icons.xml`` the change set's icons change, whole: the file that holds each GUID's entry (a new one goes
    to the last icon file of the game, 1404's add-on one)."""
    if not doc.changes.icons or doc.files is None:
        return {}
    have = [p for p in icons2070.ICONS_FILES if doc.files.exists(p)]
    if not have:
        return {}
    texts = {p: doc.files.read(p) for p in have}
    for guid, tiles in doc.changes.icons.items():
        home = next((p for p in have if icons2070.has_entry(texts[p], guid)), have[-1])
        texts[home] = icons2070.set_icons(texts[home], {guid: tiles})
    return {p: data for p, data in texts.items() if data != doc.files.read(p)}


def _asset_files(doc: AssetDocument) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    before, after, loose = placements(doc)
    last_file = max((e.file for e in doc.index.entries), default=0)
    for k, path in enumerate(doc.index.files):
        pieces = _pieces(doc, k, before, after)
        if k == last_file and loose:  # new assets without an anchor: after the file's last asset
            last = max((e for e in doc.index.entries if e.file == k), key=lambda e: e.end, default=None)
            if last is not None:
                extra = b"".join(b"\n" + to_text(doc.asset(g)).encode("utf-8") for g in loose)
                hit = next((p for p in pieces if p[0] == last.start), None)
                pieces = [p for p in pieces if p is not hit]
                pieces.append((last.start, last.end, (hit[2] if hit else doc.index.raw(last.guid)) + extra))
        if not pieces:
            continue
        data = doc.index.local[k].read_bytes()
        out, pos = [], 0
        for start, end, body in sorted(pieces):
            out += [data[pos:start], body]
            pos = end
        out.append(data[pos:])
        files[path] = b"".join(out)
    return files


def render_texts(doc: AssetDocument) -> dict[str, bytes]:
    """The text files the change set's texts change, whole, by the Texts app's writer (``textedit.textrender.whole_files``:
    an edited text replaced where its file has the id, a new one appended, a line break kept off the line games'
    lines)."""
    layout = LAYOUTS[doc.changes.game]
    files: dict[str, bytes] = {}
    for language, texts in doc.changes.texts.items():
        if not (texts and layout.texts and doc.files is not None):
            continue
        loaded = texttable.read_language(doc.files, layout, language)
        if not loaded.files and texttable.style_of(layout) == textfiles.XML:
            doc.files.read(layout.texts.format(language=layout.language_code(language)))  # says which file is missing
        files |= textrender.whole_files(loaded, layout, texts, doc.changes.mod_id)
    return files


def write_tree(files: dict[str, bytes], folder: Path) -> list[Path]:
    """The files under ``folder`` at their game paths."""
    written = []
    for path, data in files.items():
        dest = Path(folder) / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        written.append(dest)
    return written


def write_archive(files: dict[str, bytes], dest: Path) -> Path:
    """The files packed into one archive (the format 2070 and 1404 read, V2.0)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(write_v20(files))
    return dest
