"""Saving an edited effect into the project: the three ways (the effect at its own path, a copy named for the model's
config with the config pointed at it, a new file) with the reason each is open or not, and the files each one writes.
No Qt.

``ways(effect)`` lists them; ``plan(effect, way, new_path)`` gives the files by game path, what to tell the person,
what stops it and the project files it replaces. A config goes as a ModOps patch where the game's mod loader patches
configs (``models.cfgpatch``), else whole. docs/particle-editor.md, "How mods replace or add effects".
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ..infra.i18n import N_, tr
from ..models import cfgpatch
from ..models.cfgdoc import SDoc
from ..sources.overlay import normalize
from .effectdoc import CEffectDoc, naming_leaves

REPLACE, COPY, NEW = "replace", "copy", "new"
TITLES = {
    REPLACE: N_("particles.effect_own_path"),
    COPY: N_("particles.copy_model"),
    NEW: N_("particles.new_effect_file"),
}


@dataclass(frozen=True)
class SWay:
    way: str
    path: str
    allowed: bool
    why: str


@dataclass
class SPlan:
    """What a save writes (game path -> bytes), what to tell the person, what stops it, what it takes out."""

    way: str
    files: dict[str, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


def copy_path(effect: CEffectDoc) -> str:
    """``<config folder>/<config name>_<effect name>.<ext>``: the copy one model plays ("" without a model)."""
    if effect.m_model is None:
        return ""
    folder, _separator, name = effect.m_model.asked.rpartition("/")
    stem, extension = effect.title.rsplit(".", 1)
    return f"{folder}/{name.rsplit('.', 1)[0]}_{stem}.{extension}"


def ways(effect: CEffectDoc) -> list[SWay]:
    """The ways the effect can be saved, with their reasons."""
    out = [SWay(REPLACE, effect.m_path, True, tr("particles.games_file_replaced_every_model"))]
    if effect.m_model is None:
        out.append(SWay(COPY, "", False, tr("particles.open_effect_model_file_gt")))
    elif not effect.m_model.blocks:
        out.append(SWay(COPY, "", False, tr("particles.models_config_not_name_effect")))
    else:
        out.append(SWay(COPY, copy_path(effect), True, tr(
            "particles.copy_only_plays_config_pointed", config=effect.m_model.asked,
            how=_config_way(effect)[1]
        )))  # fmt: skip
    out.append(SWay(NEW, "", True, tr("particles.file_path_your_own_plays")))
    return out


def _config_way(effect: CEffectDoc) -> tuple[str, str]:
    """``cfgpatch.PATCH`` or ``REPLACE`` for the model's config, and its words."""
    model = effect.m_model
    found = {
        way.way: way for way in cfgpatch.ways(effect.m_game, model.asked, model.read, effect.m_files.m_base.exists)
    }
    if found[cfgpatch.PATCH].allowed:
        return cfgpatch.PATCH, tr("particles.modops_patch_path", path=found[cfgpatch.PATCH].path)
    return cfgpatch.REPLACE, tr("particles.whole_config_path", path=model.read)


def plan(effect: CEffectDoc, way: str, new_path: str = "") -> SPlan:
    """The files a save in ``way`` writes."""
    out = SPlan(way)
    if not effect.editable:
        out.problems.append(tr("particles.file_not_read_back_byte"))
        return out
    if way == REPLACE:
        if not effect.differs_from_game and not effect.model_changed:
            out.problems.append(tr("particles.nothing_differs_games_own_file"))
            return out
        out.files[effect.m_path] = effect.to_bytes()
        if effect.model_changed:
            _config(effect, effect.m_model.doc, out)
    elif way == COPY:
        target = copy_path(effect)
        if not target or not effect.m_model.blocks:
            out.problems.append(tr("particles.no_model_config_point_copy"))
            return out
        out.files[target] = effect.to_bytes()
        _config(effect, _pointed(effect.m_model.doc, effect.m_path, target), out)
        out.notes.append(tr("particles.the_copy_path", path=target))
    else:
        _new(effect, new_path, out)
    return out


def _new(effect: CEffectDoc, new_path: str, out: SPlan) -> None:
    path = normalize(new_path)
    extension = effect.m_path.rsplit(".", 1)[-1]
    if not path.endswith("." + extension):
        out.problems.append(tr("particles.new_file_keeps_extension", ext=extension))
    elif not path.startswith("data/") or any(part in ("", ".", "..") or ":" in part for part in path.split("/")):
        out.problems.append(tr("particles.not_path_under_games_data", path=new_path))
    elif effect.m_files.m_base.exists(path) and path != effect.m_path:
        out.problems.append(tr("particles.game_file_choose_another_name", path=path))
    else:
        out.files[path] = effect.to_bytes()


def _pointed(doc: SDoc, effect: str, target: str) -> SDoc:
    """A copy of the config with every value naming ``effect`` naming ``target`` instead, in its own slashes."""
    out = copy.deepcopy(doc)
    for leaf in naming_leaves(out, effect):
        leaf.text = target.replace("/", "\\") if "\\" in leaf.text else target
    return out


def _config(effect: CEffectDoc, doc: SDoc, out: SPlan) -> None:
    """The model's config into the plan: as a patch where the game patches configs, else whole."""
    model = effect.m_model
    how, _words = _config_way(effect)
    if how == cfgpatch.PATCH:
        patch = cfgpatch.render_patch(model.base, doc)
        if not patch.problems:
            if patch.text:
                out.files[model.read + ".xml"] = patch.text.encode("utf-8")
            out.stale.append(model.read)
            return
        out.notes += patch.problems
    out.files[model.read] = doc.to_bytes()
    out.stale.append(model.read + ".xml")
