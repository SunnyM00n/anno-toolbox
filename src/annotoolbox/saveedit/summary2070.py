"""The summary of an Anno 2070 save: the game setup, the players with their credits, the islands with the population
of each player, the load list's numbers (``docs/save-editor.md``, "Anno 2070 in detail").
"""

from __future__ import annotations

import struct

from ..formats import bintree, savegame2070
from ..infra.i18n import N_
from .container import SaveFile
from .records import Section, Summary, Table, duration, guid_ref, number, utf16

SETUP = ("Seed", "RandomMap", "GameType", "WorldSize", "IslandSize", "Fertilities", "Resources", "StartCredit",
         "DifficultyPoints", "EnableTraderQuests", "DiscoveredMap", "StartWithWarehouse", "QuestFrequency")  # fmt: skip
RESIDENTS = "m_ResidentCountPerLevel"  # eight int32, one per player, per faction and level
PLAYERS = 8


def _text(node, path: str) -> str:
    found = node.path(path) if node is not None else None
    if found is None:
        return ""
    if found.text is not None:
        return found.text
    return utf16(found.data) if found.data else ""


def _load_list(s: Summary, stats, engine) -> None:
    sec = s.section(N_("Save"))
    sec.rows += [
        (N_("Game"), "Anno 2070"),
        (N_("Game version"), _text(stats, "GameStats/Version")),
        (N_("Play time"), duration(number(_text(stats, "GameStats/PlayingTimeMilliSec")))),
        (N_("Money in the load list"), _text(stats, "GameStats/GoldAmount")),
        (N_("Population in the load list"), _text(stats, "GameStats/Inhabitants")),
        (N_("Save kind"), _text(stats, "GameStats/SaveGameType")),
        (N_("Game speed"), engine.value("m_GameSpeed", "") if engine is not None else ""),
    ]


def _profile(faction) -> object:
    """The player's profile GUID as a cell that can be named, "" for none."""
    guid = number(faction.value("Profile"), 0)
    return guid_ref(guid) if guid else ""


def _players(s: Summary, params, tree) -> None:
    credits = {}
    for p in savegame2070.players(tree):
        node = p.path("m_Resource/Credits")
        if node is not None and node.text is not None:
            credits[number(p.value("m_PlayerID"), -1)] = number(node.text) // savegame2070.CREDIT_UNIT
    table = Table([N_("Player"), N_("Kind"), N_("Name"), N_("Profile"), N_("Difficulty"), N_("Team"), N_("Colour"),
                   N_("Credits")])  # fmt: skip
    factions = params.find("FactionInfos") if params is not None else None
    for i in factions.children if factions is not None else []:
        pid = number(i.value("PlayerId"), -1)
        colour = number(i.value("Color"))
        table.rows.append([
            str(pid), _text(i, "Type/PlayerType"), _text(i, "Name"), _profile(i),
            _text(i, "Difficulty/Difficulty"), i.value("Team", ""), f"#{colour & 0xFFFFFFFF:08X}",
            f"{credits[pid]:,}" if pid in credits else "",
        ])  # fmt: skip
    s.sections.append(Section(N_("Players"), table=table))


def _setup(s: Summary, params) -> None:
    sec = s.section(N_("Game setup"))
    for name in SETUP:
        node = params.find(name) if params is not None else None
        if node is not None and node.text is not None:
            sec.named.append((name, node.text))


def _population(island) -> list[int]:
    """Per player, the residents of every faction and level of the island."""
    out = [0] * PLAYERS
    levels = island.find("ResidentLevels")
    for node in levels.children if levels is not None else []:
        if node.name.startswith(RESIDENTS) and node.data and len(node.data) == 4 * PLAYERS:
            for i, n in enumerate(struct.unpack(f"<{PLAYERS}i", node.data)):
                out[i] += n
    return out


def _islands(s: Summary, world) -> None:
    table = Table([N_("Island"), N_("Name"), N_("File"), N_("Position"), N_("Size"), N_("Residents per player")])
    islands = world.find("Islands") if world is not None else None
    children = islands.children if islands is not None else []
    for value in (c for c in children if c.name == "v"):
        cfg = value.find("IslandConfig")
        if cfg is None or not cfg.value("Filename"):
            continue  # the open sea
        residents = _population(value)
        people = ", ".join(f"{i}: {n:,}" for i, n in enumerate(residents) if n)
        table.rows.append([
            cfg.value("IslandID", ""), cfg.value("Name", ""), cfg.value("Filename", ""),
            f"{cfg.value('PositionX', '')}, {cfg.value('PositionZ', '')}", f"{cfg.value('Width', '')} x {cfg.value('Height', '')}",
            people or "-",
        ])  # fmt: skip
    sec = Section(N_("Islands"), table=table)
    if world is not None:
        sec.rows.append((N_("World size"), f"{world.value('Width', '')} x {world.value('Height', '')}"))
    s.sections.append(sec)


def summarize(save: SaveFile) -> Summary:
    s = Summary(save.game)
    stats = save.part("stats.xml").tree() if save.part("stats.xml") else None
    game = save.part("game2.xml").tree() if save.part("game2.xml") else None
    tree = save.part("save.sww").tree()
    if not isinstance(tree, bintree.Tree):
        s.note(N_("save.sww is no binary tag tree: only the parts are shown."))
        return s
    engine = tree.path("SaveGame/m_GameEngine")
    _load_list(s, stats, engine)
    params = game.nodes[0] if isinstance(game, bintree.Tree) and game.nodes else None  # the game's setup comes first
    _players(s, params, tree)
    _setup(s, params)
    _islands(s, tree.path("SaveGame/WorldConfig"))
    return s
