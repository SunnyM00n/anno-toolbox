"""The summary of an Anno 117 save: version, mods, DLCs, setup, players and money, population, quests, sessions; and
the islands of each session read from its own document (``islands``). What each value is and where it lives:
``docs/save-editor.md``, "Anno 117 in detail".
"""

from __future__ import annotations

import struct

from ..formats import filedb, storage117
from ..infra.i18n import N_
from .container import SaveFile
from .records import (
    ModEntry,
    Section,
    Summary,
    Table,
    duration,
    goods_ref,
    guid_ref,
    i32,
    option_ref,
    stamp,
    text_ref,
    u32,
    u64,
    utf8,
    utf16,
)

SESSION_STOP = "SessionEffectManager"  # the first node of a session after AreaInfo: the islands scan ends there


def _data(node, path: str) -> bytes | None:
    found = node.path(path) if node is not None else None
    return found.data if isinstance(found, filedb.Attrib) else None


def _value(node, path: str, read=u32) -> str:
    data = _data(node, path)
    return "" if data is None else str(read(data))


def _guid(node, path: str = "") -> object:
    """A GUID attribute as a cell that can be named (``names.apply``), "" when the save has none."""
    data = _data(node, path) if path else getattr(node, "data", None)
    return guid_ref(u32(data)) if data and len(data) >= 4 else ""


def _enum(tag) -> str:
    """A ``Setup...`` option: ``value/id`` (u16), "" when it has none."""
    data = _data(tag, "value/id")
    return str(struct.unpack_from("<H", data)[0]) if data and len(data) >= 2 else ""


def version_rows(s: Summary, meta, top) -> None:
    sec = s.section(N_("Save"))
    sec.rows += [
        (N_("Game"), _value(meta, "CorporationVersionName", utf16)),
        (N_("File version"), _value(meta, "CorporationFileVersion")),
        (N_("Save name"), _value(meta, "CorporationSaveGameName", utf16)),
        (N_("Profile"), _value(top, "SavegameProfileDisplayName", utf16)),
        (N_("Last changed"), stamp(u64(_data(meta, "LastModTime") or b""))),
        (N_("Started"), stamp(u64(_data(top, "CreationTimeStamp") or b""))),
        (N_("Saved"), stamp(u64(_data(top, "WallTimeOnSave") or b""))),
        (N_("Play time"), duration(u64(_data(top, "SavedActivePlaytime") or b""))),
        (N_("Money in the load list"), _value(top, "TotalMoney", i32)),
        (N_("Population in the load list"), _value(top, "TotalPopulation", i32)),
        (N_("Modded"), _value(top, "WasModded", lambda d: d[0] if d else 0) or "0"),
    ]
    history = top.find("SavegameHistory") if top is not None else None
    builds = [
        (u64(_data(h, "Timestamp") or b""), _value(h, "Changelist")) for h in (history.children if history else [])
    ]
    if builds:
        sec.rows.append(
            (N_("Game builds that saved it"), f"{len(builds)}, last {builds[-1][1]} ({stamp(builds[-1][0])})")
        )


def mods_rows(s: Summary, top) -> None:
    info = top.find("ModActivationInfo") if top is not None else None
    mods = info.find("Mods") if info is not None else None
    for entry in mods.children if mods is not None else []:
        if isinstance(entry, filedb.Tag):
            s.mods.append(ModEntry(_value(entry, "ModID", utf8), _value(entry, "ModVersion", utf8)))
    table = Table([N_("Mod"), N_("Version")], [[m.mod_id, m.version] for m in s.mods])
    sec = Section(N_("Mods"), table=table)
    options = info.find("Options") if info is not None else None
    for entry in options.children if options is not None else []:
        if isinstance(entry, filedb.Tag):
            sec.rows.append((N_("Option"), f"{_value(entry, 'Option', utf8)} = {_value(entry, 'Value', utf8)}"))
    s.sections.append(sec)
    dlcs = top.find("ActiveDLCs") if top is not None else None
    guids = [u32(a.data) for a in (dlcs.findall("DLC") if dlcs is not None else [])]
    s.section(N_("Save")).rows.append((N_("DLCs"), guid_ref(*guids) if guids else "-"))


def setup_rows(s: Summary, top) -> None:
    sec = s.section(N_("Game setup"))
    sec.rows += [
        (N_("Difficulty preset"), _guid(top, "DifficultyPreset")),
        (N_("Seed"), _value(top, "GameSeed")),
        (N_("Start province"), _guid(top, "DefaultStartProvince")),
    ]
    for child in top.children if top is not None else []:
        if isinstance(child, filedb.Tag) and child.name.startswith("Setup") and child.find("value") is not None:
            sec.named.append((child.name, option_ref(child.name, _enum(child))))


def money_by_participant(data_root) -> dict[int, int]:
    """Each participant's balance: the last i32 of its meta storage (save-format "Money"), in file order."""
    out = {}
    economy = data_root.path("MetaGameManager/EconomyManager")
    for pair in economy.findall("Pair") if economy is not None else []:
        storage = _data(pair, "MetaEconomy/StrgLrg")
        guid = _data(pair, "ParticipantGUID")
        if storage and guid and len(storage) >= 4 and len(storage) % 4 == 0:
            out[u32(guid)] = struct.unpack_from("<i", storage, len(storage) - 4)[0]
    return out


def _per_participant(node) -> dict[int, object]:
    """A ``Data`` map of the meta managers: a participant GUID, then its ``#1`` record."""
    out, key = {}, None
    for child in node.children if node is not None else []:
        if isinstance(child, filedb.Attrib):
            key = u32(child.data)
        elif key is not None:
            out[key] = child
            key = None
    return out


def _players(s: Summary, top, data_root) -> None:
    table = Table([N_("Slot"), N_("Company"), N_("Colour"), N_("Logo"), N_("Portrait"), N_("Rival profile")])
    slots = top.find("SetupSlots") if top is not None else None
    for slot in slots.findall("Slot") if slots is not None else []:
        table.rows.append([
            _value(slot, "ID/id", lambda d: struct.unpack_from("<H", d)[0] if len(d) >= 2 else 0),
            _value(slot, "CompanyName", utf8), _guid(slot, "ColorGuid"), _guid(slot, "LogoGuid"),
            _guid(slot, "PortraitGuid"), _guid(slot, "SecondPartyProfile"),
        ])  # fmt: skip
    s.sections.append(Section(N_("Players"), table=table))
    money = money_by_participant(data_root)
    population = _per_participant(data_root.path("MetaGameManager/PopulationManager/MaxReachedPopulationCount/Data"))
    people = Table([N_("Participant"), N_("Money"), N_("Highest population reached"), N_("Other goods held")])
    held = meta_goods(data_root)
    for guid in sorted(set(money) | set(population)):
        reached = _reached(population.get(guid))
        people.rows.append(
            [
                guid_ref(guid),
                f"{money[guid]:,}" if guid in money else "",
                "" if reached is None else f"{reached:,}",
                goods_ref(held.get(guid, [])),
            ]  # fmt: skip
        )
    s.sections.append(Section(N_("Participants"), table=people))


def meta_goods(data_root) -> dict[int, list[tuple[int, int]]]:
    """Per participant the goods of its meta storage other than the money (permits and the like)."""
    out = {}
    economy = data_root.path("MetaGameManager/EconomyManager")
    for pair in economy.findall("Pair") if economy is not None else []:
        storage = pair.path("MetaEconomy/StrgLrg")
        guid = _data(pair, "ParticipantGUID")
        if isinstance(storage, filedb.Attrib) and guid and len(guid) >= 4:
            try:
                goods = storage117.decode(storage.name, storage.data)
            except storage117.StorageError:
                continue
            out[u32(guid)] = [(g, n) for g, n in goods if g != storage117.MONEY_GOOD]
    return out


def _reached(record) -> int | None:
    """``MaxReachedPopulationCount``: pairs of a population level's GUID and a float count, summed."""
    if record is None:
        return None
    values = [a.data for a in record.children if isinstance(a, filedb.Attrib)]
    return round(sum(struct.unpack("<f", v)[0] for v in values[1::2] if len(v) == 4))


def _quests(s: Summary, data_root) -> None:
    quests = data_root.path("MetaGameManager/QuestManager")
    if quests is None:
        return
    table = Table([N_("Participant"), N_("Quest log entries"), N_("Quests started"), N_("Quests ended")])
    entries = _per_participant(quests.path("QuestEntryHandler/QuestEntries/Data"))
    lines = _per_participant(quests.path("StoryLines/Data"))
    for guid in sorted(set(entries) | set(lines)):
        story = lines.get(guid)
        created = story.find("CreatedComponents") if story is not None else None
        ended = story.find("EndedComponents") if story is not None else None
        log = entries.get(guid)
        table.rows.append([
            guid_ref(guid), str(len(log.children)) if log is not None else "",
            str(len(created.children)) if created is not None else "", str(len(ended.children)) if ended is not None else "",
        ])  # fmt: skip
    sec = Section(N_("Quests"), table=table)
    campaign = quests.find("CampaignHandler")
    if campaign is not None:
        progress = _data(campaign, "CampaignProgress")
        solved = campaign.find("SolvedComponentGUIDs")
        sec.rows += [
            (N_("Campaign chapter"), _value(campaign, "ActiveChapter/id", lambda d: struct.unpack_from("<H", d)[0])),
            (
                N_("Campaign progress"),
                f"{struct.unpack_from('<f', progress)[0]:.0%}" if progress and len(progress) == 4 else "",
            ),
            (N_("Campaign steps solved"), str(len(solved.children)) if solved is not None else ""),
        ]
    s.sections.append(sec)


def session_entries(data_root) -> list[tuple[int, object]]:
    """``GameSessions``: (session GUID, its ``#1`` record) in file order."""
    return list(_per_participant(data_root.path("MetaGameManager/GameSessions")).items())


def _sessions(s: Summary, data_root) -> None:
    table = Table([N_("Session"), N_("Map template type"), N_("Seed"), N_("Session document (bytes)")])
    for guid, entry in session_entries(data_root):
        blob = _data(entry, "SessionData/BinaryData") or b""
        desc = entry.find("SessionDesc")
        table.rows.append([guid_ref(guid), _guid(desc, "MapGeneratorSettings/MapTemplateType"),
                           _value(desc, "MapGeneratorSettings/RandomSeed"), f"{len(blob):,}"])  # fmt: skip
    s.sections.append(Section(N_("Sessions"), table=table))


def summarize(save: SaveFile) -> Summary:
    """The summary without the sessions' own documents (fast; ``islands`` reads those)."""
    s = Summary(save.game)
    meta = save.part("meta.a7s").tree() if save.part("meta.a7s") else None
    header = save.part("header.a7s").tree() if save.part("header.a7s") else None
    top = header.find("CorporationProfile") if header is not None else None
    data_root = save.part("data.a7s").tree()
    version_rows(s, meta, top)
    mods_rows(s, top)
    setup_rows(s, top)
    _players(s, top, data_root)
    _quests(s, data_root)
    _sessions(s, data_root)
    if top is None:
        s.note(N_("The save has no readable header: only the world state is summarised."))
    return s


# The islands of a session (its own document, scanned only as far as the islands)


def _island_spans(doc: bytes) -> filedb.Span | None:
    def done(attr: filedb.Span) -> bool:
        node = attr
        while node.parent is not None and node.parent.name != "GameSessionManager":
            node = node.parent
        return node.name == SESSION_STOP

    return filedb.scan(doc, stop=done).find("GameSessionManager")


def _city(doc: bytes, node: filedb.Span | None) -> object:
    """``CityNameGuid``: an 8-byte text id (the name's line in the game's texts)."""
    data = node.value(doc) if node is not None else b""
    return text_ref(str(struct.unpack("<q", data)[0])) if len(data) == 8 else ""


def _stock(doc: bytes, node: filedb.Span | None) -> object:
    """The goods of the ``StrgLrg`` or ``StrgSmll`` below ``node`` (its stock), "" when it has none or none reads."""
    for name in (storage117.LARGE, storage117.SMALL):
        found = node.find(name) if node is not None else None
        if found is not None:
            try:
                return goods_ref(storage117.decode(name, found.value(doc)))
            except storage117.StorageError:
                return ""
    return ""


def _area_rows(doc: bytes, manager: filedb.Span, session: int) -> list[list]:
    rows, key = [], ""
    info = manager.find("AreaInfo")
    for child in info.children if info is not None else []:
        if not child.is_tag:
            key = child.value(doc).hex()
            continue
        owner = child.find("OwnerProfile")
        trends = child.path("AreaEconomy/StorageTrends")
        goods = [c for c in trends.children if not c.is_tag] if trends is not None else []
        rows.append([
            guid_ref(session), key, guid_ref(u32(owner.value(doc))) if owner is not None else "",
            _city(doc, child.find("CityNameGuid")), str(len(goods)),
            _stock(doc, child.find("AreaEconomy")), _stock(doc, child.path("AreaEconomy/TradeStorage")),
        ])  # fmt: skip
    return rows


def islands(save: SaveFile, say=lambda _t, **_p: None) -> Section:
    """Per session: its map template and every island area with its owner and the goods its storage tracks."""
    table = Table(
        [
            N_("Session"),
            N_("Area"),
            N_("Owner profile"),
            N_("City name"),
            N_("Goods tracked"),
            N_("Stock"),
            N_("Trade storage"),
        ]
    )
    sec = Section(N_("Islands"), table=table)
    for guid, entry in session_entries(save.part("data.a7s").tree()):
        say(N_("reading session {guid}"), guid=guid)
        doc = _data(entry, "SessionData/BinaryData") or b""
        if filedb.version(doc) != 3:
            continue
        manager = _island_spans(doc)
        if manager is None:
            continue
        template = manager.path("MapTemplate/Filename")
        if template is not None:
            sec.rows.append((N_("Map template"), f"{guid}: {utf16(template.value(doc))}"))
        table.rows += _area_rows(doc, manager, guid)
    return sec
