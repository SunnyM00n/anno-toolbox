"""The 3D scene behind the UI preview: a still of the world the effects that read the scene (the background blur, the
world darken) draw from. It is a picture of the scene as the display shows it, the size of the preview's reference
frame; ``stand_in()`` makes one (sky, sea, an island with a coast), ``load(path)`` takes a picture file (a saved
screenshot or a picture the viewer or the island editor saved), ``from_image`` a picture the renderer drew
(``worldrender``).

``Scene.sample(x, y, frame, blurred)`` reads the scene at points of the reference space, in linear light; ``worldfx``
turns that into the pictures of the effects. docs/ui/117/world-effects.md says how the game does it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..models import mipchain

SIZE = (1920, 1080)  # the reference frame: the game's UI is 1920 x 1080 before it is stretched to the window
BLUR_RADIUS = 2  # the shader's 5 x 5 taps, one pixel apart
_numbers = itertools.count(1)


@dataclass(eq=False)
class Scene:
    """A scene still: ``srgb`` is (height, width, 3) uint8 as the display shows it; ``key`` names it for caches."""

    key: str
    srgb: np.ndarray
    _linear: np.ndarray | None = field(default=None, repr=False)
    _blurred: np.ndarray | None = field(default=None, repr=False)
    uid: int = field(default_factory=_numbers.__next__, init=False)  # tells two scenes of one name apart in caches

    @property
    def linear(self) -> np.ndarray:
        if self._linear is None:
            self._linear = mipchain.srgb_to_linear(self.srgb).astype(np.float32)
        return self._linear

    @property
    def blurred(self) -> np.ndarray:
        """The scene through the shader's 5 x 5 box (every tap one pixel from the next, the edge repeated)."""
        if self._blurred is None:
            self._blurred = box_blur(self.linear, BLUR_RADIUS)
        return self._blurred

    def sample(self, x: np.ndarray, y: np.ndarray, frame: tuple[float, float], blurred: bool = False) -> np.ndarray:
        """The scene's linear colour at points (x, y) of the reference space of a frame of size ``frame``: the scene
        stretched over the frame, the nearest pixel. The result has the shape of ``x`` plus the three channels."""
        h, w = self.srgb.shape[:2]
        px = np.clip((x / max(frame[0], 1.0) * w).astype(np.int32), 0, w - 1)
        py = np.clip((y / max(frame[1], 1.0) * h).astype(np.int32), 0, h - 1)
        return (self.blurred if blurred else self.linear)[py, px]


def box_blur(img: np.ndarray, radius: int) -> np.ndarray:
    """The mean of the (2 r + 1) squared pixels round each pixel of an (h, w, c) array, the edge clamped."""
    n = 2 * radius + 1
    out = img.astype(np.float64)
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (radius + 1, radius)
        total = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
        length = out.shape[axis]
        out = (np.take(total, range(n, n + length), axis=axis) - np.take(total, range(length), axis=axis)) / n
    return out.astype(np.float32)


def stand_in(size: tuple[int, int] = SIZE) -> Scene:
    """A made-up world to look at the effects over: sky and haze, a sea with swell, an island with a beach, fields,
    a few roofs and a road, so a blur and a darken show on edges, colour and fine detail alike."""
    w, h = size
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = x / w, y / h
    horizon = 0.38
    sky = np.stack([0.30 + 0.45 * v / horizon, 0.50 + 0.35 * v / horizon, 0.85 + 0.1 * v / horizon], -1)
    swell = 0.5 + 0.5 * np.sin(u * 140 + np.sin(v * 60) * 3) * np.sin(v * 220)
    depth = np.clip((v - horizon) / (1 - horizon), 0, 1)[..., None]
    sea = np.array((0.10, 0.36, 0.52)) * (1 - depth) + np.array((0.03, 0.18, 0.30)) * depth
    sea = sea * (0.85 + 0.15 * swell[..., None])
    img = np.where((v < horizon)[..., None], sky, sea)
    # the island: a rounded blob; its distance field gives the beach, the land and the hills
    cx, cy = 0.5, 0.66
    d = np.hypot((u - cx) / 0.30, (v - cy) / 0.20 + 0.18 * np.sin(u * 9)) - 1.0
    inland = np.clip(-d, 0, 1)[..., None]
    land = np.array((0.30, 0.52, 0.20)) * (1 - inland) + np.array((0.48, 0.42, 0.26)) * inland
    fields = 0.5 + 0.5 * np.sign(np.sin(u * 90) * np.sin(v * 70))
    land = land * (0.9 + 0.1 * fields[..., None])
    beach = ((d > -0.06) & (d < 0.0))[..., None]
    land = np.where(beach, np.array((0.82, 0.74, 0.52)), land)
    foam = ((d >= 0.0) & (d < 0.025))[..., None]
    img = np.where((d < 0.0)[..., None], land, np.where(foam, np.array((0.85, 0.92, 0.95)), img))
    for rx, ry in ((0.46, 0.64), (0.50, 0.67), (0.55, 0.65), (0.52, 0.62), (0.44, 0.69)):  # the roofs
        inside = (np.abs(u - rx) < 0.012) & (np.abs(v - ry) < 0.016)
        img = np.where(inside[..., None], np.array((0.72, 0.28, 0.18)), img)
    road = (np.abs(v - 0.66 - 0.05 * np.sin(u * 12)) < 0.003) & (d < -0.05)
    img = np.where(road[..., None], np.array((0.86, 0.80, 0.66)), img)
    return Scene("stand-in", mipchain.linear_to_srgb8(np.clip(img, 0, 1)))


def load(path: str | Path, size: tuple[int, int] = SIZE) -> Scene:
    """A scene from a picture file (anything Qt reads), stretched to ``size``; ``ValueError`` when it does not read."""
    from PySide6.QtGui import QImage

    img = QImage(str(path))
    if img.isNull():
        raise ValueError(f"not a picture: {path}")
    return from_image(img, f"file:{Path(path).name}", size)


def from_image(img, key: str, size: tuple[int, int] = SIZE) -> Scene:
    """A scene from a ``QImage`` (a picture file's, the renderer's), stretched to ``size``."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    if img.isNull():
        raise ValueError(f"no picture: {key}")
    img = img.scaled(size[0], size[1], Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    rows = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    # a copy: the rows are the picture's memory, freed with it (a view of them would outlive it)
    return Scene(key, rows[:, : img.width() * 3].reshape(img.height(), -1, 3).copy())


def to_qimage(scene: Scene):
    """The scene as a ``QImage`` (what the preview paints behind the UI)."""
    from PySide6.QtGui import QImage

    h, w = scene.srgb.shape[:2]
    return QImage(np.ascontiguousarray(scene.srgb).tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
