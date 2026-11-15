"""A mod's card as rows of text for the details pane, with no Qt: its ``modinfo`` fields, where it sits in the
profile, what is wrong with it and what the last start said. ``rows`` gives (label, text) pairs, ``html`` the same
as a small table. ``rows`` is the entry; ``docs/mod-manager.md`` "The window".
"""

from __future__ import annotations

from html import escape

from ..infra.i18n import N_, tr
from ..modloader.loadlog import ModRun
from .catalog import LISTED, NEW, ON, ModCard
from .checks import Issue

SETUP_TEXT = {
    "RequiresNewGame": N_("needs a new game"),
    "SafeToRemove": N_("safe to remove from a save"),
    "Multiplayer": N_("usable in multiplayer"),
    "Campaign": N_("usable in the campaign"),
}
HOW = {LISTED: N_("listed in the profile"), NEW: N_("not listed, on through EnableNewMods")}


def _state_text(card: ModCard, state: tuple[str, str] | None) -> str:
    if card.problem:
        return tr("not a mod the loader takes: {why}", why=card.problem)
    if card.parked:
        return tr("parked: the folder name starts with - or ., the loader skips it")
    if card.duplicate_of:
        return tr("left out: the loader keeps the copy in {folder}", folder=card.duplicate_of)
    if card.mod is not None and card.mod.parent is not None:
        return tr("comes with {id}", id=card.mod.top.mod_id)
    if state is None:
        return ""
    on, how = state
    return (tr("on") if on == ON else tr("off")) + (f", {tr(HOW[how])}" if how in HOW else ", " + tr("not listed"))


def rows(
    card: ModCard,
    state: tuple[str, str] | None = None,
    issues: list[Issue] | None = None,
    run: ModRun | None = None,
) -> list[tuple[str, str]]:
    """The details of ``card``: (label, text), empty texts left out."""
    out: list[tuple[str, str]] = [(tr("Name"), card.name)]
    if card.mod is not None:
        out.append(("ModID", card.mod_id))
    out += [(tr("Version"), card.version), (tr("Category"), card.category), (tr("Folder"), str(card.folder))]
    out += [(tr("Found in"), card.root.label), (tr("In the profile"), _state_text(card, state))]
    out += [(tr("Description"), card.description), (tr("Difficulty"), card.difficulty)]
    if card.mod is not None:
        setup = "; ".join(f"{tr(SETUP_TEXT[k])}: {tr('yes') if v else tr('no')}" for k, v in card.setup.items())
        out.append((tr("Game setup"), setup))
        out += [(f"{kind}", ", ".join(ids)) for kind, ids in card.dependencies.items()]
        if card.mod.options:
            out.append((tr("Options"), ", ".join(f"{k} = {v}" for k, v in card.mod.options.items())))
    if card.children:
        out.append((tr("Nested mods"), ", ".join(c.mod_id or c.folder.name for c in card.children)))
    for issue in issues or ():
        out.append((tr("Problem") if issue.level == "problem" else tr("Note"), f"{issue.code}: {issue.text}"))
    if run is not None:
        said = tr("loaded") if run.loaded else (tr("enabled, not loaded") if run.enabled else "")
        if run.seconds is not None:
            said += " " + tr("in {seconds} s", seconds=f"{run.seconds:.1f}")
        out.append((tr("Last start"), said))
        out += [(tr("Log"), line) for line in run.problems]
    return [(k, v) for k, v in out if v]


def html(table: list[tuple[str, str]]) -> str:
    """``rows`` as an HTML table (the labels bold, the texts escaped)."""
    body = "".join(
        f"<tr><td style='padding-right:12px;vertical-align:top'><b>{escape(k)}</b></td><td>{escape(v)}</td></tr>"
        for k, v in table
    )
    return f"<table cellspacing='2'>{body}</table>"
