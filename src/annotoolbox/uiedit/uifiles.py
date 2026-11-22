"""A game's UI files: the generated Phoenix files of a game tree by kind and name, decoded on demand, the layout
versions new objects are written in, who names a file, and the game's texts by line id.

``CUiFiles(tree, cache)`` is the entry over a ``sources.gametree.CGameTree`` (117, 1800, or a folder of loose files);
``scan_users`` reads every widget file and scene once (in the background) and is kept beside the game's index;
``names_of`` gives the History Editions' instance names from the editor's documents. docs/formats/phoenix-scene-format.md
is the format; docs/ui/117/scenes.md the scenes.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from ..formats import phoenix, phoenixread
from ..infra import buildcache
from ..infra.i18n import N_
from ..suite import linkscenes
from . import elements, fields

KIND_NAMES = {"WI": N_("ui.widget_file"), "SC": N_("ui.scene"), "IM": N_("ui.image"), "EN": N_("ui.enum_lower"),
              "CP": N_("ui.component"), "BE": N_("ui.widget_type"), "EF": N_("ui.effect"), "TA": N_("ui.image_atlas"),
              "VI": N_("ui.video"), "PA": N_("ui.palette_lower"), "FT": N_("common.font"), "FD": N_("ui.font_data"),
              "TS": N_("ui.text_style"), "LF": N_("ui.localized_font"), "EC": N_("ui.font_fallback"),
              "FF": N_("ui.font_family"), "LG": N_("ui.language_group")}  # fmt: skip  # shown through tr(name)
EDITABLE = {"WI", "SC"}  # the kinds the element tree and the preview work on; the others open in the properties
GAMEDATA = "data/ui/studio/gamedata/"  # the History Editions keep their UI files here, scenes with a ``.scene`` suffix
STUDIO = "data/ui/studio/"  # ... and the editor's JSON documents of them, ``<folder>/<name>_<file guid>.json``
TEXTS = ("data/base/config/gui/texts_{language}.xml", "data/config/gui/texts_{language}.xml")
CACHE_FORMAT = 2
LINE_ID_DIGITS = 16  # 117's line ids are 16 hex digits; 1800's are decimal numbers (its texts are ``<GUID>``)
LOADED = 600  # decoded files kept: one scene places a few hundred, and a smaller set re-decodes them all per frame


@dataclass
class SEntry:
    path: str
    key: str  # the GUID, or a scene's state name
    kind: str  # two letters: WI, SC, IM ...
    size: int

    @property
    def is_scene(self) -> bool:
        return self.kind == "SC"


def key_of(path: str) -> str:
    """A file's key: its name, a scene without its ``.scene`` suffix."""
    return path.rsplit("/", 1)[-1].lower().removesuffix(".scene")


def generated_prefix(tree) -> str:
    """Where the game keeps its generated UI files (117 and 1800: ``data/ui/studio/generated/``, the History
    Editions: ``data/ui/studio/gamedata/``)."""
    if tree.under(phoenix.GENERATED):
        return phoenix.GENERATED
    if tree.under(GAMEDATA):
        return GAMEDATA
    found = next((path for path in tree.m_paths if "/studio/generated/" in path), "")
    return found[: found.index("/studio/generated/") + len("/studio/generated/")] if found else phoenix.GENERATED


def is_guid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return len(text) == 36


class CUiFiles:
    """The UI files of one game tree; ``entries`` by key, ``load(key)`` the decoded file (``phoenixread.CDecoder``)."""

    def __init__(self, tree, cache: Path | None = None, schema: dict | None = None) -> None:
        self.m_tree, self.m_cache = tree, Path(cache) if cache else None
        self.m_schema = schema or phoenix.load_schema()
        self.m_prefix = generated_prefix(tree)
        self.m_entries: dict[str, SEntry] = {}
        self.m_versions: dict[str, str] = {}
        self.m_users: dict[str, list[tuple[str, str]]] = {}  # key -> (file key, instance name) naming it
        self.m_prototypes: dict[str, str] = {}  # an instance kind -> a file that has one to copy
        self.m_extra: dict[str, bytes] = {}  # files over the game's: a project's copies and new files, by key
        self.m_live: dict[str, dict] = {}  # the trees of files open for editing: what the preview places
        self._m_loaded: OrderedDict[str, phoenixread.CDecoder] = OrderedDict()
        self._m_lock = threading.Lock()
        self._m_texts: dict[str, dict[int, str]] = {}
        self._m_documents: dict[str, str] | None = None  # file GUID -> its editor document (the History Editions)
        self._m_names: dict[str, dict[str, str]] = {}
        self._index()

    def _cache_file(self, name: str) -> Path | None:
        return self.m_cache / name if self.m_cache else None

    def _read_cache(self, name: str) -> dict | None:
        path = self._cache_file(name)
        if path is None:
            return None
        return buildcache.read_kept(path, {"format": CACHE_FORMAT, "stamp": self.m_tree.m_stamp})

    def _write_cache(self, name: str, data: dict) -> None:
        path = self._cache_file(name)
        if path is None or not self.m_tree.m_stamp:
            return
        buildcache.write_kept(path, {"format": CACHE_FORMAT, "stamp": self.m_tree.m_stamp, **data})

    def _index(self) -> None:
        cached = self._read_cache("ui-index.json")
        if cached is not None:
            self.m_entries = {entry["key"]: SEntry(**entry) for entry in cached["entries"]}
            self.m_versions = cached["versions"]
            return
        tables = []
        for path in self.m_tree.under(self.m_prefix):
            data = self.m_tree.read(path)
            if not data.startswith(b"PHX"):
                continue
            key = key_of(path)
            self.m_entries[key] = SEntry(path, key, data[3:5].decode("latin1"), len(data))
            try:
                tables.append(phoenixread.type_table(data))
            except (ValueError, EOFError, IndexError):
                continue
        self.m_versions = phoenix.layout_versions(tables)
        self._write_cache("ui-index.json", {"entries": [asdict(entry) for entry in self.m_entries.values()],
                                            "versions": self.m_versions})  # fmt: skip

    def of_kind(self, *kinds: str) -> list[SEntry]:
        return sorted((entry for entry in self.m_entries.values() if entry.kind in kinds), key=lambda entry: entry.key)

    def read(self, key: str) -> bytes:
        if key in self.m_extra:
            return self.m_extra[key]
        return self.m_tree.read(self.m_entries[key].path)

    def lay_over(self, files: dict[str, bytes]) -> None:
        """A project's UI files (game path -> bytes) over the game's: they are read instead, new ones are listed."""
        for path, data in files.items():
            key = key_of(path)
            if not data.startswith(b"PHX"):
                continue
            self.m_extra[key] = data
            self.m_entries[key] = SEntry(path, key, data[3:5].decode("latin1"), len(data))
            with self._m_lock:
                self._m_loaded.pop(key, None)

    def drop_copy(self, key: str) -> None:
        """Forget the project's copy of a file and its open tree: the game's own is read and decoded again."""
        self.m_extra.pop(key, None)
        self.m_live.pop(key, None)
        with self._m_lock:
            self._m_loaded.pop(key, None)

    def load(self, key: str) -> phoenixread.CDecoder:
        """The decoded file ``key`` (kept: the next call returns the same tree, until it is dropped). The window and
        the preview's layout read files on two threads: one decoder per file for both."""
        with self._m_lock:
            hit = self._m_loaded.get(key)
            if hit is not None:
                self._m_loaded.move_to_end(key)
                return hit
        decoder = phoenixread.load(self.read(key), self.m_schema)  # decoded outside the lock: the other thread goes on
        decoder.m_log = []  # the field-by-field reading is for the decode tools; kept it would hold most of the memory
        with self._m_lock:
            decoder = self._m_loaded.setdefault(key, decoder)
            self._m_loaded.move_to_end(key)
            while len(self._m_loaded) > LOADED:
                self._m_loaded.popitem(last=False)
        return decoder

    def root(self, key: str | None) -> dict | None:
        """The decoded root of a file, or None when the game has no such file or it does not decode."""
        if key in self.m_live:
            return self.m_live[key]
        if not key or key not in self.m_entries:
            return None
        try:
            return self.load(key).m_root
        except (ValueError, EOFError, KeyError, IndexError):
            return None

    def names_of(self, root: dict) -> dict[str, str]:
        """The names the editor's JSON document of a file gives its objects (lower-case GUID -> name), for files that
        store none (the History Editions); empty where there is no document (docs/ui-editor.md "The History
        Editions")."""
        guid = str(uuid.UUID(bytes_le=root["guid"])) if root.get("guid") else ""
        if guid not in self._m_names:
            if self._m_documents is None:
                found = (
                    document_path
                    for document_path in self.m_tree.under(STUDIO)
                    if document_path.endswith(".json") and not document_path.startswith(GAMEDATA)
                )
                self._m_documents = {
                    document_path[-41:-5].lower(): document_path
                    for document_path in found
                    if is_guid(document_path[-41:-5])
                }
            path = self._m_documents.get(guid)
            try:
                self._m_names[guid] = _document_names(json.loads(self.m_tree.read(path))) if path else {}
            except ValueError:  # a document that does not read only costs the names: the file opens without them
                self._m_names[guid] = {}
        return self._m_names[guid]

    def title(self, key: str) -> str:
        """What a list shows for a file: a scene's name, a widget file's names as its instances give them."""
        entry = self.m_entries.get(key)
        if entry is None:
            return key
        if not is_guid(key):
            return key
        names = sorted({name for _, name in self.m_users.get(key, []) if name})
        return f"{', '.join(names[:3])}{' ...' if len(names) > 3 else ''}" if names else key

    def scan_users(self) -> dict[str, list[tuple[str, str]]]:
        """Who names each file: (file, instance name) for every container that places it; read once, kept."""
        cached = self._read_cache("ui-users.json")
        if cached is not None:
            self.m_users = {k: [tuple(pair) for pair in pairs] for k, pairs in cached["users"].items()}
            return self.m_users
        users: dict[str, list[tuple[str, str]]] = {}
        for entry in self.of_kind(*EDITABLE):
            try:
                root = phoenixread.load(self.read(entry.key), self.m_schema).m_root
            except (ValueError, EOFError, KeyError, IndexError):
                continue
            for element in elements.tree(root).walk():
                target = element.template if element.kind == "container" else None
                if target:
                    users.setdefault(target, []).append((entry.key, element.name))
            widget_type = fields.get(root, "WidgetType")
            if widget_type:
                users.setdefault(widget_type, []).append((entry.key, ""))
        self.m_users = users
        self._write_cache("ui-users.json", {"users": users})
        return users

    def scene_states(self, paths: Iterable[str]) -> list[str]:
        """The UI states whose scenes show the widget files at ``paths`` (their GUID names), through the files that
        place them (``scan_users`` first; before it nothing is known). The live link's scene reload names them."""
        todo = [path.rsplit("/", 1)[-1].lower() for path in paths]
        seen, states = set(todo), []
        while todo:
            for user, _name in self.m_users.get(todo.pop(), []):
                entry = self.m_entries.get(user)
                if entry is not None and entry.is_scene:
                    states.append(linkscenes.scene_state(entry.path))
                elif user not in seen:
                    seen.add(user)
                    todo.append(user)
        return list(dict.fromkeys(state for state in states if state))

    def texts(self, language: str = "english") -> dict[int, str]:
        """The game's texts by line id (a label's ``LocalizationId`` is the id's 16 hex digits; 1800's a decimal
        number)."""
        if language not in self._m_texts:
            out: dict[int, str] = {}
            for pattern in TEXTS:
                path = pattern.format(language=language)
                if self.m_tree.exists(path):
                    text = self.m_tree.read(path).decode("utf-8", "replace")
                    for match in re.finditer(
                        r"<(?:LineId|GUID)>(-?\d+)</(?:LineId|GUID)>\s*<Text>(.*?)</Text>", text, re.S
                    ):
                        out[int(match.group(1))] = _unescape(match.group(2))
                    break
            self._m_texts[language] = out
        return self._m_texts[language]

    def text_of(self, loca_id: str, language: str = "english") -> str | None:
        """The text of a ``LocalizationId`` ("A010 D068 1398 4305"), None when the game has none."""
        if not isinstance(loca_id, str):
            return None
        digits = loca_id.replace(" ", "")
        try:
            value = int(digits) if digits.isdigit() and len(digits) < LINE_ID_DIGITS else int(digits, 16)
        except ValueError:
            return None
        if value >= 1 << 63:
            value -= 1 << 64
        return self.texts(language).get(value)


def _document_names(data) -> dict[str, str]:
    """Every object of an editor document that has an ``ObjectID`` and a ``Name``, by its GUID in lower case."""
    out, todo = {}, [data]
    while todo:
        item = todo.pop()
        if isinstance(item, dict):
            ident, name = item.get("ObjectID"), item.get("Name")
            if isinstance(ident, str) and isinstance(name, str) and name:
                out.setdefault(ident.lower(), name)
            todo.extend(item.values())
        elif isinstance(item, list):
            todo.extend(item)
    return out


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )
