"""The debug-command catalogue of the palette: which commands a game offers its modders, their typed arguments, and
the checks that keep the live link's message set closed (only these ids, only values of these types, no free text).

``commands(game)`` is the catalogue, ``check_values`` types a command's values or says why not, ``preview`` shows the
call the game side builds from its fixed template, ``find`` is the search, ``push_history`` / ``toggle_favourite`` keep
the palette's remembered state. The window is ``linkpalette``. docs/debug-palette.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..infra.i18n import N_, english, tr

GAME = "anno117"
HISTORY_LIMIT = 50
CHEATS = "cheats"  # the pseudo category of the names the game itself registers (docs/debug-palette.md 1.3)
FAVOURITES = "favourites"

PERCENT, GUID, FLAG = "percent", "guid", "flag"  # the argument kinds: 0 to 100, an asset's GUID, on or off
ECONOMY, VIEWS, SELECTION, SPAWN = "economy", "views", "selection", "spawn"
CATEGORIES = {
    ECONOMY: N_("common.economy_build"),
    VIEWS: N_("common.views_display"),
    SELECTION: N_("common.selected_objects"),
    SPAWN: N_("common.spawn_test"),
}
TAG_USED, TAG_LISTED = "used", "listed"  # called by a mod of ours in a game, or only declared (debug-palette 1)


@dataclass(frozen=True)
class ArgSpec:
    """One typed argument: ``kind`` is ``PERCENT``, ``GUID`` or ``FLAG``; ``name`` is the template's placeholder."""

    name: str
    kind: str
    title: str


@dataclass(frozen=True)
class Command:
    """One command: the id the wire carries, where it sits, what it does, the call the game side makes from it."""

    id: str
    category: str
    title: str
    effect: str
    call: str  # the fixed template; ``{name}`` is replaced by a typed value (docs/debug-palette.md 1)
    args: tuple[ArgSpec, ...] = ()
    state: str = ""  # the game's field that holds a toggle's state ("" = no state to read back)
    confirm: bool = False  # destructive: the palette asks first
    tag: str = TAG_LISTED


_PERCENT = ArgSpec("percentage", PERCENT, N_("common.percent"))
_GUID = ArgSpec("guid", GUID, N_("common.asset_guid"))
_ON = ArgSpec("value", FLAG, N_("common.on"))
_G = "Cheat.GlobalCheats:"
_S = "Cheats:"


def _toggle(id_, category, title, effect, method, state="", tag=TAG_LISTED) -> Command:
    return Command(id_, category, title, effect, f"{_G}{method}()", (), state, False, tag)


_COMMANDS: tuple[Command, ...] = (
    _toggle("ignore_building_costs", ECONOMY, N_("common.ignore_building_costs"), N_("common.building_costs_not_charged"),
        "ToggleIgnoreBuildingCosts", "IgnoreBuildingCosts"),
    _toggle("ignore_fertilities", ECONOMY, N_("common.ignore_fertilities"), N_("common.every_building_produces_any_ground"),
        "ToggleIgnoreFertilities", "IgnoreFertilities"),
    _toggle("ignore_threats", ECONOMY, N_("common.ignore_threats"), N_("common.threats_not_affect_player"), "ToggleIgnoreThreats", "IgnoreThreats"),
    _toggle("upgrade_check", ECONOMY, N_("common.skip_upgrade_check"), N_("common.buildings_upgrade_without_requirements"),
        "ToggleUpgradeCheck", "", TAG_USED),
    _toggle("unlock_all", ECONOMY, N_("common.unlock_everything"), N_("common.every_unlock_open_human_players"),
        "ToggleUnlockAllForHumans", "", TAG_USED),
    _toggle("productivity", ECONOMY, N_("common.cheat_productivity"), N_("common.productivity_raised_games_cheat"),
        "ToggleProductivity", "IsProductivityCheated"),
    _toggle("incidents", ECONOMY, N_("common.switch_incidents"), N_("common.incidents_fires_plagues_turned_off"),
        "ToggleIncidents", "IncidentsDisabled"),
    _toggle("happy_days", ECONOMY, N_("common.switch_happy_days"), N_("common.happy_days_events_turned_off"), "ToggleHappyDaysDisabled"),
    _toggle("super_ship_speed", ECONOMY, N_("common.super_ship_speed"), N_("common.ships_sail_very_fast"), "ToggleSuperShipSpeed", "SuperShipSpeedEnabled"),
    _toggle("undiscovered", ECONOMY, N_("common.reveal_map"), N_("common.undiscovered_areas_shown_discovered"),
        "DisableUndiscovered", "IsUndiscoveredDisabled"),
    _toggle("win_lose", ECONOMY, N_("common.no_win_lose"), N_("common.win_lose_conditions_switched_off"),
        "DisableWinLoseConditions", "IsWinLoseConditionsDisabled"),
    _toggle("construction_ai", ECONOMY, N_("common.construction_ai"), N_("common.construction_helper_ai_switched_off"),
        "ToggleConstructionAIEnabled", "ConstructionAIEnabled"),
    _toggle("attack_view", VIEWS, N_("common.attack_debug_view"), N_("common.shows_what_attack_logic_sees"), "ToggleAttackDebugView", "IsAttackDebugView"),
    _toggle("los_view", VIEWS, N_("common.line_sight_view"), N_("common.shows_line_sight_debug_view"), "ToggleLOSDebugView", "IsLOSDebugView"),
    _toggle("debug_zoom", VIEWS, N_("common.debug_camera_zoom"), N_("common.camera_zoom_further_play"), "ToggleUseDebugCameraZoomLevels"),
    _toggle("letterboxing", VIEWS, N_("common.no_letterboxing"), N_("common.cinematic_black_bars_switched_off"), "ToggleLetterboxingDisabled"),
    _toggle("banners", VIEWS, N_("common.block_banners"), N_("common.banner_notifications_blocked"), "ToggleBlockBannerNotifications"),
    _toggle("debug_drawings", VIEWS, N_("common.hide_debug_drawings"), N_("common.debug_drawings_suppressed"), "ToggleSuppressDebugDrawings"),
    Command("heal_selected", SELECTION, N_("common.heal_selected"), N_("common.heals_selected_objects_percentage"),
        f"{_S}HealSelectedObjects({{percentage}})", (_PERCENT,)),
    Command("damage_selected", SELECTION, N_("common.damage_selected"), N_("common.damages_selected_objects_percentage"),
        f"{_S}DamageSelectedObjects({{percentage}})", (_PERCENT,)),
    Command("invincible_selected", SELECTION, N_("common.invincible_selected"), N_("common.makes_selected_objects_invincible_mortal"),
        f"{_S}SetSelectedObjectsInvincible({{value}})", (_ON,)),
    Command("destroy_selected", SELECTION, N_("common.destroy_selected"), N_("common.destroys_selected_objects"),
        f"{_S}DestroySelectedObjects()", (), "", True),
    Command("spawn_object", SPAWN, N_("common.spawn_object"), N_("common.spawns_asset_player"), f"{_S}SpawnGameObject({{guid}})", (_GUID,)),
    Command("build_mode", SPAWN, N_("common.build_mode_asset"), N_("common.starts_build_mode_building"),
        f"{_S}StartBuildMode({{guid}})", (_GUID,)),
    Command("show_notification", SPAWN, N_("common.show_notification"), N_("common.plays_notification_asset"), f"{_S}ShowNotification({{guid}})", (_GUID,)),
    Command("show_text_popup", SPAWN, N_("common.show_text_popup"), N_("common.opens_text_popup_asset"), f"{_S}ShowTextPopup({{guid}})", (_GUID,)),
    Command("show_collectables", SPAWN, N_("common.show_all_collectables"), N_("common.shows_hides_every_collectable"),
        f"{_S}ShowAllCollectables({{value}})", (_ON,)),
    Command("fps_view", VIEWS, N_("common.frame_rate_view"), N_("common.shows_hides_frame_rate"), f"{_S}ToggleFpsView()"),
    Command("debug_ui", VIEWS, N_("common.debug_ui"), N_("common.shows_hides_debug_ui"), f"{_S}ToggleDebugUI()"),
    Command("version_label", VIEWS, N_("common.version_label"), N_("common.shows_hides_version_label"), f"{_S}ToggleVersionLabel()"),
)  # fmt: skip
_BY_GAME = {GAME: {c.id: c for c in _COMMANDS}}


def commands(game: str = GAME) -> dict[str, Command]:
    """The catalogue of a game by id (empty for a game that has none yet)."""
    return _BY_GAME.get(game, {})


def category_title(category: str) -> str:
    """The category's name in the interface language."""
    if category == FAVOURITES:
        return tr("common.favourites")
    if category == CHEATS:
        return tr("common.games_own_cheats")
    return tr(CATEGORIES[category])


def check_values(command: Command, values: dict) -> tuple[dict, str]:
    """The typed values of ``command`` and "" , or ``({}, why)``: every argument present, of its kind and in range,
    nothing else (the closed message set, docs/debug-palette.md section 2)."""
    names = {a.name for a in command.args}
    extra = sorted(set(values) - names)
    if extra:
        return {}, tr("common.command_unexpected_argument_name", command=command.id, name=extra[0])
    typed: dict = {}
    for arg in command.args:
        if arg.name not in values:
            return {}, tr("common.command_name_is_missing", command=command.id, name=arg.name)
        value = values[arg.name]
        if arg.kind == FLAG and isinstance(value, bool):
            typed[arg.name] = value
        elif arg.kind in (PERCENT, GUID) and isinstance(value, int) and not isinstance(value, bool):
            limit = 100 if arg.kind == PERCENT else 1 << 53
            if not 0 <= value <= limit:
                return {}, tr("common.must_between_0", command=command.id, name=arg.name, limit=limit)
            typed[arg.name] = value
        else:
            return {}, tr("common.command_name_not_valid_kind", command=command.id, name=arg.name, kind=arg.kind)
    return typed, ""


def preview(command: Command, values: dict) -> str:
    """The call the game side makes (its fixed template with the values put in); a value not given shows as its name."""
    shown = {a.name: "{" + a.name + "}" for a in command.args}
    for name, value in values.items():
        if name in shown:
            shown[name] = str(value).lower() if isinstance(value, bool) else str(value)
    return command.call.format(**shown)


def find(game: str, text: str = "", category: str = "", favourites: tuple[str, ...] = ()) -> list[Command]:
    """The commands matching the search ``text`` (id, title, effect, in the interface language and in English) in a
    ``category`` ("" all; ``FAVOURITES`` the starred ones), in catalogue order."""
    words = text.lower().split()
    found = []
    for command in commands(game).values():
        if category == FAVOURITES and command.id not in favourites:
            continue
        if category and category != FAVOURITES and command.category != category:
            continue
        english_words = (english(command.title), english(command.effect))  # found by either language's words
        haystack = " ".join((command.id, *english_words, tr(command.title), tr(command.effect))).lower()
        if all(word in haystack for word in words):
            found.append(command)
    return found


# the palette's remembered state: {"history": [...], "favourites": [ids]} in the suite settings


def push_history(state: dict, entry: dict) -> None:
    """Put ``entry`` (command, values, status, message) first in ``state["history"]``, at most ``HISTORY_LIMIT``."""
    state["history"] = [entry, *state.get("history", [])][:HISTORY_LIMIT]


def toggle_favourite(state: dict, command_id: str) -> bool:
    """Star or unstar a command; True when it is a favourite now."""
    stars = [c for c in state.get("favourites", []) if c != command_id]
    starred = len(stars) == len(state.get("favourites", []))
    state["favourites"] = [*stars, command_id] if starred else stars
    return starred
