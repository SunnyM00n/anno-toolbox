"""The end of the game look's frame (a mixin of ``desktopgame.CGameIsland``): the native parts' frame ends and the
stats of which ways drew, the water copies' and the rivers' fallback to the current path when one failed."""

from __future__ import annotations

import time

from ...infra.i18n import tr


class CGameEnd:
    """``CGameIsland``'s frame end."""

    def _end_native(self, native) -> None:
        """The frame's end for the native parts (``native``: the terrain's, None when the current path drew it):
        the device's frame ended once for all of them (their late releases count in it), which ways drew."""
        if native is not None:
            native.end_frame()
        if self.m_native_objects is not None:
            self.m_native_objects.end_frame()
        water = self.m_native_water
        self.m_renderer.m_stats["water_native"] = water.m_drawn if water is not None else 0
        if water is not None:
            water.end_frame()
        if self.m_native_shading is not None:
            self.m_native_shading.end_frame()
        if self._m_device is not None:
            self._m_device.end_frame()
            from ...render import nativegpu  # the native backend is Windows only

            nativegpu.keep_pipelines(self._m_device, time.monotonic())  # what this start made, for the next
        self.m_renderer.m_stats["terrain_native"] = int(native is not None)
        self.m_renderer.m_stats["pages_native"] = int(getattr(self.m_pages, "m_native", False))
        self.m_renderer.m_stats["bake_native"] = int(getattr(self.m_bake, "m_native", False))
        self.m_renderer.m_stats["shading_native"] = int(
            self.m_native_shading is not None and not self.m_native_shading.m_error
        )
        post = self.m_native_post
        if post is not None:
            post.end_frame()
        self.m_renderer.m_stats["post_native"] = int(post is not None and not post.m_error)
        self.m_renderer.m_stats["ocean_native"] = int(
            self.m_native_ocean is not None and not self.m_native_ocean.m_error
        )
        self._end_copies()

    def _end_copies(self) -> None:
        """The native depth copy's frame end; once it failed (said once), the current path's pass from then on."""
        copies = self.m_native_copies
        self.m_renderer.m_stats["copies_native"] = int(copies is not None and not copies.m_error)
        if copies is not None and copies.m_error:
            self.m_renderer._message(tr("islands.games_native_water", reason=f"copy: {copies.m_error}"[:300]))
            copies.release()
            self.m_native_copies = self.m_extras.m_native_copies = None
        elif copies is not None:
            copies.end_frame()
        inland = self.m_native_inland
        self.m_renderer.m_stats["inland_native"] = int(inland is not None and not inland.m_error)
        if inland is not None and inland.m_error:  # the rivers' passes by the current path from now on, said once
            self.m_renderer._message(tr("islands.games_native_water", reason=f"rivers: {inland.m_error}"[:300]))
            inland.release()
            self.m_native_inland = None
        elif inland is not None:
            inland.end_frame()
