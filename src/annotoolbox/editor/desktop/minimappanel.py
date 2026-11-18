"""The Minimap dock: the island as a strategy minimap (``editor.minimap``), repainted a moment after the heights
change, off the window's thread when given a ``suite.background.Background`` (a continental island's took 2 s).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy

from ...infra.i18n import tr
from .. import minimap

SEA = (24, 52, 78)  # what shows through the clear pixels
MAX_PIXELS = 512
DELAY_MS = 150  # a brush stroke changes the heights many times a second: paint once it pauses


class MinimapPanel(QLabel):
    """A label that shows the minimap of ``session`` at the size of the dock, square; ``say`` is told when a paint
    failed (the status line), the last picture staying."""

    def __init__(self, session, background=None, say: Callable[[str], None] | None = None) -> None:
        super().__init__(alignment=Qt.AlignmentFlag.AlignCenter)
        self.session, self.background, self.say = session, background, say
        self._painting = self._again = False  # a paint runs off the window's thread; another is due after it
        self.setMinimumSize(QSize(96, 96))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setToolTip(tr("islands.tip.land_above_sea_level_coast"))
        self.picture: np.ndarray | None = None  # the last minimap, RGBA over the sea colour
        self._timer = QTimer(self, singleShot=True, interval=DELAY_MS)
        self._timer.timeout.connect(self.repaint_map)
        session.listeners.append(self._changed)
        self.destroyed.connect(lambda _=None, s=session, f=self._changed: f in s.listeners and s.listeners.remove(f))
        self.repaint_map()

    def _changed(self, change) -> None:
        if change.heights is not None:
            self._timer.start()

    def repaint_map(self) -> None:
        """Paint the minimap from the session's current heights (off the window's thread with a background; one
        paint at a time, the last change painted after the one running)."""
        s = self.session
        heights, pixels = s.heights.copy(), min(MAX_PIXELS, max(16, s.size * 2))
        if self.background is None:
            try:
                self._painted(_picture(heights, pixels))
            except Exception as exc:  # as a background's work hands it over: said, the island still opens
                self._painted(exc)
            return
        if self._painting:
            self._again = True
            return
        self._painting = True
        self.background.run(lambda: _picture(heights, pixels), self._painted)

    def _painted(self, picture) -> None:
        if not shiboken6.isValid(self):  # the island was closed while it painted
            return
        self._painting = False
        if isinstance(picture, np.ndarray):
            self.picture = picture
            self._show()
        elif self.say is not None:
            self.say(tr("islands.minimap_not_painted", error=f"{type(picture).__name__}: {picture}"))
        if self._again:
            self._again = False
            self.repaint_map()

    def _show(self) -> None:
        if self.picture is None:
            return
        h, w = self.picture.shape[:2]
        image = QImage(self.picture.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        side = max(1, min(self.width(), self.height()))
        self.setPixmap(QPixmap.fromImage(image).scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show()


def _picture(heights: np.ndarray, pixels: int) -> np.ndarray:
    """The minimap of ``heights`` at ``pixels`` a side over the sea colour, RGB."""
    rgba = minimap.paint(heights, pixels)
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba[..., :3] * alpha + np.array(SEA, np.float32) * (1.0 - alpha)
    return np.ascontiguousarray(np.clip(rgb, 0, 255).astype(np.uint8))
