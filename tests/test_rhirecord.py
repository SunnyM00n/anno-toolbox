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
    class Native:
        destroyed = False

        def destroy(self) -> None:
            self.destroyed = True

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
