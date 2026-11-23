"""Step 7 on the native backend (render/postnative): the eye adaptation's clock (settled on a first frame or after a
pause, a bench's fixed steps), the view target's formats; with the game and the module: every shader of the step bound into
a pipeline on Windows' software rasteriser, and an island's picture finished natively in the island view."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QRhiTexture
from tests.render.renderfakes import native_modules

from annotoolbox.render import eyeadapt, postnative


def _post() -> postnative.CNativePost:
    post = object.__new__(postnative.CNativePost)
    post._m_started, post._m_last_frame = False, None
    return post


def test_the_exposure_settles_on_a_first_frame_and_after_a_pause_and_a_bench_steps_it_alike():
    clock = iter([10.0, 10.05, 12.0])
    post = _post()
    post.m_clock = lambda: next(clock)
    assert post._step() is None  # the first frame: at the target at once
    post._m_started = True
    assert post._step() == pytest.approx(0.05)  # eased by the time since the last frame
    assert post._step() is None  # more than ``SETTLE`` after the last frame
    assert eyeadapt.SETTLE < 1.9
    post.steady(1 / 60)  # as CFrameExtras.steady: at the target, then fixed steps whatever the frames cost
    assert post._step() is None and post._step() == pytest.approx(1 / 60) and post._step() == pytest.approx(1 / 60)


class FakeTex:
    def __init__(self, fmt, flags=None) -> None:
        self._fmt, self._flags = fmt, flags if flags is not None else QRhiTexture.Flag(0)

    def format(self):
        return self._fmt

    def flags(self):
        return self._flags


class FakeAtt:
    def __init__(self, native) -> None:
        self.native = native


class FakeFbo:
    def __init__(self, colour, depth=None) -> None:
        self.color_attachments = [FakeAtt(colour)]
        self.depth_attachment = FakeAtt(depth) if depth is not None else None


def test_the_composite_draws_in_the_view_targets_own_formats():
    F = QRhiTexture.Format
    assert postnative.target_format(FakeFbo(FakeTex(F.RGBA8), FakeTex(F.D32F))) == ("RGBA8_UNORM", "D32")
    assert postnative.target_format(FakeFbo(FakeTex(F.RGBA8, QRhiTexture.Flag.sRGB))) == ("SRGBA8_UNORM", "")
    with pytest.raises(postnative.nativeforeign.CNativeError):
        postnative.target_format(FakeFbo(FakeTex(F.UnknownFormat)))  # a format the module has no name for


@pytest.mark.xdist_group("island_view")
def test_every_shader_of_the_step_binds_on_the_software_rasteriser():
    from tests.render.renderfakes import native_module

    natives = native_modules()
    native_module()
    from annotoolbox.render import nativegpu

    gpu = nativegpu.own_device(adapter=-2)
    post = postnative.CNativePost(gpu, natives)
    assert set(post.m_kernels) == set(postnative.KERNELS)
    for key in postnative.DRAWS:
        kernel = post._draw(key, "RGBA8_UNORM" if key == "composite" else postnative.HDR, "")
        resolved = kernel.resolve({"cExposureBuffer": post.m_exposure_buffer})
        assert len(kernel.sets(0, resolved, kernel.write(post.m_list, 0, None))) == len(kernel.m_layouts)
    assert post._draw("composite", "RGBA8_UNORM", "").m_push == bytes(16)  # cbComposite's flags: none
    post.m_list.execute()
    post.release()
    assert gpu.errors == 0, gpu.messages()


DRAW = """
import json, sys
sys.path.insert(0, "scripts")
import native_compare as nc
from annotoolbox.editor import editorapp
from annotoolbox.editor.editsession import CEditSession
app = editorapp.CApp()
app.open("anno117", nc.island_path(app.files(), "roman_island_small_01"))
session = CEditSession(json.loads(app.m_editor.island_json()), app.m_editor.edits())
session.finish_loading()
cams = nc.cameras(session)
cams.pop("whole")
pictures, numbers = nc.one_way(session, app.m_editor, cams, (320, 180), 1, 600.0, "post")
print(json.dumps({"nodes": numbers["nodes"], "said": numbers["said"], "failed": numbers["failed"]}))
"""


def test_an_islands_picture_is_finished_natively_in_the_island_view():
    native_modules()
    from tests.render.renderfakes import native_module, render_backends, run_child

    native_module()
    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(DRAW)
    assert not got["failed"] and not got["said"], got
    assert got["nodes"]["close"]["post_native"] == 1, got
