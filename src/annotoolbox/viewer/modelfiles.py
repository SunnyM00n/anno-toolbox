"""The files a model draws, read from a game's tree and kept: meshes (``.rdm`` and Granny ``.gr2`` as
``rdmmesh.MeshFile``), textures as RGBA (``fileresolve.decode``, a smaller mip for a large file), effects
(``render.particlefx``, their textures found by ``fileresolve``) and animations. Thread-safe; the renderer calls it
from its loading threads.

``ModelFiles(files)`` is the entry; ``mesh``, ``texture``, ``effect`` and ``animation`` raise for a file that does
not read, and the caller says so (the Checks panel lists it). A Granny file's skeletons, skins and animations come in
the RDM records the view's rig plays (``granny_meshes``; ``models.grannyrig``). docs/viewer.md "How files are found".
"""

from __future__ import annotations

import threading

import numpy as np

from ..formats import grannyanim
from ..formats.rdmread import Animation, RdmFile, read_rdm
from ..formats.rdp117 import FRAME_TIME_MS
from ..models import fileresolve
from ..models.fileresolve import TEXTURE_MAX
from ..models.grannyrig import (
    best_skeleton,
    colour_record,
    granny_animation,
    granny_skeleton,
    granny_skin,
    pinned,
    subdivided,
)
from ..models.rdmmesh import MeshData, MeshFile, read_meshes


def granny_meshes(data: bytes, cloth_rounds: int = 0) -> tuple[MeshFile, list[grannyanim.Skeleton]]:
    """A ``.gr2``'s meshes as arrays, their groups keyed by material name (lower case), each skinned to its model's
    skeleton (the file's skeletons as RDM records in ``rdm``); a mesh whose colours pin it as cloth stays rigid,
    keeps its colours (``MeshData.stored``) and is split ``cloth_rounds`` times (``grannyrig.subdivided``). Also
    the skeletons as Granny keeps them, for animations read later."""
    from ..formats import grannyread
    from ..formats.grannyglb import meshes_of

    root = grannyread.GrannyFile(data).root()
    skeletons = grannyanim.read_skeletons(root)
    owners = grannyanim.model_skeletons(root)
    out = MeshFile(rdm=RdmFile(False, "", "granny", skeletons=[granny_skeleton(s) for s in skeletons]))
    for m in meshes_of(root):
        if cloth_rounds and pinned(m.colors):
            m = subdivided(m, cloth_rounds)
        verts = np.concatenate(
            [np.asarray(m.positions, np.float32), np.asarray(m.normals, np.float32), np.asarray(m.uvs, np.float32)], 1
        )
        groups: dict[str, list] = {}
        for name, idx in m.groups:
            groups.setdefault(name.lower(), []).extend(idx)
        mesh = MeshData(m.name, verts, {k: np.asarray(v, np.uint32) for k, v in groups.items()})
        if m.colors is not None:
            mesh.stored = colour_record(m, [i for _n, idx in m.groups for i in idx])
        skin = None if pinned(m.colors) else granny_skin(m, skeletons, owners.get(m.name))
        if skin is not None:
            mesh.skeleton, mesh.joints, mesh.weights = skin
            mesh.bones = 1 if m.bone_weights is None else mesh.joints.shape[1]
        out.meshes.append(mesh)
    return out, skeletons


CLOTH_ROUNDS = {"anno1701": 2}  # 1701's cloth nodes ask for 3 levels of detail (granny.md "Cloth", inferred)


def effects1701(text: str, name: str, texture) -> tuple[list, list[str]]:
    """The drawn emitters of a 1701 effect file as the view's effects, and why the others are left out (``texture``
    turns a texture name into the file the game loads); the counterpart of ``particlefx.load_efc``."""
    from ..formats.efc1701 import read_efc
    from ..models import particles1701
    from ..models.particlebake import BakeError
    from ..render.particlefx import Effect

    out, left = [], []
    for k, emitter in enumerate(read_efc(text)):
        label = f"{name} #{k}"
        try:
            baked = particles1701.bake(emitter, label)
        except BakeError as exc:
            left.append(f"{label}: {exc}")
            continue
        frame_ms = FRAME_TIME_MS / baked.time_scale
        out.append(Effect.from_particles(label, baked.particles, [baked.material], texture(emitter.texture),
                                         baked.frame_count, frame_ms, baked.looped))  # fmt: skip
    return out, left


class ModelFiles:
    """Meshes, textures, effects and animations of one game's tree, each read once."""

    def __init__(self, files, texture_max: int = TEXTURE_MAX) -> None:
        self.files, self.texture_max = files, texture_max
        self._lock = threading.Lock()
        self._kept: dict[tuple[str, str], object] = {}
        self._skeletons: list[grannyanim.Skeleton] = []  # every Granny mesh file's, for its animations

    def _once(self, kind: str, path: str, make):
        key = (kind, path)
        with self._lock:
            if key in self._kept:
                return self._kept[key]
        value = make()
        with self._lock:
            self._kept[key] = value
        return value

    def mesh(self, path: str) -> MeshFile:
        def make() -> MeshFile:
            data = self.files.read(path)
            if not path.endswith(".gr2"):
                return read_meshes(data)
            game = getattr(self.files, "game", "")
            mesh_file, skeletons = granny_meshes(data, CLOTH_ROUNDS.get(game, 0))
            with self._lock:
                self._skeletons += skeletons
            return mesh_file

        return self._once("mesh", path, make)

    def texture(self, path: str, most: int = 0) -> tuple[int, int, int, bytes]:
        """(width, height, 4, RGBA bytes) of a texture, its first level at most ``texture_max`` (and ``most``, the
        rendering quality's largest map, where it is given) texels a side."""
        limit = min(self.texture_max, most) if most else self.texture_max

        def make():
            px = fileresolve.decode(self.files.read(path), limit)
            return px.shape[1], px.shape[0], 4, np.ascontiguousarray(px).tobytes()

        return self._once("texture", f"{limit}:{path}", make)

    def effect(self, path: str) -> list:
        """The effects of a particle file: a 117 ``.rdp``, a 2070 ``.efc`` (its sprite emitters) or a 1701 one (its
        drawn emitters, ``models.particles1701``), their textures the files the game loads (``Resolver.shown``: a
        missing one keeps its name, so it draws as missing)."""
        from ..formats.efc1701 import is_efc1701
        from ..render.particlefx import load_efc, load_rdp

        resolve = fileresolve.Resolver(self.files, getattr(self.files, "game", "") or "anno117", keep=False)

        def make() -> list:
            data = self.files.read(path)
            if path.endswith(".rdp"):
                effect = load_rdp(data, path)
                effect.texture = resolve.shown(effect.texture) if effect.texture else ""
                effect.material_textures = [resolve.shown(t) if t else "" for t in effect.material_textures]
                return [effect]
            if is_efc1701(text := data.decode("latin-1")):
                return effects1701(text, path, resolve.shown)[0]
            found, _left = load_efc(data.decode("utf-8", "replace"), path, resolve.shown)
            return found

        return self._once("effect", path, make)

    def animation(self, path: str) -> Animation | None:
        """The first animation of an animation file (the one the engine plays), None for a file without one. A
        ``.gr2`` is sampled against the skeleton that names most of its bones: its own, else one of a mesh read
        before (the view reads a part's mesh before its animation)."""

        def make():
            data = self.files.read(path)
            if path.endswith(".gr2"):
                return self._granny_animation(data)
            found = read_rdm(data).animations
            return found[0] if found else None

        return self._once("animation", path, make)

    def _granny_animation(self, data: bytes) -> Animation | None:
        from ..formats import grannyread

        root = grannyread.GrannyFile(data).root()
        found = grannyanim.read_animations(root)
        if not found:
            return None
        with self._lock:
            skeletons = [*grannyanim.read_skeletons(root), *self._skeletons]
        return granny_animation(found[0], best_skeleton(found[0], skeletons))
