"""The QRhi recorder's failure handling and caches with a stub device (no GPU): a pass that raises must not stay
recorded for the frames after it, and nothing it opened may leak."""

from __future__ import annotations

from annotoolbox.render import rhipass


class Batch:
    def __init__(self, pool: list) -> None:
        self.pool = pool
        pool.append(self)

    def release(self) -> None:
        self.pool.remove(self)


class Rhi:
    def __init__(self) -> None:
        self.open: list = []

    def isYUpInFramebuffer(self) -> bool:
        return True

    def isYUpInNDC(self) -> bool:
        return True

    def isClipDepthZeroToOne(self) -> bool:
        return False

    def nextResourceUpdateBatch(self) -> Batch:
        return Batch(self.open)


class Cb:
    def __init__(self) -> None:
        self.passes = 0

    def endPass(self) -> None:
        self.passes -= 1


class Native:
    """A device resource of a counting fake: destroyed once at most."""

    def __init__(self) -> None:
        self.destroyed = 0

    def destroy(self) -> None:
        self.destroyed += 1


def boom(cb, batch) -> None:
    raise ValueError("cannot reshape")


def test_a_pass_that_raises_leaves_nothing_recorded_and_no_batch_open():
    rhi = Rhi()
    rec = rhipass.Recorder(rhi)
    for _ in range(3):
        rec.native_pass(boom)
        try:
            rec._run(Cb())
        except ValueError:
            pass
        assert rec._passes == [] and rec._pending == [] and rhi.open == []
    assert len(rec.errors) == 3 and "cannot reshape" in rec.errors[0]


def test_the_released_resources_rotate_even_when_a_frame_fails():
    rhi = Rhi()
    rec = rhipass.Recorder(rhi)
    old = Native()
    rec.later(old)
    rec.native_pass(boom)
    try:
        rec._run(Cb())
    except ValueError:
        pass
    assert rec._dying == [old] and not old.destroyed
    rec._run(Cb())
    assert old.destroyed


def test_a_view_whose_frame_raises_sets_failed_and_still_ends_the_frame():
    from annotoolbox.render import rhiview

    class Ctx:
        ended = 0

        def begin(self, cb, tex):
            return None

        def end(self):
            self.ended += 1
            raise ValueError("late")

    class Size:
        def width(self):
            return 4

        height = width

    class Tex:
        def pixelSize(self):
            return Size()

    view = rhiview.RhiView.__new__(rhiview.RhiView)
    view.ctx, view.failed = Ctx(), ""
    view.colorTexture = lambda: Tex()
    view.draw = lambda *a: (_ for _ in ()).throw(KeyError("draw"))
    view.render(None)
    assert view.ctx.ended == 1 and view.failed.startswith("KeyError")


class Released:
    """A texture, framebuffer or program of a counting fake device."""

    def __init__(self, bank: list) -> None:
        self.released = False
        bank.append(self)

    def release(self) -> None:
        self.released = True


class FakeCtx:
    def __init__(self) -> None:
        self.made: list[Released] = []

    def native_texture(self, size, fmt, **kw):
        return Released(self.made)

    def depth_renderbuffer(self, size):
        return Released(self.made)

    def framebuffer(self, colors=(), depth=None):
        return Released(self.made)

    def program(self, **kw):
        return Released(self.made)

    def vertex_array(self, *a):
        return object()


def test_a_deferred_frame_releases_every_target_it_made_when_the_size_changes():
    from annotoolbox.render import frame2070

    ctx = FakeCtx()
    frame = frame2070.Frame2070.__new__(frame2070.Frame2070)
    frame.ctx, frame.size, frame.targets = ctx, None, []
    frame.lit = frame.depth = frame.fbo = frame.lit_fbo = frame.lit_depth_fbo = None
    frame.read_depth = frame.read_fbo = frame.shadow_map = frame.refraction = frame.refraction_fbo = None
    frame.blend = frame.blend_fbo = frame.blend_depth_fbo = frame.blend_copy = frame.blend_clear = None
    frame.composite, frame.depth_copy, frame.lit_copy = ctx.program(), ctx.program(), ctx.program()
    frame.parts = frame2070.Parts2070()
    frame._targets((64, 64))
    first = list(ctx.made)
    frame._targets((65, 64))
    assert all(o.released for o in first if o not in (frame.composite, frame.depth_copy, frame.lit_copy))
    frame.release_shading = None
    frame.shading = Released(ctx.made)
    frame.release()
    assert all(o.released for o in ctx.made)


def test_the_game_and_forward_frames_release_their_depth_and_framebuffers_too():
    from annotoolbox.render import frameforward, gameframe

    ctx = FakeCtx()
    forward = frameforward.ForwardFrame.__new__(frameforward.ForwardFrame)
    forward.ctx, forward.size, forward.targets, forward.fbo = ctx, None, [], None
    forward.composite, forward.shadow = ctx.program(), None
    forward.depth_copy, forward.read_depth, forward.read_fbo, forward.copies = None, None, None, None
    forward.prez, forward.prez_size = None, None
    forward.shared = forward.shared_prepass = frameforward.SharedBuffers(ctx)
    forward._targets((8, 8))
    first = list(ctx.made)
    forward._targets((9, 8))
    assert all(o.released for o in first if o is not forward.composite)
    forward.release()
    assert all(o.released for o in ctx.made)

    ctx = FakeCtx()
    game = gameframe.GameFrame.__new__(gameframe.GameFrame)
    game.ctx, game.size, game.targets, game.lit, game.depth, game.fbo, game.shadow = ctx, None, [], [], None, None, None
    game.lit_fbo, game.info, game.stencil = None, None, False
    game.composite = ctx.program()
    game._gbuffer((8, 8))
    first = list(ctx.made)
    game._gbuffer((9, 8))
    assert all(o.released for o in first if o is not game.composite)
    game.release()
    assert all(o.released for o in ctx.made)


def test_a_released_program_takes_its_pipelines_and_sets_with_it_and_its_serial_is_new_each_time():
    from annotoolbox.render import rhidevice

    rec = rhipass.Recorder(Rhi())
    a, b = rhidevice.Program.__new__(rhidevice.Program), rhidevice.Program.__new__(rhidevice.Program)
    for prog in (a, b):
        prog.ctx, prog.uniform_pool = rec, []
        prog.serial = rhipass.Recorder.next_serial()
    buf = Released([])
    buf.destroy = lambda: None
    a.uniform_pool = [buf]
    rec._srbs = {(a.serial, 0, ()): "srb-a", (b.serial, 0, ()): "srb-b"}
    rec._pipelines = {(a.serial, "v"): "pipe-a", (b.serial, "v"): "pipe-b"}
    a.release()
    assert list(rec._srbs) == [(b.serial, 0, ())] and list(rec._pipelines) == [(b.serial, "v")]
    assert sorted(x for x in rec._doomed if isinstance(x, str)) == ["pipe-a", "srb-a"] and buf in rec._doomed
    assert a.serial != b.serial


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
    program = SimpleNamespace(vs=stage, ps=stage, numbers={}, stages={}, cbuffers={})
    draw = fxcdraw.FxcDraw(SimpleNamespace(rhi=rhi, later=later.append), program, None, None, None)
    first = draw._sampler("cLinearWrapSampler")
    draw.release()
    assert first in later and draw._sampler("cLinearWrapSampler") is not first
    assert draw.constants == {} and draw.buffers == {}


def test_a_rigid_forward_model_uploads_its_one_matrix_once():
    import numpy as np

    from annotoolbox.render import frameforward

    class Row:
        def __init__(self) -> None:
            self.writes: list = []

        def write(self, data, level=0, viewport=None) -> None:
            self.writes.append((len(data), viewport))

    model = frameforward.ForwardModel.__new__(frameforward.ForwardModel)
    model.rows, model._written = [Row(), Row(), Row()], [None] * 3
    model.world, model.palette, model.bones = np.identity(4), None, 0
    model.update_matrices()
    model.update_matrices()  # nothing changed: nothing uploaded
    assert [r.writes for r in model.rows] == [[(16, (0, 0, 1, 1))]] * 3
    model.world = np.diag([2.0, 1.0, 1.0, 1.0])
    model.update_matrices()
    assert [len(r.writes) for r in model.rows] == [2, 1, 1]  # only the x row changed


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

    class Sampler:
        def __init__(self, args) -> None:
            self.args, self.w, self.cmp = args, None, None

        def setAddressW(self, w) -> None:
            self.w = w

        def setTextureCompareOp(self, op) -> None:
            self.cmp = op

        def create(self) -> None:
            made.append(self)

    class Rhi:
        def newSampler(self, *args):
            return Sampler(args)

    point = gamedraw.sampler_of(Rhi(), fxstage.sampler_named("sPointClampCmp"))
    linear = gamedraw.sampler_of(Rhi(), fxstage.Sampler("MIN_MAG_MIP_LINEAR"))
    assert point.args[0] == point.args[1] != linear.args[0] and point.args[2] == linear.args[2]  # a linear mip filter
    assert point.args[3] == point.w != linear.args[3] and point.cmp is not None and linear.cmp is None

    v = np.array([[0, 0, 0, 3], [1, 2, 3, 5]], "<f4")  # position, then one bone byte group packed in a float
    data = np.zeros((2, 16), np.uint8)
    data[:, :12] = v[:, :3].astype("<f4").view(np.uint8).reshape(2, 12)
    data[:, 12] = [0, 4]
    mesh = gamedraw.Mesh(data.tobytes(), 16, ((0, 0, 3, 0), (7, 5, 4, 12)), np.array([0, 1, 1], np.uint32), 2)
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

    class Native:
        def create(self) -> bool:
            return True

    uploads = []
    rhi = SimpleNamespace(newTexture=lambda *a: Native())
    ctx = SimpleNamespace(rhi=rhi, _upload=lambda tex, fn: uploads.append(fn), later=lambda x: None,
                          forget=lambda serial: None, cpu_srgb_mips=False)  # fmt: skip
    tex = rhidevice.Texture(ctx, (4, 4), 4)
    tex.create()
    tex.write(np.zeros((4, 4, 4), np.uint8).tobytes())
    assert 0 in tex._levels
    tex.build_mipmaps()
    assert tex._levels == {} and tex.mipmapped and len(uploads) == 3  # the base again, then the levels


def test_a_released_device_destroys_what_it_holds_now_and_what_is_let_go_after():
    """Its view's QRhi goes away: no later frame would destroy what waits, and what a renderer lets go afterwards."""
    rec = rhipass.Recorder(Rhi())
    waiting, dying, srb, pipe, sampler, late = (Native() for _ in range(6))
    rec.later(waiting)
    rec._dying = [dying]
    rec._srbs, rec._pipelines, rec._samplers = {(1, 0, ()): srb}, {(1, "v"): pipe, (2, "v"): None}, {"s": sampler}
    rec.native_pass(boom)  # recorded for a frame that never comes
    rec.release()
    assert [n.destroyed for n in (waiting, dying, srb, pipe, sampler)] == [1] * 5
    assert not (rec._srbs or rec._pipelines or rec._samplers or rec._doomed or rec._dying or rec._passes)
    rec.later(late)
    rec.later(None)
    assert late.destroyed == 1 and rec._doomed == []


def test_a_released_texture_takes_the_sets_that_bind_it_and_no_other():
    rec = rhipass.Recorder(Rhi())
    both, one, none = (1, 0, ((7, "s"), (8, "s"))), (1, 1, ((8, "s"),)), (2, 0, ())
    rec._srbs = {both: Native(), one: Native(), none: Native()}
    rec._srb_users = {7: {both}, 8: {both, one}}
    rec.forget(7)
    assert list(rec._srbs) == [one, none] and rec._srb_users == {8: {one}} and len(rec._doomed) == 1
    rec.forget(7)  # released twice, or never bound: nothing more
    rec.forget_program(1)
    assert list(rec._srbs) == [none] and rec._srb_users == {} and len(rec._doomed) == 2


def test_a_view_whose_device_goes_away_tells_its_owner_and_releases_the_device():
    from annotoolbox.render import rhiview

    class Ctx:
        released = 0

        def release(self) -> None:
            self.released += 1

    seen = []
    view = rhiview.RhiView.__new__(rhiview.RhiView)
    ctx = view.ctx = Ctx()
    view._device, view.failed = 1, ""
    view.released = seen.append
    view.releaseResources()
    assert seen == [ctx] and ctx.released == 1 and view.ctx is None and view._device is None
    view.releaseResources()  # no device: nothing to release
    assert seen == [ctx]
    view.ctx = ctx
    view.released = lambda ctx: (_ for _ in ()).throw(KeyError("renderer"))
    view.releaseResources()  # the owner's release failed: the device's own still goes
    assert ctx.released == 2 and view.failed.startswith("KeyError")


def test_a_baker_that_does_not_start_or_never_ends_is_a_bake_error(monkeypatch):
    import subprocess
    from pathlib import Path

    import pytest

    from annotoolbox.render import shaderbake

    for error in (FileNotFoundError("qsb"), subprocess.TimeoutExpired("qsb", 120)):
        monkeypatch.setattr(subprocess, "run", lambda *a, error=error, **kw: (_ for _ in ()).throw(error))
        with pytest.raises(shaderbake.BakeError, match="did not run"):
            shaderbake._run(Path("qsb"), ["-d", "x"])


def test_the_terrain_bake_destroys_the_pipelines_of_the_models_it_let_go():
    """A baked node's model is let go a frame later by its resources alone: its program's pipeline keeps its count."""
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import gamedraw, terrainbake

    program = gamedraw.Program.__new__(gamedraw.Program)
    kept, given_back = Native(), Native()
    program.pipelines = {"bake": [kept, 37], "last": [given_back, 1]}
    last = SimpleNamespace(release=lambda: gamedraw.GameModel._give_back(SimpleNamespace(program=program), "last"))
    bake = terrainbake.Bake.__new__(terrainbake.Bake)
    bake.slots, bake.keep, bake.arrays, bake._done, bake.program = [], [], {}, [last], program
    bake.common = SimpleNamespace(release=lambda: None)
    bake.release()
    assert kept.destroyed == 1 and given_back.destroyed == 1 and program.pipelines == {}


def test_the_models_view_releases_its_renderer_with_the_device_and_shows_its_scene_again():
    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.viewer import modelview

    class Renderer:
        released = 0

        def release(self) -> None:
            self.released += 1

    class Ctx:
        def release(self) -> None:
            assert renderer.released == 1  # the renderer first: its resources are this device's

    view = modelview.RhiModelView.__new__(modelview.RhiModelView)
    renderer = view.renderer = Renderer()
    view.ctx, view._device, view.failed, view.scene, view._pending = Ctx(), 1, "", "scene", None
    view.releaseResources()
    assert renderer.released == 1 and view.renderer is None and view._pending == "scene" and not view.failed
    view.ctx = Ctx()
    view.releaseResources()  # no renderer yet (the window closed first): nothing more
    assert renderer.released == 1 and not view.failed


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
        c = gs.Constants(layout)
        gs.replay(c, "sun", (1,), lambda c: c.set("cSun", (1.0, 2.0, 3.0, 4.0)))
        assert np.frombuffer(c.data, np.float32).tolist() == [1.0, 2.0, 3.0, 4.0]
    assert len(gs._PATHS) == 3 and len(gs._replayed) == 3
    assert [kept[0] for kept in gs._PATHS.values()] == layouts[-3:]  # the oldest went
    first = gs.Constants(layouts[0])  # let go, asked for again: worked out anew
    assert first.set("cSun", (5.0, 0.0, 0.0, 0.0)) and len(gs._PATHS) == 3


def test_a_closed_model_library_takes_no_more_work_and_says_nothing():
    import pytest

    pytest.importorskip("PySide6")  # render's package imports Qt
    from annotoolbox.render.modellibrary import ModelLibrary

    library = ModelLibrary(editor=None, workers=1)
    library.close()
    library.request(["house.cfg"])  # each of these raised RuntimeError from the closed pool
    assert library.texture("d:wall.psd") is None and library.parts("house.cfg") is None
    assert library.set_full({"house.cfg"}) and not library._submit(print)
    library.pool = None  # closed between the check and the call
    library._closed = False

    class Shut:
        def submit(self, *a):
            raise RuntimeError("cannot schedule new futures after shutdown")

    library.pool = Shut()
    assert not library._submit(print)


def test_a_refused_srgb_level_pipeline_destroys_what_the_level_made(monkeypatch):
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import srgbmips

    binding = lambda *a: a  # noqa: E731
    stage = SimpleNamespace(ComputeStage=1)
    fake = SimpleNamespace(StageFlag=stage, uniformBuffer=binding, sampledTexture=binding, imageStore=binding)
    monkeypatch.setattr(srgbmips, "B", fake)

    class Stage(tuple):
        Type = SimpleNamespace(Compute=0)

        def __new__(cls, *a):
            return super().__new__(cls, a)

    monkeypatch.setattr(srgbmips, "QRhiShaderStage", Stage)

    class Made(Native):
        def create(self) -> bool:
            return not getattr(self, "refused", False)

        def __getattr__(self, name: str):
            if name.startswith("set"):
                return lambda *a: None
            raise AttributeError(name)

    class Device:
        def __init__(self) -> None:
            self.made: list = []

        def _new(self, *a) -> Made:
            self.made.append(Made())
            return self.made[-1]

        newTexture = newBuffer = newSampler = newShaderResourceBindings = _new

        def newComputePipeline(self) -> Made:
            pipe = self._new()
            pipe.refused = True
            return pipe

    device = Device()
    mips = srgbmips.SrgbMips.__new__(srgbmips.SrgbMips)
    mips.rhi, mips.shader, mips.pipeline = device, None, None
    texture = type("T", (), {"native": object()})()
    with pytest.raises(RuntimeError, match="refused"):
        mips._level(None, texture, 0, (2, 2))
    assert len(device.made) == 5 and [m.destroyed for m in device.made] == [1] * 5 and mips.pipeline is None


def test_a_batch_a_native_pass_hands_on_itself_is_not_released_a_second_time():
    """``cb.resourceUpdate(batch)`` in a native pass (the seas' and the pages' uploads): QRhi lets the batch go, and
    the recorder released it again at the frame's end."""

    class Commands(Cb):
        def resourceUpdate(self, batch) -> None:
            batch.release()  # as QRhi: the batch goes back to its pool

    rhi = Rhi()
    rec = rhipass.Recorder(rhi)
    rec.native_pass(lambda cb, batch: cb.resourceUpdate(batch))
    rec._run(Commands())  # a second release of the batch raised (the fake's pool no longer holds it)
    assert rhi.open == [] and rec.errors == [] and rec._open_batches == []


def test_the_shading_destroys_the_sky_cube_a_new_light_replaces():
    """Every change of the look's light made a cube and kept the last one until the view closed."""
    import pytest

    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from annotoolbox.render import gamelight

    shading = gamelight.DeferredShading.__new__(gamelight.DeferredShading)
    shading.fixed, shading._sh, shading._replaced = {}, (), []
    shading.res = SimpleNamespace(keep=[], invalidate=lambda: None)

    def texture(batch, *a, **kw) -> Native:
        shading.res.keep.append(Native())
        return shading.res.keep[-1]

    shading._texture, shading._raw = texture, lambda name: name
    batch = SimpleNamespace(uploadStaticBuffer=lambda *a: None)
    cubes = []
    for k in range(4):
        sh = tuple((float(k), 0.0, 0.0) for _ in range(9))
        shading._cube(batch, gamelight.Sky((0.0, 1.0, 0.0), (1.0, 1.0, 1.0), sh))
        shading._cube(batch, gamelight.Sky((0.0, 1.0, 0.0), (1.0, 1.0, 1.0), sh))  # the same light: the same cube
        cubes.append(shading.fixed["cube"])
    assert len({id(c) for c in cubes}) == 4
    # the one in use and the one before it (a binding set of this frame may hold it) stand, the others are gone
    assert [c.destroyed for c in cubes] == [1, 1, 0, 0] and shading.res.keep == [cubes[3]]
    shading.res.release = lambda: [r.destroy() for r in shading.res.keep]
    shading.constants = {}
    shading.pipeline = None
    shading.release()
    assert [c.destroyed for c in cubes] == [1, 1, 1, 1] and shading._replaced == []


def test_a_frame_part_the_device_refuses_is_said_once_and_asked_for_no_more():
    """``make`` raising went on to the view in every frame after (the compiled result stays in the loader)."""
    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import gameextras

    said, made = [], []

    def make(got):
        made.append(got)
        raise RuntimeError("no such buffer")

    extras = gameextras.FrameExtras(None, lambda: "source", lambda job, work: work(), said.append)
    for _frame in range(3):
        assert extras._part("sea", lambda source: "compiled", make, gameextras.N_SEA) is None
    assert made == ["compiled"] and len(said) == 1 and "no such buffer" in said[0]
    assert extras.made == {"sea": False}


def test_a_released_particle_layer_makes_its_drawer_anew():
    import pytest

    pytest.importorskip("PySide6")
    from annotoolbox.render import particledraw

    class Drawer:
        released = 0

        def release(self) -> None:
            self.released += 1

    layer = particledraw.ParticleLayer(None, None, list, None)
    drawer = layer.drawer = Drawer()
    layer.release()
    layer.release()  # nothing left to release
    assert drawer.released == 1 and layer.drawer is None
