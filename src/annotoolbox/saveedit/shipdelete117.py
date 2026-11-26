"""The delete of one ship from an Anno 117 save, as the game removes a destroyed ship (``docs/save-editor.md``,
"Ships (117)"; the layout and the evidence: ``docs/formats/save-format.md``, "Ships"). ``plan`` finds the ship and
every reference to it and says what is removed, set to none, left or refused; ``delete`` makes the change in the
session's document and in ``data.a7s``; ``write_beside`` writes it as a new file next to the save.
"""

from __future__ import annotations

import bisect
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import filedb, savegame117
from ..infra.i18n import N_, tr
from . import edits
from .container import SSaveFile
from .ships117 import SShip, number, read_ship, ship_spans

REMOVED, CLEARED, KEPT, REFUSED = "removed", "cleared", "kept", "refused"
NESTED = frozenset({"BinaryData"})  # a session's own document, searched on its own (ObjectIDs are per session)
STORAGES = frozenset({"StrgLrg", "StrgSmll"})  # goods maps of u32 words: a match there is an amount, not a reference
UNITS = "GameSessionManager/SessionUnitManager/UnitSystem"
ITEM_MAP = "GameSessionManager/ItemSessionManager/ItemMap"
LOCAL_EFFECTS = "MetaGameManager/MetaEffectManager/LocalEffects"
META_OBJECTS = "MetaGameManager/MetaGameObjectManager/MetaObjectMap"
ROUTES = "MetaGameManager/SessionTradeRouteManager/RouteMap"
UNKNOWN = N_("saves.ship_refused_unknown")
OBJECT_RULES = tuple((re.compile(pattern), action, reason) for pattern, action, reason in (
    (r"UnitSystem/AttackLocationHandler/UnitData/#32768$", KEPT, N_("saves.ship_kept_attack_memory")),
    (r"UnitSystem/Units/#1/Actor/Elements/", KEPT, N_("saves.ship_kept_old_target")),
    (r"/PropertyTradeRouteVehicle/TradeDescription/TargetShipID$", KEPT, N_("saves.ship_kept_trade_partner")),
    (r"NotificationSystem/ArchiveArray/Archive/", KEPT, N_("saves.ship_kept_message")),
    (r"/objects/#1/Health/LastAttackerID$", CLEARED, N_("saves.ship_cleared_last_attacker")),
    (r"LoadingPier", REFUSED, N_("saves.ship_refused_harbor")),
    (r"AIUnitManager|ConstructionAI|/Pirate/|/Trader/|MilitaryAI", REFUSED, N_("saves.ship_refused_ai")),
    (r"QuestManager|ConditionManager", REFUSED, N_("saves.ship_refused_quest")),
    (r"GroupBehaviorHandler", REFUSED, N_("saves.ship_refused_group")),
    (r"CarrierObject", REFUSED, N_("saves.ship_refused_units")),
    (r"SessionUnitManager", REFUSED, N_("saves.ship_refused_fight")),
))  # fmt: skip
META_RULES = tuple((re.compile(pattern), action, reason) for pattern, action, reason in (  # the fields of MetaIDs
    (r"/RouteMap/#1/LostShips/#1/ShipID$", KEPT, N_("saves.ship_kept_lost_record")),
    (r"/MetaUnitManager/QueuedTransfers/#1/TargetGameObject$", REFUSED, N_("saves.ship_refused_travel")),
    (r"/QuestManager/.*/MetaObjectID$", REFUSED, N_("saves.ship_refused_quest")),
    (r"/ShipHandler/Ships/(#32768|#1/Unit)$|/ShipHandler/Requests/#1/Ship$|/TradeShips/#1/ObjID$|/Trader/Units/#1/#32768$",
     REFUSED, N_("saves.ship_refused_ai")),
    (r"/MetaPersistent/MetaID$|/UnitMetaObjectRef$|/MetaObjectMap/#1/ID$|/RouteMap/#1/Ships$", REFUSED, UNKNOWN),
))  # fmt: skip
# a group behaviour's id is a small number too: only a transfer's GroupID counts (its group sails to another session)
GROUP_RULES = ((re.compile(r"/MetaUnitManager/QueuedTransfers/#1/GroupID$"), REFUSED, N_("saves.ship_refused_travel")),)


class CShipDeleteError(ValueError):
    """A ship that cannot be deleted: not found, or a reference the delete does not know how to remove."""


@dataclass(frozen=True)
class SReference:
    """One place that refers to the ship: ``place`` is its path (``session: ...`` or ``data.a7s: ...``), ``action``
    what the delete does there, ``reason`` a string id saying why or what."""

    place: str
    action: str
    reason: str


@dataclass(frozen=True)
class SPlan:
    """What a delete of ``ship`` does; ``session_edits`` and ``data_edits`` are byte ranges for ``filedb.splice``,
    ``binary_data`` the session's ``BinaryData`` attribute in ``data.a7s`` (its head, its end, its name id)."""

    ship: SShip
    references: tuple[SReference, ...]
    session_edits: tuple[tuple[int, int, bytes], ...] = field(repr=False)
    data_edits: tuple[tuple[int, int, bytes], ...] = field(repr=False)
    binary_data: tuple[int, int, int] = (0, 0, 0)

    @property
    def refused(self) -> tuple[SReference, ...]:
        return tuple(reference for reference in self.references if reference.action == REFUSED)


@dataclass
class _CDocument:
    """One document being planned: its bytes, its scan, its attributes by offset, and the ranges the delete removes
    and the edits it makes, gathered as the plan finds them."""

    m_label: str
    m_data: bytes
    m_root: filedb.CSpan
    m_starts: list[int] = field(default_factory=list)
    m_attributes: list[filedb.CSpan] = field(default_factory=list)
    m_removed: list[tuple[int, int]] = field(default_factory=list)
    m_edits: list[tuple[int, int, bytes]] = field(default_factory=list)
    m_references: list[SReference] = field(default_factory=list)

    def remove(self, node: filedb.CSpan, reason: str, last: filedb.CSpan | None = None) -> None:
        """Remove ``node`` (through ``last``: a map's key and its value) and say so."""
        stop = filedb.end_of(last or node)
        self.m_removed.append((node.m_header, stop))
        self.m_edits.append((node.m_header, stop, b""))
        self.m_references.append(SReference(f"{self.m_label}: {path_of(node)}", REMOVED, reason))

    def replace(self, node: filedb.CSpan, content: bytes) -> None:
        nid = struct.unpack_from("<I", self.m_data, node.m_header + 4)[0]
        self.m_edits.append((node.m_header, filedb.end_of(node), filedb.attribute_bytes(nid, content)))

    def is_removed(self, offset: int) -> bool:
        return any(start <= offset < stop for start, stop in self.m_removed)


def path_of(node: filedb.CSpan) -> str:
    """A node's place in its document, its names from the top down."""
    names = []
    while node is not None and node.m_parent is not None:
        names.append(node.m_name)
        node = node.m_parent
    return "/".join(reversed(names))


def _document(label: str, data: bytes) -> _CDocument:
    """A document scanned, with its attributes in offset order."""
    try:
        root = filedb.scan(data)
    except filedb.CFileDBError as error:
        raise CShipDeleteError(f"{label}: {error}") from None
    document = _CDocument(label, data, root)
    stack = list(reversed(root.m_children))
    while stack:
        node = stack.pop()
        if node.m_is_tag:
            stack += reversed(node.m_children)
        else:
            document.m_starts.append(node.m_start)
            document.m_attributes.append(node)
    return document


def _attribute_at(document: _CDocument, offset: int) -> filedb.CSpan | None:
    k = bisect.bisect_right(document.m_starts, offset) - 1
    node = document.m_attributes[k] if k >= 0 else None
    return node if node is not None and offset < node.m_start + node.m_size_bytes else None


def _pairs(tag: filedb.CSpan | None):
    """A map's (key attribute, value tag) pairs."""
    children = tag.m_children if tag is not None else []
    return [
        (key, value) for key, value in zip(children, children[1:], strict=False) if not key.m_is_tag and value.m_is_tag
    ]


def _session(data: _CDocument, session: int) -> filedb.CSpan:
    """The session's ``BinaryData`` attribute in ``data.a7s``."""
    for key, entry in _pairs(data.m_root.path("MetaGameManager/GameSessions")):
        if number(key, data.m_data, "<I") == session:
            found = entry.path("SessionData/BinaryData")
            if found is not None and not found.m_is_tag:
                return found
    raise CShipDeleteError(f"session {session} is not in the save")


def _own(session: _CDocument, ship: SShip) -> tuple[filedb.CSpan, int | None]:
    """The ship's object removed, with its unit and its group behaviour (a group of more is refused); returns the
    object and the id of the behaviour removed (None when none is)."""
    found = next((span for span in ship_spans(session.m_root)
                  if number(span.find("ID"), session.m_data, "<Q") == ship.object_id), None)  # fmt: skip
    if found is None:
        raise CShipDeleteError(f"ship {ship.object_id:#x} is not in session {ship.session}")
    session.remove(found, N_("saves.ship_removed_object"))
    units = session.m_root.path(UNITS)
    unit = next((u for u in (units.find("Units").m_children if units and units.find("Units") else [])
                 if number(u.find("ID"), session.m_data, "<Q") == ship.object_id), None)  # fmt: skip
    if unit is None:
        return found, None
    session.remove(unit, N_("saves.ship_removed_unit"))
    behaviour_id = number(unit.find("Behaviors"), session.m_data, "<Q")
    behaviours = units.path("GroupBehaviorHandler/Behaviors")
    for behaviour in behaviours.m_children if behaviours is not None else []:
        if number(behaviour.find("m_ID"), session.m_data, "<Q") != behaviour_id:
            continue
        active, pending = behaviour.find("ActiveMemberIDs"), behaviour.find("PendingMemberIDs")
        members = [number(m, session.m_data, "<Q") for m in (active.m_children if active else [])]
        if members == [ship.object_id] and not (pending and pending.m_children):
            session.remove(behaviour, N_("saves.ship_removed_behaviour"))
            return found, behaviour_id
    return found, None  # a behaviour of more members stays: the search refuses the ship's place in it (a group)


def _module_effects(found: filedb.CSpan, document: bytes) -> list[int]:
    """The ids of the effects of the ship's installed modules (``EffectId/value``; none for a module without one)."""
    modules = found.path("ShipModuleOwner/InstalledShipModules")
    effects = [number(m.path("EffectId/value"), document, "<Q", 0) for m in (modules.m_children if modules else [])]
    return [effect for effect in effects if effect]


def _items(session: _CDocument, ship: SShip) -> list[int]:
    """The items the ship holds, from the session's item map; one with a running effect or a quest is refused.
    Returns the ids of the items removed."""
    removed = []
    for key, value in _pairs(session.m_root.path(ITEM_MAP)):
        ident = number(key, session.m_data, "<Q")
        if ident not in ship.item_ids:
            continue
        quest = value.find("QuestID")
        if number(value.find("EffectID"), session.m_data, "<Q", 0) or (quest is not None and quest.m_children):
            session.m_references.append(SReference(f"{session.m_label}: {path_of(value)}", REFUSED,
                                                   N_("saves.ship_refused_item")))  # fmt: skip
        else:
            session.remove(key, N_("saves.ship_removed_item"), value)
            removed.append(ident)
    return removed


def _meta(data: _CDocument, ship: SShip) -> None:
    """The ship's module effects, its meta object and its place on its trade route, in ``data.a7s``."""
    for key, value in _pairs(data.m_root.path(LOCAL_EFFECTS)):
        if number(key, data.m_data, "<Q") == ship.object_id:
            data.remove(key, N_("saves.ship_removed_effects"), value)
    meta_objects = data.m_root.path(META_OBJECTS)
    for entry in meta_objects.m_children if meta_objects is not None else []:
        if entry.m_is_tag and number(entry.find("ID"), data.m_data, "<Q") == ship.meta_id:
            data.remove(entry, N_("saves.ship_removed_meta_object"))
    routes = data.m_root.path(ROUTES)
    word = struct.pack("<Q", ship.meta_id)
    for route in routes.m_children if routes is not None else []:
        ships = route.find("Ships") if route.m_is_tag else None
        content = ships.value(data.m_data) if ships is not None and not ships.m_is_tag else b""
        kept = [content[k : k + 8] for k in range(0, len(content) - len(content) % 8, 8) if content[k : k + 8] != word]
        if len(kept) * 8 < len(content):
            data.m_removed.append((ships.m_start, ships.m_start + ships.m_size_bytes))
            data.replace(ships, b"".join(kept))
            data.m_references.append(SReference(f"{data.m_label}: {path_of(ships)}", REMOVED,
                                                N_("saves.ship_removed_route_place")))  # fmt: skip


def _classify(document: _CDocument, node: filedb.CSpan, rules, default: str | None) -> None:
    """One reference outside what is removed: by the first rule its path matches (``default`` without one)."""
    place = path_of(node)
    action, reason = next(((a, r) for pattern, a, r in rules if pattern.search(place)), (REFUSED, default))
    if reason is None:
        return
    if action == CLEARED:
        document.replace(node, bytes(node.m_size_bytes))
    document.m_references.append(SReference(f"{document.m_label}: {place}", action, reason))


def _is_value_or_word(node: filedb.CSpan) -> bool:
    """A whole 8-byte value or a list of them; the storages' words are amounts, not references."""
    return node.m_size_bytes == 8 or (node.m_size_bytes % 8 == 0 and node.m_name not in STORAGES)


def _is_meta_field(node: filedb.CSpan) -> bool:
    return node.m_size_bytes == 8 or node.m_name == "Ships"


def _references(document: _CDocument, ident: int, rules, default: str | None, fits) -> None:
    """Every place outside the removed ranges whose aligned 8 bytes are ``ident``, in an attribute that ``fits``,
    classified by ``rules`` (``default`` without a match: None ignores it, for an id that is a small number)."""
    needle = struct.pack("<Q", ident)
    offset = document.m_data.find(needle)
    while offset >= 0:
        node = _attribute_at(document, offset)
        if (node is not None and node.m_name not in NESTED and not document.is_removed(offset)
                and (offset - node.m_start) % 8 == 0 and fits(node)):  # fmt: skip
            _classify(document, node, rules, default)
        offset = document.m_data.find(needle, offset + 1)


def plan(save: SSaveFile, ship: SShip) -> SPlan:
    """What deleting ``ship`` removes, sets to none, leaves and refuses (a few seconds: off the window's thread)."""
    part = save.part("data.a7s")
    if part is None:
        raise CShipDeleteError(f"{save.path.name}: no data.a7s")
    data = _document("data.a7s", part.m_doc)
    binary_data = _session(data, ship.session)
    session = _document(f"session {ship.session}", binary_data.value(data.m_data))
    found, behaviour_id = _own(session, ship)
    ship = read_ship(found, session.m_data, ship.session)  # the save's own state, not the caller's copy
    if ship.unit_meta_ids:
        session.m_references.append(SReference(f"{session.m_label}: {path_of(found)}", REFUSED,
                                               N_("saves.ship_refused_units")))  # fmt: skip
    if ship.is_quest_object:
        session.m_references.append(SReference(f"{session.m_label}: {path_of(found)}/QuestObject", REFUSED,
                                               N_("saves.ship_refused_quest")))  # fmt: skip
    removed_ids = _items(session, ship) + _module_effects(found, session.m_data)
    _meta(data, ship)
    for document in (session, data):
        _references(document, ship.object_id, OBJECT_RULES, UNKNOWN, _is_value_or_word)
        _references(document, ship.meta_id, META_RULES, None, _is_meta_field)
        for ident in removed_ids:  # an item's or a module effect's id left elsewhere would dangle
            _references(document, ident, (), UNKNOWN, _is_value_or_word)
    if behaviour_id is not None:
        _references(data, behaviour_id, GROUP_RULES, None, lambda node: node.m_size_bytes == 8)
    nid = struct.unpack_from("<I", data.m_data, binary_data.m_header + 4)[0]
    return SPlan(ship, tuple(session.m_references + data.m_references), tuple(session.m_edits), tuple(data.m_edits),
                 (binary_data.m_header, filedb.end_of(binary_data), nid))  # fmt: skip


def delete(save: SSaveFile, planned: SPlan) -> savegame117.SSave:
    """The save without the ship, as ``plan`` said; refused when the plan has a reference it cannot remove. Both
    changed documents are scanned again and the ship checked to be gone."""
    if planned.refused:
        raise CShipDeleteError("; ".join(f"{ref.place} ({ref.reason})" for ref in planned.refused))
    data = save.part("data.a7s").m_doc
    start, stop, nid = planned.binary_data
    old_session = data[start + 8 : start + 8 + struct.unpack_from("<i", data, start)[0]]
    new_session = filedb.splice(old_session, list(planned.session_edits))
    if any(number(span.find("ID"), new_session, "<Q") == planned.ship.object_id
           for span in ship_spans(filedb.scan(new_session))):  # fmt: skip
        raise CShipDeleteError(f"ship {planned.ship.object_id:#x} is still in its session after the delete")
    replaced = (start, stop, filedb.attribute_bytes(nid, new_session))
    new_data = filedb.splice(data, [*planned.data_edits, replaced])
    meta_objects = filedb.scan(new_data).path(META_OBJECTS)
    if any(number(entry.find("ID"), new_data, "<Q") == planned.ship.meta_id
           for entry in (meta_objects.m_children if meta_objects is not None else [])):  # fmt: skip
        raise CShipDeleteError(f"the ship's meta object {planned.ship.meta_id} is still in data.a7s")
    return edits.save117(save).with_docs({"data.a7s": new_data})


HEADINGS = (
    (REFUSED, N_("saves.ship_heading_refused")),
    (REMOVED, N_("saves.ship_heading_removed")),
    (CLEARED, N_("saves.ship_heading_cleared")),
    (KEPT, N_("saves.ship_heading_kept")),
)


def describe(planned: SPlan) -> str:
    """The plan as the question before a delete shows it: per action its places, each with what or why."""
    out = []
    for action, heading in HEADINGS:
        found = [reference for reference in planned.references if reference.action == action]
        if found:
            out.append(tr(heading, n=len(found)))
            out += [f"- {tr(reference.reason)}: {reference.place}" for reference in found]
    return "\n".join(out)


def write_beside(save: SSaveFile, planned: SPlan) -> Path:
    """Make the planned delete and write the result as ``<name> (edited).a8s`` next to the save; returns its path."""
    return edits.write_new(save, lambda: delete(save, planned))
