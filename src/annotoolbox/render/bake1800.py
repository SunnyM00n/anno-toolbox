"""Anno 1800's and 2205's ground baked by their own ``terrain_baking.fx``: per node the materials it holds in the
set's order (``TerrainBakeSplat``, 2205 over its ``TerrainClear``), 1800's grit splats or 2205's tint over them,
the albedo encoded as the terrain shader reads it, the normal turned for the view's mirror, the mips by
``TerrainGenerateMip``, all copied into 1800's arrays or 2205's textures of the node.

``compile_stages`` compiles (in the background); ``Bake`` stands where 117's ``terrainbake.Bake`` stands in
``desktopgame.GameIsland``'s bake loop, ``Bake.job`` makes a node's ``NodeBake``; ``RECIPES`` what differs by game,
``Ground`` what only these games' bake reads. docs/rendering/island-1800-2205-view.md 13 and 14.
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
from .fxcdraw import Dummies, SharedBuffers
from .nativepass import Composite, draw_models
from .objects1800 import Binding, Draws, ObjectDraw, plain_texture

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
F = QRhiTexture.Format
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
class Recipe:
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
    "anno1800": Recipe((*terrain1800.DEFINES["anno1800"], ("BAKE_NORMAL_MAP", "")), None, (0.5, 0.5, 1.0, 1.0),
                       ("albedo", "height", "gloss", "normal"), GRIT, ("albedo", "height", "gloss"),
                       {"encoded": ("albedo", terrain1800.encode_fs(linear=False)),
                        "flipped": ("normal", normal_fs("vec4(n.r, 1.0 - n.g, n.b, 1.0)"))},
                       {"cBakedDiffTexArray": "encoded", "cBakedNormalTexArray": "flipped"},
                       ("cMipGenDiffuseTexture", "cMipGenHeightTexture"), 1.0, DECAL),
    # 2205: three targets over its own clear; its mirrored patch turns the normal's x but not the tangent: the
    # normal's u negated [ours]; the normal's two parts a texture each, read from red. Its mip pass reads at twice
    # the coordinate times the atlas ratio: a half ratio reads the level above whole [ours]
    "anno2205": Recipe((*terrain1800.DEFINES["anno2205"], ("BAKE_NORMAL_MAP", "")), CLEAR_PASS, (0.0, 0.0, 0.0, 0.0),
                       ("albedo", "spec", "normal"), TINT, ("albedo",),
                       {"encoded": ("albedo", terrain1800.encode_fs(linear=False)),
                        "normal_x": ("normal", normal_fs("vec4(vec3(1.0 - n.r), 1.0)")),
                        "normal_y": ("normal", normal_fs("vec4(vec3(n.g), 1.0)"))},
                       {"cBakedDiffTex": "encoded", "cBakedNormalXTex": "normal_x", "cBakedNormalYTex": "normal_y"},
                       ("cMipGenDiffuseTexture", "cMipGenSpecGlossHeightTexture", "cMipGenNormalTexture"), 0.5),
}  # fmt: skip


@dataclass
class Ground(terrainbake.GroundSource):
    """The island's ground (117's) with what only these games' bake reads: per grit material its direction and
    slope threshold (``read_grit``), 2205's tint map (its side and BC1 blocks: ``terrain2205.tint_map``; None)."""

    grit: dict = field(default_factory=dict)
    tint: tuple | None = None


def with_extras(ground: terrainbake.GroundSource, grit: dict | None = None, tint: tuple | None = None) -> Ground:
    """``ground`` as a ``Ground`` with its grit materials and its tint map."""
    return Ground(**{f.name: getattr(ground, f.name) for f in fields(ground)}, grit=grit or {}, tint=tint)


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
QUAD = gamedraw.Mesh(bytes(16), 4, (), np.array([0, 1, 2, 2, 1, 3], np.uint32), 4)
_CORNERS = np.array([[x, 0.0, z, 1.0] for x in (-0.5, 0.5) for z in (-0.5, 0.5)])


@dataclass(frozen=True, eq=False)
class BakedDecal:
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
    """Whether a config's decal (``ModelLibrary.decals``) is baked into the ground rather than drawn."""
    values = decal.get("values") or {}
    kind = str(decal.get("kind", "")).strip().lower()
    return kind == BAKED_KIND and str(values.get("ShaderID", "")).strip() == BAKED_SHADER


def decal_material(decal: dict) -> tuple[dict, dict]:
    """A baked decal's material values (``cTrnDecal...``) and maps: the diffuse always (its alpha the mask), the
    normal and height where the material switches them on."""
    v = decal.get("values") or {}

    def num(name: str, default: float = 1.0) -> float:
        return float(v.get(name, default))

    maps, flags = {"cDiffuseTexture": decal.get("diffuse") or ""}, 0
    if str(v.get("NORMAL_ENABLED", "0")).strip() == "1" and decal.get("normal"):
        flags, maps["cNormalTexture"] = flags | BAKE_NORMAL, decal["normal"]
    if str(v.get("HEIGHT_MAP_ENABLED", "0")).strip() == "1" and v.get("cHeightMap"):
        flags, maps["cHeightTexture"] = flags | BAKE_HEIGHT, str(v["cHeightMap"])
    values = {"cTrnDecalMaterialFlags": flags, "cTrnDecalDiffuseOpacity": num("cDiffuseOpacity"),
              "cTrnDecalDiffuseColor": [num(f"cDiffuseColor.{c}") for c in "rgb"],
              "cTrnDecalGlossinessFactor": num("cGlossinessFactor"), "cTrnDecalHeightFactor": num("cHeightFactor")}  # fmt: skip
    return values, {k: p for k, p in maps.items() if p}


def baked_decals(placed, size: float) -> list[BakedDecal]:
    """The baked decals of ``placed`` ((instance matrices [n, 4, 4] in the view's axes, the config's decals) per
    model) on an island of ``size`` tiles (the view's x is the island's ``size - x``); a hidden instance (scale 0)
    left out."""
    out = []
    for mats, decals in placed:
        for d in decals:
            if not baked(d):
                continue
            values, maps = decal_material(d)
            ex, ez = (float(e) for e in d["extents"])  # half sizes: the quad's side is twice
            scale = np.diag([2 * ex, 1.0, 2 * ez, 1.0])
            for m in mats:
                if np.abs(m[:3, :3]).sum() == 0:
                    continue
                world = np.asarray(m, float) @ np.asarray(d["matrix"], float) @ scale
                corners = _CORNERS @ world.T
                xs, zs = size - corners[:, 0], corners[:, 2]
                box = (float(xs.min()), float(zs.min()), float(xs.max()), float(zs.max()))
                out.append(BakedDecal(world, values, maps, box))
    return out


def node_decals(decals, node) -> list[BakedDecal]:
    """The decals whose box overlaps ``node``'s square."""
    x1, z1 = node.x + node.tiles, node.z + node.tiles
    return [d for d in decals if d.box[0] < x1 and node.x < d.box[2] and d.box[1] < z1 and node.z < d.box[3]]


def decal_values(decal: BakedDecal, node) -> dict:
    """A decal's own part of ``cbTerrainBaking`` in ``node``'s bake: its place (``MUL(v, M)``: row vectors), the
    node's corner and side in the island's tiles (``vsBakeDecal`` puts the quad there), its material's values."""
    return {**decal.values, "cTrnDecalWorldMtx": decal.world.T, "cTrnDecalNodePosIS": [float(node.x), float(node.z)],
            "cTrnDecalNodeSizeIS": float(node.tiles)}  # fmt: skip


def levels(size: int) -> int:
    """The mip levels of a baked side of ``size`` texels, down to one texel."""
    return max(1, int(size).bit_length())


def compile_stages(files, compiler, game: str = "anno1800") -> dict:
    """In the background: ``TerrainBakeSplat``'s two stages and its states (``vs``, ``ps``, ``state``), the clear
    pass's the same under ``clear`` where the game has one, the pass over the splats under ``after`` and the mip
    pass under ``mip``."""
    recipe = RECIPES[game]
    source = terrain1800.TerrainSource(files, compiler, game)

    def technique(name: str) -> dict:
        vs, ps = source.effect_pass(EFFECT, recipe.defines, name)[:2]
        return {"vs": vs, "ps": ps, "state": source.technique_state(EFFECT, recipe.defines, name)}

    out = {**technique(SPLAT), "game": game, "after": technique(recipe.after), "mip": technique(MIP)}
    if recipe.clear_pass is not None:
        out["clear"] = technique(recipe.clear_pass)
    if recipe.decal is not None:
        out["decal"] = technique(recipe.decal)
    return out


def bake_values(node, ground, island: dict, page=None, size: int = terrainbake.SIZE) -> dict:
    """``cbTerrainBaking`` of a node: 117's (``terrainbake.bake_values``: the names 1800's buffer shares) with the
    node's page for the heights and normals of displacement (``page``: its parameters; None: the vertices'), and
    2205's names (its mesh's place: none, the vertices hold the session's positions; the session's size)."""
    out = terrainbake.bake_values(node, ground, island, page, size)
    out.update({"cTrnBakeDisplacementEnabled": int(page is not None),
                "cTrnGritNormalParams": list(page) if page is not None else [0.0, 0.0, 1.0],
                "cTrnBakeChunkPos": [0.0, 0.0], "cTrnSize": [ground.size, ground.size],
                "cTrnBakeUsedAtlasRatio": [1.0, 1.0]})  # fmt: skip
    return out


def splat_data(splat: terrainbake.Splat) -> dict:
    """The one value that changes from material to material: its id, its tiling and its blend's sharpness."""
    return {"cTrnBakeSplatData": [float(splat.mat_id), splat.repetition, splat.sharpness, 0.0]}


def flat_state(state):
    """A full-target pass's states as the bake's targets take them: no depth (they have none), no culling, no
    stencil (2205's tint keeps a texel from being tinted twice where draws overlap: a node's one mesh does not)."""
    return replace(state, cull="none", depth_test=False, depth_write=False, stencil=False)


class NodeBake:
    """A node's bake (a job of ``Bake.run``): its clear draw (2205), its splats' draws over its mesh, the draws over
    them (1800's grit materials, 2205's tint), each with its material's maps and value."""

    def __init__(self, bake: Bake, mesh: gamedraw.Mesh, values: dict, splats: list, own: dict, node=None,
                 decals: list = ()) -> None:  # fmt: skip
        self.bake, self.mesh, self.values, self.own = bake, mesh, values, own
        grit = bake.ground.grit if bake.recipe.after == GRIT else {}
        self.splats = [s for s in splats if s.mat_id not in grit]
        self.grits = [s for s in splats if s.mat_id in grit]
        self.node, self.decals = node, list(decals)  # the decals over its square (``node_decals``)
        self.passes: dict[str, Draws] = {}

    def _prepare(self, key: str, rp, signature, batch, items: list) -> None:
        """A pass's draws over the node's mesh (a decal's over its quad: ``QUAD``; ``objects1800.Draws``: the first
        the lead the others draw through), one per item (its values, its textures by the shader's names)."""
        b = self.bake
        if key not in self.passes:
            program, state = {"splat": (b.program, b.state), **b.extra}[key]
            mesh = QUAD if key == "decal" else self.mesh
            self.passes[key] = Draws(b.ctx, program, mesh, state, b.dummies, Binding(b.shared, OWN, fallbacks=MISSING))
        draws = self.passes[key]
        draws.begin()
        for values, textures in items:
            draws.take().prepare(rp, signature, batch, textures, lambda c, v=values: gs.set_values(c.values(), v))

    def _items(self, splats: list, extra: Callable[[terrainbake.Splat], dict]) -> list:
        b = self.bake
        return [({**self.values, **splat_data(s), **extra(s)}, b.maps.get(s.mat_id, {})) for s in splats]

    def prepare(self, rp, signature, batch) -> None:
        """The clear and the splats (the splats' pass)."""
        textures = {**self.bake.textures, **self.own}
        if "clear" in self.bake.extra:
            self._prepare("clear", rp, signature, batch, [(self.values, textures)])
        items = [(v, {**textures, **maps}) for v, maps in self._items(self.splats, lambda s: {})]
        self._prepare("splat", rp, signature, batch, items)

    def after_items(self, normal) -> list:
        """The draws over the splats: a grit material's each (``normal``: the splats' baked normal), the tint once."""
        b, textures = self.bake, {**self.bake.textures, **self.own}
        if b.recipe.after == TINT:
            return [(self.values, textures)] if TINT_MAP in b.textures else []
        textures["cGritBakedNormalTexture"] = normal
        grit = lambda s: {"cTrnBakeGritData": list(b.ground.grit[s.mat_id])}  # noqa: E731
        return [(v, {**textures, **maps}) for v, maps in self._items(self.grits, grit)]

    def prepare_after(self, rp, signature, batch, items: list) -> None:
        self._prepare("after", rp, signature, batch, items)

    def decal_items(self) -> list:
        """A draw per decal over the node whose diffuse map (its mask) is on the device: its values in this node's
        bake and its maps."""
        b, textures = self.bake, {**self.bake.textures, **self.own}
        maps = getattr(b, "decal_maps", {})
        return [({**self.values, **decal_values(d, self.node)},
                 {**textures, **{name: maps[p] for name, p in d.maps.items() if p in maps}})
                for d in self.decals if d.maps.get("cDiffuseTexture") in maps]  # fmt: skip

    def prepare_decals(self, rp, signature, batch) -> None:
        self._prepare("decal", rp, signature, batch, self.decal_items())

    def draw_decals(self, cb, size) -> None:
        self._draw(("decal",), cb, size)

    def _draw(self, keys: tuple, cb, size) -> None:
        for key in keys:
            for d in self.passes[key].taken() if key in self.passes else ():
                d.draw(cb, size)

    def draw(self, cb, size) -> None:
        self._draw(("clear", "splat"), cb, size)

    def draw_after(self, cb, size) -> None:
        self._draw(("after",), cb, size)

    def release(self) -> None:
        for part in self.passes.values():
            part.release()
        self.passes = {}


class Lane:
    """One node's targets a frame (the recipe's splat targets and its passes' outputs, each a texture of the view's
    device with a framebuffer; the targets of the pass over the splats; per mip level the stored targets at that
    side and the mip pass's draw); a node baked in a frame has its own (on Direct3D 12 a target drawn again in the
    same frame after an encode read it left the earlier slices black: island-1800-2205-view.md 7)."""

    def __init__(self, ctx, size: int, recipe: Recipe) -> None:
        names = (*recipe.targets, *recipe.encodes)
        self.textures = {name: ctx.texture((size, size), 4, dtype="f1") for name in names}
        self.splats = ctx.framebuffer([self.textures[n] for n in recipe.targets])
        self.after = ctx.framebuffer([self.textures[n] for n in recipe.after_targets])
        self.encodes = {name: ctx.framebuffer([self.textures[name]]) for name in recipe.encodes}
        self.levels: list[tuple] = []  # per level from 1: (textures by the stored target, framebuffer, its draw)
        stored = list(recipe.stores.values())
        for k in range(1, levels(size)):
            side = max(1, size >> k)
            made = {name: ctx.texture((side, side), 4, dtype="f1") for name in stored}
            self.levels.append((made, ctx.framebuffer([made[n] for n in stored]), None))

    def level_textures(self, k: int) -> dict:
        """The stored targets at level ``k`` (0: the bake's own)."""
        return self.textures if k == 0 else self.levels[k - 1][0]

    def release(self) -> None:
        for fbo in (self.splats, self.after, *self.encodes.values(), *(f for _t, f, _d in self.levels)):
            fbo.release()
        for _t, _f, draw in self.levels:
            if draw is not None:
                draw.release()
        for tex in (*self.textures.values(), *(t for made, _f, _d in self.levels for t in made.values())):
            tex.release()


def _copies(ctx, items: list) -> None:
    """Texture copies (dst, src, side, layer, level) in one batch, taken by an empty compute pass of its own (a
    batch no pass takes is dropped)."""

    def run(cb, incoming) -> None:
        batch = ctx.rhi.nextResourceUpdateBatch()
        for dst, src, side, layer, level in items:
            desc = QRhiTextureCopyDescription()
            desc.setDestinationLayer(layer)
            desc.setDestinationLevel(level)
            desc.setPixelSize(QSize(side, side))
            desc.setSourceTopLeft(QPoint(0, 0))
            batch.copyTexture(dst, src, desc)
        cb.beginComputePass(incoming)
        cb.endComputePass(batch)

    ctx.native_pass(run)


class Bake:
    """A game's bake of the island's ground (module docstring); ``stages``: ``compile_stages``' result, ``ground`` a
    ``Ground`` (or 117's ``terrainbake.GroundSource``) whose splats hold the ``MAP_KINDS`` maps
    (``desktopgame.island_ground``); ``destination(layer)``: where a node's bake goes ((textures by the shader's
    names, the layer in them); None: 1800's ``arrays`` at the node's slice), with every mip level."""

    paint = terrainbake.Bake.paint  # 117's: the words into ``ground``, written with the next run
    _write_paint = terrainbake.Bake._write_paint

    def __init__(self, ctx, stages: dict, ground, size: int = terrainbake.SIZE, slices: int = terraindraw.SLICES,
                 max_texture: int = 0, destination: Callable[[int], tuple] | None = None) -> None:  # fmt: skip
        self.ctx, self.rhi = ctx, ctx.rhi
        self.recipe = RECIPES[stages.get("game", "anno1800")]
        self.program = gamedraw.Program(stages["vs"], stages["ps"])
        self.state = stages["state"]
        self.extra = {key: (gamedraw.Program(stages[key]["vs"], stages[key]["ps"]), stages[key]["state"])
                      for key in ("clear", "after", "decal") if key in stages}  # fmt: skip
        if "decal" in self.extra:  # the splats' blend over the targets, no depth (there is none)
            program, state = self.extra["decal"]
            self.extra["decal"] = (program, flat_state(state))
        self.decals: list[BakedDecal] = []  # the island's baked decals (``set_decals``)
        self.decal_maps: dict = {}  # their maps on the device by the material's file name
        self._decal_files: dict = {}  # ... and those still to go up (``DdsFile``s)
        if "after" in self.extra:
            program, state = self.extra["after"]
            self.extra["after"] = (program, state if self.recipe.after == GRIT else flat_state(state))
        mip = stages.get("mip")
        self.mip = (gamedraw.Program(mip["vs"], mip["ps"]), flat_state(mip["state"])) if mip else None
        self.ground = ground if isinstance(ground, Ground) else with_extras(ground)
        self.size, self.max_texture = size, max_texture
        self.error = ""
        self.dummies = Dummies(self.rhi)
        self.shared = SharedBuffers(ctx)  # the frame's buffers, the same for every splat
        self.textures: dict = {}  # the material maps (R32_UINT) and 2205's tint map, made with the first run
        self.maps: dict[int, dict] = {}  # a material's maps by the shader's names
        self.keep: list = []
        self._painted: list = []
        self.lanes: list[Lane] = []
        self.passes: dict[str, Composite] = {}
        self._done: list[NodeBake] = []  # the last frame's jobs: released once that frame is through
        self.destination = destination
        self.levels = levels(size) if self.mip is not None else 1
        flags = QRhiTexture.Flag.MipMapped if self.levels > 1 else QRhiTexture.Flag(0)
        self.arrays = {}
        for name in self.recipe.stores if destination is None else ():
            array = self.rhi.newTextureArray(F.RGBA8, slices, QSize(size, size), 1, flags)
            array.create()
            self.arrays[name] = array

    def job(self, node, mesh: gamedraw.Mesh, island: dict, page=None, own: dict | None = None) -> NodeBake:
        """A node's bake: ``mesh`` its height samples (``terrainbake.bake_mesh``), ``page`` its page's parameters
        and ``own`` the page's textures when it draws with one (``cHeightPageTex``, ``cNormalPageTex``,
        ``cTerrainHeightTex``), the splats it holds in the set's order (``terrainbake.node_splats``)."""
        values = bake_values(node, self.ground, island, page, self.size)
        decals = node_decals(getattr(self, "decals", ()), node)
        return NodeBake(self, mesh, values, terrainbake.node_splats(self.ground, node), own or {}, node, decals)

    def set_decals(self, decals: list[BakedDecal], files: dict) -> list[tuple]:
        """The island's baked decals from now on, ``files`` their maps (the material's file name -> ``DdsFile``;
        uploaded with the next bake); the boxes of the decals that went or came (their nodes to bake again)."""
        old, new = {d.sign(): d for d in self.decals}, {d.sign(): d for d in decals}
        self.decals = decals
        self._decal_files.update({name: dds for name, dds in files.items() if name not in self.decal_maps})
        return [d.box for k, d in old.items() if k not in new] + [d.box for k, d in new.items() if k not in old]

    def _decal_uploads(self, batch) -> None:
        for name, dds in self._decal_files.items():
            self.decal_maps[name] = tex = plain_texture(self.rhi, batch, gamemaps.smaller(dds, self.max_texture))
            self.keep.append(tex)
        self._decal_files = {}

    def _ground_textures(self, batch) -> None:
        """The material maps (117's: ``terrainbake.material_map_textures``), 2205's tint map (BC1) and each
        material's maps as plain 2D textures (the quality's largest side); the maps' files let go once on the
        device."""
        g = self.ground
        maps = terrainbake.material_map_textures(self.rhi, batch, g)
        self.textures.update(maps)
        self.keep += maps.values()
        if g.tint is not None:
            side, raw = g.tint
            tex = self.rhi.newTexture(F.BC1, QSize(side, side), 1, QRhiTexture.Flag(0))
            tex.create()
            gamedraw.upload_level(batch, tex, raw)
            self.textures[TINT_MAP] = tex
            self.keep.append(tex)
            g.tint = None
        for s in g.splats:
            maps = {}
            for name, dds in zip(TEXTURES, s.maps, strict=False):
                if dds is not None:
                    maps[name] = plain_texture(self.rhi, batch, gamemaps.smaller(dds, self.max_texture))
                    self.keep.append(maps[name])
            self.maps[s.mat_id] = maps
        g.splats = [replace(s, maps=()) for s in g.splats]

    def _lane(self, i: int) -> Lane:
        while len(self.lanes) <= i:
            self.lanes.append(Lane(self.ctx, self.size, self.recipe))
        return self.lanes[i]

    def run(self, ctx, jobs: list) -> None:
        """Bake ``jobs`` (at most ``PER_FRAME``: (``NodeBake``, layer)): the splats into a lane's targets, the pass
        over them, the passes after them, the mips, the copies to the node's ``destination``."""
        for job in self._done:
            job.release()
        self._done = [job for job, _layer in jobs]
        if not jobs:
            return
        r = self.recipe
        self.shared.begin()
        self.passes = self.passes or {name: Composite(ctx, fs) for name, (_source, fs) in r.encodes.items()}
        size = (self.size, self.size)
        for i, (job, layer) in enumerate(jobs[:PER_FRAME]):
            lane = self._lane(i)

            def before(batch) -> None:
                if not self.textures:
                    self._ground_textures(batch)
                self._write_paint(batch)

            draw_models(ctx, lane.splats, [job], lambda m, rp, sig, batch: m.prepare(rp, sig, batch),
                        lambda m, cb: m.draw(cb, size), before=before, targets=len(r.targets), color=r.clear)  # fmt: skip
            self._after(ctx, job, lane)
            self._decal_pass(ctx, job, lane)
            for name, (source, _fs) in r.encodes.items():
                self.passes[name].draw(lane.encodes[name], {source: lane.textures[source]}, {}, depth_test=False)
            self._mips(ctx, lane)
            self._store(ctx, lane, layer)

    def _after(self, ctx, job: NodeBake, lane: Lane) -> None:
        """The pass over the splats (1800's grit materials, 2205's tint) into the lane's targets, kept as they are."""
        if "after" not in self.extra or (self.recipe.after == GRIT and not job.grits):
            return
        size, count, normal = (self.size, self.size), len(self.recipe.after_targets), lane.textures["normal"].native

        def prepare(m, rp, sig, batch) -> None:  # when the pass runs: the first run's maps are made in its first pass
            m.prepare_after(rp, sig, batch, m.after_items(normal))

        draw_models(ctx, lane.after, [job], prepare, lambda m, cb: m.draw_after(cb, size), targets=count, clear=False)

    def _decal_pass(self, ctx, job: NodeBake, lane: Lane) -> None:
        """The node's decals over the splats and grit into the splats' targets (``TerrainBakeDecal``, as the engine
        bakes them after the splats: island-1800-2205-view.md 19)."""
        if "decal" not in self.extra or not job.decals:
            return
        size = (self.size, self.size)

        def prepare(m, rp, sig, batch) -> None:
            self._decal_uploads(batch)
            m.prepare_decals(rp, sig, batch)

        draw_models(ctx, lane.splats, [job], prepare, lambda m, cb: m.draw_decals(cb, size),
                    targets=len(self.recipe.targets), clear=False)  # fmt: skip

    def _mips(self, ctx, lane: Lane) -> None:
        """Each level of the stored targets from the one above by the game's mip pass (a point a texel)."""
        if self.levels < 2:
            return
        r, (program, state) = self.recipe, self.mip
        stored = list(r.stores.values())
        values = {"cTrnBakeUsedAtlasRatio": [r.atlas, r.atlas]}
        for k in range(1, self.levels):
            made, fbo, draw = lane.levels[k - 1]
            if draw is None:
                draw = ObjectDraw(
                    ctx, program, gamedraw.screen_triangle(), state, self.dummies, Binding(self.shared, OWN)
                )
                lane.levels[k - 1] = (made, fbo, draw)
            above = lane.level_textures(k - 1)
            textures = {slot: above[name].native for slot, name in zip(r.mips, stored, strict=True)}
            side = max(1, self.size >> k)
            draw_models(ctx, fbo, [draw], lambda m, rp, sig, batch, t=textures: m.prepare(
                            rp, sig, batch, t, lambda c: gs.set_values(c.values(), values)),
                        lambda m, cb, s=side: m.draw(cb, (s, s)), targets=len(stored))  # fmt: skip

    def _store(self, ctx, lane: Lane, layer: int) -> None:
        """Every level of the stored targets into the node's ``destination`` (1800's arrays at its slice)."""
        into, at = self.destination(layer) if self.destination is not None else (self.arrays, layer)
        items = []
        for k in range(self.levels):
            made, side = lane.level_textures(k), max(1, self.size >> k)
            items += [(into[name], made[target].native, side, at, k) for name, target in self.recipe.stores.items()]
        _copies(ctx, items)

    def release(self) -> None:
        for job in self._done:
            job.release()
        for lane in self.lanes:
            lane.release()
        for part in self.passes.values():
            part.release()
        for tex in (*self.keep, *self.arrays.values()):
            self.ctx.later(tex)
        self.shared.release()
        self.dummies.release(self.ctx)
        self._done, self.lanes, self.passes, self.keep, self.textures, self.maps = [], [], {}, [], {}, {}
