"""Anno 2070's sky as its sky manager draws it: ``sky.fx``'s ``Sky`` technique on a sphere around the camera at the far
plane less 3, behind every model (depth tested, not written), its colour the ambient set's zenith, horizon and sun
glow and three samples of the engine's noise volume as clouds. Anno 1404's ``Sky`` takes the same sphere with its
scattering and a cloud cube (``frame1404``).

``dome()`` is the sphere, ``sky_values(sun)`` the constants by name, ``read_volume(files)`` the noise volume's slices,
``SkyDome(ctx, stages, dummies)`` the draw; ``SkyCube(ctx, stages, dummies)`` the sky rendered into the cube the
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
from .fxcdraw import FxcDraw, volume_of
from .gamedraw import Mesh, Program

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
            a, b = i * (segments + 1) + j, (i + 1) * (segments + 1) + j
            index += [a, b, a + 1, a + 1, b, b + 1]
    return np.array(index, np.uint32)


def dome(rings: int = RINGS, segments: int = SEGMENTS) -> Mesh:
    """A unit sphere of ``rings`` x ``segments`` quads, positions only (``p3``)."""
    verts = []
    for i in range(rings + 1):
        polar = math.pi * i / rings
        for j in range(segments + 1):
            a = 2 * math.pi * j / segments
            verts.append((math.sin(polar) * math.cos(a), math.cos(polar), math.sin(polar) * math.sin(a)))
    data = np.array(verts, "<f4")
    return Mesh(data.tobytes(), 12, ((0, 7, 3, 0),), ring_index(rings, segments), len(verts))


def sky_values(sun) -> dict:
    """The sky's own constants (``frame2070.Sun2070``): the clouds' colour and density, their two turns."""
    return {"cCloudColor": [*sun.cloud_color, sun.cloud_density], **cloud_turns(sun.seconds)}


def rotation_y(a: float) -> np.ndarray:
    """Direct3D's ``D3DXMatrixRotationY`` (row vectors, 3 x 3)."""
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, -s], [0.0, 1.0, 0.0], [s, 0.0, c]])


def yaw_pitch(yaw: float, pitch: float) -> np.ndarray:
    """Direct3D's ``D3DXMatrixRotationYawPitchRoll`` without roll (row vectors, 3 x 3): the pitch about x, then the
    yaw about y."""
    c, s = math.cos(pitch), math.sin(pitch)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, s], [0.0, -s, c]]) @ rotation_y(yaw)


def cloud_turns(seconds: float) -> dict:
    """``cMtxCloudRotate0`` and ``cMtxCloudRotate1`` after ``seconds``: the sky managers of 1404 and 2070 turn the two
    cloud layers about the up axis by ``CLOUD_TURNS`` radians per second (``rotation_y``, ``gs.as_columns``), in the
    game's axes as the passes draw (shader-compile.md 11 "The game's own axes")."""
    return {f"cMtxCloudRotate{k}": gs.as_columns(rotation_y(rate * seconds)) for k, rate in enumerate(CLOUD_TURNS)}


def face_values(face: int) -> dict:
    """``cMtxSkyCubeFace`` of one of the cube's faces (``FACE_TURNS``)."""
    return {"cMtxSkyCubeFace": gs.as_columns(yaw_pitch(*FACE_TURNS[face]))}


def face_quad() -> Mesh:
    """The sky manager's quad over a face: (-1, 1), (1, 1), (-1, -1), (1, -1) at z 0 (``p3``), drawn as a strip."""
    v = np.array([[-1, 1, 0], [1, 1, 0], [-1, -1, 0], [1, -1, 0]], "<f4")
    return Mesh(v.tobytes(), 12, ((0, 7, 3, 0),), np.array([0, 1, 2, 2, 1, 3], np.uint32), 4)


def read_volume(files) -> list[np.ndarray]:
    """In the background: the noise volume's first level, slice by slice (RGBA bytes), as stored."""
    dds = ddsdecode.DdsFile(files.read(VOLUME))
    return [np.ascontiguousarray(dds.level(0, 0, z)) for z in range(dds.slices(0))]


class SkyDome:
    """The sky's draw (``stages``: the technique's ``PassStages``) and its clouds once they are read: 2070's noise
    volume (``set_volume``, its own) or 1404's cloud cube (``cube``, the caller's); the old engine's games share it."""

    def __init__(self, ctx, stages, dummies) -> None:
        self.ctx = ctx
        # both faces: from inside the sphere each view ray meets one face in front, whichever way it is wound
        state = replace(stages.state, cull="none")
        self.draw_call = FxcDraw(ctx, Program(stages.vs, stages.ps), dome(), state, dummies)
        # the clouds' density is the map's alpha (2070's noise volume, 1404's cube): none while it loads or with the
        # clouds switched off, not a white overcast
        self.draw_call.fallbacks = {"cTexture0": NO_CLOUDS}
        self.volume = self.cube = None

    def set_volume(self, slices: list[np.ndarray]) -> None:
        if self.volume is None:
            self.volume = volume_of(self.ctx, slices)

    def maps(self) -> dict:
        found = self.volume if self.volume is not None else self.cube
        return {"cTexture0": found} if found is not None else {}

    def release(self) -> None:
        self.draw_call.release()
        if self.volume is not None:
            self.ctx.later(self.volume)
            self.volume = None


class SkyCube:
    """The sky as the sky manager renders it each frame into ``RT_SKY_CUBEMAP`` for the water to reflect: the
    ``SkyCubeMap`` technique (``stages``) once per face over ``face_quad`` with that face's ``cMtxSkyCubeFace``, the
    frame's light and the clouds of the noise volume (``set_volume``); gamma values, mirrored at the horizon."""

    def __init__(self, ctx, stages, dummies) -> None:
        self.ctx = ctx
        state = replace(stages.state, cull="none", depth_test=False, depth_write=False)
        program, quad = Program(stages.vs, stages.ps), face_quad()
        self.draws = [FxcDraw(ctx, program, quad, state, dummies) for _face in range(6)]
        for d in self.draws:
            d.fallbacks = {"cTexture0": NO_CLOUDS}
        self.volume = None
        self.texture = None  # the cube, made on first use
        self.targets: list = []  # per face (render target, its pass descriptor)

    def set_volume(self, slices: list[np.ndarray]) -> None:
        if self.volume is None:
            self.volume = volume_of(self.ctx, slices)

    def _targets(self) -> None:
        if self.texture is not None:
            return
        rhi = self.ctx.rhi
        flags = QRhiTexture.Flag.CubeMap | QRhiTexture.Flag.RenderTarget
        self.texture = rhi.newTexture(QRhiTexture.Format.RGBA16F, QSize(CUBE_SIDE, CUBE_SIDE), 1, flags)
        self.texture.create()
        for face in range(6):
            attachment = QRhiColorAttachment(self.texture)
            attachment.setLayer(face)
            desc = QRhiTextureRenderTargetDescription()
            desc.setColorAttachments([attachment])
            rt = rhi.newTextureRenderTarget(desc)
            rp = rt.newCompatibleRenderPassDescriptor()
            rt.setRenderPassDescriptor(rp)
            rt.create()
            self.targets.append((rt, rp))

    def render(self, fill) -> QRhiTexture:
        """The six faces as passes of the frame (``fill(c)``: the camera, light and sky values by name, as the dome
        takes them); the cube, to bind as ``tSkyEnv``."""
        self._targets()
        maps = {"cTexture0": self.volume} if self.volume is not None else {}
        size = (CUBE_SIDE, CUBE_SIDE)
        for face, (draw, (rt, rp)) in enumerate(zip(self.draws, self.targets, strict=True)):

            def run(cb, batch, draw=draw, rt=rt, rp=rp, face=face) -> None:
                draw.prepare(rp, (1, 1), batch, maps, lambda c: (fill(c), gs.set_values(c.values(), face_values(face))))
                cb.beginPass(rt, QColor.fromRgbF(0, 0, 0, 0), QRhiDepthStencilClearValue(1.0, 0), batch)
                draw.draw(cb, size)
                cb.endPass()

            self.ctx.native_pass(run)
        return self.texture

    def release(self) -> None:
        for d in self.draws:
            d.release()
        for rt, rp in self.targets:
            self.ctx.later(rt)
            self.ctx.later(rp)
        self.targets = []
        for t in (self.texture, self.volume):
            if t is not None:
                self.ctx.later(t)
        self.texture = self.volume = None
