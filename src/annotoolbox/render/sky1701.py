"""Anno 1701's sky as its terrain draws it before everything else: ``terrain/sky.fx`` on a generated half sphere around
the camera, coloured by the sky texture's column of the time of day, then two cloud layers by ``terrain/clouds.fx``
just above the camera, scrolling and turning with the time; no depth, a projection of its own.

``CSky1701(ctx, sky_stages, cloud_stages, dummies)`` is the draw (``draw(fbo, size_px, camera, light)`` records it as the
frame's first pass); ``dome`` and ``cloud_quad`` are the meshes, ``sky_values`` / ``cloud_values`` the constants by
name. docs/rendering/shader-compile.md section 13, "Sky, clouds and post".
"""

from __future__ import annotations

import math

import numpy as np

from . import gameshader as gs
from .fxcdraw import CFxcDraw
from .gamedraw import CMesh, CProgram
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


def dome() -> CMesh:
    """The half sphere (position, uv), v 0 at the zenith and 1 at the horizon, as a triangle list."""
    rows = []
    for i in range(RINGS + 1):
        ring_angle = i * math.pi / (2 * RINGS)
        for j in range(SEGMENTS + 1):
            segment_angle = j * 2 * math.pi / SEGMENTS
            ring_sine = math.sin(ring_angle)
            rows.append(
                (
                    RADIUS * ring_sine * math.sin(segment_angle),
                    HEIGHT * math.cos(ring_angle),
                    RADIUS * ring_sine * math.cos(segment_angle),
                    1 - j / SEGMENTS,
                    1 - math.cos(ring_angle),
                )
            )
    data = np.array(rows, "<f4")
    return CMesh(data.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), ring_index(RINGS, SEGMENTS), len(rows))


def cloud_quad() -> CMesh:
    """The cloud layers' quad: corners at +-1 across, uv +-0.5."""
    data = np.array([(x, 0.0, z, x / 2, z / 2) for x, z in ((-1, -1), (1, -1), (-1, 1), (1, 1))], "<f4")
    return CMesh(data.tobytes(), 20, ((0, 7, 3, 0), (4, 7, 2, 12)), np.array([0, 1, 2, 2, 1, 3], np.uint32), 4)


def _turned_only(camera: gs.SCamera) -> np.ndarray:
    """The camera's view without its place: the sky and the clouds go where the camera is."""
    view = np.array(camera.view, float)
    view[3, :3] = 0.0
    return view


def _projection(camera: gs.SCamera) -> np.ndarray:
    """The sky's own projection, after the view's x mirror for a camera in the game's axes (``SCamera.mirror``)."""
    proj = gs.perspective(FOV, camera.size_px[0] / max(1, camera.size_px[1]), NEAR, FAR)
    return gs.X_MIRROR @ proj if camera.mirror else proj


def _translation(x: float, y: float, z: float) -> np.ndarray:
    matrix = np.identity(4)
    matrix[3, :3] = (x, y, z)
    return matrix


def sky_values(light, camera: gs.SCamera) -> dict:
    """The dome's constants (matrices as ours, row vectors, as 1701's column-major declarations take them): its place
    under the camera, the time of day as the texture's column, the light it is tinted by."""
    day = min(1.0, max(0.0, light.time_of_day / 24.0))
    return {"vcMtxWorldViewProj": _translation(0.0, -BELOW, 0.0) @ _turned_only(camera) @ _projection(camera),
            "vcUsrConstant1": [day, 0.0, 0.0, 0.0], "vcLgtAmbient": [*light.ambient, 1.0],
            "vcLgtDiffuse": [*light.diffuse, 1.0]}  # fmt: skip


def _texture_matrix(rotation: np.ndarray, offset) -> np.ndarray:
    """A Direct3D texture matrix of 2D coordinates: ``(u, v, 1)`` times it, the offset in its third row."""
    matrix = np.identity(4)
    matrix[:2, :2] = rotation
    matrix[2, :2] = offset
    return matrix


def tilt(camera: gs.SCamera, angle_rad: float = TILT) -> np.ndarray:
    """The clouds' turn by ``angle`` about the camera's right axis, laid level (a 4x4 of ours, row vectors): a pitched
    camera tilts the layers as a level one does, never squashes them."""
    right = np.array(camera.view, float)[:3, 0] * (1.0, 0.0, 1.0)
    axis = right / max(float(np.linalg.norm(right)), 1e-9)
    cross = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    turn = (
        math.cos(angle_rad) * np.identity(3)
        + math.sin(angle_rad) * cross
        + (1 - math.cos(angle_rad)) * np.outer(axis, axis)
    )
    out = np.identity(4)
    out[:3, :3] = turn.T  # Rodrigues' turn is for column vectors
    return out


def cloud_values(light, camera: gs.SCamera, seconds: float, layer: int) -> dict:
    """One cloud layer's constants (``LAYERS[layer]``) after ``seconds``: the quad scaled, tilted about the camera's
    right axis and lifted above it; the first texture scrolled, the second turned; colour and threshold by the cover.
    The quad's uvs run along the world's x: ``camera`` is in the game's axes (shader-compile.md 11 "The game's own
    axes")."""
    height, scale = LAYERS[layer]
    view = _turned_only(camera)
    world = np.diag([CLOUD_SIZE, 1.0, CLOUD_SIZE, 1.0]) @ tilt(camera, TILT) @ _translation(0.0, height, 0.0)
    offset = (SCROLL * seconds) % 1.0 + 0.5
    turn_angle = TURN * seconds
    turn = np.array([[math.cos(turn_angle), math.sin(turn_angle)], [-math.sin(turn_angle), math.cos(turn_angle)]])
    light_sum = np.asarray(light.ambient, float) + np.asarray(light.diffuse, float)
    return {"vcMtxWorld": world, "vcMtxViewProj": view @ _projection(camera),
            "vcMtxTexture0": _texture_matrix(np.diag([scale, scale]), (offset, offset)),
            "vcMtxTexture1": _texture_matrix(turn, (0.5, 0.5)),
            "pcUsrConstant0": [*(light_sum * min(1.5 - COVER, 1.0)), (1.0 - COVER) * 0.5]}  # fmt: skip


class CSky1701:
    """The dome and the cloud layers (module docstring); ``sky_map`` / ``cloud_map`` the caller's textures (None:
    that part is not drawn), ``clouds`` whether the layers are on."""

    def __init__(self, ctx, sky_stages, cloud_stages, dummies) -> None:
        self.m_ctx = ctx
        self.m_dome = CFxcDraw(
            ctx, CProgram(sky_stages.vertex_stage, sky_stages.pixel_stage), dome(), sky_stages.state, dummies
        )
        quad = cloud_quad()
        self.m_layers = [CFxcDraw(ctx, CProgram(cloud_stages.vertex_stage, cloud_stages.pixel_stage), quad, cloud_stages.state, dummies)
                       for _ in LAYERS]  # fmt: skip
        for draw in (self.m_dome, *self.m_layers):  # through the camera's mirrored projection: their faces swap
            draw.m_is_mirrored = True
        self.m_sky_map = self.m_cloud_map = None
        self.m_clouds = True

    def _draws(self, camera: gs.SCamera, light) -> list:
        if self.m_sky_map is None:
            return []
        out = [(self.m_dome, {"samplerTex0": self.m_sky_map}, sky_values(light, camera))]
        if self.m_clouds and self.m_cloud_map is not None:
            maps = {f"samplerTex{k}": self.m_cloud_map for k in range(3)}
            seconds = float(getattr(light, "seconds", 0.0))
            out += [
                (layer_mesh, maps, cloud_values(light, camera, seconds, k))
                for k, layer_mesh in enumerate(self.m_layers)
            ]
        return out

    def draw(self, framebuffer, size_px: tuple[int, int], camera: gs.SCamera, light) -> bool:
        """The sky as the first pass into ``framebuffer`` (cleared); False when there is nothing to draw yet (its texture
        loads): the caller clears with its own pass then."""
        draws = self._draws(camera, light)
        if not draws:
            return False

        def prepare(render_pass, signature, batch) -> None:
            for draw, maps, values in draws:
                draw.prepare(
                    render_pass,
                    signature,
                    batch,
                    maps,
                    lambda constants, layer_values=values: gs.set_values(constants.values(), layer_values),
                )

        def record(command_buffer) -> None:
            for draw, _maps, _values in draws:
                draw.draw(command_buffer, size_px)

        # the sky's alpha falls to 0.08 at the horizon: it fades into the clear colour, the profile's fog **[I]**
        draw_pass(self.m_ctx, framebuffer, prepare, record, color=(*light.fog, 1.0))
        return True

    def release(self) -> None:
        for draw in (self.m_dome, *self.m_layers):
            draw.release()
