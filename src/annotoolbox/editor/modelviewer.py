"""The models of 2070, 1404 and 1701 in the island editor's views, read from the games' own files: a mixin of the
islands that answers the view's ``model`` and ``banner`` from a ``modelread.ModelConfig`` and its ``mesh_glb``
from the meshes (``RdmModels``: 2070's ``.rdm``; ``grannyviewer.GrannyModels``: the Granny files of 1404 and 1701).

``model_cfg`` decides per object config whether it draws or stays a marker (a missing or unreadable config or
mesh, said in the Log through ``model_notes``). island-editor "Other games".
"""

from __future__ import annotations

import re
import threading

from ..formats.rdmglb import rdm_to_glb
from ..formats.rdmread import RdmReadError
from ..infra.buildcache import write_atomic
from ..infra.i18n import N_, tr
from ..models import cfg2070, modelread
from ..models.modelread import Leaves, ModelConfig, Piece, Place
from ..render.particlefx import load_efc

NOTES_LISTED = 12  # models named one by one in the Log; the rest are counted
MODEL_SHADER, WATER_SHADER, BANNER_SHADER = 8, 7, 28  # the view's shaders (modelparts.model_parts)


def part(mesh: str, textures: tuple[str, str], color, shader: int, place: Place) -> dict:
    """One part in the shape of ``modelparts.model_parts``."""
    return {
        "mesh": mesh,
        "diffuse": textures[0],
        "normal": textures[1],
        "color": list(color),
        "shader": shader,
        "flow": 0.0,
        "flipbook": None,
        "position": list(place.position),
        "rotation": list(place.rotation),
        "scale": list(place.scale),
    }


def leaves_part(leaves: Leaves) -> dict:
    """The part of a leaf cluster: on the banner shader, its mesh named ``<cfg>|leaves<index>``
    (``leaf_cards`` gives the cards)."""
    return part(f"{leaves.cfg}|leaves{leaves.index}", (leaves.diffuse, ""), leaves.color, BANNER_SHADER, leaves.place)


def leaves_ref(mesh: str) -> tuple[str, int] | None:
    """(cfg, index) of a mesh name ``leaves_part`` gave, None for a mesh part."""
    m = re.fullmatch(r"(.+)\|leaves(\d+)", mesh)
    return (m.group(1), int(m.group(2))) if m and not m.group(1).endswith((".gr2", ".rdm")) else None


def leaf_cards(text: str, index: int) -> dict:
    """The cards of a config's ``index``-th leaf cluster in the shape the view's banner shader takes
    (``rdmbanner.read_banner``): per corner the card's centre, the corner offset, the outward direction, the uv."""
    out: dict[str, list] = {"positions": [], "corners": [], "radial": [], "uvs": [], "indices": []}
    for k, card in enumerate(modelread.cards(cfg2070.parse_cfg(text).billboard_clusters[index])):
        for (sx, sy), (u, v) in zip(modelread.CORNERS, card.uvs, strict=True):
            out["positions"] += card.position
            out["corners"] += [sx * card.reach, sy * card.reach]
            out["radial"] += card.radial
            out["uvs"] += [u, v]
        out["indices"] += [4 * k, 4 * k + 1, 4 * k + 2, 4 * k, 4 * k + 2, 4 * k + 3]
    return out


def rdm_parts(piece: Piece) -> list[dict]:
    """The parts of a 2070 piece, one per material (the mesh named ``<rdm>|<material index>``); a terrain
    cut-out draws nothing, water draws on the view's water shader."""
    out = []
    for k, s in enumerate(piece.surfaces or (modelread.Surface("", "", ""),)):
        if s.kind == "cutout":
            continue
        color = tuple(a * b for a, b in zip(s.color, piece.color, strict=True))
        shader = WATER_SHADER if s.kind == "water" else MODEL_SHADER
        out.append(part(f"{piece.mesh}|{k}", (s.diffuse, s.normal), color, shader, piece.place))
    return out


class ConfigModels:
    """Models read from the games' configs. A subclass sets ``tree2070`` (the game's files) and implements
    ``model_config``, ``_mesh_problem``, ``_piece_parts`` and ``mesh_glb``; the cache and locks are
    ``IslandEditor``'s."""

    def model_config(self, cfg: str) -> ModelConfig:
        """What a model config draws: its pieces, the configs it nests, its leaf clusters."""
        raise NotImplementedError

    def _mesh_problem(self, mesh: str) -> str:
        """Why a mesh cannot be drawn ("" when it can, as far as can be told without decoding it)."""
        raise NotImplementedError

    def _piece_parts(self, piece: Piece) -> list[dict]:
        raise NotImplementedError

    def resolver_source(self) -> tuple[object, str]:
        """The game's files and key the models' names are looked up in (``IslandEditor.resolver``)."""
        game = getattr(self, "game", None)
        return self.tree2070, game.key if game is not None else "anno2070"

    def textures_of(self, cfg: str):
        """A texture name of ``cfg`` -> what the view loads (``Resolver.shown``: a missing one draws as missing)."""
        resolve = self.resolver()
        return lambda name: resolve.shown(name, owner=cfg)

    def file_problems(self) -> list[tuple[str, str, str]]:
        rows = super().file_problems()
        return rows + [(mesh, why, "") for mesh, why in sorted(self._model_state()[1].items()) if why]

    def _model_state(self) -> tuple[dict, dict, dict, threading.Lock]:
        if not hasattr(self, "_models_read"):
            self._models_read = ({}, {}, {}, threading.Lock())  # cfg -> problem, mesh -> problem, decoded, guard
        return self._models_read

    def model_cfg(self, cfg: str) -> str:
        """``cfg`` when it draws something, else "" (the object is a marker; ``model_notes`` says why)."""
        problems, _bad, _decoded, guard = self._model_state()
        with guard:
            if cfg in problems:
                return "" if problems[cfg] else cfg
        problems[cfg] = self._problem(cfg)
        return "" if problems[cfg] else cfg

    def _problem(self, cfg: str) -> str:
        try:
            model = self.model_config(cfg)
        except Exception as exc:  # a config the reader does not take: a marker, said in the Log
            return tr("islands.config_not_read", error=f"{type(exc).__name__}: {exc}")
        if not model.pieces and not model.leaves and not model.files:
            return tr("islands.config_names_no_mesh")
        found = [self._mesh_problem(p.mesh) for p in model.pieces]
        return found[0] if all(found) and not model.leaves and not model.files else ""

    def model_notes(self) -> list[str]:
        """The configs drawn as markers and the meshes left out of the others, and why, for the Log."""
        problems, bad = self._model_state()[:2]
        notes = []
        for title, rows in (
            (N_("islands.model_configs_drawn_markers"), sorted((c, why) for c, why in problems.items() if why)),
            (N_("islands.meshes_left_out_models"), sorted((m, why) for m, why in bad.items() if why)),
        ):
            if rows:
                notes.append(tr(title, n=len(rows)))
                notes += [tr("islands.name_why", name=name, why=why) for name, why in rows[:NOTES_LISTED]]
                if len(rows) > NOTES_LISTED:
                    notes.append(tr("islands.and_n_more", n=len(rows) - NOTES_LISTED))
        return notes

    def model(self, cfg: str) -> dict:
        model = self.model_config(cfg)
        parts = [p for piece in model.pieces if not self._mesh_problem(piece.mesh) for p in self._piece_parts(piece)]
        parts += [leaves_part(leaves) for leaves in model.leaves]
        nested = [
            {"cfg": path, "position": list(at.position), "rotation": list(at.rotation), "scale": list(at.scale)}
            for path, at in model.files
        ]
        effects = [
            {
                "file": e.file,
                "position": list(e.place.position),
                "rotation": list(e.place.rotation),
                "scale": list(e.place.scale),
                "color": list(e.color) if e.color else None,
                "started": e.started,
            }
            for e in model.effects
        ]
        return {"parts": parts, "files": nested, "effects": effects}

    def effect(self, path: str) -> list:
        """The sprite emitters of a 2070 effect file (``particlefx``); what is not a sprite is listed in ``notes``."""
        tree = self.tree2070
        found, left = load_efc(tree.read_text(path), path, self.textures_of(path))
        for why in left:
            note = tr("islands.effect_left_out", why=why)
            if note not in self.notes:
                self.notes.append(note)
        return found

    def effect_texture_png(self, key: str) -> bytes:
        """An effect's texture (the game file ``effect`` named) as PNG."""
        return self.texture_png(key)

    def banner(self, ref: str) -> dict:
        """A leaf cluster's cards (``<cfg>|leaves<index>``), which the view turns to the camera."""
        found = leaves_ref(ref)
        if found is None:
            raise FileNotFoundError(tr("islands.not_leaf_cluster", ref=ref))
        return leaf_cards(self.tree2070.read_text(found[0]), found[1])


class RdmModels(ConfigModels):
    """2070's models: configs read with ``modelread`` (textures found as the game finds them), ``.rdm`` meshes
    drawn one part per material."""

    def model_config(self, cfg: str) -> ModelConfig:
        return modelread.read_config(self.tree2070.read_text(cfg), self.textures_of(cfg), cfg)

    def _mesh_problem(self, mesh: str) -> str:
        bad = self._model_state()[1]
        if mesh not in bad:
            bad[mesh] = "" if self.tree2070.exists(mesh) else tr("islands.not_games_files")
        return bad[mesh] and f"{mesh}: {bad[mesh]}"

    def _piece_parts(self, piece: Piece) -> list[dict]:
        return rdm_parts(piece)

    def mesh_glb(self, ref: str) -> bytes:
        """One material of a 2070 mesh (``<rdm>|<material index>``) as binary glTF, made once."""
        rdm, _, material = ref.rpartition("|")
        out = self._cached(ref + "|rdm2070", ".glb")
        with self._lock(out):
            if not out.is_file():
                try:
                    write_atomic(out, rdm_to_glb(self.tree2070.read_bytes(rdm), int(material)))
                except (RdmReadError, ValueError) as exc:
                    raise FileNotFoundError(tr("islands.cannot_read_mesh", ref=ref, error=exc)) from exc
        return out.read_bytes()
