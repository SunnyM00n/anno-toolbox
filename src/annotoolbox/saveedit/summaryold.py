"""The summary of an Anno 1404 or 1701 save (``.sww``): the world it holds, read as the world files are
(``docs/save-editor.md``, "The other games"). Built from the documented world format with synthetic samples; no real
save of either game was available, so what a real save adds (the players' economy, the buildings) is only in the tree.
"""

from __future__ import annotations

from ..infra.i18n import N_
from .container import SSaveFile
from .records import CSummary, SSection, STable

WORLD = "WorldConfig"
SIZE = (("Width", "Height"), ("m_Width", "m_Height"))  # 1404's names, 1701's
ISLAND_FIELDS = {  # the island's record: (1404's tag, 1701's tag) of each value
    "name": ("Name", "m_Name"),
    "file": ("Filename", "m_GOPFilename"),
    "x": ("PositionX", "m_XPosition"),
    "z": ("PositionZ", "m_ZPosition"),
    "width": ("Width", "m_Width"),
    "height": ("Height", "m_Height"),
}


def _first(node, *names: str) -> str:
    """The text of the first of ``names`` that ``node`` has as a child."""
    for name in names:
        value = node.value(name) if node is not None else None
        if value is not None:
            return value.strip()
    return ""


def _world(root):
    return root.find(WORLD) if root is not None else None


def _islands(world) -> STable:
    table = STable([N_("islands.island"), N_("common.name"), N_("common.file"), N_("saves.position"), N_("saves.size")])
    islands = world.find("Islands") if world is not None else None
    for number, value in enumerate(
        (child for child in islands.children if child.name == "v") if islands is not None else [], 1
    ):
        config = value.find("IslandConfig") or value
        got = {key: _first(config, *names) for key, names in ISLAND_FIELDS.items()}
        table.rows.append([
            str(number), got["name"], got["file"], f"{got['x']}, {got['z']}", f"{got['width']} x {got['height']}",
        ])  # fmt: skip
    return table


def _participants(world) -> STable | None:
    infos = world.path("GameData/PlayerInfos") if world is not None else None
    if infos is None:
        return None
    table = STable([N_("saves.player"), N_("ui.type"), N_("mods.profile"), N_("common.colour")])
    for number, value in enumerate(child for child in infos.children if child.name == "v"):
        table.rows.append([str(number), _first(value, "m_Type"), _first(value, "m_Profile"), _first(value, "m_Color")])
    return table


def summarize(save: SSaveFile) -> CSummary:
    summary = CSummary(save.game)
    part = save.parts[0] if save.parts else None
    world = _world(part.tree()) if part is not None and part.tree() is not None else None
    if world is None:
        summary.note(N_("saves.no_world_found_save_shown"))
        return summary
    section_node = summary.section(N_("common.save"))
    section_node.rows += [
        (N_("common.game"), "Anno 1404" if save.game == "anno1404" else "Anno 1701"),
        (N_("saves.saved_game"), _first(world, "m_IsSaveGame")),
        (N_("saves.world_name"), _first(world, "m_Name")),
        (N_("saves.world_size"), _size(world)),
    ]
    summary.m_sections.append(SSection(N_("common.islands"), table=_islands(world)))
    participants = _participants(world)
    if participants is not None:
        summary.m_sections.append(SSection(N_("saves.players"), table=participants))
    summary.note(N_("saves.only_world_read_no_save"))
    return summary


def _size(world) -> str:
    for width, height in SIZE:
        if _first(world, width):
            return f"{_first(world, width)} x {_first(world, height)}"
    return ""
