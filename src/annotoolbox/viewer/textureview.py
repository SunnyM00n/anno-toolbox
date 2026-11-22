"""The viewer's texture view: a DDS (any level and face, BC7 included: ``models.ddsdecode``) or a PNG, TGA or JPEG,
shown whole or by channel (alpha over a checkerboard), at a zoom; an HDR texture at an exposure, a cube's six faces
as a cross; the header's facts beside it.

``CTextureView(parent)`` is the entry: ``show(path, data)``; ``channel_image``, ``cube_cross`` and
``describe`` are the parts without Qt. Anno's channel uses (a normal map's alpha is gloss): docs/engine-notes.md.
"""

from __future__ import annotations

import io

import numpy as np
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

CHANNELS = ("RGBA", "RGB", "R", "G", "B", "A")
ZOOMS = (N_("viewer.fit"), "25%", "50%", "100%", "200%", "400%")
CHECKER = 8  # pixels per square of the transparency checkerboard
# where each face of a cube (DDS order: +X, -X, +Y, -Y, +Z, -Z) sits in a cross of 4 x 3 squares (column, row)
CROSS = ((2, 1), (0, 1), (1, 0), (1, 2), (1, 1), (3, 1))


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
    h, w = pixels.shape[:2]
    y, x = np.mgrid[0:h, 0:w]
    board = np.where(((x // CHECKER) + (y // CHECKER)) % 2 == 0, 200, 150).astype(np.float32)[..., None]
    alpha = pixels[..., 3:4].astype(np.float32) / 255.0
    return (pixels[..., :3] * alpha + board * (1 - alpha) + 0.5).astype(np.uint8)


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
        """Show a texture file; the level and face start at the top."""
        self.m_dds, self.m_pixels = None, None
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
        else:
            from PIL import Image

            try:
                self.m_pixels = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
            except (OSError, ValueError) as exc:  # no picture the decoders read: the last file's picture must not stay
                self.m_info.setText(tr("viewer.path_error", path=path, error=exc))
                self.m_picture.clear()
                return
            self.m_mip.setEnabled(False)
            self.m_face.setEnabled(False)
            self.m_cross.setVisible(False)
            self.m_exposure.setVisible(False)
        self._decode()

    def _decode(self) -> None:
        if self.m_dds is not None:
            try:
                self.m_pixels = self.texels(self.m_mip.value(), self.m_face.value())
            except (CUnsupportedDds, IndexError) as exc:  # a level cut short, a face that is not there
                self.m_info.setText(str(exc))
                self.m_picture.clear()
                return
        self._draw()

    def texels(self, mip: int, face: int) -> np.ndarray:
        """The level shown: one face or, with the cross, the six faces of the cube ``face`` is in; an HDR texture at
        its exposure."""
        if self.m_dds is None:
            raise IndexError("no texture")
        faces = (
            range(face - face % 6, face - face % 6 + 6)
            if self.m_cross.isChecked() and not self.m_cross.isHidden()
            else (face,)
        )
        if self.m_dds.m_hdr and not self.m_exposure.isHidden():
            pictures = [
                exposed_srgb8(self.m_dds.level_float(mip, face_index), self.m_exposure.value()) for face_index in faces
            ]
        else:
            pictures = [self.m_dds.level(mip, face_index) for face_index in faces]
        return cube_cross(pictures) if len(pictures) == 6 else pictures[0]

    def _draw(self) -> None:
        if self.m_pixels is None:
            return
        rgb = np.ascontiguousarray(channel_image(self.m_pixels, self.m_channel.currentText()))
        height, width = rgb.shape[:2]
        image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()
        pixmap = QPixmap.fromImage(image)
        zoom = self.m_zoom.currentData()
        if zoom == ZOOMS[0]:
            area = self.m_scroll.viewport().size()
            pixmap = pixmap.scaled(area, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        elif zoom != "100%":
            k = int(zoom[:-1]) / 100
            mode = Qt.TransformationMode.FastTransformation if k > 1 else Qt.TransformationMode.SmoothTransformation
            pixmap = pixmap.scaled(int(width * k), int(height * k), Qt.AspectRatioMode.KeepAspectRatio, mode)
        self.m_picture.setPixmap(pixmap)
        self.m_info.setText(f"{width} x {height}")  # i18n: ok (a size)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.m_zoom.currentData() == ZOOMS[0]:
            self._draw()
