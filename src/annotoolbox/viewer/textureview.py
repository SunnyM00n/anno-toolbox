"""The viewer's texture view: a DDS (any level and face, BC7 included: ``models.ddsdecode``) or a PNG, TGA or JPEG,
shown whole or by channel (alpha over a checkerboard), at a zoom; an HDR texture at an exposure, a cube's six faces
as a cross; the header's facts beside it.

``TextureView(parent)`` is the entry: ``show(path, data)``; ``channel_image``, ``cube_cross`` and
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
from ..models.ddsdecode import DdsFile, UnsupportedDds
from ..models.mipchain import exposed_srgb8

CHANNELS = ("RGBA", "RGB", "R", "G", "B", "A")
ZOOMS = (N_("viewer.fit"), "25%", "50%", "100%", "200%", "400%")
CHECKER = 8  # pixels per square of the transparency checkerboard
# where each face of a cube (DDS order: +X, -X, +Y, -Y, +Z, -Z) sits in a cross of 4 x 3 squares (column, row)
CROSS = ((2, 1), (0, 1), (1, 0), (1, 2), (1, 1), (3, 1))


def cube_cross(faces: list[np.ndarray]) -> np.ndarray:
    """A cube's six faces (h, w, 4) laid out as a cross; the squares without a face are transparent."""
    h, w = faces[0].shape[:2]
    out = np.zeros((3 * h, 4 * w, 4), faces[0].dtype)
    for face, (col, row) in zip(faces, CROSS, strict=True):
        out[row * h : (row + 1) * h, col * w : (col + 1) * w] = face
    return out


def channel_image(px: np.ndarray, channel: str) -> np.ndarray:
    """``px`` (h, w, 4 uint8) as the RGB picture of one channel choice: the colour over a checkerboard by its alpha
    (``RGBA``), the colour alone (``RGB``), or one channel as grey."""
    if channel in ("R", "G", "B", "A"):
        grey = px[..., "RGBA".index(channel)]
        return np.repeat(grey[..., None], 3, axis=2)
    if channel == "RGB":
        return px[..., :3].copy()
    h, w = px.shape[:2]
    y, x = np.mgrid[0:h, 0:w]
    board = np.where(((x // CHECKER) + (y // CHECKER)) % 2 == 0, 200, 150).astype(np.float32)[..., None]
    alpha = px[..., 3:4].astype(np.float32) / 255.0
    return (px[..., :3] * alpha + board * (1 - alpha) + 0.5).astype(np.uint8)


def describe(data: bytes) -> list[tuple[str, str]]:
    """The facts of a texture file for the property panel."""
    if data[:4] == b"DDS ":
        try:
            info = DdsFile(data).info
        except UnsupportedDds as exc:
            return [(tr("viewer.format"), tr("viewer.dds_not_read", error=exc))]
        rows = [
            (tr("viewer.format"), f"DDS {info.format}" + (" (sRGB)" if info.srgb else "")),
            (tr("viewer.size"), f"{info.width} x {info.height}"),
        ]
        rows += [(tr("viewer.mip_levels"), str(info.mips)), (tr("viewer.faces"), str(info.faces))]
        return rows
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
    except (OSError, ValueError) as exc:  # no picture the decoders read (an empty file, a format they lack)
        return [(tr("viewer.format"), str(exc))]
    return [(tr("viewer.format"), f"{img.format} {img.mode}"), (tr("viewer.size"), f"{img.width} x {img.height}")]


class TextureView(QWidget):
    """A texture with its channel, level, face and zoom chosen above it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.dds: DdsFile | None = None
        self.pixels: np.ndarray | None = None
        self.channel = QComboBox()
        self.channel.addItems(CHANNELS)
        self.mip = QSpinBox(prefix=tr("viewer.level"))
        self.face = QSpinBox(prefix=tr("viewer.face"))
        self.cross = QCheckBox(tr("viewer.cross"), toolTip=tr("viewer.tip.cubes_six_faces_once"))
        self.exposure = QDoubleSpinBox(
            minimum=-12.0,
            maximum=12.0,
            singleStep=0.5,
            prefix=tr("viewer.exposure"),
            suffix=" EV",  # i18n: ok (a unit)
        )
        self.zoom = QComboBox()
        for z in ZOOMS:
            self.zoom.addItem(tr(z) if z == ZOOMS[0] else z, z)
        self.info = QLabel()
        self.picture = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.scroll = QScrollArea(widgetResizable=True)
        self.scroll.setWidget(self.picture)
        bar = QHBoxLayout()
        for w in (
            QLabel(tr("viewer.channel")),
            self.channel,
            self.mip,
            self.face,
            self.cross,
            self.exposure,
            QLabel(tr("viewer.zoom")),
            self.zoom,
        ):
            bar.addWidget(w)
        bar.addWidget(self.info, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.scroll, 1)
        for box in (self.channel, self.zoom):
            box.currentIndexChanged.connect(lambda _i: self._draw())
        for spin in (self.mip, self.face, self.exposure):
            spin.valueChanged.connect(lambda _v: self._decode())
        self.cross.toggled.connect(lambda _on: self._decode())

    def show_texture(self, path: str, data: bytes) -> None:
        """Show a texture file; the level and face start at the top."""
        self.dds, self.pixels = None, None
        if data[:4] == b"DDS ":
            try:
                self.dds = DdsFile(data)
            except UnsupportedDds as exc:
                self.info.setText(tr("viewer.path_error", path=path, error=exc))
                self.picture.clear()
                return
            for spin, count in ((self.mip, self.dds.info.mips), (self.face, self.dds.info.faces)):
                spin.blockSignals(True)
                spin.setRange(0, count - 1)
                spin.setValue(0)
                spin.setEnabled(count > 1)
                spin.blockSignals(False)
            self.cross.setVisible(self.dds.info.faces % 6 == 0)
            self.exposure.setVisible(self.dds.hdr)
        else:
            from PIL import Image

            self.pixels = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
            self.mip.setEnabled(False)
            self.face.setEnabled(False)
            self.cross.setVisible(False)
            self.exposure.setVisible(False)
        self._decode()

    def _decode(self) -> None:
        if self.dds is not None:
            try:
                self.pixels = self.texels(self.mip.value(), self.face.value())
            except (UnsupportedDds, IndexError) as exc:  # a level cut short, a face that is not there
                self.info.setText(str(exc))
                self.picture.clear()
                return
        self._draw()

    def texels(self, mip: int, face: int) -> np.ndarray:
        """The level shown: one face or, with the cross, the six faces of the cube ``face`` is in; an HDR texture at
        its exposure."""
        if self.dds is None:
            raise IndexError("no texture")
        faces = (
            range(face - face % 6, face - face % 6 + 6)
            if self.cross.isChecked() and not self.cross.isHidden()
            else (face,)
        )
        if self.dds.hdr and not self.exposure.isHidden():
            pictures = [exposed_srgb8(self.dds.level_float(mip, f), self.exposure.value()) for f in faces]
        else:
            pictures = [self.dds.level(mip, f) for f in faces]
        return cube_cross(pictures) if len(pictures) == 6 else pictures[0]

    def _draw(self) -> None:
        if self.pixels is None:
            return
        rgb = np.ascontiguousarray(channel_image(self.pixels, self.channel.currentText()))
        h, w = rgb.shape[:2]
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(image)
        zoom = self.zoom.currentData()
        if zoom == ZOOMS[0]:
            area = self.scroll.viewport().size()
            pix = pix.scaled(area, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        elif zoom != "100%":
            k = int(zoom[:-1]) / 100
            mode = Qt.TransformationMode.FastTransformation if k > 1 else Qt.TransformationMode.SmoothTransformation
            pix = pix.scaled(int(w * k), int(h * k), Qt.AspectRatioMode.KeepAspectRatio, mode)
        self.picture.setPixmap(pix)
        self.info.setText(f"{w} x {h}")  # i18n: ok (a size)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.zoom.currentData() == ZOOMS[0]:
            self._draw()
