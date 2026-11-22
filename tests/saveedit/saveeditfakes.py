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


def damaged_117(path: Path) -> Path:
    """A 117 save whose ``data.a7s`` unpacks to bytes that are no document (its other parts whole)."""
    whole = savegame117.read(fake_save117(path.with_name("whole.a8s")))
    junk = b"no document " * 8
    parts = {**whole.stored, "data.a7s": zlib.compress(junk, 9) + savegame117.EMPTY_ZLIB + struct.pack("<I", len(junk))}
    path.write_bytes(rda.write_v22_stored(parts))
    return path
