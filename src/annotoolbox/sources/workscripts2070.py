"""What 2070's work-unit scripts say, as numbers: the fishery boat, the mine drone, the felling and planting workers.

``read`` parses ``fishery.csl``, ``minefeedback.csl``, ``lumberjack.csl`` and ``treefarmer.csl`` (``formats/csl2070``)
and returns their sleeps, search radius, dummy names, drone asset and stand-off, so the unit builders take them from the
data instead of typing them (docs/formats/csl.md, "Work-unit scripts"). Times are milliseconds, distances tiles.
A script whose shape differs from the one read here gives ``None`` for that part and a warning, never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats import csl2070 as csl
from .overlay import CSourceTree

API = "data/ai/cslapi.xml"
FISHERY = "data/ai/feedback/fishery.csl"
MINE = "data/ai/feedback/minefeedback.csl"
LUMBERJACK = "data/ai/economy/lumberjack.csl"
TREEFARMER = "data/ai/economy/treefarmer.csl"


@dataclass(frozen=True)
class SFisheryScript:
    berth_dummy: str  # where the boat is created ("ship", the building's dummy of that name; index -1)
    pier_dummy: str  # the pier it sails to first ("harbour_0", index 0)
    water_radius: float | None  # _getWaterPos search radius; a plain number in the script (no _REALINT_ONE), so tiles
    work_sequence: int | None  # the animation it plays at the fishing ground
    work_repeats: int | None  # ... this many times (the sleep is the animation's length times this)
    rest_ms: int | None  # the sleep after each trip
    no_water_ms: int | None  # the sleep when no water was found


@dataclass(frozen=True)
class SMineScript:
    drone_unit: int | None  # _feedback_transportation_droneGUID
    dummy: str  # "drone": the dummy of the mine and of its station the drone flies between
    hover_ms: int | None  # the sleep after the flight to the mine
    landed_ms: int | None  # the sleep after the flight back, before it fades
    rest_ms: int | None  # the sleep between flights
    fly_sequences: tuple[int, ...]  # the walk sequence out (1000) and back (1001)


@dataclass(frozen=True)
class SFellScript:
    stand_off: float | None  # tiles in front of the tree (the script adds 2048 of 4096 towards the building)
    fall_ms: int | None  # treeFallingTime
    cuts: tuple[tuple[int, int], ...]  # (work sequence, first-hit time ms): the chopping variants, in script order
    shake: tuple[int | None, ...] = ()  # _shakeTree's numbers after the two handles
    no_tree_ms: int | None = None  # the sleep when there is no tree
    sink_ms: int | None = None  # the sleep while the felled tree sinks (fall + 3000)
    fade_ms: int | None = None  # _startTreeDying's fade-out time (fall + 5000)
    work_end_ms: int | None = None  # the sleep between the fall and the end of the work sequence


@dataclass(frozen=True)
class SPlantScript:
    stand_off: float | None
    no_position_ms: int | None
    sequences: tuple[int, ...]  # the sequences whose full length it sleeps, in order


@dataclass
class SWorkScripts:
    fishery: SFisheryScript | None = None
    mine: SMineScript | None = None
    fell: SFellScript | None = None
    plant: SPlantScript | None = None
    warnings: list[str] = field(default_factory=list)


def _load(tree: CSourceTree, path: str, api: csl.SApi, warnings: list[str]) -> csl.CScript | None:
    if not tree.exists(path):
        warnings.append(f"work scripts: {path} is not in 2070's data")
        return None
    try:
        return csl.load(tree.read_text(path, "latin-1"), api)
    except csl.CCslError as error:
        warnings.append(f"work scripts: {path}: {error}")
        return None


def _known_sleeps(script: csl.CScript, function: str) -> list[int]:
    return [sleep_ms for _, sleep_ms in script.sleeps(function) if sleep_ms is not None]


def _sequence_sleep(script: csl.CScript, function: str) -> tuple[int | None, int | None]:
    """``_sleep(_getSequenceDuration(SEQ) * n)``: the sequence and n (1 when there is no factor)."""
    for event, _ in script.sleeps(function):
        arg = event.call.args[0]
        factor = 1
        if isinstance(arg, csl.SBinary) and arg.operator == "*":
            arg, factor = arg.left, script.fold(arg.right) or 1
        if isinstance(arg, csl.SCall) and arg.name == "_getSequenceDuration":
            return script.fold(arg.args[0]), factor
    return None, None


def _dummy_names(script: csl.CScript) -> list[str]:
    return [
        argument.text
        for event in script.calls("_getDummyPosition")
        for argument in event.call.args
        if isinstance(argument, csl.SStr)
    ]


def _stand_off(script: csl.CScript) -> float | None:
    """The distance the worker stops short of the tree: ``targetPos.x += (...) * 2048 / dist`` in fixed point."""
    for _fn, stmts in _bodies(script):
        for stmt in stmts:
            if isinstance(stmt, csl.SAssign) and stmt.operator == "+=" and isinstance(stmt.target, csl.SMember):
                numbers = _numbers(stmt.value)
                if numbers:
                    return numbers[0] / csl.REALINT_ONE
    return None


def _bodies(script: csl.CScript):
    for function in script.m_module.functions.values():
        yield function.name, _flat(function.body)


def _flat(stmts):
    for statement in stmts:
        yield statement
        for part in (getattr(statement, "then", []), getattr(statement, "other", []), getattr(statement, "body", [])):
            yield from _flat(part)


def _numbers(node) -> list[int]:
    if isinstance(node, csl.SNum):
        return [node.value]
    if isinstance(node, csl.SBinary):
        return _numbers(node.left) + _numbers(node.right)
    return []


def _fishery(script: csl.CScript) -> SFisheryScript | None:
    names = _dummy_names(script)
    radius = [script.fold(event.call.args[-1]) for event in script.calls("_getWaterPos")]
    if len(names) < 2 or not radius:
        return None
    sequence, repeats = _sequence_sleep(script, "goFishing")
    main = _known_sleeps(script, "main")
    idle = [
        sleep_ms
        for event, sleep_ms in script.sleeps("goFishing")
        if sleep_ms is not None and any(word.startswith("not ") for word in event.when)
    ]
    return SFisheryScript(
        names[0],
        names[1],
        radius[0],
        sequence,
        repeats,
        main[-1] if main else None,
        idle[0] if idle else None,
    )


def _mine(script: csl.CScript) -> SMineScript | None:
    names = _dummy_names(script)
    spawn = script.calls("_createObjectAtPos3D")
    if not names or not spawn:
        return None
    fly = _known_sleeps(script, "fly")
    main = _known_sleeps(script, "main")
    sequences = tuple(
        value
        for event in script.events("fly")
        if event.name == "_setWalkSequence" and (value := script.fold(event.call.args[0])) is not None
    )
    return SMineScript(
        script.fold(spawn[0].call.args[0]),
        names[0],
        fly[0] if len(fly) > 0 else None,
        fly[1] if len(fly) > 1 else None,
        main[-1] if main else None,
        sequences,
    )


def _fell(script: csl.CScript) -> SFellScript | None:
    if "lumber" not in script.m_module.functions:
        return None
    falling = script.values("treeFallingTime") or [None]
    seqs = script.assignments("workSequence", "lumber")
    times = script.assignments("cutTime", "lumber")
    cuts = tuple(
        (sequence, time)
        for sequence, time in zip(seqs, times, strict=False)
        if sequence is not None and time is not None
    )
    shake = [script.folded(event)[2:] for event in script.calls("_shakeTree")]
    sleeps = [sleep_ms for _, sleep_ms in script.sleeps("lumber")]
    dying = [script.folded(event)[-1] for event in script.calls("_startTreeDying")]
    sinks = [
        sleep_ms
        for sleep_ms in sleeps
        if sleep_ms is not None and falling[0] is not None and sleep_ms == falling[0] + 3000
    ]
    return SFellScript(
        _stand_off(script),
        falling[0],
        cuts,
        tuple(shake[0]) if shake else (),
        next((sleep_ms for sleep_ms in sleeps if sleep_ms == 30000), None),
        sinks[0] if sinks else None,
        dying[0] if dying and dying[0] is not None and dying[0] != 0 else None,
        next((sleep_ms for sleep_ms in sleeps if sleep_ms in (2000,)), None),
    )


def _plant(script: csl.CScript) -> SPlantScript | None:
    if "plant" not in script.m_module.functions:
        return None
    sequences = tuple(
        value
        for event in script.events("plant")
        if event.name == "_getSequenceDuration" and (value := script.fold(event.call.args[0])) is not None
    )
    conditional_sleeps_ms = [
        sleep_ms for event, sleep_ms in script.sleeps("plant") if sleep_ms is not None and event.when
    ]
    return SPlantScript(_stand_off(script), conditional_sleeps_ms[0] if conditional_sleeps_ms else None, sequences)


def read(tree: CSourceTree) -> SWorkScripts:
    """The four scripts' numbers; a missing or unreadable script leaves its part ``None`` and a warning."""
    out = SWorkScripts()
    if not tree.exists(API):
        out.warnings.append(f"work scripts: {API} is not in 2070's data")
        return out
    api = csl.read_api(tree.read_text(API, "latin-1"))
    for path, attribute, reader in (
        (FISHERY, "fishery", _fishery),
        (MINE, "mine", _mine),
        (LUMBERJACK, "fell", _fell),
        (TREEFARMER, "plant", _plant),
    ):
        script = _load(tree, path, api, out.warnings)
        value = reader(script) if script is not None else None
        if script is not None and value is None:
            out.warnings.append(f"work scripts: {path} has another shape than the reader expects")
        setattr(out, attribute, value)
    return out
