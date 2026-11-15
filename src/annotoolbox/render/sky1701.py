"""Anno 1701's sky as its terrain draws it before everything else: ``terrain/sky.fx`` on a generated half sphere around
the camera, coloured by the sky texture's column of the time of day, then two cloud layers by ``terrain/clouds.fx``
just above the camera, scrolling and turning with the time; no depth, a projection of its own.

``Sky1701(ctx, sky_stages, cloud_stages, dummies)`` is the draw (``draw(fbo, size, cam, light)`` records it as the
frame's first pass); ``dome`` and ``cloud_quad`` are the meshes, ``sky_values`` / ``cloud_values`` the constants by
name. docs/rendering/shader-compile.md section 13, "Sky, clouds and post".
"""

from __future__ import annotations

import math

import numpy as np

from . import gameshader as gs
from .fxcdraw import FxcDraw
from .gamedraw import Mesh, Program
from .nativepass import draw_pass
from .sky2070 import ring_index

SKY_FX = "data/system/fxshaders/terrain/sky.fx"
CLOUDS_FX = "data/system/fxshaders/terrain/clouds.fx"
TECHNIQUE = "Object"
# the default sky preset's textures (data/levels/presets/default.prs, as their stored first level)
SKY_TEXTURE = "data/graphics/textures/skybox/enviroment_01_blue_0.dds"
CLOUD_TEXTURE = "data/graphics/textures/skybox/cloud_01_blue_0.dds"
# the engine's half sphere: 11 x 11 vertices, scaled 4 across and 4.2 up, placed 0.5 below the camera
RINGS, SEGMENTS = 10, 10
RADIUS, HEIGHT, BELOW = 4.0, 4.2, 0.5
FOV, NEAR, FAR = 0.75, 1.0, 7000.0  # the sky's own projection (radians, world units)
# the cloud layers in their drawing order: height above the camera, the scrolling layer's scale
LAYERS = ((13.0, 4.0), (11.0, 2.0))
CLOUD_SIZE = 128.0  # the quad's half width
TILT = math.pi / 32  # the layers' tilt about the camera's right axis
SCROLL, TURN = 0.005, 0.04  # per second: the first texture's offset, the second's turn (radians)
COVER = 0.0  # the cloud cover without weather


def dome() -> Mesh:
    """The half sphere (position, uv), v 0 at the zenith and 1 at the horizon, as a triangle list."""
    rows = []
    for i in range(RINGS + 1):
        t = i * math.pi / (2 * RINGS)
        for j in range(SEGMENTS + 1):
            p = j * 2 * math.pi / SEGMENTS
            s = math.sin(t)
            rows.append((RADIUS * s * math.sin(p), HEIGHT * math.cos(t), RADIUS * s * math.cos(p), 1 - j / SEGMENTS,
                         1 - math.cos(t)))  # fmt: skip
    data = np.array(rows, "<f4")
    return Mesh(data.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), ring_index(RINGS, SEGMENTS), len(rows))


def cloud_quad() -> Mesh:
    """The cloud layers' quad: corners at +-1 across, uv +-0.5."""
    data = np.array([(x, 0.0, z, x / 2, z / 2) for x, z in ((-1, -1), (1, -1), (-1, 1), (1, 1))], "<f4")
    return Mesh(data.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), np.array([0, 1, 2, 2, 1, 3], np.uint32), 4)


def _turned_only(cam: gs.Camera) -> np.ndarray:
    """The camera's view without its place: the sky and the clouds go where the camera is."""
    view = np.array(cam.view, float)
    view[3, :3] = 0.0
    return view


def _projection(cam: gs.Camera) -> np.ndarray:
    """The sky's own projection, after the view's x mirror for a camera in the game's axes (``Camera.mirror``)."""
    proj = gs.perspective(FOV, cam.size[0] / max(1, cam.size[1]), NEAR, FAR)
    return gs.X_MIRROR @ proj if cam.mirror else proj


def _translation(x: float, y: float, z: float) -> np.ndarray:
    m = np.identity(4)
    m[3, :3] = (x, y, z)
    return m


def sky_values(light, cam: gs.Camera) -> dict:
    """The dome's constants (matrices as ours, row vectors, as 1701's column-major declarations take them): its place
    under the camera, the time of day as the texture's column, the light it is tinted by."""
    day = min(1.0, max(0.0, light.time_of_day / 24.0))
    return {"vcMtxWorldViewProj": _translation(0.0, -BELOW, 0.0) @ _turned_only(cam) @ _projection(cam),
            "vcUsrConstant1": [day, 0.0, 0.0, 0.0], "vcLgtAmbient": [*light.ambient, 1.0],
            "vcLgtDiffuse": [*light.diffuse, 1.0]}  # fmt: skip


def _texture_matrix(rotation: np.ndarray, offset) -> np.ndarray:
    """A Direct3D texture matrix of 2D coordinates: ``(u, v, 1)`` times it, the offset in its third row."""
    m = np.identity(4)
    m[:2, :2] = rotation
    m[2, :2] = offset
    return m


def tilt(cam: gs.Camera, angle: float = TILT) -> np.ndarray:
    """The clouds' turn by ``angle`` about the camera's right axis, laid level (a 4x4 of ours, row vectors): a pitched
    camera tilts the layers as a level one does, never squashes them."""
    right = np.array(cam.view, float)[:3, 0] * (1.0, 0.0, 1.0)
    k = right / max(float(np.linalg.norm(right)), 1e-9)
    cross = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    turn = math.cos(angle) * np.identity(3) + math.sin(angle) * cross + (1 - math.cos(angle)) * np.outer(k, k)
    out = np.identity(4)
    out[:3, :3] = turn.T  # Rodrigues' turn is for column vectors
    return out


def cloud_values(light, cam: gs.Camera, seconds: float, layer: int) -> dict:
    """One cloud layer's constants (``LAYERS[layer]``) after ``seconds``: the quad scaled, tilted about the camera's
    right axis and lifted above it; the first texture scrolled, the second turned; colour and threshold by the cover.
    The quad's uvs run along the world's x: ``cam`` is in the game's axes (shader-compile.md 11 "The game's own
    axes")."""
    height, scale = LAYERS[layer]
    view = _turned_only(cam)
    world = np.diag([CLOUD_SIZE, 1.0, CLOUD_SIZE, 1.0]) @ tilt(cam, TILT) @ _translation(0.0, height, 0.0)
    offset = (SCROLL * seconds) % 1.0 + 0.5
    a = TURN * seconds
    turn = np.array([[math.cos(a), math.sin(a)], [-math.sin(a), math.cos(a)]])
    light_sum = np.asarray(light.ambient, float) + np.asarray(light.diffuse, float)
    return {"vcMtxWorld": world, "vcMtxViewProj": view @ _projection(cam),
            "vcMtxTexture0": _texture_matrix(np.diag([scale, scale]), (offset, offset)),
            "vcMtxTexture1": _texture_matrix(turn, (0.5, 0.5)),
            "pcUsrConstant0": [*(light_sum * min(1.5 - COVER, 1.0)), (1.0 - COVER) * 0.5]}  # fmt: skip


class Sky1701:
    """The dome and the cloud layers (module docstring); ``sky_map`` / ``cloud_map`` the caller's textures (None:
    that part is not drawn), ``clouds`` whether the layers are on."""

    def __init__(self, ctx, sky_stages, cloud_stages, dummies) -> None:
        self.ctx = ctx
        self.dome = FxcDraw(ctx, Program(sky_stages.vs, sky_stages.ps), dome(), sky_stages.state, dummies)
        quad = cloud_quad()
        self.layers = [FxcDraw(ctx, Program(cloud_stages.vs, cloud_stages.ps), quad, cloud_stages.state, dummies)
                       for _ in LAYERS]  # fmt: skip
        for draw in (self.dome, *self.layers):  # through the camera's mirrored projection: their faces swap
            draw.mirrored = True
        self.sky_map = self.cloud_map = None
        self.clouds = True

    def _draws(self, cam: gs.Camera, light) -> list:
        if self.sky_map is None:
            return []
        out = [(self.dome, {"samplerTex0": self.sky_map}, sky_values(light, cam))]
        if self.clouds and self.cloud_map is not None:
            maps = {f"samplerTex{k}": self.cloud_map for k in range(3)}
            seconds = float(getattr(light, "seconds", 0.0))
            out += [(d, maps, cloud_values(light, cam, seconds, k)) for k, d in enumerate(self.layers)]
        return out

    def draw(self, fbo, size: tuple[int, int], cam: gs.Camera, light) -> bool:
        """The sky as the first pass into ``fbo`` (cleared); False when there is nothing to draw yet (its texture
        loads): the caller clears with its own pass then."""
        draws = self._draws(cam, light)
        if not draws:
            return False

        def prepare(rp, signature, batch) -> None:
            for draw, maps, values in draws:
                draw.prepare(rp, signature, batch, maps, lambda c, v=values: gs.set_values(c.values(), v))

        def record(cb) -> None:
            for draw, _maps, _values in draws:
                draw.draw(cb, size)

        # the sky's alpha falls to 0.08 at the horizon: it fades into the clear colour, the profile's fog **[I]**
        draw_pass(self.ctx, fbo, prepare, record, color=(*light.fog, 1.0))
        return True

    def release(self) -> None:
        for draw in (self.dome, *self.layers):
            draw.release()
