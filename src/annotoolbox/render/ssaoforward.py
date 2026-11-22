"""Anno 1800's screen-space ambient occlusion by its own compute shaders (``ambient_ssao.fx``, NVIDIA's HBAO+ in six
techniques) on the forward path: the view depth from the opaque models' pre-pass, its sixteen quarter-size slices,
the occlusion per slice, put back together at full size and blurred across and down, read by the models' colour pass
as ``cSSAOTex``.

``compile_stages(shaders)`` compiles in the background (``shaders``: the game's ``CForwardSource``);
``CSsao1800(rhi, stages)``'s ``run(ctx, depth, normals, camera)`` records the passes and returns the texture the models
read. docs/rendering/shader-compile.md section 11, "1800's ambient occlusion".
"""

from __future__ import annotations

import math

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .gamecompute import CComputeChain, CComputeStage, groups, record

EFFECT = "data/shaders/fx/ambient_ssao.fx"
TECHNIQUES = {"linear": "LinearizeDepth", "split": "DeInterleaveDepth", "ao": "AO_Compute",
              "join": "AO_ReInterleave", "blur_x": "AO_BlurX", "blur_y": "AO_BlurY"}  # fmt: skip
GROUP = 16  # SSAO_THREAD_GROUPS_X / _Y
DIRECTIONS = 8
# the engine's quality defines (its settings record; the second of shaderdefines' lists)
DEFINES = (("SSAO_THREAD_GROUPS_X", str(GROUP)), ("SSAO_THREAD_GROUPS_Y", str(GROUP)),
           ("NUM_DIRECTIONS", str(DIRECTIONS)), ("NUM_STEPS", "4"), ("KERNEL_RADIUS", "4"), ("USE_ADAPTIVE_SAMPLING", "1"))  # fmt: skip
SLICES = 16  # the 4 x 4 pixel pattern, a quarter-size slice each
SPLIT_OFFSETS = ((0.5, 0.5), (0.5, 2.5))  # DeInterleaveDepth's two dispatches: rows 0-1 into slices 0-7, rows 2-3
# the engine's settings record (taken from how the game behaves): radius, bias, power, blur sharpness far and near, their
# depths, small and large scale, fore- and background radius
SETTINGS = {"radius": 2.0, "bias": 0.2, "power": 2.0, "sharpness": 3.0, "near_sharpness": 10.0, "near_depth": 0.0,
            "far_depth": 0.2, "small": 1.0, "large": 1.0, "foreground": 0.05, "background": 0.35}  # fmt: skip
ADAPTIVE = True  # USE_ADAPTIVE_SAMPLING
GOLDEN = 0.6180339887498949


def compile_stages(shaders) -> dict:
    """In the background: the six techniques' compute stages by ``TECHNIQUES``' keys."""
    return {key: shaders.call_stage(EFFECT, DEFINES, tech) for key, tech in TECHNIQUES.items()}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def global_values(size_px: tuple[int, int], camera: gs.SCamera, settings: dict | None = None) -> dict:
    """``cbSSAOGlobal`` for a picture of ``size_px`` seen by ``camera`` as the engine works it out from its settings
    record (``settings``, default ``SETTINGS``) and the camera."""
    settings = SETTINGS if settings is None else settings
    width, height = (max(1, int(value)) for value in size_px)
    p00, p11 = abs(float(camera.projection[0, 0])), abs(float(camera.projection[1, 1]))
    near, far = float(camera.near), float(camera.far)
    radius = max(1e-6, settings["radius"] * 0.25) * (1.0 if ADAPTIVE else 0.5)
    to_screen = radius * 0.5 * height * p11
    bias = _clamp(settings["bias"], 0.0, 0.5)
    far_sharp = max(0.0, settings["sharpness"]) * 4.0
    return {
        "cInvQuarterRes": [4.0 / width, 4.0 / height], "cInvFullRes": [1.0 / width, 1.0 / height],
        "cUVToView0": [2.0 / p00, -2.0 / p11], "cUVToView1": [-1.0 / p00, 1.0 / p11],
        "cLinearizeDepth0": 1.0 / far - 1.0 / near, "cLinearizeDepth1": 1.0 / near,
        "cNegInvRadius2": -1.0 / radius**2, "cRadiusToScreen": to_screen, "cNDotVBias": bias,
        "cPowerExponent": _clamp(settings["power"], 1.0, 6.0), "cBlurSharpnessFar": far_sharp,
        "cBlurSharpnessNear": max(1.0, settings["near_sharpness"]) * far_sharp,
        "cBlurSharpnessNearDepth": near + (far - near) * settings["near_depth"] * 0.05,
        "cBlurSharpnessFarDepth": near + 1e-6 + (far - near) * settings["far_depth"] * 0.2,
        "cSmallScaleAOFac": 2.0 * _clamp(settings["small"], 0.0, 4.0) / (1.0 - bias) * (1.0 if ADAPTIVE else 0.6),
        "cLargeScaleAOFac": _clamp(settings["large"], 0.0, 4.0) / (1.0 - bias) * 0.6,
        "cForegroundRadiusPixels": to_screen / max(1e-6, near, settings["foreground"] * far * 0.2),
        "cBackgroundRadiusPixels": to_screen / max(1e-6, 2.0 * near, settings["background"] * far * 0.4),
    }  # fmt: skip


def jitter(slice_: int) -> list[float]:
    """``cJitter`` of a slice: a turn of the directions within one step and a start within the first, from a fixed
    low-discrepancy sequence (the engine's table is not read: ours)."""
    angle_fraction, radius_fraction = (slice_ * GOLDEN) % 1.0, (slice_ * 5 % SLICES + 0.5) / SLICES
    angle_rad = 2.0 * math.pi * angle_fraction / DIRECTIONS
    return [math.cos(angle_rad), math.sin(angle_rad), radius_fraction, 0.0]


def quarter(size_px: tuple[int, int]) -> tuple[int, int]:
    """The slices' size: a quarter of the picture's, rounded up."""
    return (max(1, (int(size_px[0]) + 3) // 4), max(1, (int(size_px[1]) + 3) // 4))


class CSsao1800(CComputeChain):
    """The six techniques and their targets (module docstring); a stage per dispatch where the constants differ (the
    two de-interleaves, the sixteen slices), sharing their pipeline."""

    def __init__(self, rhi, stages: dict) -> None:
        self.m_rhi, self.m_size_px, self.m_targets = rhi, None, {}
        self.m_stages = {key: CComputeStage(rhi, stages[key]) for key in ("linear", "join", "blur_x", "blur_y")}
        for k in range(len(SPLIT_OFFSETS)):
            self.m_stages[f"split{k}"] = CComputeStage(rhi, stages["split"], self.m_stages.get("split0"))
        for slice_index in range(SLICES):
            self.m_stages[f"ao{slice_index}"] = CComputeStage(rhi, stages["ao"], self.m_stages.get("ao0"))

    def _texture(self, texture_format, size_px: tuple[int, int], layers: int = 0):
        flag = QRhiTexture.Flag.UsedWithLoadStore
        qsize = QSize(*size_px)
        texture = (
            self.m_rhi.newTextureArray(texture_format, layers, qsize, 1, flag)
            if layers
            else self.m_rhi.newTexture(texture_format, qsize, 1, flag)
        )
        texture.create()
        return texture

    def _targets(self, size_px: tuple[int, int]) -> dict:
        if self.m_size_px != size_px:
            self._drop()
            texture_format, small = QRhiTexture.Format, quarter(size_px)
            self.m_targets = {"depth": self._texture(texture_format.R32F, size_px), "slices": self._texture(texture_format.R32F, small, SLICES),
                            "ao": self._texture(texture_format.R32F, small, SLICES), "z0": self._texture(texture_format.RGBA16F, size_px),
                            "z1": self._texture(texture_format.RGBA16F, size_px)}  # fmt: skip
            for stage in self.m_stages.values():
                stage.m_res.invalidate()
            self.m_size_px = size_px
        return self.m_targets

    def run(self, ctx, depth, normals, camera: gs.SCamera):
        """The passes over the pre-pass's ``depth`` and ``normals`` (device textures of ``camera.size_px``) seen by ``camera``;
        returns the occlusion the models read."""
        size = tuple(int(value) for value in camera.size_px)
        targets = self._targets(size)
        small = quarter(size)
        view = gs.left_handed_values(camera)  # view space z ahead, as the frame's (frameforward.fill_frame)
        common = {**gs.camera_values(camera), **view, **global_values(size, camera)}
        # the pre-pass's face normals point the other way in the view's mirrored axes: a negated view turns them
        # back (shader-compile.md 11 "1800's ambient occlusion")
        common["cMtxView"] = -view["cMtxView"]
        inputs = {"cDepthBuffer": depth, "cSceneNormalsBuffer": normals, "cOutViewDepthTex": targets["depth"],
                  "cLinearDepthTex": targets["depth"], "cOutQuarterSizeViewDepthTex": targets["slices"],
                  "cDeInterleavedDepthTex": targets["slices"], "cOutQuarterSizeAOTex": targets["ao"], "cDeInterleavedAOTex": targets["ao"]}  # fmt: skip

        def values(**extra):
            return lambda constants: gs.set_values([constants], {**common, **extra})

        full, part = (
            (groups(size[0], GROUP), groups(size[1], GROUP), 1),
            (groups(small[0], GROUP), groups(small[1], GROUP), 1),
        )
        record(ctx, self.m_stages["linear"], inputs, values(), full)
        for k, offset in enumerate(SPLIT_OFFSETS):
            record(ctx, self.m_stages[f"split{k}"], inputs, values(cOffset=list(offset), cSliceIndex=8.0 * k), part)
        for slice_index in range(SLICES):
            fill = values(
                cOffset=[float(slice_index % 4), float(slice_index // 4)],
                cSliceIndex=float(slice_index),
                cJitter=jitter(slice_index),
            )
            record(ctx, self.m_stages[f"ao{slice_index}"], inputs, fill, part)
        record(ctx, self.m_stages["join"], {**inputs, "cOutAOZTex": targets["z1"]}, values(), full)
        record(
            ctx,
            self.m_stages["blur_x"],
            {**inputs, "cAOZTex": targets["z1"], "cOutAOZTex": targets["z0"]},
            values(),
            full,
        )
        record(
            ctx,
            self.m_stages["blur_y"],
            {**inputs, "cAOZTex": targets["z0"], "cOutAOZTex": targets["z1"]},
            values(),
            full,
        )
        return targets["z1"]

    def _drop(self) -> None:
        for texture in self.m_targets.values():
            texture.destroy()
        self.m_targets, self.m_size_px = {}, None

    def release(self) -> None:
        super().release()
        self._drop()
