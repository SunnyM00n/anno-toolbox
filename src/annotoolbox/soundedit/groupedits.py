"""The edits of state and switch groups: the Init bank's state manager (``formats/bnkstmg``) and the names the editor
shows for a group and its values.

A group and each of its values is an id, the hash of its name (``group_id``); a Switch container and a Set-State or
Set-Switch action use it by id (``edits``), the Init bank's ``STMG`` chunk registers every state group and the switch
groups a game parameter drives. What is written and what is not known about the game: docs/sound/bank-objects.md "State
and switch groups"; the user's side: docs/sound-editor.md "Groups".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..exporting.banks import SGroupNames
from ..formats import bnkcurves, bnkstmg
from ..formats.bnk import fnv1_32
from ..infra.i18n import N_, tr
from .registry import CEditError, check_u32, edit, reading

if TYPE_CHECKING:
    from .bankdoc import CBankDoc

STMG = b"STMG"


def group_id(name: str) -> int:
    """The id of a group, a state or a switch: the FNV-1 hash of its lower-case name."""
    return fnv1_32(name)


def _id(name_or_id) -> int:
    """An id given as a number or as a name (hashed)."""
    if isinstance(name_or_id, str):
        if not name_or_id.strip():
            raise CEditError(tr("common.group_value_needs_name"))
        return group_id(name_or_id.strip())
    return check_u32(name_or_id, tr("sound.an_id"), zero=False)


def manager(doc: CBankDoc) -> bnkstmg.SStateManager:
    """The bank's state manager; ``CEditError`` for a bank without one (only the Init bank has it)."""
    data = doc.chunk(STMG)
    if data is None:
        raise CEditError(tr("common.state_manager_init_bank_open"))
    with reading(tr("sound.state_manager")):
        return bnkstmg.read(data, doc.m_version)


def _put(doc: CBankDoc, state_manager: bnkstmg.SStateManager) -> None:
    doc.put_chunk(STMG, bnkstmg.render(state_manager))


def _name(doc: CBankDoc, ident: int, name: str, values) -> None:
    """Show a group and its values by name from now on (the names are the editor's: the game's info file is not
    written)."""
    names = doc.m_bank_info.groups.setdefault(ident, SGroupNames(name.strip()))
    names.name = name.strip()
    names.values.update({_id(value): str(value).strip() for value in values if isinstance(value, str)})


def _transition(value) -> int:
    if not 0 <= int(value) < 2**31:
        raise CEditError(tr("common.transition_time_number_milliseconds"))
    return int(value)


def _customs(rows) -> list[tuple[int, int, int]]:
    try:
        return [(_id(source), _id(target), _transition(transition)) for source, target, transition in rows]
    except (TypeError, ValueError) as exc:
        raise CEditError(tr("common.write_custom_transition_state")) from exc


# state groups


@edit(N_("sound.add_state_group"))
def add_state_group(doc: CBankDoc, group: str, states: list | None = None, transition: int = 0) -> int:
    """A state group (``group``: its name, hashed; ``states``: the names of its states, kept as names) with its default
    transition time in ms; returns its id."""
    state_manager, ident = manager(doc), _id(group)
    if ident in [existing.id for existing in state_manager.states]:
        raise CEditError(tr("common.state_manager_group_already", group=ident))
    state_manager.states.append(bnkstmg.SStateGroup(ident, _transition(transition)))
    _put(doc, state_manager)
    _name(doc, ident, group if isinstance(group, str) else str(group), states or [])
    return ident


def _state_group(state_manager: bnkstmg.SStateManager, group) -> bnkstmg.SStateGroup:
    ident = _id(group)
    found = next((existing for existing in state_manager.states if existing.id == ident), None)
    if found is None:
        raise CEditError(tr("common.state_manager_no_group", group=ident))
    return found


@edit(N_("sound.change_state_group"))
def set_state_group(doc: CBankDoc, group, transition: int | None = None, custom: list | None = None) -> None:
    """A state group's default transition time and its custom transitions (rows of from state, to state, ms; a state
    as a name or an id)."""
    state_manager = manager(doc)
    found = _state_group(state_manager, group)
    if transition is not None:
        found.transition = _transition(transition)
    if custom is not None:
        found.custom = _customs(custom)
    _put(doc, state_manager)


@edit(N_("sound.remove_state_group"))
def remove_state_group(doc: CBankDoc, group) -> None:
    """A state group out of the state manager."""
    state_manager = manager(doc)
    state_manager.states.remove(_state_group(state_manager, group))
    _put(doc, state_manager)


# switch groups a game parameter drives


def _points(rows) -> list[tuple[float, int, int]]:
    try:
        out = [(float(x), _id(switch), int(interp)) for x, switch, interp in rows]
    except (TypeError, ValueError) as exc:
        raise CEditError(tr("common.write_point_parameter_value_switch")) from exc
    try:
        bnkcurves.check_curve([point[0] for point in out], [point[2] for point in out], minimum=1)
    except ValueError as exc:
        raise CEditError(tr("sound.switch_group_follows_parameter_along", exc=exc)) from exc
    return out


@edit(N_("sound.add_switch_group"))
def add_switch_group(
    doc: CBankDoc, group: str, parameter, points: list, switches: list | None = None
) -> int:  # fmt: skip
    """A switch group that a game parameter drives (the Init bank registers only these): ``points`` are rows of the
    parameter's value, the switch it selects from there on (name or id) and the interpolation (9: constant);
    returns the group's id."""
    state_manager, ident = manager(doc), _id(group)
    if ident in [switch.id for switch in state_manager.switches]:
        raise CEditError(tr("common.state_manager_group_already", group=ident))
    state_manager.switches.append(bnkstmg.SSwitchGroup(ident, _id(parameter), 0, _points(points)))
    _put(doc, state_manager)
    values = [*(switches or []), *[point[1] for point in points if isinstance(point[1], str)]]
    _name(doc, ident, group if isinstance(group, str) else str(group), values)
    return ident


@edit(N_("sound.remove_switch_group"))
def remove_switch_group(doc: CBankDoc, group) -> None:
    """A switch group out of the state manager."""
    state_manager, ident = manager(doc), _id(group)
    found = next((switch for switch in state_manager.switches if switch.id == ident), None)
    if found is None:
        raise CEditError(tr("common.state_manager_no_switch_group", group=ident))
    state_manager.switches.remove(found)
    _put(doc, state_manager)


# a new group from the window's form


def plan_group(
    doc: CBankDoc, kind: str, name: str, values: list[str], parameter: str = "", points: list | None = None
) -> tuple[str, dict] | str:
    """What a new group ``name`` (``kind``: ``state`` or ``switch``) with its ``values`` needs: the edit and its
    arguments when the bank is the Init bank and the group is one it registers (a state group; a switch group a game
    parameter drives), else the sentence that tells where it lives. A group that needs no definition is named in the
    editor so the lists show it, and its ids are given for the forms that take one."""
    if not name.strip():
        return tr("common.group_needs_name")
    state = kind == "state"
    if (state or parameter.strip()) and doc.chunk(STMG) is not None:
        if state:
            return "add_state_group", {"group": name.strip(), "states": values}
        return "add_switch_group", {"group": name.strip(), "parameter": parameter.strip(), "points": points or []}
    _name(doc, group_id(name), name, values)
    ids = ", ".join(f"{value} = {group_id(value)}" for value in values)
    if state:
        return tr(
            "common.state_groups_registered_init_bank",
            name=name.strip(),
            id=group_id(name),
            ids=ids,
        )
    return tr(
        "common.switch_group_needs_no_definition",
        name=name.strip(),
        id=group_id(name),
        ids=ids,
    )


# what the window shows


def group_lines(doc: CBankDoc, ident: int) -> list[str]:
    """The facts of a state or switch group of the Init bank ([] when ``ident`` is none)."""
    try:
        state_manager = manager(doc)
    except CEditError:
        return []
    bank_info = doc.m_bank_info
    title, value = bank_info.group_label(ident), lambda member: bank_info.group_label(ident, member)

    for group in state_manager.states:
        if group.id == ident:
            lines = [tr("common.state_group_default_transition_ms", name=title, id=ident, time=group.transition)]
            return lines + [
                tr("common.transition_to_ms", src=value(source), dst=value(target), time=time)
                for source, target, time in group.custom
            ]
    for switch in state_manager.switches:
        if switch.id == ident:
            return [
                tr(
                    "common.switch_group_driven_game_parameter",
                    name=title,
                    id=ident,
                    parameter=switch.parameter,
                )
            ] + [tr("common.x_switch_name", x=f"{x:g}", name=value(sw)) for x, sw, _k in switch.points]
    return []


def group_rows(doc: CBankDoc) -> list[tuple[int, str]]:
    """(id, title) of every state and switch group of the state manager, by name where the editor knows one."""
    try:
        state_manager = manager(doc)
    except CEditError:
        return []

    named = doc.m_bank_info.group_label
    out = [(group.id, tr("sound.state_group_name", name=named(group.id))) for group in state_manager.states]
    out += [(switch.id, tr("sound.switch_group_name", name=named(switch.id))) for switch in state_manager.switches]
    return sorted(out, key=lambda row: row[1])
