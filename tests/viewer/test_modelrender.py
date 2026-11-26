"""The renderer's loads in the game's look (viewer-performance.md section 4, item 2): the plain look asks for no map
the game's look draws, the stand-in draws untextured while the shaders compile, and the game's jobs have workers of
their own."""

from threading import Event
from types import SimpleNamespace as N

import numpy as np
import pytest

pytest.importorskip("PySide6")

from annotoolbox.render.blocktexture import SBlockMap
from annotoolbox.render.loader import CLoader
from annotoolbox.viewer import modelrender
from annotoolbox.viewer.modellights import SHADERS
from annotoolbox.viewer.modelrender import CModelRenderer, SViewState


class FakeUniform:
    """One uniform of ``FakeLitProgram``: it keeps the value set."""

    def __init__(self, program: "FakeLitProgram", name: str) -> None:
        self.m_program, self.m_name = program, name

    def write(self, data) -> None:
        pass

    @property
    def value(self):
        return self.m_program.values.get(self.m_name)

    @value.setter
    def value(self, value) -> None:
        self.m_program.values[self.m_name] = value


class FakeLitProgram:
    """A shader program as ``_draw_lit`` sets it."""

    def __init__(self) -> None:
        self.values: dict = {}

    def __getitem__(self, name: str):
        return FakeUniform(self, name)


def renderer(*, look: str = SHADERS, stand_in: bool = False) -> tuple[CModelRenderer, list[str]]:
    """A renderer with no device: it records the files it is asked for."""
    r = CModelRenderer.__new__(CModelRenderer)
    asked: list[str] = []
    r.m_textures, r.m_failed, r.m_state = {}, {}, SViewState(look=look)
    r.m_loader, r.m_ctx = N(m_futures={}, pending=lambda: 0), N()
    r._ask = lambda key, work: asked.append(key)
    r._m_stand_in = stand_in
    r._m_dummy = N(use=lambda unit: None)
    r._m_lights = (0, b"", b"", b"")
    r._bind_joints = lambda prog, item: None
    r._put_light = lambda *args: None
    r.m_prog = {"lit": FakeLitProgram()}
    return r, asked


def lit_draw(r: CModelRenderer) -> FakeLitProgram:
    material = N(diffuse="maps/wall_diff.dds", normal="maps/wall_norm.dds", color=(0.2, 0.4, 0.8), opacity=1.0)
    item = {"gpu": False, "vaos": {"main": N(render=lambda: None)}}
    frame = N(view_proj=np.identity(4), lt=None, eye=None, shadow=None)
    r._draw_lit(material, item, np.identity(4), frame, False)
    return r.m_prog["lit"]


def test_the_stand_in_draws_untextured_and_asks_for_no_map():
    r, asked = renderer(stand_in=True)
    program = lit_draw(r)
    assert asked == []  # the game's shaders will draw this group: its plain maps are never decoded
    assert program.values["textured"] is False and program.values["tint"] == (0.6, 0.6, 0.6)


def test_a_group_the_game_leaves_is_drawn_by_the_plain_look_with_its_maps():
    r, asked = renderer(stand_in=False)
    lit_draw(r)
    assert [key.split(":", 2)[-1] for key in asked] == ["maps/wall_diff.dds", "maps/wall_norm.dds"]


def test_a_lazy_texture_is_used_when_it_is_in_and_never_asked_for():
    r, asked = renderer()
    held = object()
    r.m_textures[r._texture_key("maps/in.dds", True)] = held
    assert r._texture("maps/in.dds", True, lazy=True) is held
    assert r._texture("maps/out.dds", True, lazy=True) is None and asked == []
    assert r._texture("maps/out.dds", True) is None and len(asked) == 1  # a plain ask still goes to the loader


def test_a_device_that_refuses_a_block_map_gets_decoded_maps_from_then_on(monkeypatch):
    """Item 6: a QRhi device asks for the stored blocks; one that cannot take them makes the renderer decode again."""
    r, _asked = renderer()
    jobs, forgotten = [], []
    r.m_ctx = N(m_rhi=object(), m_decode_srgb=False)
    r.m_loader = N(m_futures={}, pending=lambda: 0, forget=forgotten.append, m_changed=Event())
    r._ask = lambda key, work: jobs.append(key) or SBlockMap("BC7", 16, 16, [bytes(256)])
    monkeypatch.setattr(modelrender, "block_texture", lambda *_args: None)
    assert r._texture("maps/a.dds", True) is None
    assert jobs == ["blk:0:maps/a.dds"] and forgotten == jobs and r.m_loader.m_changed.is_set()
    r._ask = lambda key, work: jobs.append(key)
    r._texture("maps/a.dds", True)
    assert jobs[-1] == "tex:0:maps/a.dds"
    r._m_blocks = True
    r.m_ctx = N(m_rhi=object(), m_decode_srgb=True)  # QRhi's OpenGL: sRGB maps are decoded from the start
    r._texture("maps/b.dds", True)
    assert jobs[-1] == "tex:0:maps/b.dds"


def test_the_shadow_map_is_drawn_again_only_for_the_maps_of_its_groups():
    """The key held the count of every texture loaded, so a map no shadow caster uses redrew it."""
    r, _asked = renderer()

    def group(path: str):
        material = N(shader=0, diffuse=path)
        return N(node=path, cloth=None, material=lambda key: material), {"key": 0, "pose": None}, np.identity(4)

    drawn = [group("maps/a.dds")]
    before = r._shadow_key(drawn, np.identity(4))
    r.m_textures[r._texture_key("maps/elsewhere.dds", True)] = object()
    assert r._shadow_key(drawn, np.identity(4)) == before
    r.m_textures[r._texture_key("maps/a.dds", True)] = object()
    assert r._shadow_key(drawn, np.identity(4)) != before


def test_the_game_jobs_run_on_workers_of_their_own_and_count_as_busy():
    from threading import Event

    r = CModelRenderer.__new__(CModelRenderer)
    r.m_loader, r.m_game_loader = CLoader(2), CLoader(2)
    r.m_state, r.m_game_pass = SViewState(look=SHADERS), N(busy=lambda: 0)
    release = Event()
    try:
        r.m_loader.get("tex:big", release.wait)  # a decode holds a worker of the files' loader
        assert r.busy() == 1
        r.m_game_loader.get("gs:0:0:0", lambda: "compiled")
        r.m_game_loader.m_futures["gs:0:0:0"].result(timeout=10)  # not queued behind the decode
        r.m_game_loader.get("gs:slow", release.wait)
        assert r.busy() == 2
        assert r.changed()  # the game's job finished: the window draws again
        release.set()
        for loader in (r.m_loader, r.m_game_loader):
            for future in list(loader.m_futures.values()):
                future.result(timeout=10)
        assert r.busy() == 0
    finally:
        release.set()
        r.m_loader.close()
        r.m_game_loader.close()


def test_a_groups_compile_is_asked_of_the_game_loader():
    from annotoolbox.viewer.ownpass import COwnPass

    asked = []
    loader = N(get=lambda job, work: asked.append(job))
    p = COwnPass.__new__(COwnPass)
    p.m_renderer, p.m_models = N(m_game_loader=loader, m_loader=None), {}
    assert p._job((0, 0, 1), "gs:0:0:1", lambda: None, "wall") is None and asked == ["gs:0:0:1"]


def test_a_frame_of_the_plain_look_asks_for_maps_after_a_stand_in_frame():
    """The stand-in flag was set by a frame of the game's look and no frame of the plain look cleared it, so the plain
    look asked for no map any more."""
    r, asked = renderer(stand_in=True)
    r.m_game_pass = N(why_not=lambda: "")
    assert r._begin_maps(SViewState(look=SHADERS)) is True and r._m_stand_in is False
    r._m_stand_in = True  # the game's look drew its stand-in
    assert r._begin_maps(SViewState(look="own")) is False
    lit_draw(r)
    assert len(asked) == 2
