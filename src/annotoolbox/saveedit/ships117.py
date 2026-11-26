"""The ships of an Anno 117 save, per session: name, type, owner, position, health, modules, cargo, items and trade
route, read from each session's own document (``docs/formats/save-format.md``, "Ships"). Start at ``read``; ``rows``
gives the Ships tab's table, ``shipdelete117`` removes one ship.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

from ..formats import filedb
from ..formats.savegame117 import HUMAN_PARTICIPANT  # an object without ``OwnerProfile`` is the human's
from ..infra.i18n import N_, tr
from . import summary117
from .container import SSaveFile

SHIP_TAG = "ShipModuleOwner"  # the tag that makes an object a ship (save-format "The ship's object")
OBJECTS = "AreaObjectManager/GameObject/objects"
GOODS, ITEMS, UNIT = 1, 2, 3  # a cargo slot's ``Stack/index``
HEADERS = (
    N_("islands.session"), N_("saves.ship_name"), N_("saves.ship_type"), N_("saves.owner_profile"),
    N_("saves.ship_position"), N_("saves.ship_health"), N_("saves.ship_modules"), N_("saves.ship_cargo"),
    N_("saves.ship_items"), N_("saves.ship_trade_route"),
)  # fmt: skip


class CShipsError(ValueError):
    """A session document whose ships do not read."""


@dataclass(frozen=True)
class SShip:
    """One ship as its session holds it. ``x_m``/``z_m`` are world metres; ``goods`` are (product, amount) pairs."""

    session: int
    object_id: int  # ObjectID: object index in the low word, area id in the high word
    meta_id: int  # MetaID: the ship's meta object in data.a7s
    guid: int
    owner_profile: int
    vehicle_text_guid: int  # the text id of its name (an i64)
    vehicle_text_iterator: int
    x_m: float
    z_m: float
    current_hit_points: int | None  # None: undamaged (the game stores none)
    ship_modules: tuple[int, ...]
    goods: tuple[tuple[int, int], ...]
    slot_count: int
    slots_used: int
    items: tuple[int, ...]  # the item GUIDs in its sockets and cargo slots
    item_ids: tuple[int, ...]  # their ItemIDs (the session's ItemMap keys)
    unit_meta_ids: tuple[int, ...]  # the land units it carries, by MetaID
    trade_route_id: int | None
    is_quest_object: bool


def number(node: filedb.CSpan | None, document: bytes, struct_format: str, default=None):
    """An attribute's value by its ``struct`` format, ``default`` when it is missing or of another size."""
    if node is None or node.m_is_tag or node.m_size_bytes != struct.calcsize(struct_format):
        return default
    return struct.unpack(struct_format, node.value(document))[0]


def sea_area(root: filedb.CSpan) -> filedb.CSpan | None:
    """The session's first area manager, the sea area that holds its ships."""
    managers = root.path("GameSessionManager/AreaManagers")
    return managers.m_children[0] if managers is not None and managers.m_children else None


def ship_spans(root: filedb.CSpan) -> list[filedb.CSpan]:
    """The objects of the sea area that are ships, in document order."""
    area = sea_area(root)
    objects = area.path(OBJECTS) if area is not None else None
    return [o for o in (objects.m_children if objects is not None else []) if o.m_is_tag and o.find(SHIP_TAG)]


def _cargo(ship: filedb.CSpan, document: bytes) -> tuple[list, int, list, list]:
    """The goods, the slots in use, the items (GUID, ItemID) and the carried units' MetaIDs of the cargo slots."""
    slots = ship.path("ItemContainer/SlotContainer/SlotList")
    goods, items, units, used = [], [], [], 0
    for slot in slots.m_children if slots is not None else []:
        kind, value = number(slot.path("Stack/index"), document, "<Q"), slot.path("Stack/value")
        if value is None:
            continue
        before = len(goods) + len(items) + len(units)
        if kind == GOODS:
            goods.append((number(value.find("ProductGUID"), document, "<I", 0),
                          number(value.find("ProductCount"), document, "<I", 0)))  # fmt: skip
        elif kind == ITEMS:
            stack = value.find("Stack")
            items += [(number(entry.find("GUID"), document, "<I", 0), number(entry.find("ID"), document, "<Q", 0))
                      for entry in (stack.m_children if stack is not None else [])]  # fmt: skip
        elif kind == UNIT and number(value.find("UnitMetaObjectRef"), document, "<Q", 0):
            units.append(number(value.find("UnitMetaObjectRef"), document, "<Q"))
        used += len(goods) + len(items) + len(units) > before
    sockets = ship.path("ItemContainer/SocketContainer/SocketItems")
    for socket in sockets.m_children if sockets is not None else []:
        ident = number(socket.path("ItemReference/ID"), document, "<Q", 0)
        if ident:
            guid = number(socket.path("ItemReference/GUID"), document, "<I") or number(
                socket.find("GUID"), document, "<I", 0
            )
            items.append((guid, ident))
    return goods, used, items, units


def read_ship(ship: filedb.CSpan, document: bytes, session: int) -> SShip:
    """One ship object of a session's document (``ship_spans``) as a record."""
    position = ship.find("Position")
    x_m, _y, z_m = (
        struct.unpack("<3f", position.value(document)) if position and position.m_size_bytes == 12 else (0, 0, 0)
    )
    modules = ship.path(f"{SHIP_TAG}/InstalledShipModules")
    goods, used, items, units = _cargo(ship, document)
    slots = ship.path("ItemContainer/SlotContainer/SlotList")
    quest = ship.find("QuestObject")
    return SShip(
        session=session,
        object_id=number(ship.find("ID"), document, "<Q", 0),
        meta_id=number(ship.path("MetaPersistent/MetaID"), document, "<Q", 0),
        guid=number(ship.find("guid"), document, "<I", 0),
        owner_profile=number(ship.find("OwnerProfile"), document, "<I", HUMAN_PARTICIPANT),
        vehicle_text_guid=number(ship.path("Nameable/VehicleTextGuid"), document, "<q", 0),
        vehicle_text_iterator=number(ship.path("Nameable/VehicleTextIterator"), document, "<I", 1),
        x_m=x_m,
        z_m=z_m,
        current_hit_points=number(ship.path("Health/CurrentHitPoints"), document, "<I"),
        ship_modules=tuple(
            number(m.find("ShipModuleGuid"), document, "<I", 0) for m in (modules.m_children if modules else [])
        ),
        goods=tuple(goods),
        slot_count=len(slots.m_children) if slots is not None else 0,
        slots_used=used,
        items=tuple(guid for guid, _ident in items),
        item_ids=tuple(ident for _guid, ident in items),
        unit_meta_ids=tuple(units),
        trade_route_id=number(ship.path("PropertyTradeRouteVehicle/TradeRouteID"), document, "<I"),
        is_quest_object=quest is not None and any(child.m_children for child in quest.m_children if child.m_is_tag),
    )


def session_documents(save: SSaveFile) -> list[tuple[int, bytes]]:
    """(session GUID, its own document) of every session of the save, in file order."""
    data_root = save.part("data.a7s").tree() if save.part("data.a7s") else None
    if not isinstance(data_root, filedb.STag):
        raise CShipsError(f"{save.path.name}: data.a7s does not read")
    out = []
    for guid, entry in summary117.session_entries(data_root):
        found = entry.path("SessionData/BinaryData")
        if isinstance(found, filedb.SAttrib) and filedb.version(found.data) == 3:
            out.append((guid, found.data))
    return out


def scan_session(document: bytes, guid: int) -> filedb.CSpan:
    try:
        return filedb.scan(document)
    except filedb.CFileDBError as error:
        raise CShipsError(f"session {guid}: {error}") from None


def read(save: SSaveFile, say: Callable[..., None] = lambda _text, **_values: None) -> list[SShip]:
    """Every ship of the save's sessions, session by session (a session takes a few seconds: run it off the
    window's thread). A ship that travels between sessions is in none and is not listed."""
    ships = []
    for guid, document in session_documents(save):
        say(N_("saves.reading_session_guid"), guid=guid)
        root = scan_session(document, guid)
        ships += [read_ship(span, document, guid) for span in ship_spans(root)]
    return ships


def route_names(save: SSaveFile) -> dict[int, str]:
    """Each trade route's ``ID`` and ``Name`` (``SessionTradeRouteManager/RouteMap``)."""
    data_root = save.part("data.a7s").tree() if save.part("data.a7s") else None
    found = (
        data_root.path("MetaGameManager/SessionTradeRouteManager/RouteMap")
        if isinstance(data_root, filedb.STag)
        else None
    )
    out = {}
    for route in found.children if found is not None else []:
        ident, name = (route.find("ID"), route.find("Name")) if isinstance(route, filedb.STag) else (None, None)
        if isinstance(ident, filedb.SAttrib) and len(ident.data) == 4:
            out[ident.u32()] = name.data.decode("utf-16-le", "replace") if isinstance(name, filedb.SAttrib) else ""
    return out


def _named(guid: int, names) -> str:
    found = names.asset(guid) if names is not None and guid else ""
    return f"{found} ({guid})" if found else str(guid)


def _goods_text(goods: tuple[tuple[int, int], ...], names) -> str:
    return ", ".join(f"{names.asset(guid) if names is not None and names.asset(guid) else guid} {amount:,}"
                     for guid, amount in goods)  # fmt: skip


def ship_name(ship: SShip, names=None) -> str:
    """The ship's name from the game's texts (its text id without them), its number behind it when above 1."""
    text = (names.text(str(ship.vehicle_text_guid)) if names is not None else "") or str(ship.vehicle_text_guid)
    return f"{text} ({ship.vehicle_text_iterator})" if ship.vehicle_text_iterator > 1 else text


def row(ship: SShip, routes: dict[int, str], names=None) -> list[str]:
    """A ship as the Ships tab shows it (``HEADERS``); ``names`` is a ``names.CGameNames`` or None."""
    route = "" if ship.trade_route_id is None else routes.get(ship.trade_route_id, "") or str(ship.trade_route_id)
    health = (
        tr("saves.ship_undamaged")
        if ship.current_hit_points is None
        else tr("saves.ship_hit_points", n=ship.current_hit_points)
    )
    cargo = tr("saves.ship_slots_used", used=ship.slots_used, n=ship.slot_count)
    return [
        _named(ship.session, names), ship_name(ship, names), _named(ship.guid, names), _named(ship.owner_profile, names),
        f"{ship.x_m:.0f}, {ship.z_m:.0f}", health,
        ", ".join(_named(guid, names) for guid in ship.ship_modules),
        f"{cargo}: {_goods_text(ship.goods, names)}" if ship.goods else cargo,
        ", ".join(_named(guid, names) for guid in ship.items), route,
    ]  # fmt: skip


def rows(ships: list[SShip], routes: dict[int, str], names=None) -> list[list[str]]:
    return [row(ship, routes, names) for ship in ships]
