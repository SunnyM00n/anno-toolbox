"""The ground and the water of the PC renderer's frame (a mixin of ``desktoprender.CRenderer``): the ground's
material layers and blend weights as textures (``_sync_ground``, ``_bind_ground``), the sea, rivers and lakes as
plain surfaces (``_draw_water``: where the look's own shaders do not draw them). Needs the renderer's context,
programs, loader and session (island-editor-view "The 3D view")."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np

from ...infra.i18n import tr
from ...render.camera import gl
from ...render.gameinland import LAVA_FLAG
from ...render.light import srgb_hex
from ..editchange import SChange
from ..editsession import game_of
from .desktopgeometry import ribbon, triangulate

if TYPE_CHECKING:
    from .desktoprender import SViewOptions

GROUND_TEXTURE_MAX = 512  # 2070's own size (117's are 1024; archived 1024 in island-editor-view)
# the islands whose own look is the game's: the editor's ground there is each material's flat colour (the fallback;
# island-editor-view "Looks")
PLAIN_GROUND = ("anno117", "anno1800", "anno2205")
PLAIN_SIZE = 4  # a plain layer's side: one colour
LAVA_CRUST = (0.05, 0.02, 0.012)  # a lava river's plain colour, linear [I]
LAVA_GLOW = (0.5, 0.09, 0.0)  # and what it emits, linear: a stand-in for the game's lava shader (water.md 1.9a) [I]
DEEP = (0.004, 0.018, 0.03)  # the sea bed beyond the island's grid, linear


def is_lava(spline: dict) -> bool:
    """Whether a river line of the island is lava: its ``flags`` (the River block's ``RenderFlags``) has ``LAVA_FLAG``."""
    return spline["kind"] == "River" and bool(spline.get("flags", 0) & LAVA_FLAG)


class CGroundDraw:
    """The renderer's ground and water methods; ``CRenderer`` brings the state they use."""

    # background loads
    def _albedo(self):
        from PIL import Image

        image = Image.open(io.BytesIO(self.m_editor.albedo_png())).convert("RGB")
        return image.size, image.tobytes()

    def _ground_info(self):
        """In the background: the ground's layers, their weights and the materials (read from the archive: 1.1 s on
        a continental island inside a frame, performance.md 6, item 2)."""
        ground_meta = self.m_editor.ground()
        side, layer_count = ground_meta["width"], len(ground_meta["materials"])
        weights = np.frombuffer(self.m_editor.ground_weights(), np.uint8).reshape(layer_count, side, side).copy()
        return ground_meta, weights, self._materials(ground_meta)

    def _layer_pixels(self, material: dict, size_px: int):
        """One ground material as the texture arrays take it: diffuse with its blend mask as alpha, normal."""
        from PIL import Image

        def open_image(path, **options):
            if not path:
                return None
            try:
                return Image.open(io.BytesIO(self.m_editor.texture_png(path, **options)))
            except Exception:
                return None

        diffuse_image, alpha_image, normal_image = (
            open_image(material.get("diffuse"), is_opaque=True),
            open_image(material.get("diffuse"), use_alpha=True),
            open_image(material.get("normal"), is_normal=True),
        )
        rgb = (
            diffuse_image.convert("RGB").resize((size_px, size_px))
            if diffuse_image
            else Image.new("RGB", (size_px, size_px), (128, 128, 128))
        )
        mask = (
            alpha_image.convert("L").resize((size_px, size_px))
            if alpha_image
            else Image.new("L", (size_px, size_px), 255)
        )
        rgba = Image.merge("RGBA", (*rgb.split(), mask))
        normal_rgb = (
            normal_image.convert("RGB").resize((size_px, size_px))
            if normal_image
            else Image.new("RGB", (size_px, size_px), (128, 128, 255))
        )
        return rgba.tobytes(), normal_rgb.tobytes()

    def _texture_size(self, materials: list[dict]) -> int:
        from PIL import Image

        best = 256
        for material in materials[:4]:
            try:
                best = max(
                    best, Image.open(io.BytesIO(self.m_editor.texture_png(material["diffuse"], is_opaque=True))).width
                )
            except Exception:
                continue
        return min(GROUND_TEXTURE_MAX, best)

    def _layers(self, ids: tuple):
        mats = self.m_session.m_paint_materials
        materials = [mats.get(i, {"id": i, "diffuse": None, "normal": None, "repetition": 4}) for i in ids]
        if game_of(self.m_session.m_island) in PLAIN_GROUND:
            return PLAIN_SIZE, [self._plain_pixels(m) for m in materials]
        size = self._texture_size(materials)
        return size, [self._layer_pixels(m, size) for m in materials]

    def _plain_pixels(self, material: dict):
        """One ground material as its flat colour: the mean of its diffuse map (grey without one), no blend mask, a
        flat normal."""
        from PIL import Image

        colour = (128, 128, 128)
        try:
            image = (
                Image.open(io.BytesIO(self.m_editor.texture_png(material["diffuse"], is_opaque=True)))
                if material.get("diffuse")
                else None
            )
            if image is not None:
                colour = image.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        except Exception:  # a map that does not read: grey, as a layer without one
            pass
        pixel_count = PLAIN_SIZE * PLAIN_SIZE
        return bytes([*colour, 255]) * pixel_count, bytes([128, 128, 255]) * pixel_count

    # the ground's textures
    def _sync_ground(self, change: SChange, opts: SViewOptions) -> None:
        session = self.m_session
        ground_meta = self.m_loader.get("ground", self._ground_info) if self.m_editor is not None else None
        if isinstance(ground_meta, tuple) and session.m_weights is None:
            meta, weights, materials = ground_meta
            session.attach_ground([layer["id"] for layer in meta["materials"]], weights, materials)
            change.weights = (0, 0, weights.shape[1] - 1, weights.shape[1] - 1)
        if session.m_weights is None:
            return
        self._sync_layers(change)
        self._sync_weights(change)
        if opts.look2070 and self.m_ground["alpha2070"] is None and self.m_editor is not None:
            self._sync_look2070()

    def _sync_layers(self, change: SChange) -> None:
        """The ground's material layers as texture arrays, made again when the layers or their looks changed."""
        session, ground, ctx = self.m_session, self.m_ground, self.m_ctx
        ids = tuple(session.m_ground_ids)
        known = frozenset(
            i for i in ids if i in session.m_paint_materials
        )  # a layer drawn plain gets its look once known
        if ground["ids"] == ids and ground["known"] == known:
            return
        if ground["asked"] != (ids, known):
            self.m_loader.forget(("layers", ids))
            ground["asked"] = (ids, known)
        layers = self.m_loader.get(("layers", ids), lambda: self._layers(ids))
        if not isinstance(layers, tuple):
            return
        size, pixels = layers
        for k in ("diffuse", "normals"):
            if ground.get(k) is not None:
                ground[k].release()
        ground["diffuse"] = ctx.texture_array(
            (size, size, len(ids)), 4, b"".join(layer_pixels[0] for layer_pixels in pixels)
        )
        ground["normals"] = ctx.texture_array(
            (size, size, len(ids)), 3, b"".join(layer_pixels[1] for layer_pixels in pixels)
        )
        for layer_texture in (ground["diffuse"], ground["normals"]):
            layer_texture.build_mipmaps()
            layer_texture.anisotropy = 8.0
            layer_texture.repeat_x = layer_texture.repeat_y = True
        ground["ids"], ground["size"], ground["known"] = ids, size, known
        change.weights = (0, 0, session.m_weights.shape[1] - 1, session.m_weights.shape[1] - 1)

    def _sync_weights(self, change: SChange) -> None:
        """The paint weights as a texture array: made when the size or the layers changed, else the changed block."""
        session, ctx = self.m_session, self.m_ctx
        side = session.m_weights.shape[1]
        if self._m_weights_tex is None or self._m_weights_tex.size != (side, side, len(session.m_ground_ids)):
            if self._m_weights_tex is not None:
                self._m_weights_tex.release()
            self._m_weights_tex = ctx.texture_array(
                (side, side, len(session.m_ground_ids)), 1, session.m_weights.tobytes()
            )
            self._m_weights_tex.repeat_x = self._m_weights_tex.repeat_y = False
        elif change.weights is not None:
            x0, z0, x1, z1 = change.weights
            block = np.ascontiguousarray(session.m_weights[:, z0 : z1 + 1, x0 : x1 + 1])
            self._m_weights_tex.write(
                block.tobytes(), viewport=(x0, z0, 0, x1 - x0 + 1, z1 - z0 + 1, len(session.m_ground_ids))
            )

    def _sync_look2070(self) -> None:
        """2070's ground look: its alpha layers and the slot table of its chunks."""
        ground = self.m_ground
        got = self.m_loader.get("ground2070", self.m_editor.ground2070)
        if isinstance(got, tuple):
            info70, alpha = got
            side, depth = info70["side"], info70["layers"]
            ground["alpha2070"] = self.m_ctx.texture_array((side * 17, side * 17, depth), 1, alpha)
            ground["alpha2070"].repeat_x = ground["alpha2070"].repeat_y = False
            slots = np.asarray(info70["slots"], np.uint8).reshape(side, side * depth)
            texture = self.m_ctx.texture((side * depth, side), 1, slots.tobytes(), dtype="u1")
            texture.m_filter = (0x2600, 0x2600)  # NEAREST
            ground["slots2070"], ground["side2070"], ground["per2070"] = texture, side, info70["per"]
        elif isinstance(got, Exception):
            self._message(tr("islands.no_2070_look", reason=got))

    def _materials(self, meta: dict) -> list[dict]:
        mats = {}
        try:
            mats = {material["id"]: material for material in self.m_editor.materials()}
        except Exception:  # the set does not read: the island's own list stands (the gallery says why)
            pass
        return [{**material, **mats.get(material["id"], {})} for material in meta["materials"]] + [
            material for i, material in mats.items() if i not in {x["id"] for x in meta["materials"]}
        ]

    # water
    def _water(self):
        if self._m_water_meshes is None:
            ctx, size = self.m_ctx, self.m_size_tiles
            low, high = -6 * size, 7 * size  # out to where the fog has the horizon
            sea = np.array([[low, 0, low], [high, 0, low], [low, 0, high], [high, 0, high]], "f4")
            sea_vbo = ctx.buffer(sea.tobytes())
            rivers, lakes, lava = [], [], []
            for spline in self.m_session.m_island.get("splines", []):
                if spline["kind"] not in ("River", "Lake") or len(spline["points"]) < 2:
                    continue
                points = [[size - point[0], point[1], point[2]] for point in spline["points"]]
                if spline["kind"] == "Lake":
                    triangles = triangulate([(point[0], point[2]) for point in points])
                    if triangles:
                        y = points[0][1]
                        vertices = np.array([[point[0], y, point[2]] for point in points], "f4")
                        lakes.append((vertices, np.asarray(triangles, "u4").ravel()))
                elif is_lava(spline):
                    lava.append(ribbon(points, spline.get("widths", [])))
                else:
                    rivers.append(ribbon(points, spline.get("widths", [])))
            meshes = []
            for kind, items in (("river", rivers), ("lake", lakes), ("lava", lava)):
                for vertices, indices in items:
                    if len(indices):
                        meshes.append((kind, ctx.buffer(vertices.tobytes()), ctx.buffer(indices.tobytes())))
            floor = float(self.m_session.m_base_heights.min()) - 0.5  # past the island's grid: a sea bed to look into
            bed = np.array(
                [
                    [low, floor, low, *DEEP],
                    [high, floor, low, *DEEP],
                    [low, floor, high, *DEEP],
                    [high, floor, high, *DEEP],
                ],
                "f4",
            )
            bed_vao = ctx.vertex_array(self.m_prog["flat"], [(ctx.buffer(bed.tobytes()), "3f 3f", "pos", "rgb")])
            self._m_water_meshes = {"sea": sea_vbo, "bed": bed_vao, "inner": meshes, "vaos": {}}
        return self._m_water_meshes

    def _water_vao(self, vbo, ibo=None):
        """The water program's vertex array of a surface's buffers, made once."""
        cache = self._m_water_meshes["vaos"]
        if id(vbo) not in cache:
            cache[id(vbo)] = self.m_ctx.vertex_array(
                self.m_prog["water"], [(vbo, "3f", "pos")], index_buffer=ibo, index_element_size=4, skip_errors=True
            )
        return cache[id(vbo)]

    def _bind_ground(self, prog, opts: SViewOptions) -> None:
        ground, session = self.m_ground, self.m_session
        albedo = self.m_loader.get("albedo", self._albedo) if self.m_editor is not None else None
        if isinstance(albedo, tuple) and "albedo" not in self.m_textures:
            (albedo_width_px, albedo_height_px), raw = albedo
            texture = self.m_ctx.texture((albedo_width_px, albedo_height_px), 3, raw, internal_format=0x8C41)
            texture.build_mipmaps()
            texture.anisotropy = 8.0
            self.m_textures["albedo"] = texture
        texture = self.m_textures.get("albedo")
        prog["islandSize"].value = self.m_size_tiles
        prog["hasAlbedo"].value = texture is not None
        (texture or self._m_dummy).use(0)
        prog["albedo"].value = 0
        ready = (
            ground["ids"] is not None
            and tuple(session.m_ground_ids) == ground["ids"]
            and self._m_weights_tex is not None
        )
        mode = 0  # the baked image; 1 the weights; 3 2070's layers
        if ready:
            mode = 3 if opts.look2070 and ground["alpha2070"] is not None else 1
        prog["groundMode"].value = mode
        for unit, name, layer_texture in (
            (1, "weights", self._m_weights_tex if ready else None),
            (3, "diffuse", ground.get("diffuse") if ready else None),
            (4, "normals", ground.get("normals") if ready else None),
            (5, "alpha2070", ground["alpha2070"]),
        ):
            (layer_texture or self._m_dummy_array).use(unit)
            if name in prog:
                prog[name].value = unit
        (ground.get("slots2070") or self._m_dummy_u).use(6)
        if "slots2070" in prog:
            prog["slots2070"].value = 6
        if not ready:
            return
        mats = session.m_paint_materials
        repetitions = [float(mats.get(i, {}).get("repetition") or 4) for i in session.m_ground_ids] + [4.0] * (
            32 - len(session.m_ground_ids)
        )
        orders = [mats.get(i, {}).get("order", i) for i in session.m_ground_ids]
        rank = sorted(range(len(orders)), key=lambda k: orders[k]) + [0] * (32 - len(orders))
        sharp = [float(mats.get(i, {}).get("sharpness", 1.0)) for i in session.m_ground_ids] + [1.0] * (
            32 - len(orders)
        )
        for name, kind, values in (("repeat_", "f4", repetitions), ("rank", "i4", rank), ("sharp", "f4", sharp)):
            if name in prog:  # a driver drops a uniform the shader never reads
                prog[name].write(np.asarray(values[:32], kind).tobytes())
        if "layers" in prog:
            prog["layers"].value = len(session.m_ground_ids)
        if "side2070" in prog:
            prog["side2070"].value = float(ground.get("side2070", 1))
            prog["per2070"].value = float(ground.get("per2070", 16))

    def _draw_water(self, view_proj, lt, eye, shadow, sea: bool = True, inland: bool = True) -> None:
        """The sea (without ``sea``: rivers and lakes only), rivers and lakes (without ``inland``: the sea only) as
        plain surfaces: the view's own look, the fallback where the game's own shaders do not draw the water."""
        ctx, wm, fbo = self.m_ctx, self._water(), self.m_targets["msaa"]
        prog = self.m_prog["water"]
        prog["viewProj"].write(gl(view_proj))
        self._set_light(prog, lt, eye, shadow)
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        fbo.depth_mask = False
        prog["waterColor"].value, prog["opacity"].value = srgb_hex(0x1F5F80), 0.78
        if sea:
            self._water_vao(wm["sea"]).render(ctx.TRIANGLE_STRIP)
        look = srgb_hex(0x357FA3), 0.82
        prog["waterColor"].value, prog["opacity"].value = look
        if inland:
            self._inner_once(look)
        ctx.disable(ctx.BLEND)
        fbo.depth_mask = True

    def _inner_once(self, look: tuple) -> None:
        """Rivers and lakes each pixel once (the nearest surface): a depth pass, then colour where the depth
        is equal; two see-through surfaces doubled into a light band (2026-10-16). ``look``: the water's colour and
        opacity; a lava river glows instead (``LAVA_GLOW``)."""
        ctx, wm, fbo = self.m_ctx, self._m_water_meshes, self.m_targets["msaa"]
        items = wm["inner"]
        if not items:
            return
        fbo.color_mask = (False, False, False, False)
        fbo.depth_mask = True
        for _, vbo, ibo in items:
            self._water_vao(vbo, ibo).render()
        fbo.color_mask = (True, True, True, True)
        fbo.depth_mask = False
        ctx.depth_func = "=="
        prog = self.m_prog["water"]
        for kind, vbo, ibo in items:
            glows = kind == "lava"
            prog["glow"].value = LAVA_GLOW if glows else (0.0, 0.0, 0.0)
            prog["waterColor"].value, prog["opacity"].value = (LAVA_CRUST, 1.0) if glows else look
            self._water_vao(vbo, ibo).render()
        prog["glow"].value = (0.0, 0.0, 0.0)
        ctx.depth_func = "<"
