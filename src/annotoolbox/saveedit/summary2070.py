"""The summary of an Anno 2070 save: the game setup, the participants with their credits, the islands with the population
of each participant, the load list's numbers (``docs/save-editor.md``, "Anno 2070 in detail").
"""

from __future__ import annotations

import struct

from ..formats import bintree, savegame2070
from ..infra.i18n import N_
from .container import SSaveFile
from .records import CSummary, SSection, STable, duration, guid_ref, number, utf16

SETUP = ("Seed", "RandomMap", "GameType", "WorldSize", "IslandSize", "Fertilities", "Resources", "StartCredit",
         "DifficultyPoints", "EnableTraderQuests", "DiscoveredMap", "StartWithWarehouse", "QuestFrequency")  # fmt: skip
RESIDENTS = "m_ResidentCountPerLevel"  # eight int32, one per player, per faction and level
PARTICIPANT_COUNT = 8


def _text(node, path: str) -> str:
    found = node.path(path) if node is not None else None
    if found is None:
        return ""
    if found.text is not None:
        return found.text
    return utf16(found.data) if found.data else ""


def _load_list(summary: CSummary, stats, engine) -> None:
    section_node = summary.section(N_("common.save"))
    section_node.rows += [
        (N_("common.game"), "Anno 2070"),
        (N_("saves.game_version"), _text(stats, "GameStats/Version")),
        (N_("saves.play_time"), duration(number(_text(stats, "GameStats/PlayingTimeMilliSec")))),
        (N_("saves.money_load_list"), _text(stats, "GameStats/GoldAmount")),
        (N_("saves.population_load_list"), _text(stats, "GameStats/Inhabitants")),
        (N_("saves.save_kind"), _text(stats, "GameStats/SaveGameType")),
        (N_("saves.game_speed"), engine.value("m_GameSpeed", "") if engine is not None else ""),
    ]


def _profile(faction) -> object:
    """The player's profile GUID as a cell that can be named, "" for none."""
    guid = number(faction.value("Profile"), 0)
    return guid_ref(guid) if guid else ""


def _participants(summary: CSummary, parameters, tree) -> None:
    credits = {}
    for participant in savegame2070.participants(tree):
        node = participant.path("m_Resource/Credits")
        if node is not None and node.text is not None:
            credits[number(participant.value("m_PlayerID"), -1)] = number(node.text) // savegame2070.CREDIT_UNIT
    table = STable([N_("saves.player"), N_("ui.kind"), N_("common.name"), N_("mods.profile"), N_("common.difficulty"), N_("saves.team"), N_("common.colour"),
                   N_("saves.credits")])  # fmt: skip
    factions = parameters.find("FactionInfos") if parameters is not None else None
    for i in factions.children if factions is not None else []:
        participant_id = number(i.value("PlayerId"), -1)
        colour = number(i.value("Color"))
        table.rows.append([
            str(participant_id), _text(i, "Type/PlayerType"), _text(i, "Name"), _profile(i),
            _text(i, "Difficulty/Difficulty"), i.value("Team", ""), f"#{colour & 0xFFFFFFFF:08X}",
            f"{credits[participant_id]:,}" if participant_id in credits else "",
        ])  # fmt: skip
    summary.m_sections.append(SSection(N_("saves.players"), table=table))


def _setup(summary: CSummary, parameters) -> None:
    section_node = summary.section(N_("mods.game_setup"))
    for name in SETUP:
        node = parameters.find(name) if parameters is not None else None
        if node is not None and node.text is not None:
            section_node.named.append((name, node.text))


def _population(island) -> list[int]:
    """Per player, the residents of every faction and level of the island."""
    out = [0] * PARTICIPANT_COUNT
    levels = island.find("ResidentLevels")
    for node in levels.children if levels is not None else []:
        if node.name.startswith(RESIDENTS) and node.data and len(node.data) == 4 * PARTICIPANT_COUNT:
            for i, count in enumerate(struct.unpack(f"<{PARTICIPANT_COUNT}i", node.data)):
                out[i] += count
    return out


def _islands(summary: CSummary, world) -> None:
    table = STable(
        [
            N_("islands.island"),
            N_("common.name"),
            N_("common.file"),
            N_("saves.position"),
            N_("saves.size"),
            N_("saves.residents_per_player"),
        ]
    )
    islands = world.find("Islands") if world is not None else None
    children = islands.children if islands is not None else []
    for value in (child for child in children if child.name == "v"):
        cfg = value.find("IslandConfig")
        if cfg is None or not cfg.value("Filename"):
            continue  # the open sea
        residents = _population(value)
        people = ", ".join(f"{i}: {count:,}" for i, count in enumerate(residents) if count)
        table.rows.append([
            cfg.value("IslandID", ""), cfg.value("Name", ""), cfg.value("Filename", ""),
            f"{cfg.value('PositionX', '')}, {cfg.value('PositionZ', '')}", f"{cfg.value('Width', '')} x {cfg.value('Height', '')}",
            people or "-",
        ])  # fmt: skip
    section_node = SSection(N_("common.islands"), table=table)
    if world is not None:
        section_node.rows.append((N_("saves.world_size"), f"{world.value('Width', '')} x {world.value('Height', '')}"))
    summary.m_sections.append(section_node)


def summarize(save: SSaveFile) -> CSummary:
    summary = CSummary(save.game)
    stats = save.part("stats.xml").tree() if save.part("stats.xml") else None
    game = save.part("game2.xml").tree() if save.part("game2.xml") else None
    tree = save.part("save.sww").tree()
    if not isinstance(tree, bintree.STree):
        summary.note(N_("saves.save_sww_no_binary_tag"))
        return summary
    engine = tree.path("SaveGame/m_GameEngine")
    _load_list(summary, stats, engine)
    parameters = (
        game.nodes[0] if isinstance(game, bintree.STree) and game.nodes else None
    )  # the game's setup comes first
    _participants(summary, parameters, tree)
    _setup(summary, parameters)
    _islands(summary, tree.path("SaveGame/WorldConfig"))
    return summary
