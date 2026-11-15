"""Anno 2205's and 1800's sun shadow as their own shaders make and read it, for a model viewer: 2205's one shadow map
(the engine's shadow method 0) fitted around the models, drawn by each material's ``Obj2Shadow`` technique; 1800's four
cascades in one map (``ModelObj2Shadow``, an instance per cascade); the members their colour passes read (no Qt in
``fit`` and ``cascades``).

``fit(down, corners)`` / ``cascades(cam, down, corners)`` are the values; ``ShadowMap(ctx, size)`` the depth target and
its pass. What is the engine's and what is chosen: docs/rendering/shader-compile.md section 11, "Shadow".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import gameshader as gs
from . import gameshadow
from .fxcdraw import SharedBuffers
from .nativepass import draw_models

SIZE = 2048  # the shaders' SHADOWMAP_WIDTH / _HEIGHT of the graphics set the viewer compiles with
# the engine's depth bias, in units of the shadow's depth range (0.00015 and 0.00055 over the range)
BIAS = (0.00015, 0.00055)
MARGIN = 1.05  # the fitted box a little larger than the models, so their edges stay inside the map
# the caster's rasterizer bias (ours: the engine's shadow pass state was not read); without it the 12 filter taps
# three texels apart shadow every sloped face of a model that fills the map
CASTER_BIAS, CASTER_SLOPE = 64, 2.5


@dataclass(frozen=True)
class Fit:
    """The light's camera: ``view_proj`` (row vectors: clip = p M) for the shadow pass, and ``values`` the members of
    ``cbShadow`` both passes read."""

    view_proj: np.ndarray
    values: dict


def fit(down, corners: np.ndarray, beyond: np.ndarray | None = None) -> Fit:
    """An orthographic light camera along ``down`` (the direction the sunlight travels) that holds every point of
    ``corners`` ([n, 3], world), with depth 0 to 1 over their extent along the light; ``beyond``: receivers that
    cast nothing (2070's sea), whose far end the depth reaches too, so that past the casters they read lit."""
    d = np.asarray(down, float)
    d /= np.linalg.norm(d)
    pts = np.asarray(corners, float)
    centre = (pts.min(axis=0) + pts.max(axis=0)) / 2
    radius = max(1e-3, float(np.linalg.norm(pts - centre, axis=1).max())) * MARGIN
    view = gs.look_at(centre - d * radius * 2, centre)
    proj = np.identity(4)
    proj[0, 0] = proj[1, 1] = 1.0 / radius
    view_proj = view @ proj
    along = pts @ d
    near, far = float(along.min()) - radius * 0.05, float(along.max()) + radius * 0.05
    if beyond is not None and len(beyond):
        far = max(far, float((np.asarray(beyond, float) @ d).max()) + radius * 0.05)
    k = 1.0 / max(1e-6, far - near)
    depth = [*(d * k), -near * k]  # depth = (p . down - near) / (far - near): the plane both passes compare on
    texture = np.array([[0.5, 0, 0, 0], [0, -0.5, 0, 0], [0, 0, 1, 0], [0.5 + 0.5 / SIZE, 0.5 + 0.5 / SIZE, 0, 1]])
    values = {
        "cShadowMethod": 0, "cMtxShadowVP": view_proj, "cMtxShadowVPT0": view_proj @ texture,
        "cVecShadowVPDN": depth, "cVecShadowVPDP": depth, "cShadowBias": [BIAS[0] * k, BIAS[1] * k],
    }  # fmt: skip
    return Fit(view_proj, values)


CASCADE_ROWS = ("cShadowSampleOffsets", "cShadowPartitionDivision")  # cbShadow's arrays, element by element


def cascades(
    cam: gs.Camera, down, corners: np.ndarray, size: int = gameshadow.SIZE, used: int = gameshadow.COUNT
) -> dict:
    """1800's cascades over the casters' ``corners`` ([n, 3], world) seen by ``cam``: ``cbShadow`` and every
    ``cShadowInformation`` member by name, fitted as 117's viewer fits its own (``gameshadow.fit``); ``size`` the
    map's side, ``used`` the cascades read (the rendering quality's: the splits stay, the shadow ends sooner). The
    filter's taps go in texels of the map: 1800's read adds ``cShadowSampleOffsets`` x its radius to the map's
    coordinate (island-1800-2205-view.md 8)."""
    pts = np.asarray(corners, float)
    found = gameshadow.fit(gs.left_handed(cam), (pts.min(0), pts.max(0)), -np.asarray(down, float))
    values = {
        "cUsedShadowPartitions": max(1, min(used, gameshadow.COUNT)), "cShadowMapSize": [size] * 2,
        "cShadowSampleOffsets": [[x / size, y / size, 0, 0] for x, y in gameshadow.SAMPLES],
        "cShadowPartitionDivision": [list(d) for d in gameshadow.DIVISIONS],
    }  # fmt: skip
    for i, k in enumerate(found.cascades):
        info = f"cShadowInformation[{i}]."
        values.update({info + "ShadowMatrix": k.matrix, info + "DepthTransform": [*k.depth_axis, 0.0],
                       info + "MinLS": k.lo, info + "MaxLS": k.hi, info + "DepthRange": list(k.depth_range),
                       info + "SplitDistance": k.split})  # fmt: skip
    return values


def corners(box: tuple, world: np.ndarray) -> np.ndarray:
    """The eight corners of a mesh's box (min, max) placed by ``world`` (column vectors)."""
    lo, hi = (np.asarray(v, float) for v in box)
    pts = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    return (pts @ np.asarray(world, float).T)[:, :3]


class ShadowMap:
    """The depth target the casters are drawn into by their ``Obj2Shadow`` pass (``casters``: models with a
    ``shadow`` draw), read by the colour pass through ``cShadowMap``."""

    def __init__(self, ctx, size: int = SIZE, shared: bool = False) -> None:
        self.ctx, self.size = ctx, size
        self.depth = ctx.depth_renderbuffer((size, size))
        self.fbo = ctx.framebuffer([], self.depth)
        # 2205's and 1800's casters: the frame's shadow values written once into buffers all of them bind
        self.shared = SharedBuffers(ctx) if shared else None

    def render(self, casters: list, values: dict, fill_model) -> None:
        """The casters' shadow pass; ``fill_model(model, c)`` writes a model's own buffers (its instance, its matrices,
        its material) beside ``values`` (with ``shared``: into the pass's frame buffers once)."""
        if self.shared is not None:
            self.shared.begin()

        def prepare(m, rp, signature, batch) -> None:
            if self.shared is not None:
                m.shadow.shared = self.shared

            def fill(c: dict) -> None:
                gs.set_values(c.values(), values, CASCADE_ROWS)
                fill_model(m, c)

            m.shadow.prepare(rp, signature, batch, m.textures(), fill)

        draw_models(self.ctx, self.fbo, casters, prepare, lambda m, cb: m.shadow.draw(cb, (self.size, self.size)),
                    targets=0)  # fmt: skip

    @property
    def native(self):
        return self.depth.native

    def release(self) -> None:
        for t in (self.fbo, self.depth):
            t.release()
        if self.shared is not None:
            self.shared.release()
