"""The resources of one of the game's shaders on QRhi, shared by the passes that bind a shader by name: a dynamic
uniform buffer per constant buffer, a stand-in for every resource the frame does not hand over, the binding set made
again when the frame's inputs change (the old one retired a frame later), the constants written and uploaded.

``Resources(rhi, numbers, stages, constants, declared)`` is the entry; ``rebind(batch, inputs)`` then ``write(batch,
fill)`` before a pass, ``srb`` the binding set a pipeline is made against, ``release()`` at the end. ``ComputeStage``,
``PostPass`` and ``DeferredShading`` each add their pipeline and what they draw or dispatch.
docs/rendering/frame-117-viewer.md.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QRhiShaderResourceBinding

from . import gameshader as gs
from .gamedraw import fixed_sampler, stand_in, uniform_buffer

B = QRhiShaderResourceBinding
RAW = ("byteaddress", "uav_rwbyteaddress")


class Resources:
    """What a shader binds and the frame's constants (module docstring).

    ``numbers``: (name, kind) -> binding number; ``stages(key)``: the stage flags of a binding; ``constants``: the
    ``gameshader.Constants`` by buffer name; ``declared(name)``: the declared type of a texture. ``stand_in(batch,
    name, kind)`` is a pass's own answer for a resource the frame does not hand (None: the default one, made once);
    ``raw`` binds a structured buffer (QRhi's Direct3D 12 backend binds every storage buffer as an unordered-access
    view, ``bufferLoad`` reads it only); ``address`` is the address mode of a sampler whose name says none.
    """

    def __init__(
        self,
        rhi,
        numbers: dict[tuple[str, str], int],
        stages: Callable[[tuple[str, str]], object],
        constants: dict[str, gs.Constants],
        declared: Callable[[str], str],
        *,
        stand_in: Callable | None = None,
        raw: Callable = B.bufferLoadStore,
        address: str = "WRAP",
    ) -> None:
        self.rhi, self.numbers, self.stages, self.constants, self.declared = rhi, numbers, stages, constants, declared
        self.hook, self.raw, self.address = stand_in, raw, address
        self.buffers: dict[str, object] = {}
        self.made: dict[str, object] = {}  # the default stand-ins, made once
        self.keep: list = []  # everything made here, and what the owner adds (its pipeline): destroyed on release
        self.srb = None
        self._bound: tuple | None = ()
        self._held: tuple = ()  # the inputs bound: kept, so that their ids are theirs alone while ``_bound`` names them
        self._retired: list = []

    def uniform(self, name: str):
        """The dynamic uniform buffer of constant buffer ``name``, made once."""
        if name not in self.buffers:
            self.buffers[name] = uniform_buffer(self.rhi, len(self.constants[name].data))
            self.keep.append(self.buffers[name])
        return self.buffers[name]

    def resource(self, batch, name: str, kind: str, inputs: dict):
        """What binds as ``name``: the frame's input, else the pass's own stand-in, else the default one of the kind
        (a sampler by the words of its name, a texture or buffer zeroed through ``batch``)."""
        found = inputs.get(name)
        if found is None and self.hook is not None:
            found = self.hook(batch, name, kind)
        if found is None:
            if name not in self.made:
                if kind == "sampler":
                    self.made[name] = fixed_sampler(self.rhi, name, self.address)
                else:
                    self.made[name] = stand_in(self.rhi, kind, self.declared(name), batch)
                self.keep.append(self.made[name])
            found = self.made[name]
        return found

    def _bindings(self, batch, inputs: dict) -> list:
        out = []
        for key, number in self.numbers.items():
            name, kind = key
            st = self.stages(key)
            if kind == "cbuffer":
                out.append(B.uniformBuffer(number, st, self.uniform(name)))
                continue
            res = self.resource(batch, name, kind, inputs)
            if kind == "sampler":
                out.append(B.sampler(number, st, res))
            elif kind == "texture":
                out.append(B.texture(number, st, res))
            elif kind in RAW:
                out.append(self.raw(number, st, res))
            else:
                out.append(B.imageStore(number, st, res, 0))
        return out

    def invalidate(self) -> None:
        """The next ``rebind`` makes the binding set again (a resource the pass handed out was replaced)."""
        self._bound = None

    def rebind(self, batch, inputs: dict) -> bool:
        """Make the binding set again when ``inputs`` changed (the old one is destroyed a frame later); True then."""
        bound = tuple(sorted((k, id(v)) for k, v in inputs.items()))
        if self.srb is not None and bound == self._bound:
            return False
        for old in self._retired:  # the frame that read them is done
            old.destroy()
        self._retired = [self.srb] if self.srb is not None else []
        self.srb = self.rhi.newShaderResourceBindings()
        self.srb.setBindings(self._bindings(batch, inputs))
        self.srb.create()
        self._bound, self._held = bound, tuple(inputs.values())
        return True

    def write(self, batch, fill: Callable[[gs.Constants], None] | None = None) -> None:
        """Every constant buffer filled by ``fill(c)`` and uploaded into ``batch``."""
        for name, c in self.constants.items():
            if fill is not None:
                fill(c)
            batch.updateDynamicBuffer(self.uniform(name), 0, bytes(c.data))

    def release(self) -> None:
        for r in [*self.keep, *self._retired, *([self.srb] if self.srb is not None else [])]:
            r.destroy()
        self.keep, self._retired, self.buffers, self.made = [], [], {}, {}
        self.srb, self._bound, self._held = None, (), ()
