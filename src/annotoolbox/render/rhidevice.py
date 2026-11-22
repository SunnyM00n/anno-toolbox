"""The QRhi device: the drawing interface our renderers use (the subset of moderngl's context they call: programs
from our GLSL, buffers, vertex arrays, textures, framebuffers, uniforms by name, the depth, blend and cull state),
on Qt's QRhi with Direct3D 12, Direct3D 11, Vulkan or OpenGL behind it.

``CRhiContext(rhi)`` is the entry; a view calls ``begin(command_buffer, widget)`` and ``end()`` around a renderer's frame
(``rhiview.CRhiView`` does). Calls are recorded, not run: each target's draws become a render pass (``rhipass``),
and the passes run at ``end`` in the order the calls came, each after the uploads made before it. A program's
GLSL is ported and baked once (``glslport``, ``shaderbake``). docs/tool-suite.md section 21, "The device layer".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QByteArray, QPoint, QSize
from PySide6.QtGui import (
    QRhi,
    QRhiBuffer,
    QRhiTexture,
    QRhiTextureSubresourceUploadDescription,
    QRhiTextureUploadDescription,
    QRhiTextureUploadEntry,
    QShader,
    QShaderCode,
    QShaderKey,
    QShaderVersion,
)

from ..models import mipchain
from . import glslport, rhipass, shaderbake

# moderngl's constants, so a renderer written for one device runs on the other
BLEND, DEPTH_TEST, CULL_FACE, PROGRAM_POINT_SIZE = 1, 2, 4, 16
POINTS, LINES, LINE_STRIP, TRIANGLES, TRIANGLE_STRIP = 0, 1, 3, 4, 5
NEAREST, LINEAR = 0x2600, 0x2601
NEAREST_MIPMAP_NEAREST, LINEAR_MIPMAP_NEAREST, NEAREST_MIPMAP_LINEAR, LINEAR_MIPMAP_LINEAR = (
    0x2700,
    0x2701,
    0x2702,
    0x2703,
)
ZERO, ONE, SRC_COLOR, ONE_MINUS_SRC_COLOR, SRC_ALPHA, ONE_MINUS_SRC_ALPHA = 0, 1, 0x300, 0x301, 0x302, 0x303
DST_ALPHA, ONE_MINUS_DST_ALPHA, DST_COLOR, ONE_MINUS_DST_COLOR = 0x304, 0x305, 0x306, 0x307
SRGB8, SRGB8_ALPHA8 = 0x8C41, 0x8C43  # OpenGL's sRGB formats of three and four components

TextureFormat = QRhiTexture.Format
# (dtype, components) -> (QRhi format, numpy type, components stored)
_FORMATS = {
    ("f1", 1): (TextureFormat.R8, np.uint8, 1), ("f1", 2): (TextureFormat.RG8, np.uint8, 2), ("f1", 3): (TextureFormat.RGBA8, np.uint8, 4),
    ("f1", 4): (TextureFormat.RGBA8, np.uint8, 4), ("f2", 1): (TextureFormat.R16F, np.float16, 1), ("f2", 2): (TextureFormat.RGBA16F, np.float16, 4),
    ("f2", 3): (TextureFormat.RGBA16F, np.float16, 4), ("f2", 4): (TextureFormat.RGBA16F, np.float16, 4), ("f4", 1): (TextureFormat.R32F, np.float32, 1),
    ("f4", 2): (TextureFormat.RGBA32F, np.float32, 4), ("f4", 3): (TextureFormat.RGBA32F, np.float32, 4), ("f4", 4): (TextureFormat.RGBA32F, np.float32, 4),
    ("u1", 1): (TextureFormat.R8UI, np.uint8, 1), ("u4", 1): (TextureFormat.R32UI, np.uint32, 1), ("u4", 2): (TextureFormat.RG32UI, np.uint32, 2),
    ("u4", 4): (TextureFormat.RGBA32UI, np.uint32, 4), ("i4", 1): (TextureFormat.R32SI, np.int32, 1),
}  # fmt: skip
_ONE = {np.uint8: 255, np.float16: 1.0, np.float32: 1.0, np.uint32: 1, np.int32: 1}


def _rgba(pixels: np.ndarray) -> np.ndarray:
    """8-bit pixels with an alpha channel (opaque where they had none)."""
    if pixels.shape[-1] == 4:
        return np.ascontiguousarray(pixels)
    return np.concatenate([pixels, np.full((*pixels.shape[:-1], 4 - pixels.shape[-1]), 255, np.uint8)], axis=-1)


class CUniform:
    """One uniform of a program by name: ``value`` and ``write`` as moderngl's; a sampler's value is its unit."""

    def __init__(self, program: CProgram, name: str) -> None:
        self.program, self.name = program, name

    @property
    def value(self):
        return self.program.m_values.get(self.name)

    @value.setter
    def value(self, value) -> None:
        program = self.program
        program.m_values[self.name] = value
        if self.name in program.m_ported.samplers:
            program.m_units[self.name] = int(value)
        else:
            shaderbake.pack_member(program.m_data, program.m_baked.members[self.name], value)

    def write(self, data: bytes) -> None:
        if self.name not in self.program.m_ported.samplers:
            shaderbake.write_member(self.program.m_data, self.program.m_baked.members[self.name], bytes(data))


def with_bytecode(shader: QShader, profile: str) -> None:
    """``shader``'s HLSL (shader model 5.0) also as DXBC, compiled as QRhi's Direct3D backends compile it when a
    pipeline is made and kept (``shaderbake.dxbc``): the backends take the bytecode, so a start makes the view's
    pipelines without compiling (island-117-view.md "Speed", part 14). Left as it is where none compiles."""
    hlsl = QShaderKey(QShader.Source.HlslShader, QShaderVersion(50))
    code = shader.shader(hlsl)
    if code.shader().isEmpty():
        return
    blob = shaderbake.dxbc(bytes(code.shader().data()), bytes(code.entryPoint().data()).decode(), profile)
    if blob is None:
        return
    key = QShaderKey(QShader.Source.DxbcShader, QShaderVersion(50))
    shader.setShader(key, QShaderCode(QByteArray(blob), code.entryPoint()))
    shader.setResourceBindingMap(key, shader.nativeResourceBindingMap(hlsl))


class CProgram:
    """A vertex and fragment shader pair of ours, ported and baked, with its uniform block's current values."""

    def __init__(self, ctx: CRhiContext, vertex: str, fragment: str) -> None:
        self.ctx = ctx
        self.m_ported = glslport.port(vertex, fragment)
        self.m_baked = shaderbake.bake(self.m_ported)
        self.m_shaders = (
            QShader.fromSerialized(QByteArray(self.m_baked.vertex)),
            QShader.fromSerialized(QByteArray(self.m_baked.fragment)),
        )
        if ctx.m_rhi.backend() in (QRhi.Implementation.D3D11, QRhi.Implementation.D3D12):
            for shader, profile in zip(self.m_shaders, ("vs_5_0", "ps_5_0"), strict=True):
                with_bytecode(shader, profile)
        self.m_data = bytearray(max(16, self.m_baked.block_size))
        self.m_values: dict[str, object] = {}
        self.m_units = dict.fromkeys(self.m_ported.samplers, 0)
        self.m_uniform_pool: list = []  # one buffer per draw of the frame (rhipass)
        self._m_uniforms: dict[str, CUniform] = {}
        self.m_serial = rhipass.CRecorder.next_serial()  # the caches key on it: an id() can be given again

    def __contains__(self, name: str) -> bool:
        return name in self.m_baked.members or name in self.m_ported.samplers

    def __getitem__(self, name: str) -> CUniform:
        got = self._m_uniforms.get(name)  # made once: a frame of many parts asks tens of thousands of times
        if got is None:
            if name not in self:
                raise KeyError(name)
            got = self._m_uniforms[name] = CUniform(self, name)
        return got

    def get(self, name: str, default=None):
        return self[name] if name in self else default

    def release(self) -> None:
        self.ctx.forget_program(self.m_serial)
        for buffer in self.m_uniform_pool:
            self.ctx.later(buffer)  # a frame in flight may still read it
        self.m_uniform_pool = []


class CBuffer:
    """A vertex or index buffer; ``dynamic`` ones are rewritten each frame (host-visible on the GPU)."""

    def __init__(self, ctx: CRhiContext, data: bytes | None, reserve: int, dynamic: bool) -> None:
        self.ctx, self.dynamic = ctx, dynamic
        raw = bytes(data) if data is not None else bytes(reserve)
        self.size = len(raw)
        kind = QRhiBuffer.Type.Dynamic if dynamic else QRhiBuffer.Type.Static
        usage = QRhiBuffer.UsageFlag.VertexBuffer | QRhiBuffer.UsageFlag.IndexBuffer
        self.native = ctx.m_rhi.newBuffer(kind, usage, max(16, self.size))
        self.native.create()
        if raw:
            self.write(raw)

    def write(self, data, offset: int = 0) -> None:
        raw = bytes(data)
        if self.dynamic:
            self.ctx._upload(self, lambda updates: updates.updateDynamicBuffer(self.native, offset, raw))
        else:
            self.ctx._upload(self, lambda updates: updates.uploadStaticBuffer(self.native, offset, raw))

    def release(self) -> None:
        self.ctx.later(self.native)


@dataclass
class CTexture:
    """A 2D texture, array or 3D texture, or a depth texture; its filter, wrap and compare state make its sampler."""

    ctx: CRhiContext
    size: tuple[int, ...]
    components: int
    dtype: str = "f1"
    m_kind: str = "2d"  # 2d | array | 3d | depth
    samples: int = 0
    m_srgb: bool = False
    filter: tuple[int, int] = (LINEAR, LINEAR)
    repeat_x: bool = True
    repeat_y: bool = True
    repeat_z: bool = True
    compare_func: str = ""
    anisotropy: float = 1.0
    m_mipmapped: bool = False
    native: object = None
    m_serial: int = 0
    m_target: bool = True  # can be drawn into (a framebuffer's attachment)
    m_qformat: object = None  # a QRhi format of its own (a G-buffer's), written only by drawing
    m_storage: bool = False  # written by a compute pass (an image store)
    m_native_id: int = 0  # a view's own image: its resource id, to see when the view made a new one
    _m_levels: dict = field(default_factory=dict)  # the data uploaded at level 0, kept for a later mip build

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    def _format(self):
        if self.m_qformat is not None:
            return self.m_qformat, np.uint8, 4
        if self.m_kind == "depth":
            return TextureFormat.D32F, np.float32, 1
        texture_format, numpy_type, component_count = _FORMATS[(self.dtype, self.components)]
        return texture_format, numpy_type, component_count

    def create(self) -> None:
        rhi, texture_format = self.ctx.m_rhi, self._format()[0]
        # an image read from a file is never drawn into (QRhi's OpenGL backend keeps sRGB only without the flag)
        flags = QRhiTexture.Flag.RenderTarget if self.m_target else QRhiTexture.Flag(0)
        if self.samples <= 1:
            flags |= QRhiTexture.Flag.UsedAsTransferSource
        if self.m_srgb:
            flags |= QRhiTexture.Flag.sRGB
        if self.m_storage:
            flags |= QRhiTexture.Flag.UsedWithLoadStore
        if self.m_mipmapped:
            flags |= QRhiTexture.Flag.MipMapped | QRhiTexture.Flag.UsedWithGenerateMips
        width_px, height_px = self.size[0], self.size[1]
        if self.m_kind == "3d":
            self.native = rhi.newTexture(
                texture_format, width_px, height_px, self.size[2], 1, flags | QRhiTexture.Flag.ThreeDimensional
            )
        elif self.m_kind == "array":
            self.native = rhi.newTextureArray(texture_format, self.size[2], QSize(width_px, height_px), 1, flags)
        else:
            self.native = rhi.newTexture(texture_format, QSize(width_px, height_px), max(1, self.samples), flags)
        if not self.native.create():
            raise RuntimeError(f"the texture {self.size} {texture_format} was not created")
        if self.m_serial:
            self.ctx.forget(self.m_serial)
        self.m_serial = rhipass.CRecorder.next_serial()

    def _layers(self, data, width_px: int, height_px: int, layers: int) -> list[bytes]:
        texture_format, numpy_type, component_count = self._format()
        pixels = np.frombuffer(bytes(data), dtype=numpy_type).reshape(layers, height_px, width_px, -1)
        if pixels.shape[-1] < component_count:  # RGB stored as RGBA
            pad = np.full((*pixels.shape[:-1], component_count - pixels.shape[-1]), _ONE[numpy_type], numpy_type)
            pixels = np.concatenate([pixels, pad], axis=-1)
        return [np.ascontiguousarray(layer_pixels).tobytes() for layer_pixels in pixels]

    def write(self, data, level: int = 0, viewport=None) -> None:
        """moderngl's write: a whole level, or the part ``viewport`` names ((x, y, w, h); an array's or a 3D
        texture's (x, y, z, w, h, depth))."""
        deep = self.m_kind in ("3d", "array")
        if viewport is None:
            x = y = z = 0
            width_px, height_px, depth_px = (
                max(1, self.size[0] >> level),
                max(1, self.size[1] >> level),
                self.size[2] if deep else 1,
            )
        elif len(viewport) == 4:
            (x, y, width_px, height_px), z, depth_px = viewport, 0, 1
        else:
            x, y, z, width_px, height_px, depth_px = viewport
        layers = self._layers(data, width_px, height_px, depth_px)
        if level == 0:  # the base a later mip build starts from: only a whole one is kept
            self._m_levels.pop(0, None)
            if viewport is None:
                self._m_levels[0] = data
        row_bytes = width_px * self._format()[2] * np.dtype(self._format()[1]).itemsize
        is_partial = viewport is not None

        def upload(updates, layers=layers, native=self.native) -> None:
            entries = []
            for k, raw in enumerate(layers):
                description = QRhiTextureSubresourceUploadDescription(raw)
                description.setDataStride(row_bytes)
                if is_partial:
                    description.setDestinationTopLeft(QPoint(x, y))
                    description.setSourceSize(QSize(width_px, height_px))
                entries.append(QRhiTextureUploadEntry(z + k, level, description))
            batch = QRhiTextureUploadDescription()
            batch.setEntries(entries)
            updates.uploadTexture(native, batch)

        self.ctx._upload(self, upload)

    def build_mipmaps(self, base: int = 0, max_level: int = 1000) -> None:
        """The levels from the base level last written whole; the base's pixels are let go after (a view's maps
        kept a CPU copy each: gigabytes on a continental island)."""
        try:
            self._build_mipmaps()
        finally:
            self._m_levels = {}

    def _build_mipmaps(self) -> None:
        base_data = self._m_levels.get(0)
        if not self.m_mipmapped:
            self.m_mipmapped = True
            self.ctx.later(self.native)
            self.create()
        self._m_levels = {}
        self.filter = (LINEAR_MIPMAP_LINEAR, LINEAR)
        if self.m_srgb and self.ctx.m_cpu_srgb_mips and base_data is not None and self.m_kind == "2d":
            # Direct3D 12 loses the device generating the levels of an sRGB texture (section 21): our compute pass
            # builds them (srgbmips), the CPU where it cannot
            builder = self.ctx.srgb_mips()
            if builder is not None:
                self.write(base_data)
                try:
                    builder.build(self.ctx, self)
                    return
                except RuntimeError:  # the backend refused the pass: the CPU from now on
                    self.ctx._m_srgb_mips[0] = None
            rgba = np.frombuffer(bytes(base_data), np.uint8).reshape(self.size[1], self.size[0], -1)
            for level, level_image in enumerate(mipchain.mip_chain(_rgba(rgba), gamma=True)):
                self.write(level_image.tobytes(), level)
            return
        if base_data is not None:
            self.write(base_data)
        self.ctx._upload(self, lambda updates, native=self.native: updates.generateMips(native))

    def use(self, location: int = 0) -> None:
        self.ctx.m_units[location] = self

    def sampler_key(self) -> tuple:
        return (*self.filter, self.repeat_x, self.repeat_y, self.repeat_z, self.compare_func)

    def release(self) -> None:
        self.ctx.forget(self.m_serial)
        self.ctx.later(self.native)
        self.native = None


@dataclass
class CFramebuffer:
    """Colour attachments and a depth attachment (textures); ``display`` marks the view's own image."""

    ctx: CRhiContext
    color_attachments: list
    depth_attachment: CTexture | None = None
    m_display: bool = False
    m_targets: dict = field(default_factory=dict)  # (preserve, resolve texture id) -> QRhi render target
    _m_depth_mask: bool = True

    @property
    def depth_mask(self) -> bool:
        """moderngl's per-framebuffer depth writes: in force while this framebuffer is in use."""
        return self._m_depth_mask

    @depth_mask.setter
    def depth_mask(self, is_enabled: bool) -> None:
        self._m_depth_mask = bool(is_enabled)
        if self.ctx._m_fbo is self:
            self.ctx.depth_mask = self._m_depth_mask

    @property
    def size(self) -> tuple[int, int]:
        first = self.color_attachments[0] if self.color_attachments else self.depth_attachment
        return first.size[0], first.size[1]

    @property
    def samples(self) -> int:
        first = self.color_attachments[0] if self.color_attachments else self.depth_attachment
        return max(1, first.samples)

    @property
    def viewport(self) -> tuple[int, int, int, int]:
        return (0, 0, *self.size)

    def use(self) -> None:
        self.ctx._m_fbo = self
        self.ctx.viewport = self.viewport
        self.ctx.depth_mask = self._m_depth_mask

    def clear(self, red=0.0, green=0.0, blue=0.0, alpha=0.0, depth=1.0, viewport=None, color=None) -> None:
        rgba = tuple(color) if color is not None else (red, green, blue, alpha)
        self.ctx._clear(self, rgba, depth)

    def read(self, *_args, **_kw):
        raise NotImplementedError("the QRhi device reads a frame back with its view's grab, not mid-frame")

    def release(self) -> None:
        for (
            render_target,
            render_pass,
            _signature,
        ) in self.m_targets.values():  # rhipass._target's (target, pass descriptor, signature)
            self.ctx.later(render_target)
            self.ctx.later(render_pass)
        self.m_targets = {}


class CVertexArray:
    """Buffers bound to a program's inputs by moderngl's format strings ("3f 3f 2f", "16f/i")."""

    def __init__(self, ctx, program: CProgram, content, index_buffer=None, index_element_size=4, skip_errors=False):
        self.ctx, self.program = ctx, program
        self.index_buffer, self.index_element_size = index_buffer, index_element_size
        self.m_buffers, self.m_layout = rhipass.vertex_layout(program.m_ported.attributes, content, skip_errors)
        self.m_key = rhipass.layout_key(self.m_layout)

    def _count(self) -> int:
        if self.index_buffer is not None:
            return self.index_buffer.size // self.index_element_size
        sizes = [
            buffer.size // stride for buffer, stride, is_instanced in self.m_buffers if not is_instanced and stride
        ]
        return min(sizes) if sizes else 0

    def render(self, mode: int | None = None, vertices: int = -1, first: int = 0, instances: int = -1) -> None:
        count = self._count() if vertices < 0 else vertices
        if count > 0 and instances != 0:
            self.ctx._draw(self, TRIANGLES if mode is None else mode, count, first, max(1, instances))

    def release(self) -> None:
        pass


class CRhiContext(rhipass.CRecorder):
    """The device for one view: what a renderer calls, as on moderngl's context (module docstring)."""

    BLEND, DEPTH_TEST, CULL_FACE, PROGRAM_POINT_SIZE = BLEND, DEPTH_TEST, CULL_FACE, PROGRAM_POINT_SIZE
    POINTS, LINES, LINE_STRIP, TRIANGLES, TRIANGLE_STRIP = POINTS, LINES, LINE_STRIP, TRIANGLES, TRIANGLE_STRIP
    NEAREST, LINEAR, LINEAR_MIPMAP_LINEAR = NEAREST, LINEAR, LINEAR_MIPMAP_LINEAR
    ZERO, ONE, SRC_ALPHA, ONE_MINUS_SRC_ALPHA, DST_COLOR = ZERO, ONE, SRC_ALPHA, ONE_MINUS_SRC_ALPHA, DST_COLOR
    ONE_MINUS_SRC_COLOR, DST_ALPHA, ONE_MINUS_DST_ALPHA = ONE_MINUS_SRC_COLOR, DST_ALPHA, ONE_MINUS_DST_ALPHA

    def __init__(self, rhi) -> None:
        super().__init__(rhi)
        self.max_samples = max(rhi.supportedSampleCounts() or [1])
        self.m_cpu_srgb_mips = rhi.backend() == QRhi.Implementation.D3D12
        self._m_srgb_mips: list = []  # the compute builder of sRGB levels, made on first use ([] not yet, [None] none)
        self.m_decode_srgb = rhi.backend() == QRhi.Implementation.OpenGLES2
        self._m_command_buffer = None

    # resources

    def srgb_mips(self):
        """The compute builder of sRGB mip levels (``srgbmips``), made once; None where it cannot be made (no
        shader baker, a refused pipeline): the levels are then built on the CPU."""
        if not self._m_srgb_mips:
            from . import srgbmips

            try:
                self._m_srgb_mips.append(srgbmips.CSrgbMips(self.m_rhi))
            except Exception:  # the baker missing or refusing: the CPU path stays
                self._m_srgb_mips.append(None)
        return self._m_srgb_mips[0]

    def release(self) -> None:
        """``CRecorder.release``, and the builder of sRGB levels."""
        super().release()
        for builder in self._m_srgb_mips:
            if builder is not None:
                builder.release()
        self._m_srgb_mips = []

    def program(self, vertex_shader: str, fragment_shader: str, **_unused) -> CProgram:
        return CProgram(self, vertex_shader, fragment_shader)

    def buffer(self, data=None, reserve: int = 0, dynamic: bool = False) -> CBuffer:
        return CBuffer(self, data, reserve, dynamic)

    def vertex_array(self, program, content, index_buffer=None, index_element_size=4, skip_errors=False, **_):
        return CVertexArray(self, program, content, index_buffer, index_element_size, skip_errors)

    def _texture(self, size_px, components, data, dtype="f1", kind="2d", samples=0, srgb=False) -> CTexture:
        texture = CTexture(self, tuple(size_px), components, dtype, kind, samples, srgb)
        texture.m_target = data is None or kind == "depth"
        texture.create()
        if data is not None:
            texture.write(data)
        elif kind != "depth" and not samples:  # moderngl's textures start as zeros
            texture.write(bytes(int(np.prod(size_px)) * components * np.dtype(texture._format()[1]).itemsize))
        return texture

    def texture(self, size, components, data=None, samples=0, alignment=1, dtype="f1", internal_format=None):
        srgb = internal_format in (SRGB8, SRGB8_ALPHA8)  # three components are stored as four: sRGB all the same
        if srgb and self.m_decode_srgb and data is not None and dtype == "f1":
            # QRhi's OpenGL backend samples sRGB textures undecoded: decoded here, kept as half floats
            pixels = np.frombuffer(bytes(data), np.uint8).reshape(-1, components)
            linear = mipchain.srgb_to_linear(pixels[:, :3])
            if components == 4:
                linear = np.concatenate([linear, pixels[:, 3:] / 255.0], axis=1)
            return self._texture(size, components, linear.astype(np.float16).tobytes(), "f2", "2d", samples)
        return self._texture(size, components, data, dtype, "2d", samples, srgb)

    def texture_array(self, size, components, data=None, alignment=1, dtype="f1"):
        return self._texture(size, components, data, dtype, "array")

    def texture3d(self, size, components, data=None, alignment=1, dtype="f1"):
        return self._texture(size, components, data, dtype, "3d")

    def native_texture(self, size_px, qformat, samples: int = 0, srgb: bool = False, storage: bool = False) -> CTexture:
        """A render target in a QRhi format of its own (``QRhiTexture.Format``), for passes that are not ours;
        ``storage``: a compute pass writes it (``native_pass``)."""
        texture = CTexture(self, tuple(size_px), 4, samples=samples, m_srgb=srgb, m_qformat=qformat, m_storage=storage)
        texture.create()
        return texture

    def depth_texture(self, size, data=None, samples=0, alignment=4) -> CTexture:
        texture = self._texture(size, 1, None, "f4", "depth", samples)
        texture.compare_func = "<="
        return texture

    def renderbuffer(self, size, components=4, samples=0, dtype="f1") -> CTexture:
        return self._texture(size, components, None, dtype, "2d", samples or 1)

    def depth_renderbuffer(self, size, samples=0) -> CTexture:
        return self._texture(size, 1, None, "f4", "depth", samples or 1)

    def framebuffer(self, color_attachments=(), depth_attachment=None) -> CFramebuffer:
        colors = [color_attachments] if isinstance(color_attachments, CTexture) else list(color_attachments)
        return CFramebuffer(self, colors, depth_attachment)

    def detect_framebuffer(self, glo=None) -> CFramebuffer:
        return self.screen

    # frames
    def begin(self, command_buffer, color_texture) -> CFramebuffer:
        """Start recording a frame into the view's image ``color_texture`` (a ``QRhiTexture``); returns the frame's
        screen, what moderngl's ``detect_framebuffer`` gives."""
        self._m_command_buffer = command_buffer
        size_px = color_texture.pixelSize()
        width_px, height_px = size_px.width(), size_px.height()
        ident = color_texture.globalResourceId()
        # a resize keeps the texture object (and its id) but changes its size: either one makes the screen anew
        if (
            self.screen is None
            or self.screen.color_attachments[0].m_native_id != ident
            or self.screen.size != (width_px, height_px)
        ):
            if self.screen is not None:
                self.screen.release()
                self.screen.depth_attachment.release()
            color = CTexture(
                self, (width_px, height_px), 4, native=color_texture, m_serial=rhipass.CRecorder.next_serial()
            )
            color.m_native_id = ident
            self.screen = CFramebuffer(
                self, [color], self._texture((width_px, height_px), 1, None, "f4", "depth"), m_display=True
            )
        self._m_fbo, self.viewport = self.screen, self.screen.viewport
        return self.screen

    def end(self) -> None:
        """Run what the frame recorded (the passes in order) into the command buffer ``begin`` was given."""
        self.m_make_spent, self.m_make_left = 0.0, 0  # (``nativepass.prepared`` counts them while the frame runs)
        self._run(self._m_command_buffer)
        self._m_command_buffer = None

    # state
    def enable(self, flags: int) -> None:
        self.m_flags |= flags

    def disable(self, flags: int) -> None:
        self.m_flags &= ~flags

    def finish(self) -> None:
        pass
