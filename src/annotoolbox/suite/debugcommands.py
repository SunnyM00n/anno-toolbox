"""The debug-command catalogue of the palette: which commands a game offers its modders, their typed arguments, and
the checks that keep the live link's message set closed (only these ids, only values of these types, no free text).

``commands(game)`` is the catalogue, ``check_values`` types a command's values or says why not, ``preview`` shows the
call the game side builds from its fixed template, ``find`` is the search, ``push_history`` / ``toggle_favourite`` keep
the palette's remembered state. The window is ``linkpalette``. docs/debug-palette.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..infra.i18n import N_, tr

GAME = "anno117"
HISTORY_LIMIT = 50
CHEATS = "cheats"  # the pseudo category of the names the game itself registers (docs/debug-palette.md 1.3)
FAVOURITES = "favourites"

PERCENT, GUID, FLAG = "percent", "guid", "flag"  # the argument kinds: 0 to 100, an asset's GUID, on or off
ECONOMY, VIEWS, SELECTION, SPAWN = "economy", "views", "selection", "spawn"
CATEGORIES = {
    ECONOMY: N_("Economy and build"),
    VIEWS: N_("Views and display"),
    SELECTION: N_("Selected objects"),
    SPAWN: N_("Spawn and test"),
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


_PERCENT = ArgSpec("percentage", PERCENT, N_("Percent"))
_GUID = ArgSpec("guid", GUID, N_("Asset GUID"))
_ON = ArgSpec("value", FLAG, N_("On"))
_G = "Cheat.GlobalCheats:"
_S = "Cheats:"


def _toggle(id_, category, title, effect, method, state="", tag=TAG_LISTED) -> Command:
    return Command(id_, category, title, effect, f"{_G}{method}()", (), state, False, tag)


_COMMANDS: tuple[Command, ...] = (
    _toggle("ignore_building_costs", ECONOMY, N_("Ignore building costs"), N_("building costs are not charged"),
        "ToggleIgnoreBuildingCosts", "IgnoreBuildingCosts"),
    _toggle("ignore_fertilities", ECONOMY, N_("Ignore fertilities"), N_("every building produces on any ground"),
        "ToggleIgnoreFertilities", "IgnoreFertilities"),
    _toggle("ignore_threats", ECONOMY, N_("Ignore threats"), N_("threats do not affect the player"), "ToggleIgnoreThreats", "IgnoreThreats"),
    _toggle("upgrade_check", ECONOMY, N_("Skip the upgrade check"), N_("buildings upgrade without their requirements"),
        "ToggleUpgradeCheck", "", TAG_USED),
    _toggle("unlock_all", ECONOMY, N_("Unlock everything"), N_("every unlock is open for the human players"),
        "ToggleUnlockAllForHumans", "", TAG_USED),
    _toggle("productivity", ECONOMY, N_("Cheat productivity"), N_("productivity is raised by the game's cheat"),
        "ToggleProductivity", "IsProductivityCheated"),
    _toggle("incidents", ECONOMY, N_("Switch incidents"), N_("incidents (fires, plagues) are turned off or on"),
        "ToggleIncidents", "IncidentsDisabled"),
    _toggle("happy_days", ECONOMY, N_("Switch happy days"), N_("the happy days events are turned off or on"), "ToggleHappyDaysDisabled"),
    _toggle("super_ship_speed", ECONOMY, N_("Super ship speed"), N_("ships sail very fast"), "ToggleSuperShipSpeed", "SuperShipSpeedEnabled"),
    _toggle("undiscovered", ECONOMY, N_("Reveal the map"), N_("undiscovered areas are shown as discovered"),
        "DisableUndiscovered", "IsUndiscoveredDisabled"),
    _toggle("win_lose", ECONOMY, N_("No win or lose"), N_("the win and lose conditions are switched off"),
        "DisableWinLoseConditions", "IsWinLoseConditionsDisabled"),
    _toggle("construction_ai", ECONOMY, N_("Construction AI"), N_("the construction helper AI is switched on or off"),
        "ToggleConstructionAIEnabled", "ConstructionAIEnabled"),
    _toggle("attack_view", VIEWS, N_("Attack debug view"), N_("shows what the attack logic sees"), "ToggleAttackDebugView", "IsAttackDebugView"),
    _toggle("los_view", VIEWS, N_("Line-of-sight view"), N_("shows the line-of-sight debug view"), "ToggleLOSDebugView", "IsLOSDebugView"),
    _toggle("debug_zoom", VIEWS, N_("Debug camera zoom"), N_("the camera may zoom further than in play"), "ToggleUseDebugCameraZoomLevels"),
    _toggle("letterboxing", VIEWS, N_("No letterboxing"), N_("cinematic black bars are switched off"), "ToggleLetterboxingDisabled"),
    _toggle("banners", VIEWS, N_("Block banners"), N_("banner notifications are blocked"), "ToggleBlockBannerNotifications"),
    _toggle("debug_drawings", VIEWS, N_("Hide debug drawings"), N_("the debug drawings are suppressed"), "ToggleSuppressDebugDrawings"),
    Command("heal_selected", SELECTION, N_("Heal selected"), N_("heals the selected objects by a percentage"),
        f"{_S}HealSelectedObjects({{percentage}})", (_PERCENT,)),
    Command("damage_selected", SELECTION, N_("Damage selected"), N_("damages the selected objects by a percentage"),
        f"{_S}DamageSelectedObjects({{percentage}})", (_PERCENT,)),
    Command("invincible_selected", SELECTION, N_("Invincible selected"), N_("makes the selected objects invincible or mortal"),
        f"{_S}SetSelectedObjectsInvincible({{value}})", (_ON,)),
    Command("destroy_selected", SELECTION, N_("Destroy selected"), N_("destroys the selected objects"),
        f"{_S}DestroySelectedObjects()", (), "", True),
    Command("spawn_object", SPAWN, N_("Spawn an object"), N_("spawns the asset at the player"), f"{_S}SpawnGameObject({{guid}})", (_GUID,)),
    Command("build_mode", SPAWN, N_("Build mode for an asset"), N_("starts the build mode for the building"),
        f"{_S}StartBuildMode({{guid}})", (_GUID,)),
    Command("show_notification", SPAWN, N_("Show a notification"), N_("plays a notification asset"), f"{_S}ShowNotification({{guid}})", (_GUID,)),
    Command("show_text_popup", SPAWN, N_("Show a text popup"), N_("opens a text popup asset"), f"{_S}ShowTextPopup({{guid}})", (_GUID,)),
    Command("show_collectables", SPAWN, N_("Show all collectables"), N_("shows or hides every collectable"),
        f"{_S}ShowAllCollectables({{value}})", (_ON,)),
    Command("fps_view", VIEWS, N_("Frame rate view"), N_("shows or hides the frame rate"), f"{_S}ToggleFpsView()"),
    Command("debug_ui", VIEWS, N_("Debug UI"), N_("shows or hides the debug UI"), f"{_S}ToggleDebugUI()"),
    Command("version_label", VIEWS, N_("Version label"), N_("shows or hides the version label"), f"{_S}ToggleVersionLabel()"),
)  # fmt: skip
_BY_GAME = {GAME: {c.id: c for c in _COMMANDS}}


def commands(game: str = GAME) -> dict[str, Command]:
    """The catalogue of a game by id (empty for a game that has none yet)."""
    return _BY_GAME.get(game, {})


def category_title(category: str) -> str:
    """The category's name in the interface language."""
    if category == FAVOURITES:
        return tr("Favourites")
    if category == CHEATS:
        return tr("The game's own cheats")
    return tr(CATEGORIES[category])


def check_values(command: Command, values: dict) -> tuple[dict, str]:
    """The typed values of ``command`` and "" , or ``({}, why)``: every argument present, of its kind and in range,
    nothing else (the closed message set, docs/debug-palette.md section 2)."""
    names = {a.name for a in command.args}
    extra = sorted(set(values) - names)
    if extra:
        return {}, tr("{command}: unexpected argument {name}", command=command.id, name=extra[0])
    typed: dict = {}
    for arg in command.args:
        if arg.name not in values:
            return {}, tr("{command}: {name} is missing", command=command.id, name=arg.name)
        value = values[arg.name]
        if arg.kind == FLAG and isinstance(value, bool):
            typed[arg.name] = value
        elif arg.kind in (PERCENT, GUID) and isinstance(value, int) and not isinstance(value, bool):
            limit = 100 if arg.kind == PERCENT else 1 << 53
            if not 0 <= value <= limit:
                return {}, tr(
                    "{command}: {name} must be between 0 and {limit}", command=command.id, name=arg.name, limit=limit
                )
            typed[arg.name] = value
        else:
            return {}, tr("{command}: {name} is not a valid {kind}", command=command.id, name=arg.name, kind=arg.kind)
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
        haystack = " ".join((command.id, command.title, command.effect, tr(command.title), tr(command.effect))).lower()
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
