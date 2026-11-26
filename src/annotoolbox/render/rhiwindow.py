"""The 3D view in a native window of its own: a ``QWindow`` with its own QRhi (Direct3D 12) and swap chain inside a
widget, so the window's thread records the next frame while the GPU draws the last one. A ``QRhiWidget`` waits for
each frame's GPU work before its paint event ends (docs/rendering/performance.md section 6.7).

``CRhiWindowView`` is the widget, with ``rhiview.CRhiView``'s interface (``ready``, ``released``, ``draw``, ``picture``,
``grabFramebuffer``, ``rhi``); ``is_possible(backend)`` says where one can be made. ``CRhiSurface`` is the window, and
``CChildMirror`` draws the widget's children over the frame, since the native window covers them.
"""

from __future__ import annotations

import os
import struct
import sys
import traceback

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QGuiApplication,
    QImage,
    QPainter,
    QPlatformSurfaceEvent,
    QRegion,
    QRhi,
    QRhiTexture,
    QSurface,
    QWindow,
)
from PySide6.QtWidgets import QWidget

from . import rhipass
from .nativepass import CComposite
from .rhidevice import CFramebuffer, CRhiContext, CTexture
from .rhiview import DEBUG_ENV

MIRRORED = 4  # child widgets drawn over the frame at most: the view has three (frame rate, cursor's tile, hint) [ours]
PRESENT_FS = """
#version 330
uniform sampler2D frame; uniform sampler2D children;
uniform vec4 places[4]; uniform vec4 sources[4]; uniform int childCount;
in vec2 vNdc; out vec4 color;  // read below: Direct3D 12 refuses a pixel shader whose inputs skip the vertex's first
void main() {
    if (abs(vNdc.x) > 2.0) discard;
    vec2 at = gl_FragCoord.xy;
    vec3 rgb = texelFetch(frame, ivec2(at), 0).rgb;
    for (int i = 0; i < childCount; i++) {
        vec2 inside = at - places[i].xy;
        if (all(greaterThanEqual(inside, vec2(0.0))) && all(lessThan(inside, places[i].zw))) {
            vec4 child = texelFetch(children, ivec2(sources[i].xy + inside), 0);  // premultiplied
            rgb = child.rgb + rgb * (1.0 - child.a);
        }
    }
    color = vec4(rgb, 1.0);
}
"""


def is_possible(backend: str) -> bool:
    """Whether the view can draw in a window of its own here: Direct3D 12 on Windows' own platform (Qt's offscreen
    platform, the tests', makes no native windows)."""
    platform = QGuiApplication.platformName() if QGuiApplication.instance() is not None else ""
    return backend == "d3d12" and sys.platform == "win32" and platform == "windows"


def stack(sizes_px: list[tuple[int, int]]) -> tuple[tuple[int, int], list[int]]:
    """One image holding pictures of ``sizes_px`` one under the other: its size and each picture's top row."""
    tops, height_px = [], 0
    for _width_px, picture_height_px in sizes_px:
        tops.append(height_px)
        height_px += picture_height_px
    return (max((width_px for width_px, _ in sizes_px), default=1) or 1, height_px or 1), tops


def scale_rect(rect: tuple[int, int, int, int], ratio: float) -> tuple[int, int, int, int]:
    """A widget's rectangle (x, y, width, height in its parent) in the frame's pixels."""
    x, y, width, height = rect
    left, top = round(x * ratio), round(y * ratio)
    return left, top, max(1, round((x + width) * ratio) - left), max(1, round((y + height) * ratio) - top)


def pack_rects(rects: list[tuple[int, int, int, int]], count: int) -> bytes:
    """``count`` rectangles as a std140 ``vec4`` array, zeros past the given ones."""
    padded = [*rects, *[(0, 0, 0, 0)] * count][:count]
    return b"".join(struct.pack("4f", *(float(value) for value in rect)) for rect in padded)


class CChildMirror:
    """The view's visible child widgets but ``skipped`` drawn into one image, one under the other, again only when one
    of them changed; ``places`` and ``sources`` say where each stands in the frame and in the image."""

    def __init__(self, view: QWidget, skipped: QWidget) -> None:
        self.m_view, self.m_skipped = view, skipped
        self.m_key: tuple | None = None
        self.m_image: QImage | None = None  # premultiplied RGBA in the frame's pixels
        self.m_places: list[tuple[int, int, int, int]] = []
        self.m_sources: list[tuple[int, int, int, int]] = []
        self.m_texture: CTexture | None = None  # the image on the device
        self.m_uploaded: tuple | None = None  # the key of what the texture holds

    def collect_children(self) -> list[QWidget]:
        """The visible child widgets drawn in the view; a child window (the view's context menu) is a window of its
        own over the native one, and its place is on the screen, not in the view."""
        found = self.m_view.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
        shown = [child for child in found if child is not self.m_skipped and child.isVisible()]
        return [child for child in shown if not child.isWindow()][:MIRRORED]

    def refresh(self, ratio: float) -> bool:
        """The image made again when a child's place, size or text changed; True when it was."""
        children = self.collect_children()
        key = (ratio, *((child.geometry().getRect(), getattr(child, "text", str)()) for child in children))
        if key == self.m_key:
            return False
        self.m_key = key
        self.m_places = [scale_rect(child.geometry().getRect(), ratio) for child in children]
        (width_px, height_px), tops = stack([(place[2], place[3]) for place in self.m_places])
        self.m_sources = [(0, top, place[2], place[3]) for top, place in zip(tops, self.m_places, strict=True)]
        image = QImage(width_px, height_px, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        for child, top in zip(children, tops, strict=True):
            painter.resetTransform()
            painter.translate(QPointF(0.0, float(top)))
            painter.scale(ratio, ratio)
            child.render(painter, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        painter.end()
        self.m_image = image.convertToFormat(QImage.Format.Format_RGBA8888_Premultiplied)
        return True

    def texture(self, ctx) -> CTexture:
        """The image on ``ctx``'s device, uploaded again when it changed (one clear pixel before the first)."""
        image = self.m_image
        if image is None:
            image = QImage(1, 1, QImage.Format.Format_RGBA8888_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
        size_px = (image.width(), image.height())
        data = bytes(image.constBits())[: size_px[0] * size_px[1] * 4]
        if self.m_texture is not None and self.m_texture.size != size_px:
            self.m_texture.release()
            self.m_texture = None
        if self.m_texture is None:
            self.m_texture = ctx.texture(size_px, 4, data)
        elif self.m_uploaded != self.m_key:
            self.m_texture.write(data)
        self.m_uploaded = self.m_key
        return self.m_texture

    def release(self) -> None:
        if self.m_texture is not None:
            self.m_texture.release()
        self.m_texture, self.m_uploaded = None, None


class _CSwapChainTarget:
    """The swap chain's back buffer standing as a framebuffer's colour attachment: its size; never sampled."""

    samples = 0
    native = None

    def __init__(self, size_px: tuple[int, int]) -> None:
        self.size = size_px


class CRhiSurface(QWindow):
    """The native window the view draws into: a Direct3D surface that lets every input through to the widget beneath,
    so focus, shortcuts, drops and menus stay the widget's."""

    def __init__(self, view: CRhiWindowView) -> None:
        super().__init__()
        self.m_view = view
        self.setSurfaceType(QSurface.SurfaceType.Direct3DSurface)
        self.setFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.setFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)

    def exposeEvent(self, event) -> None:  # Qt's name
        if self.isExposed():
            self.requestUpdate()

    def resizeEvent(self, event) -> None:  # Qt's name
        super().resizeEvent(event)
        self.requestUpdate()

    def event(self, event) -> bool:  # Qt's name
        kind = event.type()
        if kind == QEvent.Type.UpdateRequest:
            self.m_view.frame_now()
            return True
        if (
            kind == QEvent.Type.PlatformSurface
            and event.surfaceEventType() == QPlatformSurfaceEvent.SurfaceEventType.SurfaceAboutToBeDestroyed
        ):
            self.m_view.drop_swap_chain()  # it names this window; the device and the renderer stay
        return super().event(event)


class CRhiWindowView(QWidget):
    """A 3D view drawn into a window of its own (module docstring); ``draw`` is the subclass's frame, ``failed`` the
    first error a frame raised."""

    def __init__(self, backend: str = "d3d12", parent=None) -> None:
        super().__init__(parent)
        self.m_backend = backend
        self.m_ctx: CRhiContext | None = None
        self.m_failed = ""
        self._m_rhi = None
        self._m_swap_chain = None
        self._m_render_pass = None
        self._m_texture = None  # the frame's colour (a QRhiTexture) at the swap chain's size
        self._m_frame: CTexture | None = None  # the same for the present pass
        self._m_grab = None  # a picture's colour at the size asked for
        self._m_present: CComposite | None = None
        self._m_display: CFramebuffer | None = None  # the swap chain's back buffer
        self._m_is_due = False  # a frame is asked for (``update``)
        self.m_surface = CRhiSurface(self)
        self.m_container = QWidget.createWindowContainer(self.m_surface, self)
        self.m_container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.m_container.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.m_container.lower()
        self.m_mirror = CChildMirror(self, self.m_container)
        application = QGuiApplication.instance()
        if application is not None:  # the device goes before Qt's own objects (a bound slot: gone with the view)
            application.aboutToQuit.connect(self.release_device)

    def rhi(self):
        return self._m_rhi

    def ready(self, ctx: CRhiContext) -> None:
        """The device is made (again, after the window was made anew): the subclass makes its renderer."""

    def released(self, ctx: CRhiContext) -> None:
        """The device goes away (the window is destroyed): the subclass releases its renderer, whose resources are
        this device's."""

    def draw(self, ctx: CRhiContext, screen, size_px: tuple[int, int]) -> None:
        """One frame into ``screen`` (``size_px``)."""

    def update(self) -> None:  # QWidget's name: a frame as soon as the event loop is free
        """A frame when the events queued now are handled: not at the display's next refresh, as Qt's update request
        would, which left the thread idle for half a refresh a frame (``beginFrame`` paces it by the frames in
        flight)."""
        if not self._m_is_due:
            self._m_is_due = True
            QTimer.singleShot(0, self, self._run_due)

    def _run_due(self) -> None:
        self._m_is_due = False
        self.frame_now()

    def resizeEvent(self, event) -> None:  # Qt's name
        super().resizeEvent(event)
        self.m_container.setGeometry(self.rect())

    def event(self, event) -> bool:  # Qt's name: the cursor over the view is the native window's
        if event.type() == QEvent.Type.CursorChange:
            self.m_surface.setCursor(self.cursor())
        return super().event(event)

    # the device
    def _make_device(self) -> CRhiContext:
        """The QRhi and the context, made on first use; ``ready`` is told."""
        if self.m_ctx is None:
            from PySide6.QtGui import QRhiD3D12InitParams  # Qt's Windows builds only

            params = QRhiD3D12InitParams()
            params.enableDebugLayer = os.environ.get(DEBUG_ENV, "") == "1"
            rhi = QRhi.create(QRhi.Implementation.D3D12, params, QRhi.Flag(0), None)
            if rhi is None:
                raise RuntimeError("no Direct3D 12 device for the view's window")
            self._m_rhi, self.m_ctx = rhi, CRhiContext(rhi)
            self.ready(self.m_ctx)
        return self.m_ctx

    def _fit_swap_chain(self) -> bool:
        """The swap chain made on the window's first frame and resized to the window's pixels; False: no frame now."""
        if self._m_swap_chain is None:
            chain = self._m_rhi.newSwapChain()
            chain.setWindow(self.m_surface)
            if self._m_render_pass is None:
                self._m_render_pass = chain.newCompatibleRenderPassDescriptor()
            chain.setRenderPassDescriptor(self._m_render_pass)
            self._m_swap_chain = chain
        size = self._m_swap_chain.surfacePixelSize()
        if size.isEmpty():
            return False
        if size != self._m_swap_chain.currentPixelSize() and not self._m_swap_chain.createOrResize():
            raise RuntimeError("the view's swap chain was not made")
        return True

    def drop_swap_chain(self) -> None:
        """The swap chain let go (its native window is destroyed: the top-level window was made anew, or it closes);
        the next frame makes one for the new window. The render pass stays: the present pass's pipeline is made for
        it."""
        if self._m_swap_chain is not None:
            self._m_rhi.finish()
            self._m_swap_chain.destroy()
            self._m_swap_chain = self._m_display = None

    def release_device(self) -> None:
        """Everything of the device let go, the renderer's first (the application quits, the device was lost, or the
        owner is done with the view; a later frame makes it all again and tells ``ready``)."""
        ctx, self.m_ctx = self.m_ctx, None
        if ctx is None:
            return
        self._m_rhi.finish()  # no frame in flight from here on
        try:
            self.released(ctx)
        except Exception as exc:  # pattern: ok (a boundary: the quit signal or a frame; kept in ``failed``)
            self.m_failed = self.m_failed or f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        finally:
            self._release_rest(ctx)

    def _release_rest(self, ctx: CRhiContext) -> None:
        from . import nativegpu  # Windows only

        nativegpu.detach(self._m_rhi)  # the native backend's hold on the device goes before the device
        if self._m_present is not None:
            self._m_present.release()
        self.m_mirror.release()
        ctx.release()
        for resource in (self._m_texture, self._m_grab, self._m_swap_chain, self._m_render_pass):
            if resource is not None:
                resource.destroy()
        self._m_texture = self._m_frame = self._m_grab = self._m_swap_chain = self._m_render_pass = None
        self._m_present = self._m_display = self._m_rhi = None

    # frames
    def frame_now(self) -> None:
        """One frame into the swap chain (Qt's update request): the view's frame into its texture, the present pass,
        and the present; nothing while the window is not exposed."""
        self._swap_frame(None)

    def _swap_frame(self, grab_size: tuple[int, int] | None):
        """A swap chain frame; ``grab_size``: the view's frame drawn at that size and read back (the present shows the
        last picture again when that is not the window's size). Returns the read-back, or None."""
        if not self.m_surface.isExposed():
            return None
        try:
            self._make_device()
            if not self._fit_swap_chain():
                return None
        except RuntimeError as exc:
            self.m_failed = self.m_failed or str(exc)
            return None
        chain = self._m_swap_chain
        result = self._m_rhi.beginFrame(chain)
        if result == QRhi.FrameOpResult.FrameOpSwapChainOutOfDate:
            chain.createOrResize()
            self.m_surface.requestUpdate()
            return None
        if result == QRhi.FrameOpResult.FrameOpDeviceLost:
            self._lose_device()
            return None
        if result != QRhi.FrameOpResult.FrameOpSuccess:
            return None
        try:
            read = self._record(chain.currentFrameCommandBuffer(), grab_size)
        finally:
            ended = self._m_rhi.endFrame(chain)
        if ended == QRhi.FrameOpResult.FrameOpDeviceLost:
            self._lose_device()
            return None
        return read

    def _lose_device(self) -> None:
        """The device was lost (a driver reset, a GPU hang): as QRhi asks, everything of it goes, and the next frame
        makes a new device and tells ``ready``. A ``QRhiWidget`` does the same on its own."""
        self.release_device()
        self.m_surface.requestUpdate()

    def _record(self, command_buffer, grab_size: tuple[int, int] | None):
        """The view's frame, a grab's read-back and the present pass recorded and run; errors kept in ``failed``."""
        ctx, window_size = self.m_ctx, self._m_swap_chain.currentPixelSize().toTuple()
        try:
            texture = self._fit_colour(window_size)
            if grab_size not in (None, window_size):
                self._m_grab = texture = self._make_colour(self._m_grab, grab_size)
        except RuntimeError as exc:  # a texture the device refused: an empty frame, the reason kept
            self.m_failed = self.m_failed or str(exc)
            return None
        screen = ctx.begin(command_buffer, texture)
        size_px = texture.pixelSize().toTuple()
        read = []
        steps = [lambda: self.draw(ctx, screen, size_px)]
        if grab_size is not None:
            steps.append(lambda: read.append(ctx.read_later(screen.color_attachments[0])))
        steps += [self._present, ctx.end]
        for step in steps:
            try:
                step()
            except Exception as exc:  # pattern: ok (a boundary: an error inside Qt's event would vanish; kept)
                if not self.m_failed:
                    self.m_failed = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()
        return read[0] if read else None

    def _fit_colour(self, size_px: tuple[int, int]):
        """The window's frame texture at ``size_px``, and its wrapper for the present pass."""
        made = self._make_colour(self._m_texture, size_px)
        if made is not self._m_texture:
            if self._m_frame is not None:
                self.m_ctx.forget(self._m_frame.m_serial)
            self._m_texture = made
            self._m_frame = CTexture(self.m_ctx, size_px, 4, native=made, m_serial=rhipass.CRecorder.next_serial())
        return made

    def _make_colour(self, texture, size_px: tuple[int, int]):
        """``texture`` while it has ``size_px``, else a new colour texture of that size (the old one let go late)."""
        if texture is not None and texture.pixelSize().toTuple() == size_px:
            return texture
        if texture is not None:
            self.m_ctx.later(texture)
        flags = QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource
        made = self._m_rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(*size_px), 1, flags)
        if not made.create():
            raise RuntimeError(f"the view's frame {size_px} was not made")
        return made

    def _present(self) -> None:
        """The window's frame texture (the last picture again after a grab of another size) and the child widgets into
        the swap chain's back buffer."""
        ctx = self.m_ctx
        if self._m_present is None:
            self._m_present = CComposite(ctx, PRESENT_FS)
        mirror = self.m_mirror
        mirror.refresh(self.devicePixelRatioF())
        uniforms = {"places": pack_rects(mirror.m_places, MIRRORED), "sources": pack_rects(mirror.m_sources, MIRRORED),
                    "childCount": len(mirror.m_places)}  # fmt: skip
        textures = {"frame": self._m_frame, "children": mirror.texture(ctx)}
        self._m_present.draw(self._fit_display(), textures, uniforms, depth_test=False)

    def _fit_display(self) -> CFramebuffer:
        """The swap chain's back buffer as the present pass's framebuffer (its render target this frame)."""
        chain, size_px = self._m_swap_chain, self._m_swap_chain.currentPixelSize().toTuple()
        if self._m_display is None or self._m_display.size != size_px:
            self._m_display = CFramebuffer(self.m_ctx, [_CSwapChainTarget(size_px)], None, m_display=True)
        # ``rhipass``'s signature: (colour formats, depth, samples, resolve); the swap chain's format named apart
        signature = ((("swap chain", int(chain.format().value)),), False, 1, False)
        target = (chain.currentFrameRenderTarget(), self._m_render_pass, signature)
        self._m_display.m_targets = {(True, None): target, (False, None): target}
        return self._m_display

    def grabFramebuffer(self) -> QImage | None:  # QRhiWidget's name
        """The view's frame drawn now and read back once the GPU ran it (None while the window is not exposed)."""
        size = self.m_surface.size() * self.m_surface.devicePixelRatio()
        return self._grab((size.width(), size.height()))

    def picture(self, width_px: int, height_px: int) -> QImage | None:
        """One frame at ``width_px`` x ``height_px`` as a ``QImage`` (None while the window is not exposed: a hidden
        view keeps its place and size, as ``rhiview.CRhiView.picture``)."""
        return self._grab((width_px, height_px))

    def _grab(self, size_px: tuple[int, int]) -> QImage | None:
        result = self._swap_frame(size_px)
        if result is None:
            return None
        self._m_rhi.finish()  # the frame's GPU work done: its read-backs are filled
        size, data = result.pixelSize, bytes(result.data)  # the image below reads ``data`` until it is copied
        image = QImage(data, size.width(), size.height(), QImage.Format.Format_RGBA8888_Premultiplied)
        return image.mirrored() if self._m_rhi.isYUpInFramebuffer() else image.copy()
