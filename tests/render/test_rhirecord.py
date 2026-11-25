"""The QRhi recorder's failure handling and caches with a stub device (no GPU): a pass that raises must not stay
recorded for the frames after it, and nothing it opened may leak."""

from __future__ import annotations

from annotoolbox.render import rhipass


class FakeUpdateBatch:
    def __init__(self, pool: list) -> None:
        self.pool = pool
        pool.append(self)

    def release(self) -> None:
        self.pool.remove(self)


class FakeRhi:
    def __init__(self) -> None:
        self.open: list = []

    def isYUpInFramebuffer(self) -> bool:
        return True

    def isYUpInNDC(self) -> bool:
        return True

    def isClipDepthZeroToOne(self) -> bool:
        return False

    def nextResourceUpdateBatch(self) -> FakeUpdateBatch:
        return FakeUpdateBatch(self.open)


class FakeCb:
    def __init__(self) -> None:
        self.passes = 0

    def endPass(self) -> None:
        self.passes -= 1


class FakeNative:
    """A device resource of a counting fake: destroyed once at most."""

    def __init__(self) -> None:
        self.destroyed = 0

    def destroy(self) -> None:
        self.destroyed += 1


def boom(command_buffer, batch) -> None:
    raise ValueError("cannot reshape")


def test_a_pass_that_raises_leaves_nothing_recorded_and_no_batch_open():
    rhi = FakeRhi()
    rec = rhipass.CRecorder(rhi)
    for _ in range(3):
        rec.native_pass(boom)
        try:
            rec._run(FakeCb())
        except ValueError:
            pass
        assert rec._m_passes == [] and rec._m_pending == [] and rhi.open == []
    assert len(rec.m_errors) == 3 and "cannot reshape" in rec.m_errors[0]


def test_the_released_resources_rotate_even_when_a_frame_fails():
    rhi = FakeRhi()
    rec = rhipass.CRecorder(rhi)
    old = FakeNative()
    rec.later(old)
    rec.native_pass(boom)
    try:
        rec._run(FakeCb())
    except ValueError:
        pass
    assert rec._m_dying == [old] and not old.destroyed
    rec._run(FakeCb())
    assert old.destroyed


def test_a_view_whose_frame_raises_sets_failed_and_still_ends_the_frame():
    from annotoolbox.render import rhiview

    class Ctx:
        ended = 0

        def begin(self, command_buffer, tex):
            return None

        def end(self):
            self.ended += 1
            raise ValueError("late")

    class FakeSize:
        def width(self):
            return 4

        height = width

    class FakeTex:
        def pixelSize(self):
            return FakeSize()

    view = rhiview.CRhiView.__new__(rhiview.CRhiView)
    view.m_ctx, view.m_failed = Ctx(), ""
    view.colorTexture = lambda: FakeTex()
    view.draw = lambda *a: (_ for _ in ()).throw(KeyError("draw"))
    view.render(None)
    assert view.m_ctx.ended == 1 and view.m_failed.startswith("KeyError")


class FakeReleasable:
    """A texture, framebuffer or program of a counting fake device."""

    def __init__(self, bank: list) -> None:
        self.released = False
        bank.append(self)

    def release(self) -> None:
        self.released = True


class FakeCtx:
    def __init__(self) -> None:
        self.made: list[FakeReleasable] = []

    def native_texture(self, size, fmt, **kw):
        return FakeReleasable(self.made)

    def depth_renderbuffer(self, size):
        return FakeReleasable(self.made)

    def framebuffer(self, colors=(), depth=None):
        return FakeReleasable(self.made)

    def program(self, **kw):
        return FakeReleasable(self.made)

    def vertex_array(self, *a):
        return object()


def test_a_deferred_frame_releases_every_target_it_made_when_the_size_changes():
    from annotoolbox.render import frame2070

    ctx = FakeCtx()
    frame = frame2070.CFrame2070.__new__(frame2070.CFrame2070)
    frame.m_ctx, frame.m_size_px, frame.m_targets = ctx, None, []
    frame.m_lit = frame.m_depth = frame.m_fbo = frame.m_lit_fbo = frame.m_lit_depth_fbo = None
    frame.m_read_depth = frame.m_read_fbo = frame.m_shadow_map = frame.m_refraction = frame.m_refraction_fbo = None
    frame.m_blend = frame.m_blend_fbo = frame.m_blend_depth_fbo = frame.m_blend_copy = frame.m_blend_clear = None
    frame.m_composite, frame.m_depth_copy, frame.m_lit_copy = ctx.program(), ctx.program(), ctx.program()
    frame.m_parts = frame2070.SParts2070()
    frame._targets((64, 64))
    first = list(ctx.made)
    frame._targets((65, 64))
    assert all(o.released for o in first if o not in (frame.m_composite, frame.m_depth_copy, frame.m_lit_copy))
    frame.release_shading = None
    frame.m_shading = FakeReleasable(ctx.made)
    frame.release()
    assert all(o.released for o in ctx.made)


def test_the_game_and_forward_frames_release_their_depth_and_framebuffers_too():
    from annotoolbox.render import frameforward, gameframe, gamestill

    ctx = FakeCtx()
    forward = frameforward.CForwardFrame.__new__(frameforward.CForwardFrame)
    forward.m_ctx, forward.m_size_px, forward.m_targets, forward.m_fbo = ctx, None, [], None
    forward.m_composite, forward.m_shadow = ctx.program(), None
    forward.m_depth_copy, forward.m_read_depth, forward.m_read_fbo, forward.m_copies = None, None, None, None
    forward.m_prez, forward.m_prez_size = None, None
    forward.m_shared = forward.m_shared_prepass = frameforward.CSharedBuffers(ctx)
    forward._targets((8, 8))
    first = list(ctx.made)
    forward._targets((9, 8))
    assert all(o.released for o in first if o is not forward.m_composite)
    forward.release()
    assert all(o.released for o in ctx.made)

    ctx = FakeCtx()
    game = gameframe.CGameFrame.__new__(gameframe.CGameFrame)
    game.m_ctx, game.m_size_px, game.m_targets, game.m_lit, game.m_depth, game.m_fbo, game.m_shadow = (
        ctx,
        None,
        [],
        [],
        None,
        None,
        None,
    )
    game.m_lit_fbo, game.m_info, game.m_has_stencil = None, None, False
    game.m_composite, game.m_still = ctx.program(), gamestill.CStill()
    game._gbuffer((8, 8))
    first = list(ctx.made)
    game._gbuffer((9, 8))
    assert all(o.released for o in first if o is not game.m_composite)
    game.release()
    assert all(o.released for o in ctx.made)


def test_a_released_program_takes_its_pipelines_and_sets_with_it_and_its_serial_is_new_each_time():
    from annotoolbox.render import rhidevice

    rec = rhipass.CRecorder(FakeRhi())
    a, b = rhidevice.CProgram.__new__(rhidevice.CProgram), rhidevice.CProgram.__new__(rhidevice.CProgram)
    for prog in (a, b):
        prog.ctx, prog.m_uniform_pool = rec, []
        prog.m_serial = rhipass.CRecorder.next_serial()
    buf = FakeReleasable([])
    buf.destroy = lambda: None
    a.m_uniform_pool = [buf]
    rec._m_srbs = {(a.m_serial, 0, ()): "srb-a", (b.m_serial, 0, ()): "srb-b"}
    rec._m_pipelines = {(a.m_serial, "v"): "pipe-a", (b.m_serial, "v"): "pipe-b"}
    a.release()
    assert list(rec._m_srbs) == [(b.m_serial, 0, ())] and list(rec._m_pipelines) == [(b.m_serial, "v")]
    assert sorted(x for x in rec._m_doomed if isinstance(x, str)) == ["pipe-a", "srb-a"] and buf in rec._m_doomed
    assert a.m_serial != b.m_serial


def test_the_stand_in_for_a_declared_texture_has_its_dimension_and_element_type():
    from PySide6.QtGui import QRhiTexture

    from annotoolbox.render.fxcdraw import dummy_format

    f = QRhiTexture.Format
    assert dummy_format("Texture3D<uint2>") == ("3d", f.RG32UI)
    assert dummy_format("Texture2D<uint>") == ("2d", f.R32UI)
    assert dummy_format("TextureCube<float4>") == ("cube", f.RGBA8)
    assert dummy_format("Texture2DArray") == ("array", f.RGBA8)
    assert dummy_format("Texture2D") == ("2d", f.RGBA8) and dummy_format("Texture3D") == ("3d", f.RGBA8)
    assert dummy_format("Texture2D<int4>") == ("2d", f.RGBA32SI)


def test_a_released_fxc_draw_hands_its_samplers_on_and_makes_them_anew():
    """A released draw prepared again must not bind the samplers it handed over for destruction."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from annotoolbox.render import fxcdraw

    later, rhi = [], MagicMock()
    rhi.newSampler.side_effect = lambda *args: MagicMock()
    stage = SimpleNamespace(samplers={}, types={})
    program = SimpleNamespace(m_vertex_stage=stage, m_pixel_stage=stage, m_numbers={}, m_stages={}, m_cbuffers={})
    draw = fxcdraw.CFxcDraw(SimpleNamespace(m_rhi=rhi, later=later.append), program, None, None, None)
    first = draw._sampler("cLinearWrapSampler")
    draw.release()
    assert first in later and draw._sampler("cLinearWrapSampler") is not first
    assert draw.m_constants == {} and draw.m_buffers == {}


def test_a_rigid_forward_model_uploads_its_one_matrix_once():
    import numpy as np

    from annotoolbox.render import frameforward

    class FakeRow:
        def __init__(self) -> None:
            self.writes: list = []

        def write(self, data, level=0, viewport=None) -> None:
            self.writes.append((len(data), viewport))

    model = frameforward.CForwardModel.__new__(frameforward.CForwardModel)
    model.m_rows, model._m_written = [FakeRow(), FakeRow(), FakeRow()], [None] * 3
    model.m_world, model.m_palette, model.m_bones = np.identity(4), None, 0
    model.update_matrices()
    model.update_matrices()  # nothing changed: nothing uploaded
    assert [r.writes for r in model.m_rows] == [[(16, (0, 0, 1, 1))]] * 3
    model.m_world = np.diag([2.0, 1.0, 1.0, 1.0])
    model.update_matrices()
    assert [len(r.writes) for r in model.m_rows] == [2, 1, 1]  # only the x row changed


def test_a_placement_with_a_non_unit_quaternion_is_a_pure_rotation():
    import numpy as np

    from annotoolbox.render import modellibrary

    m = modellibrary.trs((1, 2, 3), (0.0, 2.0, 0.0, 2.0), 1.0)  # 90 degrees about y, not normalised
    assert np.allclose(m[:3, :3] @ m[:3, :3].T, np.identity(3), atol=1e-5)
    assert np.allclose(m[:3, 3], (1, 2, 3))


def test_a_sampler_state_makes_one_qrhi_sampler_and_a_mesh_knows_its_box_and_bones():
    import numpy as np

    from annotoolbox.models import fxstage
    from annotoolbox.render import gamedraw

    made = []

    class FakeSampler:
        def __init__(self, args) -> None:
            self.args, self.w, self.cmp = args, None, None

        def setAddressW(self, w) -> None:
            self.w = w

        def setTextureCompareOp(self, op) -> None:
            self.cmp = op

        def create(self) -> None:
            made.append(self)

    class FakeRhi:
        def newSampler(self, *args):
            return FakeSampler(args)

    point = gamedraw.sampler_of(FakeRhi(), fxstage.sampler_named("sPointClampCmp"))
    linear = gamedraw.sampler_of(FakeRhi(), fxstage.SSampler("MIN_MAG_MIP_LINEAR"))
    assert point.args[0] == point.args[1] != linear.args[0] and point.args[2] == linear.args[2]  # a linear mip filter
    assert point.args[3] == point.w != linear.args[3] and point.cmp is not None and linear.cmp is None

    v = np.array([[0, 0, 0, 3], [1, 2, 3, 5]], "<f4")  # position, then one bone byte group packed in a float
    data = np.zeros((2, 16), np.uint8)
    data[:, :12] = v[:, :3].astype("<f4").view(np.uint8).reshape(2, 12)
    data[:, 12] = [0, 4]
    mesh = gamedraw.CMesh(data.tobytes(), 16, ((0, 0, 3, 0), (7, 5, 4, 12)), np.array([0, 1, 1], np.uint32), 2)
    lo, hi = mesh.box()
    assert lo.tolist() == [0, 0, 0] and hi.tolist() == [1, 2, 3]
    assert mesh.bone_count(2) == 5 and mesh.bone_count(0) == 0


def test_a_texture_lets_its_base_pixels_go_once_its_levels_are_built():
    """A map written whole keeps its pixels only until its levels are built (each of a view's maps kept a CPU copy
    for good before: gigabytes on a continental island)."""
    import numpy as np
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import rhidevice

    class FakeNative:
        def create(self) -> bool:
            return True

    uploads = []
    rhi = SimpleNamespace(newTexture=lambda *a: FakeNative())
    ctx = SimpleNamespace(m_rhi=rhi, _upload=lambda tex, fn: uploads.append(fn), later=lambda x: None,
                          forget=lambda serial: None, m_cpu_srgb_mips=False)  # fmt: skip
    tex = rhidevice.CTexture(ctx, (4, 4), 4)
    tex.create()
    tex.write(np.zeros((4, 4, 4), np.uint8).tobytes())
    assert 0 in tex._m_levels
    tex.build_mipmaps()
    assert tex._m_levels == {} and tex.m_mipmapped and len(uploads) == 3  # the base again, then the levels


def test_a_released_device_destroys_what_it_holds_now_and_what_is_let_go_after():
    """Its view's QRhi goes away: no later frame would destroy what waits, and what a renderer lets go afterwards."""
    rec = rhipass.CRecorder(FakeRhi())
    waiting, dying, srb, pipe, sampler, late = (FakeNative() for _ in range(6))
    rec.later(waiting)
    rec._m_dying = [dying]
    rec._m_srbs, rec._m_pipelines, rec._m_samplers = {(1, 0, ()): srb}, {(1, "v"): pipe, (2, "v"): None}, {"s": sampler}
    rec.native_pass(boom)  # recorded for a frame that never comes
    rec.release()
    assert [n.destroyed for n in (waiting, dying, srb, pipe, sampler)] == [1] * 5
    assert not (rec._m_srbs or rec._m_pipelines or rec._m_samplers or rec._m_doomed or rec._m_dying or rec._m_passes)
    rec.later(late)
    rec.later(None)
    assert late.destroyed == 1 and rec._m_doomed == []


def test_a_released_texture_takes_the_sets_that_bind_it_and_no_other():
    rec = rhipass.CRecorder(FakeRhi())
    both, one, none = (1, 0, ((7, "s"), (8, "s"))), (1, 1, ((8, "s"),)), (2, 0, ())
    rec._m_srbs = {both: FakeNative(), one: FakeNative(), none: FakeNative()}
    rec._m_srb_users = {7: {both}, 8: {both, one}}
    rec.forget(7)
    assert list(rec._m_srbs) == [one, none] and rec._m_srb_users == {8: {one}} and len(rec._m_doomed) == 1
    rec.forget(7)  # released twice, or never bound: nothing more
    rec.forget_program(1)
    assert list(rec._m_srbs) == [none] and rec._m_srb_users == {} and len(rec._m_doomed) == 2


def test_a_view_whose_device_goes_away_tells_its_owner_and_releases_the_device():
    from annotoolbox.render import rhiview

    class Ctx:
        released = 0

        def release(self) -> None:
            self.released += 1

    seen = []
    view = rhiview.CRhiView.__new__(rhiview.CRhiView)
    ctx = view.m_ctx = Ctx()
    view._m_device, view.m_failed = 1, ""
    view.released = seen.append
    view.releaseResources()
    assert seen == [ctx] and ctx.released == 1 and view.m_ctx is None and view._m_device is None
    view.releaseResources()  # no device: nothing to release
    assert seen == [ctx]
    view.m_ctx = ctx
    view.released = lambda ctx: (_ for _ in ()).throw(KeyError("renderer"))
    view.releaseResources()  # the owner's release failed: the device's own still goes
    assert ctx.released == 2 and view.m_failed.startswith("KeyError")


def test_a_baker_that_does_not_start_or_never_ends_is_a_bake_error(monkeypatch):
    import subprocess
    from pathlib import Path

    import pytest

    from annotoolbox.render import shaderbake

    for error in (FileNotFoundError("qsb"), subprocess.TimeoutExpired("qsb", 120)):
        monkeypatch.setattr(subprocess, "run", lambda *a, error=error, **kw: (_ for _ in ()).throw(error))
        with pytest.raises(shaderbake.CBakeError, match="did not run"):
            shaderbake._run(Path("qsb"), ["-d", "x"])


def test_the_terrain_bake_destroys_the_pipelines_of_the_models_it_let_go():
    """A baked node's model is let go a frame later by its resources alone: its program's pipeline keeps its count."""
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import gamedraw, terrainbake

    program = gamedraw.CProgram.__new__(gamedraw.CProgram)
    kept, given_back = FakeNative(), FakeNative()
    program.m_pipelines = {"bake": [kept, 37], "last": [given_back, 1]}
    last = SimpleNamespace(release=lambda: gamedraw.CGameModel._give_back(SimpleNamespace(m_program=program), "last"))
    bake = terrainbake.CBake.__new__(terrainbake.CBake)
    bake.m_slots, bake.m_keep, bake.m_arrays, bake._m_done, bake.m_program = [], [], {}, [last], program
    bake.m_common = SimpleNamespace(release=lambda: None)
    bake.release()
    assert kept.destroyed == 1 and given_back.destroyed == 1 and program.m_pipelines == {}


def test_the_models_view_releases_its_renderer_with_the_device_and_shows_its_scene_again():
    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.viewer import modelview

    class FakeRenderer:
        released = 0

        def release(self) -> None:
            self.released += 1

    class Ctx:
        def release(self) -> None:
            assert renderer.released == 1  # the renderer first: its resources are this device's

    view = modelview.CRhiModelView.__new__(modelview.CRhiModelView)
    renderer = view.m_renderer = FakeRenderer()
    view.m_ctx, view._m_device, view.m_failed, view.m_scene, view._m_pending = Ctx(), 1, "", "scene", None
    view.releaseResources()
    assert renderer.released == 1 and view.m_renderer is None and view._m_pending == "scene" and not view.m_failed
    view.m_ctx = Ctx()
    view.releaseResources()  # no renderer yet (the window closed first): nothing more
    assert renderer.released == 1 and not view.m_failed


def test_the_layout_caches_of_the_game_shaders_keep_a_bounded_number(monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from annotoolbox.render import gameshader as gs

    monkeypatch.setattr(gs, "KEPT_LAYOUTS", 3)
    monkeypatch.setattr(gs, "_PATHS", {})
    monkeypatch.setattr(gs, "_replayed", {})
    var = SimpleNamespace(name="cSun", offset=0, size=16, type="float4", elements=0, members=[], rows=1, columns=4)
    layouts = [SimpleNamespace(size=16, variables=[var]) for _ in range(8)]
    for layout in layouts:
        c = gs.CConstants(layout)
        gs.replay(c, "sun", (1,), lambda c: c.set("cSun", (1.0, 2.0, 3.0, 4.0)))
        assert np.frombuffer(c.m_data, np.float32).tolist() == [1.0, 2.0, 3.0, 4.0]
    assert len(gs._PATHS) == 3 and len(gs._replayed) == 3
    assert [kept[0] for kept in gs._PATHS.values()] == layouts[-3:]  # the oldest went
    first = gs.CConstants(layouts[0])  # let go, asked for again: worked out anew
    assert first.set("cSun", (5.0, 0.0, 0.0, 0.0)) and len(gs._PATHS) == 3


def test_the_frame_layout_cache_of_the_game_shaders_keeps_a_bounded_number(monkeypatch):
    from types import SimpleNamespace

    from annotoolbox.render import gameshader as gs

    monkeypatch.setattr(gs, "KEPT_LAYOUTS", 3)
    monkeypatch.setattr(gs, "_frame_layouts", {})
    var = SimpleNamespace(name="cExposure", offset=0, size=4, type="float", elements=0, members=[], rows=1, columns=1)
    layouts = [SimpleNamespace(size=16, variables=[var]) for _ in range(8)]
    for layout in layouts:
        assert gs._frame_layout(layout)[0] == ((0, 4),)
    assert [kept[0] for kept in gs._frame_layouts.values()] == layouts[-3:]  # the oldest went


def test_a_closed_model_library_takes_no_more_work_and_says_nothing():
    import pytest

    pytest.importorskip("PySide6")  # render's package imports Qt
    from annotoolbox.render.modellibrary import CModelLibrary

    library = CModelLibrary(editor=None, workers=1)
    library.close()
    library.request(["house.cfg"])  # each of these raised RuntimeError from the closed pool
    assert library.texture("d:wall.psd") is None and library.parts("house.cfg") is None
    assert library.set_full({"house.cfg"}) and not library._submit(print)
    library.m_pool = None  # closed between the check and the call
    library._m_closed = False

    class FakeShut:
        def submit(self, *a):
            raise RuntimeError("cannot schedule new futures after shutdown")

    library.m_pool = FakeShut()
    assert not library._submit(print)


def test_a_refused_srgb_level_pipeline_destroys_what_the_level_made(monkeypatch):
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import srgbmips

    binding = lambda *a: a  # noqa: E731
    stage = SimpleNamespace(ComputeStage=1)
    fake = SimpleNamespace(StageFlag=stage, uniformBuffer=binding, sampledTexture=binding, imageStore=binding)
    monkeypatch.setattr(srgbmips, "Binding", fake)

    class FakeStage(tuple):
        Type = SimpleNamespace(Compute=0)

        def __new__(cls, *a):
            return super().__new__(cls, a)

    monkeypatch.setattr(srgbmips, "QRhiShaderStage", FakeStage)

    class FakeMade(FakeNative):
        def create(self) -> bool:
            return not getattr(self, "refused", False)

        def __getattr__(self, name: str):
            if name.startswith("set"):
                return lambda *a: None
            raise AttributeError(name)

    class FakeDevice:
        def __init__(self) -> None:
            self.made: list = []

        def _new(self, *a) -> FakeMade:
            self.made.append(FakeMade())
            return self.made[-1]

        newTexture = newBuffer = newSampler = newShaderResourceBindings = _new

        def newComputePipeline(self) -> FakeMade:
            pipe = self._new()
            pipe.refused = True
            return pipe

    device = FakeDevice()
    mips = srgbmips.CSrgbMips.__new__(srgbmips.CSrgbMips)
    mips.m_rhi, mips.m_shader, mips.m_pipeline = device, None, None
    texture = type("T", (), {"native": object()})()
    with pytest.raises(RuntimeError, match="refused"):
        mips._level(None, texture, 0, (2, 2))
    assert len(device.made) == 5 and [m.destroyed for m in device.made] == [1] * 5 and mips.m_pipeline is None


def test_a_batch_a_native_pass_hands_on_itself_is_not_released_a_second_time():
    """``command_buffer.resourceUpdate(batch)`` in a native pass (the seas' and the pages' uploads): QRhi lets the batch go, and
    the recorder released it again at the frame's end."""

    class FakeCommands(FakeCb):
        def resourceUpdate(self, batch) -> None:
            batch.release()  # as QRhi: the batch goes back to its pool

    rhi = FakeRhi()
    rec = rhipass.CRecorder(rhi)
    rec.native_pass(lambda command_buffer, batch: command_buffer.resourceUpdate(batch))
    rec._run(FakeCommands())  # a second release of the batch raised (the fake's pool no longer holds it)
    assert rhi.open == [] and rec.m_errors == [] and rec._m_open_batches == []


def test_the_shading_destroys_the_sky_cube_a_new_light_replaces():
    """Every change of the look's light made a cube and kept the last one until the view closed."""
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import gamelight

    shading = gamelight.CDeferredShading.__new__(gamelight.CDeferredShading)
    shading.m_fixed, shading._m_sh, shading._m_replaced = {}, (), []
    shading.m_res = SimpleNamespace(m_keep=[], invalidate=lambda: None)

    def texture(batch, *a, **kw) -> FakeNative:
        shading.m_res.m_keep.append(FakeNative())
        return shading.m_res.m_keep[-1]

    shading._texture, shading._raw = texture, lambda name: name
    batch = SimpleNamespace(uploadStaticBuffer=lambda *a: None)
    cubes = []
    for k in range(4):
        sh = tuple((float(k), 0.0, 0.0) for _ in range(9))
        shading._cube(batch, gamelight.SSky((0.0, 1.0, 0.0), (1.0, 1.0, 1.0), sh))
        shading._cube(batch, gamelight.SSky((0.0, 1.0, 0.0), (1.0, 1.0, 1.0), sh))  # the same light: the same cube
        cubes.append(shading.m_fixed["cube"])
    assert len({id(c) for c in cubes}) == 4
    # the one in use and the one before it (a binding set of this frame may hold it) stand, the others are gone
    assert [c.destroyed for c in cubes] == [1, 1, 0, 0] and shading.m_res.m_keep == [cubes[3]]
    shading.m_res.release = lambda: [r.destroy() for r in shading.m_res.m_keep]
    shading.m_constants = {}
    shading.m_pipeline = None
    shading.release()
    assert [c.destroyed for c in cubes] == [1, 1, 1, 1] and shading._m_replaced == []


def test_the_shading_sends_empty_light_cells_once_not_every_frame():
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import gamelight

    shading = gamelight.CDeferredShading.__new__(gamelight.CDeferredShading)
    shading._m_clusters, shading._m_emptied, shading._raw = None, False, lambda name: name
    sent = []
    batch = SimpleNamespace(uploadStaticBuffer=lambda buf, data: sent.append(buf))
    for _frame in range(3):
        shading._lights(batch, None)
    assert sent == ["cLightClusterBuffer"]  # the first frame only: the buffer then holds no light

    """``make`` raising went on to the view in every frame after (the compiled result stays in the loader)."""
    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import gameextras

    said, made = [], []

    def make(got):
        made.append(got)
        raise RuntimeError("no such buffer")

    extras = gameextras.CFrameExtras(None, lambda: "source", lambda job, work: work(), said.append)
    for _frame in range(3):
        assert extras._part("sea", lambda source: "compiled", make, gameextras.N_SEA) is None
    assert made == ["compiled"] and len(said) == 1 and "no such buffer" in said[0]
    assert extras.m_made == {"sea": False}


def test_a_steady_eye_adaptation_starts_at_its_target_and_eases_by_fixed_steps():
    """A bench grabs two ways after the same number of frames: the same exposure history however long they took."""
    import struct
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import gameextras

    runs = []
    eye = SimpleNamespace(m_error="", m_read_back=False, m_readback=None, m_exposure="buffer")
    eye.restart = lambda: runs.append("restart")
    eye.run = lambda ctx, image, size, step: runs.append(step)
    extras = gameextras.CFrameExtras(None, lambda: "source", lambda job, work: work(), print)
    extras.m_made["eye"] = eye
    extras._m_last_frame = 123.0
    extras.steady(0.25)
    for _frame in range(3):
        assert extras._exposure(None, None, (8, 8)) == "buffer"
    assert runs == ["restart", None, 0.25, 0.25]
    assert extras.exposure() is None
    extras.read_exposure()
    assert eye.m_read_back
    eye.m_readback = SimpleNamespace(data=struct.pack("<2f", 1.5, 0.0))
    assert extras.exposure() == 1.5


def test_a_released_particle_layer_makes_its_drawer_anew():
    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import particledraw

    class FakeDrawer:
        released = 0

        def release(self) -> None:
            self.released += 1

    layer = particledraw.CParticleLayer(None, None, list, None)
    drawer = layer.m_drawer = FakeDrawer()
    layer.release()
    layer.release()  # nothing left to release
    assert drawer.released == 1 and layer.m_drawer is None
