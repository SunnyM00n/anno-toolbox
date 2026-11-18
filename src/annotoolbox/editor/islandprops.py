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
from ..othergames.isd1701 import Island1701
from . import islandmeta
from .editchange import Change, copy_json
from .editsession import EditSession
from .formats.isd import props as isd_props

ASSETS_117 = "data/base/config/export/assets.xml"


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str  # int | angle | rect | choice | choices | readonly
    lo: float = 0
    hi: float = 0
    choices: tuple[str, ...] = ()  # choice: one of them ("" allowed); choices: several, joined with ";"
    tip: str = ""
    optional: bool = False  # an angle the island may leave unset


@dataclass
class Section:
    """One group of settings: the edit file's key, its fields, the island's own values and the checks for it."""

    title: str
    edit_key: str
    fields: tuple[Field, ...]
    base: dict
    check: Callable[[dict], list[str]]
    note: str = ""
    enabled: bool = True  # False: shown, not editable (the note says why)
    extra: dict = field(default_factory=dict)

    def values(self, edits: dict) -> dict:
        """The island's values with the edit file's changes over them."""
        return {**self.base, **edits.get(self.edit_key, {})}


def info_fields() -> tuple[Field, ...]:
    return (
        Field("grid", tr("islands.grid_tiles"), "readonly", tip=tr("islands.tip.islands_grid_fixed_files")),
        Field(
            "start_coast", tr("islands.start_coast_faces_degrees"), "angle", 0, 360,
            tip=tr("islands.tip.where_start_coast_lies"), optional=True,
        ),
        Field(
            "active_rect", tr("islands.active_rectangle_tiles"), "rect",
            tip=tr("islands.tip.x0_z0_x1_z1_whole"),
        ),
    )  # fmt: skip


def property_fields(north_south: bool = False) -> tuple[Field, ...]:
    """The 2070-format island's property fields; ``north_south``: 1701's header (clime north or south, difficulty 0-2)."""
    if north_south:
        return (
            Field("clime", tr("islands.clime"), "int", 0, 1, tip=tr("islands.tip.0_north_1_south")),
            Field("difficulty", tr("common.difficulty"), "int", 0, 2, tip=tr("islands.tip.0_to_2")),
        )
    return (
        Field("clime", tr("islands.clime"), "int", 0, 1, tip=tr("islands.tip.0_above_water_1_underwater")),
        Field("difficulty", tr("common.difficulty"), "int", 0, 1, tip=tr("islands.tip.0_all_one_island_game")),
        Field("sea_level", tr("islands.sea_level"), "int", -40, 0, tip=tr("islands.tip.0_normal_islands_28_lower")),
    )


def _info_section(editor, session: EditSession) -> Section:
    info = islandinfo.read(editor.view.info)
    grid = info["grid"] or session.size
    note = ""
    if session.edits.get("active"):
        note = tr("islands.active_map_edited_rectangle_follows")
    return Section(
        tr("islands.island"),
        "meta",
        info_fields(),
        {k: info[k] for k in ("grid", "start_coast", "active_rect")},
        lambda values: islandinfo.check(values, grid),
        note,
    )


def registration_of(editor) -> dict | None:
    """The source island's pool registration and the values the game's own registrations use, from the game's
    assets; None when the assets cannot be read."""
    try:
        text = editor.files.read(ASSETS_117).decode("utf-8", "ignore")
    except (KeyError, OSError):
        return None
    found = islandmeta.registrations(text, editor.a7m_path)
    current = found[0].values if found else dict.fromkeys(islandmeta.FIELDS, "")
    return {"current": current, "known": islandmeta.known_values(text)}


def _registration_section(editor, session: EditSession) -> Section | None:
    data = registration_of(editor)
    if data is None:
        return None
    fields = []
    for name in islandmeta.FIELDS:
        kind = "choices" if name in islandmeta.LISTS else "choice"
        choices = tuple(data["known"][name]) if name != "IslandPlaceholderSize" else ("", "Medium", "Large")
        fields.append(
            Field(
                name,
                name.removeprefix("Island"),
                kind,
                choices=choices,
                tip=tr("islands.tip.name_registration", name=name),
            )
        )
    new = session.edits.get("target") == "new"
    return Section(
        tr("islands.pool_registration"),
        "registration",
        tuple(fields),
        data["current"],
        lambda values: islandmeta.check(values, data["known"], session.size),
        "" if new else tr("islands.only_island_saved_new_one"),
        new,
    )


def _session_section(editor, session: EditSession) -> Section | None:
    """The session settings (``sessionsettings``): the ambient light and the vegetation set from the game's own
    lists, the playable area; None for an island with none of them."""
    base = session.island.get("session")
    if not base:
        return None
    names = {"ambient": sessionsettings.ambient_names, "vegetation": sessionsettings.vegetation_names}
    labels = {
        "ambient": (tr("islands.ambient_light"), tr("islands.light_preset_island_drawn_games")),
        "vegetation": (tr("islands.vegetation_set"), tr("islands.set_islands_vegetation_props_drawn")),
    }
    fields = [
        Field(key, labels[key][0], "choice", choices=tuple(names[key](editor.files)), tip=labels[key][1])
        if key in labels
        else Field(
            key,
            tr("islands.playable_area_tiles"),
            "rect",
            tip=tr("islands.tip.x0_z0_x1_z1_inside"),
        )
        for key in sessionsettings.FIELDS
        if key in base
    ]
    return Section(
        tr("islands.session"),
        "session",
        tuple(fields),
        base,
        lambda changes: [why for k, v in changes.items() if (why := sessionsettings.problem(k, v, session.size))],
        tr("islands.names_game_not_know_kept"),
    )


def _properties_section(editor) -> Section | None:
    """The properties of a 2070-format island's file, or of a 1701 island's ``.isc`` header."""
    header = isinstance(editor.isl, Island1701)
    root = editor.isl.header if header else editor.isl.root
    if root is None:
        return None
    only = isd_props.HEADER_1701 if header else None
    ranges = isd_props.RANGES_1701 if header else None
    keys = isd_props.editable(root, only)
    if not keys:
        return None
    base = isd_props.read(root)
    fields = (
        Field("size", tr("islands.size_tiles"), "readonly"),
        *(f for f in property_fields(header) if f.key in keys),
    )
    return Section(
        tr("islands.island_properties"),
        "properties",
        fields,
        {"size": base["width"], **base},
        lambda props: isd_props.check(props, ranges),
    )


def sections_for(editor, session: EditSession) -> list[Section]:
    """The sections of the open island, empty when it has none (a built island or one only looked at)."""
    from .a7mviewer import A7mViewer
    from .formats.a7m.edit117 import VanillaEditor

    if session.readonly:
        return []
    out: list[Section | None] = []
    if isinstance(editor, (VanillaEditor, A7mViewer)) and getattr(editor, "view", None) is not None:
        out.append(_info_section(editor, session))
        if isinstance(editor, VanillaEditor):
            out.append(_registration_section(editor, session))
    elif isinstance(getattr(editor, "isl", None), (isd2070.Island, Island1701)):
        out.append(_properties_section(editor))
    if getattr(editor, "view", None) is not None and getattr(editor, "files", None) is not None:
        out.append(_session_section(editor, session))
    return [s for s in out if s is not None]


def set_values(session: EditSession, section: Section, values: dict) -> list[str]:
    """Write ``values`` of ``section`` into the edit document as one change; only what differs from the island's own
    is kept. Returns the problems the checks name (the values are written all the same: the form warns, it does not
    forbid, as the island build does)."""
    changes = {k: v for k, v in values.items() if k in section.base and v != section.base[k]}
    problems = section.check(changes)
    before = copy_json(session.edits.get(section.edit_key))
    after = copy_json(changes) or None

    def put(value) -> None:
        if value is None:
            session.edits.pop(section.edit_key, None)
        else:
            session.edits[section.edit_key] = copy_json(value)
        session._notify(Change())

    if before != after:
        put(after)
        session._push(f"{section.title.lower()}", lambda: put(before), lambda: put(after))
    return problems
