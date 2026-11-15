"""Anno 1800's and 2205's rivers and lakes by their own ``water_inland_water.fx``: a river a ribbon of 117's vertex
(``gameinland.river_mesh``) drawn by ``RiverDefault``, a lake its outline by ``LakeDefault``, each with its
``cbInlandWater``, lit with the sea's maps and values; 1800's rivers read their flow map (``water_cluster_flow.fx``).

``compile_stages(source, game)`` runs in the background; ``Inland(ctx, game, stages, maps, dummies, shared)`` is the
device side: ``set_bodies(bodies, size)``, then per frame ``run(ctx, heights)`` and ``models(sea)``.
docs/rendering/island-1800-2205-view.md 18.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import frameforward, gameinland
from . import gameshader as gs
from .fxcdraw import FxcDraw
from .gamecompute import ComputeChain, ComputeStage, copy_into, groups, record
from .gamedraw import Program
from .nativepass import draw_models
from .objects1800 import named_maps, plain_texture

EFFECT = "data/shaders/fx/water_inland_water.fx"
FLOW_EFFECT = "data/shaders/fx/water_cluster_flow.fx"
# the engine's define of the flow shader [I: 117's value, read in its executable; 1800's not read]
FLOW_DEFINES = (("MAX_RIVER_FLOW_SPEED", "8.000000"),)
EFFECTS = "data/graphics/effects/"


@dataclass(frozen=True)
class Game:
    """What differs between the two games: the passes (1800's river pass 0 clears the depth over the ocean, the colour
    is pass 1), whether the rivers read a flow map, the maps by the shaders' names (``EFFECTS``-relative) and a body's
    colour without one of its own."""

    river: str
    flow: bool
    maps: dict
    colour: tuple


GAMES = {
    # [I: the maps by name; the engine's assignment is not read]
    "anno1800": Game("RiverDefault#1", True, {"cWaterDuckweedMap": "water/duckweed_0.dds",
                                              "cWaterDuckweedNormMap": "water/duckweed_norm_0.dds",
                                              "cWaterAlgaeMap": "water/algae_0.dds",
                                              "cWaterAlgaeNormMap": "water/algae_norm_0.dds"},
                     (3, 28, 50)),
    # 2205's rivers modulate the sea's own colour (the sea hands 1, 1, 1) [I]
    "anno2205": Game("RiverDefault", False, {}, (255, 255, 255)),
}  # fmt: skip
# 1800's cInlandWaterTurbidity: no file holds it [E]
TURBIDITY = 1.0
TECHNIQUES = {"lake": "LakeDefault"}
FLOW = {"raster": "ClusterFlow"}
COMPUTE = {"expand": "ExpandFlow", "dilate": "Dilate"}
# the flow raster: no blend [ours: the engine fades a ribbon's ends with a second output's alpha], no depth
RASTER = {"blend": False, "depth_test": False, "depth_write": False, "cull": "none"}


def compile_stages(source, game: str) -> dict:
    """In the background: the river's and lake's passes (stages and states), 1800's flow raster and its two compute
    passes, the maps' files (``DdsFile``; one that does not read is left to its stand-in)."""
    g = GAMES[game]
    out = {"river": (source.effect_pass(EFFECT, (), g.river), source.technique_state(EFFECT, (), g.river)),
           "lake": (source.effect_pass(EFFECT, (), TECHNIQUES["lake"]),
                    source.technique_state(EFFECT, (), TECHNIQUES["lake"]))}  # fmt: skip
    if g.flow:
        tech = FLOW["raster"]
        out["raster"] = (source.effect_pass(FLOW_EFFECT, FLOW_DEFINES, tech),
                         source.technique_state(FLOW_EFFECT, FLOW_DEFINES, tech))  # fmt: skip
        out.update({k: source.call_stage(FLOW_EFFECT, FLOW_DEFINES, t) for k, t in COMPUTE.items()})
    out["maps"] = named_maps(source.files, g.maps, EFFECTS)
    return out


def look_values(game: str, body: gameinland.Body, size: float) -> dict:
    """``cbInlandWater`` of a body: its colour (1800's ``WaterColor`` bytes; else the game's), its flags, the
    turbidity, no height offset; 1800's flow map's place over the island's square; 2205's swamp index 0."""
    g = GAMES[game]
    colour = [c / 255 for c in body.look.get("WaterColor", g.colour)]
    if not g.flow:
        return {"cInlandWaterColor": colour, "cInlandWaterSwampIndex": 0.0}
    side = gameinland.flow_side(size)
    return {**gameinland.flow_values(size, side), "cInlandWaterColor": colour, "cInlandWaterFlags": int(body.flags),
            "cInlandWaterTurbidity": TURBIDITY, "cInlandWaterHeightOffset": 0.0}  # fmt: skip


class InlandDraw:
    """A river or lake as a model of the forward frame's blended pass (it refracts the scene's copies: ``water``): its
    ``FxcDraw``, the sea's maps and values and its own look written each frame."""

    blended, water, shadow, prepass, ssr = True, True, None, None, None

    def __init__(self, draw: FxcDraw, look: dict, box: tuple) -> None:
        self.draw_call, self.look, self.later = draw, look, []
        self.sea = None  # this frame's sea (its ``textures`` and ``values``)
        self.own: dict = {}
        lo, hi = box
        self.world = np.identity(4)
        self.world[:3, 3] = (np.asarray(lo) + np.asarray(hi)) / 2
        self.box = box

    def prepare(self, rp, signature, batch, cam: gs.Camera, light: dict, extra: dict | None = None) -> None:
        sea = self.sea

        def fill(c: dict) -> None:
            frameforward.fill_frame(c, cam, light)
            gs.set_values(c.values(), {**sea.values, **self.look})

        self.draw_call.prepare(rp, signature, batch, {**sea.textures, **self.own, **(extra or {})}, fill)

    def draw(self, cb, size) -> None:
        self.draw_call.draw(cb, size)

    def corners(self) -> np.ndarray:
        lo, hi = self.box
        return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])

    def release(self) -> None:
        self.draw_call.release()


class Inland(ComputeChain):
    """The island's rivers and lakes on the device (module docstring); ``error`` says why they do not draw."""

    def __init__(self, ctx, game: str, stages: dict, dummies, shared=None) -> None:
        self.ctx, self.rhi, self.game, self.dummies, self.shared = ctx, ctx.rhi, game, dummies, shared
        self.programs = {k: (Program(*stages[k][0]), stages[k][1]) for k in ("river", "lake", "raster") if k in stages}
        self.stages = {k: ComputeStage(ctx.rhi, stages[k]) for k in COMPUTE if k in stages}
        self.files = dict(stages.get("maps", {}))
        self.bodies: list[gameinland.Body] = []
        self.size = 0.0
        self.own: dict = {}  # the maps every draw binds: the flow map, the engine's files
        self._made: list = []
        self._targets: list = []  # the flow raster's target, its expansion, the map, the raster's framebuffer
        self._raster: list = []
        self._models: list[InlandDraw] = []
        self._flow_due = False
        self._ready = False  # the maps and the flow targets made

    def set_bodies(self, bodies: list, size: float) -> None:
        """The island's rivers and lakes (drawn, their flow map made, from the next frame)."""
        self.bodies, self.size, self._flow_due = list(bodies), float(size), True
        self._drop_models()

    def _maps(self, ctx) -> None:
        """The engine's files onto the device and 1800's flow targets, once."""
        files, self.files, self._ready = self.files, {}, True

        def upload(cb, batch) -> None:  # an empty compute pass hands the batch on
            for name, dds in files.items():
                self.own[name] = tex = plain_texture(self.rhi, batch, dds)
                self._made.append(tex)
            cb.beginComputePass(batch)
            cb.endComputePass()

        ctx.native_pass(upload)
        if GAMES[self.game].flow and "raster" in self.programs:
            side = gameinland.flow_side(self.size)
            F = QRhiTexture.Format
            raster = ctx.native_texture((side, side), F.RGBA8)
            expanded, flow = (ctx.native_texture((side, side), F.RGBA8, storage=True) for _ in range(2))
            self._targets = [raster, expanded, flow, ctx.framebuffer([raster])]
            self.own["cInlandWaterClusterFlowTex"] = flow.native

    def run(self, ctx, heights=None) -> None:
        """Before the frame's passes: the maps once, the flow map when the bodies changed (``heights``: the
        island's coarse height texture; None: none, every ribbon read as water)."""
        if not self.size or self.error:
            return
        if not self._ready:
            self._maps(ctx)
        if self._flow_due and self._targets:
            self._make_flow(ctx, heights)
        self._flow_due = False

    def _make_flow(self, ctx, heights) -> None:
        """The rivers drawn from above into the raster by ``ClusterFlow``, expanded, copied into the flow map and
        dilated there (117's steps, ``gameinland.InlandWater._make_flow``)."""
        raster, expanded, flow, fbo = self._targets
        side = gameinland.flow_side(self.size)
        values = gameinland.flow_values(self.size, side)
        program, state = self.programs["raster"]
        state = replace(state, **RASTER)
        for d in self._raster:
            d.release()
        self._raster = []
        for body in self.bodies:
            mesh = gameinland.river_mesh(body, self.size) if body.kind == "River" else None
            if mesh is not None:
                self._raster.append(FxcDraw(ctx, program, mesh, state, self.dummies))
        named = {} if heights is None else {"cFlowTerrainHeightTex": heights}
        fill = lambda c: gs.set_values(c.values(), values)  # noqa: E731
        draw_models(ctx, fbo, self._raster, lambda d, rp, sig, batch: d.prepare(rp, sig, batch, named, fill),
                    lambda d, cb: d.draw(cb, (side, side)), depth=0.0)  # fmt: skip
        n = groups(side, 16)
        record(ctx, self.stages["expand"], {"cExpandFlowSrc": raster.native, "cExpandFlowDst": expanded.native},
               None, (n, n, 1), lambda: copy_into(self.rhi, flow.native, expanded.native, (side, side)))  # fmt: skip
        record(ctx, self.stages["dilate"], {"cExpandFlowSrc": expanded.native, "cExpandFlowDst": flow.native}, None,
               (n, n, 1))  # fmt: skip

    def models(self, sea) -> list[InlandDraw]:
        """This frame's draws (rivers by their order, then the lakes) lit with ``sea``'s maps and values; [] before the
        maps are made."""
        if self.error or not self.size or sea is None:
            return []
        if not self._models:
            self._models = self._make_models()
        for m in self._models:
            m.sea, m.own = sea, self.own
        return list(self._models)

    def _make_models(self) -> list[InlandDraw]:
        out = []
        rivers = sorted((b for b in self.bodies if b.kind == "River"), key=lambda b: b.order)
        for body in [*rivers, *(b for b in self.bodies if b.kind != "River")]:
            river = body.kind == "River"
            mesh = gameinland.river_mesh(body, self.size) if river else gameinland.lake_mesh(body, self.size)
            if mesh is None:
                continue
            program, state = self.programs["river" if river else "lake"]
            draw = FxcDraw(self.ctx, program, mesh, state, self.dummies)
            draw.shared = self.shared
            out.append(InlandDraw(draw, look_values(self.game, body, self.size), mesh.box()))
        return out

    def take_failure(self, failed: list) -> str:
        """Why one of its draws failed in the last frame (taken out of ``failed``), "" when none did."""
        mine = [x for x in failed if any(x[0] is m for m in (*self._models, *self._raster))]
        failed[:] = [x for x in failed if all(x is not y for y in mine)]
        why = mine[0][1] if mine else next((m.draw_call.error for m in self._models if m.draw_call.error), "")
        if why:
            self._drop_models()
        return why

    def _drop_models(self) -> None:
        for m in self._models:
            m.release()
        self._models = []

    def release(self) -> None:
        self._drop_models()
        for d in self._raster:
            d.release()
        super().release()
        for tex in self._made:
            self.ctx.later(tex)
        for t in self._targets:
            t.release()
        self.own, self._made, self._targets, self._raster, self._ready = {}, [], [], [], False
