"""The light of the viewer's own look on the model renderer: the frame's light (``render.light``; in the game's-shaders
look the light its pass hands the view), its uniforms put into a program and the config's lights packed for the plain
shading (``plainshading.light_uniforms``).

``ModelLights`` is a mixin of ``modelrender.ModelRenderer``, which has the ``box`` of the scene, its ``scene``, the
``place`` of a part, the ``game_pass`` and the ``_dummy_shadow`` texture these read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ..render.camera import MIRROR, gl
from ..render.light import light
from . import plainshading

if TYPE_CHECKING:
    from .modelrender import ViewState

SHADERS = "shaders"  # the look of the game's own shaders (nativelook.NATIVE)
NIGHT = 0.06  # the sun and the sky light at night, against the lights


def under(node: str, roots) -> bool:
    """Whether a part's node lies at or below one of the node paths ``roots``."""
    return any(node == r or node.startswith(r + "/") for r in roots if r is not None)


@dataclass
class _Look:
    """The fields of the island view's options ``render.light.light`` reads."""

    look2070: bool = False
    underwater: bool = False
    caustics: bool = False
    game_light: bool = False
    scheme: str = "light"


class ModelLights:
    """What the renderer needs for the light of a frame (module docstring)."""

    def _light(self, state: ViewState) -> dict:
        """The frame's light: the editor's in our plain look; in the game's-shaders look the game's (a pass that
        finishes as 2070 does takes 2070's), changed by a pass that lights the view itself (``view_light``)."""
        shaders = state.look == SHADERS
        old = shaders and getattr(self.game_pass, "look2070", False)  # a pass that finishes as 2070
        look = _Look(look2070=old, game_light=shaders and not old, scheme=state.scheme)
        size = float(np.linalg.norm(self.box[1] - self.box[0])) if self.box is not None else 4.0
        lt = light(look, max(size, 1.0) * 40)
        lt["uwTime"] = 0.0
        view_light = getattr(self.game_pass, "view_light", None) if shaders else None
        return view_light(lt, state.night) if view_light is not None else lt

    def _put_light(self, prog, lt: dict, eye, shadow) -> None:
        def put(name, value):
            if name in prog:
                prog[name].value = value

        for name in ("sunColor", "ambientMode", "fogColor"):
            put(name, lt[name])
        put("sunDir", tuple(float(v) for v in lt["sunDir"]))
        for name, default in (("hemiSky", (0, 0, 0)), ("hemiGround", (0, 0, 0)), ("envLight", 0.0),
                              ("flatAmbient", (0, 0, 0)), ("shFactor", 0.0), ("uwLight", (0, 0, 0))):  # fmt: skip
            put(name, lt.get(name, default))
        if "sh" in lt and "sh" in prog:
            prog["sh"].write(np.asarray(lt["sh"], "f4").tobytes())
        near, far = lt.get("fog", (1e5, 2e5))
        put("fogNear", near)
        put("fogFar", far)
        put("eye", tuple(float(v) for v in eye))
        put("uwFx", (0.0, 0.0))
        put("shadowOn", shadow is not None)
        if "shadowMatrix" in prog:
            prog["shadowMatrix"].write(gl(shadow[1] if shadow is not None else np.identity(4)))
        if "shadowMap" in prog:
            (shadow[0] if shadow is not None else self._dummy_shadow).use(7)
            prog["shadowMap"].value = 7

    def _light_set(self, state: ViewState):
        """The config's lights this frame, packed for the plain shading inside the game's-shaders look (none in our
        plain look as a look of its own: the editor's light has none)."""
        if state.look != SHADERS or self.scene is None or not self.scene.lights:
            return (0, b"", b"", b"")
        lights, places = [], []
        for lamp in self.scene.lights:
            place = self.place(lamp, state)
            if place is not None and state.shows(lamp):
                lights.append(lamp)
                places.append(MIRROR @ place)
        middle = (self.box[0] + self.box[1]) / 2 if self.box is not None else np.zeros(3)
        return plainshading.light_uniforms(lights, places, state.light_color, middle)
