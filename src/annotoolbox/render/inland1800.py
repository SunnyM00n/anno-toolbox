"""Anno 1800's and 2205's rivers and lakes by their own ``water_inland_water.fx``: a river a ribbon of 117's vertex
(``gameinland.river_mesh``) drawn by ``RiverDefault``, a lake its outline by ``LakeDefault``, each with its
``cbInlandWater``, lit with the sea's maps and values; 1800's rivers read their flow map (``water_cluster_flow.fx``).

``compile_stages(source, game)`` runs in the background; ``CInland(ctx, game, stages, maps, dummies, shared)`` is the
device side: ``set_bodies(bodies, size)``, then per frame ``run(ctx, heights)`` and ``models(sea)``.
docs/rendering/island-1800-2205-view.md 18.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtGui import QRhiTexture

from . import frameforward, gameinland
from . import gameshader as gs
from .fxcdraw import CFxcDraw
from .gamecompute import CComputeChain, CComputeStage, copy_into, groups, record
from .gamedraw import CProgram
from .nativepass import draw_models
from .objects1800 import named_maps, plain_texture

EFFECT = "data/shaders/fx/water_inland_water.fx"
FLOW_EFFECT = "data/shaders/fx/water_cluster_flow.fx"
# the engine's define of the flow shader [I: 117's value, taken from how 117 behaves; 1800's not read]
FLOW_DEFINES = (("MAX_RIVER_FLOW_SPEED", "8.000000"),)
EFFECTS = "data/graphics/effects/"


@dataclass(frozen=True)
class SGame:
    """What differs between the two games: the passes (1800's river pass 0 clears the depth over the ocean, the colour
    is pass 1), whether the rivers read a flow map, the maps by the shaders' names (``EFFECTS``-relative) and a body's
    colour without one of its own."""

    river: str
    flow: bool
    maps: dict
    colour: tuple


GAMES = {
    # [I: the maps by name; the engine's assignment is not read]
    "anno1800": SGame("RiverDefault#1", True, {"cWaterDuckweedMap": "water/duckweed_0.dds",
                                              "cWaterDuckweedNormMap": "water/duckweed_norm_0.dds",
                                              "cWaterAlgaeMap": "water/algae_0.dds",
                                              "cWaterAlgaeNormMap": "water/algae_norm_0.dds"},
                     (3, 28, 50)),
    # 2205's rivers modulate the sea's own colour (the sea hands 1, 1, 1) [I]
    "anno2205": SGame("RiverDefault", False, {}, (255, 255, 255)),
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
    game_spec = GAMES[game]
    out = {"river": (source.effect_pass(EFFECT, (), game_spec.river), source.technique_state(EFFECT, (), game_spec.river)),
           "lake": (source.effect_pass(EFFECT, (), TECHNIQUES["lake"]),
                    source.technique_state(EFFECT, (), TECHNIQUES["lake"]))}  # fmt: skip
    if game_spec.flow:
        tech = FLOW["raster"]
        out["raster"] = (source.effect_pass(FLOW_EFFECT, FLOW_DEFINES, tech),
                         source.technique_state(FLOW_EFFECT, FLOW_DEFINES, tech))  # fmt: skip
        out.update({k: source.call_stage(FLOW_EFFECT, FLOW_DEFINES, technique) for k, technique in COMPUTE.items()})
    out["maps"] = named_maps(source.m_files, game_spec.maps, EFFECTS)
    return out


def look_values(game: str, body: gameinland.SBody, size_tiles: float) -> dict:
    """``cbInlandWater`` of a body: its colour (1800's ``WaterColor`` bytes; else the game's), its flags, the
    turbidity, no height offset; 1800's flow map's place over the island's square; 2205's swamp index 0."""
    game_spec = GAMES[game]
    colour = [channel / 255 for channel in body.look.get("WaterColor", game_spec.colour)]
    if not game_spec.flow:
        return {"cInlandWaterColor": colour, "cInlandWaterSwampIndex": 0.0}
    side = gameinland.flow_side(size_tiles)
    return {**gameinland.flow_values(size_tiles, side), "cInlandWaterColor": colour, "cInlandWaterFlags": int(body.flags),
            "cInlandWaterTurbidity": TURBIDITY, "cInlandWaterHeightOffset": 0.0}  # fmt: skip


class CInlandDraw:
    """A river or lake as a model of the forward frame's blended pass (it refracts the scene's copies: ``water``): its
    ``CFxcDraw``, the sea's maps and values and its own look written each frame."""

    m_blended, m_water, m_shadow, m_prepass, m_ssr = True, True, None, None, None

    def __init__(self, draw: CFxcDraw, look: dict, box: tuple) -> None:
        self.m_draw_call, self.m_look, self.m_later = draw, look, []
        self.m_sea = None  # this frame's sea (its ``textures`` and ``values``)
        self.m_own: dict = {}
        min_box, max_box = box
        self.m_world = np.identity(4)
        self.m_world[:3, 3] = (np.asarray(min_box) + np.asarray(max_box)) / 2
        self.m_box = box

    def prepare(
        self, render_pass, signature, batch, camera: gs.SCamera, light: dict, extra: dict | None = None
    ) -> None:
        sea = self.m_sea

        def fill(constants: dict) -> None:
            frameforward.fill_frame(constants, camera, light)
            gs.set_values(constants.values(), {**sea.m_values, **self.m_look})

        self.m_draw_call.prepare(render_pass, signature, batch, {**sea.m_textures, **self.m_own, **(extra or {})}, fill)

    def draw(self, command_buffer, size_px) -> None:
        self.m_draw_call.draw(command_buffer, size_px)

    def corners(self) -> np.ndarray:
        min_box, max_box = self.m_box
        return np.array(
            [
                [x, y, z]
                for x in (min_box[0], max_box[0])
                for y in (min_box[1], max_box[1])
                for z in (min_box[2], max_box[2])
            ]
        )

    def release(self) -> None:
        self.m_draw_call.release()


class CInland(CComputeChain):
    """The island's rivers and lakes on the device (module docstring); ``error`` says why they do not draw."""

    def __init__(self, ctx, game: str, stages: dict, dummies, shared=None) -> None:
        self.m_ctx, self.m_rhi, self.m_game, self.m_dummies, self.m_shared = ctx, ctx.m_rhi, game, dummies, shared
        self.m_programs = {
            k: (CProgram(*stages[k][0]), stages[k][1]) for k in ("river", "lake", "raster") if k in stages
        }
        self.m_stages = {k: CComputeStage(ctx.m_rhi, stages[k]) for k in COMPUTE if k in stages}
        self.m_files = dict(stages.get("maps", {}))
        self.m_bodies: list[gameinland.SBody] = []
        self.m_size_tiles = 0.0
        self.m_own: dict = {}  # the maps every draw binds: the flow map, the engine's files
        self._m_made: list = []
        self._m_targets: list = []  # the flow raster's target, its expansion, the map, the raster's framebuffer
        self._m_raster: list = []
        self._m_models: list[CInlandDraw] = []
        self._m_flow_due = False
        self._m_ready = False  # the maps and the flow targets made

    def set_bodies(self, bodies: list, size_tiles: float) -> None:
        """The island's rivers and lakes (drawn, their flow map made, from the next frame)."""
        self.m_bodies, self.m_size_tiles, self._m_flow_due = list(bodies), float(size_tiles), True
        self._drop_models()

    def _maps(self, ctx) -> None:
        """The engine's files onto the device and 1800's flow targets, once."""
        files, self.m_files, self._m_ready = self.m_files, {}, True

        def upload(command_buffer, batch) -> None:  # an empty compute pass hands the batch on
            for name, dds in files.items():
                self.m_own[name] = texture = plain_texture(self.m_rhi, batch, dds)
                self._m_made.append(texture)
            command_buffer.beginComputePass(batch)
            command_buffer.endComputePass()

        ctx.native_pass(upload)
        if GAMES[self.m_game].flow and "raster" in self.m_programs:
            side = gameinland.flow_side(self.m_size_tiles)
            TextureFormat = QRhiTexture.Format
            raster = ctx.native_texture((side, side), TextureFormat.RGBA8)
            expanded, flow = (ctx.native_texture((side, side), TextureFormat.RGBA8, storage=True) for _ in range(2))
            self._m_targets = [raster, expanded, flow, ctx.framebuffer([raster])]
            self.m_own["cInlandWaterClusterFlowTex"] = flow.native

    def run(self, ctx, heights=None) -> None:
        """Before the frame's passes: the maps once, the flow map when the bodies changed (``heights``: the
        island's coarse height texture; None: none, every ribbon read as water)."""
        if not self.m_size_tiles or self.m_error:
            return
        if not self._m_ready:
            self._maps(ctx)
        if self._m_flow_due and self._m_targets:
            self._make_flow(ctx, heights)
        self._m_flow_due = False

    def _make_flow(self, ctx, heights) -> None:
        """The rivers drawn from above into the raster by ``ClusterFlow``, expanded, copied into the flow map and
        dilated there (117's steps, ``gameinland.CInlandWater._make_flow``)."""
        raster, expanded, flow, fbo = self._m_targets
        side = gameinland.flow_side(self.m_size_tiles)
        values = gameinland.flow_values(self.m_size_tiles, side)
        program, state = self.m_programs["raster"]
        state = replace(state, **RASTER)
        for draw in self._m_raster:
            draw.release()
        self._m_raster = []
        for body in self.m_bodies:
            mesh = gameinland.river_mesh(body, self.m_size_tiles) if body.kind == "River" else None
            if mesh is not None:
                self._m_raster.append(CFxcDraw(ctx, program, mesh, state, self.m_dummies))
        named = {} if heights is None else {"cFlowTerrainHeightTex": heights}
        fill = lambda constants: gs.set_values(constants.values(), values)  # noqa: E731
        draw_models(ctx, fbo, self._m_raster, lambda draw, render_pass, signature, batch: draw.prepare(render_pass, signature, batch, named, fill),
                    lambda draw, command_buffer: draw.draw(command_buffer, (side, side)), depth=0.0, defer=False)  # fmt: skip
        group_count = groups(side, 16)
        record(ctx, self.m_stages["expand"], {"cExpandFlowSrc": raster.native, "cExpandFlowDst": expanded.native},
               None, (group_count, group_count, 1), lambda: copy_into(self.m_rhi, flow.native, expanded.native, (side, side)))  # fmt: skip
        record(ctx, self.m_stages["dilate"], {"cExpandFlowSrc": expanded.native, "cExpandFlowDst": flow.native}, None,
               (group_count, group_count, 1))  # fmt: skip

    def models(self, sea) -> list[CInlandDraw]:
        """This frame's draws (rivers by their order, then the lakes) lit with ``sea``'s maps and values; [] before the
        maps are made."""
        if self.m_error or not self.m_size_tiles or sea is None:
            return []
        if not self._m_models:
            self._m_models = self._make_models()
        for model in self._m_models:
            model.m_sea, model.m_own = sea, self.m_own
        return list(self._m_models)

    def _make_models(self) -> list[CInlandDraw]:
        out = []
        rivers = sorted(
            (candidate for candidate in self.m_bodies if candidate.kind == "River"),
            key=lambda candidate: candidate.order,
        )
        for body in [*rivers, *(candidate for candidate in self.m_bodies if candidate.kind != "River")]:
            river = body.kind == "River"
            mesh = (
                gameinland.river_mesh(body, self.m_size_tiles)
                if river
                else gameinland.lake_mesh(body, self.m_size_tiles)
            )
            if mesh is None:
                continue
            program, state = self.m_programs["river" if river else "lake"]
            draw = CFxcDraw(self.m_ctx, program, mesh, state, self.m_dummies)
            draw.m_shared = self.m_shared
            out.append(CInlandDraw(draw, look_values(self.m_game, body, self.m_size_tiles), mesh.box()))
        return out

    def take_failure(self, failed: list) -> str:
        """Why one of its draws failed in the last frame (taken out of ``failed``), "" when none did."""
        mine = [x for x in failed if any(x[0] is model for model in (*self._m_models, *self._m_raster))]
        failed[:] = [x for x in failed if all(x is not y for y in mine)]
        why = (
            mine[0][1]
            if mine
            else next((model.m_draw_call.m_error for model in self._m_models if model.m_draw_call.m_error), "")
        )
        if why:
            self._drop_models()
        return why

    def _drop_models(self) -> None:
        for model in self._m_models:
            model.release()
        self._m_models = []

    def release(self) -> None:
        self._drop_models()
        for draw in self._m_raster:
            draw.release()
        super().release()
        for texture in self._m_made:
            self.m_ctx.later(texture)
        for target in self._m_targets:
            target.release()
        self.m_own, self._m_made, self._m_targets, self._m_raster, self._m_ready = {}, [], [], [], False
