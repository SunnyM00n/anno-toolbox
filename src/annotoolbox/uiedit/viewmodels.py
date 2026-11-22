"""The view models a UI binds to: the game's UI data classes from its Lua type stubs (``rdui`` game objects: values,
child objects, arrays), the readouts of a script list, the names a user gives the field ids the files use, and the
sample values the preview shows in place of the game's.

``CViewModels.read(tree)`` parses the stubs; ``class_for_scene`` guesses a scene's data class from its name;
``CScriptList.read(path)`` a script list (``script-api.json``); ``CFieldNames`` and ``CSamples`` are kept in the project.
docs/tool-suite.md section 5, points 1 and 4; docs/ui/117/lua-interface.md.
"""

from __future__ import annotations

import bisect
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.buildcache import write_atomic
from ..infra.i18n import tr

STUBS = "data/script/types/generated/rdui/gameobjects/"
_CLASS = re.compile(r"^---@class (\S+)", re.M)
_FIELD = re.compile(r"^---@field (\w+) (.+)$", re.M)
_DOC = re.compile(r"^--- Gets the (child game object|array|value of) (\w+)", re.M)
_SETTER = re.compile(r"^--- Sets the value of (\w+)", re.M)
VALUE, CHILD, ARRAY = "value", "child", "array"


@dataclass(frozen=True)
class SFieldDef:
    name: str
    type: str
    kind: str  # value, child, array
    settable: bool = False

    @property
    def child_class(self) -> str | None:
        """The class a child object or an array element is (the first one a union names)."""
        class_name = self.type.split("|")[0].strip().removesuffix("[]").removesuffix("[]")
        return class_name if "." in class_name and not class_name.startswith(("halo.", "datasets.")) else None


@dataclass
class SClassDef:
    name: str  # rdgs.CLoadingScreenSceneData
    fields: list[SFieldDef] = field(default_factory=list)

    @property
    def short(self) -> str:
        """The name without its namespace and the C of a class."""
        tail = self.name.rsplit(".", 1)[-1]
        return tail[1:] if tail[:1] == "C" and tail[1:2].isupper() else tail


def parse_stub(text: str) -> SClassDef | None:
    """One stub file: its class and fields, each a value, a child object or an array, and whether it can be set."""
    match = _CLASS.search(text)
    if match is None:
        return None
    kinds = {name: {"child game object": CHILD, "array": ARRAY}.get(what, VALUE) for what, name in _DOC.findall(text)}
    settable = set(_SETTER.findall(text))
    out = SClassDef(match.group(1))
    for name, type_ in _FIELD.findall(text):
        kind = kinds.get(name, ARRAY if type_.strip().endswith("[]") else VALUE)
        out.fields.append(SFieldDef(name, type_.strip(), kind, name in settable))
    return out


class CViewModels:
    """The UI data classes of a game by name (empty for a game that ships no stubs)."""

    def __init__(self, classes: list[SClassDef]) -> None:
        self.m_classes = {class_def.name: class_def for class_def in classes}

    @classmethod
    def read(cls, tree) -> CViewModels:
        out = []
        for path in tree.under(STUBS):
            if path.endswith(".lua"):
                found = parse_stub(tree.read(path).decode("utf-8", "replace"))
                if found is not None:
                    out.append(found)
        return cls(out)

    def search(self, text: str) -> list[SClassDef]:
        """Classes whose name or a field's name holds every word of ``text``."""
        words = text.lower().split()
        hits = []
        for class_def in self.m_classes.values():
            hay = (class_def.name + " " + " ".join(field_def.name for field_def in class_def.fields)).lower()
            if all(word in hay for word in words):
                hits.append(class_def)
        return sorted(hits, key=lambda class_def: class_def.name)

    def class_for_scene(self, scene: str) -> SClassDef | None:
        """The data class a scene's ``GameContext`` most likely is: ``maintitlescene`` -> ``CMainTitleSceneData``
        (the state's name, the program's naming; a guess, docs/ui/117/scenes.md)."""
        stem = scene.lower().removesuffix("scene")
        for suffix in ("scenedata", "sceneobject", "scenecontainer", "data", ""):
            for class_def in self.m_classes.values():
                if class_def.short.lower() == stem + suffix:
                    return class_def
        return None


@dataclass(frozen=True)
class SReadout:
    table: str
    name: str
    kind: str
    args: tuple[str, ...]
    returns: tuple[str, ...]
    doc: str

    @property
    def call(self) -> str:
        return f"{self.table}.{self.name}({', '.join(self.args)})"


class CScriptList:
    """A script list's tables and functions (``script-api.json``: a script library's readouts and actions)."""

    def __init__(self, functions: list[SReadout], about: str = "") -> None:
        self.m_functions, self.m_about = functions, about

    @classmethod
    def read(cls, path: Path) -> CScriptList:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not all(isinstance(entry, dict) for entry in data.get("tables", [])):
            raise ValueError("not a script list")  # the window says it as it does for a file that is not JSON
        out = []
        for table in data.get("tables", []):
            for function in table.get("functions", []):
                out.append(SReadout(table.get("table", ""), function.get("name", ""), function.get("kind", ""),
                                   tuple(function.get("args", [])), tuple(function.get("returns", [])), function.get("doc", "")))  # fmt: skip
        return cls(out, data.get("about", ""))

    def readouts(self) -> list[SReadout]:
        return [readout for readout in self.m_functions if readout.kind == "readout"]


def read_kept(path: Path | None, part: str) -> dict:
    """A JSON file the editor keeps in the project, as its dictionary ({} without a file); ``ValueError`` (``OSError``)
    when it is not one whose ``part`` is a dictionary: a file changed by hand, or cut short."""
    if path is None or not Path(path).is_file():
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get(part, {}), dict):
        raise ValueError(f"no dictionary of {part}")
    return data


class CFieldNames:
    """The names a user gave field ids (``id -> "Class.Field"``): the ids are the program's, the names the stubs'.
    A file that does not read leaves ``error`` (the sentence for the person) and no ``path``: it is not written over."""

    def __init__(self, path: Path | None = None) -> None:
        self.m_path, self.m_names, self.m_error = path, {}, ""
        try:
            self.m_names = {int(k, 16): str(value) for k, value in read_kept(path, "names").get("names", {}).items()}
        except (OSError, ValueError) as exc:
            self.m_path, self.m_error = None, tr("common.path_not_read_error", path=path, error=exc)

    def set(self, uid: int, name: str) -> None:
        if name:
            self.m_names[uid] = name
        else:
            self.m_names.pop(uid, None)

    def short(self) -> dict[int, str]:
        """id -> the field's name alone (what a path shows)."""
        return {k: name.rsplit(".", 1)[-1] for k, name in self.m_names.items()}

    def save(self) -> None:
        if self.m_path is None:
            return
        data = {"format": 1, "names": {f"{k:08x}": name for k, name in sorted(self.m_names.items())}}
        write_atomic(Path(self.m_path), (json.dumps(data, indent=1) + "\n").encode("utf-8"))


class CSamples:
    """Sample values by key (``<file>/<property id>/<field id>...`` in hex): what the preview shows where the game
    would give a value. ``seen`` collects the keys the last preview read; ``generated`` the keys whose value is a
    placeholder the preview made up (``placeholders``), ``seeded`` those a file's own preview data gave; a value set
    by hand replaces either. ``on_read`` and ``on_change`` tell the preview's kept results (``layout.Context``). A
    file that does not read leaves ``error`` and no ``path``, as ``CFieldNames``."""

    def __init__(self, path: Path | None = None) -> None:
        self.m_path, self.m_values, self.m_seen, self.m_error = path, {}, {}, ""
        self.m_generated: set[str] = set()
        self.m_seeded: set[str] = set()
        self.m_fill = True  # make up a placeholder where a value is missing
        self.m_on_read: Callable[[str], None] | None = None  # a key (or a ``prefix/`` for ``keys_under``) was read
        self.m_on_change: Callable[[str, bool], None] | None = None  # a key's value changed (True: made up)
        self._m_given: tuple[int, list[str]] | None = None  # (how many are given, those keys sorted): ``blank``
        self.m_from_game: dict[str, str] = {}  # keys the game's data filled (not kept): where from (``gamedata``)
        self.m_distinct: Callable[[str], bool] | None = None  # a key the game's data fills apart from any other
        try:
            data = read_kept(path, "values")
            generated, seeded = (set(map(str, data.get(k) or [])) for k in ("generated", "seeded"))
        except (OSError, ValueError, TypeError) as exc:
            self.m_path, self.m_error = None, tr("common.path_not_read_error", path=path, error=exc)
            return
        self.m_values = data.get("values", {})
        self.m_generated, self.m_seeded = generated & set(self.m_values), seeded & set(self.m_values)

    @staticmethod
    def key(owner: str, ids) -> str:
        return "/".join([owner, *(f"{i:08x}" for i in ids)])

    def get(self, key: str, default=None):
        if self.m_on_read is not None:
            self.m_on_read(key)
        value = self.m_values.get(key, default)
        if key in self.m_generated and not self.m_fill:
            value = default
        self.m_seen[key] = value
        return value

    def given_under(self, prefix: str) -> list[str]:
        """The keys under ``prefix`` (it ends in ``/``) whose value is not made up: a file's or one set by hand."""
        if self.m_on_read is not None:
            self.m_on_read(prefix)
        return [k for k in self.m_values if k.startswith(prefix) and k not in self.m_generated]

    def blank(self, key: str) -> bool:
        """Whether nothing at ``key`` or under it is given (by hand or by a file): what is read there is made up,
        so it reads the same under any such key (the preview shares the work, ``layout.CPlacer.canon``)."""
        if self.m_on_read is not None:
            self.m_on_read(key + "/")
        if self.m_distinct is not None and self.m_distinct(key):
            return False
        sizes = len(self.m_values) - len(self.m_generated)  # a placeholder made adds to both: the given keys stay
        if self._m_given is None or self._m_given[0] != sizes:
            self._m_given = (sizes, sorted(k for k in self.m_values if k not in self.m_generated))
        given = self._m_given[1]
        if key in self.m_values and key not in self.m_generated:
            return False
        i = bisect.bisect_left(given, key + "/")
        return not (i < len(given) and given[i].startswith(key + "/"))

    def _changed(self, key: str, made: bool = False) -> None:
        if not made:
            self._m_given = None
        if self.m_on_change is not None:
            self.m_on_change(key, made)

    def set(self, key: str, value) -> None:
        self.m_generated.discard(key)
        self.m_seeded.discard(key)
        if value is None:
            self.m_values.pop(key, None)
        else:
            self.m_values[key] = value
        self._changed(key)

    def placeholder(self, key: str, value):
        """A made-up value for a key that has none (kept, marked as generated); the key's value when it has one."""
        if key in self.m_values:
            return self.get(key)
        if value is None or not self.m_fill:
            return None
        self.m_values[key] = value
        self.m_generated.add(key)
        self._changed(key, True)
        return self.get(key)

    def seed(self, key: str, value) -> None:
        """A value from a file's own preview data: it follows the file, but a value set by hand stays."""
        hand = key in self.m_values and key not in self.m_seeded and key not in self.m_generated
        if hand or (key in self.m_seeded and self.m_values[key] == value):
            return
        self.m_values[key] = value
        self.m_generated.discard(key)
        self.m_seeded.add(key)
        self._changed(key)

    def unseed(self, prefix: str, keep) -> None:
        """Drop the seeded values under ``prefix`` that are not in ``keep``: the file no longer carries them."""
        for key in [k for k in self.m_seeded if k.startswith(prefix) and k not in keep]:
            self.m_seeded.discard(key)
            self.m_values.pop(key, None)
            self._changed(key)

    def save(self) -> None:
        if self.m_path is None:
            return
        data = {"format": 1, "values": dict(sorted(self.m_values.items())), "generated": sorted(self.m_generated),
                "seeded": sorted(self.m_seeded)}  # fmt: skip
        write_atomic(Path(self.m_path), (json.dumps(data, indent=1) + "\n").encode("utf-8"))
