"""The window side of the two ways an imported mod's patch files meet the Assets app (docs/mod-import.md, "Phase 2 as
built" and "Phase 6 as built"): the report to confirm, the change and its undo, their results said in the Project
window's log. ``HANDOVER`` (the net change set, ``handover.py``) and ``LAYERED`` (edits on top of the mod,
``layered.py``) are the records the Assets part carries (``suite.handovers``); a ``Way`` is what the two differ in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from PySide6.QtWidgets import QMessageBox, QWidget

from ..infra.i18n import N_, tr
from ..suite import project as projects
from ..suite.background import Background
from ..suite.handovers import Handover
from . import handover, layered
from .assetgame import open_installed

Say = Callable[[str], None]


@dataclass(frozen=True)
class Way:
    """One way of taking the patch files: ``can_take``, ``plan(project, game, say, search)``, ``take(project, plan)`` (the
    sentence to say), ``state(project)``, ``edited_since(project)`` (the words of what the apps changed, empty none),
    ``undo(project, discard)`` (the sentence), the exception a refusal raises and the ids of the sentences."""

    can_take: Callable
    plan: Callable
    take: Callable
    state: Callable
    edited_since: Callable
    undo: Callable
    refused: type
    ids: dict


def _hand_take(project, made) -> str:
    return tr("assets.handover_taken", n=len(handover.take(project, made).files))


def _hand_undo(project, discard: bool) -> str:
    return tr("assets.handover_undone", n=handover.undo(project, discard))


def _layer_take(project, made) -> str:
    return tr("assets.layered_taken", include=layered.take(project, made)["include"])


def _layer_undo(project, discard: bool) -> str:
    layered.undo(project, discard)
    return tr("assets.layered_undone")


def _layer_edited(project) -> list[str]:
    return (
        [tr("assets.handover_edited_assets")]
        if layered.state(project) is not None and layered.has_edits(project)
        else []
    )


HANDING = Way(
    handover.can_take, handover.plan, _hand_take, handover.state, handover.edited_since, _hand_undo,
    handover.HandoverRefused,
    {
        "question": N_("common.handover_question"),
        "undo": N_("common.handover_undo_question"),
        "not_offered": N_("common.handover_not_offered"),
        "failed": N_("common.handover_failed"),
    },
)  # fmt: skip
LAYERING = Way(
    layered.can_take, layered.plan, _layer_take, layered.state, _layer_edited, _layer_undo, layered.LayerRefused,
    {
        "question": N_("common.layered_question"),
        "undo": N_("common.layered_undo_question"),
        "not_offered": N_("common.layered_not_offered"),
        "failed": N_("common.layered_failed"),
    },
)  # fmt: skip


def _confirm_take(parent: QWidget, lines: list[str], question: str = "common.handover_question") -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(tr("common.project"))
    box.setText(tr(question))
    box.setDetailedText(chr(10).join(lines))
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def _confirm_undo(parent: QWidget, what: str, question: str = "common.handover_undo_question") -> bool:
    answer = QMessageBox.question(
        parent,
        tr("common.project"),
        tr(question, what=what),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def start_take(
    parent: QWidget,
    project: projects.Project,
    settings,
    background: Background,
    say: Say,
    done: Callable[[], None],
    wait: bool = False,
    confirm: Callable[[QWidget, list[str]], bool] | None = None,
    way: Way = HANDING,
) -> None:
    """Read the patch files against the installed game off the window's thread, show what the change loses or does and
    ask; only a yes changes anything. ``done`` is called when it ended either way (``wait``: in this thread, for a test)."""
    ask = confirm or partial(_confirm_take, question=way.ids["question"])
    if not way.can_take(project):
        say(tr(way.ids["not_offered"]))
        return done()

    def work():
        game = open_installed(settings, project.game, say)
        return way.plan(project, game, say, handover.mods_folders(settings, project.game))

    def planned(result) -> None:
        try:
            if isinstance(result, Exception):
                say(tr(way.ids["failed"], error=result))
            else:
                lines = result.lines()
                for line in lines:
                    say(line)
                if ask(parent, lines):
                    say(way.take(project, result))
                else:
                    say(tr("common.handover_declined"))
        except way.refused as exc:
            say(str(exc))
        done()

    background.run(work, planned, wait)


def undo(
    parent: QWidget,
    project: projects.Project,
    say: Say,
    confirm: Callable[[QWidget, str], bool] | None = None,
    way: Way = HANDING,
) -> bool:
    """Undo the change; when the apps changed the work since, say what and ask before it is dropped. Whether it was
    undone."""
    ask = confirm or partial(_confirm_undo, question=way.ids["undo"])
    changed = way.edited_since(project)
    try:
        if changed and not ask(parent, ", ".join(changed)):
            say(tr("common.handover_declined"))
            return False
        say(way.undo(project, bool(changed)))
    except way.refused as exc:
        say(str(exc))
        return False
    return True


LAYERED = Handover(
    N_("common.layered_button"),
    N_("common.layered_undo_button"),
    layered.can_take,
    lambda project: layered.state(project) is not None,
    partial(start_take, way=LAYERING),
    partial(undo, way=LAYERING),
)
HANDOVER = Handover(
    N_("common.handover_button"),
    N_("common.handover_undo_button"),
    handover.can_take,
    lambda project: handover.state(project) is not None,
    start_take,
    undo,
    more=(LAYERED,),
)
