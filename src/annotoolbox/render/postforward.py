"""Anno 2205's and 1800's post chains by their own ``posteffect_aa_bloom.fx`` over the view's HDR picture: bright
pass, blurs, the combine (bloom, tone map, encoding; 2205 its image adjustments), then 1800's colour grade by the
setting's look-up table (``posteffect_colorgrade.fx``), into a target the view's final pass shows as it is.

``CHAINS`` (per game: passes, defines, the engine's fixed values), ``compile_chain``, ``PostChain(ctx, stages,
dummies, chain)`` (``render`` returns the finished texture) and ``lut_volume`` (1800's table) are the entries.
docs/rendering/shader-compile.md section 11 "Post chain" and "1800's post chain".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PySide6.QtGui import QImage

from .fxcdraw import ScreenChain
from .gamedraw import Program

FX = "data/shaders/fx/posteffect_aa_bloom.fx"
# the passes in order: (technique, the size it draws at as a divisor of the picture's, its inputs by texture name);
# every blur at the bright pass's half size: the shader's blur reads a texture of its target's size and the combine
# offsets both bloom textures by one texel size (shader-compile section 11 "Post chain")
PASSES = (
    ("Bright", 2, {"cTexture4": "source"}),
    ("BlurHorizontal", 2, {"cTexture0": "Bright"}),
    ("BlurVertical", 2, {"cTexture0": "BlurHorizontal"}),
    ("BlurHorizontalWide", 2, {"cTexture0": "BlurVertical"}),
    ("BlurVerticalWide", 2, {"cTexture0": "BlurHorizontalWide"}),
    ("BloomCombineAA", 1, {"cTexture2": "source", "cTexture1": "BlurVertical", "cTexture0": "BlurVerticalWide"}),
)
TECHNIQUES = tuple(p[0] for p in PASSES)
GRADE_FX = "data/shaders/fx/posteffect_colorgrade.fx"
LUT_SIZE = 32  # the table's cells per axis: a strip of 32 slices of 32 x 32 (the shipped color_reference.png)
# 1800: the bright pass reads the picture as cTexture2; then the colour grade over the combine's encoded picture
PASSES_1800 = (("Bright", 2, {"cTexture2": "source"}), *PASSES[1:], ("ColorGrade", 1, {"cTexture0": "BloomCombineAA"}))
# the engine's values a model viewer keeps fixed: no eye adaptation (no histogram), no darkening outside the player's
# area, no debug view; the filmic curve off, as the setting's own exposure and lite tone map are the path it fills
# (our reading **[I]**: the executable's choice was not read)
ENGINE_1800 = {
    "cUseEyeAdaptation": 0,
    "cDarkeningFactor": 1.0,
    "cLightingDisplayMode": 0,
    "cFilmicToneMapEnabled": 0.0,
    "cUsedLUTTextures": 0,
    "cLUTWeights": [0.0, 0.0, 0.0],
    "cGammaAdjust": 1.0,  # the options' gamma at its default **[E]**
    "cInvGammaAdjusted": 1 / 2.2,
}


@dataclass(frozen=True)
class Chain:
    """A game's post chain: its passes (technique, size divisor, inputs), the defines of its compile and the
    engine's fixed values."""

    passes: tuple
    defines: tuple = ()
    engine: dict = field(default_factory=dict)

    @property
    def techniques(self) -> tuple:
        return tuple(p[0] for p in self.passes)


# the share of its light a game's model shader stores: 1800 compiles without HDR_ENABLED, so its CompressLDR keeps 0-4
# as 0-1 and the chain's DecompressLDR multiplies by 4 again (functions.fxh; the view's own ground must match)
STORED = {"anno1800": 0.25}
CHAINS = {"anno2205": Chain(PASSES), "anno1800": Chain(PASSES_1800, (("BLOOM_ENABLED", ""),), ENGINE_1800)}


def compile_chain(shaders, chain: Chain) -> dict:
    """In the background: each technique's (vertex, pixel) stages from its effect file (``shaders``: the game's
    ``ForwardSource``)."""
    return {t: shaders.effect_pass(GRADE_FX if t == "ColorGrade" else FX, chain.defines, t) for t in chain.techniques}


def lut_volume(png: bytes) -> bytes:
    """The colour grade's table from a graded picture of 1800's: the 1024 x 32 strip at its top left (x: the blue
    slice times 32 plus red, y: green, as the shipped reference strip lays it out; that the engine reads it there is
    our reading **[I]**) as a 32 x 32 x 32 RGBA volume, slices by blue, rows by green."""
    img = QImage.fromData(png).convertToFormat(QImage.Format.Format_RGBA8888)
    n = LUT_SIZE
    if img.width() < n * n or img.height() < n:
        raise ValueError(f"a colour grade picture of {img.width()} x {img.height()} holds no {n * n} x {n} strip")
    rows = np.frombuffer(bytes(img.constBits()), np.uint8).reshape(img.height(), img.bytesPerLine())
    strip = rows[:n, : n * n * 4].reshape(n, n, n, 4)  # green, blue, red, rgba
    return np.ascontiguousarray(strip.transpose(1, 0, 2, 3)).tobytes()


class PostChain(ScreenChain):
    """The chain's passes, each its technique's shaders into a half-float target of its size (module docstring)."""

    def __init__(self, ctx, stages: dict, dummies, chain: Chain = CHAINS["anno2205"]) -> None:
        super().__init__(ctx, {name: Program(*stages[name]) for name in chain.techniques}, dummies)
        self.passes = chain.passes

    def render(self, source, size: tuple[int, int], values: dict, extra: dict | None = None):
        """The chain over ``source`` (the view's resolved HDR colour, ``size`` pixels); ``values``: the constants by
        name (``ambientforward.Light.post``, the frame's viewport), ``extra`` more textures by name (1800's table).
        Returns the finished (encoded) texture."""
        self.ensure_targets(size, {name: div for name, div, _inputs in self.passes})
        made = {"source": source}
        for name, _div, inputs in self.passes:
            target_size = self.target_size(name)
            maps = {**(extra or {}), **{slot: made[what].native for slot, what in inputs.items()}}
            # the blur's texel size is the viewport's (2205 names it cViewPort, 1800 cViewport): at 0 its taps went
            # to the picture's edges and an edge's glint ran up its whole column (island-1800-2205-view.md 18)
            port = [0, 0, *target_size]
            frame = {**values, "cViewPort": port, "cViewport": port, "cViewportCropFactor": [1, 1, 0, 0]}
            made[name] = self.run(name, maps, frame)
        return made[self.passes[-1][0]]
