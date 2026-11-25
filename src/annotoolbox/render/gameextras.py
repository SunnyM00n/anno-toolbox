"""The parts of 117's frame by the game's shaders beyond the G-buffer and its light, as the island view hands them to
its frame: the water's scene copies and the ocean's waves, the visible sky, haze and clouds, the post chain with the
eye adaptation; each compiled once in the background, made when in, falling back alone (said once).

``CFrameExtras(rhi, source, get, say)`` is the entry; ``passes(...)`` fills a ``gameframe.SPasses``, ``finish(...)``
grades and tone-maps the view's resolved colour. docs/rendering/island-117-view.md "Phases 6 to 8" (the viewer's own
copy of the steps: ``viewer.gamepass``).
"""

from __future__ import annotations

import struct
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..infra.i18n import N_, tr
from . import eyeadapt, gameatmosphere, gameclouds, gameinland, gameocean, gamepost, gamesea, gamewater

JOB = "game-frame-extra:{}"
# what each part says when it does not compile
N_WATER = N_("islands.games_water_copies")
N_OCEAN = N_("islands.games_ocean_simulation")
N_ATMOSPHERE = N_("islands.games_visible_sky_haze")
N_CLOUDS = N_("islands.games_clouds_reason")
N_POST = N_("islands.games_post_chain")
N_EYE = N_("islands.games_eye_adaptation")
N_SEA = N_("islands.games_sea_reason")
N_INLAND = N_("common.games_rivers_lakes")
N_OCEAN_NATIVE = "the ocean's simulation"  # within ``islands.games_native_water``'s reason, as the others' are English


@dataclass(frozen=True)
class SSkyParts:
    """Which of the visible sky's parts the view's switches leave in: the sky and haze, the clouds on it, the clouds
    and fog accumulated over frames and their self-shadow cache kept between frames (both the native passes' alone,
    performance.md 4 items 5 and 6)."""

    has_sky: bool = True
    has_clouds: bool = True
    is_temporal: bool = False
    is_cache_kept: bool = False


class CFrameExtras:
    """The frame's extra parts of one view (module docstring)."""

    def __init__(self, rhi, source: Callable, get: Callable, say: Callable[[str], None]) -> None:
        self.m_rhi, self.m_source, self.m_get, self.m_say = rhi, source, get, say
        self.m_made: dict[str, object] = {}  # key -> the part, or False once it failed (said)
        self._m_last_frame: float | None = None  # the eye adaptation's clock
        self.m_clock: Callable[[], float] = time.monotonic  # its time (``steady``: a bench's fixed steps)
        self.m_native_ocean = None  # oceannative.CNativeOcean the island look made (its owner releases it)
        self.m_native_copies = None  # copiesnative.CNativeCopies, likewise
        self.m_clouds_drawn = False  # the last frame's cloud pass was made (``passes``)
        self._m_ocean_at: tuple | None = None  # (the ocean, the seconds) it was last stepped to

    def _part(self, key: str, compile_: Callable, make: Callable, said: str):
        """The part ``key``: made from ``compile_(source)`` once that is in (None while it compiles); on an error
        ``said`` is said once with the reason and None comes back for good."""
        got = self.m_made.get(key)
        if got is not None:
            return got or None
        source = self.m_source()
        if source is None:
            return None
        result = self.m_get(JOB.format(key), lambda: compile_(source))
        if result is None:
            return None
        if isinstance(result, Exception):
            self.m_say(tr(said, reason=f"{type(result).__name__}: {result}"[:300]))
            self.m_made[key] = False
            return None
        try:
            part = make(result)
        except Exception as exc:  # the device refused it: said once, not raised again in every frame
            self.m_say(tr(said, reason=f"{type(exc).__name__}: {exc}"[:300]))
            self.m_made[key] = False
            return None
        self.m_made[key] = part
        if getattr(part, "m_error", ""):
            self.m_say(tr(said, reason=part.m_error[:300]))
        return part

    def _ok(self, part):
        return part if part is not None and not part.m_error else None

    def water(self):
        """(the scene's copies the water parts read, the ocean's simulation or None: a flat sea); None while the
        copies compile or when they do not."""
        water = self._ok(self._part(
            "water", lambda s: (s.stage_set(gamewater.STAGES), s.float_maps(gamewater.MAPS)),
            lambda got: gamewater.CSceneCopies(self.m_rhi, *got), N_WATER,
        ))  # fmt: skip
        if water is None:
            return None
        copies = self.m_native_copies
        water.m_native = copies if copies is not None and not copies.m_error else None
        native = self.m_native_ocean
        if native is not None and not native.m_error:  # the shipped shaders' (``oceannative``): none compiled here
            return water, native
        ocean = self._ok(self._part(
            "ocean", lambda s: (s.stage_set(gameocean.STAGES), gameocean.read_water(s.ambient_text())),
            lambda got: gameocean.COceanSimulation(self.m_rhi, *got), N_OCEAN,
        ))  # fmt: skip
        return water, ocean

    def sea(self):
        """The sea by the game's ocean shader with its coast waves (``gamesea.CSea``, its island not set yet); None
        while it compiles or when it does not."""
        return self._ok(self._part("sea", gamesea.compile_stages, lambda got: gamesea.CSea(self.m_rhi, *got), N_SEA))

    def inland(self, egypt: bool = False):
        """The rivers and lakes by the game's inland water shader (``gameinland.CInlandWater``, Egypt's variant with
        ``egypt``, its bodies not set yet); None while it compiles or when it does not."""
        return self._ok(self._part("inland", lambda s: gameinland.compile_stages(s, egypt),
                                   lambda got: gameinland.CInlandWater(self.m_rhi, *got), N_INLAND))  # fmt: skip

    def passes(
        self,
        passes,
        sky,
        seconds: float,
        ctx,
        transparent: bool,
        parts: SSkyParts | None = None,
        *,
        held: bool = False,
    ) -> None:
        """The water's copies (when a transparent part or the sea draws), the visible sky, haze and clouds (when the
        game's sky passes light the frame: ``sky``) into ``passes``. ``parts`` without the sky or the clouds leaves the
        haze (and the clouds on it) or the clouds out: their passes are not made nor drawn. ``held``: the water motion
        is switched off, so the ocean is not stepped again at the seconds it stands at."""
        parts = parts or SSkyParts()
        self.m_clouds_drawn = False
        if transparent or passes.sea is not None:
            got = self.water()
            if got is not None:
                water, ocean = got
                if ocean is not None and self._ocean_is_current(ocean, seconds) and held:
                    pass  # a held clock: the spectrum is in its maps already
                elif ocean is not None and ocean is self.m_native_ocean:
                    ocean = self._run_native(ocean, ctx, seconds)
                elif ocean is not None:
                    ocean.run(ctx, seconds)
                if ocean is not None:
                    water.m_waves, water.m_constants = ocean.inputs(), dict(gameocean.CONSTANTS)
                else:
                    water.m_waves, water.m_constants = {}, dict(gamewater.CONSTANTS)
                passes.water = water
        if sky is None or not parts.has_sky:
            return
        atmosphere = self._ok(self._part(
            "atmosphere", lambda s: (s.stage_set(gameatmosphere.STAGES), s.sky_passes()[1]),
            lambda got: gameatmosphere.CAtmosphere(self.m_rhi, *got), N_ATMOSPHERE,
        ))  # fmt: skip
        if atmosphere is None:
            return
        passes.atmosphere, passes.look = atmosphere, gameatmosphere.SLook(temporal=parts.is_temporal)
        clouds = self._ok(self._part(
            "clouds", lambda s: (s.stage_set(gameclouds.STAGES), gameclouds.read_maps(s.m_files, s.ambient_text())),
            lambda got: gameclouds.CClouds(self.m_rhi, *got), N_CLOUDS,
        )) if parts.has_clouds else None  # fmt: skip
        if clouds is not None:
            clouds.m_seconds = seconds  # the weather and the noise drift with the wind over the view's time
            clouds.m_is_cache_kept = parts.is_cache_kept
            passes.clouds = clouds
            self.m_clouds_drawn = True

    def _ocean_is_current(self, ocean, seconds: float) -> bool:
        """Whether ``ocean`` was stepped to ``seconds`` last frame already (a held clock: its FFT passes are not
        recorded again, the maps keep the same spectrum); remembers this frame's step. Only for the switch: a frame
        recorded while the shaders compile may never run, so a picture at a set time steps each frame."""
        same = self._m_ocean_at is not None and self._m_ocean_at[0] is ocean and self._m_ocean_at[1] == seconds
        self._m_ocean_at = (ocean, seconds)
        return same

    def _run_native(self, ocean, ctx, seconds: float):
        """The native simulation's frame; on a failure said once, a flat sea this frame and the current path's
        simulation from the next."""
        try:
            ocean.run(ctx, seconds)
            return ocean
        except Exception as exc:
            ocean.m_error = f"{type(exc).__name__}: {exc}"[:300]
            self.m_say(tr("islands.games_native_water", reason=f"{N_OCEAN_NATIVE}: {ocean.m_error}"))
            return None

    def far(self) -> float:
        """The far plane the clouds' rays reach (0: none drawn, the view's own)."""
        clouds = self._ok(self.m_made.get("clouds") or None) if self.m_clouds_drawn else None
        return clouds.m_maps.far if clouds is not None else 0.0

    def finish(self, ctx, image, size_px: tuple[int, int], lut, noise):
        """The game's grade and tone map of ``image`` (the view's resolved HDR colour; ``lut`` the ambient's grade
        cube, ``noise`` the shading's blue noise, either None), with the eye adaptation's exposure: the tone-mapped
        texture, or None while they compile or when they do not (the view's own finish then)."""
        post = self._ok(self._part("post", lambda s: s.post_stages(), lambda got: gamepost.CGamePost(self.m_rhi, got),
                                   N_POST))  # fmt: skip
        if post is None:
            return None
        inputs = {k: value for k, value in (("cBlueNoiseTex", noise), ("cLUTTexture0", lut)) if value is not None}
        return post.render(ctx, image, size_px, inputs, self._exposure(ctx, image, size_px))

    def _exposure(self, ctx, image, size_px: tuple[int, int]):
        """The eye adaptation's exposure over ``image`` (frame-117-viewer.md 6), eased between frames that follow
        closely; None while it compiles or when it does not (the tone map without it)."""
        eye = self._ok(self._part("eye", lambda s: s.stage_set(eyeadapt.STAGES),
                                  lambda got: eyeadapt.CEyeAdaptation(self.m_rhi, got), N_EYE))  # fmt: skip
        if eye is None:
            return None
        now = self.m_clock()
        step = None if self._m_last_frame is None else now - self._m_last_frame
        if step is None or step > eyeadapt.SETTLE:
            eye.restart()
        self._m_last_frame = now
        eye.run(ctx, image, size_px, step)
        return eye.m_exposure

    def steady(self, step: float) -> None:
        """From the next frame on, the eye adaptation starts at its target and eases by ``step`` seconds a frame,
        whatever the frames cost: two ways of drawing grabbed after the same number of frames have the same exposure
        history (``scripts/native_compare.py``)."""
        frames = iter(range(1 << 62))
        self.m_clock = lambda: next(frames) * step
        self._m_last_frame = None

    def read_exposure(self) -> None:
        """The next frame reads its exposure back (``exposure``)."""
        eye = self._ok(self.m_made.get("eye") or None)
        if eye is not None:
            eye.m_read_back = True

    def exposure(self) -> float | None:
        """The exposure read back by the frame ``read_exposure`` asked (None: not asked, not in yet, no eye)."""
        eye = self._ok(self.m_made.get("eye") or None)
        data = bytes(eye.m_readback.data) if eye is not None and eye.m_readback is not None else b""
        return struct.unpack_from("<f", data)[0] if len(data) >= 4 else None

    def release(self) -> None:
        for part in self.m_made.values():
            if part:
                part.release()
        self.m_made, self._m_last_frame = {}, None
