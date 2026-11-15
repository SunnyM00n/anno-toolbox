"""The models of the games whose meshes are Granny files (1404, 1701) for the island editor's views: the
``modelviewer.ConfigModels`` of ``gameviewers.IsdViewer`` whose meshes are ``.gr2`` files (``formats.grannyglb``),
one part per material group; the configs are read by ``models.modelread`` (1404) and ``othergames.grannymodels``
(1701), the textures are the game's own files, read by ``Island2070Editor.texture_png``. island-editor "Other games".
"""

from __future__ import annotations

import json

from ..formats.grannyglb import GrannyMesh, meshes_to_glb, read_meshes
from ..formats.grannyread import GrannyError, GrannyFile
from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..models.modelread import ANY, Piece
from .modelviewer import MODEL_SHADER, ConfigModels, part

MESHES_KEPT = 16  # decoded .gr2 files kept in memory while their materials are written one by one


def piece_parts(piece: Piece, groups: list[str]) -> list[dict]:
    """The editor's parts of a piece, one per material group of its mesh (``groups``: the ``.gr2``'s
    material names); the mesh is named ``<gr2>|<material>``."""
    out = []
    for name in dict.fromkeys(groups):
        textures = piece.materials.get(name.lower()) or piece.materials.get(ANY) or ("", "")
        if not textures[0] and len(piece.materials) == 1:  # one material in the config: it is the mesh's
            textures = next(iter(piece.materials.values()))
        out.append(part(f"{piece.mesh}|{name}", textures, piece.color, MODEL_SHADER, piece.place))
    return out


class GrannyModels(ConfigModels):
    """Models whose meshes are Granny files. A subclass implements ``model_config``."""

    def _mesh_problem(self, gr2: str) -> str:
        """Why a ``.gr2`` cannot be drawn ("" when it can, as far as its header says)."""
        bad = self._model_state()[1]
        if gr2 not in bad:
            if not self.tree2070.exists(gr2):
                bad[gr2] = tr("not in the game's files")
            else:
                try:
                    GrannyFile(self.tree2070.read_bytes(gr2)).check()
                    bad[gr2] = ""
                except GrannyError as exc:
                    bad[gr2] = str(exc)
        return bad[gr2] and f"{gr2}: {bad[gr2]}"

    def _piece_parts(self, piece: Piece) -> list[dict]:
        return piece_parts(piece, self._groups(piece.mesh))

    def _meshes(self, gr2: str) -> list[GrannyMesh]:
        """The meshes of a ``.gr2``, decoded once while its parts load."""
        _problems, _bad, meshes, guard = self._model_state()
        with self._lock(self._cached(gr2 + "|granny", ".lock")):
            with guard:
                if gr2 in meshes:
                    return meshes[gr2]
            found = read_meshes(self.tree2070.read_bytes(gr2))
            with guard:
                meshes[gr2] = found
                while len(meshes) > MESHES_KEPT:
                    meshes.pop(next(iter(meshes)))
        return found

    def _groups(self, gr2: str) -> list[str]:
        """The material names of a ``.gr2``'s triangle groups (cached on disk: the second opening decodes nothing)."""
        out = self._cached(gr2 + "|grannygroups", ".json")
        with self._lock(out):
            if not out.is_file():
                try:
                    names = [name for m in self._meshes(gr2) for name, idx in m.groups if idx]
                except GrannyError:
                    names = []  # undrawn; model_cfg has said why when the header was the cause
                write_atomic(out, json.dumps(list(dict.fromkeys(names))).encode("utf-8"))
        return json.loads(out.read_text(encoding="utf-8"))

    def mesh_glb(self, ref: str) -> bytes:
        """One material group of a ``.gr2`` (``<gr2>|<material>``) as binary glTF, made once."""
        gr2, _, material = ref.partition("|")
        out = self._cached(ref + "|grannyglb", ".glb")
        with self._lock(out):
            if not out.is_file():
                try:
                    write_atomic(out, meshes_to_glb(self._meshes(gr2), material))
                except GrannyError as exc:
                    raise FileNotFoundError(tr("cannot read the mesh {ref}: {error}", ref=ref, error=exc)) from exc
        return out.read_bytes()
