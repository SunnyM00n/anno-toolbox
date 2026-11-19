"""A game's UI files: the generated Phoenix files of a game tree by kind and name, decoded on demand, the layout
versions new objects are written in, who names a file, and the game's texts by line id.

``UiFiles(tree, cache)`` is the entry over a ``sources.gametree.GameTree`` (117, 1800, or a folder of loose files);
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
class Entry:
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
    found = next((p for p in tree.paths if "/studio/generated/" in p), "")
    return found[: found.index("/studio/generated/") + len("/studio/generated/")] if found else phoenix.GENERATED


def is_guid(text: str) -> bool:
    try:
        uuid.UUID(text)
    except ValueError:
        return False
    return len(text) == 36


class UiFiles:
    """The UI files of one game tree; ``entries`` by key, ``load(key)`` the decoded file (``phoenixread.Decoder``)."""

    def __init__(self, tree, cache: Path | None = None, schema: dict | None = None) -> None:
        self.tree, self.cache = tree, Path(cache) if cache else None
        self.schema = schema or phoenix.load_schema()
        self.prefix = generated_prefix(tree)
        self.entries: dict[str, Entry] = {}
        self.versions: dict[str, str] = {}
        self.users: dict[str, list[tuple[str, str]]] = {}  # key -> (file key, instance name) naming it
        self.prototypes: dict[str, str] = {}  # an instance kind -> a file that has one to copy
        self.extra: dict[str, bytes] = {}  # files over the game's: a project's copies and new files, by key
        self.live: dict[str, dict] = {}  # the trees of files open for editing: what the preview places
        self._loaded: OrderedDict[str, phoenixread.Decoder] = OrderedDict()
        self._lock = threading.Lock()
        self._texts: dict[str, dict[int, str]] = {}
        self._documents: dict[str, str] | None = None  # file GUID -> its editor document (the History Editions)
        self._names: dict[str, dict[str, str]] = {}
        self._index()

    def _cache_file(self, name: str) -> Path | None:
        return self.cache / name if self.cache else None

    def _read_cache(self, name: str) -> dict | None:
        path = self._cache_file(name)
        if path is None:
            return None
        return buildcache.read_kept(path, {"format": CACHE_FORMAT, "stamp": self.tree.stamp})

    def _write_cache(self, name: str, data: dict) -> None:
        path = self._cache_file(name)
        if path is None or not self.tree.stamp:
            return
        buildcache.write_kept(path, {"format": CACHE_FORMAT, "stamp": self.tree.stamp, **data})

    def _index(self) -> None:
        cached = self._read_cache("ui-index.json")
        if cached is not None:
            self.entries = {e["key"]: Entry(**e) for e in cached["entries"]}
            self.versions = cached["versions"]
            return
        tables = []
        for path in self.tree.under(self.prefix):
            data = self.tree.read(path)
            if not data.startswith(b"PHX"):
                continue
            key = key_of(path)
            self.entries[key] = Entry(path, key, data[3:5].decode("latin1"), len(data))
            try:
                tables.append(phoenixread.type_table(data))
            except (ValueError, EOFError, IndexError):
                continue
        self.versions = phoenix.layout_versions(tables)
        self._write_cache("ui-index.json", {"entries": [asdict(e) for e in self.entries.values()],
                                            "versions": self.versions})  # fmt: skip

    def of_kind(self, *kinds: str) -> list[Entry]:
        return sorted((e for e in self.entries.values() if e.kind in kinds), key=lambda e: e.key)

    def read(self, key: str) -> bytes:
        if key in self.extra:
            return self.extra[key]
        return self.tree.read(self.entries[key].path)

    def lay_over(self, files: dict[str, bytes]) -> None:
        """A project's UI files (game path -> bytes) over the game's: they are read instead, new ones are listed."""
        for path, data in files.items():
            key = key_of(path)
            if not data.startswith(b"PHX"):
                continue
            self.extra[key] = data
            self.entries[key] = Entry(path, key, data[3:5].decode("latin1"), len(data))
            with self._lock:
                self._loaded.pop(key, None)

    def drop_copy(self, key: str) -> None:
        """Forget the project's copy of a file and its open tree: the game's own is read and decoded again."""
        self.extra.pop(key, None)
        self.live.pop(key, None)
        with self._lock:
            self._loaded.pop(key, None)

    def load(self, key: str) -> phoenixread.Decoder:
        """The decoded file ``key`` (kept: the next call returns the same tree, until it is dropped). The window and
        the preview's layout read files on two threads: one decoder per file for both."""
        with self._lock:
            hit = self._loaded.get(key)
            if hit is not None:
                self._loaded.move_to_end(key)
                return hit
        d = phoenixread.load(self.read(key), self.schema)  # decoded outside the lock: the other thread goes on
        d.log = []  # the field-by-field reading is for the decode tools; kept it would hold most of the memory
        with self._lock:
            d = self._loaded.setdefault(key, d)
            self._loaded.move_to_end(key)
            while len(self._loaded) > LOADED:
                self._loaded.popitem(last=False)
        return d

    def root(self, key: str | None) -> dict | None:
        """The decoded root of a file, or None when the game has no such file or it does not decode."""
        if key in self.live:
            return self.live[key]
        if not key or key not in self.entries:
            return None
        try:
            return self.load(key).root
        except (ValueError, EOFError, KeyError, IndexError):
            return None

    def names_of(self, root: dict) -> dict[str, str]:
        """The names the editor's JSON document of a file gives its objects (lower-case GUID -> name), for files that
        store none (the History Editions); empty where there is no document (docs/ui-editor.md "The History
        Editions")."""
        guid = str(uuid.UUID(bytes_le=root["guid"])) if root.get("guid") else ""
        if guid not in self._names:
            if self._documents is None:
                found = (p for p in self.tree.under(STUDIO) if p.endswith(".json") and not p.startswith(GAMEDATA))
                self._documents = {p[-41:-5].lower(): p for p in found if is_guid(p[-41:-5])}
            path = self._documents.get(guid)
            self._names[guid] = _document_names(json.loads(self.tree.read(path))) if path else {}
        return self._names[guid]

    def title(self, key: str) -> str:
        """What a list shows for a file: a scene's name, a widget file's names as its instances give them."""
        entry = self.entries.get(key)
        if entry is None:
            return key
        if not is_guid(key):
            return key
        names = sorted({n for _, n in self.users.get(key, []) if n})
        return f"{', '.join(names[:3])}{' ...' if len(names) > 3 else ''}" if names else key

    def scan_users(self) -> dict[str, list[tuple[str, str]]]:
        """Who names each file: (file, instance name) for every container that places it; read once, kept."""
        cached = self._read_cache("ui-users.json")
        if cached is not None:
            self.users = {k: [tuple(x) for x in v] for k, v in cached["users"].items()}
            return self.users
        users: dict[str, list[tuple[str, str]]] = {}
        for entry in self.of_kind(*EDITABLE):
            try:
                root = phoenixread.load(self.read(entry.key), self.schema).root
            except (ValueError, EOFError, KeyError, IndexError):
                continue
            for el in elements.tree(root).walk():
                target = el.template if el.kind == "container" else None
                if target:
                    users.setdefault(target, []).append((entry.key, el.name))
            wtype = fields.get(root, "WidgetType")
            if wtype:
                users.setdefault(wtype, []).append((entry.key, ""))
        self.users = users
        self._write_cache("ui-users.json", {"users": users})
        return users

    def scene_states(self, paths: Iterable[str]) -> list[str]:
        """The UI states whose scenes show the widget files at ``paths`` (their GUID names), through the files that
        place them (``scan_users`` first; before it nothing is known). The live link's scene reload names them."""
        todo = [p.rsplit("/", 1)[-1].lower() for p in paths]
        seen, states = set(todo), []
        while todo:
            for user, _name in self.users.get(todo.pop(), []):
                entry = self.entries.get(user)
                if entry is not None and entry.is_scene:
                    states.append(linkscenes.scene_state(entry.path))
                elif user not in seen:
                    seen.add(user)
                    todo.append(user)
        return list(dict.fromkeys(s for s in states if s))

    def texts(self, language: str = "english") -> dict[int, str]:
        """The game's texts by line id (a label's ``LocalizationId`` is the id's 16 hex digits; 1800's a decimal
        number)."""
        if language not in self._texts:
            out: dict[int, str] = {}
            for pattern in TEXTS:
                path = pattern.format(language=language)
                if self.tree.exists(path):
                    text = self.tree.read(path).decode("utf-8", "replace")
                    for m in re.finditer(
                        r"<(?:LineId|GUID)>(-?\d+)</(?:LineId|GUID)>\s*<Text>(.*?)</Text>", text, re.S
                    ):
                        out[int(m.group(1))] = _unescape(m.group(2))
                    break
            self._texts[language] = out
        return self._texts[language]

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
