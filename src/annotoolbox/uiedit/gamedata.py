"""Game data in the preview: where a binding reads a value the game fills at run time (a field of a scene's data
object, an element of a list), the installed game's own assets give it - a building's name and icon, the patrons of
the religion scene, the quick tools' pinned buttons - in the language picked for the preview.

``GameData.resolve(key, want)`` answers one sample key (``Samples.key``: an owner and a path of field ids) with a
``Found`` value and where it came from, or None (a placeholder is made then); ``Assets`` reads the install's assets
and texts; ``FieldTable`` names the field ids. The rules per scene are ``gameplans``. docs/ui-editor.md "Game data
in the preview"; docs/ui/117/game-data.md.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from . import gameplans, previewdefaults

FIELD_TABLE = "field-names.json"  # the game's field names, in the game's index folder (``FieldTable.load``)
SHIPPED_DIR = "data"  # the package's copy per game, ``field-names-<117|1800>.json`` (docs/ui/117/game-data.md)
ELEMENT = 0x90000000  # element i of an array is the id ELEMENT + i in a path (listlayout.ELEMENT)
DATA_SOURCE = 0x30000001
CURRENT_OBJECT = 0x30000003  # an object loader's game object (``CurrentGameObject``)
OBJECT_TYPES = {12: "list", 16: "object", 19: "object"}  # a custom property's type that holds an array or an object
TEXT_LEAVES = ("text", "name", "title", "headline", "header", "caption", "label", "displayname")
LONG_LEAVES = ("description", "desc", "tooltip", "hint", "info", "body", "flavour", "flavor")
PICTURE_LEAVES = ("icon", "image", "picture", "portrait", "artwork", "background", "emblem")
GUID_LEAVES = ("guid", "assetguid", "asset", "refguid", "id")
_LOCA = re.compile(r"^-?\d{12,20}$")  # a text id of 117's tables (negative 64-bit)


@dataclass(frozen=True)
class Found:
    """A value the install gives, and where from (shown in Properties and the sample panel)."""

    value: object
    origin: str  # "Patron 1500200: name"


class FieldTable:
    """Field id -> field name of the game's data objects, read from the game: the workspace's table where the index
    folder has one (a newer build), else the one shipped with the Toolbox for the installed build, else the shipped
    one of another build (``stale``), with the names a user gave in View models over it (``viewmodels.FieldNames``).
    ``source`` says which: "workspace", "shipped", "stale" or "none"."""

    def __init__(self, names: dict[int, str] | None = None, source: str = "none") -> None:
        self.names = dict(names or {})
        self.source = source

    @property
    def stale(self) -> bool:
        """Whether the names come from another game build than the installed one (some may be wrong or missing)."""
        return self.source == "stale"

    @classmethod
    def load(cls, folder: Path | None, user: dict[int, str] | None = None, game: str | None = None,
             build: str | None = None, shipped: Traversable | None = None) -> FieldTable:  # fmt: skip
        """The workspace table of ``folder`` (``FIELD_TABLE``), else the shipped table of ``game`` (``SHIPPED_DIR``,
        or ``shipped``) whose build is ``build`` ("stale" when it is another), else empty; the user's names on top."""
        names = _read_names(Path(folder) / FIELD_TABLE if folder else None)
        source = "workspace" if names else "none"
        if not names and game:
            root = shipped if shipped is not None else resources.files(__package__).joinpath(SHIPPED_DIR)
            data = _read_table(root.joinpath(f"field-names-{game.removeprefix('anno')}.json"))
            names = _names_of(data)
            if names:
                source = "shipped" if build and str(data.get("build")) == str(build) else "stale"
        names.update(user or {})
        return cls(names, source)

    def name(self, uid: int) -> str:
        return self.names.get(uid, "")


def _read_table(path: Traversable | None) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.is_file() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _names_of(data: dict) -> dict[int, str]:
    try:
        return {int(k, 16): str(v) for k, v in (data.get("names") or {}).items()}
    except (ValueError, AttributeError):
        return {}


def _read_names(path: Path | None) -> dict[int, str]:
    return _names_of(_read_table(path))


def installed_build(folder: Path | None, exe: str) -> str | None:
    """The installed game's build as the shipped tables name it: the size of its program file (``exe``, relative to
    the install folder), None where it is not there."""
    try:
        return str((Path(folder) / exe).stat().st_size) if folder else None
    except OSError:
        return None


def stub_kinds(models) -> dict[str, str]:
    """Field name -> "child", "array" or "value" from the game's UI data classes (``viewmodels.ViewModels``); a
    name the classes use as several kinds takes the most common."""
    counts: dict[str, dict[str, int]] = {}
    for c in getattr(models, "classes", {}).values():
        for f in c.fields:
            kind = f.kind if f.kind != "value" or not f.type.endswith("[]") else "array"
            counts.setdefault(f.name, {}).setdefault(kind, 0)
            counts[f.name][kind] += 1
    return {n: max(k, key=k.get) for n, k in counts.items()}


def stub_groups(models) -> dict[str, bool]:
    """Field name -> whether it is the first of the child objects of one class that a data class holds side by side
    (a menu's four access buttons: ``BuildingsButton`` first), in the classes' own order: which of them the game
    selects first (``placeholders.selection_for``). A name in no such group is absent."""
    out: dict[str, bool] = {}
    for c in getattr(models, "classes", {}).values():
        by_type: dict[str, list[str]] = {}
        for f in c.fields:
            if f.kind == "child":
                by_type.setdefault(f.type, []).append(f.name)
        for names in by_type.values():
            for i, name in enumerate(names if len(names) > 1 else ()):
                out.setdefault(name, i == 0)
    return out


def class_fields(models) -> dict[str, frozenset[str]]:
    """Data class name -> the names of its fields (``viewmodels.ViewModels``): what tells an object loader's
    children apart (``object_child``)."""
    return {n: frozenset(f.name for f in c.fields) for n, c in getattr(models, "classes", {}).items()}


class Assets:
    """The install's assets as the preview reads them: ``element(guid)`` an ``<Asset>`` element (parsed once),
    ``by_template`` the GUIDs of a template in file order, ``text(id, language)`` a line of the text tables."""

    def __init__(self, element: Callable[[int], ET.Element | None], templates: dict[str, list[int]],
                 texts: Callable[[str], dict[int, str]]) -> None:  # fmt: skip
        self._element, self.templates, self._texts = element, templates, texts
        self._parsed: dict[int, ET.Element | None] = {}

    @classmethod
    def from_index(cls, index, texts: Callable[[str], dict[int, str]]) -> Assets:
        """Over the asset editor's index (``assetedit.assetindex.AssetIndex``)."""
        templates: dict[str, list[int]] = {}
        for e in index.entries:
            templates.setdefault(e.template, []).append(e.guid)

        def element(guid: int):
            return index.element(guid) if guid in index.by_guid else None

        return cls(element, templates, texts)

    @classmethod
    def from_xml(cls, assets: dict[int, str], texts: Callable[[str], dict[int, str]] | None = None) -> Assets:
        """A stand-in over ``<Asset>`` texts by GUID (tests); ``texts`` as ``UiFiles.texts``."""
        parsed = {g: ET.fromstring(x) for g, x in assets.items()}
        templates: dict[str, list[int]] = {}
        for g, el in parsed.items():
            templates.setdefault(el.findtext("Template") or "", []).append(g)
        return cls(parsed.get, templates, texts or (lambda _lang: {}))

    def element(self, guid: int | None) -> ET.Element | None:
        if not isinstance(guid, int) or guid <= 0:
            return None
        if guid not in self._parsed:
            try:
                self._parsed[guid] = self._element(guid)
            except (KeyError, ET.ParseError, OSError):
                self._parsed[guid] = None
        return self._parsed[guid]

    def by_template(self, template: str) -> list[int]:
        return list(self.templates.get(template, ()))

    def template_named(self, name: str) -> str | None:
        """The template whose name is ``name`` ignoring case and underscores (a scene's own config asset)."""
        flat = self.__dict__.get("_flats")
        if flat is None or flat[0] != len(self.templates):  # every layout asks: the names are flattened once
            flat = self._flats = (len(self.templates), {})
            for t in reversed(list(self.templates)):  # the first of a name wins, as the search in order did
                flat[1][_flat(t)] = t
        return flat[1].get(_flat(name))

    def text(self, value, language: str) -> str | None:
        """The line a text id names (117's ids are 64-bit numbers), None for anything else."""
        if isinstance(value, str) and _LOCA.match(value.strip()):
            return self._texts(language).get(int(value.strip()))
        return None

    def name(self, guid: int, language: str) -> str | None:
        """An asset's display name: its text line (``Text/OasisId``, ``Standard/Text``), else its ``Standard/Name``."""
        el = self.element(guid)
        if el is None:
            return None
        own = f"Values/*/{el.findtext('Template')}Name"  # a patron's PatronName
        for path in (".//Text/OasisId", ".//Standard/Text", own):
            line = self.text(el.findtext(path), language)
            if line:
                return line
        return el.findtext(".//Standard/Name")

    def description(self, guid: int, language: str) -> str | None:
        el = self.element(guid)
        if el is None:
            return None
        own = f"Values/*/{el.findtext('Template')}Description"
        return self.text(el.findtext(".//Standard/InfoDescription") or el.findtext(own), language)

    def icon(self, guid: int) -> str | None:
        el = self.element(guid)
        found = el.findtext(".//Standard/IconFilename") if el is not None else None
        return found or None

    def values(self, node: tuple[int, str], tag: str, own: bool = False) -> list[str]:
        """The texts of the fields at ``tag`` (a name, or a path such as ``LocalEffects/Item[1]/Title``) of a node:
        an asset's property blocks (``Values/*``; ``own``: only the block named after its template, a scene's
        config), or the part of an asset a path names, in file order."""
        guid, base = node
        el = self.element(guid)
        if el is None:
            return []
        at = f"{base}/{tag}" if base else f"Values/{el.findtext('Template') if own else '*'}/{tag}"
        try:
            return [x.text.strip() for x in el.findall(at) if x.text and x.text.strip()]
        except SyntaxError:  # a path ElementTree does not take
            return []


def _flat(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class Context:
    """What a key resolves against: the scene's plan, its config asset, and the node (an asset, and a path inside
    it) the key's path stands on now."""

    plan: gameplans.Plan
    config: int | None
    node: tuple[int, str] | None = None
    trail: list[str] = field(default_factory=list)  # the field names walked ("[i]" for an element)

    @property
    def names(self) -> list[str]:
        return [t for t in self.trail if not t.startswith("[")]


class GameData:
    """The resolver of one open game. ``scene`` is the open file (set before each layout); ``stamp`` what its
    answers depend on (``layout.Context.conditions``)."""

    def __init__(self, assets: Assets, table: FieldTable, kinds: dict[str, str] | None = None,
                 language: str = "english", defaults=None) -> None:  # fmt: skip
        self.assets, self.table, self.kinds = assets, table, kinds or {}
        self.language, self.defaults = language, defaults
        self.scene = ""
        self.own_names: dict[int, str] = {}  # the widget files' own property names (data side), as scopes see them
        self.lists: dict[str, list | None] = {}  # an array's key -> its elements (cleared with ``attach``)
        self.groups: dict[str, bool] = {}  # ``stub_groups``: which side-by-side child object comes first
        self.classes: dict[str, frozenset[str]] = {}  # ``class_fields``: each data class's field names
        self.play = None  # Play mode's kept choices (``playstate.Choices``), set by ``lookup``
        self.note = None  # the preview's ``Memo.note``, set by ``lookup``: what a plan's read of a choice depends on

    @property
    def stamp(self) -> tuple:
        region = getattr(self.defaults, "region", None)
        return ("gamedata", self.scene, self.language, region, len(self.table.names))

    def field_name(self, uid: int) -> str:
        """A field's name; an array's descriptor carries ``Base`` after the name the data classes give it."""
        name = self.table.name(uid) or self.own_names.get(uid, "")
        return name[:-4] if name.endswith("ArrayBase") or (name.endswith("Base") and name[:-4] in self.kinds) else name

    def names_of(self, key: str) -> tuple[str, list[int]]:
        """The owner of a key and its path of ids."""
        owner, _, rest = key.partition("/")
        ids = [int(p, 16) for p in rest.split("/") if len(p) == 8] if rest else []
        return owner, ids

    # what a path stands for

    def walk(self, key: str) -> Context:
        """The context a key's path reaches: a child object the plan names, or an array's element, moves it to the
        node that object or element stands for."""
        plan = gameplans.plan_for(self.scene)
        owner, ids = self.names_of(key)
        config = self._config(plan) if owner.split(":")[0] == self.scene else None  # a path within the scene
        ctx = Context(plan, config, (config, "") if config else None)
        if plan.main is not None and owner.split(":")[0] == self.scene:  # the object the whole scene shows
            main = plan.main(self, ctx)
            ctx.node = main[0] if main else ctx.node
        prefix = owner
        for uid in ids:
            if uid >= ELEMENT and ctx.trail:
                items = self.elements(prefix)
                i = uid - ELEMENT
                ctx.node = items[i] if items is not None and i < len(items) else None
                ctx.trail.append(f"[{i}]")
            else:
                ctx.trail.append(self.field_name(uid) or ("DataSource" if uid == DATA_SOURCE else f"{uid:08x}"))
                query = plan.objects.get(ctx.trail[-1])
                if query is not None:
                    found = query(self, ctx)
                    ctx.node = found[0] if found else ctx.node
            prefix = f"{prefix}/{uid:08x}"
        return ctx

    def _config(self, plan: gameplans.Plan) -> int | None:
        template = plan.config or self.assets.template_named(gameplans.config_template(self.scene))
        found = self.assets.by_template(template) if template else []
        return found[0] if found else None

    def elements(self, key: str) -> list | None:
        """The nodes of the array at ``key``, None where no plan names it (kept until ``attach``)."""
        if key not in self.lists:
            ctx = self.walk(key)
            query = gameplans.list_query(ctx.plan, ctx.names)
            self.lists[key] = query(self, ctx) if query is not None else None
        return self.lists[key]

    def count(self, key: str) -> int | None:
        items = self.elements(key)
        return len(items) if items else None

    def is_object(self, key: str) -> bool:
        """Whether the last field of ``key`` holds a game object or an array (the data classes say so)."""
        _, ids = self.names_of(key)
        name = self.field_name(ids[-1]) if ids else ""
        return self.kinds.get(name) in ("child", "array")

    def class_of(self, key: str) -> str | None:
        """The data class of the game object at ``key`` where a plan says it (a field typed with several classes:
        the options page's ``OptionsData``), else None."""
        ctx = self.walk(key)
        query = ctx.plan.classes.get(ctx.trail[-1]) if ctx.trail else None
        return query(self, ctx) if query is not None else None

    def distinct(self, key: str) -> bool:
        """Whether what is read under ``key`` is the game's own and differs from any other key's: a path from the
        open scene's data object, or an element of a list the plans fill and any object under it (a tab's ``Data``:
        each tab has its own selection). Such keys are not shared (``scope.canon``)."""
        owner, ids = self.names_of(key)
        if owner == self.scene and ids:
            return True
        parts = key.split("/")
        return any(len(p) == 8 and int(p, 16) >= ELEMENT and self.count("/".join(parts[:i])) is not None
                   for i, p in enumerate(parts) if i)  # fmt: skip

    # one value

    def resolve(self, key: str, want: str) -> Found | None:
        """The install's value for ``key`` of the kind ``want`` (text, long, picture, number, bool, guid), None where
        it gives none: the node's field of that name (or the plan's alias), the config assets' field of that name,
        else what of the node a field of that name shows (its name, description, icon, GUID)."""
        ctx = self.walk(key)
        names = ctx.names
        if not names or ctx.node is None:
            return None
        guid, base = ctx.node
        config = guid == ctx.config and not base
        found = self.by_name(ctx.node, ctx.trail, want, ctx.plan, own=config)
        configs = [ctx.config] if ctx.config and not config else []
        configs += [g for t in ctx.plan.extra for g in self.assets.by_template(t)[:1]]
        for other in configs if found is None else ():  # the scene's config assets, by name
            found = found or self.by_name((other, ""), ctx.trail, want, ctx.plan, own=other == ctx.config)
        if found is not None or config or base:  # a config, or a part of an asset, answers by name only
            return found
        return self.from_node(guid, names, want)

    def by_name(self, node: tuple[int, str], trail: list[str], want: str, plan, own: bool = False) -> Found | None:
        """The node's field named like the data field (or its plan alias); for a text object's ``Text`` the field
        named like the object."""
        leaf = trail[-1]
        parent = trail[-2] if len(trail) > 1 and not trail[-2].startswith("[") else ""  # an element's own Text
        generic = _flat(leaf) in ("text", "value", "icon", "name", "data")
        wanted = (parent or leaf) if generic else leaf
        if not wanted:
            return None
        tags = [*plan.aliases.get(wanted, "").split("|"), wanted]  # an alias may list several fields, the first found
        for tag in dict.fromkeys(t for t in tags if t):
            for raw in self.assets.values(node, tag, own)[:1]:
                got = self._typed(raw, want)
                if got is not None:
                    return Found(got, f"{node[0]} {tag}")
        return None

    def from_node(self, guid: int, names: list[str], want: str) -> Found | None:
        """What of an asset a field shows by its name and the kind its binding wants: name, description, icon or
        GUID."""
        what = _leaf_kind(_flat(names[-1]), want)
        if what == "name":
            text = self.assets.name(guid, self.language)
            return Found(text, f"{guid}: name") if text else None
        if what == "long":
            text = self.assets.description(guid, self.language) or self.assets.name(guid, self.language)
            return Found(text, f"{guid}: description") if text else None
        if what == "picture":
            icon = self.assets.icon(guid)
            return Found(icon, f"{guid}: icon") if icon else None
        if what == "guid":
            return Found(guid, f"{guid}: GUID")
        return None

    def _typed(self, raw: str, want: str):
        """An asset's stored text as the kind a binding wants: a text id as its line, a GUID as its asset's name or
        icon, a number as a number."""
        line = self.assets.text(raw, self.language)
        if want in ("text", "long"):
            if line is not None:
                return line
            if raw.isdigit() and self.assets.element(int(raw)) is not None:
                return self.assets.name(int(raw), self.language)
            return None if _LOCA.match(raw) else raw  # a text id without a line gives none; a number is shown
        if want == "picture":
            if "/" in raw or raw.endswith(".png"):
                return raw
            return self.assets.icon(int(raw)) if raw.isdigit() else None
        if want in ("number", "guid"):
            try:
                return int(raw) if raw.lstrip("-").isdigit() else float(raw)
            except ValueError:
                return None
        if want == "bool":
            return raw not in ("0", "false", "False", "")
        return None


def _leaf_kind(low: str, want: str) -> str | None:
    """What of an asset a field of this (flattened) name shows, by its name and the kind its binding wants."""
    if any(w in low for w in PICTURE_LEAVES):
        return "picture" if want in ("picture", "text", "value") else None
    if want == "guid" or (low in GUID_LEAVES and want in ("number", "value", "guid")):
        return "guid"
    if want in ("text", "long", "value"):
        if any(w in low for w in LONG_LEAVES) or want == "long":
            return "long"
        if any(low.endswith(w) or low == w for w in TEXT_LEAVES):
            return "name"
    return None


def want_of(scope) -> str:
    """The kind of value the binding ``scope`` is reading now wants (``scope.Scope._asking``): a list, an object, a
    text, a long text, a picture, a number, a flag."""
    from . import placeholders

    asking = getattr(scope, "_asking", None)
    if asking is None:
        return "value"
    binding, index = asking
    for target in binding.targets:
        ptype = _target_type(scope, target)
        if ptype in OBJECT_TYPES:
            return OBJECT_TYPES[ptype]
        if target.kind == "path" and target.ids and target.ids[-1] == DATA_SOURCE:
            return "list"
        if target.kind == "path" and target.ids and target.ids[-1] in placeholders.PICTURE_IDS:
            return "picture"
    vtype, name = scope.describe(binding.targets[0]) if binding.targets else ("value", "")
    source = scope.describe(binding.sources[index])[1] if index < len(binding.sources) else ""
    low = _flat(source or name)
    if any(w in low for w in PICTURE_LEAVES) and vtype in ("string", "value"):
        return "picture"
    if vtype == "string":
        return "long" if any(w in low for w in LONG_LEAVES) else "text"
    if vtype in ("float", "int"):
        return "number"
    return "bool" if vtype == "bool" else "value"


def _target_type(scope, end) -> int | None:
    """The declared type of the custom property a target end names: one of the scope's own, or of the widget file
    the targeted child places."""
    from .scope import root_info

    uid = scope._own_end(end)
    if uid is not None:
        return scope.own_info.get(uid, {}).get("type")
    if end.kind != "path" or len(end.ids) != 1 or end.ids[0] >= 0x80000000:
        return None
    node = scope.children.get(scope._target(end) or "")
    from . import fields

    placed = scope.ctx.root(fields.get(node, "Template")) if node is not None else None
    if placed is None:
        return None
    info = next((i for i in root_info(scope.ctx, placed).props if i["uid"] == end.ids[0]), None)
    return info.get("type") if info else None


def origins(scene: str, graph, guid: str, samples, live: str = "") -> list[tuple[object, object, str]]:
    """Where the values the open file's bindings give the element ``guid`` came from: per binding target (an end of
    ``graph``), the value read and "live" / "recorded" (``live``, the element's held values), "game", "sample" or
    "placeholder"; the source key's own origin for game data (``Samples.from_game``)."""
    rows = []
    for b in graph.bindings:
        targets = [t for t in b.targets if t.kind == "path" and t.instance == guid]
        sources = [s for s in b.sources if s.kind == "path" and s.mode == 1 and len(s.ids) > 1]
        for target in targets:
            for source in sources[:1]:
                key = "/".join([scene, *(f"{i:08x}" for i in source.ids)])
                if live:
                    rows.append((target, None, live))
                elif key in samples.from_game:
                    rows.append((target, None, f"game: {samples.from_game[key]}"))
                elif key in samples.generated:
                    rows.append((target, samples.values.get(key), "placeholder"))
                elif key in samples.values:
                    rows.append((target, samples.values.get(key), "sample"))
    return rows


def attach(ctx, game: GameData | None) -> None:
    """Give a preview (``layout.Context``) the game's data, or take it away."""
    ctx.game_data = game
    ctx.samples.distinct = game.distinct if game is not None else None
    if game is not None:
        game.lists.clear()


def stamp(ctx) -> tuple | None:
    """What a preview's kept results depend on of its game data (``layout.Context.conditions``)."""
    game = getattr(ctx, "game_data", None)
    if game is None:
        return None
    game.defaults = previewdefaults.current(ctx)  # as ``lookup`` sets it: the stamp before a layout is the one after
    return game.stamp


def field_name(ctx, uid: int) -> str:
    """The name of a field id of the game's data objects ("" without game data or where the table has none)."""
    game = getattr(ctx, "game_data", None)
    return game.field_name(uid) if game is not None else ""


def first_of_group(ctx, uid: int) -> bool | None:
    """Whether the field ``uid`` is the first of the side-by-side child objects of its class (``stub_groups``); None
    for a field in no such group or without game data."""
    game = getattr(ctx, "game_data", None)
    return game.groups.get(game.field_name(uid)) if game is not None else None


def list_elements(ctx, owner: str, inputs: dict, n: int, element: Callable[[int], str]):
    """A list's count and element keys: the elements of the array its data source names where the game's data
    holds that array and no value is given under the list's own key, else ``n`` and ``element`` unchanged."""
    from .scope import GameObjectRef

    game, source = getattr(ctx, "game_data", None), inputs.get(DATA_SOURCE)
    source = getattr(source, "base", source)  # a ``scope.GivenObject``: the object passed for the property
    if game is None or not isinstance(source, GameObjectRef):
        return n, element
    if ctx.samples.given_under(f"{owner}/{DATA_SOURCE:08x}/"):
        return n, element
    count = game.count(source.key)
    if count is None:
        return n, element
    return count, lambda i: f"{source.key}/{ELEMENT + i:08x}"


def lookup(scope, key: str):
    """What the open game's data gives the key ``scope`` reads now (``scope.Scope._follow``): a game object for a
    field that holds one (so the path goes on into the widget files it feeds), else a value; None without game data
    or where the install has none. The sample panel learns where it came from (``Samples.from_game``)."""
    game = _ready(scope.ctx)
    if game is None:
        return None
    note_names(game, scope)
    want = want_of(scope)
    origins = getattr(scope.ctx.samples, "from_game", None)
    if want in ("list", "object") or game.is_object(key):
        from .scope import GameObjectRef

        if origins is not None:
            origins[key] = ".".join(game.walk(key).names[-2:])  # the object or list the path goes on into
        return GameObjectRef(key)
    found = game.resolve(key, want)
    if found is None or found.value is None:
        return None
    if origins is not None:
        origins[key] = found.origin
    return found.value


def _ready(ctx) -> GameData | None:
    """A preview's game data set for a read now: the preview's assumptions, its language and Play mode's choices
    (what reads the game's data follows a choice made in Play mode); None without game data."""
    game = getattr(ctx, "game_data", None)
    if game is None:
        return None
    game.defaults = previewdefaults.current(ctx)  # the one place the preview's assumptions are read
    game.language = ctx.language
    game.play = getattr(ctx, "play_choices", None)  # a Play mode choice a plan follows (the open page)
    if game.play is not None:
        from .playstate import TOKEN

        for name in list(getattr(game.play, "values", {}) or {}):
            ctx.memo.note((TOKEN, name))
    memo = getattr(ctx, "memo", None)  # a plan that looks for a choice not made yet (``gameplans.chosen_value``)
    game.note = memo.note if memo is not None else None
    return game


def object_child(ctx, loader: dict, given, children: list):
    """The child an object loader (the file ``loader``) shows for the game object ``given``: the one whose files
    read the fields of the object's data class (``GameData.class_of``) best, the most of them and the fewest it does
    not have; None where the class is not known or no child reads one (docs/ui/117/game-data.md "Object loaders")."""
    from .scope import GameObjectRef

    base = getattr(given, "base", given)
    game = _ready(ctx)
    if game is None or not isinstance(base, GameObjectRef):
        return None
    names = game.classes.get(game.class_of(base.key) or "", frozenset())
    scores = []
    for i, c in enumerate(children):
        read = fields_read(ctx, loader, CURRENT_OBJECT, c.guid)
        scores.append((len(read & names) - len(read - names), len(read & names), -i, c))
    best = max(scores, key=lambda s: s[:3], default=None)
    return best[3] if best is not None and best[1] > 0 else None


def fields_read(ctx, root: dict | None, uid: int, child: str | None = None, depth: int = 8) -> set[str]:
    """The names of the fields a widget file reads from the game object in its property ``uid``, with those the
    files it hands the object to read (``child``: only through that instance)."""
    from . import bindings
    from .fields import get
    from .scope import root_info

    if root is None or depth <= 0:
        return set()
    info, out = root_info(ctx, root), set()
    for b in info.graph.bindings:
        for e in (*b.sources, *b.targets):
            if child is None and e.kind == "path" and e.mode == bindings.SELF and len(e.ids) > 1 and e.ids[0] == uid:
                out.add(field_name(ctx, e.ids[1]))
        if not any(s.ids == [uid] and (s.kind == "own" or s.mode == bindings.SELF) for s in b.sources):
            continue
        for t in b.targets:  # the object handed into a placed file
            if t.kind == "path" and t.mode == bindings.CHILD and len(t.ids) == 1 and child in (None, t.instance):
                node = info.children.get(t.instance or "")
                placed = ctx.root(get(node, "Template")) if node is not None else None
                out |= fields_read(ctx, placed, t.ids[0], None, depth - 1)
    out.discard("")
    return out


def note_names(game: GameData | None, scope) -> None:
    """Keep a scope's own property names, so that keys made under them are named (their ids are the files')."""
    if game is not None:
        for uid, info in scope.own_info.items():
            if info.get("name"):
                game.own_names.setdefault(uid, info["name"])
