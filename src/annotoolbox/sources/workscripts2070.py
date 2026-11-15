"""What 2070's work-unit scripts say, as numbers: the fishery boat, the mine drone, the felling and planting workers.

``read`` parses ``fishery.csl``, ``minefeedback.csl``, ``lumberjack.csl`` and ``treefarmer.csl`` (``formats/csl2070``)
and returns their sleeps, search radius, dummy names, drone asset and stand-off, so the unit builders take them from the
data instead of typing them (docs/formats/csl.md, "Work-unit scripts"). Times are milliseconds, distances tiles.
A script whose shape differs from the one read here gives ``None`` for that part and a warning, never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats import csl2070 as csl
from .overlay import SourceTree

API = "data/ai/cslapi.xml"
FISHERY = "data/ai/feedback/fishery.csl"
MINE = "data/ai/feedback/minefeedback.csl"
LUMBERJACK = "data/ai/economy/lumberjack.csl"
TREEFARMER = "data/ai/economy/treefarmer.csl"


@dataclass(frozen=True)
class FisheryScript:
    berth_dummy: str  # where the boat is created ("ship", the building's dummy of that name; index -1)
    pier_dummy: str  # the pier it sails to first ("harbour_0", index 0)
    water_radius: float | None  # _getWaterPos search radius; a plain number in the script (no _REALINT_ONE), so tiles
    work_sequence: int | None  # the animation it plays at the fishing ground
    work_repeats: int | None  # ... this many times (the sleep is the animation's length times this)
    rest_ms: int | None  # the sleep after each trip
    no_water_ms: int | None  # the sleep when no water was found


@dataclass(frozen=True)
class MineScript:
    drone_guid: int | None  # _feedback_transportation_droneGUID
    dummy: str  # "drone": the dummy of the mine and of its station the drone flies between
    hover_ms: int | None  # the sleep after the flight to the mine
    landed_ms: int | None  # the sleep after the flight back, before it fades
    rest_ms: int | None  # the sleep between flights
    fly_sequences: tuple[int, ...]  # the walk sequence out (1000) and back (1001)


@dataclass(frozen=True)
class FellScript:
    stand_off: float | None  # tiles in front of the tree (the script adds 2048 of 4096 towards the building)
    fall_ms: int | None  # treeFallingTime
    cuts: tuple[tuple[int, int], ...]  # (work sequence, first-hit time ms): the chopping variants, in script order
    shake: tuple[int | None, ...] = ()  # _shakeTree's numbers after the two handles
    no_tree_ms: int | None = None  # the sleep when there is no tree
    sink_ms: int | None = None  # the sleep while the felled tree sinks (fall + 3000)
    fade_ms: int | None = None  # _startTreeDying's fade-out time (fall + 5000)
    work_end_ms: int | None = None  # the sleep between the fall and the end of the work sequence


@dataclass(frozen=True)
class PlantScript:
    stand_off: float | None
    no_position_ms: int | None
    sequences: tuple[int, ...]  # the sequences whose full length it sleeps, in order


@dataclass
class WorkScripts:
    fishery: FisheryScript | None = None
    mine: MineScript | None = None
    fell: FellScript | None = None
    plant: PlantScript | None = None
    warnings: list[str] = field(default_factory=list)


def _load(tree: SourceTree, path: str, api: csl.Api, warnings: list[str]) -> csl.Script | None:
    if not tree.exists(path):
        warnings.append(f"work scripts: {path} is not in 2070's data")
        return None
    try:
        return csl.load(tree.read_text(path, "latin-1"), api)
    except csl.CslError as err:
        warnings.append(f"work scripts: {path}: {err}")
        return None


def _known_sleeps(script: csl.Script, function: str) -> list[int]:
    return [ms for _, ms in script.sleeps(function) if ms is not None]


def _sequence_sleep(script: csl.Script, function: str) -> tuple[int | None, int | None]:
    """``_sleep(_getSequenceDuration(SEQ) * n)``: the sequence and n (1 when there is no factor)."""
    for event, _ in script.sleeps(function):
        arg = event.call.args[0]
        factor = 1
        if isinstance(arg, csl.Binary) and arg.op == "*":
            arg, factor = arg.left, script.fold(arg.right) or 1
        if isinstance(arg, csl.Call) and arg.name == "_getSequenceDuration":
            return script.fold(arg.args[0]), factor
    return None, None


def _dummy_names(script: csl.Script) -> list[str]:
    return [a.text for e in script.calls("_getDummyPosition") for a in e.call.args if isinstance(a, csl.Str)]


def _stand_off(script: csl.Script) -> float | None:
    """The distance the worker stops short of the tree: ``targetPos.x += (...) * 2048 / dist`` in fixed point."""
    for _fn, stmts in _bodies(script):
        for stmt in stmts:
            if isinstance(stmt, csl.Assign) and stmt.op == "+=" and isinstance(stmt.target, csl.Member):
                nums = _numbers(stmt.value)
                if nums:
                    return nums[0] / csl.REALINT_ONE
    return None


def _bodies(script: csl.Script):
    for fn in script.module.functions.values():
        yield fn.name, _flat(fn.body)


def _flat(stmts):
    for s in stmts:
        yield s
        for part in (getattr(s, "then", []), getattr(s, "other", []), getattr(s, "body", [])):
            yield from _flat(part)


def _numbers(node) -> list[int]:
    if isinstance(node, csl.Num):
        return [node.value]
    if isinstance(node, csl.Binary):
        return _numbers(node.left) + _numbers(node.right)
    return []


def _fishery(script: csl.Script) -> FisheryScript | None:
    names = _dummy_names(script)
    radius = [script.fold(e.call.args[-1]) for e in script.calls("_getWaterPos")]
    if len(names) < 2 or not radius:
        return None
    sequence, repeats = _sequence_sleep(script, "goFishing")
    main = _known_sleeps(script, "main")
    idle = [ms for e, ms in script.sleeps("goFishing") if ms is not None and any(w.startswith("not ") for w in e.when)]
    return FisheryScript(
        names[0],
        names[1],
        radius[0],
        sequence,
        repeats,
        main[-1] if main else None,
        idle[0] if idle else None,
    )


def _mine(script: csl.Script) -> MineScript | None:
    names = _dummy_names(script)
    spawn = script.calls("_createObjectAtPos3D")
    if not names or not spawn:
        return None
    fly = _known_sleeps(script, "fly")
    main = _known_sleeps(script, "main")
    sequences = tuple(
        v
        for e in script.events("fly")
        if e.name == "_setWalkSequence" and (v := script.fold(e.call.args[0])) is not None
    )
    return MineScript(
        script.fold(spawn[0].call.args[0]),
        names[0],
        fly[0] if len(fly) > 0 else None,
        fly[1] if len(fly) > 1 else None,
        main[-1] if main else None,
        sequences,
    )


def _fell(script: csl.Script) -> FellScript | None:
    if "lumber" not in script.module.functions:
        return None
    falling = script.values("treeFallingTime") or [None]
    seqs = script.assignments("workSequence", "lumber")
    times = script.assignments("cutTime", "lumber")
    cuts = tuple((s, t) for s, t in zip(seqs, times, strict=False) if s is not None and t is not None)
    shake = [script.folded(e)[2:] for e in script.calls("_shakeTree")]
    sleeps = [ms for _, ms in script.sleeps("lumber")]
    dying = [script.folded(e)[-1] for e in script.calls("_startTreeDying")]
    sinks = [ms for ms in sleeps if ms is not None and falling[0] is not None and ms == falling[0] + 3000]
    return FellScript(
        _stand_off(script),
        falling[0],
        cuts,
        tuple(shake[0]) if shake else (),
        next((ms for ms in sleeps if ms == 30000), None),
        sinks[0] if sinks else None,
        dying[0] if dying and dying[0] is not None and dying[0] != 0 else None,
        next((ms for ms in sleeps if ms in (2000,)), None),
    )


def _plant(script: csl.Script) -> PlantScript | None:
    if "plant" not in script.module.functions:
        return None
    sequences = tuple(
        v
        for e in script.events("plant")
        if e.name == "_getSequenceDuration" and (v := script.fold(e.call.args[0])) is not None
    )
    no_pos = [ms for e, ms in script.sleeps("plant") if ms is not None and e.when]
    return PlantScript(_stand_off(script), no_pos[0] if no_pos else None, sequences)


def read(tree: SourceTree) -> WorkScripts:
    """The four scripts' numbers; a missing or unreadable script leaves its part ``None`` and a warning."""
    out = WorkScripts()
    if not tree.exists(API):
        out.warnings.append(f"work scripts: {API} is not in 2070's data")
        return out
    api = csl.read_api(tree.read_text(API, "latin-1"))
    for path, attr, reader in (
        (FISHERY, "fishery", _fishery),
        (MINE, "mine", _mine),
        (LUMBERJACK, "fell", _fell),
        (TREEFARMER, "plant", _plant),
    ):
        script = _load(tree, path, api, out.warnings)
        value = reader(script) if script is not None else None
        if script is not None and value is None:
            out.warnings.append(f"work scripts: {path} has another shape than the reader expects")
        setattr(out, attr, value)
    return out
