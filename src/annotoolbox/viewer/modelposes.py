"""Where the model renderer's parts stand and how its skins are posed in a frame: a part's place by its motion at the
view's time, a part hung on a joint of another model, a skinned group moved to its animation's pose (on the GPU as a
texture of joint matrices, else on the CPU).

``CModelPoses`` is a mixin of ``modelrender.CModelRenderer``, which has the ``files``, the parts ``by_node``, the frame's
cache ``_frame``, the ``_rigs``, the frame's motion environment ``_motion`` and ``_ask`` these read.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from ..models import skinning
from ..render.camera import MIRROR
from .modelscene import SPart
from .motion import SEnv, SLinked, at_time

if TYPE_CHECKING:
    from .modelrender import SViewState


class CModelPoses:
    """The places and poses of a frame (module docstring)."""

    def place(self, part, state: SViewState, depth: int = 0) -> np.ndarray | None:
        """Where a part (or a placed effect) stands this frame: its rest place, or its motion at ``state.time``."""
        if not part.motion:
            return part.matrix
        key = ("place", part.node)
        if key not in self._m_frame:
            motion_environment = getattr(self, "_m_motion", None)
            self._m_frame[key] = at_time(
                part.motion, state.time, lambda link: self._joint(link, state, depth), motion_environment
            )
        return self._m_frame[key]

    def _motion_env(self, state: SViewState, view: np.ndarray, light_values: dict) -> SEnv:
        """What an ANIMATION_TRANSFORM reads this frame, in the model's space (the view's x mirrored): the state, the
        light, the wind (its angle the clouds' stand-in, ``gameclouds.CALM``: a model view has no weather), the
        camera's axes."""
        from ..render import gameclouds

        cam = np.linalg.inv(view)[:3, :3]
        angle = gameclouds.CALM.angle_rad
        sun = MIRROR[:3, :3] @ np.asarray(light_values["sunDir"], float)
        return SEnv(state.states, tuple(sun), (math.cos(angle), math.sin(angle)), MIRROR[:3, :3] @ cam, state.clock)

    def _clip(self, node: str, state: SViewState):
        """(the animation, its time) a part plays this frame; (None, 0) for its rest pose."""
        file, start, *rest = (*state.animation.get(node, ("", 0.0)), 1.0, 0)
        if not file:
            return None, 0.0
        anim = self._ask("anim:" + file, lambda: self.m_files.animation(file))
        clip_time = skinning.clip_time(state.time - start, anim.duration_ms, rest[0], int(rest[1])) if anim else None
        return (anim, clip_time) if clip_time is not None else (None, 0.0)

    def _joint(self, link: SLinked, state: SViewState, depth: int) -> np.ndarray | None:
        """The place of the joint a part hangs on: its model's place this frame and the joint posed in it."""
        found = self.m_by_node.get(link.host)
        if found is None or depth > 8:
            return None
        k, host = found
        if host.node not in self._m_rigs:
            mesh = self._ask(f"mesh:{k}", lambda: self._mesh_of(host))
            if mesh is None:
                return None
            skeletons = mesh.rdm.skeletons if getattr(mesh, "rdm", None) is not None else []
            self._m_rigs[host.node] = skinning.CRig(skeletons[0]) if skeletons else None
        rig, host_place = self._m_rigs[host.node], self.place(host, state, depth + 1)
        joint = (rig.m_hashes.get(link.hash) if link.hash is not None else link.index) if rig is not None else None
        if host_place is None or joint is None or not 0 <= joint < len(rig.m_joints):
            return None
        key = ("joints", host.node)
        if key not in self._m_frame:
            self._m_frame[key] = rig.world(*self._clip(host.node, state))
        return host_place @ self._m_frame[key][joint]

    def _pose(self, part: SPart, item: dict, state: SViewState) -> None:
        """A skinned group moved to the animation's pose at ``state.time`` (the bind pose without an animation)."""
        anim, seconds = self._clip(part.node, state)
        mesh, skeletons = item["skinned"], item["file"].rdm.skeletons if item["file"].rdm else []
        key = (id(anim), round(seconds, 3)) if anim is not None else None
        if key == item["pose"] or mesh.skeleton is None or mesh.skeleton >= len(skeletons):
            return
        if "rig" not in item:
            item["rig"] = skinning.CRig(skeletons[mesh.skeleton])
        matrices = item["rig"].pose(anim, seconds)
        item["pose"], item["matrices"] = key, matrices
        if item["gpu"]:
            columns = np.ascontiguousarray(matrices.transpose(0, 2, 1), "f4")  # a texel a column, a row a joint
            if item["joints"] is None or item["joints"].height != max(1, len(columns)):
                item["joints"] = self.m_ctx.texture((4, max(1, len(columns))), 4, dtype="f4")
                item["joints"].filter = (self.m_ctx.NEAREST, self.m_ctx.NEAREST)
            if len(columns):
                item["joints"].write(columns.tobytes())
            item["posed"] = None
            return
        posed = skinning.skin(mesh, matrices)
        item["vbo"].write(np.ascontiguousarray(posed, "f4").tobytes())
        item["posed"] = posed

    def _bind_joints(self, prog, item: dict) -> None:
        """A skin posed on the GPU: its joints' texture for the program drawing it."""
        if item.get("gpu") and "jointTex" in self._uniform_names(prog):
            (item["joints"] or self._m_rest_joints).use(6)
            prog["jointTex"].value = 6

    def _posed(self, item: dict) -> np.ndarray:
        """The vertices as drawn this frame (a GPU skin posed on the CPU for the asking: a click)."""
        if item["gpu"] and item["matrices"] is not None:
            return skinning.skin(item["skinned"], item["matrices"])
        return item["posed"] if item["posed"] is not None else item["verts"]
