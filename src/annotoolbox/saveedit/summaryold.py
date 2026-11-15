"""The summary of an Anno 1404 or 1701 save (``.sww``): the world it holds, read as the world files are
(``docs/save-editor.md``, "The other games"). Built from the documented world format with synthetic samples; no real
save of either game was available, so what a real save adds (the players' economy, the buildings) is only in the tree.
"""

from __future__ import annotations

from ..infra.i18n import N_
from .container import SaveFile
from .records import Section, Summary, Table

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


def _islands(world) -> Table:
    table = Table([N_("Island"), N_("Name"), N_("File"), N_("Position"), N_("Size")])
    islands = world.find("Islands") if world is not None else None
    for number, value in enumerate((c for c in islands.children if c.name == "v") if islands is not None else [], 1):
        config = value.find("IslandConfig") or value
        got = {key: _first(config, *names) for key, names in ISLAND_FIELDS.items()}
        table.rows.append([
            str(number), got["name"], got["file"], f"{got['x']}, {got['z']}", f"{got['width']} x {got['height']}",
        ])  # fmt: skip
    return table


def _players(world) -> Table | None:
    infos = world.path("GameData/PlayerInfos") if world is not None else None
    if infos is None:
        return None
    table = Table([N_("Player"), N_("Type"), N_("Profile"), N_("Colour")])
    for number, value in enumerate(c for c in infos.children if c.name == "v"):
        table.rows.append([str(number), _first(value, "m_Type"), _first(value, "m_Profile"), _first(value, "m_Color")])
    return table


def summarize(save: SaveFile) -> Summary:
    s = Summary(save.game)
    part = save.parts[0] if save.parts else None
    world = _world(part.tree()) if part is not None and part.tree() is not None else None
    if world is None:
        s.note(N_("No world was found in this save: it is shown as a tree, read only."))
        return s
    sec = s.section(N_("Save"))
    sec.rows += [
        (N_("Game"), "Anno 1404" if save.game == "anno1404" else "Anno 1701"),
        (N_("Saved game"), _first(world, "m_IsSaveGame")),
        (N_("World name"), _first(world, "m_Name")),
        (N_("World size"), _size(world)),
    ]
    s.sections.append(Section(N_("Islands"), table=_islands(world)))
    players = _players(world)
    if players is not None:
        s.sections.append(Section(N_("Players"), table=players))
    s.note(N_("Only the world is read; no save of this game was available to pin the rest of its layout."))
    return s


def _size(world) -> str:
    for width, height in SIZE:
        if _first(world, width):
            return f"{_first(world, width)} x {_first(world, height)}"
    return ""
