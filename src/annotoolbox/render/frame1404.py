"""Anno 1404's models drawn forward-lit by the game's own shaders on QRhi (Direct3D 11): ``model.fx``'s
``StandardShadow`` (``WithoutShadow`` without casters; lit per vertex in gamma values, hazed by ``cbScattering``) over
the sun's shadow map of its ``Obj2Shadow``, ``sky.fx``'s ``Sky`` behind them, composited as linear light with depth.

``CFrame1404(ctx)`` is the entry: ``render(target, size_px, models, camera, sun)``; ``CModel1404`` is one mesh group,
``SSun1404`` the light and the atmosphere, ``scattering_values`` the engine's scattering constants, ``granny_mesh`` the
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
from .frame2070 import CMeshModel, sun_shadow
from .frameforward import CSceneCopies
from .fxcdraw import CFxcDraw
from .gamedraw import CMesh
from .nativepass import CComposite, draw_models, draw_pass
from .sky2070 import cloud_turns

TextureFormat = QRhiTexture.Format
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
class SSun1404:
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
    return tuple(float(group) for group in found.groups() if group is not None) if found else None


def number_in(body: str, tag: str, fallback: float) -> float:
    """The number the element ``tag`` in ``body`` holds, ``fallback`` without it."""
    found = re.search(rf"<{tag}>\s*{NUMBER}\s*</{tag}>", body)
    return float(found.group(1)) if found else fallback


def read_sun(text: str, name: str = SETTING) -> SSun1404:
    """The light and atmosphere of a set of ``ambientmap.xml`` (colours times their power, gamma values); a value the
    set lacks keeps ``SSun1404``'s."""
    body = profile_block(text, name)
    base = SSun1404((0.0, 1.0, 0.0))

    def colour(tag: str, fallback: tuple, within: str = body, power: str | None = None, alpha: bool = False) -> tuple:
        found = colour_in(within, tag)
        if found is None:
            return fallback
        k = number_in(body, power or tag + "Power", 1.0)
        rgb = tuple(channel * k for channel in found[:3])
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


def scattering_values(sun: SSun1404, camera: gs.SCamera) -> dict:
    """``cbScattering`` as the engine's setter fills it from the set's atmosphere: Rayleigh by the density, Mie by
    the turbidity, their phase-weighted and reciprocal sums, the Henyey-Greenstein terms of ``g``, the haze's range
    from ``ScatterStart`` of the view's depth (the engine's camera term is left out) and the sun colour."""
    ray = np.array(RAYLEIGH) / sun.density
    mie = np.array([scale * sun.turbidity - offset for scale, offset in MIE])
    start = sun.scatter_start * (camera.far - camera.near) + camera.near
    return {"cBetaRay": ray, "cBetaMie": mie, "cBetaDashRay": ray * RAYLEIGH_PHASE, "cBetaDashMie": mie * MIE_PHASE,
            "cReciBetaRayMie": 1.0 / (ray + mie), "cHGParameters": [1 - sun.g**2, 1 + sun.g**2, 2 * sun.g],
            "cDepthSca": [start, camera.far, 1.0 / max(1e-6, camera.far - start)], "cSunColor": list(sun.sun_color),
            "cFogColorHorizon": list(sun.fog_horizon), "cFogColorZenith": list(sun.fog_zenith), **OPTICAL_DEPTHS}  # fmt: skip


def granny_mesh(vertices: np.ndarray, indices, tangents: bool = False, uvs: int = 1) -> CMesh:
    """A Granny mesh's vertices (position, normal, uv per row, as ``viewer.modelfiles`` reads them) with the indices of
    one group, as the ``p3n3t2`` layout the shader takes, or (``tangents``) ``p3n3g3b3t2`` with the tangent frames
    made from the positions and uvs (``models.tangents``; stored negated, as the shader reads them); ``uvs`` 2
    gives the one uv as the second set too (1701's ``t2t2`` effects: the viewer reads one set)."""
    values = np.asarray(vertices, "<f4")[:, :8]
    index = np.asarray(indices, np.uint32)
    if not tangents:
        elements = ((0, 7, 3, 0), (1, 7, 3, 12)) + ((4, 7, 2, 24),) * uvs
        return CMesh(np.ascontiguousarray(values).tobytes(), 32, elements, index, len(values))
    _n, tan, bit = frames([tuple(position) for position in values[:, :3].tolist()], [tuple(uv) for uv in values[:, 6:8].tolist()],
                         index.tolist(), [tuple(normal) for normal in values[:, 3:6].tolist()])  # fmt: skip
    data = np.concatenate([values[:, :6], -np.asarray(tan, "<f4"), -np.asarray(bit, "<f4"), values[:, 6:8]], axis=1)
    elements = ((0, 7, 3, 0), (1, 7, 3, 12), (2, 7, 3, 24), (3, 7, 3, 36)) + ((4, 7, 2, 48),) * uvs
    return CMesh(np.ascontiguousarray(data, "<f4").tobytes(), 56, elements, index, len(values))


def posed_vertices(mesh: CMesh, skin, matrices: np.ndarray) -> bytes:
    """A ``granny_mesh``'s vertices moved by a Granny skin (``skin``: the mesh's joints and weights, ``matrices``:
    the pose of ``models.grannyrig``'s rig): the position, and the normal and tangent frame turned. The old engine's
    model shaders read no bones: Granny poses its meshes on the CPU, so the game's shader gets posed vertices too
    (shader-compile.md 12, "Granny skins")."""
    rows = np.frombuffer(mesh.m_rest or mesh.m_data, "<f4").reshape(mesh.m_count, mesh.m_stride // 4).copy()
    blend = skinning.blended(skin, matrices)
    for usage, _type, _count, offset in mesh.m_elements:
        word_offset = offset // 4
        if usage == POSITION:
            rows[:, word_offset : word_offset + 3] = (
                np.einsum("nij,nj->ni", blend[:, :3, :3], rows[:, word_offset : word_offset + 3]) + blend[:, :3, 3]
            )
        elif usage in DIRECTIONS:  # the tangent frame is stored negated: turning keeps the sign
            rows[:, word_offset : word_offset + 3] = skinning.turned(blend, rows[:, word_offset : word_offset + 3])
    return rows.tobytes()


def material_defines(diffuse: str, normal: str = "") -> tuple:
    """The switches a 1404 model material gives ``model.fx``: its diffuse map, and its normal map with the tangent
    layout (``P3N3G3B3T2``) the normal map needs."""
    out = (("DIFFUSE_ENABLED", "1"),) if diffuse else ()
    return out + ((("P3N3G3B3T2", "1"), ("NORMAL_ENABLED", "1")) if normal else ())


def is_water(values: dict) -> bool:
    """Whether a material (its values) is a model's water sheet: shader index 1 of the model owner's table."""
    return values.get("Owner", "MODEL") == "MODEL" and int(values.get("ShaderIndex", 0)) == WATER_INDEX


def light_values(sun: SSun1404) -> dict:
    """``cbLight``, ``cbObject`` and ``cbWater`` by name: the sun, white objects without damage, no water depth. The
    matrices go in as ours (row vectors): 1404's are declared column-major, so the shader's ``mul(M, v)`` reads them
    turned."""
    toward = np.asarray(sun.direction, float)
    return {"cLgtAmbient": [*sun.ambient, 1.0], "cLgtDiffuse": [*sun.diffuse, 1.0], "cLgtDirection": [*-toward, 0.0],
            "cLgtDirectionInv": [*toward, 0.0], "cObjectColor": [1.0, 1.0, 1.0, 1.0], "cObjectDamage": INTACT,
            "cWaterDepth": [0.0] * 4}  # fmt: skip


def as_is(matrix: np.ndarray) -> np.ndarray:
    """A matrix of ours as 1404's shaders take it (module docstring of ``light_values``)."""
    return np.asarray(matrix, float)


class CModel1404(CMeshModel):
    """One mesh group: ``draw`` (a ``CFxcDraw`` of ``WithoutShadow``), ``shadowed`` its ``StandardShadow`` and
    ``shadow`` its ``Obj2Shadow`` draw (None: not compiled), ``maps`` (texture name -> QRhi texture or None while it
    loads), ``world`` (column vectors, set per frame), ``color`` and ``alpha`` (the object colour: the player's on a
    dye material, a water sheet's own)."""

    FALLBACKS = FALLBACKS
    m_refracts = False  # drawn after the others over a copy of the scene behind it (``CWater1404``)

    def __init__(self, draw: CFxcDraw, shadowed: CFxcDraw | None = None, shadow: CFxcDraw | None = None) -> None:
        self.m_draw_call, self.m_maps, self.m_world = draw, {}, np.identity(4)
        self.m_shadowed, self.m_shadow = shadowed, shadow
        self.m_paths: dict[str, str] = {}  # the shader's texture name -> the material's file
        self.m_color, self.m_alpha = (1.0, 1.0, 1.0), 1.0
        self.m_water = False  # a model's water sheet: View > Rendering > Water takes it out
        # drawn in the game's axes, the mirror in the camera's projection: the camera's passes swap their faces, the
        # sun's own view keeps them (shader-compile.md 11 "The game's own axes")
        for call, mirrored in ((draw, True), (shadowed, True), (shadow, False)):
            if call is not None:
                call.m_fallbacks, call.m_is_mirrored = dict(self.FALLBACKS), mirrored

    def fill_model(self, constants: dict) -> None:
        """The object's own constants: its place (and its inverse) and its colour."""
        world = np.asarray(self.m_world, float)
        gs.set_values(constants.values(), {"cMtxWorld": world.T, "cMtxWorldInv": np.linalg.inv(world).T,
                                   "cObjectColor": [*self.m_color, self.m_alpha]})  # fmt: skip

    def frame_values(self, camera: gs.SCamera, sun: SSun1404) -> dict:
        """The colour pass's constants by name besides the model's own: camera, light, haze and the clock (``cTimer``:
        the game's, the wind's and the real one all the view's time, as 2070's)."""
        seconds = sun.seconds
        return {
            **gs.camera_values(camera),
            **light_values(sun),
            **scattering_values(sun, camera),
            "cTimer": [seconds, 0.0, seconds, seconds],
        }

    def pick(self, shadowed: bool) -> CFxcDraw:
        """The colour pass's draw: the shadowed one while there is a shadow map."""
        return self.m_shadowed if shadowed and self.m_shadowed is not None else self.m_draw_call

    def prepare(
        self, render_pass, signature, batch, camera: gs.SCamera, sun: SSun1404, shadow: tuple | None = None
    ) -> None:
        """The colour pass's resources and constants; ``shadow``: ``cbShadow`` and the map, when there is one."""

        def fill(constants: dict) -> None:
            gs.set_values(constants.values(), {**self.frame_values(camera, sun), **(shadow[0] if shadow else {})})
            self.fill_model(constants)

        maps = {**self.textures(), **(shadow[1] if shadow else {})}
        self.pick(shadow is not None).prepare(render_pass, signature, batch, maps, fill)

    def draw(self, command_buffer, size_px, shadowed: bool = False) -> None:
        self.pick(shadowed).draw(command_buffer, size_px)

    def release(self) -> None:
        for call in (self.m_draw_call, self.m_shadowed, self.m_shadow):
            if call is not None:
                call.release()


class CWater1404(CModel1404):
    """A water sheet (``model_water.fx``): drawn after the models and the sky, refracting the frame's copies
    ``screen`` (``CFrame1404._water``), its depth measured in the engine's left-handed view (shader-compile.md 12)."""

    m_refracts = True

    def __init__(self, *draws) -> None:
        super().__init__(*draws)
        self.m_water, self.m_screen = True, {}

    def textures(self) -> dict:
        return {**super().textures(), **self.m_screen}

    def frame_values(self, camera: gs.SCamera, sun: SSun1404) -> dict:
        return {
            **super().frame_values(camera, sun),
            **behind_values(camera),
            "cWaterCaustic": caustic_offsets(sun.seconds),
        }


def behind_values(camera: gs.SCamera) -> dict:
    """What 1404's water reads the ground behind it with: the left-handed view (its depth ahead, as the engine's) and
    the inverse view-projection of the view turned but not moved (the engine adds the camera's place after)."""
    origin = np.array(camera.view, float)
    origin[3, :3] = 0.0
    return {"cMtxView": gs.left_handed(camera).view, "cMtxViewProjInvOrigin": np.linalg.inv(origin @ camera.projection)}


def caustic_offsets(seconds: float) -> list[float]:
    """``cWaterCaustic`` at the clock ``seconds``: the two caustic layers' scroll, 0.03 and 0.02 a second, the second
    half a period on (shader-compile.md 12 "Model water"), as the engine sets them: the map is read at the game's
    world x (shader-compile.md 11 "The game's own axes")."""
    phase_a, phase_b = (0.03 * seconds) % 1.0, (0.02 * seconds) % 1.0
    return [phase_a, phase_a, phase_b, (phase_b + 0.5) % 1.0]


class CFrame1404:
    """The offscreen colour (half float, gamma values) and depth of one view, the sun's shadow map, the sky
    (``sky``: a ``sky2070.CSkyDome`` of 1404's ``Sky``, None while it compiles or when it is off) and the composite
    (module docstring)."""

    SHADOW = {"matrix": as_is}  # sun_shadow's arguments: 1404's matrices as they are

    def __init__(self, ctx) -> None:
        self.m_ctx, self.m_size_px, self.m_color, self.m_depth, self.m_fbo = ctx, None, None, None, None
        self.m_composite = CComposite(ctx, COMPOSITE_FS)
        self.m_shadow_map: shadowforward.CShadowMap | None = None
        self.m_sky = None
        self.m_copies: CSceneCopies | None = None  # the scene the water sheets refract (made with the first sheet)
        self.m_shadow: tuple | None = None  # this frame's sun shadow (``cbShadow``'s values and the map), for _after
        self.m_beyond = None  # receivers the shadow's depth reaches too (the sea: points at its far reach)
        # the sea under the model (``ocean1404``, ``ocean1701``): draws into (fbo, size, the scene's colour and depth
        # copies, the sun's shadow) with the frame's depth
        self.m_sea = None

    def _targets(self, size_px: tuple[int, int]) -> None:
        if self.m_size_px == size_px:
            return
        self._drop()
        self.m_color = self.m_ctx.native_texture(size_px, TextureFormat.RGBA16F)
        self.m_depth = self.m_ctx.depth_renderbuffer(size_px)
        self.m_fbo = self.m_ctx.framebuffer([self.m_color], self.m_depth)
        self.m_size_px = size_px

    def _shadow(self, models: list, sun: SSun1404, is_enabled: bool) -> tuple | None:
        """The casters' shadow map, ``cbShadow`` and the map by name (None when off or without casters)."""
        casters = [model for model in models if model.m_shadow is not None and model.m_shadowed is not None]
        if not is_enabled or not casters:
            return None
        self.m_shadow_map, values, maps = sun_shadow(self.m_ctx, self.m_shadow_map, casters, -np.asarray(sun.direction),
                                                   beyond=self.m_beyond, **self.SHADOW)  # fmt: skip
        return values, maps

    def render(self, target, size_px: tuple[int, int], models: list, camera: gs.SCamera, sun: SSun1404,
               shadows: bool = True) -> None:  # fmt: skip
        """The shadow map, the models (``CModel1404``), the sky and the water sheets (``CWater1404``) into the offscreen
        target, then the composite."""
        self._targets(size_px)
        solid = [model for model in models if not model.m_refracts]
        water = [model for model in models if model.m_refracts]
        shadow = self.m_shadow = self._shadow(solid, sun, shadows)  # a sheet is see-through: it casts none
        behind = self._behind(size_px, camera, sun)
        draw_models(
            self.m_ctx,
            self.m_fbo,
            solid,
            lambda model, render_pass, signature, batch: model.prepare(
                render_pass, signature, batch, camera, sun, shadow
            ),
            lambda model, command_buffer: model.draw(command_buffer, size_px, shadow is not None),
            clear=not behind,
        )
        if self.m_sky is not None:
            self._sky(size_px, camera, sun)
        if water:
            self._water(size_px, camera, sun, water, shadow)
        self._after(size_px, camera, sun)
        sky = behind or self.m_sky is not None
        uniforms = {"flip": bool(self.m_ctx.m_flip_offscreen), "sky": sky}
        self.m_composite.draw(target, {"gColor": self.m_color, "gDepth": self.m_depth}, uniforms,
                            depth_func="<=" if sky else None)  # fmt: skip

    def _water(self, size_px, camera: gs.SCamera, sun: SSun1404, water: list, shadow: tuple | None) -> None:
        """The water sheets over a copy of the scene and its depth (shader-compile.md 12 "Model water"): tested and
        written against the models' depth, lit with the sun's shadow."""
        self.m_copies = self.m_copies or CSceneCopies(self.m_ctx)
        color, depth = self.m_copies.take(self.m_color, self.m_depth, size_px)
        for model in water:
            model.screen = {"cWaterRefractionMap": color, "cWaterDepthMap": depth}
        draw_models(
            self.m_ctx,
            self.m_fbo,
            water,
            lambda model, render_pass, signature, batch: model.prepare(
                render_pass, signature, batch, camera, sun, shadow
            ),
            lambda model, command_buffer: model.draw(command_buffer, size_px, shadow is not None),
            clear=False,
        )

    def _behind(self, size_px, camera: gs.SCamera, sun) -> bool:
        """What the frame draws before the models without depth (1701's sky); True when it drew and cleared."""
        return False

    def _after(self, size_px, camera: gs.SCamera, sun) -> None:
        """The sea over the models, the sky and the water sheets, refracting a copy of them."""
        if self.m_sea is None:
            return
        self.m_copies = self.m_copies or CSceneCopies(self.m_ctx)
        color, depth = self.m_copies.take(self.m_color, self.m_depth, size_px)
        self.m_sea(self.m_fbo, size_px, color, depth, self.m_shadow)

    def _sky(self, size_px, camera: gs.SCamera, sun: SSun1404) -> None:
        """The sky dome over the models' depth (tested, not written), lit by the set's scattering."""
        sky = self.m_sky

        def fill(constants: dict) -> None:
            values = {**gs.camera_values(camera), **light_values(sun), **scattering_values(sun, camera)}
            values.update(cloud_turns(sun.seconds))
            gs.set_values(constants.values(), values)

        draw_pass(self.m_ctx, self.m_fbo, lambda render_pass, signature, batch: sky.m_draw_call.prepare(render_pass, signature, batch,
                  sky.maps(), fill), lambda command_buffer: sky.m_draw_call.draw(command_buffer, size_px), clear=False)  # fmt: skip

    def _drop(self) -> None:
        for texture in (self.m_color, self.m_depth, self.m_fbo):
            if texture is not None:
                texture.release()
        self.m_color = self.m_depth = self.m_fbo = None
        self.m_size_px = None

    def release(self) -> None:
        self._drop()
        self.m_composite.release()
        for part in (self.m_shadow_map, self.m_sky, self.m_copies):
            if part is not None:
                part.release()
        self.m_shadow_map = self.m_sky = self.m_copies = None
