"""The game's compute shaders recorded on QRhi's own command list between the passes of QRhi's frame: each dispatch's
constants in static buffers written on the module's list ahead of QRhi's frame, the states carried by hand (NVRHI
tracks none on a foreign list; QRhi must find its resources as it left them).

``CForeignKernel(device, shader, reflection, stand_ins)`` is one compute shader so bound; ``with Recording(device, command_buffer,
bridge)`` one stretch of work on QRhi's list (``nativepass.compute_pass`` runs one in a compute pass); ``CPins`` makes
QRhi's state of one of its buffers known. docs/rendering/native-backend.md 12.
"""

from __future__ import annotations

from collections import deque

from PySide6.QtGui import QRhiReadbackResult

from . import gameshader as gs
from . import nativegpu, nativelayout, nativematerials

SHADER_READ = 0x40 | 0x80  # NVRHI's resting state of the module's textures and buffers: QRhi's sampled one too
UNORDERED = 0x8
COPY_DEST, COPY_SOURCE = 0x400, 0x800
CONSTANT_SLOT = 256
READS = ("texture", "typed", "structured", "raw")
WRITES = ("rwtexture", "rwtyped", "rwstructured", "rwraw")
SETS = 8  # binding sets a kernel keeps per slot for what it was handed last [ours]
PINS = 24  # read-backs kept alive until QRhi has filled them (a few frames of three a frame) [ours]


class CNativeError(RuntimeError):
    """A shader asks for something the native path cannot bind: the current path draws it."""


class CStandIns(nativematerials.CStandIns):
    """What a pass binds where the frame hands nothing, as the current path's compute stages stand in: zeros of the
    declared kind, white (1) for the ``white`` names; a storage texture or buffer to write into and nobody reads."""

    def __init__(self, device, command_list, white=()) -> None:
        super().__init__(device, command_list)
        self.m_white = frozenset(white)

    def white_texture(self, kind: str = "2d"):
        return self._made(("white", kind), lambda: self._filled(kind, "RGBA8_UNORM", 255))

    def of(self, item: nativelayout.SItem, binding, stride: int = 16):
        if item.kind == "texture":
            kind = nativematerials.DIMENSIONS.get(binding.dimension, "2d")
            if item.name in self.m_white:
                return self.white_texture(kind)
            texture_format = "R32_UINT" if binding.returns in nativematerials.INTEGER else "RGBA8_UNORM"
            return self._made((kind, texture_format), lambda: self._filled(kind, texture_format, 0))
        if item.kind == "rwtexture":
            kind = nativematerials.DIMENSIONS.get(binding.dimension, "2d")
            kind = "2darray" if kind.startswith("cube") else kind  # no unordered view of a cube
            texture_format = {3: "R32_SINT", 4: "R32_UINT"}.get(binding.returns, "R32_FLOAT")
            layers = 4 if kind in ("3d", "2darray") else 1
            return self._made(("uav", kind, texture_format), lambda: self.m_device.texture(4, 4, texture_format, depth=layers, kind=kind,
                                                                             usage="uav", name="stand-in uav"))  # fmt: skip
        if item.kind in ("structured", "rwstructured"):
            usage = "structured,uav" if item.kind == "rwstructured" else "structured"
            return self._made((usage, stride), lambda: self._zeros(max(16, stride) * 64, usage, stride))
        if item.kind in ("raw", "rwraw"):
            return self.raw(item.kind == "rwraw")
        raise CNativeError(f"nothing stands in for {item.name} ({item.kind})")


class CForeignKernel:
    """One of the game's compute shaders (or a draw's stages: ``draw``) for QRhi's list (module docstring): its
    binding plan (constant buffers in the tables, as static buffers), layouts, samplers and pipeline; per ``slot``
    (one dispatch of a frame) its own constant buffers and sets."""

    def __init__(self, device, shader, reflection, stand_ins: CStandIns, stages: dict | None = None,
                 state: dict | None = None, inputs=None) -> None:  # fmt: skip
        self.m_device, self.m_name, self.m_stand_ins = device, shader.name, stand_ins
        reflections = [stage_reflection for _s, stage_reflection in stages.values()] if stages else [reflection]
        self.m_plan = nativelayout.SBindingPlan.of(reflections)
        if any(spec.bindless for spec in self.m_plan.layouts):
            raise CNativeError(f"{shader.name}: an unbounded array")
        self.m_layouts = nativegpu.layouts(device, self.m_plan)
        self.m_samplers = nativegpu.samplers(device, self.m_plan)
        # the root constants a draw passes as NVRHI's push constants: zeros (the composite's flags)
        self.m_push = bytes(self.m_plan.push.count) if self.m_plan.push is not None else b""
        if stages:  # without ``inputs`` a draw without vertices (the engine's full-screen strip: SV_VertexID)
            self.m_shaders = {
                stage: device.shader(stage, shader_stage.bytecode, shader_stage.name)
                for stage, (shader_stage, _r) in stages.items()
            }
            stage_shaders = self.m_shaders
            self.m_pipeline = device.graphics_pipeline(
                stage_shaders["vs"], stage_shaders.get("ps"), inputs, self.m_layouts, state
            )
        else:
            self.m_shaders = {"cs": device.shader("cs", shader.bytecode, shader.name)}
            self.m_pipeline = device.compute_pipeline(self.m_shaders["cs"], self.m_layouts)
        tabled = {item.name for spec in self.m_plan.layouts for item in spec.items if item.kind == "cbuffer"}
        every = {
            constant_buffer.name: constant_buffer
            for stage_reflection in reflections
            for constant_buffer in stage_reflection.cbuffers
        }
        self.m_cbuffers = {name: constant_buffer for name, constant_buffer in every.items() if name in tabled}
        self.m_elements = {
            name: constant_buffer.size for name, constant_buffer in every.items() if name not in tabled
        }  # structured strides
        self.m_bindings = {
            binding.name: binding for stage_reflection in reflections for binding in stage_reflection.bindings
        }
        self.m_buffers = nativegpu.CKeptBuffers(device, CONSTANT_SLOT)  # (slot, name) -> its constant buffer
        self.m_made: dict[tuple, tuple] = {}  # (slot, layout) -> (key, held, set)

    @classmethod
    def draw(cls, device, stages: dict, state: dict, stand_ins: CStandIns, inputs=None) -> CForeignKernel:
        """A draw's stages (``{"vs": (shader, reflection), "ps": ...}``) with the pipeline's fixed ``state`` and
        vertex input (``device.input_layout``; None: none)."""
        return cls(device, stages["vs"][0], None, stand_ins, stages, state, inputs)

    def write(self, command_list, slot, fill, memo: dict | None = None) -> dict:
        """The constant buffers of ``slot`` filled by ``fill(constants)``, written on the module's list ``command_list`` where their
        bytes changed; by name. ``memo``: the bytes of buffers alike that kernels of one fill share (a frame's)."""
        out = {}
        for name, constant_buffer in self.m_cbuffers.items():
            shape = (name, constant_buffer.size, len(constant_buffer.variables))
            data = memo.get(shape) if memo is not None else None
            if data is None:
                constants = gs.CConstants(constant_buffer)
                if fill is not None:
                    fill(constants)
                data = bytes(constants.m_data)
                if memo is not None:
                    memo[shape] = data
            out[name] = self.m_buffers.get(command_list, (slot, name), data, name=name)
        return out

    def resolve(self, named: dict) -> dict:
        """Every item's resource by name: the frame's, else its stand-in."""
        out = {}
        for spec in self.m_plan.layouts:
            for item in spec.items:
                if item.kind in ("sampler", "push", "cbuffer"):
                    continue
                found = named.get(item.name)
                if found is None:
                    found = self.m_stand_ins.of(item, self.m_bindings[item.name], self.m_elements.get(item.name, 16))
                out[item.name] = found
        return out

    def sets(self, slot, resolved: dict, buffers: dict) -> list:
        """The sets of ``slot`` for these resources, made when what they bind changed."""
        out = []
        for index, spec in enumerate(self.m_plan.layouts):
            held = tuple(
                resolved.get(item.name) if item.kind != "cbuffer" else buffers.get(item.name) for item in spec.items
            )
            key = tuple(map(id, held))
            found = self.m_made.get((slot, index))
            if found is None or found[0] != key:
                if found is not None:
                    found[2].release()  # late: a frame in flight may still read it
                named = {
                    item.name: resource for item, resource in zip(spec.items, held, strict=True) if resource is not None
                }
                made = nativegpu.binding_set(self.m_device, self.m_layouts[index], spec, named, self.m_samplers)
                found = self.m_made[(slot, index)] = (key, held, made)
            out.append(found[2])
        return out

    def uses(self, resolved: dict) -> list[tuple[object, int]]:
        """(resource, the state the dispatch needs it in) of everything it reads or writes."""
        out = []
        for spec in self.m_plan.layouts:
            for item in spec.items:
                if item.kind in READS:
                    out.append((resolved[item.name], SHADER_READ))
                elif item.kind in WRITES:
                    out.append((resolved[item.name], UNORDERED))
        return out

    def record(self, recording: CRecording, slot, named: dict, buffers: dict, groups: tuple[int, int, int]) -> None:
        """One dispatch on ``recording``'s list with ``slot``'s constant buffers (``write``) and ``named`` resources."""
        resolved = self.resolve(named)
        recording.dispatch(self.m_pipeline, self.sets(slot, resolved, buffers), self.uses(resolved), groups)

    def record_draw(self, recording: CRecording, slot, named: dict, buffers: dict, size_px: tuple[int, int],
                    vertices: list | None = None) -> None:  # fmt: skip
        """The full-screen strip into the targets QRhi has bound (``draw``'s kernels), or with ``vertices`` (the
        vertex buffers of a full-screen triangle) that triangle."""
        resolved = self.resolve(named)
        sets, uses = self.sets(slot, resolved, buffers), self.uses(resolved)
        recording.draw(self.m_pipeline, sets, uses, size_px, vertices or [], 3 if vertices else 4, self.m_push)

    def release(self) -> None:
        for _key, _held, made in self.m_made.values():
            made.release()
        self.m_buffers.release()
        self.m_pipeline.release()
        for shader in self.m_shaders.values():
            shader.release()
        for layout in self.m_layouts:
            layout.release()
        self.m_made = {}


class CRecording:
    """One stretch of the module's work on QRhi's list (module docstring): every resource it touches leaves in the
    state it was found in (QRhi's, or NVRHI's resting one for the module's); a dispatch that writes what an earlier
    one wrote waits for it."""

    def __init__(self, device, command_buffer, bridge) -> None:
        self.m_device, self.m_bridge = device, bridge
        self.m_module = nativegpu.load()
        self.m_list = nativegpu.command_list(command_buffer)
        self.m_home: dict[int, int] = {}  # resource -> the state it goes back to
        self.m_now: dict[int, int] = {}
        self.m_dirty: set[int] = set()  # written as unordered access since the last barrier

    def qrhi(self, texture):
        """One of QRhi's textures as the module's, found and left in the state QRhi tracks."""
        wrapped, resource = self.m_bridge.texture(texture)
        self.m_home.setdefault(resource, self.m_bridge.m_reads[resource])
        return wrapped

    def qrhi_buffer(self, buffer, stride: int, state: int):
        """One of QRhi's buffers as the module's, in ``state`` (``CPins`` made it known) and left so."""
        wrapped, resource = self.m_bridge.buffer(buffer, stride, state)
        self.m_home.setdefault(resource, state)
        return wrapped

    def handed(self, qrhi_texture, texture):
        """A module texture handed to QRhi (``qrhi_texture``): bound as the module's, in the state QRhi tracks for ``qrhi_texture``."""
        import shiboken6

        _resource, state = self.m_module.qrhi_texture(int(shiboken6.getCppPointer(qrhi_texture)[0]))
        self.m_home.setdefault(texture.native, state)
        return texture

    def _move(self, uses) -> None:
        items, wait = [], False
        for resource, state in uses:
            pointer = resource.native
            now = self.m_now.get(pointer, self.m_home.setdefault(pointer, SHADER_READ))
            if now != state:
                items.append((pointer, now, state))
                self.m_now[pointer] = state
            elif state == UNORDERED and pointer in self.m_dirty:
                wait = True
        if wait:
            self.m_module.uav_barrier(self.m_list)
            self.m_dirty.clear()
        if items:
            self.m_module.transitions(self.m_list, items)
        self.m_dirty.update(resource.native for resource, state in uses if state == UNORDERED)

    def dispatch(self, pipeline, sets: list, uses: list, groups: tuple[int, int, int]) -> None:
        self._move(uses)
        self.m_module.record_dispatch(self.m_device, self.m_list, pipeline, sets, tuple(groups))

    def draw(self, pipeline, sets: list, uses: list, size_px: tuple[int, int], vertices: list, count: int = 3,
             push: bytes = b"") -> None:  # fmt: skip
        """A draw into the targets QRhi has bound (a render pass begun with ``ExternalContent``)."""
        self._move(uses)
        viewport = (0.0, 0.0, float(size_px[0]), float(size_px[1]))
        self.m_module.record_external(self.m_device, self.m_list, pipeline, sets, count, viewport, vertices, push=push)

    def copy_buffer(self, destination, source, size_bytes: int) -> None:
        self._move([(destination, COPY_DEST), (source, COPY_SOURCE)])
        self.m_module.copy_buffer(self.m_list, destination.native, 0, source.native, 0, size_bytes)

    def finish(self) -> None:
        """Everything back in the state it was found in."""
        items = [
            (pointer, now, self.m_home[pointer]) for pointer, now in self.m_now.items() if now != self.m_home[pointer]
        ]
        if self.m_dirty and not items:
            self.m_module.uav_barrier(self.m_list)
        if items:
            self.m_module.transitions(self.m_list, items)
        self.m_now, self.m_dirty = {}, set()
        self.m_bridge.m_reads = {}

    def __enter__(self) -> CRecording:
        return self

    def __exit__(self, *_exc) -> None:
        self.finish()  # also after a raise: QRhi records the rest of its frame from the states it tracks


class CPins:
    """QRhi's state of one of its buffers made known before the module's work on QRhi's list: a read-back leaves it a
    copy's source, an upload a copy's destination (each in a pass's opening batch, so QRhi records the barrier into
    the state it believes, and the module finds the buffer there)."""

    def __init__(self) -> None:
        self.m_kept: deque = deque(maxlen=PINS)  # QRhi writes into a read-back's result after the frame

    def copy_source(self, batch, buffer, size_bytes: int) -> int:
        result = QRhiReadbackResult()
        batch.readBackBuffer(buffer, 0, size_bytes, result)
        self.m_kept.append(result)
        return COPY_SOURCE

    @staticmethod
    def copy_dest(batch, buffer, data: bytes) -> int:
        batch.uploadStaticBuffer(buffer, data)
        return COPY_DEST
