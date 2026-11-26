"""The resources of one of the game's shaders on QRhi, shared by the passes that bind a shader by name: a dynamic
uniform buffer per constant buffer, a stand-in for every resource the frame does not hand over, the binding set made
again when the frame's inputs change (the old one retired a frame later), the constants written and uploaded.

``CResources(rhi, numbers, stages, constants, declared)`` is the entry; ``rebind(batch, inputs)`` then ``write(batch,
fill)`` before a pass, ``srb`` the binding set a pipeline is made against, ``release()`` at the end. ``CComputeStage``,
``CPostPass`` and ``CDeferredShading`` each add their pipeline and what they draw or dispatch.
docs/rendering/frame-117-viewer.md.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QRhiShaderResourceBinding

from . import gamepool
from . import gameshader as gs
from .gamedraw import fixed_sampler, stand_in, uniform_buffer

Binding = QRhiShaderResourceBinding
RAW = ("byteaddress", "uav_rwbyteaddress")


def keyed(fill: Callable[[gs.CConstants], None], key: tuple) -> Callable[[gs.CConstants], None]:
    """``fill`` with its ``key``: everything it writes, compared by content (``gameshader.camera_key``,
    ``value_key``). ``CResources.write`` leaves a buffer that a fill of an equal key filled last as it is."""
    fill.key = key
    return fill


class CResources:
    """What a shader binds and the frame's constants (module docstring).

    ``numbers``: (name, kind) -> binding number; ``stages(key)``: the stage flags of a binding; ``constants``: the
    ``gameshader.CConstants`` by buffer name; ``declared(name)``: the declared type of a texture. ``stand_in(batch,
    name, kind)`` is a pass's own answer for a resource the frame does not hand (None: the default one, made once);
    ``raw`` binds a structured buffer (QRhi's Direct3D 12 backend binds every storage buffer as an unordered-access
    view, ``bufferLoad`` reads it only); ``address`` is the address mode of a sampler whose name says none.
    """

    def __init__(
        self,
        rhi,
        numbers: dict[tuple[str, str], int],
        stages: Callable[[tuple[str, str]], object],
        constants: dict[str, gs.CConstants],
        declared: Callable[[str], str],
        *,
        stand_in: Callable | None = None,
        raw: Callable = Binding.bufferLoadStore,
        address: str = "WRAP",
    ) -> None:
        self.m_rhi, self.m_numbers, self.m_stages, self.m_constants, self.m_declared = (
            rhi,
            numbers,
            stages,
            constants,
            declared,
        )
        self.m_hook, self.m_raw, self.m_address = stand_in, raw, address
        self.m_buffers: dict[str, object] = {}
        self.m_written: dict[str, object] = {}  # a constant buffer -> the key of the fill that last filled it
        self.m_made: dict[str, object] = {}  # the default stand-ins, made once
        self.m_shared: list = []  # the device's stand-ins it uses (``gamepool.give_back`` on release)
        self.m_keep: list = []  # everything made here, and what the owner adds (its pipeline): destroyed on release
        self.m_srb = None
        self._m_bound: tuple | None = ()
        self._m_held: tuple = ()  # the inputs bound: kept, so that their ids are theirs alone while ``_bound`` names them
        self._m_retired: list = []

    def uniform(self, name: str):
        """The dynamic uniform buffer of constant buffer ``name``, made once."""
        if name not in self.m_buffers:
            self.m_buffers[name] = uniform_buffer(self.m_rhi, len(self.m_constants[name].m_data))
            self.m_keep.append(self.m_buffers[name])
        return self.m_buffers[name]

    def resource(self, batch, name: str, kind: str, inputs: dict):
        """What binds as ``name``: the frame's input, else the pass's own stand-in, else the default one of the kind
        (a sampler by the words of its name, a texture or buffer zeroed through ``batch``)."""
        found = inputs.get(name)
        if found is None and self.m_hook is not None:
            found = self.m_hook(batch, name, kind)
        if found is None:
            if name not in self.m_made:
                if kind == "sampler":
                    self.m_made[name] = fixed_sampler(self.m_rhi, name, self.m_address)
                elif kind == "texture":  # sampled only, one for the device: each stage made dozens, 0.15 s a stage
                    declared = self.m_declared(name)
                    self.m_made[name], owner = gamepool.shared(self.m_rhi, ("stand-in", kind, declared),
                                                               lambda: stand_in(self.m_rhi, kind, declared, batch))  # fmt: skip
                    (self.m_shared if owner is not None else self.m_keep).append(owner or self.m_made[name])
                    return self.m_made[name]
                else:
                    self.m_made[name] = stand_in(self.m_rhi, kind, self.m_declared(name), batch)
                self.m_keep.append(self.m_made[name])
            found = self.m_made[name]
        return found

    def _bindings(self, batch, inputs: dict) -> list:
        out = []
        for key, number in self.m_numbers.items():
            name, kind = key
            shader_stages = self.m_stages(key)
            if kind == "cbuffer":
                out.append(Binding.uniformBuffer(number, shader_stages, self.uniform(name)))
                continue
            resource = self.resource(batch, name, kind, inputs)
            if kind == "sampler":
                out.append(Binding.sampler(number, shader_stages, resource))
            elif kind == "texture":
                out.append(Binding.texture(number, shader_stages, resource))
            elif kind in RAW:
                out.append(self.m_raw(number, shader_stages, resource))
            else:
                out.append(Binding.imageStore(number, shader_stages, resource, 0))
        return out

    def invalidate(self) -> None:
        """The next ``rebind`` makes the binding set again (a resource the pass handed out was replaced)."""
        self._m_bound = None

    def rebind(self, batch, inputs: dict) -> bool:
        """Make the binding set again when ``inputs`` changed (the old one is destroyed a frame later); True then."""
        bound = tuple(sorted((k, id(value)) for k, value in inputs.items()))
        if self.m_srb is not None and bound == self._m_bound:
            return False
        for old in self._m_retired:  # the frame that read them is done
            old.destroy()
        self._m_retired = [self.m_srb] if self.m_srb is not None else []
        self.m_srb = self.m_rhi.newShaderResourceBindings()
        self.m_srb.setBindings(self._bindings(batch, inputs))
        self.m_srb.create()
        self._m_bound, self._m_held = bound, tuple(inputs.values())
        return True

    def write(self, batch, fill: Callable[[gs.CConstants], None] | None = None) -> None:
        """Every constant buffer filled by ``fill(c)`` and uploaded into ``batch``; a buffer last filled by a fill of
        an equal ``key`` (``keyed``) is left as it is."""
        key = getattr(fill, "key", None)
        for name, constants in self.m_constants.items():
            if key is not None and self.m_written.get(name) == key:
                continue
            if fill is not None:
                fill(constants)
            batch.updateDynamicBuffer(self.uniform(name), 0, bytes(constants.m_data))
            self.m_written[name] = key

    def release(self) -> None:
        for resource in [*self.m_keep, *self._m_retired, *([self.m_srb] if self.m_srb is not None else [])]:
            resource.destroy()
        for owner in self.m_shared:
            gamepool.give_back(owner)
        self.m_shared = []
        self.m_keep, self._m_retired, self.m_buffers, self.m_made, self.m_written = [], [], {}, {}, {}
        self.m_srb, self._m_bound, self._m_held = None, (), ()
