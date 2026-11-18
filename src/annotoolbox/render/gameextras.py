"""The parts of 117's frame by the game's shaders beyond the G-buffer and its light, as the island view hands them to
its frame: the water's scene copies and the ocean's waves, the visible sky, haze and clouds, the post chain with the
eye adaptation; each compiled once in the background, made when in, falling back alone (said once).

``FrameExtras(rhi, source, get, say)`` is the entry; ``passes(...)`` fills a ``gameframe.Passes``, ``finish(...)``
grades and tone-maps the view's resolved colour. docs/rendering/island-117-view.md "Phases 6 to 8" (the viewer's own
copy of the steps: ``viewer.gamepass``).
"""

from __future__ import annotations

import time
from collections.abc import Callable

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


class FrameExtras:
    """The frame's extra parts of one view (module docstring)."""

    def __init__(self, rhi, source: Callable, get: Callable, say: Callable[[str], None]) -> None:
        self.rhi, self.source, self.get, self.say = rhi, source, get, say
        self.made: dict[str, object] = {}  # key -> the part, or False once it failed (said)
        self._last_frame: float | None = None  # the eye adaptation's clock

    def _part(self, key: str, compile_: Callable, make: Callable, said: str):
        """The part ``key``: made from ``compile_(source)`` once that is in (None while it compiles); on an error
        ``said`` is said once with the reason and None comes back for good."""
        got = self.made.get(key)
        if got is not None:
            return got or None
        source = self.source()
        if source is None:
            return None
        result = self.get(JOB.format(key), lambda: compile_(source))
        if result is None:
            return None
        if isinstance(result, Exception):
            self.say(tr(said, reason=f"{type(result).__name__}: {result}"[:300]))
            self.made[key] = False
            return None
        try:
            part = make(result)
        except Exception as exc:  # the device refused it: said once, not raised again in every frame
            self.say(tr(said, reason=f"{type(exc).__name__}: {exc}"[:300]))
            self.made[key] = False
            return None
        self.made[key] = part
        if getattr(part, "error", ""):
            self.say(tr(said, reason=part.error[:300]))
        return part

    def _ok(self, part):
        return part if part is not None and not part.error else None

    def water(self):
        """(the scene's copies the water parts read, the ocean's simulation or None: a flat sea); None while the
        copies compile or when they do not."""
        water = self._ok(self._part(
            "water", lambda s: (s.stage_set(gamewater.STAGES), s.float_maps(gamewater.MAPS)),
            lambda got: gamewater.SceneCopies(self.rhi, *got), N_WATER,
        ))  # fmt: skip
        if water is None:
            return None
        ocean = self._ok(self._part(
            "ocean", lambda s: (s.stage_set(gameocean.STAGES), gameocean.read_water(s.ambient_text())),
            lambda got: gameocean.OceanSimulation(self.rhi, *got), N_OCEAN,
        ))  # fmt: skip
        return water, ocean

    def sea(self):
        """The sea by the game's ocean shader with its coast waves (``gamesea.Sea``, its island not set yet); None
        while it compiles or when it does not."""
        return self._ok(self._part("sea", gamesea.compile_stages, lambda got: gamesea.Sea(self.rhi, *got), N_SEA))

    def inland(self, egypt: bool = False):
        """The rivers and lakes by the game's inland water shader (``gameinland.InlandWater``, Egypt's variant with
        ``egypt``, its bodies not set yet); None while it compiles or when it does not."""
        return self._ok(self._part("inland", lambda s: gameinland.compile_stages(s, egypt),
                                   lambda got: gameinland.InlandWater(self.rhi, *got), N_INLAND))  # fmt: skip

    def passes(self, passes, sky, seconds: float, ctx, transparent: bool) -> None:
        """The water's copies (when a transparent part or the sea draws), the visible sky, haze and clouds (when the
        game's sky passes light the frame: ``sky``) into ``passes``."""
        if transparent or passes.sea is not None:
            got = self.water()
            if got is not None:
                water, ocean = got
                if ocean is not None:
                    ocean.run(ctx, seconds)
                    water.waves, water.constants = ocean.inputs(), dict(gameocean.CONSTANTS)
                else:
                    water.waves, water.constants = {}, dict(gamewater.CONSTANTS)
                passes.water = water
        if sky is None:
            return
        atmosphere = self._ok(self._part(
            "atmosphere", lambda s: (s.stage_set(gameatmosphere.STAGES), s.sky_passes()[1]),
            lambda got: gameatmosphere.Atmosphere(self.rhi, *got), N_ATMOSPHERE,
        ))  # fmt: skip
        if atmosphere is None:
            return
        passes.atmosphere, passes.look = atmosphere, gameatmosphere.Look()
        clouds = self._ok(self._part(
            "clouds", lambda s: (s.stage_set(gameclouds.STAGES), gameclouds.read_maps(s.files, s.ambient_text())),
            lambda got: gameclouds.Clouds(self.rhi, *got), N_CLOUDS,
        ))  # fmt: skip
        if clouds is not None:
            clouds.seconds = seconds  # the weather and the noise drift with the wind over the view's time
            passes.clouds = clouds

    def far(self) -> float:
        """The far plane the clouds' rays reach (0: none drawn, the view's own)."""
        clouds = self._ok(self.made.get("clouds") or None)
        return clouds.maps.far if clouds is not None else 0.0

    def finish(self, ctx, image, size: tuple[int, int], lut, noise):
        """The game's grade and tone map of ``image`` (the view's resolved HDR colour; ``lut`` the ambient's grade
        cube, ``noise`` the shading's blue noise, either None), with the eye adaptation's exposure: the tone-mapped
        texture, or None while they compile or when they do not (the view's own finish then)."""
        post = self._ok(self._part("post", lambda s: s.post_stages(), lambda got: gamepost.GamePost(self.rhi, got),
                                   N_POST))  # fmt: skip
        if post is None:
            return None
        inputs = {k: v for k, v in (("cBlueNoiseTex", noise), ("cLUTTexture0", lut)) if v is not None}
        return post.render(ctx, image, size, inputs, self._exposure(ctx, image, size))

    def _exposure(self, ctx, image, size: tuple[int, int]):
        """The eye adaptation's exposure over ``image`` (frame-117-viewer.md 6), eased between frames that follow
        closely; None while it compiles or when it does not (the tone map without it)."""
        eye = self._ok(self._part("eye", lambda s: s.stage_set(eyeadapt.STAGES),
                                  lambda got: eyeadapt.EyeAdaptation(self.rhi, got), N_EYE))  # fmt: skip
        if eye is None:
            return None
        now = time.monotonic()
        step = None if self._last_frame is None else now - self._last_frame
        if step is None or step > eyeadapt.SETTLE:
            eye.restart()
        self._last_frame = now
        eye.run(ctx, image, size, step)
        return eye.exposure

    def release(self) -> None:
        for part in self.made.values():
            if part:
                part.release()
        self.made, self._last_frame = {}, None
