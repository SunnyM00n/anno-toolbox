"""The QRhi device: our GLSL ported for Qt's shader baker, every shader of ours baked, std140 packing, vertex formats,
and frames drawn on Direct3D 12, Direct3D 11, Vulkan and QRhi's OpenGL in a child process (a QRhi widget needs the
desktop platform; the other tests run Qt offscreen), the viewer's renderer compared with the OpenGL device's."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from annotoolbox.render import glslport, shaderbake

ROOT = Path(__file__).resolve().parents[1]

VS = """
#version 330
uniform mat4 viewProj; uniform vec3 tint;
in vec3 pos; in mat4 inst; in vec2 uv;
out vec3 vWorld; out vec2 vUv; flat out int vId;
void main() { vWorld = pos; vUv = uv; vId = gl_InstanceID + gl_VertexID; gl_Position = viewProj * inst * vec4(pos, 1.0); }
"""
FS = """
#version 330
uniform sampler2D tex; uniform float gain; uniform vec3 tint;  // shared with the vertex stage
uniform vec3 lights[4];
in vec2 vUv; out vec4 color;
void main() { color = vec4(texture(tex, vUv).rgb * tint * gain + lights[1], 1.0); }
"""


def test_a_pair_becomes_one_block_with_bindings_and_locations():
    p = glslport.port(VS, FS)
    assert [m.name for m in p.members] == ["viewProj", "tint", "gain", "lights", glslport.CLIP]
    assert p.samplers["tex"].slot == 1 and p.sampler_stages["tex"] == "f"
    assert {n: v.slot for n, v in p.attributes.items()} == {"pos": 0, "inst": 1, "uv": 5}  # a matrix takes four
    # what the fragment shader reads comes first, so its inputs are a prefix of the vertex outputs
    assert "layout(location = 0) out vec2 vUv;" in p.vertex and "layout(location = 0) in vec2 vUv;" in p.fragment
    assert "layout(location = 2) flat out int vId;" in p.vertex
    assert "gl_InstanceIndex + gl_VertexIndex" in p.vertex and "void rhi_main()" in p.vertex
    assert p.vertex.count("uniform Params") == p.fragment.count("uniform Params") == 1
    assert "//" not in p.fragment and "uniform float gain;" not in p.fragment
    with pytest.raises(ValueError, match="no vertex output"):
        glslport.port(VS, FS.replace("in vec2 vUv;", "in vec2 vUv; in vec3 vOther;"))


def test_std140_members_take_moderngl_values_and_bytes():
    block = bytearray(256)
    mat = shaderbake.Member("m", "mat3", 0, 48)
    shaderbake.pack_member(block, mat, range(9))
    assert np.frombuffer(bytes(block[:48]), "f4").reshape(3, 4)[:, :3].ravel().tolist() == list(range(9))
    arr = shaderbake.Member("a", "vec3", 64, 64, 4)
    shaderbake.write_member(block, arr, np.arange(6, dtype="f4").tobytes())  # two of four, tightly packed
    assert np.frombuffer(bytes(block[64:96]), "f4").tolist() == [0, 1, 2, 0, 3, 4, 5, 0]
    shaderbake.pack_member(block, shaderbake.Member("b", "bool", 128, 4), True)
    shaderbake.pack_member(block, shaderbake.Member("u", "uint", 132, 4), 0xFFFFFFFF)
    assert np.frombuffer(bytes(block[128:136]), "u4").tolist() == [1, 0xFFFFFFFF]
    # the tightly laid members in one copy (a mat4, an array of vec4, a vec2): the bytes as the loop wrote them
    tight = bytearray(256)
    shaderbake.pack_member(tight, shaderbake.Member("w", "mat4", 0, 64), range(16))
    shaderbake.pack_member(tight, shaderbake.Member("v", "vec4", 64, 64, 4), range(8))  # two of four
    shaderbake.pack_member(tight, shaderbake.Member("p", "vec2", 192, 8), (7.5, -1.0))
    got = np.frombuffer(bytes(tight), "f4")
    assert got[:16].tolist() == list(range(16)) and got[16:24].tolist() == list(range(8)) and not got[24:32].any()
    assert got[48:50].tolist() == [7.5, -1.0]


def _programs() -> list[tuple[str, str]]:
    from annotoolbox.render import particledraw as pd
    from annotoolbox.render import shaders as s
    from annotoolbox.viewer import plainshading as g

    return [
        (s.TERRAIN_VS, s.TERRAIN_FS), (s.MODEL_VS, s.MODEL_FS), (s.BANNER_VS, s.MODEL_FS), (s.MODEL_VS, s.DEPTH_FS),
        (s.BANNER_VS, s.DEPTH_FS), (s.pick_vs(s.MODEL_VS), s.PICK_FS), (s.pick_vs(s.BANNER_VS), s.PICK_FS),
        (s.FLAT_VS, s.FLAT_FS), (s.SKY_VS, s.SKY_FS), (s.WATER_VS, s.WATER_PLAIN_FS),
        (s.FINAL_VS, s.FINAL_FS), (s.OVERLAY_VS, s.OVERLAY_FS), (pd.VERTEX, pd.FRAGMENT), (s.MODEL_VS, g.LIT_FS),
        (g.SKIN_VS, s.MODEL_FS), (g.SKIN_VS, g.LIT_FS), (g.SKIN_VS, s.DEPTH_FS),
    ]  # fmt: skip


def test_every_shader_of_ours_bakes_for_every_backend():
    pytest.importorskip("PySide6")
    if shaderbake.find_qsb() is None:
        pytest.skip("Qt's shader baker is not installed")
    for vertex, fragment in _programs():
        baked = shaderbake.bake(glslport.port(vertex, fragment))
        assert baked.vertex[:4] and baked.block_size >= 16 and glslport.CLIP in baked.members


def test_the_final_shader_encodes_a_negative_value_as_black_not_as_no_number():
    """A game's post chain can leave a negative value in the frame; ``pow`` of it is no number, drawn as a black
    pixel. The formula is checked here on the CPU, the shader only by its text: no device runs in this test."""
    from annotoolbox.render import shaders as s

    body = s.FINAL_FS.split("vec3 srgb(vec3 c) {", 1)[1].split("}", 1)[0]
    assert body.strip().startswith("c = max(c, 0.0);")

    def srgb(c: np.ndarray, clamp: bool) -> np.ndarray:
        c = np.maximum(c, 0.0) if clamp else c
        with np.errstate(invalid="ignore"):
            return np.where(c >= 0.0031308, 1.055 * np.power(c, 1.0 / 2.4) - 0.055, 12.92 * c)

    frame = np.array([-0.25, -1e-6, 0.0, 0.002, 0.5, 1.0], "f4")
    assert not np.isnan(srgb(frame, clamp=True)).any() and srgb(frame, clamp=True)[:3].tolist() == [0.0, 0.0, 0.0]
    assert np.allclose(srgb(frame, clamp=True)[3:], srgb(frame, clamp=False)[3:])  # nothing else moved
    with np.errstate(invalid="ignore"):
        assert np.isnan(np.power(frame[:1], 1.0 / 2.4)).all()  # what the shader's pow made of it before


def test_the_views_default_to_direct3d_12_where_the_machine_has_it(monkeypatch):
    """The owner's decision (2026-11-13): 117's islands open on Direct3D 12 in the game's look, so the views' device
    is Direct3D 12 where a device of it can be made; ``ANNO_RENDERER`` and the setting still choose."""
    pytest.importorskip("PySide6")
    from annotoolbox.render import rhiview

    monkeypatch.delenv(rhiview.ENV, raising=False)
    monkeypatch.setattr(rhiview, "_has_d3d12", lambda: True)
    assert rhiview.chosen("") == "d3d12" and rhiview.chosen("gl") == "gl" and rhiview.chosen("nonsense") == "d3d12"
    monkeypatch.setattr(rhiview.vulkanprobe, "probe", lambda: rhiview.vulkanprobe.Probe(True, 1))
    monkeypatch.setenv(rhiview.ENV, "vulkan")
    assert rhiview.chosen("gl") == "vulkan"
    monkeypatch.delenv(rhiview.ENV)
    monkeypatch.setattr(rhiview, "_has_d3d12", lambda: False)
    assert rhiview.chosen("") == rhiview.DEFAULT == "gl"


def test_the_process_use_of_the_adapters_memory_is_read_natively(monkeypatch):
    """The island view's memory split (island-117-view.md "Speed", part 9): this process's share of the adapter's own
    and mapped memory, read through DXGI; None where it cannot be asked (another OS here)."""
    from annotoolbox.infra import gpuinfo

    got = gpuinfo.video_memory()
    if sys.platform == "win32" and got is not None:
        assert got.local_budget > 0 and got.local >= 0 and got.shared >= 0
    monkeypatch.setattr(gpuinfo.os, "name", "posix")
    assert gpuinfo.video_memory() is None


def test_moderngl_vertex_formats_become_qrhi_attributes():
    pytest.importorskip("PySide6")
    from annotoolbox.render import rhipass

    attrs = glslport.port(VS, FS).attributes
    buffers, (bindings, items) = rhipass.vertex_layout(
        attrs, [("v", "3f 4x 2f", "pos", "uv"), ("i", "16f/i", "inst")], False
    )
    assert bindings == ((24, False), (64, True)) and [b[2] for b in buffers] == [False, True]
    assert [(b, loc, off, m) for b, loc, _f, off, m in items] == [
        (0, 0, 0, -1), (0, 5, 16, -1), (1, 1, 0, 0), (1, 2, 16, 1), (1, 3, 32, 2), (1, 4, 48, 3)
    ]  # fmt: skip
    with pytest.raises(KeyError):
        rhipass.vertex_layout(attrs, [("v", "3f", "nothing")], False)


# frames on the GPU, in a child process with Qt's desktop platform


def _child(code: str, timeout: int = 300) -> dict:
    paths = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=ROOT, timeout=timeout
    )
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith("{")]
    if done.returncode != 0 or not lines:
        raise AssertionError(f"the child failed ({done.returncode}):\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")
    return json.loads(lines[-1])


def _backends() -> list[str]:
    if sys.platform != "win32":
        return []
    pytest.importorskip("PySide6")
    if shaderbake.find_qsb() is None:
        pytest.skip("Qt's shader baker is not installed")
    found = _child(
        "import json; from PySide6.QtWidgets import QApplication; app = QApplication([]);"
        "from annotoolbox.render import rhiview;"
        "print(json.dumps({b: rhiview.available(b) for b in ('d3d12', 'd3d11', 'opengl')}))"
    )
    return [b for b, ok in found.items() if ok]


DEVICE_FRAME = """
import json, sys
import numpy as np
from PySide6.QtWidgets import QApplication
from annotoolbox.render import shaders as S
from annotoolbox.render.rhiview import RhiView
app = QApplication([])
class V(RhiView):
    def draw(self, ctx, screen, size):
        if not hasattr(self, "prog"):
            self.prog = ctx.program(vertex_shader=S.FLAT_VS, fragment_shader=S.FLAT_FS)
            rows = np.array([[-1, -1, 0, 1, 0, 0], [0, -1, 0, 1, 0, 0], [-1, 0, 0, 1, 0, 0]], "f4")
            self.vao = ctx.vertex_array(self.prog, [(ctx.buffer(rows.tobytes()), "3f 3f", "pos", "rgb")])
            self.final = ctx.program(vertex_shader=S.FINAL_VS, fragment_shader=S.FINAL_FS)
            self.full = ctx.vertex_array(self.final, [])
            self.msaa = ctx.framebuffer([ctx.renderbuffer(size, 4, samples=4, dtype="f2")], ctx.depth_renderbuffer(size, samples=4))
            self.resolved = ctx.texture(size, 4, dtype="f2")
            self.resolve = ctx.framebuffer([self.resolved])
        self.msaa.use()
        self.msaa.clear(0.0, 0.0, 1.0, 1.0)
        self.prog["viewProj"].write(np.identity(4, "f4").tobytes())
        self.prog["alpha"].value, self.prog["gain"].value = 1.0, 1.0
        self.vao.render()
        ctx.copy_framebuffer(self.resolve, self.msaa)
        screen.use()
        self.resolved.use(0)
        self.final["frame"].value, self.final["finish"].value, self.final["exposure"].value = 0, 3, 1.0
        self.full.render(vertices=3)
v = V(sys.argv[1] if len(sys.argv) > 1 else "%s")
for _ in range(3):
    img = v.picture(64, 64)
px = {k: img.pixelColor(x, y).getRgb()[:3] for k, (x, y) in {"low_left": (4, 59), "up_right": (59, 4)}.items()}
print(json.dumps({"failed": v.failed, "errors": v.ctx.errors, "px": px, "samples": v.ctx.max_samples}))
"""


def test_the_device_draws_through_a_resolved_multisampled_target_the_right_way_up():
    checked, without = [], []
    for backend in _backends():
        got = _child(DEVICE_FRAME % backend)
        if got["samples"] < 4:  # a software driver (a machine without a GPU) may have no multisampled targets
            without.append(f"{backend} ({got['samples']})")
            continue
        checked.append(backend)
        assert not got["failed"] and not got["errors"], (backend, got)
        r, g, b = got["px"]["low_left"]
        assert r > 200 and g < 40 and b < 40, (backend, got)  # the red triangle, bottom left as OpenGL draws it
        r, g, b = got["px"]["up_right"]
        assert b > 200 and r < 40, (backend, got)  # the clear colour
    if not checked:
        pytest.skip(f"no backend here makes 4-sample targets (most samples: {', '.join(without) or 'no backend'})")


def test_an_opengl_older_than_our_glsl_is_not_offered(monkeypatch):
    """A machine without a GPU (a CI runner) gets Qt's software OpenGL 3.0: QRhi makes the device, but every pipeline
    of our GLSL 330 is refused, so the backend is not available."""
    if sys.platform != "win32":
        pytest.skip("Qt's software OpenGL ships with Qt on Windows")
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_OPENGL", "software")
    got = _child(
        "import json; from PySide6.QtWidgets import QApplication; app = QApplication([]);"
        "from annotoolbox.render import rhiview; print(json.dumps({'ok': rhiview.available('opengl')}))"
    )
    assert got == {"ok": False}


VIEWER_FRAME = """
import json, tempfile
from pathlib import Path
import numpy as np
from PySide6.QtWidgets import QApplication
import tests.test_viewer as tv
from annotoolbox.viewer import modelscene
from annotoolbox.viewer.modelfiles import ModelFiles
from annotoolbox.viewer.modelrender import ModelCamera, ModelRenderer, ViewState
from annotoolbox.viewer.modelview import rhi_picture
from annotoolbox.viewer.ownpass import OwnPass
app = QApplication([])
OwnPass.why_not = lambda self: "a forced failure"  # the game's shaders cannot draw: the plain fallback, in their light
out = {}
with tempfile.TemporaryDirectory() as tmp:
    game = tv.game.__wrapped__(Path(tmp))
    scene = modelscene.build(game, tv.HOUSE, "anno117")
    for look in ("editor", "shaders"):
        state = ViewState(look=look, time=0.5, particles=False)
        img = rhi_picture(ModelFiles(game), scene, state, (96, 64), "%s")
        rhi = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(96)] for y in range(64)], float)
        out[look] = {"std": float(rhi.std())}
        try:
            import moderngl
            ctx = moderngl.create_standalone_context(require=330)
        except Exception:
            continue
        r, cam = ModelRenderer(ctx, ModelFiles(game)), ModelCamera((0, 0, 0), 5.0)
        r.show(scene)
        fbo = ctx.framebuffer([ctx.renderbuffer((96, 64), 4)], ctx.depth_renderbuffer((96, 64)))
        for _ in range(500):
            r.render(fbo, (96, 64), cam, state)
            if not r.busy() and r.box is not None and len(r.items) == len(scene.parts):
                break
        cam.frame(*r.box)
        r.render(fbo, (96, 64), cam, state)
        gl = np.frombuffer(fbo.read(components=3), np.uint8).reshape(64, 96, 3)[::-1].astype(float)
        out[look]["diff"] = float(np.abs(gl - rhi).mean())
print(json.dumps(out))
"""


def test_the_viewers_renderer_draws_on_qrhi_as_on_opengl():
    backends = _backends()
    if "d3d12" not in backends:
        pytest.skip("no Direct3D 12 device")
    got = _child(VIEWER_FRAME % "d3d12")
    for look, result in got.items():
        assert result["std"] > 1.0, look  # something drawn
        if "diff" in result:
            assert result["diff"] < 3.0, (look, result)  # the same picture as the OpenGL device's


ISLAND_FRAME = """
import json, time
import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication
from tests.test_editsession import island
from annotoolbox.editor.desktop.desktoprhiview import RhiIslandView3D
from annotoolbox.editor.editsession import EditSession
from annotoolbox.render import camera
app = QApplication([])
s = EditSession(island())
s.finish_loading()
view = RhiIslandView3D(None, "%s")
view.load(s)
marker = next(e for e in s.entries if e.marker == "Herd")
view.camera = camera.OrbitCamera((s.size - marker.position[0], 1.5, marker.position[2]), 12)
for _ in range(100):
    img = view.picture(320, 200)
    if view.failed or (view.renderer is not None and not view.renderer.busy()):
        break
    time.sleep(0.02)
img = view.picture(320, 200)
px = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(0, 320, 4)] for y in range(0, 200, 4)], float)
view.resize(320, 200)
picked = view.pick_entry(QPoint(160, 80))
dpr = view.devicePixelRatioF()
gpu = view.gpu_pick((int(320 * dpr), int(200 * dpr)), 160 * dpr, 80 * dpr)  # the id picture read back
print(json.dumps({"failed": view.failed, "errors": view.ctx.errors, "std": float(px.std()), "gpu": gpu is not None,
                  "picked": picked is not None, "sky": view.pick_entry(QPoint(2, 2)) is None}))
"""


def test_the_island_view_draws_on_qrhi():
    backends = _backends()
    if "d3d12" not in backends:
        pytest.skip("no Direct3D 12 device")
    got = _child(ISLAND_FRAME % "d3d12")
    assert not got["failed"] and not got["errors"], got
    assert got["std"] > 2.0  # ground, sea and sky
    assert got["picked"] and got["gpu"] and got["sky"]  # a click finds the object in the id picture, none in the sky


HIDDEN_VIEW = """
import json
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from annotoolbox.editor.desktop.desktoprhiview import RhiIslandView3D
app = QApplication([])
window = QWidget()
QVBoxLayout(window).addWidget(view := RhiIslandView3D(window, "%s"))
window.resize(900, 600)
window.show()
app.processEvents()
shown = view.picture(64, 64) is not None and view.fixedColorBufferSize().isEmpty()
window.resize(1300, 500)  # a new size and aspect: the frame's screen follows the widget's buffer
app.processEvents()
view.grabFramebuffer()
dpr = view.devicePixelRatioF()
resized = view.ctx.screen.size == (round(view.width() * dpr), round(view.height() * dpr))
before = (view.width(), view.height())
window.hide()  # another app's page in front: the view is hidden inside its window
hidden = view.picture(64, 64)
print(json.dumps({"shown": shown, "resized": resized, "screen": view.ctx.screen.size,
                  "hidden": hidden is None, "same": (view.width(), view.height()) == before}))
"""


def test_the_view_follows_its_window_and_a_hidden_view_keeps_its_size():
    backends = _backends()
    if not backends:
        pytest.skip("no QRhi device")
    got = _child(HIDDEN_VIEW % backends[0])
    assert got["shown"]  # a grab on screen hands the widget its own buffer size back
    assert got["resized"], got  # a resized window draws its whole new size
    assert got["hidden"] and got["same"]  # hidden: nothing drawn, the view keeps its place in the window


MOVED_VIEW = """
import json
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from annotoolbox.render import shaders as S
from annotoolbox.render.rhiview import RhiView
app = QApplication([])
class V(RhiView):
    made = gone = 0
    def ready(self, ctx):
        self.made += 1
        self.prog = ctx.program(vertex_shader=S.FINAL_VS, fragment_shader=S.FINAL_FS)
        self.full = ctx.vertex_array(self.prog, [])
        self.tex = ctx.texture((4, 4), 4, bytes([255, 0, 0, 255]) * 16)
    def released(self, ctx):
        self.gone += 1
        self.old = self.tex
        self.prog.release()
    def draw(self, ctx, screen, size):
        screen.use()
        self.tex.use(0)
        self.prog["frame"].value, self.prog["finish"].value, self.prog["exposure"].value = 0, 3, 1.0
        self.full.render(vertices=3)
def window():
    w = QWidget()
    QVBoxLayout(w)
    w.resize(200, 150)
    return w
a, b = window(), window()
view = V("%s", a)
a.layout().addWidget(view)
a.show()
app.processEvents()
view.grabFramebuffer()
first = view.ctx
held = len(first._srbs) + len(first._pipelines)
b.layout().addWidget(view)  # another window: another device
b.show()
app.processEvents()
img = view.grabFramebuffer()
view.old.release()  # the first device's texture, let go after the device was: destroyed at once
left = len(first._srbs) + len(first._pipelines) + len(first._samplers) + len(first._doomed) + len(first._dying)
print(json.dumps({"made": view.made, "gone": view.gone, "held": held, "left": left, "released": first.released,
                  "new": view.ctx is not None and view.ctx is not first, "failed": view.failed,
                  "errors": view.ctx.errors, "px": img.pixelColor(50, 50).getRgb()[:3]}))
"""


def test_a_view_moved_to_another_window_releases_its_first_device_and_draws_on_the_new_one():
    backends = _backends()
    if not backends:
        pytest.skip("no QRhi device")
    for backend in backends:
        got = _child(MOVED_VIEW % backend)
        assert got["made"] == 2 and got["gone"] == 1 and got["new"], (backend, got)  # told once, made again
        assert got["held"] and got["left"] == 0 and got["released"], (backend, got)  # nothing of the first is kept
        assert not got["failed"] and not got["errors"] and got["px"] == [255, 0, 0], (backend, got)
