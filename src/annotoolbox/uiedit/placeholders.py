"""Placeholder data for the preview: where a binding reads a game value the sample values do not hold, a value of
the kind the binding needs is made up - text of a fitting length, a plausible number, the typical state for a flag
(true for what decides visibility, false for a lock, an error or a warning), a neutral picture named after its slot -
so a scene the game fills at run time previews whole and coherent.

``guess(binding, index, describe, locked)`` gives the value for one source of a binding; ``Samples.placeholder`` keeps
it, marked as generated. docs/ui-editor.md "Sample values and placeholders".
"""

from __future__ import annotations

from collections.abc import Callable

from . import fields, operators, previewdefaults

PICTURE = "placeholder:"  # a DynamicImageId the canvas draws as a named stand-in
TEXTS = {
    "short": "Lorem ipsum",
    "medium": "Lorem ipsum dolor sit amet",
    "long": "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore.",
}
NUMBERS = (("percent", 75.0), ("progress", 0.6), ("ratio", 0.6), ("factor", 1.0), ("count", 12),
           ("amount", 250), ("money", 1500), ("cost", 1500), ("price", 1500), ("index", 0), ("level", 3),
           ("tier", 3))  # fmt: skip
FILLERS = frozenset(TEXTS.values())  # the texts the preview makes up
COUNT_WORDS = ("amount", "count", "money", "cost", "price", "percent", "level", "tier", "quantity", "population")
ID_NAMES = ("id", "guid", "assetguid", "refguid")
PICTURE_WORDS = (
    "icon",
    "image",
    "picture",
    "portrait",
    "artwork",
    "emblem",
    "logo",
    "model2d",
)  # a text slot's picture
PICTURE_TEXT_WORDS = ("text", "label", "name", "title", "caption", "tooltip", "desc", "header", "info")
BOOL_FAMILIES = {"Logical"}
NUMBER_FAMILIES = {"Numerical", "Maths", "Comparison"}
TEXT_IDS = {0x80000008}  # a label's Text
PICTURE_IDS = {0x8000002A}  # an image's DynamicImageId
# a label's TextPlane: a layout object nobody fills holds 0 in the game, and a plane of 0 takes the text's size
# (the options tabs' texts: layout.md "Values the game gives at run time")
PLANE_IDS = {0x8000000F}
FIXED = {"AlphaTint": 1.0, "LocalizationId": None, "ZIndex": None, "Repeat": None}  # not filled by kind
# flags whose typical state is off: the overlay or message they show is the exception (a lock is ``is_locked``)
EXCEPTIONS = ("disable", "error", "warning", "invalid", "blocked", "forbid", "fail", "denied", "unavailable",
              "insufficient", "notenough", "missing", "empty", "editactive", "editmode")  # fmt: skip
USUAL = ("enable", "available", "valid")  # their opposites, checked after them ("invalid" holds "valid")
SELECTED = ("selected", "seleted")  # a selection flag; the game's tab data spells it "IsSeleted"
ELEMENT = 0x90000000  # a list's element i in a sample key (``listlayout.ELEMENT``)


def is_locked(ctx, key: str | None = None) -> bool:
    """Whether the preview shows what is locked as locked: the preview's defaults decide (all unlocked by default,
    ``previewdefaults``); under the game object ``key`` a GUID given there that the defaults lock shows its lock.
    ``ctx`` is the ``layout.Context``."""
    defaults = previewdefaults.current(ctx)
    if key and defaults.locked and ctx is not None:
        samples = ctx.m_samples
        for k in samples.given_under(key + "/"):
            value = samples.m_values.get(k)
            if isinstance(value, int) and not isinstance(value, bool) and defaults.is_locked(value):
                return True
    return defaults.is_locked()


def named(binding, index: int, describe: Callable[[object], tuple[str, str]], defaults):
    """What the preview's defaults give a source whose name, or its target's, names a value of the player's game
    (a character's name, money, a gamepad or multiplayer flag: ``previewdefaults``); None for any other."""
    names = [describe(binding.sources[index])[1], *(describe(end)[1] for end in binding.targets)]
    for target in binding.targets:
        text = target.kind == "path" and target.ids and target.ids[-1] in TEXT_IDS
        vtype = "string" if text else describe(target)[0]
        for name in filter(None, names):
            found = {"string": previewdefaults.text_for, "bool": previewdefaults.bool_for}.get(
                vtype, previewdefaults.number_for if vtype in ("int", "float") else None
            )
            value = found(name, defaults) if found else None
            if value is not None:
                return value
    return None


def state_for(name: str, locked: bool = False) -> bool | None:
    """The typical state of a flag of this name: off for a lock (unless ``locked``), an error, a warning or an
    empty list, on for their opposites; None where the name says nothing."""
    low = name.lower().replace("_", "").replace("-", "")
    if "unlock" in low:
        return not locked
    if "lock" in low:
        return locked
    if any(word in low for word in EXCEPTIONS):
        return False
    if any(word in low for word in USUAL):
        return True
    return None


def selection_for(name: str, key: str, first_of: Callable[[int], bool | None] = lambda _: None,
                  index_of: Callable[[str], int | None] = lambda _: None) -> bool | None:  # fmt: skip
    """The typical state of a selection flag of this name read at the sample key ``key``: one of a group is
    selected, the first, as the game opens a tab bar or a menu on its first entry. The nearest group above the flag
    decides: a list (its element 0, or the one ``index_of(the list's key)`` names: a tab Play mode selected) or
    child objects of one class side by side (``first_of(field id)``, the data classes' order: a menu's first access
    button); none: off. None where the name says nothing (layout.md "Values the game gives at run time")."""
    low = name.lower().replace("_", "")
    if not any(word in low for word in SELECTED):
        return None
    parts = key.split("/")
    ids = [(i, int(part, 16)) for i, part in enumerate(parts) if i and len(part) == 8]
    for at, uid in reversed(ids[:-1]):
        if uid >= ELEMENT:
            chosen = index_of("/".join(parts[:at]))
            return uid - ELEMENT == (chosen if chosen is not None else 0)
        if (first := first_of(uid)) is not None:
            return first
    return False


def _through(binding, want: bool) -> bool | None:
    """The flag that gives a binding's target ``want`` through its operator (an inverted flag is the opposite)."""
    for candidate in (True, False):
        out = operators.evaluate(binding.operator, [candidate] * len(binding.sources))
        if out and out[0] is not None and bool(out[0]) == want:
            return candidate
    return None


def _named_state(binding, index: int, describe, locked: bool) -> bool | None:
    """The flag the names say for source ``index``: its own name, else a target's through the operator."""
    own = state_for(describe(binding.sources[index])[1], locked)
    if own is not None:
        return own
    for target in binding.targets:
        want = state_for(describe(target)[1], locked)
        if want is not None and (found := _through(binding, want)) is not None:
            return found
    return None


def flag_for(binding, index: int, describe: Callable[[object], tuple[str, str]], locked: bool,
             stored: Callable[[object], object] | None = None, readers: tuple = ()) -> bool:  # fmt: skip
    """A flag for source ``index``: the typical state its own name or a target's name says (``state_for``), else
    what the names say at the other bindings reading the same game value (``readers``: (binding, index) pairs; one
    value, one state: an edit mode's flag hides its info panel too), else the flag a target stores (a layer the file
    keeps hidden, shown by the game at times: a drag hint), through the binding's operator, else true."""
    for reader, i in ((binding, index), *readers):
        found = _named_state(reader, i, describe, locked)
        if found is not None:
            return found
    for target in binding.targets if stored is not None else ():
        if isinstance(kept := stored(target), bool) and (found := _through(binding, kept)) is not None:
            return found
    return True


def text_for(name: str) -> str:
    """A filler text whose length suits a property of this name (a header short, a description long)."""
    low = name.lower()
    if any(word in low for word in ("header", "title", "name", "caption", "button", "label")):
        return TEXTS["short"]
    if any(word in low for word in ("desc", "info", "tooltip", "hint", "body", "paragraph")):  # a bare "Text": medium
        return TEXTS["long"]
    if low in ID_NAMES or any(
        word in low for word in COUNT_WORDS
    ):  # a number shown as text: the value the game counts at run time
        return str(number_for(name))
    return TEXTS["medium"]


def number_for(name: str):
    low = name.lower()
    return next((number for word, number in NUMBERS if word in low), 42)


def is_picture_name(name: str) -> bool:
    """Whether a text property of this name holds a picture's name (``Icon``, ``Model2D``), not the text shown with
    one (``IconText``)."""
    low = name.lower()
    return any(word in low for word in PICTURE_WORDS) and not any(word in low for word in PICTURE_TEXT_WORDS)


def _by_type(vtype: str, name: str):
    if name in FIXED:
        return FIXED[name]
    if vtype == "bool":
        return True
    if vtype in ("float", "int"):
        return number_for(name)
    if vtype == "string":
        return PICTURE + name if is_picture_name(name) else text_for(name)
    if vtype == "color":
        return (255, 255, 255)
    return None


def target_kind(uid: int) -> tuple[str, str]:
    """(value type, name) of a built-in property a binding writes (``fields.BUILTINS``)."""
    builtin = fields.BUILTIN_BY_ID.get(uid)
    return (builtin.vtype, builtin.name) if builtin is not None else ("value", "")


def guess(binding, index: int, describe: Callable[[object], tuple[str, str]], locked: bool = False, defaults=None,
          stored: Callable[[object], object] | None = None, readers: tuple = ()):  # fmt: skip
    """A placeholder for source ``index`` of ``binding``: what the preview's ``defaults`` give its name (``named``),
    else by the operator's family where it decides the kind, else by the target; ``describe(end)`` gives (value
    type, name) of a target end (``target_kind`` for built-ins), ``locked`` whether what is locked shows as locked
    (``is_locked``), ``stored(end)`` what a target end stores and ``readers`` the other bindings reading the same
    value (a flag's typical state, ``flag_for``)."""
    if defaults is not None and (value := named(binding, index, describe, defaults)) is not None:
        return value
    op = operators.OPERATORS.get(binding.operator)
    family = op.family if op is not None else ""
    source_name = describe(binding.sources[index])[1]
    if op is not None and op.name == "IfTrueFalseAssign" and index == 2:  # the condition: names only
        state = state_for(source_name, locked)
        others = (_named_state(reader, i, describe, locked) for reader, i in readers)
        return next((candidate for candidate in (state, *others) if candidate is not None), True)
    if family in BOOL_FAMILIES:
        return flag_for(binding, index, describe, locked, stored, readers)
    if op is not None and op.name in ("NumberToString", "NumberToBool"):
        return number_for(source_name)
    if op is not None and op.name.startswith("EnumTo"):
        return None  # an enum's values are UniqueIds; the first one is the template's
    if any(end.kind == "path" and end.ids and end.ids[-1] in PLANE_IDS for end in binding.targets):
        return 0.0
    for target in binding.targets:
        if target.kind == "path" and target.ids and target.ids[-1] in PICTURE_IDS:
            return PICTURE + (source_name or "picture")
        if target.kind == "path" and target.ids and target.ids[-1] in TEXT_IDS:
            return text_for(source_name)
        vtype, name = describe(target)
        if vtype == "picture":  # a text property its file feeds into a picture
            return PICTURE + (source_name or name or "picture")
        if name in FIXED:  # what the target is decides, whatever the source is called
            if FIXED[name] is not None:
                return FIXED[name]
            continue
        if vtype == "bool" and family not in NUMBER_FAMILIES and (source_name or name) not in FIXED:
            return flag_for(binding, index, describe, locked, stored, readers)
        value = _by_type(vtype, source_name or name)
        if value is not None:
            return value
    if family in NUMBER_FAMILIES:
        return number_for(source_name)
    return None
