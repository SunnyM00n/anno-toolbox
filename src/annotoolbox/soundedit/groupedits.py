"""The edits of state and switch groups: the Init bank's state manager (``formats/bnkstmg``) and the names the editor
shows for a group and its values.

A group and each of its values is an id, the hash of its name (``group_id``); a Switch container and a Set-State or
Set-Switch action use it by id (``edits``), the Init bank's ``STMG`` chunk registers every state group and the switch
groups a game parameter drives. What is written and what is not known about the game: docs/sound/bank-objects.md "State
and switch groups"; the user's side: docs/sound-editor.md "Groups".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..exporting.banks import GroupNames
from ..formats import bnkcurves, bnkstmg
from ..formats.bnk import fnv1_32
from ..infra.i18n import N_, tr
from .registry import EditError, check_u32, edit, reading

if TYPE_CHECKING:
    from .bankdoc import BankDoc

STMG = b"STMG"


def group_id(name: str) -> int:
    """The id of a group, a state or a switch: the FNV-1 hash of its lower-case name."""
    return fnv1_32(name)


def _id(name_or_id) -> int:
    """An id given as a number or as a name (hashed)."""
    if isinstance(name_or_id, str):
        if not name_or_id.strip():
            raise EditError(tr("common.group_value_needs_name"))
        return group_id(name_or_id.strip())
    return check_u32(name_or_id, tr("sound.an_id"), zero=False)


def manager(doc: BankDoc) -> bnkstmg.StateManager:
    """The bank's state manager; ``EditError`` for a bank without one (only the Init bank has it)."""
    data = doc.chunk(STMG)
    if data is None:
        raise EditError(tr("common.state_manager_init_bank_open"))
    with reading(tr("sound.state_manager")):
        return bnkstmg.read(data, doc.version)


def _put(doc: BankDoc, sm: bnkstmg.StateManager) -> None:
    doc.put_chunk(STMG, bnkstmg.render(sm))


def _name(doc: BankDoc, ident: int, name: str, values) -> None:
    """Show a group and its values by name from now on (the names are the editor's: the game's info file is not
    written)."""
    names = doc.info.groups.setdefault(ident, GroupNames(name.strip()))
    names.name = name.strip()
    names.values.update({_id(v): str(v).strip() for v in values if isinstance(v, str)})


def _transition(value) -> int:
    if not 0 <= int(value) < 2**31:
        raise EditError(tr("common.transition_time_number_milliseconds"))
    return int(value)


def _customs(rows) -> list[tuple[int, int, int]]:
    try:
        return [(_id(a), _id(b), _transition(t)) for a, b, t in rows]
    except (TypeError, ValueError) as exc:
        raise EditError(tr("common.write_custom_transition_state")) from exc


# state groups


@edit(N_("sound.add_state_group"))
def add_state_group(doc: BankDoc, group: str, states: list | None = None, transition: int = 0) -> int:
    """A state group (``group``: its name, hashed; ``states``: the names of its states, kept as names) with its default
    transition time in ms; returns its id."""
    sm, ident = manager(doc), _id(group)
    if ident in [g.id for g in sm.states]:
        raise EditError(tr("common.state_manager_group_already", group=ident))
    sm.states.append(bnkstmg.StateGroup(ident, _transition(transition)))
    _put(doc, sm)
    _name(doc, ident, group if isinstance(group, str) else str(group), states or [])
    return ident


def _state_group(sm: bnkstmg.StateManager, group) -> bnkstmg.StateGroup:
    ident = _id(group)
    found = next((g for g in sm.states if g.id == ident), None)
    if found is None:
        raise EditError(tr("common.state_manager_no_group", group=ident))
    return found


@edit(N_("sound.change_state_group"))
def set_state_group(doc: BankDoc, group, transition: int | None = None, custom: list | None = None) -> None:
    """A state group's default transition time and its custom transitions (rows of from state, to state, ms; a state
    as a name or an id)."""
    sm = manager(doc)
    found = _state_group(sm, group)
    if transition is not None:
        found.transition = _transition(transition)
    if custom is not None:
        found.custom = _customs(custom)
    _put(doc, sm)


@edit(N_("sound.remove_state_group"))
def remove_state_group(doc: BankDoc, group) -> None:
    """A state group out of the state manager."""
    sm = manager(doc)
    sm.states.remove(_state_group(sm, group))
    _put(doc, sm)


# switch groups a game parameter drives


def _points(rows) -> list[tuple[float, int, int]]:
    try:
        out = [(float(x), _id(switch), int(interp)) for x, switch, interp in rows]
    except (TypeError, ValueError) as exc:
        raise EditError(tr("common.write_point_parameter_value_switch")) from exc
    try:
        bnkcurves.check_curve([p[0] for p in out], [p[2] for p in out], minimum=1)
    except ValueError as exc:
        raise EditError(tr("sound.switch_group_follows_parameter_along", exc=exc)) from exc
    return out


@edit(N_("sound.add_switch_group"))
def add_switch_group(
    doc: BankDoc, group: str, parameter, points: list, switches: list | None = None
) -> int:  # fmt: skip
    """A switch group that a game parameter drives (the Init bank registers only these): ``points`` are rows of the
    parameter's value, the switch it selects from there on (name or id) and the interpolation (9: constant);
    returns the group's id."""
    sm, ident = manager(doc), _id(group)
    if ident in [s.id for s in sm.switches]:
        raise EditError(tr("common.state_manager_group_already", group=ident))
    sm.switches.append(bnkstmg.SwitchGroup(ident, _id(parameter), 0, _points(points)))
    _put(doc, sm)
    values = [*(switches or []), *[p[1] for p in points if isinstance(p[1], str)]]
    _name(doc, ident, group if isinstance(group, str) else str(group), values)
    return ident


@edit(N_("sound.remove_switch_group"))
def remove_switch_group(doc: BankDoc, group) -> None:
    """A switch group out of the state manager."""
    sm, ident = manager(doc), _id(group)
    found = next((s for s in sm.switches if s.id == ident), None)
    if found is None:
        raise EditError(tr("common.state_manager_no_switch_group", group=ident))
    sm.switches.remove(found)
    _put(doc, sm)


# a new group from the window's form


def plan_group(
    doc: BankDoc, kind: str, name: str, values: list[str], parameter: str = "", points: list | None = None
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
    ids = ", ".join(f"{v} = {group_id(v)}" for v in values)
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


def group_lines(doc: BankDoc, ident: int) -> list[str]:
    """The facts of a state or switch group of the Init bank ([] when ``ident`` is none)."""
    try:
        sm = manager(doc)
    except EditError:
        return []
    info = doc.info
    title, value = info.group_label(ident), lambda v: info.group_label(ident, v)

    for g in sm.states:
        if g.id == ident:
            lines = [tr("common.state_group_default_transition_ms", name=title, id=ident, time=g.transition)]
            return lines + [tr("common.transition_to_ms", src=value(a), dst=value(b), time=t) for a, b, t in g.custom]
    for s in sm.switches:
        if s.id == ident:
            return [
                tr(
                    "common.switch_group_driven_game_parameter",
                    name=title,
                    id=ident,
                    parameter=s.parameter,
                )
            ] + [tr("common.x_switch_name", x=f"{x:g}", name=value(sw)) for x, sw, _k in s.points]
    return []


def group_rows(doc: BankDoc) -> list[tuple[int, str]]:
    """(id, title) of every state and switch group of the state manager, by name where the editor knows one."""
    try:
        sm = manager(doc)
    except EditError:
        return []

    named = doc.info.group_label
    out = [(g.id, tr("sound.state_group_name", name=named(g.id))) for g in sm.states]
    out += [(s.id, tr("sound.switch_group_name", name=named(s.id))) for s in sm.switches]
    return sorted(out, key=lambda row: row[1])
