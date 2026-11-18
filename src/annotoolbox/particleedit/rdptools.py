"""The changes the editor makes to a baked effect's tick data (``.rdp``) as a whole: a tint of every tick's colour,
the blank first and last tick of each particle, and what the data holds. No Qt.

``tint(doc, rgba)`` and ``fade(doc)`` return a ``cfgdoc.Edit`` of the ``T_Colors`` blob (stored blue, green, red,
alpha: engine-particles "Particles"); ``facts(doc)`` is the line the tree shows. docs/particle-editor.md, "117, 1800
and 2205".
"""

from __future__ import annotations

import math

import numpy as np

from ..infra.i18n import tr
from ..models.cfgdoc import Doc, Edit
from .curves import finite


def _blob(doc: Doc, name: str):
    return doc.root.find(name)


def _put(node, data: bytes, label: str) -> Edit:
    old = node.data

    def put(value: bytes):
        return lambda: setattr(node, "data", value)

    return Edit(label, put(data), put(old))


def tint(doc: Doc, rgba: tuple[float, float, float, float]) -> Edit | None:
    """Every tick's colour and alpha times ``rgba`` (0..1 each, up to 2 to brighten); None without colour data."""
    node = _blob(doc, "T_Colors")
    if node is None or not node.data or not all(math.isfinite(c) and 0.0 <= c <= 2.0 for c in rgba):
        return None
    ticks = np.frombuffer(node.data[: len(node.data) // 4 * 4], np.uint8).reshape(-1, 4).astype(np.float32)
    factor = np.array([rgba[2], rgba[1], rgba[0], rgba[3]], np.float32)  # the file's order: blue, green, red, alpha
    out = np.clip(np.rint(ticks * factor), 0, 255).astype(np.uint8).tobytes() + node.data[len(ticks) * 4 :]
    shown = " ".join(f"{c:.2f}" for c in rgba)
    return _put(node, out, tr("particles.tint_rgba", rgba=shown))


def fade(doc: Doc) -> Edit | None:
    """Alpha 0 on the first and last tick of every particle (the game blends a last tick into an empty one, so a
    particle that ends visible flashes); None when the data does not hold together."""
    colors, starts, ends = (_blob(doc, n) for n in ("T_Colors", "P_Starts", "P_Ends"))
    frames = doc.root.find("FrameCount")
    if None in (colors, starts, ends, frames) or not (colors.data and starts.data and ends.data):
        return None
    count = int(finite(frames.text))
    if len(starts.data) % 2 or len(starts.data) != len(ends.data) or len(colors.data) % 4:
        return None
    first = np.frombuffer(starts.data, "<i2").astype(np.int64)
    last = np.frombuffer(ends.data, "<i2").astype(np.int64)
    lives = np.where(last >= first, last - first + 1, last - first + count + 1)
    if lives.min(initial=1) < 1 or int(lives.sum()) * 4 != len(colors.data):
        return None
    at = np.concatenate([[0], np.cumsum(lives)[:-1]]).astype(np.int64)
    data = np.frombuffer(colors.data, np.uint8).copy()
    data[np.concatenate([at, at + lives - 1]) * 4 + 3] = 0
    return _put(colors, data.tobytes(), tr("particles.fade_out"))


def facts(doc: Doc) -> str:
    """Particles, ticks and the blobs' sizes."""
    count = doc.root.find("ParticleCount")
    ticks = doc.root.find("TotalTickCount")
    return tr(
        "particles.particles_ticks",
        particles=count.text if count is not None else "?",
        ticks=ticks.text if ticks is not None else "?",
    )
