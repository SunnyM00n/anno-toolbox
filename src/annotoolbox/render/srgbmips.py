"""The mip levels of an 8-bit sRGB texture built by a compute pass of ours on the GPU, where QRhi's own level
generation fails (Direct3D 12 loses the device on an sRGB texture, docs/tool-suite.md section 21).

``SrgbMips(rhi)`` is the entry, one per device; ``build(ctx, texture)`` records one compute pass per level: the level
above read decoded to linear light (an sRGB texture's loads decode), a 2 x 2 box (the edge texel repeated at an odd
size), colour and alpha each on its own as ``models.mipchain`` filters them, encoded to sRGB into an 8-bit
storage image (no storage view of an sRGB format exists), then copied into the level.
"""

from __future__ import annotations

import struct

from PySide6.QtCore import QSize
from PySide6.QtGui import (
    QRhiBuffer,
    QRhiSampler,
    QRhiShaderResourceBinding,
    QRhiShaderStage,
    QRhiTexture,
    QShader,
)

from . import shaderbake
from .gamecompute import copy_into, groups

B = QRhiShaderResourceBinding
_N, _CLAMP = QRhiSampler.Filter.Nearest, QRhiSampler.AddressMode.ClampToEdge
GROUP = 8
MIPS_CS = """#version 440
layout(local_size_x = 8, local_size_y = 8) in;
layout(std140, binding = 0) uniform Params { ivec4 level; };  // x: the level read, yz: the size written
layout(binding = 1) uniform sampler2D src;
layout(binding = 2, rgba8) uniform writeonly image2D dst;
vec3 encode(vec3 c) { return mix(12.92 * c, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c)); }
void main() {
    ivec2 at = ivec2(gl_GlobalInvocationID.xy);
    if (at.x >= level.y || at.y >= level.z) return;
    ivec2 last = textureSize(src, level.x) - 1;
    vec4 sum = vec4(0.0);
    for (int k = 0; k < 4; k++) sum += texelFetch(src, min(at * 2 + ivec2(k & 1, k >> 1), last), level.x);
    sum *= 0.25;
    imageStore(dst, at, vec4(encode(clamp(sum.rgb, 0.0, 1.0)), sum.a));
}
"""


class SrgbMips:
    """The compute pipeline (baked once) and the passes of each texture (module docstring)."""

    def __init__(self, rhi) -> None:
        self.rhi = rhi
        self.shader = QShader.fromSerialized(shaderbake.bake_compute(MIPS_CS))
        self.pipeline = None

    def build(self, ctx, texture) -> None:
        """Levels 1.. of ``texture`` (a device texture, 8-bit sRGB, level 0 uploaded), each a compute pass and a copy
        recorded on ``ctx``; the per-level resources are released a frame later."""
        w, h = texture.size[0], texture.size[1]
        level = 0
        while w > 1 or h > 1:
            w, h = max(1, w // 2), max(1, h // 2)
            self._level(ctx, texture, level, (w, h))
            level += 1

    def _level(self, ctx, texture, level: int, size: tuple[int, int]) -> None:
        rhi = self.rhi
        flags = QRhiTexture.Flag.UsedWithLoadStore | QRhiTexture.Flag.UsedAsTransferSource
        scratch = rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(*size), 1, flags)
        scratch.create()
        params = rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, 16)
        params.create()
        sampler = rhi.newSampler(_N, _N, _N, _CLAMP, _CLAMP)
        sampler.create()
        srb = rhi.newShaderResourceBindings()
        st = B.StageFlag.ComputeStage
        srb.setBindings([B.uniformBuffer(0, st, params), B.sampledTexture(1, st, texture.native, sampler),
                         B.imageStore(2, st, scratch, 0)])  # fmt: skip
        srb.create()
        if self.pipeline is None:
            pipe = rhi.newComputePipeline()
            pipe.setShaderStage(QRhiShaderStage(QRhiShaderStage.Type.Compute, self.shader))
            pipe.setShaderResourceBindings(srb)
            if not pipe.create():
                raise RuntimeError("the backend refused the sRGB level pass")
            self.pipeline = pipe

        def compute_pass(cb, batch, native=texture.native) -> None:
            batch.updateDynamicBuffer(params, 0, struct.pack("<4i", level, *size, 0))
            cb.beginComputePass(batch)
            cb.setComputePipeline(self.pipeline)
            cb.setShaderResources(srb)
            cb.dispatch(groups(size[0], GROUP), groups(size[1], GROUP), 1)
            cb.endComputePass(copy_into(rhi, native, scratch, size, 0, level + 1))

        ctx.native_pass(compute_pass)
        for r in (scratch, params, sampler, srb):
            ctx.later(r)

    def release(self) -> None:
        if self.pipeline is not None:
            self.pipeline.destroy()
            self.pipeline = None
