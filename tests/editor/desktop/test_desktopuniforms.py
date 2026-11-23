"""What the view's model programs hold (``editor.desktop.desktopuniforms``) and the shadow map a still frame keeps
(``editor.desktop.desktoprender``)."""

import numpy as np
import pytest

from annotoolbox.editor.desktop.desktopuniforms import CUniformMemory


class FakeUniform:
    """A uniform that counts what is written to it."""

    def __init__(self) -> None:
        self.writes = 0
        self.held = None

    @property
    def value(self):
        return self.held

    @value.setter
    def value(self, value) -> None:
        self.writes += 1
        self.held = value

    def write(self, data: bytes) -> None:
        self.writes += 1
        self.held = data


class FakeProgram:
    """A program whose uniforms appear when asked for."""

    def __init__(self) -> None:
        self.uniforms: dict[str, FakeUniform] = {}

    def __getitem__(self, name: str) -> FakeUniform:
        return self.uniforms.setdefault(name, FakeUniform())


class FakeBoundTexture:
    """A texture that counts how often it is bound."""

    def __init__(self) -> None:
        self.uses: list[int] = []

    def use(self, unit: int) -> None:
        self.uses.append(unit)


def test_a_value_the_program_holds_is_not_written_again():
    memory, program = CUniformMemory(), FakeProgram()
    memory.put(program, "emissive", 0.0)
    memory.put(program, "emissive", 0.0)
    memory.put(program, "tint", (0.5, 0.5, 0.5))
    memory.put(program, "tint", (0.5, 0.5, 0.5))
    assert program["emissive"].writes == 1 and program["tint"].writes == 1
    memory.put(program, "tint", (1.0, 0.5, 0.5))
    assert program["tint"].writes == 2 and program["tint"].held == (1.0, 0.5, 0.5)
    assert program["emissive"].writes == 1


def test_a_false_flag_is_written_once_and_then_held():
    """``False == 0`` and a missing name must not be taken for a held value."""
    memory, program = CUniformMemory(), FakeProgram()
    memory.put(program, "textured", False)
    memory.put(program, "textured", False)
    assert program["textured"].writes == 1
    memory.put(program, "textured", True)
    assert program["textured"].writes == 2


def test_the_same_matrix_is_held_and_another_is_written():
    memory, program = CUniformMemory(), FakeProgram()
    one, other = np.identity(4, "f4").tobytes(), np.eye(4, k=1, dtype="f4").tobytes()
    memory.put_bytes(program, "part", one)
    memory.put_bytes(program, "part", one)
    memory.put_bytes(program, "part", other)
    assert program["part"].writes == 2 and program["part"].held == other


def test_programs_hold_their_own_values():
    memory, first, second = CUniformMemory(), FakeProgram(), FakeProgram()
    memory.put(first, "kind", 1)
    memory.put(second, "kind", 1)
    assert first["kind"].writes == 1 and second["kind"].writes == 1


def test_a_texture_is_bound_once_per_unit_until_something_else_binds():
    memory, one, other = CUniformMemory(), FakeBoundTexture(), FakeBoundTexture()
    memory.bind(one, 0)
    memory.bind(one, 0)
    memory.bind(other, 2)
    assert one.uses == [0] and other.uses == [2]
    memory.bind(other, 0)
    memory.bind(one, 0)
    assert one.uses == [0, 0] and other.uses == [2, 0]


def test_forgetting_makes_every_value_and_binding_written_again():
    memory, program, texture = CUniformMemory(), FakeProgram(), FakeBoundTexture()
    memory.put(program, "kind", 0)
    memory.put_bytes(program, "part", b"x")
    memory.bind(texture, 0)
    memory.forget()
    memory.put(program, "kind", 0)
    memory.put_bytes(program, "part", b"x")
    memory.bind(texture, 0)
    assert program["kind"].writes == 2 and program["part"].writes == 2 and texture.uses == [0, 0]


def _renderer():
    moderngl = pytest.importorskip("moderngl")
    try:
        ctx = moderngl.create_standalone_context(require=330)
    except Exception as exc:  # no OpenGL 3.3 driver here (a CI runner)
        pytest.skip(f"no OpenGL context: {exc}")
    from tests.editor.editorfakes import edit_island as island

    from annotoolbox.editor.desktop import desktoprender
    from annotoolbox.editor.editsession import CEditSession

    session = CEditSession(island())
    session.finish_loading()
    renderer = desktoprender.CRenderer(ctx, session, None)
    size = (320, 200)
    fbo = ctx.framebuffer([ctx.renderbuffer(size, 4)], ctx.depth_renderbuffer(size))
    return renderer, session, fbo, size


def test_a_still_frame_keeps_the_shadow_map_and_an_edit_or_a_move_draws_it_again():
    from annotoolbox.editor.desktop.desktoprender import SViewOptions
    from annotoolbox.render.camera import COrbitCamera

    renderer, session, fbo, size = _renderer()
    camera = COrbitCamera((session.m_size_tiles / 2, 0.0, session.m_size_tiles / 2), 14)
    options = SViewOptions()
    renderer.render(fbo, size, camera, options)
    first = renderer._m_shadow_taken
    assert first is not None
    renderer.render(fbo, size, camera, options)
    assert renderer._m_shadow_taken is first  # the same sun, view and scene: not drawn again

    marker = next(entry for entry in session.m_entries if entry.marker == "Herd")
    session.transform(marker, position=[marker.position[0] + 1, marker.position[1], marker.position[2]])
    renderer.render(fbo, size, camera, options)
    second = renderer._m_shadow_taken
    assert second is not first  # an edit of the scene

    renderer.render(fbo, size, camera, options)
    assert renderer._m_shadow_taken is second
    camera.m_yaw_rad += 0.3
    renderer.render(fbo, size, camera, options)
    third = renderer._m_shadow_taken
    assert third is not second  # the camera moved: its level of detail may differ
    layers = dict(options.layers, objects=False)
    renderer.render(fbo, size, camera, SViewOptions(layers=layers))
    assert renderer._m_shadow_taken is not third  # a layer was switched off
    renderer.release()
