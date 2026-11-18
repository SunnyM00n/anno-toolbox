"""The binding operators: the classes a binding runs its sources through before it writes its targets (the files'
``ConverterInstance`` subclasses), with what each computes, so the preview can evaluate a binding.

``OPERATORS`` by type hash, ``by_name`` by display name, ``evaluate(op, inputs)`` the outputs (None: the binding
writes nothing this time). The behaviour was read from the game with test values and is listed in
docs/formats/phoenix-scene-format.md, "Bindings"; the enum family and the update 3.0 ones are from their fields only.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from ..infra.i18n import N_

BASE = "6fd1ffc4"  # the operator base class every binding's operator derives from
ASSIGN = "c346a1bd"


@dataclass(frozen=True)
class Operator:
    hash: str
    name: str
    family: str
    inputs: tuple[int, int]  # the fewest and the most inputs
    outputs: int
    doc: str
    run: Callable[[list], list | None] | None = None
    tested: bool = True  # False: described from the stored fields, not run in the game


def _num(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, (tuple, list)):
        return tuple(_num(x) for x in v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def _equal(xs: list) -> bool:
    """Whether two inputs are equal: two texts as texts (``Equal(icon, "")``: an icon's name is not the empty text,
    though neither is a number), anything else as numbers."""
    if isinstance(xs[0], str) and isinstance(xs[1], str):
        return xs[0] == xs[1]
    return _num(xs[0]) == _num(xs[1])


def _pair(a, b, f):
    a, b = _num(a), _num(b)
    if isinstance(a, tuple) or isinstance(b, tuple):
        n = max(len(a) if isinstance(a, tuple) else 1, len(b) if isinstance(b, tuple) else 1)
        aa = a if isinstance(a, tuple) else (a,) * n
        bb = b if isinstance(b, tuple) else (b,) * n
        return tuple(f(x, y) for x, y in zip(aa, bb, strict=False))
    return f(a, b)


def _fold(f):
    def run(xs):
        out = xs[0]
        for x in xs[1:]:
            out = _pair(out, x, f)
        return [out]

    return run


def _one(f):
    def run(xs):
        v = _num(xs[0])
        return [tuple(f(x) for x in v) if isinstance(v, tuple) else f(v)]

    return run


def _color(f):
    def run(xs):
        out = tuple(_num(xs[0]))
        for x in xs[1:]:
            out = tuple(max(0, min(255, int(f(a, b)))) for a, b in zip(out, _num(x), strict=False))
        return [out]

    return run


def _div(a, b):
    return a / b if b else 0


def _mod(a, b):
    a, b = int(a), int(b)
    return int(math.fmod(a, b)) if b else 0


def _round(x):
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _text(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else f"{v:.1f}"
    return str(v)


def _value(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        try:
            return float(s)
        except (TypeError, ValueError):
            return 0


def _cmp(f):
    return lambda xs: [bool(f(_num(xs[0]), _num(xs[1])))]


def _range(strict: bool):
    def run(xs):
        v, lo, hi = (_num(x) for x in xs[:3])
        return [lo < v < hi if strict else lo <= v <= hi]

    return run


def _logic(f):
    def run(xs):
        out = xs[0]
        for x in xs[1:]:
            out = f(out, x)
        return [out]

    return run


def _and(a, b):
    return a & b if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) else bool(a and b)


def _or(a, b):
    return a | b if isinstance(a, int) and isinstance(b, int) and not isinstance(a, bool) else bool(a or b)


def _xor(a, b):
    return bool(a) != bool(b)


def _if(want: bool):
    return lambda xs: [xs[0]] if bool(xs[1]) == want else None


def _event_to(value: bool):
    return lambda xs: [value] if xs[0] else None


def _count(xs):
    return [len(xs[0]) if isinstance(xs[0], (list, tuple)) else 0]


def _empty(xs):
    return [not xs[0]]


def _element(xs):
    seq, i = xs[0], int(_num(xs[1]) if len(xs) > 1 else 0)
    return [seq[i] if isinstance(seq, (list, tuple)) and 0 <= i < len(seq) else None]


def _random(xs):
    return [random.uniform(float(_num(xs[0])), float(_num(xs[1])))]


def _concat(xs):
    return ["".join(_text(x) for x in xs)]


# fmt: off
_TABLE = (
    # hash, name, family, (min, max) inputs, outputs, what, how
    (ASSIGN, "Assign", "Assign", (1, 1), 1, N_("ui.value"), lambda xs: [xs[0]]),
    ("bba41a31", "TwoWayAssign", "Assign", (1, 1), 1, N_("ui.value_written_back_both_ways"), lambda xs: [xs[0]]),
    ("a3de9f56", "EventAssign", "Assign", (2, 2), 1, N_("ui.in0_when_event_in1_fires"), lambda xs: [xs[0]] if xs[1] else None),
    ("0b2ef3a9", "IfTrueAssign", "Assign", (2, 2), 1, N_("ui.in0_while_in1_true_else"), _if(True)),
    ("e6623aa6", "IfFalseAssign", "Assign", (2, 2), 1, N_("ui.in0_while_in1_false_else"), _if(False)),
    ("fa72bb0a", "IfTrueFalseAssign", "Assign", (3, 3), 1, N_("ui.in2_in0_in1"), lambda xs: [xs[0] if xs[2] else xs[1]]),
    ("97f3862c", "Add", "Numerical", (2, 8), 1, N_("ui.sum_folded_left_vectors_per"), _fold(lambda a, b: a + b)),
    ("072acb84", "Subtract", "Numerical", (2, 8), 1, N_("ui.in0_minus_rest"), _fold(lambda a, b: a - b)),
    ("d251ac32", "Substract", "Numerical", (2, 8), 1, N_("ui.old_files_subtract"), _fold(lambda a, b: a - b)),
    ("d5037298", "Multiply", "Numerical", (2, 8), 1, N_("ui.product_folded_left"), _fold(lambda a, b: a * b)),
    ("616bee77", "Min", "Numerical", (2, 2), 1, N_("ui.the_smaller"), _fold(min)),
    ("4815136f", "Max", "Numerical", (2, 2), 1, N_("ui.the_larger"), _fold(max)),
    ("23bf72e5", "AddColor", "Numerical", (2, 8), 1, N_("ui.per_channel_clamped_0_255"), _color(lambda a, b: a + b)),
    ("f98fa2f7", "SubtractColor", "Numerical", (2, 8), 1, N_("ui.per_channel_clamped"), _color(lambda a, b: a - b)),
    ("b8770396", "MultiplyColor", "Numerical", (2, 8), 1, N_("ui.per_channel_b_255"), _color(lambda a, b: a * b / 255)),
    ("b52f3dd9", "Divide", "Maths", (2, 2), 1, N_("ui.in0_in1_0_zero_divisor"), _fold(_div)),
    ("4a39ee27", "DivideColor", "Maths", (2, 2), 1, N_("ui.per_channel"), _color(lambda a, b: a / b * 255 if b else 0)),
    ("dbe093fe", "Modulo", "Maths", (2, 2), 1, N_("ui.integers_c_remainder"), _fold(_mod)),
    ("76af01ea", "Clamp", "Maths", (3, 3), 1, N_("ui.value_min_max"), lambda xs: [max(_num(xs[1]), min(_num(xs[2]), _num(xs[0])))]),
    ("c5a2db43", "Abs", "Maths", (1, 1), 1, N_("ui.absolute_value"), _one(abs)),
    ("8bd85eca", "Sign", "Maths", (1, 1), 1, N_("ui.negates_x"), _one(lambda x: -x)),
    ("a150854b", "SquareRoot", "Maths", (1, 1), 1, N_("ui.square_root"), _one(lambda x: math.sqrt(max(x, 0)))),
    ("55bed03f", "Floor", "Maths", (1, 1), 1, N_("ui.rounded_down"), _one(math.floor)),
    ("22b69259", "Ceil", "Maths", (1, 1), 1, N_("ui.rounded_up"), _one(math.ceil)),
    ("9b18e043", "Round", "Maths", (1, 1), 1, N_("ui.rounded_half_away_zero"), _one(_round)),
    ("98550e35", "Sin", "Maths", (1, 1), 1, N_("ui.sine_radians"), _one(math.sin)),
    ("38473df0", "Cos", "Maths", (1, 1), 1, N_("ui.cosine_radians"), _one(math.cos)),
    ("70b0b523", "Tan", "Maths", (1, 1), 1, N_("ui.tangent_radians"), _one(math.tan)),
    ("3227879c", "DegreeToRadian", "Maths", (1, 1), 1, N_("ui.degrees_to_radians"), _one(math.radians)),
    ("719fa13c", "RadianToDegree", "Maths", (1, 1), 1, N_("ui.radians_to_degrees"), _one(math.degrees)),
    ("a91becfd", "BoolToNumber", "Casts and text", (1, 1), 1, N_("ui.true_1_false_0"), lambda xs: [1 if xs[0] else 0]),
    ("0f8445b8", "NumberToBool", "Casts and text", (1, 1), 1, N_("ui.x_not_0"), lambda xs: [_num(xs[0]) != 0]),
    ("4aa26b0b", "NumberToFloat", "Casts and text", (1, 1), 1, N_("ui.as_a_float"), lambda xs: [float(_num(xs[0]))]),
    ("19296de7", "ValueToString", "Casts and text", (1, 1), 1, N_("ui.values_text_7_0_2"), lambda xs: [_text(xs[0])]),
    ("fb38a630", "StringToValue", "Casts and text", (1, 1), 1, N_("ui.number_text"), lambda xs: [_value(xs[0])]),
    ("ef9a050c", "ConcatValues", "Casts and text", (2, 4), 1, N_("ui.texts_joined"), _concat),
    ("9bd3b2dc", "Equal", "Comparison", (2, 2), 1, N_("ui.in0_eq_in1"), lambda xs: [_equal(xs)]),
    ("1bd6542a", "NotEqual", "Comparison", (2, 2), 1, N_("ui.in0_ne_in1"), lambda xs: [not _equal(xs)]),
    ("14cebcc9", "Greater", "Comparison", (2, 2), 1, N_("ui.in0_gt_in1"), _cmp(lambda a, b: a > b)),
    ("08ac46a1", "GreaterOrEqual", "Comparison", (2, 2), 1, N_("ui.in0_gt_eq_in1"), _cmp(lambda a, b: a >= b)),
    ("72eb91e5", "Lesser", "Comparison", (2, 2), 1, N_("ui.in0_lt_in1"), _cmp(lambda a, b: a < b)),
    ("acef0b0a", "LesserOrEqual", "Comparison", (2, 2), 1, N_("ui.in0_lt_eq_in1"), _cmp(lambda a, b: a <= b)),
    ("2308d788", "IsInRange", "Comparison", (3, 3), 1, N_("ui.min_lt_eq_value_max"), _range(False)),
    ("de094420", "IsInRangeButNotEqual", "Comparison", (3, 3), 1, N_("ui.min_lt_value_max"), _range(True)),
    ("06ac2bda", "LogicalAnd", "Logical", (2, 8), 1, N_("ui.and_integers_bitwise"), _logic(_and)),
    ("9759e6fa", "LogicalOr", "Logical", (2, 8), 1, N_("ui.or_integers_bitwise"), _logic(_or)),
    ("cf6f7bda", "LogicalXOr", "Logical", (2, 2), 1, N_("ui.exclusive_or"), _logic(_xor)),
    ("111e8241", "LogicalInvert", "Logical", (1, 1), 1, N_("ui.not"), lambda xs: [not xs[0]]),
    ("2bd13d9d", "IsNull", "Logical", (1, 1), 1, N_("ui.no_value_empty_game_object"), lambda xs: [xs[0] in (None, 0, "", ())]),
    ("c32c7054", "EventToTrue", "Events", (1, 1), 1, N_("ui.true_when_event_fires"), _event_to(True)),
    ("130e634e", "EventToFalse", "Events", (1, 1), 1, N_("ui.false_when_event_fires"), _event_to(False)),
    ("599c5545", "EventToBoolChange", "Events", (1, 1), 1, N_("ui.toggles_when_event_fires"), None),
    ("f8fa428f", "FalseToTrueChange", "Events", (1, 1), 1, N_("ui.fires_event_when_value_turns_true"), None),
    ("5b0e8ff5", "TrueToFalseChange", "Events", (1, 1), 1, N_("ui.fires_event_when_value_turns_false"), None),
    ("4c9ce34d", "ValueChange", "Events", (1, 1), 1, N_("ui.fires_event_when_value_changes"), None),
    ("13ffffc1", "Delta", "Events", (1, 1), 4, N_("ui.difference_events_increase_decrease_any"), None),
    ("d7ef3d58", "EnumToBoolean", "Enum", (1, 1), 8, N_("ui.target_targetindex_true_while_enum"), None, False),
    ("5e3ef09a", "EnumToEvent", "Enum", (1, 1), 8, N_("ui.fires_target_targetindex_when_enum"), None, False),
    ("6c4a2404", "EnumToValue", "Enum", (1, 1), 1, N_("ui.value_enum_assets_rule_gives"), None, False),
    ("09d78724", "GetCount", "Arrays", (1, 1), 1, N_("ui.number_elements"), _count, False),
    ("25838498", "IsEmpty", "Arrays", (1, 1), 1, N_("ui.whether_none"), _empty, False),
    ("4dad286c", "GetElementAt", "Arrays", (2, 2), 1, N_("ui.element_index"), _element, False),
    ("8a049cb7", "GetRandomValue", "Arrays", (2, 2), 1, N_("ui.random_value_between_two"), _random, False),
)
# fmt: on
FAMILY_LABELS = {  # an operator's family -> how it is shown
    "Assign": N_("ui.assign"),
    "Numerical": N_("ui.numerical"),
    "Maths": N_("ui.maths"),
    "Comparison": N_("ui.comparison"),
    "Logical": N_("ui.logical"),
    "Events": N_("common.events"),
    "Casts and text": N_("ui.casts_text"),
    "Enum": N_("ui.enum"),
    "Arrays": N_("ui.arrays"),
}
OPERATORS: dict[str, Operator] = {
    row[0]: Operator(row[0], row[1], row[2], row[3], row[4], row[5], row[6], *(row[7:] or (True,))) for row in _TABLE
}


def by_name(name: str) -> Operator | None:
    return next((o for o in OPERATORS.values() if o.name == name), None)


def name_of(type_hash: str, schema: dict | None = None) -> str:
    """The display name of an operator class: ours, else the schema's without its suffix, else the hash."""
    if type_hash in OPERATORS:
        return OPERATORS[type_hash].name
    rec = (schema or {}).get(type_hash) or {}
    name = rec.get("name") or ""
    return name.removesuffix("ConverterInstance") or type_hash


def evaluate(type_hash: str, inputs: list) -> list | None:
    """The outputs of an operator for ``inputs``; None when it writes nothing (unknown, an event-only rule, a
    condition that does not hold, an input it cannot use)."""
    op = OPERATORS.get(type_hash)
    if op is None or op.run is None or not inputs:
        return None
    try:
        return op.run(list(inputs))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, IndexError):
        return None


def families() -> dict[str, list[Operator]]:
    """The operators by family, in the table's order (the palette of the binding graph)."""
    out: dict[str, list[Operator]] = {}
    for op in OPERATORS.values():
        out.setdefault(op.family, []).append(op)
    return out
