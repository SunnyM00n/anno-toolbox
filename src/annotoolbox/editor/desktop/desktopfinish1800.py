"""The sky, the moving cloud shadows and the post chain of the island view's look by Anno 1800's and 2205's own
shaders: the viewer's forward pieces (``skyforward``, ``postforward``, the frame's cloud noise and sky cubes) fed by
the island's ambient setting and the view's clock.

``compile_parts`` and ``read_parts`` run in the background; ``CIslandFinish(ctx, game, light, compiled, read,
dummies)`` hands per frame the frame's ``maps`` and ``values(seconds)``, draws the ``sky`` and ``finish``es the
frame; docs/rendering/island-1800-2205-view.md 14.
"""

from __future__ import annotations

import math
import re
import time
from types import SimpleNamespace

from ...infra.i18n import tr
from ...models import ddsdecode
from ...render import ambientforward, eyeadapt, eyeforward, fxcdraw, postforward, skyforward
from ...render.nativepass import CComposite
from ...render.objects1800 import named_maps, plain_texture
from ...viewer.forwardpass import CLOUDS as CLOUDS_2205
from ...viewer.forwardpass import CForwardPass, sky_work

# the cloud noise (white, the noise in alpha) the frame reads as cCloudTex: 2205's the viewer's; 1800's the
# environment's cloud layer, its own noise of the same kind (constants.fxh "cloud shadows & cloud layer") [I]
CLOUDS = {"anno2205": CLOUDS_2205, "anno1800": "data/graphics/effects/environment/cloud_layer_0.dds"}
CLOUD_WIND = 2.0  # world units a second the clouds move at the setting's speed 1 [ours: the engine's wind not read]
WIND = (1.0, 0.0)  # the wind's direction in the view's axes, the clouds' and the plants' [ours: the session's not read]
WIND_STRENGTH = 1.0  # a setting without WindStrength [ours]
SETTLED = 0.002  # the exposure's change that is no longer seen: less than a step of an 8-bit channel [ours]
MIRROR_CUBES = {"anno2205": True, "anno1800": False}  # the viewer's choice per game (its turn carries the mirror)
# the chain's encoded picture as linear light: the view's final pass encodes by sRGB again (its finish 3)
DECODE_FS = """
#version 330
uniform sampler2D picture;
in vec2 vNdc; out vec4 color;
void main() {
    vec3 c = clamp(texelFetch(picture, ivec2(gl_FragCoord.xy), 0).rgb, 0.0, 1.0);
    color = vec4(mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c)), 1.0);
}
"""
# 1800's scene as its colour target holds it: compiled without HDR_ENABLED, its shaders store a quarter of the light
# by CompressLDR into a target of 0..1 (functions.fxh) [I: the target's format not read]; a sparkle above it would
# smear through the bloom's blurs as streaks
SATURATE_FS = """
#version 330
uniform sampler2D picture;
in vec2 vNdc; out vec4 color;
void main() { color = clamp(texelFetch(picture, ivec2(gl_FragCoord.xy), 0), 0.0, 1.0); }
"""
LDR_TARGET = {"anno1800": True, "anno2205": False}
EYE = {"anno1800": True, "anno2205": False}  # the game's eye adaptation before its chain (the viewer's ``EYE``)


def each_made(makers: dict) -> dict:
    """Each part of the look by key (``makers``: key -> its making, in the background), or the exception it raised:
    the look draws on without that part and says why once."""
    out: dict = {}
    for key, make in makers.items():
        try:
            out[key] = make()
        except Exception as exc:  # the look draws on without it
            out[key] = exc
    return out


def compile_parts(source, game: str) -> dict:
    """In the background: the sky's technique (``sky``), the post chain's stages (``post``) and 1800's eye
    adaptation (``eye``), each by key, or why it did not compile (a string: said once, the view's own sky or finish
    then, the chain without the eye adaptation)."""
    makers = {"sky": lambda: sky_work(SimpleNamespace(m_shaders=source)),
              "post": lambda: postforward.compile_chain(source, postforward.CHAINS[game])}  # fmt: skip
    if EYE[game]:
        makers["eye"] = lambda: eyeforward.compile_stages(source)
    made = each_made(makers)
    return {
        k: f"{type(outcome).__name__}: {outcome}" if isinstance(outcome, Exception) else outcome
        for k, outcome in made.items()
    }


def _cube_paths(light: ambientforward.SLight, game: str) -> list[str]:
    paths = [*skyforward.box_maps(light, game).values(), light.skybox, *light.specular_cubes[:2]]
    return list(dict.fromkeys(path for path in paths if path))


def read_parts(files, light: ambientforward.SLight, game: str) -> dict:
    """In the background: the setting's cubes by path (``fxcdraw.cube_faces``; None: no cube), the cloud noise and
    1800's specular table (``CDdsFile``s), 1800's grade table (``postforward.lut_volume``); a file that does not read
    is left out (its stand-in then)."""
    out: dict = {"cubes": {}, "maps": {}}
    for path in _cube_paths(light, game):
        try:
            dds = ddsdecode.CDdsFile(files.read(path))
            out["cubes"][path] = fxcdraw.cube_faces(dds, MIRROR_CUBES[game]) if dds.m_info.faces == 6 else None
        except (OSError, KeyError, ValueError):
            out["cubes"][path] = None
    maps = {"cCloudTex": CLOUDS[game]}
    if light.specular_cubes:
        maps["cPrefilteredBRDF"] = ambientforward.BRDF_1800
    out["maps"] = named_maps(files, maps)
    if light.lut:
        try:
            out["lut"] = postforward.lut_volume(files.read(light.lut))
        except (OSError, KeyError, ValueError):  # a grade that does not read: the picture stays ungraded
            pass
    return out


def settling(changed: bool, pending: bool, before: float, after: float) -> bool:
    """Whether the view draws once more for the exposure: a read-back on its way, or a change of more than
    ``SETTLED`` of it (the moving clouds and plants change it a little every frame: an exact compare never settled)."""
    return pending or (changed and abs(after - before) > SETTLED * max(abs(before), abs(after), 1e-6))


def read_wind(text: str) -> float:
    """The local ambient setting's ``WindStrength`` (``WIND_STRENGTH`` without one)."""
    found = re.search(r"<WindStrength>\s*([-+0-9.eE]+)\s*<", text)
    return float(found.group(1)) if found else WIND_STRENGTH


def wind_values(strength: float, seconds: float, wind=WIND) -> dict:
    """The frame's clock and wind as the plants' and fields' vertex shaders read them (``ComputeWind``,
    ``ComputeFieldWind``): ``cTimer`` the view's seconds, the setting's strength along ``wind`` [E: the session's
    direction], ``cAccumulatedWind`` their integral at a steady wind (``constants.fxh``)."""
    clock = float(seconds)
    return {"cTimer": [clock, 0.0, clock, 0.0], "cWindDirection": list(wind), "cWindStrength": float(strength),
            "cAccumulatedWind": [wind[0] * strength * clock, wind[1] * strength * clock]}  # fmt: skip


def cloud_deltas(light: ambientforward.SLight, seconds: float, wind=WIND) -> list[float]:
    """``cCloudShadowsDeltas``: how far each of the noise's two layers moved by ``seconds`` (``CalculateCloudShadowNoise``
    reads at ``positionWS.xz x 0.01 x scale - deltas.xy`` and ``x 0.015 x scale - deltas.zw``): the setting's speed
    (``CloudShadowParams.y``) along ``wind``, both layers moving the same distance over the ground [E: the engine's]."""
    scale, speed_per_s = light.clouds[0], light.clouds[1]
    far = seconds * speed_per_s * CLOUD_WIND
    out = []
    for k in (0.01, 0.015):
        out += [far * wind[0] * k * scale, far * wind[1] * k * scale]
    return out


class _CPass:
    """What the viewer's ``CForwardPass.view_light`` reads of its pass: the game, the post chain on, the setting."""

    def __init__(self, game: str, light: ambientforward.SLight) -> None:
        self.GAME, self._m_source = game, SimpleNamespace(m_light=light)

    def _on(self, _part: str) -> bool:
        return True

    def why_not(self) -> str:
        return ""

    def _ready(self):
        return self._m_source


class CIslandFinish:
    """The look's sky, cloud noise and post chain (module docstring); ``compiled``: ``compile_parts``' result,
    ``read``: ``read_parts``'; ``said(text)`` says a part that did not compile once."""

    def __init__(self, ctx, game: str, light, compiled: dict, read: dict, said) -> None:
        self.m_ctx, self.m_game, self.m_light, self.m_read = ctx, game, light, read
        self.m_wind = read.get("wind", WIND_STRENGTH)  # the setting's (``read_wind``), kept past the uploads
        self.m_dummies = dummies = fxcdraw.CDummies(ctx.m_rhi)
        sky, post = compiled.get("sky"), compiled.get("post")
        self.m_sky = skyforward.CSkyForward(ctx, *sky, dummies) if isinstance(sky, tuple) else None
        self.m_chain = chain = postforward.CHAINS[game]
        self.m_post = postforward.CPostChain(ctx, post, dummies, chain) if isinstance(post, dict) else None
        if isinstance(sky, str):  # the view's own sky then
            said(tr("islands.games_visible_sky_haze", reason=sky[:300]))
        if isinstance(post, str):  # the view's own finish then
            said(tr("islands.games_post_chain", reason=post[:300]))
        eye = compiled.get("eye")
        if isinstance(eye, str):  # the chain without it then
            said(tr("islands.games_eye_adaptation", reason=eye[:300]))
        self.m_eye = eyeforward.CEyeAdaptation1800(ctx.m_rhi, eye) if isinstance(eye, dict) and self.m_post else None
        self.m_eye_time: float | None = None  # when it last ran (its easing's time step)
        self.m_again = False  # the exposure read back changed: the view draws once more
        made = self.m_post is not None
        self.m_decode = CComposite(ctx, DECODE_FS) if made else None
        self.m_saturate = CComposite(ctx, SATURATE_FS) if made and LDR_TARGET[game] else None
        self.m_targets: dict = {}  # name -> (texture, framebuffer, size): the clamped scene, the decoded picture
        self.m_textures: dict = {}  # the frame's maps by the shaders' names, made with the first uploads
        self.m_lut = None
        self.m_keep: list = []

    def uploads(self, batch) -> None:
        """The cubes, the cloud noise, the specular table and the grade table onto the device, once."""
        if self.m_textures or self.m_read is None:
            return
        cubes = {
            path: fxcdraw.cube_of(self.m_ctx, faces)
            for path, faces in self.m_read["cubes"].items()
            if faces is not None
        }
        light = self.m_light
        for name, path in skyforward.box_maps(light, self.m_game).items():
            if path in cubes:
                self.m_textures[name] = cubes[path]
        if light.skybox in cubes:
            self.m_textures["cSkyRenderTarget"] = self.m_textures["cEnvironmentCubeTex"] = cubes[light.skybox]
        specular = [cubes.get(path) for path in light.specular_cubes[:2]]
        if specular and specular[0] is not None:
            self.m_textures.update(
                {"cPrefilteredSpecularCube0": specular[0], "cPrefilteredSpecularCube1": specular[-1] or specular[0]}
            )
        for name, dds in self.m_read["maps"].items():
            self.m_textures[name] = plain_texture(self.m_ctx.m_rhi, batch, dds)
        self.m_keep = list({id(texture): texture for texture in self.m_textures.values()}.values())
        if "lut" in self.m_read:
            lut_size = postforward.LUT_SIZE
            self.m_lut = self.m_ctx.texture3d((lut_size, lut_size, lut_size), 4, self.m_read["lut"])
        self.m_read = None

    def values(self, seconds: float) -> dict:
        """The frame's values that go with the maps: the clouds' drift at ``seconds``, the clock and the wind the
        plants sway in, 1800's cubes' turn and size."""
        out = {"cCloudShadowsDeltas": cloud_deltas(self.m_light, seconds), **wind_values(self.m_wind, seconds)}
        cube = self.m_textures.get("cPrefilteredSpecularCube0")
        if cube is not None:
            out["cPrefilteredSpecCubeSizeLog2"] = math.log2(max(1, cube.pixelSize().width()))
            out["cMtxEnvironmentRotation"] = ambientforward.cube_turn(self.m_light)
            out["cPrefilteredCubeMapLerpFac"] = 0.0  # noon: the first cube alone
        return out

    def draw_sky(self, target, size_px: tuple[int, int], camera, values: dict) -> None:
        """The game's sky into ``target`` before the frame's models (over the view's own sky)."""
        if self.m_sky is not None:
            turn = skyforward.sky_values(self.m_light, True, MIRROR_CUBES[self.m_game])
            boxes = {name: texture for name, texture in self.m_textures.items() if name in skyforward.BOXES}
            self.m_sky.render(target, size_px, camera, {**values, **turn}, boxes)

    def view_light(self, light_values: dict) -> dict:
        """The light the view draws its own parts with under the game's post chain (the viewer's rule,
        ``CForwardPass.view_light``); ``light_values`` as it is without the chain."""
        return (
            CForwardPass.view_light(_CPass(self.m_game, self.m_light), light_values, False)
            if self.m_post is not None
            else light_values
        )

    def finish(self, image, size_px: tuple[int, int], eye_enabled: bool = True):
        """The game's post chain over the view's resolved colour (1800's eye adaptation first where ``eye``: the
        rendering quality's switch), as linear light for the view's final pass; None without the chain."""
        if self.m_post is None:
            return None
        values, extra = {**self.m_light.post, **self.m_light.lite, **self.m_chain.engine}, {}
        if self.m_lut is not None:
            values.update({"cUsedLUTTextures": 1, "cLUTWeights": [1.0, 0.0, 0.0]})
            extra["cLUTTexture0"] = self.m_lut.native
        if self.m_saturate is not None:
            scene, framebuffer = self._target("scene", size_px)
            self.m_saturate.draw(framebuffer, {"picture": image}, {}, depth_test=False)
            image = scene
        values.update(self._exposure(image, size_px, eye_enabled))
        graded = self.m_post.render(image, size_px, values, extra)
        picture, framebuffer = self._target("picture", size_px)
        self.m_decode.draw(framebuffer, {"picture": graded}, {}, depth_test=False)
        return picture

    def _exposure(self, image, size_px: tuple[int, int], is_enabled: bool) -> dict:
        """The eye adaptation over the scene (the viewer's ``CForwardPass._exposure``): the chain's values for the
        exposure read back last frame, none while it is off or did not compile."""
        if self.m_eye is None or self.m_eye.m_error or not is_enabled:
            self.m_eye_time, self.m_again = None, False
            return {}
        before = self.m_eye.m_value
        self.m_again = settling(self.m_eye.take(), self.m_eye.m_readback is not None, before, self.m_eye.m_value)
        now = time.monotonic()
        step = None if self.m_eye_time is None else now - self.m_eye_time
        if step is None or step > eyeadapt.SETTLE:
            self.m_eye.restart()
        self.m_eye_time = now
        self.m_eye.run(self.m_ctx, image, size_px, step)
        return {"cUseEyeAdaptation": 1, "cExposureBuffer[0]": self.m_eye.m_value}

    def _target(self, name: str, size_px: tuple[int, int]) -> tuple:
        """A half-float target of ``size_px`` (texture, framebuffer), made again with the size."""
        found = self.m_targets.get(name)
        if found is None or found[2] != tuple(size_px):
            self._drop(name)
            texture = self.m_ctx.texture(tuple(size_px), 4, dtype="f2")
            found = self.m_targets[name] = (texture, self.m_ctx.framebuffer([texture]), tuple(size_px))
        return found[0], found[1]

    def _drop(self, name: str) -> None:
        texture, framebuffer, _size = self.m_targets.pop(name, (None, None, None))
        for target in (framebuffer, texture):
            if target is not None:
                target.release()

    def release(self) -> None:
        for name in list(self.m_targets):
            self._drop(name)
        for part in (self.m_sky, self.m_post, self.m_decode, self.m_saturate, self.m_lut, self.m_eye):
            if part is not None:
                part.release()
        for texture in self.m_keep:
            self.m_ctx.later(texture)
        self.m_dummies.release(self.m_ctx)
        self.m_sky = self.m_post = self.m_decode = self.m_saturate = self.m_lut = self.m_eye = None
        self.m_textures, self.m_keep = {}, []
