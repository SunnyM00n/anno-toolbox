"""Anno 1800's screen-space ambient occlusion by its own compute shaders (``ambient_ssao.fx``, NVIDIA's HBAO+ in six
techniques) on the forward path: the view depth from the opaque models' pre-pass, its sixteen quarter-size slices,
the occlusion per slice, put back together at full size and blurred across and down, read by the models' colour pass
as ``cSSAOTex``.

``compile_stages(shaders)`` compiles in the background (``shaders``: the game's ``ForwardSource``);
``Ssao1800(rhi, stages)``'s ``run(ctx, depth, normals, cam)`` records the passes and returns the texture the models
read. docs/rendering/shader-compile.md section 11, "1800's ambient occlusion".
"""

from __future__ import annotations

import math

from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from . import gameshader as gs
from .gamecompute import ComputeChain, ComputeStage, groups, record

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
# the engine's settings record (read in the executable): radius, bias, power, blur sharpness far and near, their
# depths, small and large scale, fore- and background radius
SETTINGS = {"radius": 2.0, "bias": 0.2, "power": 2.0, "sharpness": 3.0, "near_sharpness": 10.0, "near_depth": 0.0,
            "far_depth": 0.2, "small": 1.0, "large": 1.0, "foreground": 0.05, "background": 0.35}  # fmt: skip
ADAPTIVE = True  # USE_ADAPTIVE_SAMPLING
GOLDEN = 0.6180339887498949


def compile_stages(shaders) -> dict:
    """In the background: the six techniques' compute stages by ``TECHNIQUES``' keys."""
    return {key: shaders.call_stage(EFFECT, DEFINES, tech) for key, tech in TECHNIQUES.items()}


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def global_values(size: tuple[int, int], cam: gs.Camera, s: dict = SETTINGS) -> dict:
    """``cbSSAOGlobal`` for a picture of ``size`` seen by ``cam`` as the engine works it out from its settings
    record (``s``) and the camera."""
    w, h = (max(1, int(v)) for v in size)
    p00, p11 = abs(float(cam.proj[0, 0])), abs(float(cam.proj[1, 1]))
    near, far = float(cam.near), float(cam.far)
    radius = max(1e-6, s["radius"] * 0.25) * (1.0 if ADAPTIVE else 0.5)
    to_screen = radius * 0.5 * h * p11
    bias = _clamp(s["bias"], 0.0, 0.5)
    far_sharp = max(0.0, s["sharpness"]) * 4.0
    return {
        "cInvQuarterRes": [4.0 / w, 4.0 / h], "cInvFullRes": [1.0 / w, 1.0 / h],
        "cUVToView0": [2.0 / p00, -2.0 / p11], "cUVToView1": [-1.0 / p00, 1.0 / p11],
        "cLinearizeDepth0": 1.0 / far - 1.0 / near, "cLinearizeDepth1": 1.0 / near,
        "cNegInvRadius2": -1.0 / radius**2, "cRadiusToScreen": to_screen, "cNDotVBias": bias,
        "cPowerExponent": _clamp(s["power"], 1.0, 6.0), "cBlurSharpnessFar": far_sharp,
        "cBlurSharpnessNear": max(1.0, s["near_sharpness"]) * far_sharp,
        "cBlurSharpnessNearDepth": near + (far - near) * s["near_depth"] * 0.05,
        "cBlurSharpnessFarDepth": near + 1e-6 + (far - near) * s["far_depth"] * 0.2,
        "cSmallScaleAOFac": 2.0 * _clamp(s["small"], 0.0, 4.0) / (1.0 - bias) * (1.0 if ADAPTIVE else 0.6),
        "cLargeScaleAOFac": _clamp(s["large"], 0.0, 4.0) / (1.0 - bias) * 0.6,
        "cForegroundRadiusPixels": to_screen / max(1e-6, near, s["foreground"] * far * 0.2),
        "cBackgroundRadiusPixels": to_screen / max(1e-6, 2.0 * near, s["background"] * far * 0.4),
    }  # fmt: skip


def jitter(slice_: int) -> list[float]:
    """``cJitter`` of a slice: a turn of the directions within one step and a start within the first, from a fixed
    low-discrepancy sequence (the engine's table is not read: ours)."""
    u, r = (slice_ * GOLDEN) % 1.0, (slice_ * 5 % SLICES + 0.5) / SLICES
    a = 2.0 * math.pi * u / DIRECTIONS
    return [math.cos(a), math.sin(a), r, 0.0]


def quarter(size: tuple[int, int]) -> tuple[int, int]:
    """The slices' size: a quarter of the picture's, rounded up."""
    return (max(1, (int(size[0]) + 3) // 4), max(1, (int(size[1]) + 3) // 4))


class Ssao1800(ComputeChain):
    """The six techniques and their targets (module docstring); a stage per dispatch where the constants differ (the
    two de-interleaves, the sixteen slices), sharing their pipeline."""

    def __init__(self, rhi, stages: dict) -> None:
        self.rhi, self.size, self.targets = rhi, None, {}
        self.stages = {key: ComputeStage(rhi, stages[key]) for key in ("linear", "join", "blur_x", "blur_y")}
        for k in range(len(SPLIT_OFFSETS)):
            self.stages[f"split{k}"] = ComputeStage(rhi, stages["split"], self.stages.get("split0"))
        for s in range(SLICES):
            self.stages[f"ao{s}"] = ComputeStage(rhi, stages["ao"], self.stages.get("ao0"))

    def _texture(self, fmt, size: tuple[int, int], layers: int = 0):
        flag = QRhiTexture.Flag.UsedWithLoadStore
        q = QSize(*size)
        tex = self.rhi.newTextureArray(fmt, layers, q, 1, flag) if layers else self.rhi.newTexture(fmt, q, 1, flag)
        tex.create()
        return tex

    def _targets(self, size: tuple[int, int]) -> dict:
        if self.size != size:
            self._drop()
            f, small = QRhiTexture.Format, quarter(size)
            self.targets = {"depth": self._texture(f.R32F, size), "slices": self._texture(f.R32F, small, SLICES),
                            "ao": self._texture(f.R32F, small, SLICES), "z0": self._texture(f.RGBA16F, size),
                            "z1": self._texture(f.RGBA16F, size)}  # fmt: skip
            for stage in self.stages.values():
                stage.res.invalidate()
            self.size = size
        return self.targets

    def run(self, ctx, depth, normals, cam: gs.Camera):
        """The passes over the pre-pass's ``depth`` and ``normals`` (device textures of ``cam.size``) seen by ``cam``;
        returns the occlusion the models read."""
        size = tuple(int(v) for v in cam.size)
        t = self._targets(size)
        small = quarter(size)
        view = gs.left_handed_values(cam)  # view space z ahead, as the frame's (frameforward.fill_frame)
        common = {**gs.camera_values(cam), **view, **global_values(size, cam)}
        # the pre-pass's face normals point the other way in the view's mirrored axes: a negated view turns them
        # back (shader-compile.md 11 "1800's ambient occlusion")
        common["cMtxView"] = -view["cMtxView"]
        inputs = {"cDepthBuffer": depth, "cSceneNormalsBuffer": normals, "cOutViewDepthTex": t["depth"],
                  "cLinearDepthTex": t["depth"], "cOutQuarterSizeViewDepthTex": t["slices"],
                  "cDeInterleavedDepthTex": t["slices"], "cOutQuarterSizeAOTex": t["ao"], "cDeInterleavedAOTex": t["ao"]}  # fmt: skip

        def values(**extra):
            return lambda c: gs.set_values([c], {**common, **extra})

        full, part = (
            (groups(size[0], GROUP), groups(size[1], GROUP), 1),
            (groups(small[0], GROUP), groups(small[1], GROUP), 1),
        )
        record(ctx, self.stages["linear"], inputs, values(), full)
        for k, offset in enumerate(SPLIT_OFFSETS):
            record(ctx, self.stages[f"split{k}"], inputs, values(cOffset=list(offset), cSliceIndex=8.0 * k), part)
        for s in range(SLICES):
            fill = values(cOffset=[float(s % 4), float(s // 4)], cSliceIndex=float(s), cJitter=jitter(s))
            record(ctx, self.stages[f"ao{s}"], inputs, fill, part)
        record(ctx, self.stages["join"], {**inputs, "cOutAOZTex": t["z1"]}, values(), full)
        record(ctx, self.stages["blur_x"], {**inputs, "cAOZTex": t["z1"], "cOutAOZTex": t["z0"]}, values(), full)
        record(ctx, self.stages["blur_y"], {**inputs, "cAOZTex": t["z0"], "cOutAOZTex": t["z1"]}, values(), full)
        return t["z1"]

    def _drop(self) -> None:
        for tex in self.targets.values():
            tex.destroy()
        self.targets, self.size = {}, None

    def release(self) -> None:
        super().release()
        self._drop()
