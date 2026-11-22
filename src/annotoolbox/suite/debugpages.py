"""The actions of Anno 117's own debug pages as palette commands: the buttons whose action is one fixed script call,
read from the game itself, and the script console of the retail build.

``COMMANDS`` is the list; ``debugcommands`` puts it into the game's catalogue, and the live link's table holds the
same ids. Where each comes from and what the game does not keep: docs/debug-palette.md section 1.4.
"""

from __future__ import annotations

from ..infra.i18n import N_
from .debugcommands import ARG_GUID, ARG_ON, QUESTS, RESEARCH, SELECTION, TAG_PAGE, TOOLS, VIEWS, SCommand


def _page(id_, category, title, effect, call, arguments=(), confirm=False, net=False) -> SCommand:
    return SCommand(id_, category, title, effect, call, arguments, "", confirm, TAG_PAGE, net)


_G = (ARG_GUID,)
COMMANDS: tuple[SCommand, ...] = (
    _page("tech_visible_all", RESEARCH, N_("common.all_research_visible"), N_("common.every_research_node_shown"),
        "TechHandler:CheatVisibleAll()"),
    _page("tech_researchable_all", RESEARCH, N_("common.all_research_researchable"),
        N_("common.every_research_node_can_started"), "TechHandler:CheatResearchableAll()"),
    _page("tech_research_all", RESEARCH, N_("common.research_everything"), N_("common.every_research_node_finished"),
        "TechHandler:CheatResearchAll()"),
    _page("tech_reset_all", RESEARCH, N_("common.reset_all_research"), N_("common.every_research_node_reset"),
        "TechHandler:CheatResetAll()", confirm=True),
    _page("tech_start", RESEARCH, N_("common.start_research_node"), N_("common.starts_research_node_guid"),
        "TechHandler:StartTech({guid})", _G),
    _page("tech_complete", RESEARCH, N_("common.complete_research_node"), N_("common.finishes_research_node_guid"),
        "TechHandler:CompleteTechResearch({guid}, true)", _G),
    _page("tech_reset", RESEARCH, N_("common.reset_research_node"), N_("common.resets_research_node_guid"),
        "TechHandler:ResetTech({guid})", _G),
    _page("unlock_toggle_all", RESEARCH, N_("common.unlock_everything_unlock_manager"),
        N_("common.unlock_manager_treats_everything_unlocked"), "Unlock:ToggleCheatUnlockAll()"),
    _page("unlock_asset", RESEARCH, N_("common.unlock_asset"), N_("common.unlocks_asset_guid"), "Unlock:UnlockNet({guid})",
        _G, net=True),
    _page("relock_asset", RESEARCH, N_("common.lock_asset_again"), N_("common.locks_asset_guid_again"),
        "Unlock:RelockNet({guid})", _G, net=True),
    _page("quest_storyline", QUESTS, N_("common.start_storyline"), N_("common.starts_storyline_guid_you"),
        "Quests:CheatStartStoryLineForCurrentPlayerNet({guid})", _G, net=True),
    _page("governor_decision", QUESTS, N_("common.start_governor_decision"), N_("common.starts_governor_decision_guid"),
        "GovernorDecision:CheatStartGovernorDecisionForCurrentPlayerNet({guid})", _G, net=True),
    _page("quest_pool_cooldown", QUESTS, N_("common.end_quest_pool_cooldown"), N_("common.quest_pool_guid_ready_now"),
        "Quests:CheatEndPoolCooldownNet({guid})", _G, net=True),
    _page("quest_pool_reset", QUESTS, N_("common.reset_quest_pool"), N_("common.quest_pool_guid_entries_reset"),
        "Quests:CheatResetPoolEntriesNet({guid})", _G, net=True),
    _page("tutorial_reset", QUESTS, N_("common.reset_tutorial_quests"), N_("common.tutorial_quests_start_again"),
        "Quests:ResetTutorialQuestsNet()", confirm=True, net=True),
    _page("season_end", QUESTS, N_("common.end_season"), N_("common.current_season_ends_20_seconds"),
        "Season:CheatEndCurrentSeasonIn20s()"),
    _page("scenario_finished", QUESTS, N_("common.finish_scenario"), N_("common.scenario_counts_won"),
        "WinLose:CheatScenarioFinished()", confirm=True),
    _page("scenario_reset", QUESTS, N_("common.reset_scenario"), N_("common.scenarios_progress_reset"),
        "Scenarios:ResetScenario()", confirm=True),
    _page("camera_sequence", QUESTS, N_("common.play_camera_sequence"), N_("common.plays_camera_sequence_guid"),
        "CameraSequenceManager:CheatStartCameraSequence({guid})", _G),
    _page("monument_upgrade", SELECTION, N_("common.monument_next_step"), N_("common.selected_monument_builds_next_step"),
        "Selection.Object.Monument:CheatUpgradeMicro()"),
    _page("monument_phase", SELECTION, N_("common.monument_next_phase"), N_("common.selected_monument_reaches_next_phase"),
        "Selection.Object.Monument:CheatMicroPhase()"),
    _page("monument_pause", SELECTION, N_("common.pause_monument"), N_("common.selected_monuments_construction_pauses"),
        "Selection.Object.Monument:PauseUpgrade()"),
    _page("construction_ready", SELECTION, N_("common.finish_construction"), N_("common.selected_building_site_done"),
        "Selection.Object.DelayedConstruction:CheatReady()"),
    _page("item_in_slot", SELECTION, N_("common.item_into_slot"), N_("common.item_guid_selected_objects_slot"),
        "Selection.Object.ItemContainer:CheatItemInSlot({guid}, 1)", _G),
    _page("render_conditions_on", VIEWS, N_("common.common_render_conditions"), N_("common.draws_games_common_conditions"),
        "DebugRender:SetCommonRenderConditions(7,true)"),
    _page("render_conditions_off", VIEWS, N_("common.no_common_render_conditions"), N_("common.stops_drawing_conditions"),
        "DebugRender:SetCommonRenderConditions(7,false)"),
    _page("season_visuals", VIEWS, N_("common.skip_season_visuals"), N_("common.season_change_no_visuals"),
        "Season:CheatToggleSkipVisualUpdates()"),
    _page("debug_picking", VIEWS, N_("common.draw_picking"), N_("common.draws_what_mouse_picks"),
        "Selection:SetEnableDebugDrawPicking({value})", (ARG_ON,)),
    _page("debug_quest", VIEWS, N_("common.watch_quest"), N_("common.quest_guid_debug_drawings"),
        "Quests:SetDebugQuestGUID({guid})", _G),
    _page("debug_object", VIEWS, N_("common.watch_object_type"), N_("common.objects_guid_debug_drawings"),
        "Objects:SetDebugObjectGUID({guid})", _G),
    _page("script_console", TOOLS, N_("common.script_console"), N_("common.opens_closes_script_console"),
        "Scripts:ToggleScriptConsole()"),
    _page("cheat_page", TOOLS, N_("common.cheat_page"), N_("common.opens_cheat_overlay_retail"),
        "Cheat:ToggleInGameDebugCheatPage()"),
    _page("irrigation_reinit", TOOLS, N_("common.rebuild_irrigation"), N_("common.irrigation_grids_built_again"),
        "Irrigation:DebugReInitGrids()"),
)  # fmt: skip
