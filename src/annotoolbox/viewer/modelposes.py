"""Where the model renderer's parts stand and how its skins are posed in a frame: a part's place by its motion at the
view's time, a part hung on a joint of another model, a skinned group moved to its animation's pose (on the GPU as a
texture of joint matrices, else on the CPU).

``ModelPoses`` is a mixin of ``modelrender.ModelRenderer``, which has the ``files``, the parts ``by_node``, the frame's
cache ``_frame``, the ``_rigs``, the frame's motion environment ``_motion`` and ``_ask`` these read.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from ..models import skinning
from ..render.camera import MIRROR
from .modelscene import Part
from .motion import Env, Linked, at_time

if TYPE_CHECKING:
    from .modelrender import ViewState


class ModelPoses:
    """The places and poses of a frame (module docstring)."""

    def place(self, part, state: ViewState, depth: int = 0) -> np.ndarray | None:
        """Where a part (or a placed effect) stands this frame: its rest place, or its motion at ``state.time``."""
        if not part.motion:
            return part.matrix
        key = ("place", part.node)
        if key not in self._frame:
            env = getattr(self, "_motion", None)
            self._frame[key] = at_time(part.motion, state.time, lambda link: self._joint(link, state, depth), env)
        return self._frame[key]

    def _motion_env(self, state: ViewState, view: np.ndarray, lt: dict) -> Env:
        """What an ANIMATION_TRANSFORM reads this frame, in the model's space (the view's x mirrored): the state, the
        light, the wind (its angle the clouds' stand-in, ``gameclouds.CALM``: a model view has no weather), the
        camera's axes."""
        from ..render import gameclouds

        cam = np.linalg.inv(view)[:3, :3]
        angle = gameclouds.CALM.angle
        sun = MIRROR[:3, :3] @ np.asarray(lt["sunDir"], float)
        return Env(state.states, tuple(sun), (math.cos(angle), math.sin(angle)), MIRROR[:3, :3] @ cam, state.clock)

    def _clip(self, node: str, state: ViewState):
        """(the animation, its time) a part plays this frame; (None, 0) for its rest pose."""
        file, start, *rest = (*state.animation.get(node, ("", 0.0)), 1.0, 0)
        if not file:
            return None, 0.0
        anim = self._ask("anim:" + file, lambda: self.files.animation(file))
        t = skinning.clip_time(state.time - start, anim.duration_ms, rest[0], int(rest[1])) if anim else None
        return (anim, t) if t is not None else (None, 0.0)

    def _joint(self, link: Linked, state: ViewState, depth: int) -> np.ndarray | None:
        """The place of the joint a part hangs on: its model's place this frame and the joint posed in it."""
        found = self.by_node.get(link.host)
        if found is None or depth > 8:
            return None
        k, host = found
        if host.node not in self._rigs:
            mesh = self._ask(f"mesh:{k}", lambda: self._mesh_of(host))
            if mesh is None:
                return None
            skeletons = mesh.rdm.skeletons if getattr(mesh, "rdm", None) is not None else []
            self._rigs[host.node] = skinning.Rig(skeletons[0]) if skeletons else None
        rig, at = self._rigs[host.node], self.place(host, state, depth + 1)
        joint = (rig.hashes.get(link.hash) if link.hash is not None else link.index) if rig is not None else None
        if at is None or joint is None or not 0 <= joint < len(rig.joints):
            return None
        key = ("joints", host.node)
        if key not in self._frame:
            self._frame[key] = rig.world(*self._clip(host.node, state))
        return at @ self._frame[key][joint]

    def _pose(self, part: Part, item: dict, state: ViewState) -> None:
        """A skinned group moved to the animation's pose at ``state.time`` (the bind pose without an animation)."""
        anim, seconds = self._clip(part.node, state)
        mesh, skeletons = item["skinned"], item["file"].rdm.skeletons if item["file"].rdm else []
        key = (id(anim), round(seconds, 3)) if anim is not None else None
        if key == item["pose"] or mesh.skeleton is None or mesh.skeleton >= len(skeletons):
            return
        if "rig" not in item:
            item["rig"] = skinning.Rig(skeletons[mesh.skeleton])
        matrices = item["rig"].pose(anim, seconds)
        item["pose"], item["matrices"] = key, matrices
        if item["gpu"]:
            columns = np.ascontiguousarray(matrices.transpose(0, 2, 1), "f4")  # a texel a column, a row a joint
            if item["joints"] is None or item["joints"].height != max(1, len(columns)):
                item["joints"] = self.ctx.texture((4, max(1, len(columns))), 4, dtype="f4")
                item["joints"].filter = (self.ctx.NEAREST, self.ctx.NEAREST)
            if len(columns):
                item["joints"].write(columns.tobytes())
            item["posed"] = None
            return
        posed = skinning.skin(mesh, matrices)
        item["vbo"].write(np.ascontiguousarray(posed, "f4").tobytes())
        item["posed"] = posed

    def _bind_joints(self, prog, item: dict) -> None:
        """A skin posed on the GPU: its joints' texture for the program drawing it."""
        if item.get("gpu") and "jointTex" in prog:
            (item["joints"] or self._rest_joints).use(6)
            prog["jointTex"].value = 6

    def _posed(self, item: dict) -> np.ndarray:
        """The vertices as drawn this frame (a GPU skin posed on the CPU for the asking: a click)."""
        if item["gpu"] and item["matrices"] is not None:
            return skinning.skin(item["skinned"], item["matrices"])
        return item["posed"] if item["posed"] is not None else item["verts"]
