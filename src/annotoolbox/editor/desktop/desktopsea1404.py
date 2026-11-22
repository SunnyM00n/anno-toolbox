"""The sea of an Anno 1404 island by the game's own water shaders in the island view (Direct3D 12 or 11), over the
view's own terrain and models: the projected grid of ``render.ocean1404`` over the island's water depth map built from
its heights as the engine builds it, refracting the view's frame with the caustics, reflecting the view's scene
mirrored in the water and the sky cube 1404's sky shader renders; the view's own sea while it compiles, where it
fails and on OpenGL.

``CGameSea1404(renderer)`` is the entry, one per ``desktoprender.CRenderer``, on ``desktopsea.CIslandSea``'s seam;
docs/island-editor-view.md "1404's sea by the game's shaders".
"""

from __future__ import annotations

from dataclasses import replace

from ...render import frame1404, ocean1404, sky2070
from ...render import gameshader as gs
from ...render.frameforward import DEPTH_COPY_FS
from ...render.fxcdraw import CDummies, cube_faces, cube_of
from ...render.ocean1701 import trapezoid
from .desktopsea import GAMMA_FS, GAMMA_LAYER_FS, CFiles, CIslandSea, SViewShadow, by_folder, sun_direction, upload

JOB = "game-sea-1404"
DEPTH_JOB = "game-sea-1404-depth"
# the set an island shows: its world puts it in the area of its kind (NorthIsland, SouthIsland: GlobalAreas), which
# an island in the editor has none of; its folder names the kind [ours]
SETTINGS = ("North", "South")
MIRROR_SIDE = 1024  # the water manager's reflection map is 3/5 of the screen; a square of ours [ours]


def setting_of(source: str) -> str:
    """The ambient set of an island file (``SETTINGS``: a southern island's folder names it)."""
    return by_folder(source, "south", SETTINGS)


def shadow_values(shadow: SViewShadow | None) -> tuple | None:
    """The view's sun shadow as ``waterdisp.fx``'s ``StandardShadow`` reads it (``cbShadow``: the map's texture
    matrix as 1404's matrices go in, the plane of its depth; the comparison without a bias, ``vsShadow2ObjNoBias``),
    and the map; None without one."""
    if shadow is None:
        return None
    values = {"cMtxShadowVPP0": shadow.texture, "cVecShadowVPD": list(shadow.depth), "cShadowBias": [0.0] * 4,
              "cShadowMethod": [0.0] * 4}  # fmt: skip
    return values, {"cShadowMap": shadow.map}


def _compile(tree, source: str) -> tuple:
    """In the background: the sea's two passes and the sky cube's, the set's light and waves, the waves, ripples and
    caustics (level 0, RGBA bytes) and the cloud cube's faces."""
    from ...models import ddsdecode, shader2070

    files = CFiles(tree)
    shaders = shader2070.CSource2070(files, files.compiler("anno1404"), shader2070.GLOBALS_1404, missing_empty=True)
    stages = {k: shaders.effect_pass(*stage) for k, stage in ocean1404.STAGES.items()}
    stages["cube"] = shaders.effect_pass(frame1404.SKY_FX, (), sky2070.CUBE_TECHNIQUE)
    text = frame1404.config_text(files, frame1404.AMBIENT)
    name = setting_of(source)
    paths = (ocean1404.WAVES, ocean1404.RIPPLES, ocean1404.CAUSTICS)
    maps = {path: files.level0(path) for path in paths}
    clouds = cube_faces(ddsdecode.CDdsFile(files.read(frame1404.SKY_CUBE)))
    return stages, frame1404.read_sun(text, name), ocean1404.read_waves(text, name), maps, clouds


class CGameSea1404(CIslandSea):
    """The 1404 island's sea by the game's shaders (module docstring)."""

    GAMES = ("anno1404",)
    MIRROR_SIDE = MIRROR_SIDE
    MAP_JOB = DEPTH_JOB  # the water depth map (``ocean1404.depth_map``)

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.m_ocean: ocean1404.COcean1404 | None = None
        self.m_cube: sky2070.CSkyCube | None = None
        self.m_sun = self.m_waves = None
        self.m_maps: dict = {}  # path -> the waves, ripples and caustics on the device

    def _ready(self) -> bool:
        """The sea made once its shaders and maps are in; False while they load or when they failed (said once)."""
        if self.m_ocean is not None:
            return True
        got = self._compiled(JOB, _compile)
        if got is None:
            return False
        stages, self.m_sun, self.m_waves, maps, clouds = got
        ctx = self.m_renderer.m_ctx
        self.m_maps = {path: upload(ctx, pixels) for path, pixels in maps.items()}
        dummies = CDummies(ctx.m_rhi)
        self.m_ocean = ocean1404.COcean1404(ctx, stages, dummies)
        self.m_cube = sky2070.CSkyCube(ctx, stages["cube"], dummies)
        self.m_cube.m_volume = cube_of(ctx, clouds)  # 1404's clouds are a cube (cTexture0), not 2070's noise volume
        return True

    def _sky_cube(self, camera: gs.SCamera, sun: frame1404.SSun1404):
        """The sky cube the water reflects, rendered as 1404's sky manager renders it (``SkyCubeMap``, the set's
        scattering and the turning clouds)."""

        def fill(constants: dict) -> None:
            values = {
                **gs.camera_values(camera),
                **frame1404.light_values(sun),
                **frame1404.scattering_values(sun, camera),
            }
            gs.set_values(constants.values(), {**values, **sky2070.cloud_turns(sun.seconds)})

        return self.m_cube.render(fill)

    def _record(self, frame, targets: dict, camera, options) -> bool:
        """The depth map, the view's scene mirrored, the copies of the view's frame, the sky cube, the sea into its
        layer over the view's depth, the layer into the frame; False where the engine draws no water (a camera too
        low) and before the depth map is in."""
        ctx, session = self.m_renderer.m_ctx, self.m_renderer.m_session
        game = self._game_camera(camera, targets["size"])
        if trapezoid(game.view @ game.projection, ocean1404.TRAPEZOID) is None:  # the engine draws none
            return False
        depth = self._heights_map(
            frame.game_heights if frame.game_heights is not None else session.m_heights, ocean1404.depth_map
        )
        if depth is None:
            return False
        own, reflection = self._begin(frame, options, targets)
        self._pass("gamma", GAMMA_FS).draw(own["refraction_fbo"], {"gLit": targets["color"]}, {}, depth_test=False)
        self._pass("depth", DEPTH_COPY_FS).draw(own["read_fbo"], {"gDepth": targets["depth"]}, {}, depth_test=False)
        sun = replace(self.m_sun, direction=sun_direction(frame), seconds=float(frame.lt.get("uwTime", 0.0)))
        cam = self._flipped(game)
        self.m_ocean.m_is_mirrored = not bool(
            ctx.m_flip_offscreen
        )  # the game's mirror, undone again by the turned picture
        maps = {"waves": self.m_maps[ocean1404.WAVES].native, "depth": (depth[1].native, depth[2]),
                "cTexture0": self._sky_cube(game, sun), "cTexture2": self.m_maps[ocean1404.RIPPLES].native,
                "cTexture6": own["refraction"].native, "cTexture7": reflection, "cTexture8": own["read"].native,
                "cTexture9": self.m_maps[ocean1404.CAUSTICS].native}  # fmt: skip
        drew = self.m_ocean.draw(own["sea_fbo"], targets["size"], (game, cam), sun, self.m_waves, maps,
                               shadow_values(self._view_shadow(frame)))  # fmt: skip
        self._end(targets, GAMMA_LAYER_FS, drew)
        return drew

    def release(self) -> None:
        super().release()
        for texture in self.m_maps.values():
            texture.release()
        self.m_maps = {}
        for part in (self.m_ocean, self.m_cube):
            if part is not None:
                part.release()
        self.m_ocean = self.m_cube = None
