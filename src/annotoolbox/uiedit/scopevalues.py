"""What the preview's bindings pass around while a template is placed: the ids and limits of the engine's built-in
properties (``SIZE``, ``ALPHA``, the Animator's), a game object's reference and the fields written into it
(``SGameObjectRef``, ``SGivenObject``), a list item's slot, the axis helpers of a vector value, and the keys under which
a placing is kept (``canon``, ``inputs_key``). ``scope.CScope`` evaluates the bindings; ``scopeinfo`` reads one file.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..formats import phoenixedit
from . import fields
from .viewmodels import CSamples

ENUM_BOOLEAN, ENUM_VALUE = "d7ef3d58", "6c4a2404"  # the enum operators that read an enum asset
ENUM_CONVERTERS = (ENUM_BOOLEAN, ENUM_VALUE)
TWO_WAY = "bba41a31"  # TwoWayAssign
ALPHA, FADED = 0x80000006, 0.01  # AlphaTint; below FADED the design view shows the widget (layout.HIDDEN_ALPHA)
ANIMATOR = "d2506d55"  # the Animator component: StartValue, EndValue, CurrentValue (docs/ui/117/components.md)
ANIM_START, ANIM_END, ANIM_CURRENT = 0x71047B15, 0x7BEB711E, 0x7FB90677
ANIM_GO, ADDED = 0x78773C49, 0x8000003C  # the Animator's Start action; AddedToScene, fired as the scene opens

SIZE = 0x80000004
DATA_SOURCE, DATA_ITEM = 0x30000001, 0x30000002  # a list's array; the element (or its index) an item gets
COMPUTED = (SIZE, 0x80000037)  # read from the placed widget, not from its stored fields
DISPLAYED = (0x8000004F, 0x80000052)  # a label's DisplayedText and DisplayedSize: its text as laid out
PASSES = 12  # a value one binding writes may be another one's source: passes until nothing changes, at most
SHARES = 8  # exchanges of shared objects' fields with the placed files, at most (``Scope._share``)
GIVEN, OWN = "<given>", "<own>"  # who wrote an object's fields besides the placed instances (``Scope.shared``)
STORED = "<stored>"  # what the placing instance stores below the property: this file reads it, it is not shared
MISSING = object()
ALL = ("child", "*")  # in ``Scope._plan``: what reads a computed size, which every write to a child may change
OWN_TYPES = {0: "bool", 1: "int", 2: "int", 3: "float", 4: "string", 18: "color"}  # PropertyType -> value type
BLANK = "<a game object holding no given value>"  # ``canon``
FIRES_DEPTH = 6  # bindings followed back from an Animator's start to the scene's opening (``Scope._fires``)


@dataclass(frozen=True)
class SGameObjectRef:
    """A game object as the preview knows it: the sample key its fields are read under. ``holder``: inside a file,
    the instance whose own object it is (two instances of one widget file own objects of one key); never passed on."""

    key: str
    holder: str | None = None


@dataclass(frozen=True)
class SListSlot:
    """The element a list's item stands for: its game object and its index in the array."""

    element: SGameObjectRef
    index: int
    item: str | None  # the GUID of the item instance (the list file's one child)
    given: tuple[tuple[tuple[int, ...], object], ...] = ()  # what the game gives the item over all items (compares)


def axis(value, field: int):
    """One part of a value: ``field`` 1 the whole, 2 / 3 / 4 x / y / z."""
    if field == 1 or not isinstance(value, (tuple, list)):
        return value
    return value[field - 2] if field - 2 < len(value) else None


def with_axis(old, field: int, value, size: int = 3):
    """``old`` with one part (or the whole, ``field`` 1) replaced by ``value``."""
    if field == 1:
        return value
    if isinstance(value, (tuple, list)):  # a whole vector into one axis: that axis of it
        value = value[field - 2] if field - 2 < len(value) else (value[0] if value else 0.0)
    base = list(old) if isinstance(old, (tuple, list)) else [0.0] * size
    while len(base) < field - 1:
        base.append(0.0)
    base[field - 2] = value
    return tuple(base)


def builtin_value(node: dict, uid: int):
    """The stored value behind a built-in property of an instance (its field of the same name)."""
    builtin = fields.BUILTIN_BY_ID.get(uid)
    if builtin is None or builtin.kind != "property":
        return None
    return fields.get(node, builtin.name)


def animated_end(node: dict, path: tuple[int, ...], stored: dict, written: dict, default=lambda _uid: None):
    """In the design view, what an instance's ``Animator`` holds once its animation has run: its ``EndValue`` for a
    read of its ``CurrentValue`` while the instance stores the start there (the main menu's panels slide in from
    -30 to 40: layout.md "Values the game gives at run time"), or stores neither and the component declares them
    alike (``default``: a property id -> its declared default). None for any other read."""
    if len(path) != 2 or path[1] != ANIM_CURRENT:
        return None
    animators = {int(fields.get(component, "UniqueId", 0) or 0) for component in phoenixedit.objects(node, "Components")
                 if (fields.get(component, "ComponentTemplate") or "").startswith(ANIMATOR)}  # fmt: skip
    if path[0] not in animators:  # an instance may hold several (a text's show and hide)
        return None
    start, now = stored.get((path[0], ANIM_START), default(ANIM_START)), stored.get(path, default(ANIM_CURRENT))
    if now is None or now != start:
        return None
    return written.get((path[0], ANIM_END), stored.get((path[0], ANIM_END)))


def truthy(value) -> bool:
    """A value read as a flag: none, false, zero and the empty text are off."""
    return value not in (None, False, 0, 0.0, "")


def first_entry(ctx, enum: str | None) -> int | None:
    """The first entry of an enum asset (its UniqueId): what an enum the game fills reads in the design view while its
    value is unknown (a popup's state: closed), as an enum loader shows its first entry."""
    root = ctx.root(enum) if enum else None
    entries = phoenixedit.objects(root, "EnumEntries") if root is not None else []
    found = int(fields.get(entries[0], "UniqueId", 0) or 0) if entries else 0
    return found or None


@dataclass(frozen=True)
class SGivenObject:
    """A game object property of a placed widget file with fields the placing file wrote into it (the text plane,
    alignment and sizing of a text file's layout object): the placed file reads these fields as written."""

    base: object  # the value passed for the property itself (a ``SGameObjectRef``), None if none
    fields: tuple[tuple[tuple[int, ...], object], ...]  # (path below the property, value), sorted
    local: tuple[tuple[tuple[int, ...], object], ...] = ()  # what the instance stores there: read, not shared


def inputs_of(overrides: dict, node: dict | None = None) -> dict[int, object]:
    """The values an instance passes into its widget file's own properties (the ids below the built-ins); fields
    written below one of them come as a ``SGivenObject``, those the instance only stores (an author's preview text)
    as its ``local`` ones. ``node``: the instance, whose own components' values are not passed."""
    out: dict[int, object] = {
        path[0]: value for path, value in overrides.items() if len(path) == 1 and path[0] < 0x80000000
    }
    comps = (
        {int(fields.get(component, "UniqueId", 0) or 0) for component in phoenixedit.objects(node, "Components")}
        if node
        else ()
    )
    stored = fields.property_values(node) if node else {}
    nested: dict[int, list] = {}
    for path, value in overrides.items():
        if len(path) > 1 and path[0] < 0x80000000 and path[0] not in comps:
            nested.setdefault(path[0], []).append((tuple(path[1:]), value))
    for uid, given in nested.items():
        base = out.get(uid)
        if isinstance(base, SGivenObject):  # an object handed on with fields its file wrote: those win
            given, base = list({**dict(given), **dict(base.fields)}.items()), base.base
        local = [(path, value) for path, value in given if stored.get((uid, *path), MISSING) == value]
        written = [entry for entry in given if entry not in local]
        out[uid] = SGivenObject(base, tuple(sorted(written, key=lambda entry: entry[0])),
                               tuple(sorted(local, key=lambda entry: entry[0])))  # fmt: skip
    return out


def passed(reference: SGameObjectRef, known: dict[tuple[int, ...], object]):
    """A game object as a file passes it on (into a placed file, or back as an output): its key, with the fields
    written into it that the receiver does not know yet, as a ``SGivenObject`` (layout.md "Values the game gives at
    run time": objects are shared)."""
    base = SGameObjectRef(reference.key)
    return SGivenObject(base, tuple(sorted(known.items(), key=lambda kv: kv[0]))) if known else base


def canon(samples: CSamples, element: SGameObjectRef) -> object:
    """A list's element as a part of what is kept: one under which no value is given stands for any such, since every
    value read under it is made up the same way (the elements of a list the game fills share their bindings;
    docs/tool-suite.md section 20, "Speed")."""
    return BLANK if samples.blank(element.key) else element


def inputs_key(samples: CSamples, inputs: dict[int, object]) -> str:
    """What a file is given, as a part of what is kept: a game object in it holding no given value as any such
    (``canon``), so the elements of a list share what they pass into their widget files too."""

    def one(value):
        if isinstance(value, SGivenObject):
            return replace(value, base=one(value.base))
        return canon(samples, value) if isinstance(value, SGameObjectRef) else value

    return repr(sorted(((k, one(value)) for k, value in inputs.items()), key=lambda kv: kv[0]))
