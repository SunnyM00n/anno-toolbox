"""The game look's opaque frame kept while nothing in it changes: a frame then restores the kept depth and lit colour
and draws only what moves over them (water, haze, clouds, effects, post). Why: docs/rendering/performance.md section 4,
item 3. ``CStill.decide`` takes a key of everything the opaque part reads: the key's first frame is drawn in full, the
second is drawn in full and kept (``save_depth`` after the G-buffer, ``save_lit`` after the shading), later ones
``restore`` and skip the G-buffer, the casters and the shading, until ``KEEP_SECONDS`` are over. ``light_key``: the sun."""

from __future__ import annotations

import numpy as np

KEEP_SECONDS = 1.0  # a kept frame is drawn anew after this long: the clouds' shadow moves on it [ours]
CLOCK_KEYS = frozenset({"uwTime", "uwCaustics"})  # the light's values that move with the view's clock only


def light_key(lighting: dict) -> tuple:
    """The opaque frame's light: every value of the view's light but the water's clock, as bytes."""
    out = []
    for name in sorted(lighting):
        if name in CLOCK_KEYS:
            continue
        value = lighting[name]
        try:
            out.append((name, np.asarray(value, float).tobytes()))
        except (TypeError, ValueError):
            out.append((name, repr(value)))
    return tuple(out)


class CStill:
    """The kept depth and lit colour of one frame (module docstring)."""

    def __init__(self) -> None:
        self.m_key: tuple | None = None
        self.m_ready = False  # the depth and the lit colour held are those of ``m_key``'s frame
        self.m_taken_at = 0.0
        self.m_lit: list = []
        self.m_depth = None
        self.m_size_px: tuple[int, int] | None = None
        self.m_reused = self.m_drawn = 0  # frames reused and frames drawn in full since the start (the bench's)

    def decide(self, key: tuple | None, now: float) -> bool | None:
        """None: this frame is not kept or reused (``key`` None, or the first frame of its key); False: draw in full
        and keep it; True: restore the kept frame instead of drawing the opaque part."""
        if key is None or key != self.m_key:
            self.m_key, self.m_ready = key, False
            return None
        if self.m_ready and now - self.m_taken_at < KEEP_SECONDS:
            self.m_reused += 1
            return True
        self.m_ready, self.m_taken_at = True, now
        return False

    def forget(self) -> None:
        """What was kept is no longer the frame (the next frame is drawn in full)."""
        self.m_key, self.m_ready = None, False

    def _textures(self, frame) -> None:
        """The kept textures, made for ``frame``'s size (again when it changed)."""
        if self.m_depth is not None and self.m_size_px == frame.m_size_px:
            return
        self.release()
        ctx, size_px = frame.m_ctx, frame.m_size_px
        self.m_lit = [ctx.native_texture(size_px, texture.m_qformat) for texture in frame.m_lit]
        self.m_depth = frame._depth_stencil(size_px) if frame.m_has_stencil else ctx.depth_renderbuffer(size_px)
        self.m_size_px = size_px

    def save_depth(self, frame) -> None:
        """The G-buffer's depth (and stencil) kept, after the pass that drew it."""
        self._textures(frame)
        frame.m_ctx.copy_texture(self.m_depth, frame.m_depth)
        self.m_drawn += 1

    def save_lit(self, frame) -> None:
        """The deferred shading's colour kept, before the water and the haze are drawn over it."""
        for kept, lit in zip(self.m_lit, frame.m_lit, strict=True):
            frame.m_ctx.copy_texture(kept, lit)

    def restore(self, frame) -> None:
        """The kept depth and lit colour back into the frame's, in place of the passes that made them."""
        frame.m_ctx.copy_texture(frame.m_depth, self.m_depth)
        for kept, lit in zip(self.m_lit, frame.m_lit, strict=True):
            frame.m_ctx.copy_texture(lit, kept)

    def release(self) -> None:
        for texture in (*self.m_lit, self.m_depth):
            if texture is not None:
                texture.release()
        self.m_lit, self.m_depth, self.m_size_px = [], None, None
        self.forget()
