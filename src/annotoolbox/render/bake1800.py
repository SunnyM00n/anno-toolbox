"""Anno 1800's and 2205's ground baked by their own ``terrain_baking.fx``: per node the materials it holds in the
set's order (``TerrainBakeSplat``, 2205 over its ``TerrainClear``), 1800's grit splats or 2205's tint over them,
the albedo encoded as the terrain shader reads it, the normal turned for the view's mirror, the mips by
``TerrainGenerateMip``, all copied into 1800's arrays or 2205's textures of the node.

``compile_stages`` compiles (in the background); ``CBake`` stands where 117's ``terrainbake.CBake`` stands in
``desktopgame.CGameIsland``'s bake loop, ``CBake.job`` makes a node's ``CNodeBake``; ``RECIPES`` what differs by game,
``SGround`` what only these games' bake reads. docs/rendering/island-1800-2205-view.md 13 and 14.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, fields, replace

import numpy as np
from PySide6.QtCore import QPoint, QSize
from PySide6.QtGui import QRhiTexture, QRhiTextureCopyDescription

from . import gamedraw, gamemaps, terrain1800, terrainbake, terraindraw
from . import gameshader as gs
from .fxcdraw import CDummies, CSharedBuffers
from .nativepass import CComposite, draw_models
from .objects1800 import CDraws, CObjectDraw, SBinding, plain_texture

EFFECT = "data/shaders/fx/terrain_baking.fx"
SPLAT, CLEAR_PASS = "TerrainBakeSplat", "TerrainClear"
GRIT, TINT, MIP, DECAL = "TerrainBakeGritSplat", "TerrainBakeTint", "TerrainGenerateMip", "TerrainBakeDecal"
MAP_KINDS = ("Diffuse", "Height", "Spec", "Normal")  # a material's maps (its xml), in the shader's order
TEXTURES = ("cDiffuseTexture", "cHeightTexture", "cSpecTexture", "cNormalTexture")
# a map a material lacks: grey with a full mask, the middle height, no gloss, a flat normal [ours]
MISSING = dict(zip(TEXTURES, ((128, 128, 128, 255), (128, 128, 128, 255), (0, 0, 0, 255), (128, 128, 255, 255)),
                   strict=True))  # fmt: skip
OWN = ("cbTerrainBaking",)  # a splat's own buffer; ``cbPerFrame`` (1800: the session's size) is the bake's, shared
PER_FRAME = terrainbake.PER_FRAME  # nodes baked a frame (117's loop counts them)
TINT_MAP = "cTerrainTintTex"
TextureFormat = QRhiTexture.Format
_MATERIAL_RE = re.compile(r"<Material>(.*?)\n</>", re.S)


def normal_fs(expression: str) -> str:
    """A pass that writes ``expression`` of the baked normal ``n`` (rgb) as its colour."""
    return f"""
#version 330
uniform sampler2D normal;
in vec2 vNdc; out vec4 color;
void main() {{ vec3 n = texelFetch(normal, ivec2(gl_FragCoord.xy), 0).rgb; color = {expression}; }}
"""


@dataclass(frozen=True)
class SRecipe:
    """What a game's bake draws and keeps: its switches (the terrain's set and the normal target; the view's set
    reads no baked gloss or height, so the maps' heights are not sampled: no ``BAKE_HEIGHT_MAP``), its clear pass
    (None: the pass's ``clear`` colour), the splats' targets in order, the pass over the splats (``after``: 1800's
    grit splats, 2205's tint) and its targets, the passes after them (target <- the target it reads, its fragment
    shader), what the terrain shader reads <- the target copied into it, the mip pass's sources in that order and
    its ``cTrnBakeUsedAtlasRatio``."""

    defines: tuple
    clear_pass: str | None
    clear: tuple
    targets: tuple
    after: str
    after_targets: tuple
    encodes: dict
    stores: dict
    mips: tuple
    atlas: float
    decal: str | None = None  # the technique that bakes the config decals over the rest (1800's; None: none)


RECIPES = {
    # 1800: four targets; no clear pass in its effect: albedo (0.5, 0.5, 1) where no material covers (none on the
    # islands read), the height's middle, a flat normal [ours]. Its island matrix mirrors x, which turns the
    # terrain's bitangent over (a cross product of mirrored vectors): the normal's v negated [ours]. The mip pass
    # copies a point a level: the stored targets go through its diffuse and height slots [ours]
    "anno1800": SRecipe((*terrain1800.DEFINES["anno1800"], ("BAKE_NORMAL_MAP", "")), None, (0.5, 0.5, 1.0, 1.0),
                       ("albedo", "height", "gloss", "normal"), GRIT, ("albedo", "height", "gloss"),
                       {"encoded": ("albedo", terrain1800.encode_fs(linear=False)),
                        "flipped": ("normal", normal_fs("vec4(n.r, 1.0 - n.g, n.b, 1.0)"))},
                       {"cBakedDiffTexArray": "encoded", "cBakedNormalTexArray": "flipped"},
                       ("cMipGenDiffuseTexture", "cMipGenHeightTexture"), 1.0, DECAL),
    # 2205: three targets over its own clear; its mirrored patch turns the normal's x but not the tangent: the
    # normal's u negated [ours]; the normal's two parts a texture each, read from red. Its mip pass reads at twice
    # the coordinate times the atlas ratio: a half ratio reads the level above whole [ours]
    "anno2205": SRecipe((*terrain1800.DEFINES["anno2205"], ("BAKE_NORMAL_MAP", "")), CLEAR_PASS, (0.0, 0.0, 0.0, 0.0),
                       ("albedo", "spec", "normal"), TINT, ("albedo",),
                       {"encoded": ("albedo", terrain1800.encode_fs(linear=False)),
                        "normal_x": ("normal", normal_fs("vec4(vec3(1.0 - n.r), 1.0)")),
                        "normal_y": ("normal", normal_fs("vec4(vec3(n.g), 1.0)"))},
                       {"cBakedDiffTex": "encoded", "cBakedNormalXTex": "normal_x", "cBakedNormalYTex": "normal_y"},
                       ("cMipGenDiffuseTexture", "cMipGenSpecGlossHeightTexture", "cMipGenNormalTexture"), 0.5),
}  # fmt: skip


@dataclass
class SGround(terrainbake.SGroundSource):
    """The island's ground (117's) with what only these games' bake reads: per grit material its direction and
    slope threshold (``read_grit``), 2205's tint map (its side and BC1 blocks: ``terrain2205.tint_map``; None)."""

    grit: dict = field(default_factory=dict)
    tint: tuple | None = None


def with_extras(ground: terrainbake.SGroundSource, grit: dict | None = None, tint: tuple | None = None) -> SGround:
    """``ground`` as a ``SGround`` with its grit materials and its tint map."""
    return SGround(
        **{data_field.name: getattr(ground, data_field.name) for data_field in fields(ground)},
        grit=grit or {},
        tint=tint,
    )


def read_grit(text: str) -> dict[int, tuple[float, float]]:
    """A material set's grit materials (``GritEnabled`` 1): id -> (``GritDirection``, ``GritSlopeThreshold``)."""
    out = {}
    for block in _MATERIAL_RE.findall(text):
        values = dict(re.findall(r"<(ID|GritEnabled|GritDirection|GritSlopeThreshold)>([^<]*)</>", block))
        if values.get("GritEnabled", "0").strip() == "1" and "ID" in values:
            out[int(values["ID"])] = (float(values.get("GritDirection", 1.0)),
                                      float(values.get("GritSlopeThreshold", 0.0)))  # fmt: skip
    return out


# a config DECAL the node bake draws (``TerrainBakeDecal``): ``Type`` Terrain-Baked with the terrain decal material
# (ShaderID 2: cDiffuseOpacity, cGlossinessFactor, cHeightFactor); the others draw as geometry (section 19)
BAKED_KIND, BAKED_SHADER = "terrain-baked", "2"
BAKE_NORMAL, BAKE_HEIGHT = 1, 2  # terrain_baking.fx TDB_BAKE_NORMAL, TDB_BAKE_HEIGHT
# the decal's quad: the shader makes its corners from the vertex id, no vertex buffer (the strip as two triangles)
QUAD = gamedraw.CMesh(bytes(16), 4, (), np.array([0, 1, 2, 2, 1, 3], np.uint32), 4)
_CORNERS = np.array([[x, 0.0, z, 1.0] for x in (-0.5, 0.5) for z in (-0.5, 0.5)])


@dataclass(frozen=True, eq=False)
class SBakedDecal:
    """One placed decal of the bake: its quad's place (-0.5..0.5 in x and z to the view's axes, column vectors),
    its material's ``cbTerrainBaking`` values, its maps (the shader's name -> the material's file name) and the box
    of island tiles it covers (x0, z0, x1, z1)."""

    world: np.ndarray
    values: dict
    maps: dict
    box: tuple

    def sign(self) -> tuple:
        return (self.box, tuple(np.round(self.world, 5).ravel()), tuple(sorted(self.maps.items())),
                repr(sorted(self.values.items())))  # fmt: skip


def baked(decal: dict) -> bool:
    """Whether a config's decal (``CModelLibrary.decals``) is baked into the ground rather than drawn."""
    values = decal.get("values") or {}
    kind = str(decal.get("kind", "")).strip().lower()
    return kind == BAKED_KIND and str(values.get("ShaderID", "")).strip() == BAKED_SHADER


def decal_material(decal: dict) -> tuple[dict, dict]:
    """A baked decal's material values (``cTrnDecal...``) and maps: the diffuse always (its alpha the mask), the
    normal and height where the material switches them on."""
    material_values = decal.get("values") or {}

    def num(name: str, default: float = 1.0) -> float:
        return float(material_values.get(name, default))

    maps, flags = {"cDiffuseTexture": decal.get("diffuse") or ""}, 0
    if str(material_values.get("NORMAL_ENABLED", "0")).strip() == "1" and decal.get("normal"):
        flags, maps["cNormalTexture"] = flags | BAKE_NORMAL, decal["normal"]
    if str(material_values.get("HEIGHT_MAP_ENABLED", "0")).strip() == "1" and material_values.get("cHeightMap"):
        flags, maps["cHeightTexture"] = flags | BAKE_HEIGHT, str(material_values["cHeightMap"])
    values = {
        "cTrnDecalMaterialFlags": flags,
        "cTrnDecalDiffuseOpacity": num("cDiffuseOpacity"),
        "cTrnDecalDiffuseColor": [num(f"cDiffuseColor.{channel}") for channel in "rgb"],
        "cTrnDecalGlossinessFactor": num("cGlossinessFactor"),
        "cTrnDecalHeightFactor": num("cHeightFactor"),
    }
    return values, {shader_name: file_name for shader_name, file_name in maps.items() if file_name}


def baked_decals(placed, size_tiles: float) -> list[SBakedDecal]:
    """The baked decals of ``placed`` ((instance matrices [n, 4, 4] in the view's axes, the config's decals) per
    model) on an island of ``size_tiles`` tiles (the view's x is the island's ``size_tiles - x``); a hidden instance (scale 0)
    left out."""
    out = []
    for matrices, decals in placed:
        for decal in decals:
            if not baked(decal):
                continue
            values, maps = decal_material(decal)
            half_x, half_z = (float(extent) for extent in decal["extents"])  # half sizes: the quad's side is twice
            scale = np.diag([2 * half_x, 1.0, 2 * half_z, 1.0])
            for matrix in matrices:
                if np.abs(matrix[:3, :3]).sum() == 0:
                    continue
                world = np.asarray(matrix, float) @ np.asarray(decal["matrix"], float) @ scale
                corners = _CORNERS @ world.T
                xs, zs = size_tiles - corners[:, 0], corners[:, 2]
                box = (float(xs.min()), float(zs.min()), float(xs.max()), float(zs.max()))
                out.append(SBakedDecal(world, values, maps, box))
    return out


def node_decals(decals, node) -> list[SBakedDecal]:
    """The decals whose box overlaps ``node``'s square."""
    max_x, max_z = node.x + node.tiles, node.z + node.tiles
    return [
        decal
        for decal in decals
        if decal.box[0] < max_x and node.x < decal.box[2] and decal.box[1] < max_z and node.z < decal.box[3]
    ]


def decal_values(decal: SBakedDecal, node) -> dict:
    """A decal's own part of ``cbTerrainBaking`` in ``node``'s bake: its place (``MUL(v, M)``: row vectors), the
    node's corner and side in the island's tiles (``vsBakeDecal`` puts the quad there), its material's values."""
    return {**decal.values, "cTrnDecalWorldMtx": decal.world.T, "cTrnDecalNodePosIS": [float(node.x), float(node.z)],
            "cTrnDecalNodeSizeIS": float(node.tiles)}  # fmt: skip


def levels(size_px: int) -> int:
    """The mip levels of a baked side of ``size_px`` texels, down to one texel."""
    return max(1, int(size_px).bit_length())


def compile_stages(files, compiler, game: str = "anno1800") -> dict:
    """In the background: ``TerrainBakeSplat``'s two stages and its states (``vs``, ``ps``, ``state``), the clear
    pass's the same under ``clear`` where the game has one, the pass over the splats under ``after`` and the mip
    pass under ``mip``."""
    recipe = RECIPES[game]
    source = terrain1800.CTerrainSource(files, compiler, game)

    def technique(name: str) -> dict:
        vertex_shader, pixel_shader = source.effect_pass(EFFECT, recipe.defines, name)[:2]
        return {"vs": vertex_shader, "ps": pixel_shader, "state": source.technique_state(EFFECT, recipe.defines, name)}

    out = {**technique(SPLAT), "game": game, "after": technique(recipe.after), "mip": technique(MIP)}
    if recipe.clear_pass is not None:
        out["clear"] = technique(recipe.clear_pass)
    if recipe.decal is not None:
        out["decal"] = technique(recipe.decal)
    return out


def bake_values(node, ground, island: dict, page=None, size_texels: int = terrainbake.SIZE) -> dict:
    """``cbTerrainBaking`` of a node: 117's (``terrainbake.bake_values``: the names 1800's buffer shares) with the
    node's page for the heights and normals of displacement (``page``: its parameters; None: the vertices'), and
    2205's names (its mesh's place: none, the vertices hold the session's positions; the session's size)."""
    out = terrainbake.bake_values(node, ground, island, page, size_texels)
    out.update({"cTrnBakeDisplacementEnabled": int(page is not None),
                "cTrnGritNormalParams": list(page) if page is not None else [0.0, 0.0, 1.0],
                "cTrnBakeChunkPos": [0.0, 0.0], "cTrnSize": [ground.size, ground.size],
                "cTrnBakeUsedAtlasRatio": [1.0, 1.0]})  # fmt: skip
    return out


def splat_data(splat: terrainbake.SSplat) -> dict:
    """The one value that changes from material to material: its id, its tiling and its blend's sharpness."""
    return {"cTrnBakeSplatData": [float(splat.mat_id), splat.repetition, splat.sharpness, 0.0]}


def flat_state(state):
    """A full-target pass's states as the bake's targets take them: no depth (they have none), no culling, no
    stencil (2205's tint keeps a texel from being tinted twice where draws overlap: a node's one mesh does not)."""
    return replace(state, cull="none", depth_test=False, depth_write=False, stencil=False)


class CNodeBake:
    """A node's bake (a job of ``CBake.run``): its clear draw (2205), its splats' draws over its mesh, the draws over
    them (1800's grit materials, 2205's tint), each with its material's maps and value."""

    def __init__(self, bake: CBake, mesh: gamedraw.CMesh, values: dict, splats: list, own: dict, node=None,
                 decals: list = ()) -> None:  # fmt: skip
        self.m_bake, self.m_mesh, self.m_values, self.m_own = bake, mesh, values, own
        grit = bake.m_ground.grit if bake.m_recipe.after == GRIT else {}
        self.m_splats = [splat for splat in splats if splat.mat_id not in grit]
        self.m_grits = [splat for splat in splats if splat.mat_id in grit]
        self.m_node, self.m_decals = node, list(decals)  # the decals over its square (``node_decals``)
        self.m_passes: dict[str, CDraws] = {}

    def _prepare(self, key: str, render_pass, signature, batch, items: list) -> None:
        """A pass's draws over the node's mesh (a decal's over its quad: ``QUAD``; ``objects1800.CDraws``: the first
        the lead the others draw through), one per item (its values, its textures by the shader's names)."""
        bake = self.m_bake
        if key not in self.m_passes:
            program, state = {"splat": (bake.m_program, bake.m_state), **bake.m_extra}[key]
            mesh = QUAD if key == "decal" else self.m_mesh
            self.m_passes[key] = CDraws(
                bake.m_ctx, program, mesh, state, bake.m_dummies, SBinding(bake.m_shared, OWN, fallbacks=MISSING)
            )
        draws = self.m_passes[key]
        draws.begin()
        for values, textures in items:
            draws.take().prepare(
                render_pass,
                signature,
                batch,
                textures,
                lambda draw_context, item_values=values: gs.set_values(draw_context.values(), item_values),
            )

    def _items(self, splats: list, extra: Callable[[terrainbake.SSplat], dict]) -> list:
        bake = self.m_bake
        return [
            ({**self.m_values, **splat_data(splat), **extra(splat)}, bake.m_maps.get(splat.mat_id, {}))
            for splat in splats
        ]

    def prepare(self, render_pass, signature, batch) -> None:
        """The clear and the splats (the splats' pass)."""
        textures = {**self.m_bake.m_textures, **self.m_own}
        if "clear" in self.m_bake.m_extra:
            self._prepare("clear", render_pass, signature, batch, [(self.m_values, textures)])
        items = [
            (item_values, {**textures, **maps}) for item_values, maps in self._items(self.m_splats, lambda splat: {})
        ]
        self._prepare("splat", render_pass, signature, batch, items)

    def after_items(self, normal) -> list:
        """The draws over the splats: a grit material's each (``normal``: the splats' baked normal), the tint once."""
        bake, textures = self.m_bake, {**self.m_bake.m_textures, **self.m_own}
        if bake.m_recipe.after == TINT:
            return [(self.m_values, textures)] if TINT_MAP in bake.m_textures else []
        textures["cGritBakedNormalTexture"] = normal
        grit = lambda splat: {"cTrnBakeGritData": list(bake.m_ground.grit[splat.mat_id])}  # noqa: E731
        return [(item_values, {**textures, **maps}) for item_values, maps in self._items(self.m_grits, grit)]

    def prepare_after(self, render_pass, signature, batch, items: list) -> None:
        self._prepare("after", render_pass, signature, batch, items)

    def decal_items(self) -> list:
        """A draw per decal over the node whose diffuse map (its mask) is on the device: its values in this node's
        bake and its maps."""
        bake, textures = self.m_bake, {**self.m_bake.m_textures, **self.m_own}
        maps = getattr(bake, "m_decal_maps", {})
        return [({**self.m_values, **decal_values(decal, self.m_node)},
                 {**textures, **{name: maps[file_name] for name, file_name in decal.maps.items() if file_name in maps}})
                for decal in self.m_decals if decal.maps.get("cDiffuseTexture") in maps]  # fmt: skip

    def prepare_decals(self, render_pass, signature, batch) -> None:
        self._prepare("decal", render_pass, signature, batch, self.decal_items())

    def draw_decals(self, command_buffer, size_px) -> None:
        self._draw(("decal",), command_buffer, size_px)

    def _draw(self, keys: tuple, command_buffer, size_px) -> None:
        for key in keys:
            for draw in self.m_passes[key].taken() if key in self.m_passes else ():
                draw.draw(command_buffer, size_px)

    def draw(self, command_buffer, size_px) -> None:
        self._draw(("clear", "splat"), command_buffer, size_px)

    def draw_after(self, command_buffer, size_px) -> None:
        self._draw(("after",), command_buffer, size_px)

    def release(self) -> None:
        for part in self.m_passes.values():
            part.release()
        self.m_passes = {}


class CLane:
    """One node's targets a frame (the recipe's splat targets and its passes' outputs, each a texture of the view's
    device with a framebuffer; the targets of the pass over the splats; per mip level the stored targets at that
    side and the mip pass's draw); a node baked in a frame has its own (on Direct3D 12 a target drawn again in the
    same frame after an encode read it left the earlier slices black: island-1800-2205-view.md 7)."""

    def __init__(self, ctx, size_px: int, recipe: SRecipe) -> None:
        names = (*recipe.targets, *recipe.encodes)
        self.m_textures = {name: ctx.texture((size_px, size_px), 4, dtype="f1") for name in names}
        self.m_splats = ctx.framebuffer([self.m_textures[target_name] for target_name in recipe.targets])
        self.m_after = ctx.framebuffer([self.m_textures[target_name] for target_name in recipe.after_targets])
        self.m_encodes = {name: ctx.framebuffer([self.m_textures[name]]) for name in recipe.encodes}
        self.m_levels: list[tuple] = []  # per level from 1: (textures by the stored target, framebuffer, its draw)
        stored = list(recipe.stores.values())
        for k in range(1, levels(size_px)):
            side = max(1, size_px >> k)
            made = {name: ctx.texture((side, side), 4, dtype="f1") for name in stored}
            self.m_levels.append((made, ctx.framebuffer([made[target_name] for target_name in stored]), None))

    def level_textures(self, k: int) -> dict:
        """The stored targets at level ``k`` (0: the bake's own)."""
        return self.m_textures if k == 0 else self.m_levels[k - 1][0]

    def release(self) -> None:
        for fbo in (
            self.m_splats,
            self.m_after,
            *self.m_encodes.values(),
            *(level_framebuffer for _made, level_framebuffer, _draws in self.m_levels),
        ):
            fbo.release()
        for _made, _framebuffer, draw in self.m_levels:
            if draw is not None:
                draw.release()
        for texture in (
            *self.m_textures.values(),
            *(level_texture for made, _framebuffer, _draws in self.m_levels for level_texture in made.values()),
        ):
            texture.release()


def _copies(ctx, items: list) -> None:
    """Texture copies (dst, src, side, layer, level) in one batch, taken by an empty compute pass of its own (a
    batch no pass takes is dropped)."""

    def run(command_buffer, incoming) -> None:
        batch = ctx.m_rhi.nextResourceUpdateBatch()
        for destination, source, side, layer, level in items:
            description = QRhiTextureCopyDescription()
            description.setDestinationLayer(layer)
            description.setDestinationLevel(level)
            description.setPixelSize(QSize(side, side))
            description.setSourceTopLeft(QPoint(0, 0))
            batch.copyTexture(destination, source, description)
        command_buffer.beginComputePass(incoming)
        command_buffer.endComputePass(batch)

    ctx.native_pass(run)


class CBake:
    """A game's bake of the island's ground (module docstring); ``stages``: ``compile_stages``' result, ``ground`` a
    ``SGround`` (or 117's ``terrainbake.SGroundSource``) whose splats hold the ``MAP_KINDS`` maps
    (``desktopgame.island_ground``); ``destination(layer)``: where a node's bake goes ((textures by the shader's
    names, the layer in them); None: 1800's ``arrays`` at the node's slice), with every mip level."""

    paint = terrainbake.CBake.paint  # 117's: the words into ``ground``, written with the next run
    _write_paint = terrainbake.CBake._write_paint

    def __init__(self, ctx, stages: dict, ground, size_texels: int = terrainbake.SIZE, slices: int = terraindraw.SLICES,
                 max_texture: int = 0, destination: Callable[[int], tuple] | None = None) -> None:  # fmt: skip
        self.m_ctx, self.m_rhi = ctx, ctx.m_rhi
        self.m_recipe = RECIPES[stages.get("game", "anno1800")]
        self.m_program = gamedraw.CProgram(stages["vs"], stages["ps"])
        self.m_state = stages["state"]
        self.m_extra = {key: (gamedraw.CProgram(stages[key]["vs"], stages[key]["ps"]), stages[key]["state"])
                      for key in ("clear", "after", "decal") if key in stages}  # fmt: skip
        if "decal" in self.m_extra:  # the splats' blend over the targets, no depth (there is none)
            program, state = self.m_extra["decal"]
            self.m_extra["decal"] = (program, flat_state(state))
        self.m_decals: list[SBakedDecal] = []  # the island's baked decals (``set_decals``)
        self.m_decal_maps: dict = {}  # their maps on the device by the material's file name
        self._m_decal_files: dict = {}  # ... and those still to go up (``DdsFile``s)
        if "after" in self.m_extra:
            program, state = self.m_extra["after"]
            self.m_extra["after"] = (program, state if self.m_recipe.after == GRIT else flat_state(state))
        mip = stages.get("mip")
        self.m_mip = (gamedraw.CProgram(mip["vs"], mip["ps"]), flat_state(mip["state"])) if mip else None
        self.m_ground = ground if isinstance(ground, SGround) else with_extras(ground)
        self.m_size_texels, self.m_max_texture = size_texels, max_texture
        self.m_error = ""
        self.m_dummies = CDummies(self.m_rhi)
        self.m_shared = CSharedBuffers(ctx)  # the frame's buffers, the same for every splat
        self.m_textures: dict = {}  # the material maps (R32_UINT) and 2205's tint map, made with the first run
        self.m_maps: dict[int, dict] = {}  # a material's maps by the shader's names
        self.m_keep: list = []
        self._m_painted: list = []
        self.m_lanes: list[CLane] = []
        self.m_passes: dict[str, CComposite] = {}
        self._m_done: list[CNodeBake] = []  # the last frame's jobs: released once that frame is through
        self.m_destination = destination
        self.m_levels = levels(size_texels) if self.m_mip is not None else 1
        flags = QRhiTexture.Flag.MipMapped if self.m_levels > 1 else QRhiTexture.Flag(0)
        self.m_arrays = {}
        for name in self.m_recipe.stores if destination is None else ():
            array = self.m_rhi.newTextureArray(TextureFormat.RGBA8, slices, QSize(size_texels, size_texels), 1, flags)
            array.create()
            self.m_arrays[name] = array

    def job(self, node, mesh: gamedraw.CMesh, island: dict, page=None, own: dict | None = None) -> CNodeBake:
        """A node's bake: ``mesh`` its height samples (``terrainbake.bake_mesh``), ``page`` its page's parameters
        and ``own`` the page's textures when it draws with one (``cHeightPageTex``, ``cNormalPageTex``,
        ``cTerrainHeightTex``), the splats it holds in the set's order (``terrainbake.node_splats``)."""
        values = bake_values(node, self.m_ground, island, page, self.m_size_texels)
        decals = node_decals(getattr(self, "m_decals", ()), node)
        return CNodeBake(self, mesh, values, terrainbake.node_splats(self.m_ground, node), own or {}, node, decals)

    def set_decals(self, decals: list[SBakedDecal], files: dict) -> list[tuple]:
        """The island's baked decals from now on, ``files`` their maps (the material's file name -> ``DdsFile``;
        uploaded with the next bake); the boxes of the decals that went or came (their nodes to bake again)."""
        old, new = {decal.sign(): decal for decal in self.m_decals}, {decal.sign(): decal for decal in decals}
        self.m_decals = decals
        self._m_decal_files.update({name: dds for name, dds in files.items() if name not in self.m_decal_maps})
        return [decal.box for k, decal in old.items() if k not in new] + [
            decal.box for k, decal in new.items() if k not in old
        ]

    def _decal_uploads(self, batch) -> None:
        for name, dds in self._m_decal_files.items():
            self.m_decal_maps[name] = texture = plain_texture(
                self.m_rhi, batch, gamemaps.smaller(dds, self.m_max_texture)
            )
            self.m_keep.append(texture)
        self._m_decal_files = {}

    def _ground_textures(self, batch) -> None:
        """The material maps (117's: ``terrainbake.material_map_textures``), 2205's tint map (BC1) and each
        material's maps as plain 2D textures (the quality's largest side); the maps' files let go once on the
        device."""
        ground = self.m_ground
        maps = terrainbake.material_map_textures(self.m_rhi, batch, ground)
        self.m_textures.update(maps)
        self.m_keep += maps.values()
        if ground.tint is not None:
            side, raw = ground.tint
            texture = self.m_rhi.newTexture(TextureFormat.BC1, QSize(side, side), 1, QRhiTexture.Flag(0))
            texture.create()
            gamedraw.upload_level(batch, texture, raw)
            self.m_textures[TINT_MAP] = texture
            self.m_keep.append(texture)
            ground.tint = None
        for splat in ground.splats:
            maps = {}
            for name, dds in zip(TEXTURES, splat.maps, strict=False):
                if dds is not None:
                    maps[name] = plain_texture(self.m_rhi, batch, gamemaps.smaller(dds, self.m_max_texture))
                    self.m_keep.append(maps[name])
            self.m_maps[splat.mat_id] = maps
        ground.splats = [replace(splat, maps=()) for splat in ground.splats]

    def _lane(self, i: int) -> CLane:
        while len(self.m_lanes) <= i:
            self.m_lanes.append(CLane(self.m_ctx, self.m_size_texels, self.m_recipe))
        return self.m_lanes[i]

    def run(self, ctx, jobs: list) -> None:
        """Bake ``jobs`` (at most ``PER_FRAME``: (``CNodeBake``, layer)): the splats into a lane's targets, the pass
        over them, the passes after them, the mips, the copies to the node's ``destination``."""
        for job in self._m_done:
            job.release()
        self._m_done = [job for job, _layer in jobs]
        if not jobs:
            return
        recipe = self.m_recipe
        self.m_shared.begin()
        self.m_passes = self.m_passes or {
            name: CComposite(ctx, fragment_shader) for name, (_source, fragment_shader) in recipe.encodes.items()
        }
        size_px = (self.m_size_texels, self.m_size_texels)
        for i, (job, layer) in enumerate(jobs[:PER_FRAME]):
            lane = self._lane(i)

            def before(batch) -> None:
                if not self.m_textures:
                    self._ground_textures(batch)
                self._write_paint(batch)

            draw_models(
                ctx,
                lane.m_splats,
                [job],
                lambda node_bake, render_pass, signature, batch: node_bake.prepare(render_pass, signature, batch),
                lambda node_bake, command_buffer: node_bake.draw(command_buffer, size_px),
                before=before,
                targets=len(recipe.targets),
                color=recipe.clear,
                defer=False,
            )
            self._after(ctx, job, lane)
            self._decal_pass(ctx, job, lane)
            for name, (source, _fs) in recipe.encodes.items():
                self.m_passes[name].draw(lane.m_encodes[name], {source: lane.m_textures[source]}, {}, depth_test=False)
            self._mips(ctx, lane)
            self._store(ctx, lane, layer)

    def _after(self, ctx, job: CNodeBake, lane: CLane) -> None:
        """The pass over the splats (1800's grit materials, 2205's tint) into the lane's targets, kept as they are."""
        if "after" not in self.m_extra or (self.m_recipe.after == GRIT and not job.m_grits):
            return
        size_px, count, normal = (
            (self.m_size_texels, self.m_size_texels),
            len(self.m_recipe.after_targets),
            lane.m_textures["normal"].native,
        )

        def prepare(
            node_bake, render_pass, signature, batch
        ) -> None:  # when the pass runs: the first run's maps are made in its first pass
            node_bake.prepare_after(render_pass, signature, batch, node_bake.after_items(normal))

        draw_models(
            ctx,
            lane.m_after,
            [job],
            prepare,
            lambda node_bake, command_buffer: node_bake.draw_after(command_buffer, size_px),
            targets=count,
            clear=False,
            defer=False,
        )

    def _decal_pass(self, ctx, job: CNodeBake, lane: CLane) -> None:
        """The node's decals over the splats and grit into the splats' targets (``TerrainBakeDecal``, as the engine
        bakes them after the splats: island-1800-2205-view.md 19)."""
        if "decal" not in self.m_extra or not job.m_decals:
            return
        size_px = (self.m_size_texels, self.m_size_texels)

        def prepare(node_bake, render_pass, signature, batch) -> None:
            self._decal_uploads(batch)
            node_bake.prepare_decals(render_pass, signature, batch)

        draw_models(ctx, lane.m_splats, [job], prepare, lambda node_bake, command_buffer: node_bake.draw_decals(command_buffer, size_px),
                    targets=len(self.m_recipe.targets), clear=False, defer=False)  # fmt: skip

    def _mips(self, ctx, lane: CLane) -> None:
        """Each level of the stored targets from the one above by the game's mip pass (a point a texel)."""
        if self.m_levels < 2:
            return
        recipe, (program, state) = self.m_recipe, self.m_mip
        stored = list(recipe.stores.values())
        values = {"cTrnBakeUsedAtlasRatio": [recipe.atlas, recipe.atlas]}
        for k in range(1, self.m_levels):
            made, framebuffer, draw = lane.m_levels[k - 1]
            if draw is None:
                draw = CObjectDraw(
                    ctx, program, gamedraw.screen_triangle(), state, self.m_dummies, SBinding(self.m_shared, OWN)
                )
                lane.m_levels[k - 1] = (made, framebuffer, draw)
            above = lane.level_textures(k - 1)
            textures = {slot: above[name].native for slot, name in zip(recipe.mips, stored, strict=True)}
            side = max(1, self.m_size_texels >> k)
            draw_models(
                ctx,
                framebuffer,
                [draw],
                lambda object_draw, render_pass, signature, batch, slot_textures=textures: object_draw.prepare(
                    render_pass,
                    signature,
                    batch,
                    slot_textures,
                    lambda draw_context: gs.set_values(draw_context.values(), values),
                ),
                lambda object_draw, command_buffer, side_px=side: object_draw.draw(command_buffer, (side_px, side_px)),
                targets=len(stored),
                defer=False,
            )

    def _store(self, ctx, lane: CLane, layer: int) -> None:
        """Every level of the stored targets into the node's ``destination`` (1800's arrays at its slice)."""
        into, target_layer = self.m_destination(layer) if self.m_destination is not None else (self.m_arrays, layer)
        items = []
        for k in range(self.m_levels):
            made, side = lane.level_textures(k), max(1, self.m_size_texels >> k)
            items += [
                (into[name], made[target].native, side, target_layer, k)
                for name, target in self.m_recipe.stores.items()
            ]
        _copies(ctx, items)

    def release(self) -> None:
        for job in self._m_done:
            job.release()
        for lane in self.m_lanes:
            lane.release()
        for part in self.m_passes.values():
            part.release()
        for texture in (*self.m_keep, *self.m_arrays.values()):
            self.m_ctx.later(texture)
        self.m_shared.release()
        self.m_dummies.release(self.m_ctx)
        self._m_done, self.m_lanes, self.m_passes, self.m_keep, self.m_textures, self.m_maps = [], [], {}, [], {}, {}
