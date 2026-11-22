"""The asset editor's share of a project's mod (``suite.project.SPart``): the change set of the project's game
(``asset-edits/<game>.json``) as ModOps, with the files an imported mod carries.

``PART`` is what ``suite.apps.parts`` lists; ``render`` opens the game (``assetgame.open_installed``, the index in
the shared cache) and writes the patches of ``modops.render``; for the older games the asset files whole
(``native.render``). docs/tool-suite.md, C4.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ..infra.i18n import N_
from ..suite import appfolders
from ..suite.project import ARCHIVE_GAMES, CProject, SPart
from . import modops, native
from .assetgame import open_installed
from .changeset import CChangeSet


def change_set_path(project: CProject) -> Path:
    return project.asset_edits / f"{project.game}.json"


def has_work(project: CProject) -> bool:
    if appfolders.layered_record(project) is not None:
        return True  # a layered project's Assets part writes the mod's patch file (with or without the include)
    path = change_set_path(project)
    if not path.is_file():
        return False
    changes = CChangeSet.load(path)
    return bool(
        changes.m_assets or changes.m_new or changes.m_removed or changes.m_carry
    )  # its texts are the Texts part's (one writer)


def render(project: CProject, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    if appfolders.layered_record(project) is not None:
        from . import layered  # the layered way builds on top of the mod's own patch file (docs/mod-import.md, 3.5 B)

        return layered.render(project, settings, log)
    changes = replace(CChangeSet.load(change_set_path(project)), m_texts={})  # the Texts part writes every text
    game = open_installed(settings, project.game, log)
    doc = game.document(changes)
    if project.game in ARCHIVE_GAMES:  # the asset files whole, by game path; the delivery packs them
        out = native.render(doc)
        log(f"assets: {len(out)} asset files written whole")
        return out
    out = {path: text.encode("utf-8") for path, text in modops.render(doc, game.m_layout).items()}
    written = {written_path.lower() for written_path in out}
    for relative_path, source in modops.carried_files(changes).items():
        if relative_path.lower() not in written:  # never over a file the change set wrote (as ``modops.carry``)
            out[relative_path] = source.read_bytes()
    log(f"assets: {len(changes.m_assets)} changed, {len(changes.m_new)} new, {len(changes.m_removed)} removed")
    return out


PART = SPart("assets", N_("common.assets"), has_work, render)
