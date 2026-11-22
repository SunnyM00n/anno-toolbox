"""Anno 2070's sky as its sky manager draws it: ``sky.fx``'s ``Sky`` technique on a sphere around the camera at the far
plane less 3, behind every model (depth tested, not written), its colour the ambient set's zenith, horizon and sun
glow and three samples of the engine's noise volume as clouds. Anno 1404's ``Sky`` takes the same sphere with its
scattering and a cloud cube (``frame1404``).

``dome()`` is the sphere, ``sky_values(sun)`` the constants by name, ``read_volume(files)`` the noise volume's slices,
``CSkyDome(ctx, stages, dummies)`` the draw; ``CSkyCube(ctx, stages, dummies)`` the sky rendered into the cube the
water reflects (``SkyCubeMap``). docs/rendering/shader-compile.md section 10, "Sky" and "The ocean's waves".
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QColor,
    QRhiColorAttachment,
    QRhiDepthStencilClearValue,
    QRhiTexture,
    QRhiTextureRenderTargetDescription,
)

from ..models import ddsdecode
from . import gameshader as gs
from .fxcdraw import CFxcDraw, volume_of
from .gamedraw import CMesh, CProgram

FX = "data/system/shaders/fx/sky.fx"
TECHNIQUE = "Sky"
CUBE_TECHNIQUE = "SkyCubeMap"  # its first pass: the scattering (the second writes grey)
CUBE_SIDE = 64  # RT_SKY_CUBEMAP: 64 x 64, four half floats, one level
VOLUME = "data/graphics/effects/disaster/noisevolume.dds"  # the sky manager's cloud noise (a 3D texture)
RINGS, SEGMENTS = 16, 32  # the engine's sphere of its lower quality (the higher one doubles both)
# the two cloud layers' turn, radians per second: -1e-5 and -5e-6 per millisecond (shader-compile.md 13, "The old
# engine's cloud turn")
CLOUD_TURNS = (-0.01, -0.005)
# the six faces' cMtxSkyCubeFace as (yaw, pitch) of D3DXMatrixRotationYawPitchRoll, in Direct3D's face order
FACE_TURNS = ((math.pi / 2, 0.0), (-math.pi / 2, 0.0), (0.0, -math.pi / 2), (0.0, math.pi / 2), (0.0, 0.0),
              (math.pi, 0.0))  # fmt: skip
NO_CLOUDS = (0, 0, 0, 0)  # the cloud map's stand-in


def ring_index(rings: int, segments: int) -> np.ndarray:
    """The triangles of a sphere's ``rings`` x ``segments`` quads over (rings + 1) x (segments + 1) vertices, ring
    by ring: the old engines' sky domes (2070's and 1404's ``dome``, 1701's ``sky1701.dome``)."""
    index = []
    for i in range(rings):
        for j in range(segments):
            lower, upper = i * (segments + 1) + j, (i + 1) * (segments + 1) + j
            index += [lower, upper, lower + 1, lower + 1, upper, upper + 1]
    return np.array(index, np.uint32)


def dome(rings: int = RINGS, segments: int = SEGMENTS) -> CMesh:
    """A unit sphere of ``rings`` x ``segments`` quads, positions only (``p3``)."""
    vertices = []
    for i in range(rings + 1):
        polar = math.pi * i / rings
        for j in range(segments + 1):
            angle = 2 * math.pi * j / segments
            vertices.append((math.sin(polar) * math.cos(angle), math.cos(polar), math.sin(polar) * math.sin(angle)))
    data = np.array(vertices, "<f4")
    return CMesh(data.tobytes(), 12, ((0, 7, 3, 0),), ring_index(rings, segments), len(vertices))


def sky_values(sun) -> dict:
    """The sky's own constants (``frame2070.SSun2070``): the clouds' colour and density, their two turns."""
    return {"cCloudColor": [*sun.cloud_color, sun.cloud_density], **cloud_turns(sun.seconds)}


def rotation_y(angle_rad: float) -> np.ndarray:
    """Direct3D's ``D3DXMatrixRotationY`` (row vectors, 3 x 3)."""
    cosine, sine = math.cos(angle_rad), math.sin(angle_rad)
    return np.array([[cosine, 0.0, -sine], [0.0, 1.0, 0.0], [sine, 0.0, cosine]])


def yaw_pitch(yaw_rad: float, pitch_rad: float) -> np.ndarray:
    """Direct3D's ``D3DXMatrixRotationYawPitchRoll`` without roll (row vectors, 3 x 3): the pitch about x, then the
    yaw about y."""
    cosine, sine = math.cos(pitch_rad), math.sin(pitch_rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, cosine, sine], [0.0, -sine, cosine]]) @ rotation_y(yaw_rad)


def cloud_turns(seconds: float) -> dict:
    """``cMtxCloudRotate0`` and ``cMtxCloudRotate1`` after ``seconds``: the sky managers of 1404 and 2070 turn the two
    cloud layers about the up axis by ``CLOUD_TURNS`` radians per second (``rotation_y``, ``gs.as_columns``), in the
    game's axes as the passes draw (shader-compile.md 11 "The game's own axes")."""
    return {f"cMtxCloudRotate{k}": gs.as_columns(rotation_y(rate * seconds)) for k, rate in enumerate(CLOUD_TURNS)}


def face_values(face: int) -> dict:
    """``cMtxSkyCubeFace`` of one of the cube's faces (``FACE_TURNS``)."""
    return {"cMtxSkyCubeFace": gs.as_columns(yaw_pitch(*FACE_TURNS[face]))}


def face_quad() -> CMesh:
    """The sky manager's quad over a face: (-1, 1), (1, 1), (-1, -1), (1, -1) at z 0 (``p3``), drawn as a strip."""
    corners = np.array([[-1, 1, 0], [1, 1, 0], [-1, -1, 0], [1, -1, 0]], "<f4")
    return CMesh(corners.tobytes(), 12, ((0, 7, 3, 0),), np.array([0, 1, 2, 2, 1, 3], np.uint32), 4)


def read_volume(files) -> list[np.ndarray]:
    """In the background: the noise volume's first level, slice by slice (RGBA bytes), as stored."""
    dds = ddsdecode.CDdsFile(files.read(VOLUME))
    return [np.ascontiguousarray(dds.level(0, 0, z)) for z in range(dds.slices(0))]


class CSkyDome:
    """The sky's draw (``stages``: the technique's ``SPassStages``) and its clouds once they are read: 2070's noise
    volume (``set_volume``, its own) or 1404's cloud cube (``cube``, the caller's); the old engine's games share it."""

    def __init__(self, ctx, stages, dummies) -> None:
        self.m_ctx = ctx
        # both faces: from inside the sphere each view ray meets one face in front, whichever way it is wound
        state = replace(stages.state, cull="none")
        self.m_draw_call = CFxcDraw(ctx, CProgram(stages.vertex_stage, stages.pixel_stage), dome(), state, dummies)
        # the clouds' density is the map's alpha (2070's noise volume, 1404's cube): none while it loads or with the
        # clouds switched off, not a white overcast
        self.m_draw_call.m_fallbacks = {"cTexture0": NO_CLOUDS}
        self.m_volume = self.m_cube = None

    def set_volume(self, slices: list[np.ndarray]) -> None:
        if self.m_volume is None:
            self.m_volume = volume_of(self.m_ctx, slices)

    def maps(self) -> dict:
        found = self.m_volume if self.m_volume is not None else self.m_cube
        return {"cTexture0": found} if found is not None else {}

    def release(self) -> None:
        self.m_draw_call.release()
        if self.m_volume is not None:
            self.m_ctx.later(self.m_volume)
            self.m_volume = None


class CSkyCube:
    """The sky as the sky manager renders it each frame into ``RT_SKY_CUBEMAP`` for the water to reflect: the
    ``SkyCubeMap`` technique (``stages``) once per face over ``face_quad`` with that face's ``cMtxSkyCubeFace``, the
    frame's light and the clouds of the noise volume (``set_volume``); gamma values, mirrored at the horizon."""

    def __init__(self, ctx, stages, dummies) -> None:
        self.m_ctx = ctx
        state = replace(stages.state, cull="none", depth_test=False, depth_write=False)
        program, quad = CProgram(stages.vertex_stage, stages.pixel_stage), face_quad()
        self.m_draws = [CFxcDraw(ctx, program, quad, state, dummies) for _face in range(6)]
        for draw in self.m_draws:
            draw.m_fallbacks = {"cTexture0": NO_CLOUDS}
        self.m_volume = None
        self.m_texture = None  # the cube, made on first use
        self.m_targets: list = []  # per face (render target, its pass descriptor)

    def set_volume(self, slices: list[np.ndarray]) -> None:
        if self.m_volume is None:
            self.m_volume = volume_of(self.m_ctx, slices)

    def _targets(self) -> None:
        if self.m_texture is not None:
            return
        rhi = self.m_ctx.m_rhi
        flags = QRhiTexture.Flag.CubeMap | QRhiTexture.Flag.RenderTarget
        self.m_texture = rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(CUBE_SIDE, CUBE_SIDE), 1, flags)
        self.m_texture.create()
        for face in range(6):
            attachment = QRhiColorAttachment(self.m_texture)
            attachment.setLayer(face)
            description = QRhiTextureRenderTargetDescription()
            description.setColorAttachments([attachment])
            render_target = rhi.newTextureRenderTarget(description)
            pass_descriptor = render_target.newCompatibleRenderPassDescriptor()
            render_target.setRenderPassDescriptor(pass_descriptor)
            render_target.create()
            self.m_targets.append((render_target, pass_descriptor))

    def render(self, fill) -> QRhiTexture:
        """The six faces as passes of the frame (``fill(c)``: the camera, light and sky values by name, as the dome
        takes them); the cube, to bind as ``tSkyEnv``."""
        self._targets()
        maps = {"cTexture0": self.m_volume} if self.m_volume is not None else {}
        size = (CUBE_SIDE, CUBE_SIDE)
        for face, (draw, (render_target, pass_descriptor)) in enumerate(zip(self.m_draws, self.m_targets, strict=True)):

            def run(
                command_buffer,
                batch,
                draw=draw,
                render_target=render_target,
                pass_descriptor=pass_descriptor,
                face=face,
            ) -> None:
                draw.prepare(
                    pass_descriptor,
                    (1, 1),
                    batch,
                    maps,
                    lambda constants: (fill(constants), gs.set_values(constants.values(), face_values(face))),
                )
                command_buffer.beginPass(
                    render_target, QColor.fromRgbF(0, 0, 0, 0), QRhiDepthStencilClearValue(1.0, 0), batch
                )
                draw.draw(command_buffer, size)
                command_buffer.endPass()

            self.m_ctx.native_pass(run)
        return self.m_texture

    def release(self) -> None:
        for draw in self.m_draws:
            draw.release()
        for render_target, pass_descriptor in self.m_targets:
            self.m_ctx.later(render_target)
            self.m_ctx.later(pass_descriptor)
        self.m_targets = []
        for texture in (self.m_texture, self.m_volume):
            if texture is not None:
                self.m_ctx.later(texture)
        self.m_texture = self.m_volume = None
