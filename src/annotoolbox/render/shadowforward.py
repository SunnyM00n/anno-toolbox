"""Anno 2205's and 1800's sun shadow as their own shaders make and read it, for a model viewer: 2205's one shadow map
(the engine's shadow method 0) fitted around the models, drawn by each material's ``Obj2Shadow`` technique; 1800's four
cascades in one map (``ModelObj2Shadow``, an instance per cascade); the members their colour passes read (no Qt in
``fit`` and ``cascades``).

``fit(down, corners)`` / ``cascades(camera, down, corners)`` are the values; ``CShadowMap(ctx, size)`` the depth target and
its pass. What is the engine's and what is chosen: docs/rendering/shader-compile.md section 11, "Shadow".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import gameshader as gs
from . import gameshadow
from .fxcdraw import CSharedBuffers
from .nativepass import draw_models

SIZE = 2048  # the shaders' SHADOWMAP_WIDTH / _HEIGHT of the graphics set the viewer compiles with
# the engine's depth bias, in units of the shadow's depth range (0.00015 and 0.00055 over the range)
BIAS = (0.00015, 0.00055)
MARGIN = 1.05  # the fitted box a little larger than the models, so their edges stay inside the map
# the caster's rasterizer bias (ours: the engine's shadow pass state was not read); without it the 12 filter taps
# three texels apart shadow every sloped face of a model that fills the map
CASTER_BIAS, CASTER_SLOPE = 64, 2.5


@dataclass(frozen=True)
class SFit:
    """The light's camera: ``view_proj`` (row vectors: clip = p M) for the shadow pass, and ``values`` the members of
    ``cbShadow`` both passes read."""

    view_proj: np.ndarray
    values: dict


def fit(down, corners: np.ndarray, beyond: np.ndarray | None = None) -> SFit:
    """An orthographic light camera along ``down`` (the direction the sunlight travels) that holds every point of
    ``corners`` ([n, 3], world), with depth 0 to 1 over their extent along the light; ``beyond``: receivers that
    cast nothing (2070's sea), whose far end the depth reaches too, so that past the casters they read lit."""
    down_vector = np.array(
        down, float
    )  # a copy: the division below must not turn the caller's own vector into a unit one
    down_vector /= np.linalg.norm(down_vector)
    points = np.asarray(corners, float)
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    radius = max(1e-3, float(np.linalg.norm(points - centre, axis=1).max())) * MARGIN
    view = gs.look_at(centre - down_vector * radius * 2, centre)
    projection = np.identity(4)
    projection[0, 0] = projection[1, 1] = 1.0 / radius
    view_proj = view @ projection
    along = points @ down_vector
    near, far = float(along.min()) - radius * 0.05, float(along.max()) + radius * 0.05
    if beyond is not None and len(beyond):
        far = max(far, float((np.asarray(beyond, float) @ down_vector).max()) + radius * 0.05)
    k = 1.0 / max(1e-6, far - near)
    depth = [
        *(down_vector * k),
        -near * k,
    ]  # depth = (p . down - near) / (far - near): the plane both passes compare on
    texture = np.array([[0.5, 0, 0, 0], [0, -0.5, 0, 0], [0, 0, 1, 0], [0.5 + 0.5 / SIZE, 0.5 + 0.5 / SIZE, 0, 1]])
    values = {
        "cShadowMethod": 0, "cMtxShadowVP": view_proj, "cMtxShadowVPT0": view_proj @ texture,
        "cVecShadowVPDN": depth, "cVecShadowVPDP": depth, "cShadowBias": [BIAS[0] * k, BIAS[1] * k],
    }  # fmt: skip
    return SFit(view_proj, values)


CASCADE_ROWS = ("cShadowSampleOffsets", "cShadowPartitionDivision")  # cbShadow's arrays, element by element


def cascades(
    camera: gs.SCamera, down, corners: np.ndarray, size_px: int = gameshadow.SIZE, used: int = gameshadow.COUNT
) -> dict:
    """1800's cascades over the casters' ``corners`` ([n, 3], world) seen by ``camera``: ``cbShadow`` and every
    ``cShadowInformation`` member by name, fitted as 117's viewer fits its own (``gameshadow.fit``); ``size_px`` the
    map's side, ``used`` the cascades read (the rendering quality's: the splits stay, the shadow ends sooner). The
    filter's taps go in texels of the map: 1800's read adds ``cShadowSampleOffsets`` x its radius to the map's
    coordinate (island-1800-2205-view.md 8)."""
    points = np.asarray(corners, float)
    found = gameshadow.fit(gs.left_handed(camera), (points.min(0), points.max(0)), -np.asarray(down, float))
    values = {
        "cUsedShadowPartitions": max(1, min(used, gameshadow.COUNT)), "cShadowMapSize": [size_px] * 2,
        "cShadowSampleOffsets": [[x / size_px, y / size_px, 0, 0] for x, y in gameshadow.SAMPLES],
        "cShadowPartitionDivision": [list(down_vector) for down_vector in gameshadow.DIVISIONS],
    }  # fmt: skip
    for i, k in enumerate(found.cascades):
        prefix = f"cShadowInformation[{i}]."
        values.update({prefix + "ShadowMatrix": k.matrix, prefix + "DepthTransform": [*k.depth_axis, 0.0],
                       prefix + "MinLS": k.min_corner, prefix + "MaxLS": k.max_corner, prefix + "DepthRange": list(k.depth_range),
                       prefix + "SplitDistance": k.split})  # fmt: skip
    return values


def corners(box: tuple, world: np.ndarray) -> np.ndarray:
    """The eight corners of a mesh's box (min, max) placed by ``world`` (column vectors)."""
    min_box, max_box = (np.asarray(vector, float) for vector in box)
    points = np.array(
        [
            [x, y, z, 1.0]
            for x in (min_box[0], max_box[0])
            for y in (min_box[1], max_box[1])
            for z in (min_box[2], max_box[2])
        ]
    )
    return (points @ np.asarray(world, float).T)[:, :3]


class CShadowMap:
    """The depth target the casters are drawn into by their ``Obj2Shadow`` pass (``casters``: models with a
    ``shadow`` draw), read by the colour pass through ``cShadowMap``."""

    def __init__(self, ctx, size_px: int = SIZE, shared: bool = False) -> None:
        self.m_ctx, self.m_size_px = ctx, size_px
        self.m_depth = ctx.depth_renderbuffer((size_px, size_px))
        self.m_fbo = ctx.framebuffer([], self.m_depth)
        # 2205's and 1800's casters: the frame's shadow values written once into buffers all of them bind
        self.m_shared = CSharedBuffers(ctx) if shared else None

    def render(self, casters: list, values: dict, fill_model) -> None:
        """The casters' shadow pass; ``fill_model(model, c)`` writes a model's own buffers (its instance, its matrices,
        its material) beside ``values`` (with ``shared``: into the pass's frame buffers once)."""
        if self.m_shared is not None:
            self.m_shared.begin()

        def prepare(model, render_pass, signature, batch) -> None:
            if self.m_shared is not None:
                model.m_shadow.m_shared = self.m_shared

            def fill(constants: dict) -> None:
                gs.set_values(constants.values(), values, CASCADE_ROWS)
                fill_model(model, constants)

            model.m_shadow.prepare(render_pass, signature, batch, model.textures(), fill)

        side = (self.m_size_px, self.m_size_px)
        draw_models(self.m_ctx, self.m_fbo, casters, prepare,
                    lambda model, command_buffer: model.m_shadow.draw(command_buffer, side), targets=0)  # fmt: skip

    @property
    def m_native(self):
        return self.m_depth.native

    def release(self) -> None:
        for texture in (self.m_fbo, self.m_depth):
            texture.release()
        if self.m_shared is not None:
            self.m_shared.release()
