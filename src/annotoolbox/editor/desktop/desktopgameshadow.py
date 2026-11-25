"""The game look's sun shadow on the CPU's side (a mixin of ``desktopgame.CGameIsland``): the cascades' analysis, the
cascades as last read back for the casters the CPU culls, and the switch that culls the native objects' casters on
the GPU instead (``render.shadowcullnative``); docs/rendering/performance.md section 4, item 4.
"""

from __future__ import annotations

import numpy as np

from ...infra.i18n import tr
from ...render import gameshadow


class CGameShadow:
    """``CGameIsland``'s shadow: ``_caster_cascades``, ``_seen_cascades``, ``_fit`` and ``_cull_on_gpu``."""

    def _caster_cascades(self, frame, size_px: tuple[int, int], options, shadows: bool):
        """The cascades the CPU culls the objects' casters by (``_seen_cascades``); None without shadows or when the
        GPU culls the native casters by this frame's own (nothing read back, no frames drawn to wait for them)."""
        if shadows and not self._cull_on_gpu(options):
            return self._seen_cascades(frame, size_px)
        self._m_late = False
        return None

    def _cull_on_gpu(self, options) -> bool:
        """Whether this frame culls the native objects' casters on the GPU: the switch on (``SViewOptions
        .gpu_culling``) and the culling's shaders bound; a culling that failed is said once and the CPU culls again."""
        objects = self.m_native_objects
        if objects is None or objects.m_error:
            return False
        from ...render import shadowcullnative

        cull = objects.m_cull
        if cull is not None and cull.m_error:
            self.m_renderer._message(tr("islands.games_native_objects", reason=f"culling: {cull.m_error}"[:300]))
            self._m_cull_failed = True
        wanted = getattr(options, "gpu_culling", True) and not self._m_cull_failed
        if cull is not None and (cull.m_error or not wanted):
            cull.release()
            objects.m_cull = cull = None
        if wanted and cull is None:
            try:
                objects.m_cull = shadowcullnative.CShadowCull(self._m_device, self._m_natives)
            except Exception as exc:  # pattern: ok (a boundary: the shaders do not bind; the CPU culls, said once)
                self.m_renderer._message(tr("islands.games_native_objects", reason=f"culling: {exc}"[:300]))
                self._m_cull_failed = True
        return objects.m_cull is not None

    def _seen_cascades(self, frame, size_px: tuple[int, int]):
        """The sun's cascades as last known on the CPU (the casters are culled by them): fitted there, or read back
        from the game's analysis a frame or two ago (one read in flight at a time); None before the first.
        ``_late``: they were fitted for another camera than this frame's (the view draws on until they are not)."""
        fit = self._fit()
        self._m_late = False
        if fit is None:
            cascades = self.m_frame.m_cascades if self.m_frame is not None else None
            return cascades if cascades is not None and not cascades.gpu else None
        seen = (np.asarray(frame.view_proj).tobytes(), tuple(size_px))
        if fit.m_readbacks and fit.m_readbacks[1].data.size() >= gameshadow.INFO_BYTES * gameshadow.COUNT:
            self._m_cascades = gameshadow.read_info(bytes(fit.m_readbacks[1].data), np.zeros(3), 0.0)
            self._m_cascades_for, fit.m_readbacks = self._m_asked_for, []
        if not fit.m_readbacks:
            fit.m_read_back, self._m_asked_for = True, seen
        self._m_late = self._m_cascades_for != seen
        return self._m_cascades

    def _fit(self):
        """The cascades' analysis: the native one with the native shading, else the current path's (None: none)."""
        native = self.m_native_shading
        if native is not None and not native.m_error:
            return native.m_fit
        return self.m_fit if self.m_fit is not None and not self.m_fit.m_error else None
