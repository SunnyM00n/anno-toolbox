"""Anno 2205's cloth (a config's ``Clothes``) drawn by its own ``cloth.fx`` in the forward frame: the cloth's mesh as
stored (its rest pose: the engine's simulation moves the vertices, the viewer's do not), its place and colour in
``cbCloth``, its material's ``cCloth...`` maps.

``defines(values)`` is the variant, ``CClothModel`` a ``frameforward.CForwardModel`` with ``cbCloth`` filled.
docs/rendering/shader-compile.md section 11 "Cloth".
"""

from __future__ import annotations

import numpy as np

from ..models.shaderforward import switched_on
from .frameforward import CForwardModel

FX = "data/shaders/fx/cloth.fx"
TECHNIQUE = "Default"
SECTION = "Clothes"  # the config section whose parts are cloth
MAPS = ("cClothDiffuseTex", "cClothNormalTex", "cDyeMask", "cLogoAtlasTex")
OPTIONS = ("NORMAL_ENABLED", "DYE_MASK_ENABLED", "LOGO_ATLAS_ENABLED")  # cache.xml's keys of cloth
WHITE = -1  # cClothConstant.x: the object colour as 0xAARRGGBB (all ones: white, opaque)


def is_cloth(node: str) -> bool:
    """Whether a part of the scene (its config node path) is a cloth."""
    return node.split("/", 1)[0] == SECTION or f"/{SECTION}/" in node


def defines(values: dict) -> tuple:
    """The cloth's variant: no bones, and each option its material switches on."""
    return (("NUM_BONES_PER_VERTEX", "0"), *((name, "") for name in OPTIONS if switched_on(values.get(name))))


class CClothModel(CForwardModel):
    """A cloth in the forward frame: its place and colour in ``cbCloth`` (row vectors, as ``MUL(v, M)`` takes them)."""

    def fill_model(self, constants: dict) -> None:
        super().fill_model(constants)
        if "cbCloth" in constants:
            constants["cbCloth"].set("cClothWorldMtx", np.asarray(self.m_world, float).T)
            constants["cbCloth"].set("cClothConstant", [WHITE, 0, 0, 0])
