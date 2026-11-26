"""The viewer's texture view: a DDS (any level and face, BC7 included: ``models.ddsdecode``) or a PNG, TGA or JPEG,
shown whole or by channel (alpha over a checkerboard), at a zoom; an HDR texture at an exposure, a cube's six faces
as a cross; the header's facts beside it.

``CTextureView(parent)`` is the entry: ``show_texture(path, data)``; the decode and the picture are made off the
window's thread (``level_pixels``, ``render_image``), a small level first. ``channel_image``, ``cube_cross`` and
``describe`` are the parts without Qt. Anno's channel uses (a normal map's alpha is gloss): docs/engine-notes.md.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import shiboken6
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..infra.i18n import N_, tr
from ..models.ddsdecode import CDdsFile, CUnsupportedDds
from ..models.mipchain import exposed_srgb8
from ..suite.background import CBackground

CHANNELS = ("RGBA", "RGB", "R", "G", "B", "A")
ZOOMS = (N_("viewer.fit"), "25%", "50%", "100%", "200%", "400%")
CHECKER = 8  # pixels per square of the transparency checkerboard
# where each face of a cube (DDS order: +X, -X, +Y, -Y, +Z, -Z) sits in a cross of 4 x 3 squares (column, row)
CROSS = ((2, 1), (0, 1), (1, 0), (1, 2), (1, 1), (3, 1))
PREVIEW_PX = 512  # the longest side of the small level shown while the full one decodes


def cube_cross(faces: list[np.ndarray]) -> np.ndarray:
    """A cube's six faces (h, w, 4) laid out as a cross; the squares without a face are transparent."""
    height, width = faces[0].shape[:2]
    out = np.zeros((3 * height, 4 * width, 4), faces[0].dtype)
    for face, (col, row) in zip(faces, CROSS, strict=True):
        out[row * height : (row + 1) * height, col * width : (col + 1) * width] = face
    return out


def channel_image(pixels: np.ndarray, channel: str) -> np.ndarray:
    """``px`` (h, w, 4 uint8) as the RGB picture of one channel choice: the colour over a checkerboard by its alpha
    (``RGBA``), the colour alone (``RGB``), or one channel as grey."""
    if channel in ("R", "G", "B", "A"):
        grey = pixels[..., "RGBA".index(channel)]
        return np.repeat(grey[..., None], 3, axis=2)
    if channel == "RGB":
        return pixels[..., :3].copy()
    alpha_bytes = pixels[..., 3:4]
    if alpha_bytes.min() == 255:  # opaque: the board shows nowhere
        return pixels[..., :3].copy()
    h, w = pixels.shape[:2]
    side = 2 * CHECKER
    square = (np.arange(side) // CHECKER)[:, None] + (np.arange(side) // CHECKER)[None, :]
    board = np.tile(np.where(square % 2 == 0, 200, 150).astype(np.float32), (-(-h // side), -(-w // side)))
    alpha = alpha_bytes.astype(np.float32) / 255.0
    return (pixels[..., :3] * alpha + board[:h, :w, None] * (1 - alpha) + 0.5).astype(np.uint8)


def render_image(pixels: np.ndarray, channel: str, zoom: str, area_px: tuple[int, int]) -> QImage:
    """The picture of ``pixels`` for one channel choice and zoom (``area_px``: the size "Fit" fills), a ``QImage`` so
    that a worker thread can make it."""
    rgb = np.ascontiguousarray(channel_image(pixels, channel))
    height, width = rgb.shape[:2]
    image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()
    if zoom == ZOOMS[0]:
        if min(area_px) <= 0:  # a view with no size yet shows the picture whole
            return image
        fit = Qt.AspectRatioMode.KeepAspectRatio
        return image.scaled(area_px[0], area_px[1], fit, Qt.TransformationMode.SmoothTransformation)
    if zoom == "100%":
        return image
    k = int(zoom[:-1]) / 100
    mode = Qt.TransformationMode.FastTransformation if k > 1 else Qt.TransformationMode.SmoothTransformation
    return image.scaled(int(width * k), int(height * k), Qt.AspectRatioMode.KeepAspectRatio, mode)


@dataclass(frozen=True)
class SLevelRequest:
    """What the decode needs, read off the widgets on the window's thread: the worker touches no widget."""

    dds: CDdsFile
    face: int
    is_cross: bool
    exposure_ev: float | None  # None: not an HDR texture


def level_pixels(request: SLevelRequest, mip: int) -> np.ndarray:
    """The level ``mip`` of the request: one face or, as a cross, the six faces of the cube ``face`` is in; an HDR
    texture at its exposure."""
    faces = (
        range(request.face - request.face % 6, request.face - request.face % 6 + 6)
        if request.is_cross
        else (request.face,)
    )
    if request.exposure_ev is not None:
        pictures = [exposed_srgb8(request.dds.level_float(mip, index), request.exposure_ev) for index in faces]
    else:
        pictures = [request.dds.level(mip, index) for index in faces]
    return cube_cross(pictures) if len(pictures) == 6 else pictures[0]


def preview_level(dds: CDdsFile, mip: int) -> int | None:
    """The first level after ``mip`` of at most ``PREVIEW_PX`` on its longest side, or None when ``mip`` is that
    small already or the file has no such level."""
    if max(dds.size_of(mip)) <= PREVIEW_PX:
        return None
    return next((level for level in range(mip + 1, dds.m_info.mips) if max(dds.size_of(level)) <= PREVIEW_PX), None)


def picture_pixels(data: bytes) -> np.ndarray:
    """A PNG, TGA or JPEG as ``(height, width, 4)`` uint8; ``OSError`` or ``ValueError`` when no decoder reads it."""
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))


def describe(data: bytes) -> list[tuple[str, str]]:
    """The facts of a texture file for the property panel."""
    if data[:4] == b"DDS ":
        try:
            dds_info = CDdsFile(data).m_info
        except CUnsupportedDds as exc:
            return [(tr("viewer.format"), tr("viewer.dds_not_read", error=exc))]
        rows = [
            (tr("viewer.format"), f"DDS {dds_info.format}" + (" (sRGB)" if dds_info.srgb else "")),
            (tr("viewer.size"), f"{dds_info.width_px} x {dds_info.height_px}"),
        ]
        rows += [(tr("viewer.mip_levels"), str(dds_info.mips)), (tr("viewer.faces"), str(dds_info.faces))]
        return rows
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
    except (OSError, ValueError) as exc:  # no picture the decoders read (an empty file, a format they lack)
        return [(tr("viewer.format"), str(exc))]
    return [
        (tr("viewer.format"), f"{image.format} {image.mode}"),
        (tr("viewer.size"), f"{image.width} x {image.height}"),
    ]


class CTextureView(QWidget):
    """A texture with its channel, level, face and zoom chosen above it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.m_dds: CDdsFile | None = None
        self.m_pixels: np.ndarray | None = None
        self.m_serial = 0  # counts the decode requests: only the newest one's pixels are shown
        self.m_wanted: tuple | None = None  # the picture to make next: pixels, channel, zoom, the area
        self.m_is_drawing = False
        self.m_background = CBackground(self)
        self.m_channel = QComboBox()
        self.m_channel.addItems(CHANNELS)
        self.m_mip = QSpinBox(prefix=tr("viewer.level"))
        self.m_face = QSpinBox(prefix=tr("viewer.face"))
        self.m_cross = QCheckBox(tr("viewer.cross"), toolTip=tr("viewer.tip.cubes_six_faces_once"))
        self.m_exposure = QDoubleSpinBox(
            minimum=-12.0,
            maximum=12.0,
            singleStep=0.5,
            prefix=tr("viewer.exposure"),
            suffix=" EV",  # i18n: ok (a unit)
        )
        self.m_zoom = QComboBox()
        for z in ZOOMS:
            self.m_zoom.addItem(tr(z) if z == ZOOMS[0] else z, z)
        self.m_info = QLabel()
        self.m_picture = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.m_scroll = QScrollArea(widgetResizable=True)
        self.m_scroll.setWidget(self.m_picture)
        button_row = QHBoxLayout()
        for widget in (
            QLabel(tr("viewer.channel")),
            self.m_channel,
            self.m_mip,
            self.m_face,
            self.m_cross,
            self.m_exposure,
            QLabel(tr("viewer.zoom")),
            self.m_zoom,
        ):
            button_row.addWidget(widget)
        button_row.addWidget(self.m_info, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self.m_scroll, 1)
        for box in (self.m_channel, self.m_zoom):
            box.currentIndexChanged.connect(lambda _i: self._draw())
        for spin in (self.m_mip, self.m_face, self.m_exposure):
            spin.valueChanged.connect(lambda _v: self._decode())
        self.m_cross.toggled.connect(lambda _on: self._decode())

    def show_texture(self, path: str, data: bytes) -> None:
        """Show a texture file; the level and face start at the top. The pixels are decoded off the window's thread
        and arrive a moment later (``m_serial`` names the newest request)."""
        self.m_serial += 1  # a decode of the last file lands nowhere now
        self.m_dds, self.m_pixels, self.m_wanted = None, None, None
        if data[:4] == b"DDS ":
            try:
                self.m_dds = CDdsFile(data)
            except CUnsupportedDds as exc:
                self.m_info.setText(tr("viewer.path_error", path=path, error=exc))
                self.m_picture.clear()
                return
            for spin, count in ((self.m_mip, self.m_dds.m_info.mips), (self.m_face, self.m_dds.m_info.faces)):
                spin.blockSignals(True)
                spin.setRange(0, count - 1)
                spin.setValue(0)
                spin.setEnabled(count > 1)
                spin.blockSignals(False)
            self.m_cross.setVisible(self.m_dds.m_info.faces % 6 == 0)
            self.m_exposure.setVisible(self.m_dds.m_hdr)
            self._decode()
        else:
            self.m_mip.setEnabled(False)
            self.m_face.setEnabled(False)
            self.m_cross.setVisible(False)
            self.m_exposure.setVisible(False)
            self._start(lambda: picture_pixels(data), None, path)

    def _decode(self) -> None:
        dds = self.m_dds
        if dds is None:
            self._draw()
            return
        request = SLevelRequest(
            dds,
            self.m_face.value(),
            self.m_cross.isChecked() and not self.m_cross.isHidden(),
            self.m_exposure.value() if dds.m_hdr and not self.m_exposure.isHidden() else None,
        )
        mip = self.m_mip.value()
        small = preview_level(dds, mip)
        self._start(
            lambda: level_pixels(request, mip),
            None if small is None else lambda: level_pixels(request, small),
        )

    def _start(self, full, small=None, path: str = "") -> None:
        """Run ``full`` (the picture's pixels) off the window's thread; with ``small``, that one first and ``full``
        after it. A newer request makes the older one's pictures land nowhere."""
        self.m_serial += 1
        serial = self.m_serial

        def finish(result) -> None:
            self._land(serial, result, path, True)

        def first(result) -> None:
            if self._land(serial, result, path, False):
                self.m_background.run(full, finish)

        if small is None:
            self.m_background.run(full, finish)
        else:
            self.m_background.run(small, first)

    def _land(self, serial: int, result, path: str, is_last: bool) -> bool:
        """Show a decode's pixels when ``serial`` is still the newest request; False when it was not, and for a small
        level that failed (the full one says why)."""
        if serial != self.m_serial or not shiboken6.isValid(self):
            return False
        if isinstance(result, Exception):
            if is_last:
                self.m_pixels = None
                self.m_info.setText(tr("viewer.path_error", path=path, error=result) if path else str(result))
                self.m_picture.clear()
            return not is_last
        self.m_pixels = result
        self._draw()
        return True

    def _draw(self) -> None:
        """Show ``m_pixels`` at the channel and zoom chosen. The picture is made off the window's thread, one at a
        time; a request made while one is made replaces the one waiting (``m_wanted``)."""
        pixels = self.m_pixels
        if pixels is None:
            self.m_wanted = None
            return
        area = self.m_scroll.viewport().size()
        self.m_wanted = (pixels, self.m_channel.currentText(), self.m_zoom.currentData(), (area.width(), area.height()))
        self._draw_next()

    def _draw_next(self) -> None:
        if self.m_is_drawing or self.m_wanted is None:
            return
        request, self.m_wanted = self.m_wanted, None
        self.m_is_drawing = True
        pixels = request[0]

        def work():
            return pixels.shape[1], pixels.shape[0], render_image(*request)

        self.m_background.run(work, lambda result: self._drawn(pixels, result))

    def _drawn(self, pixels: np.ndarray, result) -> None:
        if not shiboken6.isValid(self):
            return
        self.m_is_drawing = False
        if isinstance(result, Exception):
            self.m_info.setText(str(result))
        elif self.m_wanted is None and pixels is self.m_pixels:  # not replaced by a newer picture or request
            width, height, image = result
            self.m_picture.setPixmap(QPixmap.fromImage(image))
            self.m_info.setText(f"{width} x {height}")  # i18n: ok (a size)
        self._draw_next()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.m_zoom.currentData() == ZOOMS[0]:
            self._draw()
