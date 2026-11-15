"""The sky, the moving cloud shadows and the post chain of the island view's look by Anno 1800's and 2205's own
shaders: the viewer's forward pieces (``skyforward``, ``postforward``, the frame's cloud noise and sky cubes) fed by
the island's ambient setting and the view's clock.

``compile_parts`` and ``read_parts`` run in the background; ``IslandFinish(ctx, game, light, compiled, read,
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
from ...render.nativepass import Composite
from ...render.objects1800 import named_maps, plain_texture
from ...viewer.forwardpass import CLOUDS as CLOUDS_2205
from ...viewer.forwardpass import ForwardPass, sky_work

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
    makers = {"sky": lambda: sky_work(SimpleNamespace(shaders=source)),
              "post": lambda: postforward.compile_chain(source, postforward.CHAINS[game])}  # fmt: skip
    if EYE[game]:
        makers["eye"] = lambda: eyeforward.compile_stages(source)
    made = each_made(makers)
    return {k: f"{type(v).__name__}: {v}" if isinstance(v, Exception) else v for k, v in made.items()}


def _cube_paths(light: ambientforward.Light, game: str) -> list[str]:
    paths = [*skyforward.box_maps(light, game).values(), light.skybox, *light.specular_cubes[:2]]
    return list(dict.fromkeys(p for p in paths if p))


def read_parts(files, light: ambientforward.Light, game: str) -> dict:
    """In the background: the setting's cubes by path (``fxcdraw.cube_faces``; None: no cube), the cloud noise and
    1800's specular table (``DdsFile``s), 1800's grade table (``postforward.lut_volume``); a file that does not read
    is left out (its stand-in then)."""
    out: dict = {"cubes": {}, "maps": {}}
    for path in _cube_paths(light, game):
        try:
            dds = ddsdecode.DdsFile(files.read(path))
            out["cubes"][path] = fxcdraw.cube_faces(dds, MIRROR_CUBES[game]) if dds.info.faces == 6 else None
        except (OSError, KeyError, ValueError):
            out["cubes"][path] = None
    maps = {"cCloudTex": CLOUDS[game]}
    if light.specular_cubes:
        maps["cPrefilteredBRDF"] = ambientforward.BRDF_1800
    out["maps"] = named_maps(files, maps)
    if light.lut:
        try:
            out["lut"] = postforward.lut_volume(files.read(light.lut))
        except (OSError, KeyError, ValueError):
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
    s = float(seconds)
    return {"cTimer": [s, 0.0, s, 0.0], "cWindDirection": list(wind), "cWindStrength": float(strength),
            "cAccumulatedWind": [wind[0] * strength * s, wind[1] * strength * s]}  # fmt: skip


def cloud_deltas(light: ambientforward.Light, seconds: float, wind=WIND) -> list[float]:
    """``cCloudShadowsDeltas``: how far each of the noise's two layers moved by ``seconds`` (``CalculateCloudShadowNoise``
    reads at ``positionWS.xz x 0.01 x scale - deltas.xy`` and ``x 0.015 x scale - deltas.zw``): the setting's speed
    (``CloudShadowParams.y``) along ``wind``, both layers moving the same distance over the ground [E: the engine's]."""
    scale, speed = light.clouds[0], light.clouds[1]
    far = seconds * speed * CLOUD_WIND
    out = []
    for k in (0.01, 0.015):
        out += [far * wind[0] * k * scale, far * wind[1] * k * scale]
    return out


class _Pass:
    """What the viewer's ``ForwardPass.view_light`` reads of its pass: the game, the post chain on, the setting."""

    def __init__(self, game: str, light: ambientforward.Light) -> None:
        self.GAME, self._source = game, SimpleNamespace(light=light)

    def _on(self, _part: str) -> bool:
        return True

    def why_not(self) -> str:
        return ""

    def _ready(self):
        return self._source


class IslandFinish:
    """The look's sky, cloud noise and post chain (module docstring); ``compiled``: ``compile_parts``' result,
    ``read``: ``read_parts``'; ``said(text)`` says a part that did not compile once."""

    def __init__(self, ctx, game: str, light, compiled: dict, read: dict, said) -> None:
        self.ctx, self.game, self.light, self.read = ctx, game, light, read
        self.wind = read.get("wind", WIND_STRENGTH)  # the setting's (``read_wind``), kept past the uploads
        self.dummies = dummies = fxcdraw.Dummies(ctx.rhi)
        sky, post = compiled.get("sky"), compiled.get("post")
        self.sky = skyforward.SkyForward(ctx, *sky, dummies) if isinstance(sky, tuple) else None
        self.chain = chain = postforward.CHAINS[game]
        self.post = postforward.PostChain(ctx, post, dummies, chain) if isinstance(post, dict) else None
        if isinstance(sky, str):  # the view's own sky then
            said(tr("the game's visible sky and haze: {reason}", reason=sky[:300]))
        if isinstance(post, str):  # the view's own finish then
            said(tr("the game's post chain: {reason}", reason=post[:300]))
        eye = compiled.get("eye")
        if isinstance(eye, str):  # the chain without it then
            said(tr("the game's eye adaptation: {reason}", reason=eye[:300]))
        self.eye = eyeforward.EyeAdaptation1800(ctx.rhi, eye) if isinstance(eye, dict) and self.post else None
        self.eye_time: float | None = None  # when it last ran (its easing's time step)
        self.again = False  # the exposure read back changed: the view draws once more
        made = self.post is not None
        self.decode = Composite(ctx, DECODE_FS) if made else None
        self.saturate = Composite(ctx, SATURATE_FS) if made and LDR_TARGET[game] else None
        self.targets: dict = {}  # name -> (texture, framebuffer, size): the clamped scene, the decoded picture
        self.textures: dict = {}  # the frame's maps by the shaders' names, made with the first uploads
        self.lut = None
        self.keep: list = []

    def uploads(self, batch) -> None:
        """The cubes, the cloud noise, the specular table and the grade table onto the device, once."""
        if self.textures or self.read is None:
            return
        cubes = {p: fxcdraw.cube_of(self.ctx, f) for p, f in self.read["cubes"].items() if f is not None}
        light = self.light
        for name, path in skyforward.box_maps(light, self.game).items():
            if path in cubes:
                self.textures[name] = cubes[path]
        if light.skybox in cubes:
            self.textures["cSkyRenderTarget"] = self.textures["cEnvironmentCubeTex"] = cubes[light.skybox]
        spec = [cubes.get(p) for p in light.specular_cubes[:2]]
        if spec and spec[0] is not None:
            self.textures.update(
                {"cPrefilteredSpecularCube0": spec[0], "cPrefilteredSpecularCube1": spec[-1] or spec[0]}
            )
        for name, dds in self.read["maps"].items():
            self.textures[name] = plain_texture(self.ctx.rhi, batch, dds)
        self.keep = list({id(t): t for t in self.textures.values()}.values())
        if "lut" in self.read:
            n = postforward.LUT_SIZE
            self.lut = self.ctx.texture3d((n, n, n), 4, self.read["lut"])
        self.read = None

    def values(self, seconds: float) -> dict:
        """The frame's values that go with the maps: the clouds' drift at ``seconds``, the clock and the wind the
        plants sway in, 1800's cubes' turn and size."""
        out = {"cCloudShadowsDeltas": cloud_deltas(self.light, seconds), **wind_values(self.wind, seconds)}
        cube = self.textures.get("cPrefilteredSpecularCube0")
        if cube is not None:
            out["cPrefilteredSpecCubeSizeLog2"] = math.log2(max(1, cube.pixelSize().width()))
            out["cMtxEnvironmentRotation"] = ambientforward.cube_turn(self.light)
            out["cPrefilteredCubeMapLerpFac"] = 0.0  # noon: the first cube alone
        return out

    def draw_sky(self, target, size: tuple[int, int], cam, values: dict) -> None:
        """The game's sky into ``target`` before the frame's models (over the view's own sky)."""
        if self.sky is not None:
            turn = skyforward.sky_values(self.light, True, MIRROR_CUBES[self.game])
            boxes = {k: v for k, v in self.textures.items() if k in skyforward.BOXES}
            self.sky.render(target, size, cam, {**values, **turn}, boxes)

    def view_light(self, lt: dict) -> dict:
        """The light the view draws its own parts with under the game's post chain (the viewer's rule,
        ``ForwardPass.view_light``); ``lt`` as it is without the chain."""
        return ForwardPass.view_light(_Pass(self.game, self.light), lt, False) if self.post is not None else lt

    def finish(self, image, size: tuple[int, int], eye: bool = True):
        """The game's post chain over the view's resolved colour (1800's eye adaptation first where ``eye``: the
        rendering quality's switch), as linear light for the view's final pass; None without the chain."""
        if self.post is None:
            return None
        values, extra = {**self.light.post, **self.light.lite, **self.chain.engine}, {}
        if self.lut is not None:
            values.update({"cUsedLUTTextures": 1, "cLUTWeights": [1.0, 0.0, 0.0]})
            extra["cLUTTexture0"] = self.lut.native
        if self.saturate is not None:
            scene, fbo = self._target("scene", size)
            self.saturate.draw(fbo, {"picture": image}, {}, depth_test=False)
            image = scene
        values.update(self._exposure(image, size, eye))
        graded = self.post.render(image, size, values, extra)
        picture, fbo = self._target("picture", size)
        self.decode.draw(fbo, {"picture": graded}, {}, depth_test=False)
        return picture

    def _exposure(self, image, size: tuple[int, int], on: bool) -> dict:
        """The eye adaptation over the scene (the viewer's ``ForwardPass._exposure``): the chain's values for the
        exposure read back last frame, none while it is off or did not compile."""
        if self.eye is None or self.eye.error or not on:
            self.eye_time, self.again = None, False
            return {}
        before = self.eye.value
        self.again = settling(self.eye.take(), self.eye.readback is not None, before, self.eye.value)
        now = time.monotonic()
        step = None if self.eye_time is None else now - self.eye_time
        if step is None or step > eyeadapt.SETTLE:
            self.eye.restart()
        self.eye_time = now
        self.eye.run(self.ctx, image, size, step)
        return {"cUseEyeAdaptation": 1, "cExposureBuffer[0]": self.eye.value}

    def _target(self, name: str, size: tuple[int, int]) -> tuple:
        """A half-float target of ``size`` (texture, framebuffer), made again with the size."""
        found = self.targets.get(name)
        if found is None or found[2] != tuple(size):
            self._drop(name)
            tex = self.ctx.texture(tuple(size), 4, dtype="f2")
            found = self.targets[name] = (tex, self.ctx.framebuffer([tex]), tuple(size))
        return found[0], found[1]

    def _drop(self, name: str) -> None:
        tex, fbo, _size = self.targets.pop(name, (None, None, None))
        for t in (fbo, tex):
            if t is not None:
                t.release()

    def release(self) -> None:
        for name in list(self.targets):
            self._drop(name)
        for part in (self.sky, self.post, self.decode, self.saturate, self.lut, self.eye):
            if part is not None:
                part.release()
        for tex in self.keep:
            self.ctx.later(tex)
        self.dummies.release(self.ctx)
        self.sky = self.post = self.decode = self.saturate = self.lut = self.eye = None
        self.textures, self.keep = {}, []
