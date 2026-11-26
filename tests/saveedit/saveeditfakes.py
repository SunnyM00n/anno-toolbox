"""Stand-ins for the save editor: saves of 117 and 2070, the typed values and tables a save holds."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from annotoolbox.formats import bintree, filedb, rda, savegame117, savegame2070
from annotoolbox.formats.isd2070 import SNode
from annotoolbox.infra.i18n import tr


def _attr(name: str, data: bytes) -> filedb.SAttrib:
    return filedb.SAttrib(name, data)


STOCKS117 = (  # the first area's stock: goods 2068 and 2071 with 186 and 5; the second's: 2173 and 2174 with 20 and 25
    ("StrgLrg", struct.pack("<4I", 2068, 2, 186, 5)),
    ("StrgSmll", bytes.fromhex("7d080000001419")),
)


def _session117(guid: int, areas: int) -> filedb.STag:
    """A ``GameSessions`` record: its description and its own document (a map template and island areas)."""
    infos = []
    for n in range(areas):
        trends = filedb.STag("StorageTrends", [_attr("#32768", struct.pack("<I", 2135 + n)), filedb.STag("#1", [])])
        infos += [_attr("#32768", struct.pack("<H", 0x2041 + 0x80 * n)), filedb.STag("#1", [
            _attr("CityNameGuid", bytes(range(8))), _attr("OwnerProfile", struct.pack("<I", 32777 + n)),
            filedb.STag("AreaEconomy", [trends, _attr(*(STOCKS117[n % 2]))]),
        ])]  # fmt: skip
    manager = filedb.STag("GameSessionManager", [
        filedb.STag("MapTemplate", [_attr("Filename", "data/maps/test.a7tinfo".encode("utf-16-le"))]),
        filedb.STag("AreaInfo", infos), filedb.STag("SessionEffectManager", [_attr("NextEffectIndex", bytes(8))]),
    ])  # fmt: skip
    doc = filedb.write(filedb.STag("", [_attr("SessionFileVersion", struct.pack("<I", 80)), manager]))
    desc = filedb.STag("SessionDesc", [_attr("SessionGUID", struct.pack("<I", guid)),
                                      filedb.STag("MapGeneratorSettings", [_attr("RandomSeed", struct.pack("<I", 7))])])  # fmt: skip
    return filedb.STag("#1", [desc, filedb.STag("SessionData", [_attr("BinaryData", doc)])])


def _setup117(root: str, profile: str, mods: dict[str, str]) -> bytes:
    slot = filedb.STag("Slot", [
        filedb.STag("ID", [_attr("id", struct.pack("<H", 0))]), _attr("CompanyName", profile.encode("utf-8")),
        _attr("ColorGuid", struct.pack("<I", 91304)), _attr("LogoGuid", struct.pack("<I", 92)),
        _attr("PortraitGuid", struct.pack("<I", 33070)),
    ])  # fmt: skip
    listed = [filedb.STag("#1", [_attr("ModID", m.encode()), _attr("ModVersion", v.encode())]) for m, v in mods.items()]
    top = filedb.STag(root, [
        _attr("SavegameProfileDisplayName", profile.encode("utf-16-le")), _attr("GameSeed", struct.pack("<I", 4695)),
        filedb.STag("SetupTaxes", [filedb.STag("value", [_attr("id", struct.pack("<H", 1))])]),
        filedb.STag("ModActivationInfo", [filedb.STag("Mods", listed), filedb.STag("Options", [])]),
        filedb.STag("SetupSlots", [slot]), _attr("TotalMoney", struct.pack("<5i", 1500, -1, -1, -1, -1)),
    ])  # fmt: skip
    return filedb.write(filedb.STag("", [top]))


def _data117(money: dict[int, int], sessions: int) -> bytes:
    def custom(tag: str, value: bytes) -> filedb.STag:
        return filedb.STag(tag, [_attr("#32768", b"\x01"), _attr("#32768", value)])

    human = filedb.STag("#1", [_attr("guid", struct.pack("<I", 41)), filedb.STag("MetaParticipant", [
        custom("CustomCompanyName", "Tester".encode("utf-16-le")), custom("CustomCompanyColorGuids", struct.pack("<I", 91304)),
        custom("CustomCompanyLogo", struct.pack("<I", 92)), custom("CustomAvatarPortrait", struct.pack("<I", 33070)),
    ])])  # fmt: skip
    pairs = [filedb.STag("Pair", [_attr("ParticipantGUID", struct.pack("<I", g)),
                                 filedb.STag("MetaEconomy", [_attr("StrgLrg", struct.pack("<4I", 91372, 918644, 2, m & 0xFFFFFFFF))])])
             for g, m in money.items()]  # fmt: skip
    games = []
    for n in range(sessions):
        games += [_attr("#32768", struct.pack("<I", 3245 + n)), _session117(3245 + n, 2)]
    manager = filedb.STag("MetaGameManager", [
        filedb.STag("MetaGameObjectManager", [filedb.STag("MetaObjectMap", [human])]),
        filedb.STag("EconomyManager", pairs), filedb.STag("GameSessions", games),
    ])  # fmt: skip
    return filedb.write(filedb.STag("", [manager]))


def fake_save117(path: Path, mods: dict[str, str] | None = None, money: dict[int, int] | None = None,
                 sessions: int = 1, profile: str = "Tester") -> Path:  # fmt: skip
    """A synthetic 117 savegame at ``path``: the four parts with the game's 12-byte tail, the player in all three
    places, ``mods`` (ModID -> version), each participant's ``money``, ``sessions`` with two island areas each."""
    meta = filedb.write(filedb.STag("", [
        _attr("CorporationFileVersion", struct.pack("<I", 80)), _attr("CorporationVersionName", "Anno 117 - Pax Romana".encode("utf-16-le")),
        _attr("LastModTime", struct.pack("<Q", 1_700_000_000)), _attr("CorporationSaveGameName", "Autosave ".encode("utf-16-le")),
    ]))  # fmt: skip
    docs = {"meta.a7s": meta, "header.a7s": _setup117("CorporationProfile", profile, mods or {}),
            "gamesetup.a7s": _setup117("GameSetupManager", profile, mods or {}),
            "data.a7s": _data117(money or {41: 1500, 31102: 9000}, sessions)}  # fmt: skip
    tail = zlib.compress(b"", 9)
    parts = {n: zlib.compress(d, 9) + tail + struct.pack("<I", len(d)) for n, d in docs.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rda.write_v22_stored(parts))
    return path


def _bin(name: str, text: str | None = None, *children: SNode, data: bytes | None = None) -> SNode:
    return SNode(name, text, data, list(children))


def fake_save2070(path: Path, credits: int = 80000) -> Path:
    """A synthetic 2070 savegame at ``path``: the V2.0 archive, ``ZZZZ`` packed binary tag trees, player 0 the human
    with ``credits`` (stored times 1,024, plus a fraction), one island, 24 bytes in the preamble as the game writes."""
    faction = _bin("i", None, _bin("PlayerId", "0"), _bin("Type", None, _bin("PlayerType", "Human")),
                   _bin("Name", data="Tester".encode("utf-16-le")), _bin("Color", "-7410177"))  # fmt: skip
    game = bintree.STree([_bin("Parameters", None, _bin("FactionInfos", None, faction), _bin("Seed", "5"))])
    player = _bin("i", None, _bin("hasValue", "1"), _bin("m_PlayerID", "0"),
                  _bin("m_Resource", None, _bin("Credits", str(credits * 1024 + 6))))  # fmt: skip
    island = _bin("v", None, _bin("IslandConfig", None, _bin("IslandID", "1"), _bin("Name", "N_L13"),
                                  _bin("Filename", "data/levels/islands/normal/n_l13.isd")),
                  _bin("ResidentLevels", None, _bin("m_ResidentCountPerLevelEcos1", data=struct.pack("<8i", 12, *[0] * 7))))  # fmt: skip
    world = _bin("WorldConfig", None, _bin("Width", "1792"), _bin("Height", "1792"),
                 _bin("Islands", None, _bin("k", "1"), island))  # fmt: skip
    engine = _bin("m_GameEngine", None, _bin("m_CurPlayerID", "0"), _bin("m_Players", None, player))
    save = bintree.STree([_bin("SaveGame", None, world, engine)])
    stats = bintree.STree([_bin("GameStats", None, _bin("GoldAmount", str(credits)), _bin("SyncCheckSum", "649"))])
    parts = {"game2.xml": game, "save.sww": save, "stats.xml": stats}
    raw = {n: savegame2070.pack(bintree.write(t)) for n, t in parts.items()}
    raw["preview.jpg"] = b"\xff\xd8\xff\xe0" + bytes(32)
    data = bytearray(rda.write_v20(raw, 0, flags=0))
    data[520:544] = struct.pack("<I", 20) + bytes(range(20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path


CALIDUS, JULIA, LUCIUS = 31637, 31722, 31721


BOUNDS = ((-100, -100, -65, -65), (-64, -50, -30, -30), (-29, -15, 25, 35), (36, 36, 70, 84), (85, 85, 100, 100))


def A(name: str, data: bytes) -> filedb.SAttrib:
    return filedb.SAttrib(name, data)


def T(name: str, *children) -> filedb.STag:
    return filedb.STag(name, list(children))


def u32(v: int) -> bytes:
    return struct.pack("<I", v)


def enum(name: str, value: int | None) -> filedb.STag:
    return T(name) if value is None else T(name, A("id", struct.pack("<H", value)))


def human() -> filedb.STag:
    def custom(tag: str, value: bytes) -> filedb.STag:
        return T(tag, A("#32768", b"\x01"), A("#32768", value))

    return T("#1", A("guid", u32(41)), T("MetaParticipant",
             custom("CustomCompanyName", "Tester".encode("utf-16-le")), custom("CustomCompanyColorGuids", u32(91304)),
             custom("CustomCompanyLogo", u32(92)), custom("CustomAvatarPortrait", u32(33070))))  # fmt: skip


def save_with(tmp_path: Path, doc: bytes, name: str = "a.a8s") -> Path:
    """A synthetic save whose ``data.a7s`` is ``doc`` (the other parts from ``fake_save117``)."""
    path = fake_save117(tmp_path / "P" / name)
    savegame117.write(savegame117.read(path).with_docs({"data.a7s": doc}), path)
    return path


def rows(section) -> dict[str, str]:
    return {tr(label): value for label, value in section.rows}


def section(s, title: str):
    return next(x for x in s.m_sections if tr(x.title) == title)


SHIP_A, SHIP_B, SHIP_C, LAND_UNIT = (1 << 32) | 24, (1 << 32) | 25, (1 << 32) | 30, (1 << 32) | 31  # ObjectIDs, area 1
META_A, META_B, META_C = 26, 27, 28
ITEM_A, ROUTE = 0x0400000000000010, 67
NAME_A = -6916873005774511353  # a VehicleTextGuid as an i64


def u64(v: int) -> bytes:
    return struct.pack("<Q", v)


def _slot(index: int, *value) -> filedb.STag:
    return T("#1", T("Stack", A("index", u64(index)), T("value", *value)))


def _ship(
    ident: int, meta: int, *extra, owner: int | None = None, iterator: int = 1, hit_points: int = 0
) -> filedb.STag:
    """A ship object: its asset, ID, position, owner (none: the human), name, health, meta id, empty quest."""
    head = [A("guid", u32(37222)), A("ID", u64(ident)), A("Position", struct.pack("<3f", 687.8, 0.0, 641.7))]
    if owner is not None:
        head.append(A("OwnerProfile", u32(owner)))
    health = [A("CurrentHitPoints", u32(hit_points))] if hit_points else []
    return T("#1", *head, T("QuestObject", T("QuestIDs"), T("Creator")),
             T("Nameable", A("VehicleTextGuid", struct.pack("<q", NAME_A)), A("VehicleTextIterator", u32(iterator))),
             T("MetaPersistent", A("MetaID", u64(meta))),
             T("Health", *health, T("ParentObject", A("ObjectID", u64(ident)))), *extra)  # fmt: skip


def _ships() -> list[filedb.STag]:
    """A: the human's, two modules, goods, an empty unit slot, an item, on route 67. B: a rival's, damaged. C: in a
    group with a land unit."""
    modules = T("ShipModuleOwner", T("InstalledShipModules",
                T("#1", A("ShipModuleGuid", u32(31647)), T("EffectId", A("value", u64(781 | 1 << 63)))),
                T("#1", A("ShipModuleGuid", u32(31648)), T("EffectId"))))  # fmt: skip
    cargo = T("ItemContainer", T("SlotContainer", T("SlotList",
              _slot(1, A("ProductGUID", u32(2084)), A("ProductCount", u32(16))), _slot(3, A("UnitMetaObjectRef", u64(0))),
              _slot(2, T("Stack", T("#1", A("ID", u64(ITEM_A)), A("GUID", u32(92876))))))))  # fmt: skip
    route = T("PropertyTradeRouteVehicle", A("TradeRouteID", u32(ROUTE)),
              T("TradeDescription", A("TargetShipID", u64(SHIP_A))))  # fmt: skip
    return [
        _ship(SHIP_A, META_A, modules, cargo, route),
        _ship(SHIP_B, META_B, T("ShipModuleOwner", T("InstalledShipModules")), owner=31100, iterator=2, hit_points=566),
        _ship(SHIP_C, META_C, T("ShipModuleOwner", T("InstalledShipModules"))),
    ]


def _unit(ident: int, behaviour: int) -> filedb.STag:
    return T("#1", A("ID", u64(ident)), A("Behaviors", u64(behaviour)), A("TargetObjectID", u64(0)))


def _behaviour(ident: int, *members: int) -> filedb.STag:
    return T("#1", A("m_ID", u64(ident)), T("ActiveMemberIDs", *[A("#32768", u64(m)) for m in members]),
             T("PendingMemberIDs"))  # fmt: skip


def ship_session(*extra: filedb.STag) -> bytes:
    """A session document with three ships in its sea area (``_ships``), their units and behaviours, an island area
    whose harbour remembers A as its last attacker, A's item, B waiting at a harbour, and ``extra`` nodes."""
    units = T("UnitSystem",
              T("Units", _unit(SHIP_A, 500), _unit(SHIP_B, 501), _unit(SHIP_C, 502), _unit(LAND_UNIT, 502)),
              T("GroupBehaviorHandler", T("Behaviors", _behaviour(500, SHIP_A), _behaviour(501, SHIP_B),
                                          _behaviour(502, SHIP_C, LAND_UNIT))),
              T("AttackLocationHandler", T("UnitData", A("#32768", u64(SHIP_A)), T("#1"))))  # fmt: skip
    items = T("ItemSessionManager", T("ItemMap", A("#32768", u64(ITEM_A)),
              T("#1", A("ItemID", u64(ITEM_A)), A("GUID", u32(92876)), T("QuestID"), A("EffectID", u64(0)))))  # fmt: skip
    pier = T("LoadingPierManager", T("Harbors", T("#1", T("ShipReference", A("ObjectID", u64(SHIP_B))))))
    harbour = T("#1", A("guid", u32(3402)), A("ID", u64((0x2181 << 32) | 2)),
                T("Health", A("LastAttackerID", u64(SHIP_A))))  # fmt: skip
    areas = T("AreaManagers",
              T("AreaManager_1", T("AreaObjectManager", T("GameObject", T("objects", *_ships())))),
              T("AreaManager_8577", T("AreaObjectManager", T("GameObject", T("objects", harbour)))))  # fmt: skip
    manager = T("GameSessionManager", T("SessionUnitManager", units), pier, items, *extra, areas)
    return filedb.write(T("", A("SessionFileVersion", u32(80)), manager))


def ship_data(session: bytes, *extra: filedb.STag) -> bytes:
    """A ``data.a7s`` with the human, the ships' meta objects, A's module effect, route 67 (A and B), a message about
    A, one session holding ``session``, and ``extra`` nodes."""

    def meta(ident: int, owner: int) -> filedb.STag:
        return T("#1", A("guid", u32(100471)), A("ID", u64(ident)), A("ParticipantGUID", u32(owner)),
                 T("MetaGameObjectReference"))  # fmt: skip

    effect = T(
        "#1", T("#1", A("Guid", u32(31647)), A("Id", u64(781 | 1 << 63)), T("Source", A("ObjectID", u64(SHIP_A))))
    )
    route = T("#1", A("ID", u32(ROUTE)), A("Name", "Jul - Sep".encode("utf-16-le")), A("Ships", u64(META_A) + u64(META_B)),
              T("LostShips"), A("Owner", u32(41)))  # fmt: skip
    desc = T("SessionDesc", A("SessionGUID", u32(3245)))
    manager = T("MetaGameManager",
        T("MetaGameObjectManager", T("MetaObjectMap", human(), meta(META_A, 41), meta(META_B, 31100), meta(META_C, 41))),
        T("MetaEffectManager", T("LocalEffects", A("#32768", u64(SHIP_A)), effect)),
        T("SessionTradeRouteManager", T("RouteMap", A("#32768", u32(ROUTE)), route)),
        T("NotificationSystem", T("ArchiveArray", T("Archive", T("#1", A("ObjectID", u64(SHIP_A)))))), *extra,
        T("GameSessions", A("#32768", u32(3245)), T("#1", desc, T("SessionData", A("BinaryData", session)))),
    )  # fmt: skip
    return filedb.write(T("", manager))


def ships_save(tmp_path: Path, session: bytes | None = None, data: bytes | None = None) -> Path:
    """A synthetic 117 save with the three ships (``ship_session``, ``ship_data``) at ``tmp_path/P/s.a8s``."""
    return save_with(tmp_path, data or ship_data(session or ship_session()), "s.a8s")


def damaged_117(path: Path) -> Path:
    """A 117 save whose ``data.a7s`` unpacks to bytes that are no document (its other parts whole)."""
    whole = savegame117.read(fake_save117(path.with_name("whole.a8s")))
    junk = b"no document " * 8
    parts = {**whole.stored, "data.a7s": zlib.compress(junk, 9) + savegame117.EMPTY_ZLIB + struct.pack("<I", len(junk))}
    path.write_bytes(rda.write_v22_stored(parts))
    return path
