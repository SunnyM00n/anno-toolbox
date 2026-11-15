"""Placeholder data for the preview: where a binding reads a game value the sample values do not hold, a value of
the kind the binding needs is made up - text of a fitting length, a plausible number, true for what decides
visibility, a neutral picture named after its slot - so a scene the game fills at run time previews whole.

``guess(binding, index, describe)`` gives the value for one source of a binding; ``Samples.placeholder`` keeps it,
marked as generated. docs/ui-editor.md "Sample values and placeholders".
"""

from __future__ import annotations

from collections.abc import Callable

from . import fields, operators

PICTURE = "placeholder:"  # a DynamicImageId the canvas draws as a named stand-in
TEXTS = {
    "short": "Lorem ipsum",
    "medium": "Lorem ipsum dolor sit amet",
    "long": "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore.",
}
NUMBERS = (("percent", 75.0), ("progress", 0.6), ("ratio", 0.6), ("factor", 1.0), ("count", 12),
           ("amount", 250), ("money", 1500), ("cost", 1500), ("price", 1500), ("index", 0), ("level", 3),
           ("tier", 3))  # fmt: skip
BOOL_FAMILIES = {"Logical"}
NUMBER_FAMILIES = {"Numerical", "Maths", "Comparison"}
TEXT_IDS = {0x80000008}  # a label's Text
PICTURE_IDS = {0x8000002A}  # an image's DynamicImageId
FIXED = {"AlphaTint": 1.0, "LocalizationId": None, "ZIndex": None, "Repeat": None}  # not filled by kind


def text_for(name: str) -> str:
    """A filler text whose length suits a property of this name (a header short, a description long)."""
    low = name.lower()
    if any(w in low for w in ("header", "title", "name", "caption", "button", "label")):
        return TEXTS["short"]
    if any(w in low for w in ("desc", "text", "info", "tooltip", "hint", "body")):
        return TEXTS["long"]
    return TEXTS["medium"]


def number_for(name: str):
    low = name.lower()
    return next((v for word, v in NUMBERS if word in low), 42)


def _by_type(vtype: str, name: str):
    if name in FIXED:
        return FIXED[name]
    if vtype == "bool":
        return True
    if vtype in ("float", "int"):
        return number_for(name)
    if vtype == "string":
        return text_for(name)
    if vtype == "color":
        return (255, 255, 255)
    return None


def target_kind(uid: int) -> tuple[str, str]:
    """(value type, name) of a built-in property a binding writes (``fields.BUILTINS``)."""
    b = fields.BUILTIN_BY_ID.get(uid)
    return (b.vtype, b.name) if b is not None else ("value", "")


def guess(binding, index: int, describe: Callable[[object], tuple[str, str]]):
    """A placeholder for source ``index`` of ``binding``: by the operator's family where it decides the kind, else by
    the target; ``describe(end)`` gives (value type, name) of a target end (``target_kind`` for built-ins)."""
    op = operators.OPERATORS.get(binding.operator)
    family = op.family if op is not None else ""
    source_name = describe(binding.sources[index])[1]
    if family in BOOL_FAMILIES or op is not None and op.name in ("IfTrueFalseAssign",) and index == 2:
        return True
    if op is not None and op.name in ("NumberToString", "NumberToBool"):
        return number_for(source_name)
    if op is not None and op.name.startswith("EnumTo"):
        return None  # an enum's values are UniqueIds; the first one is the template's
    for target in binding.targets:
        if target.kind == "path" and target.ids and target.ids[-1] in PICTURE_IDS:
            return PICTURE + (source_name or "picture")
        if target.kind == "path" and target.ids and target.ids[-1] in TEXT_IDS:
            return text_for(source_name)
        vtype, name = describe(target)
        value = _by_type(vtype, source_name or name)
        if value is not None:
            return value
    if family in NUMBER_FAMILIES:
        return number_for(source_name)
    return None
