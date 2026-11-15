"""Anno 1404's models drawn forward-lit by the game's own shaders on QRhi (Direct3D 11): ``model.fx``'s
``StandardShadow`` (``WithoutShadow`` without casters; lit per vertex in gamma values, hazed by ``cbScattering``) over
the sun's shadow map of its ``Obj2Shadow``, ``sky.fx``'s ``Sky`` behind them, composited as linear light with depth.

``Frame1404(ctx)`` is the entry: ``render(target, size, models, camera, sun)``; ``Model1404`` is one mesh group,
``Sun1404`` the light and the atmosphere, ``scattering_values`` the engine's scattering constants, ``granny_mesh`` the
vertices as the shader takes them. docs/rendering/shader-compile.md section 12.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from ..models import skinning
from ..models.tangents import frames
from . import gameshader as gs
from . import shadowforward
from .frame2070 import MeshModel, sun_shadow
from .frameforward import SceneCopies
from .fxcdraw import FxcDraw
from .gamedraw import Mesh
from .nativepass import Composite, draw_models, draw_pass
from .sky2070 import cloud_turns

F = QRhiTexture.Format
AMBIENT = "data/config/engine/ambientmap.xml"
SETTING = "North"  # the set the northern islands show (GlobalAreas)
NUMBER = r"([\d.eE+-]+)"
FX = "data/system/shaders/fx/model.fx"
DYE_FX = "data/system/shaders/fx/model_dye.fx"  # a dye material's (ShaderIndex 6): the player colour by its mask
WATER_FX, WATER_INDEX = "data/system/shaders/fx/model_water.fx", 1  # a water material's effect and shader index
# the highest water quality: the sheet refracts the scene behind it with its caustics (shader-compile.md 12)
WATER_DEFINES = (("WATER_BACKBUFFER_REFRACTION", "1"), ("WATER_CAUSTICS_ENABLED", "1"))
CAUSTICS = "data/graphics/effects/water/caustics_0.dds"  # the engine's Caustics.png at the highest texture quality
TECHNIQUE = "WithoutShadow"
SHADOWED = "StandardShadow"  # shadow method 0, one map (the engine's trapezoidal fit not read: ours)
CASTER = "Obj2Shadow"
SKY_FX = "data/system/shaders/fx/sky.fx"
SKY_CUBE = "data/graphics/effects/sky/sky04.dds"  # the cloud cube the render engine loads (its alpha: the density)
# no damage: no dirt from the alpha, the colour kept (AddDirt multiplies by y), not desaturated
INTACT = [0.0, 1.0, 0.0, 0.0]
POSITION, DIRECTIONS = 0, (1, 2, 3)  # mesh element usages a skin moves and turns: normal, tangent, bitangent
# a map the material lacks: white diffuse, a flat normal without specular
FALLBACKS = {"cTexture1": (128, 128, 255, 0)}
# the scattering setter's constants (shader-compile.md 12, "Sky"): Rayleigh's numerators over the density, Mie's
# coefficients per channel from the turbidity, the phase functions' 3 / 16 pi and 1 / 4 pi
RAYLEIGH = (1.579609e20, 4.167948e20, 5.538964e20)
MIE = ((0.00570717407, 0.00567752216), (0.00911854953, 0.00907117315), (0.010437048, 0.0103828218))
RAYLEIGH_PHASE, MIE_PHASE = 3.0 / (16.0 * np.pi), 1.0 / (4.0 * np.pi)
# cbScattering's members with an initial value (the optical depths in metres): the effect runtime writes those, a
# plain compile leaves them to us
OPTICAL_DEPTHS = {"cOpticalDepths": [16000.0, 75000.0, 10000.0, 60000.0], "cDeltaOpticalDepths": [59000.0, 50000.0]}
# the game's colour leaves in gamma values; the view adds linear light and encodes at its end
COMPOSITE_FS = """
#version 330
uniform sampler2D gColor; uniform sampler2D gDepth; uniform bool flip; uniform bool sky;
in vec2 vNdc; out vec4 color;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    if (flip) uv.y = 1.0 - uv.y;
    float depth = texture(gDepth, uv).r;
    if (depth >= 1.0 && !sky) discard;
    color = vec4(pow(max(texture(gColor, uv).rgb, 0.0), vec3(2.2)), 1.0);
    gl_FragDepth = min(depth, 1.0);
}
"""


@dataclass(frozen=True)
class Sun1404:
    """The light and the atmosphere as an ambient set gives them: the direction towards the sun (the view's), the sun
    and ambient colours in gamma values (colour times power), the atmosphere's ``Density``, ``Turbidity``, ``g`` and
    ``ScatterStart``, its sun colour times ``SunColorPower``, the fog colours and the mood colour (rgba)."""

    direction: tuple[float, float, float]
    diffuse: tuple[float, float, float] = (1.389, 1.268, 1.021)
    ambient: tuple[float, float, float] = (0.423, 0.574, 0.651)
    density: float = 1.3e25
    turbidity: float = 0.9948044
    g: float = -0.1
    scatter_start: float = 0.2
    sun_color: tuple[float, float, float] = (10.0, 7.333, 7.333)
    fog_horizon: tuple[float, float, float] = (0.784, 0.886, 0.933)
    fog_zenith: tuple[float, float, float] = (0.435, 0.682, 0.816)
    mood: tuple[float, float, float, float] = (1.0, 0.973, 0.851, 0.0)
    seconds: float = 0.0  # the view's time: the clouds' turn (sky2070.cloud_turns)
    ocean: tuple[float, float, float] = (0.135, 0.176, 0.229)  # OceanWaterColor times its power: the sea's colour


def config_text(files, path: str) -> str:
    """An old engine's config file (``ambientmap.xml``, ``light.xml``) as text, empty where the install has none."""
    return files.read(path).decode("latin-1") if files.exists(path) else ""


def profile_block(text: str, name: str) -> str:
    """The body of the element ``name`` (an ambient file's set or profile, a block of one), empty without it."""
    found = re.search(rf"<{name}>(.*?)</{name}>", text, re.S)
    return found.group(1) if found else ""


def colour_in(body: str, tag: str) -> tuple[float, ...] | None:
    """The ``<r>``, ``<g>``, ``<b>`` (and ``<a>``, where it has one) of the element ``tag`` in ``body`` as stored;
    None without it. The old engines' ambient and light files keep their colours so."""
    rgb = rf"<{tag}>\s*<r>{NUMBER}</r>\s*<g>{NUMBER}</g>\s*<b>{NUMBER}</b>(?:\s*<a>{NUMBER}</a>)?"
    found = re.search(rgb, body)
    return tuple(float(v) for v in found.groups() if v is not None) if found else None


def number_in(body: str, tag: str, fallback: float) -> float:
    """The number the element ``tag`` in ``body`` holds, ``fallback`` without it."""
    found = re.search(rf"<{tag}>\s*{NUMBER}\s*</{tag}>", body)
    return float(found.group(1)) if found else fallback


def read_sun(text: str, name: str = SETTING) -> Sun1404:
    """The light and atmosphere of a set of ``ambientmap.xml`` (colours times their power, gamma values); a value the
    set lacks keeps ``Sun1404``'s."""
    body = profile_block(text, name)
    base = Sun1404((0.0, 1.0, 0.0))

    def colour(tag: str, fallback: tuple, within: str = body, power: str | None = None, alpha: bool = False) -> tuple:
        found = colour_in(within, tag)
        if found is None:
            return fallback
        k = number_in(body, power or tag + "Power", 1.0)
        rgb = tuple(v * k for v in found[:3])
        return (*rgb, found[3] if len(found) > 3 else 0.0) if alpha else rgb

    air = profile_block(body, "Atmosphere")
    return replace(base, diffuse=colour("DiffuseColor", base.diffuse), ambient=colour("AmbientColor", base.ambient),
                   density=number_in(air, "Density", base.density),
                   turbidity=number_in(air, "Turbidity", base.turbidity), g=number_in(air, "g", base.g),
                   scatter_start=number_in(air, "ScatterStart", base.scatter_start),
                   sun_color=colour("SunColor", base.sun_color, air, "SunColorPower"),
                   fog_horizon=colour("FogHorizonColor", base.fog_horizon),
                   fog_zenith=colour("FogZenithColor", base.fog_zenith),
                   mood=colour("MoodColor", base.mood, alpha=True),
                   ocean=colour("OceanWaterColor", base.ocean))  # fmt: skip


def scattering_values(sun: Sun1404, cam: gs.Camera) -> dict:
    """``cbScattering`` as the engine's setter fills it from the set's atmosphere: Rayleigh by the density, Mie by
    the turbidity, their phase-weighted and reciprocal sums, the Henyey-Greenstein terms of ``g``, the haze's range
    from ``ScatterStart`` of the view's depth (the engine's camera term is left out) and the sun colour."""
    ray = np.array(RAYLEIGH) / sun.density
    mie = np.array([a * sun.turbidity - b for a, b in MIE])
    start = sun.scatter_start * (cam.far - cam.near) + cam.near
    return {"cBetaRay": ray, "cBetaMie": mie, "cBetaDashRay": ray * RAYLEIGH_PHASE, "cBetaDashMie": mie * MIE_PHASE,
            "cReciBetaRayMie": 1.0 / (ray + mie), "cHGParameters": [1 - sun.g**2, 1 + sun.g**2, 2 * sun.g],
            "cDepthSca": [start, cam.far, 1.0 / max(1e-6, cam.far - start)], "cSunColor": list(sun.sun_color),
            "cFogColorHorizon": list(sun.fog_horizon), "cFogColorZenith": list(sun.fog_zenith), **OPTICAL_DEPTHS}  # fmt: skip


def granny_mesh(vertices: np.ndarray, indices, tangents: bool = False, uvs: int = 1) -> Mesh:
    """A Granny mesh's vertices (position, normal, uv per row, as ``viewer.modelfiles`` reads them) with the indices of
    one group, as the ``p3n3t2`` layout the shader takes, or (``tangents``) ``p3n3g3b3t2`` with the tangent frames
    made from the positions and uvs (``models.tangents``; stored negated, as the shader reads them); ``uvs`` 2
    gives the one uv as the second set too (1701's ``t2t2`` effects: the viewer reads one set)."""
    v = np.asarray(vertices, "<f4")[:, :8]
    index = np.asarray(indices, np.uint32)
    if not tangents:
        elements = ((0, 7, 3, 0), (1, 7, 3, 12)) + ((4, 7, 2, 24),) * uvs
        return Mesh(np.ascontiguousarray(v).tobytes(), 32, elements, index, len(v))
    _n, tan, bit = frames([tuple(p) for p in v[:, :3].tolist()], [tuple(u) for u in v[:, 6:8].tolist()],
                         index.tolist(), [tuple(n) for n in v[:, 3:6].tolist()])  # fmt: skip
    data = np.concatenate([v[:, :6], -np.asarray(tan, "<f4"), -np.asarray(bit, "<f4"), v[:, 6:8]], axis=1)
    elements = ((0, 7, 3, 0), (1, 7, 3, 12), (2, 7, 3, 24), (3, 7, 3, 36)) + ((4, 7, 2, 48),) * uvs
    return Mesh(np.ascontiguousarray(data, "<f4").tobytes(), 56, elements, index, len(v))


def posed_vertices(mesh: Mesh, skin, matrices: np.ndarray) -> bytes:
    """A ``granny_mesh``'s vertices moved by a Granny skin (``skin``: the mesh's joints and weights, ``matrices``:
    the pose of ``models.grannyrig``'s rig): the position, and the normal and tangent frame turned. The old engine's
    model shaders read no bones: Granny poses its meshes on the CPU, so the game's shader gets posed vertices too
    (shader-compile.md 12, "Granny skins")."""
    rows = np.frombuffer(mesh.rest or mesh.data, "<f4").reshape(mesh.count, mesh.stride // 4).copy()
    blend = skinning.blended(skin, matrices)
    for usage, _type, _count, offset in mesh.elements:
        at = offset // 4
        if usage == POSITION:
            rows[:, at : at + 3] = np.einsum("nij,nj->ni", blend[:, :3, :3], rows[:, at : at + 3]) + blend[:, :3, 3]
        elif usage in DIRECTIONS:  # the tangent frame is stored negated: turning keeps the sign
            rows[:, at : at + 3] = skinning.turned(blend, rows[:, at : at + 3])
    return rows.tobytes()


def material_defines(diffuse: str, normal: str = "") -> tuple:
    """The switches a 1404 model material gives ``model.fx``: its diffuse map, and its normal map with the tangent
    layout (``P3N3G3B3T2``) the normal map needs."""
    out = (("DIFFUSE_ENABLED", "1"),) if diffuse else ()
    return out + ((("P3N3G3B3T2", "1"), ("NORMAL_ENABLED", "1")) if normal else ())


def is_water(values: dict) -> bool:
    """Whether a material (its values) is a model's water sheet: shader index 1 of the model owner's table."""
    return values.get("Owner", "MODEL") == "MODEL" and int(values.get("ShaderIndex", 0)) == WATER_INDEX


def light_values(sun: Sun1404) -> dict:
    """``cbLight``, ``cbObject`` and ``cbWater`` by name: the sun, white objects without damage, no water depth. The
    matrices go in as ours (row vectors): 1404's are declared column-major, so the shader's ``mul(M, v)`` reads them
    turned."""
    toward = np.asarray(sun.direction, float)
    return {"cLgtAmbient": [*sun.ambient, 1.0], "cLgtDiffuse": [*sun.diffuse, 1.0], "cLgtDirection": [*-toward, 0.0],
            "cLgtDirectionInv": [*toward, 0.0], "cObjectColor": [1.0, 1.0, 1.0, 1.0], "cObjectDamage": INTACT,
            "cWaterDepth": [0.0] * 4}  # fmt: skip


def as_is(m: np.ndarray) -> np.ndarray:
    """A matrix of ours as 1404's shaders take it (module docstring of ``light_values``)."""
    return np.asarray(m, float)


class Model1404(MeshModel):
    """One mesh group: ``draw`` (a ``FxcDraw`` of ``WithoutShadow``), ``shadowed`` its ``StandardShadow`` and
    ``shadow`` its ``Obj2Shadow`` draw (None: not compiled), ``maps`` (texture name -> QRhi texture or None while it
    loads), ``world`` (column vectors, set per frame), ``color`` and ``alpha`` (the object colour: the player's on a
    dye material, a water sheet's own)."""

    FALLBACKS = FALLBACKS
    refracts = False  # drawn after the others over a copy of the scene behind it (``Water1404``)

    def __init__(self, draw: FxcDraw, shadowed: FxcDraw | None = None, shadow: FxcDraw | None = None) -> None:
        self.draw_call, self.maps, self.world = draw, {}, np.identity(4)
        self.shadowed, self.shadow = shadowed, shadow
        self.paths: dict[str, str] = {}  # the shader's texture name -> the material's file
        self.color, self.alpha = (1.0, 1.0, 1.0), 1.0
        self.water = False  # a model's water sheet: View > Rendering > Water takes it out
        # drawn in the game's axes, the mirror in the camera's projection: the camera's passes swap their faces, the
        # sun's own view keeps them (shader-compile.md 11 "The game's own axes")
        for d, mirrored in ((draw, True), (shadowed, True), (shadow, False)):
            if d is not None:
                d.fallbacks, d.mirrored = dict(self.FALLBACKS), mirrored

    def fill_model(self, c: dict) -> None:
        """The object's own constants: its place (and its inverse) and its colour."""
        w = np.asarray(self.world, float)
        gs.set_values(c.values(), {"cMtxWorld": w.T, "cMtxWorldInv": np.linalg.inv(w).T,
                                   "cObjectColor": [*self.color, self.alpha]})  # fmt: skip

    def frame_values(self, cam: gs.Camera, sun: Sun1404) -> dict:
        """The colour pass's constants by name besides the model's own: camera, light, haze and the clock (``cTimer``:
        the game's, the wind's and the real one all the view's time, as 2070's)."""
        s = sun.seconds
        return {**gs.camera_values(cam), **light_values(sun), **scattering_values(sun, cam), "cTimer": [s, 0.0, s, s]}

    def pick(self, shadowed: bool) -> FxcDraw:
        """The colour pass's draw: the shadowed one while there is a shadow map."""
        return self.shadowed if shadowed and self.shadowed is not None else self.draw_call

    def prepare(self, rp, signature, batch, cam: gs.Camera, sun: Sun1404, shadow: tuple | None = None) -> None:
        """The colour pass's resources and constants; ``shadow``: ``cbShadow`` and the map, when there is one."""

        def fill(c: dict) -> None:
            gs.set_values(c.values(), {**self.frame_values(cam, sun), **(shadow[0] if shadow else {})})
            self.fill_model(c)

        maps = {**self.textures(), **(shadow[1] if shadow else {})}
        self.pick(shadow is not None).prepare(rp, signature, batch, maps, fill)

    def draw(self, cb, size, shadowed: bool = False) -> None:
        self.pick(shadowed).draw(cb, size)

    def release(self) -> None:
        for d in (self.draw_call, self.shadowed, self.shadow):
            if d is not None:
                d.release()


class Water1404(Model1404):
    """A water sheet (``model_water.fx``): drawn after the models and the sky, refracting the frame's copies
    ``screen`` (``Frame1404._water``), its depth measured in the engine's left-handed view (shader-compile.md 12)."""

    refracts = True

    def __init__(self, *draws) -> None:
        super().__init__(*draws)
        self.water, self.screen = True, {}

    def textures(self) -> dict:
        return {**super().textures(), **self.screen}

    def frame_values(self, cam: gs.Camera, sun: Sun1404) -> dict:
        return {**super().frame_values(cam, sun), **behind_values(cam), "cWaterCaustic": caustic_offsets(sun.seconds)}


def behind_values(cam: gs.Camera) -> dict:
    """What 1404's water reads the ground behind it with: the left-handed view (its depth ahead, as the engine's) and
    the inverse view-projection of the view turned but not moved (the engine adds the camera's place after)."""
    origin = np.array(cam.view, float)
    origin[3, :3] = 0.0
    return {"cMtxView": gs.left_handed(cam).view, "cMtxViewProjInvOrigin": np.linalg.inv(origin @ cam.proj)}


def caustic_offsets(seconds: float) -> list[float]:
    """``cWaterCaustic`` at the clock ``seconds``: the two caustic layers' scroll, 0.03 and 0.02 a second, the second
    half a period on (shader-compile.md 12 "Model water"), as the engine sets them: the map is read at the game's
    world x (shader-compile.md 11 "The game's own axes")."""
    a, b = (0.03 * seconds) % 1.0, (0.02 * seconds) % 1.0
    return [a, a, b, (b + 0.5) % 1.0]


class Frame1404:
    """The offscreen colour (half float, gamma values) and depth of one view, the sun's shadow map, the sky
    (``sky``: a ``sky2070.SkyDome`` of 1404's ``Sky``, None while it compiles or when it is off) and the composite
    (module docstring)."""

    SHADOW = {"matrix": as_is}  # sun_shadow's arguments: 1404's matrices as they are

    def __init__(self, ctx) -> None:
        self.ctx, self.size, self.color, self.depth, self.fbo = ctx, None, None, None, None
        self.composite = Composite(ctx, COMPOSITE_FS)
        self.shadow_map: shadowforward.ShadowMap | None = None
        self.sky = None
        self.copies: SceneCopies | None = None  # the scene the water sheets refract (made with the first sheet)
        self.shadow: tuple | None = None  # this frame's sun shadow (``cbShadow``'s values and the map), for _after
        self.beyond = None  # receivers the shadow's depth reaches too (the sea: points at its far reach)
        # the sea under the model (``ocean1404``, ``ocean1701``): draws into (fbo, size, the scene's colour and depth
        # copies, the sun's shadow) with the frame's depth
        self.sea = None

    def _targets(self, size: tuple[int, int]) -> None:
        if self.size == size:
            return
        self._drop()
        self.color = self.ctx.native_texture(size, F.RGBA16F)
        self.depth = self.ctx.depth_renderbuffer(size)
        self.fbo = self.ctx.framebuffer([self.color], self.depth)
        self.size = size

    def _shadow(self, models: list, sun: Sun1404, on: bool) -> tuple | None:
        """The casters' shadow map, ``cbShadow`` and the map by name (None when off or without casters)."""
        casters = [m for m in models if m.shadow is not None and m.shadowed is not None]
        if not on or not casters:
            return None
        self.shadow_map, values, maps = sun_shadow(self.ctx, self.shadow_map, casters, -np.asarray(sun.direction),
                                                   beyond=self.beyond, **self.SHADOW)  # fmt: skip
        return values, maps

    def render(self, target, size: tuple[int, int], models: list, cam: gs.Camera, sun: Sun1404,
               shadows: bool = True) -> None:  # fmt: skip
        """The shadow map, the models (``Model1404``), the sky and the water sheets (``Water1404``) into the offscreen
        target, then the composite."""
        self._targets(size)
        solid = [m for m in models if not m.refracts]
        water = [m for m in models if m.refracts]
        shadow = self.shadow = self._shadow(solid, sun, shadows)  # a sheet is see-through: it casts none
        behind = self._behind(size, cam, sun)
        draw_models(self.ctx, self.fbo, solid, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, sun, shadow),
                    lambda m, cb: m.draw(cb, size, shadow is not None), clear=not behind)  # fmt: skip
        if self.sky is not None:
            self._sky(size, cam, sun)
        if water:
            self._water(size, cam, sun, water, shadow)
        self._after(size, cam, sun)
        sky = behind or self.sky is not None
        uniforms = {"flip": bool(self.ctx.flip_offscreen), "sky": sky}
        self.composite.draw(target, {"gColor": self.color, "gDepth": self.depth}, uniforms,
                            depth_func="<=" if sky else None)  # fmt: skip

    def _water(self, size, cam: gs.Camera, sun: Sun1404, water: list, shadow: tuple | None) -> None:
        """The water sheets over a copy of the scene and its depth (shader-compile.md 12 "Model water"): tested and
        written against the models' depth, lit with the sun's shadow."""
        self.copies = self.copies or SceneCopies(self.ctx)
        color, depth = self.copies.take(self.color, self.depth, size)
        for m in water:
            m.screen = {"cWaterRefractionMap": color, "cWaterDepthMap": depth}
        draw_models(self.ctx, self.fbo, water, lambda m, rp, sig, batch: m.prepare(rp, sig, batch, cam, sun, shadow),
                    lambda m, cb: m.draw(cb, size, shadow is not None), clear=False)  # fmt: skip

    def _behind(self, size, cam: gs.Camera, sun) -> bool:
        """What the frame draws before the models without depth (1701's sky); True when it drew and cleared."""
        return False

    def _after(self, size, cam: gs.Camera, sun) -> None:
        """The sea over the models, the sky and the water sheets, refracting a copy of them."""
        if self.sea is None:
            return
        self.copies = self.copies or SceneCopies(self.ctx)
        color, depth = self.copies.take(self.color, self.depth, size)
        self.sea(self.fbo, size, color, depth, self.shadow)

    def _sky(self, size, cam: gs.Camera, sun: Sun1404) -> None:
        """The sky dome over the models' depth (tested, not written), lit by the set's scattering."""
        sky = self.sky

        def fill(c: dict) -> None:
            values = {**gs.camera_values(cam), **light_values(sun), **scattering_values(sun, cam)}
            values.update(cloud_turns(sun.seconds))
            gs.set_values(c.values(), values)

        draw_pass(self.ctx, self.fbo, lambda rp, signature, batch: sky.draw_call.prepare(rp, signature, batch,
                  sky.maps(), fill), lambda cb: sky.draw_call.draw(cb, size), clear=False)  # fmt: skip

    def _drop(self) -> None:
        for t in (self.color, self.depth, self.fbo):
            if t is not None:
                t.release()
        self.color = self.depth = self.fbo = None
        self.size = None

    def release(self) -> None:
        self._drop()
        self.composite.release()
        for part in (self.shadow_map, self.sky, self.copies):
            if part is not None:
                part.release()
        self.shadow_map = self.sky = self.copies = None
