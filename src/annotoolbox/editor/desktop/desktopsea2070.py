"""The sea of an Anno 2070 island by the game's own water shaders in the island view (Direct3D 12 or 11), over the
view's own terrain and models: the island's water info map built as the game builds it, the coast waves, the view's
scene mirrored into the planar reflection, the island's own ambient set; the view's own sea while they compile, where
they fail and on OpenGL.

``CGameSea2070(renderer)`` is the entry, one per ``desktoprender.CRenderer``, on ``desktopsea.CIslandSea``'s seam;
docs/island-editor-view.md "2070's sea by the game's shaders".
"""

from __future__ import annotations

import numpy as np

from ...render import coast2070, frame2070, ocean2070, sky2070
from ...render import gameshader as gs
from ...render.frameforward import DEPTH_COPY_FS
from ...render.fxcdraw import CDummies
from .desktopsea import CFiles, CIslandSea, by_folder, sun_direction, upload

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

    files = CFiles(tree)
    shaders = shader2070.CSource2070(files, files.compiler("anno2070"))
    wanted = {**ocean2070.STAGES, "ocean": (ocean2070.FX, ocean2070.ISLAND_DEFINES, ocean2070.TECHNIQUE),
              **coast2070.STAGES}  # fmt: skip
    stages = {stage_name: shaders.effect_pass(*stage_arguments) for stage_name, stage_arguments in wanted.items()}
    ambient = ambient2070.parse(files.read(ambient2070.AMBIENT_MAP).decode("latin-1"))
    setting = ambient.setting(ambient.area(area_of(source)).setting, VARIANT)
    paths = (*ocean2070.WAVES.values(), ocean2070.RIPPLES, ocean2070.CAUSTICS, coast2070.COAST)
    maps = {path: files.level0(path) for path in paths}
    return stages, setting, sky2070.read_volume(files), maps


class CGameSea2070(CIslandSea):
    """The 2070 island's sea by the game's shaders (module docstring)."""

    GAMES = ("anno2070",)
    MAP_JOB = INFO_JOB  # the water info map (``ocean2070.info_map``), point-sampled as the ocean declares it

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.m_ocean: ocean2070.COcean2070 | None = None
        self.m_coast: coast2070.CCoastWaves | None = None
        self.m_setting = None
        self.m_maps: dict = {}  # path -> the engine's water maps on the device
        self.m_shadow_map = None

    def _ready(self) -> bool:
        """The ocean made once its shaders and maps are in; False while they load or when they failed (said once)."""
        if self.m_ocean is not None:
            return True
        got = self._compiled(JOB, _compile)
        if got is None:
            return False
        stages, self.m_setting, volume, maps = got
        ctx = self.m_renderer.m_ctx
        self.m_ocean = ocean2070.COcean2070(ctx, stages, CDummies(ctx.m_rhi), self.m_setting.water)
        self.m_ocean.m_cube.set_volume(volume)
        self.m_maps = {path: upload(ctx, pixels) for path, pixels in maps.items()}
        coast = self.m_maps[coast2070.COAST].native
        self.m_coast = coast2070.CCoastWaves(ctx, stages, self.m_ocean.m_dummies, coast, self.m_ocean.m_cube.m_volume)
        return True

    def _record(self, frame, targets: dict, camera, options) -> bool:
        """The view's scene mirrored, the copies of the view's frame, the coast waves' step, the sea into its layer
        over the view's depth, the layer into the frame; False without the info map yet."""
        ctx, session = self.m_renderer.m_ctx, self.m_renderer.m_session
        info_map = self._heights_map(
            frame.game_heights if frame.game_heights is not None else session.m_heights, ocean2070.info_map, point=True
        )
        if info_map is None:
            return False
        own, reflection = self._begin(frame, options, targets)
        self._pass("half", HALF_COPY_FS).draw(own["refraction_fbo"], {"gLit": targets["color"]}, {}, depth_test=False)
        self._pass("depth", DEPTH_COPY_FS).draw(own["read_fbo"], {"gDepth": targets["depth"]}, {}, depth_test=False)
        sun = frame2070.sun_of(self.m_setting, sun_direction(frame), float(frame.lt.get("uwTime", 0.0)))
        half, maps = (
            float(session.m_size_tiles) / 2,
            {path: map_texture.native for path, map_texture in self.m_maps.items()},
        )
        texel = (0.5 / info_map[2], 0.5 / info_map[2])
        sea = ocean2070.SSea((half, half), half + MARGIN, maps, info_map[1].native, texel, self.m_coast, reflection)
        # the game's sea takes no sun shadow: its shader hands the lookup a distance of 0 (shader-compile.md 10)
        self.m_shadow_map, shadow = frame2070.unshadowed(ctx, self.m_shadow_map)
        copies = {"cTexture5": own["refraction"].native, "cTexture6": own["read"].native}
        view_camera = self._camera(camera, targets["size"])
        waves = frame2070.ocean_waves(self.m_ocean, sea, sun.seconds)
        coast = {"cTexture1": own["read"].native, "cTexture3": info_map[1].native, "cTexture4": waves["cTexture0"]}
        self._coast_waves(view_camera, sun.seconds, coast, texel, still=options.particle_time is not None)
        inputs = frame2070.SSeaInputs(copies, shadow, waves)
        frame2070.ocean_pass(ctx, own["sea_fbo"], targets["size"], view_camera, sun, self.m_ocean, sea, inputs)
        self._end(targets, SEA_FS)
        return True

    def _coast_waves(self, camera: gs.SCamera, seconds: float, maps: dict, texel: tuple, still: bool) -> None:
        """The coast waves stepped to ``seconds`` round what ``camera`` sees (``coast2070``): the first time, and for a
        still picture's clock (``still``) set back, from ``WARM`` seconds before it, so that a picture shows them
        settled and two pictures of one moment agree."""
        coast = self.m_coast
        view = (camera.eye, -np.asarray(camera.view, float)[:3, 2], camera.view @ camera.projection)

        def fill(time_s: float):
            def put(constants: dict) -> None:
                frame2070.fill_camera(constants, camera, time_s)
                gs.set_values(constants.values(), {**frame2070.water_values(camera, (1.0, 1.0, 1.0, 1.0)),
                                           "cWaterConfig": [*texel, 1.0 / ocean2070.TILE, 0.0]})  # fmt: skip

            return put

        if coast.m_last is None or (still and seconds < coast.m_last):
            coast.restart()
            time_s = seconds - WARM
            while time_s < seconds:
                coast.step(time_s, view, maps, fill(time_s))
                time_s += coast2070.STEP * 1.0001  # a hair past the step: each of them is due
        coast.step(seconds, view, maps, fill(seconds))

    def release(self) -> None:
        super().release()
        if self.m_coast is not None:
            self.m_coast.release()
            self.m_coast = None
        for texture in self.m_maps.values():
            texture.release()
        self.m_maps = {}
        if self.m_ocean is not None:
            self.m_ocean.release()
            self.m_ocean = None
        if self.m_shadow_map is not None:
            self.m_shadow_map.release()
            self.m_shadow_map = None
