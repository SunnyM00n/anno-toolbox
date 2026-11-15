"""The view models a UI binds to: the game's UI data classes from its Lua type stubs (``rdui`` game objects: values,
child objects, arrays), the readouts of a script list, the names a user gives the field ids the files use, and the
sample values the preview shows in place of the game's.

``ViewModels.read(tree)`` parses the stubs; ``class_for_scene`` guesses a scene's data class from its name;
``ScriptList.read(path)`` a script list (``script-api.json``); ``FieldNames`` and ``Samples`` are kept in the project.
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

STUBS = "data/script/types/generated/rdui/gameobjects/"
_CLASS = re.compile(r"^---@class (\S+)", re.M)
_FIELD = re.compile(r"^---@field (\w+) (.+)$", re.M)
_DOC = re.compile(r"^--- Gets the (child game object|array|value of) (\w+)", re.M)
_SETTER = re.compile(r"^--- Sets the value of (\w+)", re.M)
VALUE, CHILD, ARRAY = "value", "child", "array"


@dataclass(frozen=True)
class FieldDef:
    name: str
    type: str
    kind: str  # value, child, array
    settable: bool = False

    @property
    def child_class(self) -> str | None:
        """The class a child object or an array element is (the first one a union names)."""
        t = self.type.split("|")[0].strip().removesuffix("[]").removesuffix("[]")
        return t if "." in t and not t.startswith(("halo.", "datasets.")) else None


@dataclass
class ClassDef:
    name: str  # rdgs.CLoadingScreenSceneData
    fields: list[FieldDef] = field(default_factory=list)

    @property
    def short(self) -> str:
        """The name without its namespace and the C of a class."""
        tail = self.name.rsplit(".", 1)[-1]
        return tail[1:] if tail[:1] == "C" and tail[1:2].isupper() else tail


def parse_stub(text: str) -> ClassDef | None:
    """One stub file: its class and fields, each a value, a child object or an array, and whether it can be set."""
    m = _CLASS.search(text)
    if m is None:
        return None
    kinds = {name: {"child game object": CHILD, "array": ARRAY}.get(what, VALUE) for what, name in _DOC.findall(text)}
    settable = set(_SETTER.findall(text))
    out = ClassDef(m.group(1))
    for name, type_ in _FIELD.findall(text):
        kind = kinds.get(name, ARRAY if type_.strip().endswith("[]") else VALUE)
        out.fields.append(FieldDef(name, type_.strip(), kind, name in settable))
    return out


class ViewModels:
    """The UI data classes of a game by name (empty for a game that ships no stubs)."""

    def __init__(self, classes: list[ClassDef]) -> None:
        self.classes = {c.name: c for c in classes}

    @classmethod
    def read(cls, tree) -> ViewModels:
        out = []
        for path in tree.under(STUBS):
            if path.endswith(".lua"):
                found = parse_stub(tree.read(path).decode("utf-8", "replace"))
                if found is not None:
                    out.append(found)
        return cls(out)

    def search(self, text: str) -> list[ClassDef]:
        """Classes whose name or a field's name holds every word of ``text``."""
        words = text.lower().split()
        hits = []
        for c in self.classes.values():
            hay = (c.name + " " + " ".join(f.name for f in c.fields)).lower()
            if all(w in hay for w in words):
                hits.append(c)
        return sorted(hits, key=lambda c: c.name)

    def class_for_scene(self, scene: str) -> ClassDef | None:
        """The data class a scene's ``GameContext`` most likely is: ``maintitlescene`` -> ``CMainTitleSceneData``
        (the state's name, the program's naming; a guess, docs/ui/117/scenes.md)."""
        stem = scene.lower().removesuffix("scene")
        for suffix in ("scenedata", "sceneobject", "scenecontainer", "data", ""):
            for c in self.classes.values():
                if c.short.lower() == stem + suffix:
                    return c
        return None


@dataclass(frozen=True)
class Readout:
    table: str
    name: str
    kind: str
    args: tuple[str, ...]
    returns: tuple[str, ...]
    doc: str

    @property
    def call(self) -> str:
        return f"{self.table}.{self.name}({', '.join(self.args)})"


class ScriptList:
    """A script list's tables and functions (``script-api.json``: a script library's readouts and actions)."""

    def __init__(self, functions: list[Readout], about: str = "") -> None:
        self.functions, self.about = functions, about

    @classmethod
    def read(cls, path: Path) -> ScriptList:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        out = []
        for table in data.get("tables", []):
            for f in table.get("functions", []):
                out.append(Readout(table.get("table", ""), f.get("name", ""), f.get("kind", ""),
                                   tuple(f.get("args", [])), tuple(f.get("returns", [])), f.get("doc", "")))  # fmt: skip
        return cls(out, data.get("about", ""))

    def readouts(self) -> list[Readout]:
        return [f for f in self.functions if f.kind == "readout"]


class FieldNames:
    """The names a user gave field ids (``id -> "Class.Field"``): the ids are the program's, the names the stubs'."""

    def __init__(self, path: Path | None = None) -> None:
        self.path, self.names = path, {}
        if path is not None and Path(path).is_file():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.names = {int(k, 16): v for k, v in data.get("names", {}).items()}

    def set(self, uid: int, name: str) -> None:
        if name:
            self.names[uid] = name
        else:
            self.names.pop(uid, None)

    def short(self) -> dict[int, str]:
        """id -> the field's name alone (what a path shows)."""
        return {k: v.rsplit(".", 1)[-1] for k, v in self.names.items()}

    def save(self) -> None:
        if self.path is None:
            return
        data = {"format": 1, "names": {f"{k:08x}": v for k, v in sorted(self.names.items())}}
        write_atomic(Path(self.path), (json.dumps(data, indent=1) + "\n").encode("utf-8"))


class Samples:
    """Sample values by key (``<file>/<property id>/<field id>...`` in hex): what the preview shows where the game
    would give a value. ``seen`` collects the keys the last preview read; ``generated`` the keys whose value is a
    placeholder the preview made up (``placeholders``), ``seeded`` those a file's own preview data gave; a value set
    by hand replaces either. ``on_read`` and ``on_change`` tell the preview's kept results (``layout.Context``)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path, self.values, self.seen = path, {}, {}
        self.generated: set[str] = set()
        self.seeded: set[str] = set()
        self.fill = True  # make up a placeholder where a value is missing
        self.on_read: Callable[[str], None] | None = None  # a key (or a ``prefix/`` for ``keys_under``) was read
        self.on_change: Callable[[str, bool], None] | None = None  # a key's value changed (True: made up)
        self._given: tuple[int, list[str]] | None = None  # (how many are given, those keys sorted): ``blank``
        if path is not None and Path(path).is_file():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.values = data.get("values", {})
            self.generated = set(data.get("generated", [])) & set(self.values)
            self.seeded = set(data.get("seeded", [])) & set(self.values)

    @staticmethod
    def key(owner: str, ids) -> str:
        return "/".join([owner, *(f"{i:08x}" for i in ids)])

    def get(self, key: str, default=None):
        if self.on_read is not None:
            self.on_read(key)
        value = self.values.get(key, default)
        if key in self.generated and not self.fill:
            value = default
        self.seen[key] = value
        return value

    def given_under(self, prefix: str) -> list[str]:
        """The keys under ``prefix`` (it ends in ``/``) whose value is not made up: a file's or one set by hand."""
        if self.on_read is not None:
            self.on_read(prefix)
        return [k for k in self.values if k.startswith(prefix) and k not in self.generated]

    def blank(self, key: str) -> bool:
        """Whether nothing at ``key`` or under it is given (by hand or by a file): what is read there is made up,
        so it reads the same under any such key (the preview shares the work, ``layout.Placer.canon``)."""
        if self.on_read is not None:
            self.on_read(key + "/")
        sizes = len(self.values) - len(self.generated)  # a placeholder made adds to both: the given keys stay
        if self._given is None or self._given[0] != sizes:
            self._given = (sizes, sorted(k for k in self.values if k not in self.generated))
        given = self._given[1]
        if key in self.values and key not in self.generated:
            return False
        i = bisect.bisect_left(given, key + "/")
        return not (i < len(given) and given[i].startswith(key + "/"))

    def _changed(self, key: str, made: bool = False) -> None:
        if not made:
            self._given = None
        if self.on_change is not None:
            self.on_change(key, made)

    def set(self, key: str, value) -> None:
        self.generated.discard(key)
        self.seeded.discard(key)
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = value
        self._changed(key)

    def placeholder(self, key: str, value):
        """A made-up value for a key that has none (kept, marked as generated); the key's value when it has one."""
        if key in self.values:
            return self.get(key)
        if value is None or not self.fill:
            return None
        self.values[key] = value
        self.generated.add(key)
        self._changed(key, True)
        return self.get(key)

    def seed(self, key: str, value) -> None:
        """A value from a file's own preview data: it follows the file, but a value set by hand stays."""
        hand = key in self.values and key not in self.seeded and key not in self.generated
        if hand or (key in self.seeded and self.values[key] == value):
            return
        self.values[key] = value
        self.generated.discard(key)
        self.seeded.add(key)
        self._changed(key)

    def unseed(self, prefix: str, keep) -> None:
        """Drop the seeded values under ``prefix`` that are not in ``keep``: the file no longer carries them."""
        for key in [k for k in self.seeded if k.startswith(prefix) and k not in keep]:
            self.seeded.discard(key)
            self.values.pop(key, None)
            self._changed(key)

    def save(self) -> None:
        if self.path is None:
            return
        data = {"format": 1, "values": dict(sorted(self.values.items())), "generated": sorted(self.generated),
                "seeded": sorted(self.seeded)}  # fmt: skip
        write_atomic(Path(self.path), (json.dumps(data, indent=1) + "\n").encode("utf-8"))
