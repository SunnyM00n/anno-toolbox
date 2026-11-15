"""The sea of an Anno 1701 island by the game's own water shaders in the island view (Direct3D 12 or 11), over the
view's own terrain and models: the projected grid of ``render.ocean1701``, refracting the view's frame, reflecting the
view's scene mirrored in the water and the game's sky cube, its waves damped by the water depth worked out from the
view's depth as the game's terrain writes it; the view's own sea while it compiles, where it fails and on OpenGL.

``GameSea1701(renderer)`` is the entry, one per ``desktoprender.Renderer``, on ``desktopsea.IslandSea``'s seam;
docs/island-editor-view.md "1701's sea by the game's shaders".
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ...othergames.isd1701 import TILE
from ...render import coast1701, ocean1701
from ...render.camera import gl
from ...render.frame1404 import config_text
from ...render.frame1701 import AMBIENT, LIGHT, read_light
from ...render.fxcdraw import Dummies, cube_faces, cube_of
from .desktopsea import GAMMA_LAYER_FS, Files, IslandSea, ViewShadow, by_folder, sun_direction, upload

JOB = "game-sea-1701"
# the ambient map's default profiles of a northern and a southern island; an island in the editor has no place on a
# world map, so its folder picks one [ours]
PROFILES = ("North", "South")
REFLECTION_SIDE = 2048  # CTerrain's reflection map at the highest quality (512 x 1, 2 or 4)
COAST_WIDTH = 96.0  # a coast line's width where the file has none for it (world units: the common one) [ours]

# the view's linear light into the game's picture (gamma values, as an 8-bit target holds them) and the water depth
# the game's terrain writes beside its colour (terrain_pervertex.cg: saturate(-y / 125) in world units), from the
# view's depth. Where the view has no ground, or only the floor at the island's border height (``seabed``), the
# game has its world's sea floor: that height's depth and the colour the water turns to over depth stand in [ours]
REFRACTION_FS = """
#version 330
uniform sampler2D gLit; uniform sampler2D gDepth; uniform mat4 invViewProj;
uniform float scale; uniform float range; uniform float floorY; uniform vec3 seabed; uniform bool depthMap;
in vec2 vNdc; out vec4 color;
void main() {
    ivec2 at = ivec2(gl_FragCoord.xy);
    float d = texelFetch(gDepth, at, 0).r;
    vec4 p = invViewProj * vec4(vNdc, d * 2.0 - 1.0, 1.0);
    float y = d >= 1.0 ? floorY : max(p.y / p.w, floorY);
    bool floor = y <= floorY + 0.01;
    if (depthMap) { color = vec4(vec3(clamp(-y * scale / range, 0.0, 1.0)), 1.0); return; }
    vec3 c = pow(clamp(texelFetch(gLit, at, 0).rgb, 0.0, 1.0), vec3(1.0 / 2.2));
    color = vec4(floor ? seabed : c, 1.0);
}
"""


def seabed(heights: np.ndarray) -> float:
    """The sea floor round an island (view units): the height of its border. 1701's islands end at -100 world units
    all round, where the world's floor lies **[I]**."""
    h = np.asarray(heights, float)
    return float(np.concatenate([h[0], h[-1], h[:, 0], h[:, -1]]).mean()) if h.size else 0.0


def profile_of(source: str) -> str:
    """The ambient profile of an island file (``PROFILES``: a southern island's folder names it)."""
    return by_folder(source, "south", PROFILES)


def coast_lines(session, isl) -> list[coast1701.CoastLine]:
    """The island's coast lines in world units with the edits on (the points the session's, the widths and kinds
    the file's: each point the width of the file's point nearest to it, as an edit writes it back). Lines of the
    file with fewer than two points are the ones the session leaves out."""
    shipped = [line for line in (isl.coasts() if isl is not None else []) if len(line.points) >= 2]
    out = []
    for k in range(len((session.water_net or {}).get("coasts", []))):
        pts = np.asarray(session.path.coast(k)["points"], float).reshape(-1, 2) * TILE
        line = shipped[k] if k < len(shipped) else None
        if line is None:
            widths, kind = np.full(len(pts), COAST_WIDTH), 0
        else:
            old = np.asarray(line.points, float)[:, [0, 2]] * TILE
            near = ((pts[:, None, :] - old[None, :, :]) ** 2).sum(-1).argmin(1)
            widths, kind = np.asarray(line.widths, float)[near] * TILE, line.variation
        # the session keeps no height: y 0, as nearly every shipped coast point has it (80,517 of 80,527 in 400
        # files) [ours]
        xyz = np.stack([pts[:, 0], np.zeros(len(pts)), pts[:, 1]], 1)
        out.append(coast1701.CoastLine(xyz, widths, kind))
    return out


def shadow_values(shadow: ViewShadow | None) -> tuple | None:
    """The view's sun shadow as ``water.fx`` with ``VERTEXWATER_SHADOW`` reads it (``Ocean1701.draw``'s
    ``shadow``): the map's texture matrix, the plane of its depth, the map; None without one."""
    if shadow is None:
        return None
    values = {"vcMtxShadowBias": shadow.texture, "vcShdConstants": list(shadow.depth)}
    return values, {ocean1701.SHADOW_MAP: shadow.map}


def _compile(tree, source: str) -> tuple:
    """In the background: the sea's passes (the position pass with the coast waves) and the coast lines' two,
    the profile's light and ocean colour, the waves (level 0, RGBA bytes), the sky cube's faces and the coast
    waves' textures."""
    from ...models import ddsdecode, shader1701

    files = Files(tree)
    shaders = shader1701.Source1701(files, files.compiler("anno1701"))
    wanted = {**ocean1701.STAGES, **ocean1701.WAVE_STAGES, **coast1701.STAGES}
    stages = {k: shaders.effect_pass(*v) for k, v in wanted.items()}
    name, ambient = profile_of(source), config_text(files, AMBIENT)
    light = read_light(ambient, config_text(files, LIGHT), name)
    sky = cube_faces(ddsdecode.DdsFile(files.read(ocean1701.SKY_CUBE)))
    coast = {k: files.level0(p) for k, p in coast1701.TEXTURES.items()}
    return stages, light, ocean1701.ocean_color(ambient, name), files.level0(ocean1701.WAVES), sky, coast


class GameSea1701(IslandSea):
    """The 1701 island's sea by the game's shaders (module docstring)."""

    GAMES = ("anno1701",)
    SCALE = TILE
    MIRROR_SIDE = REFLECTION_SIDE

    def __init__(self, renderer) -> None:
        super().__init__(renderer)
        self.ocean: ocean1701.Ocean1701 | None = None
        self.light = self.color = None
        self.maps: dict = {}  # the waves, the sky cube and the coast waves' textures on the device
        self.coast: coast1701.CoastWaves1701 | None = None
        self._coast_key = None  # the coast lines' edits the strips were made from

    def _ready(self) -> bool:
        """The sea made once its shaders and maps are in; False while they load or when they failed (said once)."""
        if self.ocean is not None:
            return True
        got = self._compiled(JOB, _compile)
        if got is None:
            return False
        stages, self.light, self.color, waves, sky, coast = got
        ctx = self.r.ctx
        self.maps = {
            "waves": upload(ctx, waves),
            "sky": cube_of(ctx, sky),
            **{k: upload(ctx, v) for k, v in coast.items()},
        }
        dummies = Dummies(ctx.rhi)
        self.ocean = ocean1701.Ocean1701(ctx, stages, dummies)
        self.coast = coast1701.CoastWaves1701(ctx, stages, dummies)
        return True

    def _coast_waves(self, cam, seconds: float):
        """The terrain's wave texture of this frame: the coast lines' strips (made again when the lines are
        edited) drawn for the camera ``cam``."""
        s = self.r.session
        key = repr(sorted(s.edits.get("coast", {}).items()))
        if key != self._coast_key:
            lines = coast_lines(s, getattr(self.r.editor, "isl", None))
            self.coast.lines(coast1701.strips(lines))
            self._coast_key = key
        maps = {k: self.maps[k].native for k in coast1701.TEXTURES}
        return self.coast.draw(cam, seconds, maps).native

    def _record(self, f, t: dict, camera, opts) -> bool:
        """The view's scene mirrored, the copies of the view's frame and its water depth, the sea into its layer over
        the view's depth, the layer into the frame; False where the engine draws no water (a camera too low)."""
        ctx = self.r.ctx
        game = self._game_camera(camera, t["size"])
        if ocean1701.trapezoid(game.view @ game.proj) is None:  # the engine draws none: no passes for nothing
            return False
        own, reflection = self._begin(f, opts, t)
        bed = {"invViewProj": gl(np.linalg.inv(np.asarray(f.view_proj, float))), "scale": float(self.SCALE),
               "range": ocean1701.DEPTH_RANGE, "floorY": seabed(self.r.session.heights),
               "seabed": tuple(float(v) for v in ocean1701.deep_color(self.light, self.color))}  # fmt: skip
        inputs, copy = {"gLit": t["color"], "gDepth": t["depth"]}, self._pass("refraction", REFRACTION_FS)
        copy.draw(own["refraction_fbo"], inputs, {**bed, "depthMap": False}, depth_test=False)
        copy.draw(own["read_fbo"], inputs, {**bed, "depthMap": True}, depth_test=False)
        seconds = float(f.lt.get("uwTime", 0.0))
        light = replace(self.light, direction=sun_direction(f), seconds=seconds)
        cam = self._flipped(game)
        self.ocean.mirrored = not bool(ctx.flip_offscreen)  # the game's mirror, undone again by the turned picture
        maps = {"cSamplerWave": self.maps["waves"].native, "cSamplerSkyRef": self.maps["sky"],
                "cSamplerRefrac": own["refraction"].native, "cSamplerReflec": reflection,
                "cSamplerWaterDepth": own["read"].native,
                ocean1701.DYNAMIC_WAVES: self._coast_waves(cam, seconds)}  # fmt: skip
        drew = self.ocean.draw(own["sea_fbo"], t["size"], (game, cam), light, maps, seconds, self.color,
                               shadow_values(self._view_shadow(f)))  # fmt: skip
        self._end(t, GAMMA_LAYER_FS, drew)
        return drew

    def release(self) -> None:
        super().release()
        for k, tex in self.maps.items():
            if k == "sky":
                self.r.ctx.later(tex)  # a native cube (``fxcdraw.cube_of``): gone after the frame in flight
            else:
                tex.release()
        self.maps = {}
        if self.coast is not None:
            self.coast.release()
            self.coast, self._coast_key = None, None
        if self.ocean is not None:
            self.ocean.release()
            self.ocean = None
