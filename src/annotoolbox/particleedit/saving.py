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
from ..models.cfgdoc import Doc
from ..sources.overlay import normalize
from .effectdoc import EffectDoc, naming_leaves

REPLACE, COPY, NEW = "replace", "copy", "new"
TITLES = {
    REPLACE: N_("particles.effect_own_path"),
    COPY: N_("particles.copy_model"),
    NEW: N_("particles.new_effect_file"),
}


@dataclass(frozen=True)
class Way:
    way: str
    path: str
    allowed: bool
    why: str


@dataclass
class Plan:
    """What a save writes (game path -> bytes), what to tell the person, what stops it, what it takes out."""

    way: str
    files: dict[str, bytes] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)


def copy_path(effect: EffectDoc) -> str:
    """``<config folder>/<config name>_<effect name>.<ext>``: the copy one model plays ("" without a model)."""
    if effect.model is None:
        return ""
    folder, _s, name = effect.model.asked.rpartition("/")
    stem, ext = effect.title.rsplit(".", 1)
    return f"{folder}/{name.rsplit('.', 1)[0]}_{stem}.{ext}"


def ways(effect: EffectDoc) -> list[Way]:
    """The ways the effect can be saved, with their reasons."""
    out = [Way(REPLACE, effect.path, True, tr("particles.games_file_replaced_every_model"))]
    if effect.model is None:
        out.append(Way(COPY, "", False, tr("particles.open_effect_model_file_gt")))
    elif not effect.model.blocks:
        out.append(Way(COPY, "", False, tr("particles.models_config_not_name_effect")))
    else:
        out.append(Way(COPY, copy_path(effect), True, tr(
            "particles.copy_only_plays_config_pointed", config=effect.model.asked,
            how=_config_way(effect)[1]
        )))  # fmt: skip
    out.append(Way(NEW, "", True, tr("particles.file_path_your_own_plays")))
    return out


def _config_way(effect: EffectDoc) -> tuple[str, str]:
    """``cfgpatch.PATCH`` or ``REPLACE`` for the model's config, and its words."""
    model = effect.model
    found = {w.way: w for w in cfgpatch.ways(effect.game, model.asked, model.read, effect.files.base.exists)}
    if found[cfgpatch.PATCH].allowed:
        return cfgpatch.PATCH, tr("particles.modops_patch_path", path=found[cfgpatch.PATCH].path)
    return cfgpatch.REPLACE, tr("particles.whole_config_path", path=model.read)


def plan(effect: EffectDoc, way: str, new_path: str = "") -> Plan:
    """The files a save in ``way`` writes."""
    out = Plan(way)
    if not effect.editable:
        out.problems.append(tr("particles.file_not_read_back_byte"))
        return out
    if way == REPLACE:
        if not effect.differs_from_game and not effect.model_changed:
            out.problems.append(tr("particles.nothing_differs_games_own_file"))
            return out
        out.files[effect.path] = effect.to_bytes()
        if effect.model_changed:
            _config(effect, effect.model.doc, out)
    elif way == COPY:
        target = copy_path(effect)
        if not target or not effect.model.blocks:
            out.problems.append(tr("particles.no_model_config_point_copy"))
            return out
        out.files[target] = effect.to_bytes()
        _config(effect, _pointed(effect.model.doc, effect.path, target), out)
        out.notes.append(tr("particles.the_copy_path", path=target))
    else:
        _new(effect, new_path, out)
    return out


def _new(effect: EffectDoc, new_path: str, out: Plan) -> None:
    path = normalize(new_path)
    ext = effect.path.rsplit(".", 1)[-1]
    if not path.endswith("." + ext):
        out.problems.append(tr("particles.new_file_keeps_extension", ext=ext))
    elif not path.startswith("data/") or any(p in ("", ".", "..") or ":" in p for p in path.split("/")):
        out.problems.append(tr("particles.not_path_under_games_data", path=new_path))
    elif effect.files.base.exists(path) and path != effect.path:
        out.problems.append(tr("particles.game_file_choose_another_name", path=path))
    else:
        out.files[path] = effect.to_bytes()


def _pointed(doc: Doc, effect: str, target: str) -> Doc:
    """A copy of the config with every value naming ``effect`` naming ``target`` instead, in its own slashes."""
    out = copy.deepcopy(doc)
    for leaf in naming_leaves(out, effect):
        leaf.text = target.replace("/", "\\") if "\\" in leaf.text else target
    return out


def _config(effect: EffectDoc, doc: Doc, out: Plan) -> None:
    """The model's config into the plan: as a patch where the game patches configs, else whole."""
    model = effect.model
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
