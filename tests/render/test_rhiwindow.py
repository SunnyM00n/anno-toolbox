"""The 3D view in a window of its own (``render.rhiwindow``, performance.md section 6.7): the child widgets' mirror,
a lost device and a refused texture, the rest after a slow frame, where the window can be made, and on Windows' own
platform (a child process) the island view drawn, grabbed, picked, resized, its window made anew and letting input
through to the widget."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QRhi
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget
from tests.render.renderfakes import render_backends, run_child

from annotoolbox.editor.desktop import desktoprhiview, desktopview
from annotoolbox.render import rhiwindow


def test_pictures_stack_one_under_the_other_and_rectangles_follow_the_pixel_ratio():
    assert rhiwindow.stack([(40, 10), (25, 7)]) == ((40, 17), [0, 10])
    assert rhiwindow.stack([]) == ((1, 1), [])
    assert rhiwindow.scale_rect((8, 8, 51, 20), 1.5) == (12, 12, 76, 30)  # its far edge rounded, not its width
    assert rhiwindow.scale_rect((0, 0, 0, 0), 2.0) == (0, 0, 1, 1)
    packed = rhiwindow.pack_rects([(1, 2, 3, 4)], 2)
    assert len(packed) == 32 and packed[16:] == bytes(16)


def test_the_mirror_draws_the_visible_children_but_the_container_and_again_only_on_a_change():
    QApplication.instance() or QApplication([])
    view = QWidget()
    view.resize(300, 200)
    container = QWidget(view)
    first, second, hidden = QLabel("12 fps", view), QLabel("x 3, z 4", view), QLabel("gone", view)
    first.setGeometry(8, 8, 60, 20)
    second.setGeometry(8, 160, 80, 20)
    view.show()
    hidden.hide()
    mirror = rhiwindow.CChildMirror(view, container)
    assert mirror.collect_children() == [first, second]
    assert mirror.refresh(1.5)
    assert mirror.m_places == [(12, 12, 90, 30), (12, 240, 120, 30)]
    assert mirror.m_sources == [(0, 0, 90, 30), (0, 30, 120, 30)]
    assert (mirror.m_image.width(), mirror.m_image.height()) == (120, 60)
    assert not mirror.refresh(1.5)  # nothing changed: the image stays
    first.setText("13 fps")
    assert mirror.refresh(1.5)
    view.close()


def test_the_mirror_leaves_out_a_child_window_such_as_the_views_context_menu():
    """The view's context menu is its child but a window of its own, placed on the screen: it was drawn into the
    frame too, a copy of the open menu at its screen position."""
    QApplication.instance() or QApplication([])
    view = QWidget()
    view.resize(300, 200)
    label = QLabel("12 fps", view)
    view.show()
    menu = QMenu(view)
    menu.addAction("Hide")
    menu.popup(QPoint(40, 40))
    try:
        assert menu.isVisible() and rhiwindow.CChildMirror(view, QWidget(view)).collect_children() == [label]
    finally:
        menu.close()
        view.close()


class FakeSurface:
    """The native window as the frame code asks it: always exposed; counts the update requests."""

    def __init__(self) -> None:
        self.m_requests = 0

    def isExposed(self) -> bool:
        return True

    def requestUpdate(self) -> None:
        self.m_requests += 1


class FakeSwapChain:
    def __init__(self) -> None:
        self.m_destroyed = False

    def surfacePixelSize(self) -> QSize:
        return QSize(64, 32)

    def currentPixelSize(self) -> QSize:
        return QSize(64, 32)

    def currentFrameCommandBuffer(self):
        return None

    def destroy(self) -> None:
        self.m_destroyed = True


class FakeRhi:
    """A QRhi whose frames begin and end with the given results and whose textures are refused when asked."""

    def __init__(self, begun=QRhi.FrameOpResult.FrameOpSuccess, ended=QRhi.FrameOpResult.FrameOpSuccess) -> None:
        self.m_begun, self.m_ended = begun, ended

    def beginFrame(self, _chain):
        return self.m_begun

    def endFrame(self, _chain):
        return self.m_ended

    def finish(self):
        return QRhi.FrameOpResult.FrameOpSuccess

    def backendName(self) -> str:
        return "Fake"

    def newTexture(self, *_args):
        return SimpleNamespace(create=lambda: False)


def _fake_device(view, rhi: FakeRhi) -> list[str]:
    """``view`` on ``rhi`` with a stand-in context and swap chain; the list says what was released."""
    done: list[str] = []
    view.m_surface = FakeSurface()
    view._m_rhi, view._m_swap_chain = rhi, FakeSwapChain()
    view.m_ctx = SimpleNamespace(release=lambda: done.append("ctx"), later=lambda _native: None)
    view.released = lambda _ctx: done.append("renderer")
    return done


@pytest.mark.parametrize("lost_at", ["begin", "end"])
def test_a_lost_device_lets_everything_of_it_go_and_the_next_frame_makes_a_new_one(lost_at, monkeypatch):
    """A driver reset ends a frame with the device lost: the view kept the dead device and drew nothing from then on
    (a ``QRhiWidget`` makes a new one on its own)."""
    QApplication.instance() or QApplication([])
    view = rhiwindow.CRhiWindowView()
    lost, fine = QRhi.FrameOpResult.FrameOpDeviceLost, QRhi.FrameOpResult.FrameOpSuccess
    rhi = FakeRhi(lost, fine) if lost_at == "begin" else FakeRhi(fine, lost)
    done = _fake_device(view, rhi)
    surface = view.m_surface
    monkeypatch.setattr(view, "_record", lambda _buffer, _grab: None)
    try:
        view.frame_now()
        assert done == ["renderer", "ctx"] and view.m_ctx is None and view.rhi() is None
        assert surface.m_requests == 1  # a frame again, on a new device
    finally:
        view.deleteLater()


def test_a_frame_texture_the_device_refuses_is_kept_as_the_reason_not_raised():
    QApplication.instance() or QApplication([])
    view = rhiwindow.CRhiWindowView()
    _fake_device(view, FakeRhi())
    try:
        view.frame_now()  # raised RuntimeError out of Qt's event
        assert "was not made" in view.m_failed
    finally:
        view.deleteLater()


def test_the_island_views_window_frames_count_for_its_rest_after_a_slow_frame(monkeypatch):
    """Its frames are no paint events: a slow one was never followed by the rest the widget takes (performance.md
    6, item 2), so an island that loads in the window kept the thread busy."""
    QApplication.instance() or QApplication([])
    view = desktoprhiview.CWindowIslandView3D(None)
    monkeypatch.setattr(view, "_swap_frame", lambda _grab: time.sleep(0.12))
    try:
        view._m_input_at = time.monotonic() - 10.0
        view.frame_now()
        assert view._m_paint_s > desktopview.SLOW_FRAME_S and view._resting(time.monotonic())
    finally:
        view.deleteLater()


def test_the_own_window_is_made_only_with_direct3d_12_on_windows_own_platform():
    QApplication.instance() or QApplication([])
    assert not rhiwindow.is_possible("d3d12")  # the tests' offscreen platform makes no native windows
    assert not rhiwindow.is_possible("vulkan")
    view = desktoprhiview.make_island_view("d3d12", None, is_own_window=True)
    try:
        assert type(view) is desktoprhiview.CRhiIslandView3D  # the widget, as before
    finally:
        view.deleteLater()


WINDOW_VIEW = """
import ctypes, json, time
import numpy as np
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QTextEdit
from tests.editor.editorfakes import edit_island as island
from annotoolbox.editor.desktop.desktoprhiview import CWindowIslandView3D, make_island_view
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.render import camera
app = QApplication([])
window = QMainWindow()
view = make_island_view("d3d12", window, is_own_window=True)
window.setCentralWidget(view)
dock = QDockWidget("dock")
dock.setWidget(QTextEdit())
window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
window.resize(700, 400)
window.show()
s = CEditSession(island())
s.finish_loading()
view.load(s)
marker = next(e for e in s.m_entries if e.marker == "Herd")
view.m_camera = camera.COrbitCamera((s.m_size_tiles - marker.position[0], 1.5, marker.position[2]), 12)
drawn = []
for _ in range(400):
    app.processEvents()
    view.update()
    if view.m_failed or (view.m_renderer is not None and not view.m_renderer.busy()):
        break
    time.sleep(0.01)
img = view.picture(320, 200)
px = np.array([[img.pixelColor(x, y).getRgb()[:3] for x in range(0, 320, 4)] for y in range(0, 200, 4)], float)
dpr = view.devicePixelRatioF()
size = (round(view.width() * dpr), round(view.height() * dpr))
whole = view.grabFramebuffer()
gpu = view.pick_entry(QPoint(view.width() // 2, round(view.height() * 0.4)))
window.resize(900, 500)
for _ in range(30):
    app.processEvents()
    time.sleep(0.01)
view.grabFramebuffer()
resized = view.m_ctx.screen.size == (round(view.width() * dpr), round(view.height() * dpr))
hwnd = int(view.m_surface.winId())
seen = []
class Seen(QObject):
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            seen.append(event.position().toPoint().toTuple())
        return False
spy = Seen()
view.installEventFilter(spy)
view.setCursor(Qt.CursorShape.CrossCursor)
cursor = view.m_surface.cursor().shape() == Qt.CursorShape.CrossCursor
view.m_tool = "paint"  # a click there neither picks nor places
for message, buttons in ((0x0201, 1), (0x0202, 0)):  # the button down and up posted to the native window
    ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), message, buttons, (30 << 16) | 40)
for _ in range(30):
    app.processEvents()
    time.sleep(0.01)
renderer = view.m_renderer
window.windowHandle().destroy()  # the top-level's native window made anew, as a QRhiWidget appearing in it does
window.show()
for _ in range(60):
    app.processEvents()
    view.update()
    time.sleep(0.01)
again = view.grabFramebuffer()
remade = view.m_renderer is renderer and again is not None and again.width() > 0
print(json.dumps({"own": type(view) is CWindowIslandView3D, "remade": remade, "failed": view.m_failed,
                  "errors": view.m_ctx.m_errors,
                  "std": float(px.std()), "whole": [whole.width(), whole.height()], "size": list(size),
                  "gpu": gpu is not None, "resized": resized, "through": seen, "cursor": cursor,
                  "mirrored": len(view.m_mirror.m_places)}))
"""


def test_the_island_view_draws_in_its_own_window_and_lets_input_through():
    if "d3d12" not in render_backends():
        pytest.skip("no Direct3D 12 device")
    got = run_child(WINDOW_VIEW)
    assert got["own"] and not got["failed"] and not got["errors"], got
    assert got["std"] > 2.0  # ground, sea and sky in a picture of another size than the window's
    assert got["whole"] == got["size"]  # a grab is the window's own pixels
    assert got["gpu"]  # a click finds the object: its id picture is read back in the grabbed frame
    assert got["resized"]  # the frame follows the window's new size
    assert got["through"] == [[40, 30], [40, 30]]  # a click on the window reaches the view widget beneath
    assert got["cursor"]  # the view's cursor shown over its window
    assert got["remade"]  # a window made anew takes a new swap chain; the renderer and its island stay
    assert got["mirrored"] >= 1  # the frame rate's label drawn over the frame
