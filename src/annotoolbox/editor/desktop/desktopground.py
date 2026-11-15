"""The ground and the water of the PC renderer's frame (a mixin of ``desktoprender.Renderer``): the ground's
material layers and blend weights as textures (``_sync_ground``, ``_bind_ground``), the sea, rivers and lakes as
plain surfaces (``_draw_water``: where the look's own shaders do not draw them). Needs the renderer's context,
programs, loader and session (island-editor-view "The 3D view")."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np

from ...infra.i18n import tr
from ...render.camera import gl
from ...render.light import srgb_hex
from ..editchange import Change
from ..editsession import game_of
from .desktopgeometry import ribbon, triangulate

if TYPE_CHECKING:
    from .desktoprender import ViewOptions

GROUND_TEXTURE_MAX = 512  # 2070's own size (117's are 1024; archived 1024 in island-editor-view)
# the islands whose own look is the game's: the editor's ground there is each material's flat colour (the fallback;
# island-editor-view "Looks")
PLAIN_GROUND = ("anno117", "anno1800", "anno2205")
PLAIN_SIZE = 4  # a plain layer's side: one colour
DEEP = (0.004, 0.018, 0.03)  # the sea bed beyond the island's grid, linear


class GroundDraw:
    """The renderer's ground and water methods; ``Renderer`` brings the state they use."""

    # background loads
    def _albedo(self):
        from PIL import Image

        img = Image.open(io.BytesIO(self.editor.albedo_png())).convert("RGB")
        return img.size, img.tobytes()

    def _ground_info(self):
        info = self.editor.ground()
        n, m = info["width"], len(info["materials"])
        return info, np.frombuffer(self.editor.ground_weights(), np.uint8).reshape(m, n, n).copy()

    def _layer_pixels(self, m: dict, size: int):
        """One ground material as the texture arrays take it: diffuse with its blend mask as alpha, normal."""
        from PIL import Image

        def img(path, **kw):
            if not path:
                return None
            try:
                return Image.open(io.BytesIO(self.editor.texture_png(path, **kw)))
            except Exception:
                return None

        d, a, n = (
            img(m.get("diffuse"), opaque=True),
            img(m.get("diffuse"), alpha=True),
            img(m.get("normal"), normal=True),
        )
        rgb = d.convert("RGB").resize((size, size)) if d else Image.new("RGB", (size, size), (128, 128, 128))
        mask = a.convert("L").resize((size, size)) if a else Image.new("L", (size, size), 255)
        rgba = Image.merge("RGBA", (*rgb.split(), mask))
        nrm = n.convert("RGB").resize((size, size)) if n else Image.new("RGB", (size, size), (128, 128, 255))
        return rgba.tobytes(), nrm.tobytes()

    def _texture_size(self, materials: list[dict]) -> int:
        from PIL import Image

        best = 256
        for m in materials[:4]:
            try:
                best = max(best, Image.open(io.BytesIO(self.editor.texture_png(m["diffuse"], opaque=True))).width)
            except Exception:
                continue
        return min(GROUND_TEXTURE_MAX, best)

    def _layers(self, ids: tuple):
        mats = self.session.paint_materials
        materials = [mats.get(i, {"id": i, "diffuse": None, "normal": None, "repetition": 4}) for i in ids]
        if game_of(self.session.island) in PLAIN_GROUND:
            return PLAIN_SIZE, [self._plain_pixels(m) for m in materials]
        size = self._texture_size(materials)
        return size, [self._layer_pixels(m, size) for m in materials]

    def _plain_pixels(self, m: dict):
        """One ground material as its flat colour: the mean of its diffuse map (grey without one), no blend mask, a
        flat normal."""
        from PIL import Image

        colour = (128, 128, 128)
        try:
            img = (
                Image.open(io.BytesIO(self.editor.texture_png(m["diffuse"], opaque=True))) if m.get("diffuse") else None
            )
            if img is not None:
                colour = img.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        except Exception:  # a map that does not read: grey, as a layer without one
            pass
        n = PLAIN_SIZE * PLAIN_SIZE
        return bytes([*colour, 255]) * n, bytes([128, 128, 255]) * n

    # the ground's textures
    def _sync_ground(self, change: Change, opts: ViewOptions) -> None:
        s = self.session
        info = self.loader.get("ground", self._ground_info) if self.editor is not None else None
        if isinstance(info, tuple) and s.weights is None:
            meta, weights = info
            s.attach_ground([m["id"] for m in meta["materials"]], weights, self._materials(meta))
            change.weights = (0, 0, weights.shape[1] - 1, weights.shape[1] - 1)
        if s.weights is None:
            return
        self._sync_layers(change)
        self._sync_weights(change)
        if opts.look2070 and self.ground["alpha2070"] is None and self.editor is not None:
            self._sync_look2070()

    def _sync_layers(self, change: Change) -> None:
        """The ground's material layers as texture arrays, made again when the layers or their looks changed."""
        s, g, ctx = self.session, self.ground, self.ctx
        ids = tuple(s.ground_ids)
        known = frozenset(i for i in ids if i in s.paint_materials)  # a layer drawn plain gets its look once known
        if g["ids"] == ids and g["known"] == known:
            return
        if g["asked"] != (ids, known):
            self.loader.forget(("layers", ids))
            g["asked"] = (ids, known)
        layers = self.loader.get(("layers", ids), lambda: self._layers(ids))
        if not isinstance(layers, tuple):
            return
        size, pix = layers
        for k in ("diffuse", "normals"):
            if g.get(k) is not None:
                g[k].release()
        g["diffuse"] = ctx.texture_array((size, size, len(ids)), 4, b"".join(p[0] for p in pix))
        g["normals"] = ctx.texture_array((size, size, len(ids)), 3, b"".join(p[1] for p in pix))
        for t in (g["diffuse"], g["normals"]):
            t.build_mipmaps()
            t.anisotropy = 8.0
            t.repeat_x = t.repeat_y = True
        g["ids"], g["size"], g["known"] = ids, size, known
        change.weights = (0, 0, s.weights.shape[1] - 1, s.weights.shape[1] - 1)

    def _sync_weights(self, change: Change) -> None:
        """The paint weights as a texture array: made when the size or the layers changed, else the changed block."""
        s, ctx = self.session, self.ctx
        n = s.weights.shape[1]
        if self._weights_tex is None or self._weights_tex.size != (n, n, len(s.ground_ids)):
            if self._weights_tex is not None:
                self._weights_tex.release()
            self._weights_tex = ctx.texture_array((n, n, len(s.ground_ids)), 1, s.weights.tobytes())
            self._weights_tex.repeat_x = self._weights_tex.repeat_y = False
        elif change.weights is not None:
            x0, z0, x1, z1 = change.weights
            block = np.ascontiguousarray(s.weights[:, z0 : z1 + 1, x0 : x1 + 1])
            self._weights_tex.write(block.tobytes(), viewport=(x0, z0, 0, x1 - x0 + 1, z1 - z0 + 1, len(s.ground_ids)))

    def _sync_look2070(self) -> None:
        """2070's ground look: its alpha layers and the slot table of its chunks."""
        g = self.ground
        got = self.loader.get("ground2070", self.editor.ground2070)
        if isinstance(got, tuple):
            info70, alpha = got
            side, depth = info70["side"], info70["layers"]
            g["alpha2070"] = self.ctx.texture_array((side * 17, side * 17, depth), 1, alpha)
            g["alpha2070"].repeat_x = g["alpha2070"].repeat_y = False
            slots = np.asarray(info70["slots"], np.uint8).reshape(side, side * depth)
            tex = self.ctx.texture((side * depth, side), 1, slots.tobytes(), dtype="u1")
            tex.filter = (0x2600, 0x2600)  # NEAREST
            g["slots2070"], g["side2070"], g["per2070"] = tex, side, info70["per"]
        elif isinstance(got, Exception):
            self._message(tr("no 2070 look: {reason}", reason=got))

    def _materials(self, meta: dict) -> list[dict]:
        mats = {}
        try:
            mats = {m["id"]: m for m in self.editor.materials()}
        except Exception:
            pass
        return [{**m, **mats.get(m["id"], {})} for m in meta["materials"]] + [
            m for i, m in mats.items() if i not in {x["id"] for x in meta["materials"]}
        ]

    # water
    def _water(self):
        if self._water_meshes is None:
            ctx, size = self.ctx, self.size
            a, b = -6 * size, 7 * size  # out to where the fog has the horizon
            sea = np.array([[a, 0, a], [b, 0, a], [a, 0, b], [b, 0, b]], "f4")
            sea_vbo = ctx.buffer(sea.tobytes())
            rivers, lakes = [], []
            for sp in self.session.island.get("splines", []):
                if sp["kind"] not in ("River", "Lake") or len(sp["points"]) < 2:
                    continue
                pts = [[size - p[0], p[1], p[2]] for p in sp["points"]]
                if sp["kind"] == "Lake":
                    tris = triangulate([(p[0], p[2]) for p in pts])
                    if tris:
                        y = pts[0][1]
                        verts = np.array([[p[0], y, p[2]] for p in pts], "f4")
                        lakes.append((verts, np.asarray(tris, "u4").ravel()))
                else:
                    rivers.append(ribbon(pts, sp.get("widths", [])))
            meshes = []
            for kind, items in (("river", rivers), ("lake", lakes)):
                for verts, idx in items:
                    if len(idx):
                        meshes.append((kind, ctx.buffer(verts.tobytes()), ctx.buffer(idx.tobytes())))
            floor = float(self.session.base_heights.min()) - 0.5  # past the island's grid: a sea bed to look into
            bed = np.array(
                [[a, floor, a, *DEEP], [b, floor, a, *DEEP], [a, floor, b, *DEEP], [b, floor, b, *DEEP]], "f4"
            )
            bed_vao = ctx.vertex_array(self.prog["flat"], [(ctx.buffer(bed.tobytes()), "3f 3f", "pos", "rgb")])
            self._water_meshes = {"sea": sea_vbo, "bed": bed_vao, "inner": meshes, "vaos": {}}
        return self._water_meshes

    def _water_vao(self, vbo, ibo=None):
        """The water program's vertex array of a surface's buffers, made once."""
        cache = self._water_meshes["vaos"]
        if id(vbo) not in cache:
            cache[id(vbo)] = self.ctx.vertex_array(
                self.prog["water"], [(vbo, "3f", "pos")], index_buffer=ibo, index_element_size=4, skip_errors=True
            )
        return cache[id(vbo)]

    def _bind_ground(self, prog, opts: ViewOptions) -> None:
        g, s = self.ground, self.session
        albedo = self.loader.get("albedo", self._albedo) if self.editor is not None else None
        if isinstance(albedo, tuple) and "albedo" not in self.textures:
            (aw, ah), raw = albedo
            tex = self.ctx.texture((aw, ah), 3, raw, internal_format=0x8C41)
            tex.build_mipmaps()
            tex.anisotropy = 8.0
            self.textures["albedo"] = tex
        tex = self.textures.get("albedo")
        prog["islandSize"].value = self.size
        prog["hasAlbedo"].value = tex is not None
        (tex or self._dummy).use(0)
        prog["albedo"].value = 0
        ready = g["ids"] is not None and tuple(s.ground_ids) == g["ids"] and self._weights_tex is not None
        mode = 0  # the baked image; 1 the weights; 3 2070's layers
        if ready:
            mode = 3 if opts.look2070 and g["alpha2070"] is not None else 1
        prog["groundMode"].value = mode
        for unit, name, t in (
            (1, "weights", self._weights_tex if ready else None),
            (3, "diffuse", g.get("diffuse") if ready else None),
            (4, "normals", g.get("normals") if ready else None),
            (5, "alpha2070", g["alpha2070"]),
        ):
            (t or self._dummy_array).use(unit)
            if name in prog:
                prog[name].value = unit
        (g.get("slots2070") or self._dummy_u).use(6)
        if "slots2070" in prog:
            prog["slots2070"].value = 6
        if not ready:
            return
        mats = s.paint_materials
        rep = [float(mats.get(i, {}).get("repetition") or 4) for i in s.ground_ids] + [4.0] * (32 - len(s.ground_ids))
        orders = [mats.get(i, {}).get("order", i) for i in s.ground_ids]
        rank = sorted(range(len(orders)), key=lambda k: orders[k]) + [0] * (32 - len(orders))
        sharp = [float(mats.get(i, {}).get("sharpness", 1.0)) for i in s.ground_ids] + [1.0] * (32 - len(orders))
        prog["layers"].value = len(s.ground_ids)
        prog["repeat_"].write(np.asarray(rep[:32], "f4").tobytes())
        prog["rank"].write(np.asarray(rank[:32], "i4").tobytes())
        prog["sharp"].write(np.asarray(sharp[:32], "f4").tobytes())
        if "side2070" in prog:
            prog["side2070"].value = float(g.get("side2070", 1))
            prog["per2070"].value = float(g.get("per2070", 16))

    def _draw_water(self, view_proj, lt, eye, shadow, sea: bool = True, inland: bool = True) -> None:
        """The sea (without ``sea``: rivers and lakes only), rivers and lakes (without ``inland``: the sea only) as
        plain surfaces: the view's own look, the fallback where the game's own shaders do not draw the water."""
        ctx, wm, fbo = self.ctx, self._water(), self.targets["msaa"]
        prog = self.prog["water"]
        prog["viewProj"].write(gl(view_proj))
        self._set_light(prog, lt, eye, shadow)
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        fbo.depth_mask = False
        prog["waterColor"].value, prog["opacity"].value = srgb_hex(0x1F5F80), 0.78
        if sea:
            self._water_vao(wm["sea"]).render(ctx.TRIANGLE_STRIP)
        prog["waterColor"].value, prog["opacity"].value = srgb_hex(0x357FA3), 0.82
        if inland:
            self._inner_once()
        ctx.disable(ctx.BLEND)
        fbo.depth_mask = True

    def _inner_once(self) -> None:
        """Rivers and lakes each pixel once (the nearest surface): a depth pass, then colour where the depth
        is equal; two see-through surfaces doubled into a light band (2026-10-16)."""
        ctx, wm, fbo = self.ctx, self._water_meshes, self.targets["msaa"]
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
        for _, vbo, ibo in items:
            self._water_vao(vbo, ibo).render()
        ctx.depth_func = "<"
