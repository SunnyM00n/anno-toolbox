"""A mod's card as rows of text for the details pane, with no Qt: its ``modinfo`` fields, where it sits in the
profile, what is wrong with it and what the last start said. ``rows`` gives (label, text) pairs, ``html`` the same
as a small table. ``rows`` is the entry; ``docs/mod-manager.md`` "The window".
"""

from __future__ import annotations

from html import escape

from ..infra.i18n import N_, tr
from ..modloader.loadlog import ModRun
from .catalog import LISTED, NEW, ON, ModCard, root_label
from .checks import PROBLEM, Issue

SETUP_TEXT = {
    "RequiresNewGame": N_("mods.needs_new_game"),
    "SafeToRemove": N_("mods.safe_remove_save"),
    "Multiplayer": N_("mods.usable_multiplayer"),
    "Campaign": N_("mods.usable_campaign"),
}
HOW = {LISTED: N_("mods.listed_profile"), NEW: N_("mods.not_listed_through_enablenewmods")}


def _state_text(card: ModCard, state: tuple[str, str] | None) -> str:
    if card.problem:
        return tr("mods.not_mod_loader_takes", why=card.problem)
    if card.parked:
        return tr("mods.parked_folder_name_starts_loader")
    if card.duplicate_of:
        return tr("mods.left_out_loader_keeps_copy", folder=card.duplicate_of)
    if card.mod is not None and card.mod.parent is not None:
        return tr("mods.comes_with_id", id=card.mod.top.mod_id)
    if state is None:
        return ""
    on, how = state
    return (tr("mods.on") if on == ON else tr("mods.off")) + (
        f", {tr(HOW[how])}" if how in HOW else ", " + tr("mods.not_listed")
    )


def rows(
    card: ModCard,
    state: tuple[str, str] | None = None,
    issues: list[Issue] | None = None,
    run: ModRun | None = None,
) -> list[tuple[str, str]]:
    """The details of ``card``: (label, text), empty texts left out."""
    out: list[tuple[str, str]] = [(tr("common.name"), card.name)]
    if card.mod is not None:
        out.append(("ModID", card.mod_id))
    out += [
        (tr("common.version"), card.version),
        (tr("mods.category"), card.category),
        (tr("mods.folder"), str(card.folder)),
    ]
    out += [(tr("mods.found_in"), root_label(card.root)), (tr("mods.in_the_profile"), _state_text(card, state))]
    out += [(tr("mods.description"), card.description), (tr("common.difficulty"), card.difficulty)]
    if card.mod is not None:
        setup = "; ".join(
            f"{tr(SETUP_TEXT[k])}: {tr('mods.yes') if v else tr('mods.no')}" for k, v in card.setup.items()
        )
        out.append((tr("mods.game_setup"), setup))
        out += [(f"{kind}", ", ".join(ids)) for kind, ids in card.dependencies.items()]
        if card.mod.options:
            out.append((tr("common.options"), ", ".join(f"{k} = {v}" for k, v in card.mod.options.items())))
    if card.children:
        out.append((tr("mods.nested_mods"), ", ".join(c.mod_id or c.folder.name for c in card.children)))
    for issue in issues or ():
        out.append((tr("mods.problem") if issue.level == PROBLEM else tr("mods.note"), f"{issue.code}: {issue.text}"))
    if run is not None:
        said = tr("mods.loaded") if run.loaded else (tr("mods.enabled_not_loaded") if run.enabled else "")
        if run.seconds is not None:
            said += " " + tr("mods.in_seconds_s", seconds=f"{run.seconds:.1f}")
        out.append((tr("mods.last_start"), said))
        out += [(tr("common.log"), line) for line in run.problems]
    return [(k, v) for k, v in out if v]


def html(table: list[tuple[str, str]]) -> str:
    """``rows`` as an HTML table (the labels bold, the texts escaped)."""
    body = "".join(
        f"<tr><td style='padding-right:12px;vertical-align:top'><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>"
        for k, v in table
    )
    return f"<table cellspacing='2'>{body}</table>"
