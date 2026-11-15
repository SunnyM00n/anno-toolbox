"""The sea of an Anno 2070 island by the game's own water shaders in the island view (Direct3D 12 or 11), over the
view's own terrain and models: the island's water info map built as the game builds it, the coast waves, the view's
scene mirrored into the planar reflection, the island's own ambient set; the view's own sea while they compile, where
they fail and on OpenGL.

``GameSea2070(renderer)`` is the entry, one per ``desktoprender.Renderer``, on ``desktopsea.IslandSea``'s seam;
docs/island-editor-view.md "2070's sea by the game's shaders".
"""

from __future__ import annotations

import numpy as np

from ...render import coast2070, frame2070, ocean2070, sky2070
from ...render import gameshader as gs
from ...render.frameforward import DEPTH_COPY_FS
from ...render.fxcdraw import Dummies
from .desktopsea import Files, IslandSea, by_folder, sun_direction, upload

JOB = "game-sea-2070"
INFO_JOB = "game-sea-2070-info"
# the area an island lies in when its map names none, above and under water (the world's ambient areas); the variant
# follows the island's ecology in game: the first, its best, stands in
AREAS = ("NorthIsland", "Underwater_Island_North")
VARIANT = 0
MARGIN = 256.0  # how far the sea's chunks reach past the island's edge [ours: the engine's go to the frustum's end]
WARM = 20.0  # seconds of coast waves run before the first picture, so that it shows them settled [ours]

# the view's linear light into the game's lit scene, stored halved (``LOW_QUALITY_HDR_ENABLED``)
HALF_COPY_FS = """
#version 330
uniform sampler2D gLit;
in vec2 vNdc; out vec4 color;
void main() { color = vec4(texelFetch(gLit, ivec2(gl_FragCoord.xy), 0).rgb * 0.5, 1.0); }
"""
# the sea's layer back into the view's light where the ocean drew (its depth is in the view's already)
SEA_FS = """
#version 330
uniform sampler2D gSea;
in vec2 vNdc; out vec4 color;
void main() {
    vec4 c = texelFetch(gSea, ivec2(gl_FragCoord.xy), 0);
    if (c.a <= 0.0) discard;
    color = vec4(c.rgb * 2.0, 1.0);
}
"""


def area_of(source: str) -> str:
    """The ambient area of an island file (``source``: its path in the install): its map names none, so the engine
    puts it in the area of its kind, above or under water (``AREAS``)."""
    return by_folder(source, "underwater", AREAS)


def _compile(tree, source: str) -> tuple:
    """In the background: the sea's and the coast waves' stages, the island's ambient set, the sky's cloud noise and
    the engine's water maps (level 0, RGBA bytes)."""
    from ...models import shader2070
    from ...sources import ambient2070

    files = Files(tree)
    shaders = shader2070.Source2070(files, files.compiler("anno2070"))
    wanted = {**ocean2070.STAGES, "ocean": (ocean2070.FX, ocean2070.ISLAND_DEFINES, ocean2070.TECHNIQUE),
              **coast2070.STAGES}  # fmt: skip
    stages = {k: shaders.effect_pass(*v) for k, v in wanted.items()}
    ambient = ambient2070.parse(files.read(ambient2070.AMBIENT_MAP).decode("latin-1"))
    setting = ambient.setting(ambient.area(area_of(source)).setting, VARIANT)
    paths = (*ocean2070.WAVES.values(), ocean2070.RIPPLES, ocean2070.CAUSTICS, coast2070.COAST)
    maps = {p: files.level0(p) for p in paths}
    return stages, setting, sky2070.read_volume(files), maps


class GameSea2070(IslandSea):
    """The 2070 island's sea by the game's shaders (module docstring)."""

    GAMES = ("anno2070",)
    MAP_JOB = INFO_JOB  # the water info map (``ocean2070.info_map``), point-sampled as the ocean declares it

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.ocean: ocean2070.Ocean2070 | None = None
        self.coast: coast2070.CoastWaves | None = None
        self.setting = None
        self.maps: dict = {}  # path -> the engine's water maps on the device
        self.shadow_map = None

    def _ready(self) -> bool:
        """The ocean made once its shaders and maps are in; False while they load or when they failed (said once)."""
        if self.ocean is not None:
            return True
        got = self._compiled(JOB, _compile)
        if got is None:
            return False
        stages, self.setting, volume, maps = got
        ctx = self.r.ctx
        self.ocean = ocean2070.Ocean2070(ctx, stages, Dummies(ctx.rhi), self.setting.water)
        self.ocean.cube.set_volume(volume)
        self.maps = {path: upload(ctx, px) for path, px in maps.items()}
        coast = self.maps[coast2070.COAST].native
        self.coast = coast2070.CoastWaves(ctx, stages, self.ocean.dummies, coast, self.ocean.cube.volume)
        return True

    def _record(self, f, t: dict, camera, opts) -> bool:
        """The view's scene mirrored, the copies of the view's frame, the coast waves' step, the sea into its layer
        over the view's depth, the layer into the frame; False without the info map yet."""
        ctx, s = self.r.ctx, self.r.session
        info = self._heights_map(
            f.game_heights if f.game_heights is not None else s.heights, ocean2070.info_map, point=True
        )
        if info is None:
            return False
        own, reflection = self._begin(f, opts, t)
        self._pass("half", HALF_COPY_FS).draw(own["refraction_fbo"], {"gLit": t["color"]}, {}, depth_test=False)
        self._pass("depth", DEPTH_COPY_FS).draw(own["read_fbo"], {"gDepth": t["depth"]}, {}, depth_test=False)
        sun = frame2070.sun_of(self.setting, sun_direction(f), float(f.lt.get("uwTime", 0.0)))
        half, maps = float(s.size) / 2, {p: m.native for p, m in self.maps.items()}
        texel = (0.5 / info[2], 0.5 / info[2])
        sea = ocean2070.Sea((half, half), half + MARGIN, maps, info[1].native, texel, self.coast, reflection)
        # the game's sea takes no sun shadow: its shader hands the lookup a distance of 0 (shader-compile.md 10)
        self.shadow_map, shadow = frame2070.unshadowed(ctx, self.shadow_map)
        copies = {"cTexture5": own["refraction"].native, "cTexture6": own["read"].native}
        cam = self._camera(camera, t["size"])
        waves = frame2070.ocean_waves(self.ocean, sea, sun.seconds)
        coast = {"cTexture1": own["read"].native, "cTexture3": info[1].native, "cTexture4": waves["cTexture0"]}
        self._coast_waves(cam, sun.seconds, coast, texel, still=opts.particle_time is not None)
        inputs = frame2070.SeaInputs(copies, shadow, waves)
        frame2070.ocean_pass(ctx, own["sea_fbo"], t["size"], cam, sun, self.ocean, sea, inputs)
        self._end(t, SEA_FS)
        return True

    def _coast_waves(self, cam: gs.Camera, seconds: float, maps: dict, texel: tuple, still: bool) -> None:
        """The coast waves stepped to ``seconds`` round what ``cam`` sees (``coast2070``): the first time, and for a
        still picture's clock (``still``) set back, from ``WARM`` seconds before it, so that a picture shows them
        settled and two pictures of one moment agree."""
        coast = self.coast
        view = (cam.eye, -np.asarray(cam.view, float)[:3, 2], cam.view @ cam.proj)

        def fill(at: float):
            def put(c: dict) -> None:
                frame2070.fill_camera(c, cam, at)
                gs.set_values(c.values(), {**frame2070.water_values(cam, (1.0, 1.0, 1.0, 1.0)),
                                           "cWaterConfig": [*texel, 1.0 / ocean2070.TILE, 0.0]})  # fmt: skip

            return put

        if coast.last is None or (still and seconds < coast.last):
            coast.restart()
            at = seconds - WARM
            while at < seconds:
                coast.step(at, view, maps, fill(at))
                at += coast2070.STEP * 1.0001  # a hair past the step: each of them is due
        coast.step(seconds, view, maps, fill(seconds))

    def release(self) -> None:
        super().release()
        if self.coast is not None:
            self.coast.release()
            self.coast = None
        for tex in self.maps.values():
            tex.release()
        self.maps = {}
        if self.ocean is not None:
            self.ocean.release()
            self.ocean = None
        if self.shadow_map is not None:
            self.shadow_map.release()
            self.shadow_map = None
