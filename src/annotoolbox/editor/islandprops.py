"""The island's own settings as forms: what ``.a7minfo`` holds (start coast, active rectangle), the pool registration of
an island saved as a new one, and the properties of a 2070-format island (clime, difficulty, sea level).

``sections_for`` lists the sections the open island offers, each with its fields, the island's own values and its
checks; ``set_values`` writes a section's values into the edit document as one undoable change, keeping only what
differs from the island (the edit file's ``meta``, ``registration`` and ``properties``; island-editor "Full spec").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..formats import isd2070
from ..infra.i18n import tr
from ..islands import islandinfo, sessionsettings
from ..othergames.isd1701 import CIsland1701
from . import islandmeta
from .editchange import SChange, copy_json
from .editsession import CEditSession
from .formats.isd import props as isd_props

ASSETS_117 = "data/base/config/export/assets.xml"


@dataclass(frozen=True)
class SField:
    key: str
    label: str
    kind: str  # int | angle | rect | choice | choices | readonly
    lo: float = 0
    hi: float = 0
    choices: tuple[str, ...] = ()  # choice: one of them ("" allowed); choices: several, joined with ";"
    tip: str = ""
    optional: bool = False  # an angle the island may leave unset


@dataclass
class SSection:
    """One group of settings: the edit file's key, its fields, the island's own values and the checks for it."""

    title: str
    edit_key: str
    fields: tuple[SField, ...]
    base: dict
    check: Callable[[dict], list[str]]
    note: str = ""
    enabled: bool = True  # False: shown, not editable (the note says why)
    extra: dict = field(default_factory=dict)

    def values(self, edits: dict) -> dict:
        """The island's values with the edit file's changes over them."""
        return {**self.base, **edits.get(self.edit_key, {})}


def info_fields() -> tuple[SField, ...]:
    return (
        SField("grid", tr("islands.grid_tiles"), "readonly", tip=tr("islands.tip.islands_grid_fixed_files")),
        SField(
            "start_coast", tr("islands.start_coast_faces_degrees"), "angle", 0, 360,
            tip=tr("islands.tip.where_start_coast_lies"), optional=True,
        ),
        SField(
            "active_rect", tr("islands.active_rectangle_tiles"), "rect",
            tip=tr("islands.tip.x0_z0_x1_z1_whole"),
        ),
    )  # fmt: skip


def property_fields(north_south: bool = False) -> tuple[SField, ...]:
    """The 2070-format island's property fields; ``north_south``: 1701's header (clime north or south, difficulty 0-2)."""
    if north_south:
        return (
            SField("clime", tr("islands.clime"), "int", 0, 1, tip=tr("islands.tip.0_north_1_south")),
            SField("difficulty", tr("common.difficulty"), "int", 0, 2, tip=tr("islands.tip.0_to_2")),
        )
    return (
        SField("clime", tr("islands.clime"), "int", 0, 1, tip=tr("islands.tip.0_above_water_1_underwater")),
        SField("difficulty", tr("common.difficulty"), "int", 0, 1, tip=tr("islands.tip.0_all_one_island_game")),
        SField("sea_level", tr("islands.sea_level"), "int", -40, 0, tip=tr("islands.tip.0_normal_islands_28_lower")),
    )


def _info_section(editor, session: CEditSession) -> SSection:
    island_info = islandinfo.read(editor.m_view.a7minfo)
    grid = island_info["grid"] or session.m_size_tiles
    note = ""
    if session.m_edits.get("active"):
        note = tr("islands.active_map_edited_rectangle_follows")
    return SSection(
        tr("islands.island"),
        "meta",
        info_fields(),
        {k: island_info[k] for k in ("grid", "start_coast", "active_rect")},
        lambda values: islandinfo.check(values, grid),
        note,
    )


def registration_of(editor) -> dict | None:
    """The source island's pool registration and the values the game's own registrations use, from the game's
    assets; None when the assets cannot be read."""
    try:
        text = editor.m_files.read(ASSETS_117).decode("utf-8", "ignore")
    except (KeyError, OSError):
        return None
    found = islandmeta.registrations(text, editor.m_a7m_path)
    current = found[0].values if found else dict.fromkeys(islandmeta.FIELDS, "")
    return {"current": current, "known": islandmeta.known_values(text)}


def _registration_section(editor, session: CEditSession) -> SSection | None:
    data = registration_of(editor)
    if data is None:
        return None
    fields = []
    for name in islandmeta.FIELDS:
        kind = "choices" if name in islandmeta.LISTS else "choice"
        choices = tuple(data["known"][name]) if name != "IslandPlaceholderSize" else ("", "Medium", "Large")
        fields.append(
            SField(
                name,
                name.removeprefix("Island"),
                kind,
                choices=choices,
                tip=tr("islands.tip.name_registration", name=name),
            )
        )
    new = session.m_edits.get("target") == "new"
    return SSection(
        tr("islands.pool_registration"),
        "registration",
        tuple(fields),
        data["current"],
        lambda values: islandmeta.check(values, data["known"], session.m_size_tiles),
        "" if new else tr("islands.only_island_saved_new_one"),
        new,
    )


def _session_section(editor, session: CEditSession) -> SSection | None:
    """The session settings (``sessionsettings``): the ambient light and the vegetation set from the game's own
    lists, the playable area; None for an island with none of them."""
    base = session.m_island.get("session")
    if not base:
        return None
    names = {"ambient": sessionsettings.ambient_names, "vegetation": sessionsettings.vegetation_names}
    labels = {
        "ambient": (tr("islands.ambient_light"), tr("islands.light_preset_island_drawn_games")),
        "vegetation": (tr("islands.vegetation_set"), tr("islands.set_islands_vegetation_props_drawn")),
    }
    fields = [
        SField(key, labels[key][0], "choice", choices=tuple(names[key](editor.m_files)), tip=labels[key][1])
        if key in labels
        else SField(
            key,
            tr("islands.playable_area_tiles"),
            "rect",
            tip=tr("islands.tip.x0_z0_x1_z1_inside"),
        )
        for key in sessionsettings.FIELDS
        if key in base
    ]
    return SSection(
        tr("islands.session"),
        "session",
        tuple(fields),
        base,
        lambda changes: [
            why for k, value in changes.items() if (why := sessionsettings.problem(k, value, session.m_size_tiles))
        ],
        tr("islands.names_game_not_know_kept"),
    )


def _properties_section(editor) -> SSection | None:
    """The properties of a 2070-format island's file, or of a 1701 island's ``.isc`` header."""
    header = isinstance(editor.m_isl, CIsland1701)
    root = editor.m_isl.m_header if header else editor.m_isl.m_root
    if root is None:
        return None
    only = isd_props.HEADER_1701 if header else None
    ranges = isd_props.RANGES_1701 if header else None
    keys = isd_props.editable(root, only)
    if not keys:
        return None
    base = isd_props.read(root)
    fields = (
        SField("size", tr("islands.size_tiles"), "readonly"),
        *(property_field for property_field in property_fields(header) if property_field.key in keys),
    )
    return SSection(
        tr("islands.island_properties"),
        "properties",
        fields,
        {"size": base["width"], **base},
        lambda props: isd_props.check(props, ranges),
    )


def sections_for(editor, session: CEditSession) -> list[SSection]:
    """The sections of the open island, empty when it has none (a built island or one only looked at)."""
    from .a7mviewer import CA7mViewer
    from .formats.a7m.edit117 import CVanillaEditor

    if session.m_readonly:
        return []
    out: list[SSection | None] = []
    if isinstance(editor, (CVanillaEditor, CA7mViewer)) and getattr(editor, "m_view", None) is not None:
        out.append(_info_section(editor, session))
        if isinstance(editor, CVanillaEditor):
            out.append(_registration_section(editor, session))
    elif isinstance(getattr(editor, "m_isl", None), (isd2070.CIsland, CIsland1701)):
        out.append(_properties_section(editor))
    if getattr(editor, "m_view", None) is not None and getattr(editor, "m_files", None) is not None:
        out.append(_session_section(editor, session))
    return [section for section in out if section is not None]


def set_values(session: CEditSession, section: SSection, values: dict) -> list[str]:
    """Write ``values`` of ``section`` into the edit document as one change; only what differs from the island's own
    is kept. Returns the problems the checks name (the values are written all the same: the form warns, it does not
    forbid, as the island build does)."""
    changes = {k: new_value for k, new_value in values.items() if k in section.base and new_value != section.base[k]}
    problems = section.check(changes)
    before = copy_json(session.m_edits.get(section.edit_key))
    after = copy_json(changes) or None

    def put(value) -> None:
        if value is None:
            session.m_edits.pop(section.edit_key, None)
        else:
            session.m_edits[section.edit_key] = copy_json(value)
        session._notify(SChange())

    if before != after:
        put(after)
        session._push(f"{section.title.lower()}", lambda: put(before), lambda: put(after))
    return problems
