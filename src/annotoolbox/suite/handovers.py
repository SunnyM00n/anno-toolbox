"""What the Project window needs to hand an imported mod's carried files to an app (docs/mod-import.md, "Phase 2 as
built"): a ``SHandover`` record an app's ``Part`` carries, so the window never imports the app. Stdlib only.

``SHandover`` holds the two button labels, whether each is offered for a project, and the two actions (the window's own
functions of the app: the first asks and reports, the second undoes). ``suite.apps.parts`` puts the app's record on its
part.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SHandover:
    """``take_label`` / ``undo_label``: string ids (``N_``) of the buttons. ``can_take(project)`` / ``can_undo(project)``
    say whether the button is offered. ``start_take(parent, project, settings, background, say, done, wait, confirm)``
    reads, reports and (on a yes) hands over, then calls ``done``; ``undo(parent, project, say, confirm)`` returns
    whether it was undone. ``more``: other ways the same app takes the files (offered beside this one; a project uses one,
    each ``can_take`` says no once another is taken)."""

    take_label: str
    undo_label: str
    can_take: Callable[[object], bool]
    can_undo: Callable[[object], bool]
    start_take: Callable[..., None]
    undo: Callable[..., bool]
    more: tuple[SHandover, ...] = ()
