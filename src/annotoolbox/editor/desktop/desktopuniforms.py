"""What the view's model programs already hold (``desktoprender``): the values their uniforms were last set to and the
textures bound to their units.

A part that asks for what the program already has writes nothing: most parts of a frame share the plain values (no
flipbook, no foam, the same sampler units) and often a map. ``forget`` after anything else may have written the same
programs or bound those units (island-editor-view "GPU load").
"""

from __future__ import annotations


class CUniformMemory:
    """The last value written per program and uniform, and the texture bound per unit; ``put``, ``put_bytes`` and
    ``bind`` write only what differs."""

    def __init__(self) -> None:
        self.m_values: dict = {}  # program -> {uniform name: the value last written}
        self.m_units: dict[int, object] = {}  # texture unit -> the texture bound there

    def put(self, program, name: str, value) -> None:
        """``program[name].value = value`` unless the program holds that value (a number, a bool or a tuple of them)."""
        held = self.m_values.setdefault(program, {})
        if name in held and held[name] == value:
            return
        held[name] = value
        program[name].value = value

    def put_bytes(self, program, name: str, data: bytes) -> None:
        """``program[name].write(data)`` unless the program holds those bytes (a matrix as ``render.camera.gl``)."""
        held = self.m_values.setdefault(program, {})
        if name in held and held[name] == data:
            return
        held[name] = data
        program[name].write(data)

    def bind(self, texture, unit: int) -> None:
        """``texture.use(unit)`` unless that texture is bound there."""
        if self.m_units.get(unit) is texture:
            return
        self.m_units[unit] = texture
        texture.use(unit)

    def forget(self) -> None:
        """Hold nothing: the next ``put`` and ``bind`` write (other code wrote the programs or bound the units)."""
        self.m_values.clear()
        self.m_units.clear()
