"""The files a model draws, read from a game's tree and kept: meshes (``.rdm`` and Granny ``.gr2`` as
``rdmmesh.SMeshFile``), textures as RGBA (``fileresolve.decode``) or as stored blocks (``render.blocktexture``),
effects (``render.particlefx``, their textures found by ``fileresolve``) and animations. Thread-safe; the renderer
calls it from its loading threads.

``CModelFiles(files)`` is the entry; ``mesh``, ``texture``, ``effect`` and ``animation`` raise for a file that does
not read, and the caller says so (the Checks panel lists it). A Granny file's skeletons, skins and animations come in
the RDM records the view's rig plays (``granny_meshes``; ``models.grannyrig``). docs/viewer.md "How files are found".
"""

from __future__ import annotations

import threading

import numpy as np

from ..formats import grannyanim
from ..formats.rdmread import SAnimation, SRdmFile, read_rdm
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
from ..models.rdmmesh import SMeshData, SMeshFile, read_meshes
from ..render.blocktexture import SBlockMap, stored_blocks


def granny_meshes(data: bytes, cloth_rounds: int = 0) -> tuple[SMeshFile, list[grannyanim.SSkeleton]]:
    """A ``.gr2``'s meshes as arrays, their groups keyed by material name (lower case), each skinned to its model's
    skeleton (the file's skeletons as RDM records in ``rdm``); a mesh whose colours pin it as cloth stays rigid,
    keeps its colours (``SMeshData.stored``) and is split ``cloth_rounds`` times (``grannyrig.subdivided``). Also
    the skeletons as Granny keeps them, for animations read later."""
    from ..formats import grannyread
    from ..formats.grannyglb import meshes_of

    root = grannyread.CGrannyFile(data).root()
    skeletons = grannyanim.read_skeletons(root)
    owners = grannyanim.model_skeletons(root)
    out = SMeshFile(rdm=SRdmFile(False, "", "granny", skeletons=[granny_skeleton(skeleton) for skeleton in skeletons]))
    for granny_mesh in meshes_of(root):
        if cloth_rounds and pinned(granny_mesh.colors):
            granny_mesh = subdivided(granny_mesh, cloth_rounds)
        verts = np.concatenate(
            [
                np.asarray(granny_mesh.positions, np.float32),
                np.asarray(granny_mesh.normals, np.float32),
                np.asarray(granny_mesh.uvs, np.float32),
            ],
            1,
        )
        groups: dict[str, list] = {}
        for name, indices in granny_mesh.groups:
            groups.setdefault(name.lower(), []).extend(indices)
        mesh = SMeshData(
            granny_mesh.name, verts, {k: np.asarray(group_indices, np.uint32) for k, group_indices in groups.items()}
        )
        if granny_mesh.colors is not None:
            mesh.stored = colour_record(granny_mesh, [i for _n, indices in granny_mesh.groups for i in indices])
        skin = None if pinned(granny_mesh.colors) else granny_skin(granny_mesh, skeletons, owners.get(granny_mesh.name))
        if skin is not None:
            mesh.skeleton, mesh.joints, mesh.weights = skin
            mesh.bones = 1 if granny_mesh.bone_weights is None else mesh.joints.shape[1]
        out.meshes.append(mesh)
    return out, skeletons


CLOTH_ROUNDS = {"anno1701": 2}  # 1701's cloth nodes ask for 3 levels of detail (granny.md "Cloth", inferred)


def effects1701(text: str, name: str, texture) -> tuple[list, list[str]]:
    """The drawn emitters of a 1701 effect file as the view's effects, and why the others are left out (``texture``
    turns a texture name into the file the game loads); the counterpart of ``particlefx.load_efc``."""
    from ..formats.efc1701 import read_efc
    from ..models import particles1701
    from ..models.particlebake import CBakeError
    from ..render.particlefx import SEffect

    out, left = [], []
    for k, emitter in enumerate(read_efc(text)):
        label = f"{name} #{k}"
        try:
            baked = particles1701.bake(emitter, label)
        except CBakeError as exc:
            left.append(f"{label}: {exc}")
            continue
        frame_ms = FRAME_TIME_MS / baked.time_scale
        out.append(SEffect.from_particles(label, baked.particles, [baked.material], texture(emitter.texture),
                                         baked.frame_count, frame_ms, baked.looped))  # fmt: skip
    return out, left


class CModelFiles:
    """Meshes, textures, effects and animations of one game's tree, each read once."""

    def __init__(self, files, max_texture_px: int = TEXTURE_MAX) -> None:
        self.m_files, self.m_max_texture_px = files, max_texture_px
        self._m_lock = threading.Lock()
        self._m_kept: dict[tuple[str, str], object] = {}
        self._m_skeletons: list[grannyanim.SSkeleton] = []  # every Granny mesh file's, for its animations

    def _once(self, kind: str, path: str, make):
        key = (kind, path)
        with self._m_lock:
            if key in self._m_kept:
                return self._m_kept[key]
        value = make()
        with self._m_lock:  # two loading threads may both have read it: the first one stored is the one everyone shares
            return self._m_kept.setdefault(key, value)

    def mesh(self, path: str) -> SMeshFile:
        def make() -> SMeshFile:
            data = self.m_files.read(path)
            if not path.endswith(".gr2"):
                return read_meshes(data)
            game = getattr(self.m_files, "m_game", "")
            mesh_file, skeletons = granny_meshes(data, CLOTH_ROUNDS.get(game, 0))
            with self._m_lock:
                self._m_skeletons += skeletons
            return mesh_file

        return self._once("mesh", path, make)

    def texture(self, path: str, most: int = 0, blocks: bool = False) -> tuple[int, int, int, bytes] | SBlockMap:
        """(width, height, 4, RGBA bytes) of a texture, its first level at most ``texture_max`` (and ``most``, the
        rendering quality's largest map, where it is given) texels a side. With ``blocks``, a DDS whose blocks a
        GPU takes comes as they are stored instead (``blocktexture.stored_blocks``), the rest decoded."""
        limit = min(self.m_max_texture_px, most) if most else self.m_max_texture_px

        def make():
            data = self.m_files.read(path)
            if blocks and (stored := stored_blocks(data, limit)) is not None:
                return stored
            pixels = fileresolve.decode(data, limit)
            return pixels.shape[1], pixels.shape[0], 4, np.ascontiguousarray(pixels).tobytes()

        return self._once("blocks" if blocks else "texture", f"{limit}:{path}", make)

    def effect(self, path: str) -> list:
        """The effects of a particle file: a 117 ``.rdp``, a 2070 ``.efc`` (its sprite emitters) or a 1701 one (its
        drawn emitters, ``models.particles1701``), their textures the files the game loads (``CResolver.shown``: a
        missing one keeps its name, so it draws as missing)."""
        from ..formats.efc1701 import is_efc1701
        from ..render.particlefx import load_efc, load_rdp

        def make() -> list:
            resolve = fileresolve.CResolver(
                self.m_files, getattr(self.m_files, "m_game", "") or "anno117", m_keep=False
            )
            data = self.m_files.read(path)
            if path.endswith(".rdp"):
                effect = load_rdp(data, path)
                effect.texture = resolve.shown(effect.texture) if effect.texture else ""
                effect.material_textures = [
                    resolve.shown(texture) if texture else "" for texture in effect.material_textures
                ]
                return [effect]
            if is_efc1701(text := data.decode("latin-1")):
                return effects1701(text, path, resolve.shown)[0]
            found, _left = load_efc(data.decode("utf-8", "replace"), path, resolve.shown)
            return found

        return self._once("effect", path, make)

    def animation(self, path: str) -> SAnimation | None:
        """The first animation of an animation file (the one the engine plays), None for a file without one. A
        ``.gr2`` is sampled against the skeleton that names most of its bones: its own, else one of a mesh read
        before (the view reads a part's mesh before its animation)."""

        def make():
            data = self.m_files.read(path)
            if path.endswith(".gr2"):
                return self._granny_animation(data)
            found = read_rdm(data).animations
            return found[0] if found else None

        return self._once("animation", path, make)

    def _granny_animation(self, data: bytes) -> SAnimation | None:
        from ..formats import grannyread

        root = grannyread.CGrannyFile(data).root()
        found = grannyanim.read_animations(root)
        if not found:
            return None
        with self._m_lock:
            skeletons = [*grannyanim.read_skeletons(root), *self._m_skeletons]
        return granny_animation(found[0], best_skeleton(found[0], skeletons))
