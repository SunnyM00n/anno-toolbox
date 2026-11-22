"""The summary of an Anno 117 save: version, mods, DLCs, setup, participants and money, population, quests, sessions; and
the islands of each session read from its own document (``islands``). What each value is and where it lives:
``docs/save-editor.md``, "Anno 117 in detail".
"""

from __future__ import annotations

import struct

from ..formats import filedb, storage117
from ..infra.i18n import N_
from .container import SSaveFile
from .records import (
    CSummary,
    SModEntry,
    SSection,
    STable,
    duration,
    goods_ref,
    guid_ref,
    i32,
    option_ref,
    stamp,
    text_ref,
    u16,
    u32,
    u64,
    utf8,
    utf16,
)

SESSION_STOP = "SessionEffectManager"  # the first node of a session after AreaInfo: the islands scan ends there


def _data(node, path: str) -> bytes | None:
    found = node.path(path) if node is not None else None
    return found.data if isinstance(found, filedb.SAttrib) else None


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
    return str(u16(data)) if data and len(data) >= 2 else ""


def version_rows(summary: CSummary, meta, top) -> None:
    section_node = summary.section(N_("common.save"))
    section_node.rows += [
        (N_("common.game"), _value(meta, "CorporationVersionName", utf16)),
        (N_("saves.file_version"), _value(meta, "CorporationFileVersion")),
        (N_("saves.save_name"), _value(meta, "CorporationSaveGameName", utf16)),
        (N_("mods.profile"), _value(top, "SavegameProfileDisplayName", utf16)),
        (N_("saves.last_changed"), stamp(u64(_data(meta, "LastModTime") or b""))),
        (N_("saves.started"), stamp(u64(_data(top, "CreationTimeStamp") or b""))),
        (N_("islands.saved"), stamp(u64(_data(top, "WallTimeOnSave") or b""))),
        (N_("saves.play_time"), duration(u64(_data(top, "SavedActivePlaytime") or b""))),
        (N_("saves.money_load_list"), _value(top, "TotalMoney", i32)),
        (N_("saves.population_load_list"), _value(top, "TotalPopulation", i32)),
        (N_("saves.modded"), _value(top, "WasModded", lambda data: data[0] if data else 0) or "0"),
    ]
    history = top.find("SavegameHistory") if top is not None else None
    builds = [
        (u64(_data(entry, "Timestamp") or b""), _value(entry, "Changelist"))
        for entry in (history.children if history else [])
    ]
    if builds:
        section_node.rows.append(
            (N_("saves.game_builds_saved"), f"{len(builds)}, last {builds[-1][1]} ({stamp(builds[-1][0])})")
        )


def mods_rows(summary: CSummary, top) -> None:
    activation_info = top.find("ModActivationInfo") if top is not None else None
    mods = activation_info.find("Mods") if activation_info is not None else None
    for entry in mods.children if mods is not None else []:
        if isinstance(entry, filedb.STag):
            summary.m_mods.append(SModEntry(_value(entry, "ModID", utf8), _value(entry, "ModVersion", utf8)))
    table = STable([N_("mods.mod"), N_("common.version")], [[mod.mod_id, mod.version] for mod in summary.m_mods])
    section_node = SSection(N_("common.mods"), table=table)
    options = activation_info.find("Options") if activation_info is not None else None
    for entry in options.children if options is not None else []:
        if isinstance(entry, filedb.STag):
            section_node.rows.append(
                (N_("saves.option"), f"{_value(entry, 'Option', utf8)} = {_value(entry, 'Value', utf8)}")
            )
    summary.m_sections.append(section_node)
    dlcs = top.find("ActiveDLCs") if top is not None else None
    found = dlcs.findall("DLC") if dlcs is not None else []
    guids = [u32(attrib.data) for attrib in found if isinstance(attrib, filedb.SAttrib)]
    summary.section(N_("common.save")).rows.append((N_("saves.dlcs"), guid_ref(*guids) if guids else "-"))


def setup_rows(summary: CSummary, top) -> None:
    section_node = summary.section(N_("mods.game_setup"))
    section_node.rows += [
        (N_("saves.difficulty_preset"), _guid(top, "DifficultyPreset")),
        (N_("saves.seed"), _value(top, "GameSeed")),
        (N_("saves.start_province"), _guid(top, "DefaultStartProvince")),
    ]
    for child in top.children if top is not None else []:
        if isinstance(child, filedb.STag) and child.name.startswith("Setup") and child.find("value") is not None:
            section_node.named.append((child.name, option_ref(child.name, _enum(child))))


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
        if isinstance(child, filedb.SAttrib):
            key = u32(child.data)
        elif key is not None:
            out[key] = child
            key = None
    return out


def _participants(summary: CSummary, top, data_root) -> None:
    table = STable(
        [
            N_("saves.slot"),
            N_("saves.company"),
            N_("common.colour"),
            N_("saves.logo"),
            N_("saves.portrait"),
            N_("saves.rival_profile"),
        ]
    )
    slots = top.find("SetupSlots") if top is not None else None
    for slot in slots.findall("Slot") if slots is not None else []:
        table.rows.append([
            _value(slot, "ID/id", u16),
            _value(slot, "CompanyName", utf8), _guid(slot, "ColorGuid"), _guid(slot, "LogoGuid"),
            _guid(slot, "PortraitGuid"), _guid(slot, "SecondPartyProfile"),
        ])  # fmt: skip
    summary.m_sections.append(SSection(N_("saves.players"), table=table))
    money = money_by_participant(data_root)
    population = _per_participant(data_root.path("MetaGameManager/PopulationManager/MaxReachedPopulationCount/Data"))
    people = STable(
        [
            N_("saves.participant"),
            N_("saves.money"),
            N_("saves.highest_population_reached"),
            N_("saves.other_goods_held"),
        ]
    )
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
    summary.m_sections.append(SSection(N_("saves.participants"), table=people))


def meta_goods(data_root) -> dict[int, list[tuple[int, int]]]:
    """Per participant the goods of its meta storage other than the money (permits and the like)."""
    out = {}
    economy = data_root.path("MetaGameManager/EconomyManager")
    for pair in economy.findall("Pair") if economy is not None else []:
        storage = pair.path("MetaEconomy/StrgLrg")
        guid = _data(pair, "ParticipantGUID")
        if isinstance(storage, filedb.SAttrib) and guid and len(guid) >= 4:
            try:
                goods = storage117.decode(storage.name, storage.data)
            except storage117.CStorageError:
                continue
            out[u32(guid)] = [(good, amount) for good, amount in goods if good != storage117.MONEY_GOOD]
    return out


def _reached(record) -> int | None:
    """``MaxReachedPopulationCount``: pairs of a population level's GUID and a float count, summed."""
    if record is None:
        return None
    values = [attrib.data for attrib in record.children if isinstance(attrib, filedb.SAttrib)]
    return round(sum(struct.unpack("<f", value)[0] for value in values[1::2] if len(value) == 4))


def _quests(summary: CSummary, data_root) -> None:
    quests = data_root.path("MetaGameManager/QuestManager")
    if quests is None:
        return
    table = STable(
        [N_("saves.participant"), N_("saves.quest_log_entries"), N_("saves.quests_started"), N_("saves.quests_ended")]
    )
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
    section_node = SSection(N_("saves.quests"), table=table)
    campaign = quests.find("CampaignHandler")
    if campaign is not None:
        progress = _data(campaign, "CampaignProgress")
        solved = campaign.find("SolvedComponentGUIDs")
        section_node.rows += [
            (N_("saves.campaign_chapter"), _value(campaign, "ActiveChapter/id", u16)),
            (
                N_("saves.campaign_progress"),
                f"{struct.unpack_from('<f', progress)[0]:.0%}" if progress and len(progress) == 4 else "",
            ),
            (N_("saves.campaign_steps_solved"), str(len(solved.children)) if solved is not None else ""),
        ]
    summary.m_sections.append(section_node)


def session_entries(data_root) -> list[tuple[int, object]]:
    """``GameSessions``: (session GUID, its ``#1`` record) in file order."""
    return list(_per_participant(data_root.path("MetaGameManager/GameSessions")).items())


def _sessions(summary: CSummary, data_root) -> None:
    table = STable(
        [N_("islands.session"), N_("saves.map_template_type"), N_("saves.seed"), N_("saves.session_document_bytes")]
    )
    for guid, entry in session_entries(data_root):
        blob = _data(entry, "SessionData/BinaryData") or b""
        desc = entry.find("SessionDesc")
        table.rows.append([guid_ref(guid), _guid(desc, "MapGeneratorSettings/MapTemplateType"),
                           _value(desc, "MapGeneratorSettings/RandomSeed"), f"{len(blob):,}"])  # fmt: skip
    summary.m_sections.append(SSection(N_("saves.sessions"), table=table))


def summarize(save: SSaveFile) -> CSummary:
    """The summary without the sessions' own documents (fast; ``islands`` reads those)."""
    summary = CSummary(save.game)
    meta = save.part("meta.a7s").tree() if save.part("meta.a7s") else None
    header = save.part("header.a7s").tree() if save.part("header.a7s") else None
    top = header.find("CorporationProfile") if header is not None else None
    data_root = save.part("data.a7s").tree()
    if not isinstance(data_root, filedb.STag):  # a damaged part: the header's rows are still the save's
        summary.note(N_("saves.world_state_not_read"))
        data_root = filedb.STag("")
    version_rows(summary, meta, top)
    mods_rows(summary, top)
    setup_rows(summary, top)
    _participants(summary, top, data_root)
    _quests(summary, data_root)
    _sessions(summary, data_root)
    if top is None:
        summary.note(N_("saves.save_no_readable_header_only"))
    return summary


# The islands of a session (its own document, scanned only as far as the islands)


def _island_spans(document: bytes) -> filedb.CSpan | None:
    def done(span: filedb.CSpan) -> bool:
        node = span
        while node.m_parent is not None and node.m_parent.m_name != "GameSessionManager":
            node = node.m_parent
        return node.m_name == SESSION_STOP

    return filedb.scan(document, stop=done).find("GameSessionManager")


def _city(document: bytes, node: filedb.CSpan | None) -> object:
    """``CityNameGuid``: an 8-byte text id (the name's line in the game's texts)."""
    data = node.value(document) if node is not None else b""
    return text_ref(str(struct.unpack("<q", data)[0])) if len(data) == 8 else ""


def _stock(document: bytes, node: filedb.CSpan | None) -> object:
    """The goods of the ``StrgLrg`` or ``StrgSmll`` below ``node`` (its stock), "" when it has none or none reads."""
    for name in (storage117.LARGE, storage117.SMALL):
        found = node.find(name) if node is not None else None
        if found is not None:
            try:
                return goods_ref(storage117.decode(name, found.value(document)))
            except storage117.CStorageError:
                return ""
    return ""


def _area_rows(document: bytes, manager: filedb.CSpan, session: int) -> list[list]:
    rows, key = [], ""
    area_info = manager.find("AreaInfo")
    for child in area_info.m_children if area_info is not None else []:
        if not child.m_is_tag:
            key = child.value(document).hex()
            continue
        owner = child.find("OwnerProfile")
        trends = child.path("AreaEconomy/StorageTrends")
        goods = [good for good in trends.m_children if not good.m_is_tag] if trends is not None else []
        rows.append([
            guid_ref(session), key, guid_ref(u32(owner.value(document))) if owner is not None else "",
            _city(document, child.find("CityNameGuid")), str(len(goods)),
            _stock(document, child.find("AreaEconomy")), _stock(document, child.path("AreaEconomy/TradeStorage")),
        ])  # fmt: skip
    return rows


def islands(save: SSaveFile, say=lambda _text, **_values: None) -> SSection:
    """Per session: its map template and every island area with its owner and the goods its storage tracks."""
    table = STable(
        [
            N_("islands.session"),
            N_("saves.area"),
            N_("saves.owner_profile"),
            N_("saves.city_name"),
            N_("saves.goods_tracked"),
            N_("saves.stock"),
            N_("saves.trade_storage"),
        ]
    )
    section_node = SSection(N_("common.islands"), table=table)
    data_root = save.part("data.a7s").tree()
    for guid, entry in session_entries(data_root) if isinstance(data_root, filedb.STag) else []:
        say(N_("saves.reading_session_guid"), guid=guid)
        document = _data(entry, "SessionData/BinaryData") or b""
        if filedb.version(document) != 3:
            continue
        manager = _island_spans(document)
        if manager is None:
            continue
        template = manager.path("MapTemplate/Filename")
        if template is not None:
            section_node.rows.append((N_("saves.map_template"), f"{guid}: {utf16(template.value(document))}"))
        table.rows += _area_rows(document, manager, guid)
    return section_node
