"""Anno 2070's default post sequence (``posteffects.pfx`` ``SEQ_DEFAULT_HDR``) by its own shaders over the view's
picture: the bright pass with the G-buffer's glow, two blurs at half and two at a quarter of the size, the combine
with the filmic tone curve, then the mood colour; the result is linear, the view's final pass encodes it.

``CPostChain2070(ctx, stages, dummies)`` is the entry (``stages``: the step -> ``SPassStages``, see ``STEPS``);
``render(source, glow, size_px, values)`` records it. docs/rendering/shader-compile.md section 10, "Bloom and post".
"""

from __future__ import annotations

from .fxcdraw import CScreenChain
from .gamedraw import CProgram
from .nativepass import CComposite

FX = "data/system/shaders/fx/"
# the sequence's steps: (name, effect, technique, size divisor, inputs by texture name, cUsrConstant1 of the pfx)
STEPS = (
    ("bright", "postfx_hdr_bright_p3t2.fx", "Object", 1, {"cTexture0": "scene", "cTexture1": "glow"}, None),
    ("blur_x", "postfx_hdr_bloomblur_p3t2.fx", "Object", 2, {"cTexture0": "bright"}, (0.5, 0.0)),
    ("blur_y", "postfx_hdr_bloomblur_p3t2.fx", "Object", 2, {"cTexture0": "blur_x"}, (0.0, 0.5)),
    ("blur_x_2", "postfx_hdr_bloomblur_p3t2.fx", "Object", 4, {"cTexture0": "blur_y"}, (0.5, 0.0)),
    ("blur_y_2", "postfx_hdr_bloomblur_p3t2.fx", "Object", 4, {"cTexture0": "blur_x_2"}, (0.0, 0.5)),
    ("combine", "postfx_hdr_bloomcombine_p3t2.fx", "Object", 1,
     {"cTexture0": "scene", "cTexture1": "blur_y", "cTexture2": "blur_y_2"}, None),
    ("colorize", "postfx_colorize.fx", "Colorize", 1, {"cTexture0": "combine"}, None),
)  # fmt: skip
# Anno 1404's default sequence of shader model 4 (its posteffects.pfx ``SEQ_DEFAULT``) as a viewer runs it: the glow
# and heat steps read targets only effects draw into (none in a model viewer: they leave the scene as it is), so the
# colorize step is what remains (docs/rendering/shader-compile.md section 12)
STEPS_1404 = (("colorize", "postfx_colorize.fx", "Colorize", 1, {"cTexture0": "scene"}, None),)
# the view's picture is linear at its full value, the game's scene leaves its lighting halved (LOW_QUALITY_HDR); the
# G-buffer's glow is a target of the device's own, stored the other way up against the view's pictures where it flips
COPY_FS = """
#version 330
uniform sampler2D gSource; uniform bool flip; uniform float scale;
in vec2 vNdc; out vec4 color;
void main() {
    vec2 uv = vNdc * 0.5 + 0.5;
    if (flip) uv.y = 1.0 - uv.y;
    color = vec4(texture(gSource, uv).rgb * scale, 1.0);
}
"""


def compile_steps(shaders, steps: tuple = STEPS) -> dict:
    """In the background: every step's ``SPassStages`` (``shaders``: a ``shader2070.CSource2070``)."""
    return {name: shaders.effect_pass(FX + effect_file, (), technique) for name, effect_file, technique, *_ in steps}


class CPostChain2070(CScreenChain):
    """The sequence's draws, each into a half-float target of its size (module docstring); ``steps`` another game's
    (``STEPS_1404``), ``scale`` what the scene is multiplied by first (2070's is stored halved)."""

    def __init__(self, ctx, stages: dict, dummies, steps: tuple = STEPS, scale: float = 0.5) -> None:
        super().__init__(
            ctx, {name: CProgram(stages[name].vertex_stage, stages[name].pixel_stage) for name, *_ in steps}, dummies
        )
        self.m_steps, self.m_scale = steps, scale
        self.m_copy = CComposite(ctx, COPY_FS)

    def render(self, source, glow, size_px: tuple[int, int], values: dict):
        """The sequence over ``source`` (the view's resolved picture), ``glow`` the G-buffer's glow target (or None);
        ``values``: ``cPostBlur`` and ``cMoodColor``. Returns the finished, linear texture (the view's way up)."""
        self.ensure_targets(
            size_px, {"scene": 1, "glow": 1, **{name: divisor for name, _fx, _t, divisor, *_ in self.m_steps}}
        )
        made = {}
        for name, picture, flip, scale in (
            ("scene", source, False, self.m_scale),
            ("glow", glow, self.m_ctx.m_flip_offscreen, 1.0),
        ):
            if picture is None:
                continue
            texture, fbo, _ = self.m_targets[name]
            self.m_copy.draw(fbo, {"gSource": picture}, {"flip": bool(flip), "scale": scale}, depth_test=False)
            made[name] = texture.native
        for name, _fx, _t, _div, inputs, direction in self.m_steps:
            target_size = self.target_size(name)
            maps = {slot: made[what] for slot, what in inputs.items() if made.get(what) is not None}
            texel = [1.0 / target_size[0], 1.0 / target_size[1], *target_size]  # the pass's pixel (inferred)
            frame = {**values, "cUsrConstant0": texel, "cUsrConstant1": [*(direction or (0.0, 0.0)), 0.0, 0.0]}
            made[name] = self.run(name, maps, frame).native
        return self.m_targets[self.m_steps[-1][0]][0]

    def release(self) -> None:
        super().release()
        self.m_copy.release()
