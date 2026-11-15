"""The asset editor's share of a project's mod (``suite.project.Part``): the change set of the project's game
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
from ..suite.project import ARCHIVE_GAMES, Part, Project
from . import modops, native
from .assetgame import open_installed
from .changeset import ChangeSet


def change_set_path(project: Project) -> Path:
    return project.asset_edits / f"{project.game}.json"


def has_work(project: Project) -> bool:
    path = change_set_path(project)
    if not path.is_file():
        return False
    c = ChangeSet.load(path)
    return bool(c.assets or c.new or c.removed or c.carry)  # its texts are the Texts part's (one writer)


def render(project: Project, settings, log: Callable[[str], None]) -> dict[str, bytes]:
    changes = replace(ChangeSet.load(change_set_path(project)), texts={})  # the Texts part writes every text
    game = open_installed(settings, project.game, log)
    doc = game.document(changes)
    if project.game in ARCHIVE_GAMES:  # the asset files whole, by game path; the delivery packs them
        out = native.render(doc)
        log(f"assets: {len(out)} asset files written whole")
        return out
    out = {path: text.encode("utf-8") for path, text in modops.render(doc, game.layout).items()}
    written = {p.lower() for p in out}
    for rel, src in modops.carried_files(changes).items():
        if rel.lower() not in written:  # never over a file the change set wrote (as ``modops.carry``)
            out[rel] = src.read_bytes()
    log(f"assets: {len(changes.assets)} changed, {len(changes.new)} new, {len(changes.removed)} removed")
    return out


PART = Part("assets", N_("Assets"), has_work, render)
