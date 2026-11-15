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
TITLES = {REPLACE: N_("The effect at its own path"), COPY: N_("A copy for this model"), NEW: N_("A new effect file")}


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
    out = [Way(REPLACE, effect.path, True, tr("the game's file replaced: every model that places it shows the edit"))]
    if effect.model is None:
        out.append(
            Way(COPY, "", False, tr("open the effect from a model (File > Open from a model) to give it a copy"))
        )
    elif not effect.model.blocks:
        out.append(Way(COPY, "", False, tr("the model's config does not name this effect")))
    else:
        out.append(Way(COPY, copy_path(effect), True, tr(
            "a copy only {config} plays: the config is pointed at it ({how})", config=effect.model.asked,
            how=_config_way(effect)[1]
        )))  # fmt: skip
    out.append(
        Way(NEW, "", True, tr("a file at a path of your own: it plays once a config names it (the object builder)"))
    )
    return out


def _config_way(effect: EffectDoc) -> tuple[str, str]:
    """``cfgpatch.PATCH`` or ``REPLACE`` for the model's config, and its words."""
    model = effect.model
    found = {w.way: w for w in cfgpatch.ways(effect.game, model.asked, model.read, effect.files.base.exists)}
    if found[cfgpatch.PATCH].allowed:
        return cfgpatch.PATCH, tr("a ModOps patch, {path}", path=found[cfgpatch.PATCH].path)
    return cfgpatch.REPLACE, tr("the whole config, {path}", path=model.read)


def plan(effect: EffectDoc, way: str, new_path: str = "") -> Plan:
    """The files a save in ``way`` writes."""
    out = Plan(way)
    if not effect.editable:
        out.problems.append(tr("the file does not read back byte for byte (or is empty), so it is shown but not saved"))
        return out
    if way == REPLACE:
        if not effect.differs_from_game and not effect.model_changed:
            out.problems.append(
                tr("nothing differs from the game's own file: a copy of it would only hide other mods' changes")
            )
            return out
        out.files[effect.path] = effect.to_bytes()
        if effect.model_changed:
            _config(effect, effect.model.doc, out)
    elif way == COPY:
        target = copy_path(effect)
        if not target or not effect.model.blocks:
            out.problems.append(tr("no model config to point at a copy"))
            return out
        out.files[target] = effect.to_bytes()
        _config(effect, _pointed(effect.model.doc, effect.path, target), out)
        out.notes.append(tr("the copy: {path}", path=target))
    else:
        _new(effect, new_path, out)
    return out


def _new(effect: EffectDoc, new_path: str, out: Plan) -> None:
    path = normalize(new_path)
    ext = effect.path.rsplit(".", 1)[-1]
    if not path.endswith("." + ext):
        out.problems.append(tr("a new file keeps the extension .{ext}", ext=ext))
    elif not path.startswith("data/") or any(p in ("", ".", "..") or ":" in p for p in path.split("/")):
        out.problems.append(tr("{path} is not a path under the game's data folder", path=new_path))
    elif effect.files.base.exists(path) and path != effect.path:
        out.problems.append(tr("the game has a file at {path}: choose another name", path=path))
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
