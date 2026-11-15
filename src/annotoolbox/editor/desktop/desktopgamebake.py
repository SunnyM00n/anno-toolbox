"""The ground of the island view's game look (a mixin of ``desktopgame.GameIsland``): the game's bake of the shown
nodes' slices (``render.terrainbake``) from the island's material set and material maps (``island_ground``), the
session's paint strokes painted into those maps as the island build paints them (``PaintedMaps``), and the view's
own bake (its ground drawn from above into the slices) while the game's has not come in or failed.

``GameIsland.draw`` calls ``_bake_game(models)`` once the game's bake is made (``_game_bake``), else
``_bake_own(opts, nodes)``; docs/rendering/island-117-view.md, phase 3.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QRhiTexture

from ...infra.i18n import tr
from ...render import terrainbake, terraindraw, terrainpages
from ...render.camera import gl
from ...render.gamecompute import carry, copy_into
from ...render.nativepass import Composite
from ..editchange import union_box

GROUND_JOB = "game-island-ground"


def bake_matrix(size: float, x0: float, z0: float, tiles: float, depth: float | None = None) -> np.ndarray:
    """The view's axes (x mirrored: ``size - x``) onto a node's square seen from above: x0..x0+tiles across, z0 at
    the bottom row of the target (an offscreen target is kept as OpenGL stores it), depth constant; ``depth``: the
    rectangle's extent along z when it is not square (a trimmed stored mesh's, ``terraindraw.mesh_rect``)."""
    depth = depth or tiles
    m = np.zeros((4, 4))
    m[0, 0], m[0, 3] = -2 / tiles, 2 * (size - x0) / tiles - 1
    m[1, 2], m[1, 3] = 2 / depth, -2 * z0 / depth - 1
    m[3, 3] = 1.0
    return m


def map_file(files, name: str):
    """A material's map (``x.psd`` names the ``x_0.dds`` the game loads) as a DDS file, None when the game has none."""
    from ...models import ddsdecode, fileresolve

    path = fileresolve.texture_file(name, files.exists) if name else ""
    try:
        return ddsdecode.DdsFile(files.read(path)) if path else None
    except (ddsdecode.UnsupportedDds, KeyError, FileNotFoundError):
        return None


def island_ground(editor, kinds: tuple = terrainbake.MAP_KINDS) -> terrainbake.GroundSource | None:
    """The island's material set and material maps as the game's bake reads them (None for an island without them:
    a new one, another game's); ``kinds`` the maps of a material it reads (1800's bake reads a normal map too). The
    maps are read again from the island's file when the bake let them go on the device (``GroundSource.reread``)."""
    view = getattr(editor, "view", None)
    if view is None or not all(hasattr(view, k) for k in ("material_maps", "materials", "draw_order")):
        return None
    ids, alphas = view.material_maps()
    splats = []
    for mid, m in sorted(view.materials().items(), key=lambda item: item[1]["order"]):
        maps = tuple(map_file(view.files, m.get("maps", {}).get(kind, "")) for kind in kinds)
        splats.append(terrainbake.Splat(mid, m["repetition"], m["sharpness"], m.get("floor", 0.0),
                                        m.get("ceil", 0.0), bool(m.get("add", 0)), maps))  # fmt: skip
    return terrainbake.GroundSource(splats, ids.copy(), alphas.copy(), float(view.size), reread=view.material_maps)


class PaintedMaps:
    """The game's bake's material maps (``ground``: a ``render.terrainbake.GroundSource``, its words changed in place)
    with the session's paint strokes in them as the island build paints them (``groundedit.paint_map``): the
    committed strokes over the island file's maps, the stroke in progress over those again (``order``: material id ->
    place in the set). Until the first stroke ``ground`` may hold no maps at all (the bake let them go on the device,
    ``GroundSource.release_maps``): they are read again from the island's file then (Egypt's quadrant: 0.3 GB)."""

    def __init__(self, ground, order: dict[int, int]) -> None:
        self.ground, self.order = ground, order
        self._kept = None  # the island file's maps, when they cannot be read again
        self.committed: tuple | None = None  # the maps with the committed strokes (made at the first stroke)
        self.strokes: list = []  # the committed strokes painted into ``committed``
        self.shown: tuple | None = None  # the box (alpha texels) the stroke in progress was painted into

    def base(self) -> tuple:
        """A copy of the island file's (ids, alphas)."""
        if self.ground.reread is not None:
            return tuple(np.array(m, np.uint32) for m in self.ground.reread())
        return self._kept[0].copy(), self._kept[1].copy()

    def _start(self) -> None:
        """The first stroke: the file's maps into ``ground`` (read again if the bake let them go), the copies."""
        g = self.ground
        if not g.has_maps():
            g.ids, g.alphas = self.base()
        elif g.reread is None:
            self._kept = (g.ids.copy(), g.alphas.copy())
        self.committed = (g.ids.copy(), g.alphas.copy())

    def _ok(self, strokes: list) -> list:
        return [st for st in strokes if int(st.get("material", -1)) in self.order and int(st["material"]) < 32]

    def _box(self, strokes: list) -> tuple[int, int, int, int]:
        """The alpha texels (x0, z0, x1, z1: block aligned, one block wider) the strokes reach."""
        from ..groundedit import stroke_box

        n, b = self.ground.alphas.shape[0], self.ground.block
        per = n / self.ground.size
        x0, z0, x1, z1 = stroke_box(strokes)
        lo = [max(0, int(v * per) // b * b - b) for v in (x0, z0)]
        hi = [min(n, -(-int(np.ceil(v * per)) // b) * b + b) for v in (x1, z1)]
        return lo[0], lo[1], hi[0], hi[1]

    def _paint(self, maps: tuple, strokes: list) -> None:
        from ..groundedit import paint_map

        per = self.ground.alphas.shape[0] // max(1, round(self.ground.size))
        for st in strokes:
            paint_map(maps[0], maps[1], [st], per, self.order)

    def update(self, committed: list, current: dict | None) -> list[tuple[int, int, int, int]]:
        """The maps for the committed strokes and the one in progress; returns the boxes (alpha texels) whose words
        may have changed."""
        committed, changed = self._ok(committed), []
        current = (self._ok([current]) or [None])[0] if current is not None and current.get("points") else None
        if self.committed is None:
            if not committed and current is None:
                return []  # nothing painted yet: the maps are the file's
            self._start()
        elif not self.ground.has_maps():  # strokes went in before the bake let the maps go: the committed ones back
            self.ground.ids, self.ground.alphas = self.committed[0].copy(), self.committed[1].copy()
        n, b = self.ground.alphas.shape[0], self.ground.block
        old = self.strokes
        if len(committed) >= len(old) and all(a is c for a, c in zip(old, committed, strict=False)):
            new = committed[len(old) :]
            self._paint(self.committed, new)
            changed += [self._box([st]) for st in new]
        else:  # undone or replaced: all again from the file's maps
            self.committed = self.base()
            self._paint(self.committed, committed)
            changed.append((0, 0, n, n))
        self.strokes = committed
        if self.shown is not None:
            changed.append(self.shown)
        self.shown = self._box([current]) if current is not None else None
        boxes = changed + ([self.shown] if self.shown is not None else [])
        for x0, z0, x1, z1 in boxes:  # the shown maps: the committed ones there
            self.ground.ids[z0 // b : z1 // b, x0 // b : x1 // b] = self.committed[0][
                z0 // b : z1 // b, x0 // b : x1 // b
            ]
            self.ground.alphas[z0:z1, x0:x1] = self.committed[1][z0:z1, x0:x1]
        if current is not None:
            self._paint((self.ground.ids, self.ground.alphas), [current])
        return boxes


class IslandBake:
    """The ground's methods of ``GameIsland``, which brings the state they use (``r``, ``terrain``, ``pages``,
    ``bake``, ``quality`` and the bake's bookkeeping). The view's own bake goes by tables a game's look sets."""

    # a node's bake targets (name, texel type): the ground's albedo (linear) and its detail normal
    LANE_TARGETS = (("albedo", "f2"), ("normal", "f1"))
    ENCODES: dict = {}  # target -> (the target it reads, the pass's fragment shader), after the ground's two
    STORES = {"cBakedDiffTexArray": "albedo", "cBakedNormalTexArray": "normal"}  # the shader's name <- a target
    ARRAYS = True  # into arrays of a slice a node; False: a node's own textures (``terrain.slot_textures``)
    ARRAY_FORMATS = {"cBakedDiffTexArray": QRhiTexture.Format.RGBA16F}  # the rest RGBA8
    LANES = 0  # nodes baked a frame, each into targets of its own; 0: all of them into the same targets

    def _bake_state(self, opts) -> tuple:
        """What the view's own ground depends on: its layers, which of them have their looks, the weights texture,
        the look."""
        g = self.r.ground
        return (g.get("ids"), g.get("known"), id(self.r._weights_tex), "albedo" in self.r.textures,
                opts.look2070)  # fmt: skip

    def _ground(self) -> tuple:
        """In the background: the island's ground for the game's bake, in a tuple (its ground may be None)."""
        return (island_ground(self.r.editor),)

    def _game_bake(self) -> bool:
        """Whether the game's bake makes the ground: it is made once its shaders and the island's ground are in, the
        nodes made again to read its arrays; an island without a ground (or its error, said) keeps our own."""
        if self.bake is not None:
            return True
        if self._bake_stages is None:
            return False
        got = self.r.loader.get(GROUND_JOB, self._ground)
        if isinstance(got, Exception):
            self.r._message(tr("the game's ground bake: {reason}", reason=f"{type(got).__name__}: {got}"[:300]))
        if not isinstance(got, tuple) or got[0] is None:
            if got is not None:
                self._bake_stages = None
            return False
        size = self.quality.bake_size if self.quality is not None else terrainbake.SIZE
        self.bake = self._new_bake(got[0], size)
        self.r.loader.forget(GROUND_JOB)  # its maps' files go once on the device: a new bake reads the ground again
        s = self.r.session
        if s.edits.get("paint") and s.weights is not None:  # the session's strokes into that ground's maps
            n = s.weights.shape[-1] - 1
            self._pending.weights = union_box(self._pending.weights, (0, 0, n, n))
        self.terrain.textures.update(self.bake.arrays)
        self.terrain.set_heights(self.terrain.heights, None)
        self._baked_with.clear()
        self._drop_own_bake()
        return True

    def _new_bake(self, ground, size: int):
        """The game's bake of ``ground`` at ``size`` texels a node (1800's and 2205's looks: their own bake)."""
        q = self.quality
        return terrainbake.Bake(self.r.ctx, self._bake_stages, ground, size=size, alike=self.terrain.island,
                                max_texture=q.max_texture if q is not None else 0)  # fmt: skip

    def _drop_bake(self, why: str) -> None:
        """The game's bake failed (said once): our own bake from now on, the nodes made again without its arrays."""
        self.r._message(tr("the game's ground bake: {reason}", reason=why[:300]))
        for name in self.bake.arrays:
            self.terrain.textures.pop(name, None)
        self.bake.release()
        self.bake, self._bake_stages, self._bake_key, self._unbaked = None, None, None, 0
        self.terrain.set_heights(self.terrain.heights, None)

    def _bake_game(self, models: list) -> None:
        """The game's bake of the shown nodes whose slice is new, spoilt or was baked from another page (a few a
        frame, nearest first): each from the page it draws with, else from its vertices."""
        terrain, pages = self.terrain, self.pages
        box, self._pending.weights = self._pending.weights, None
        if box is not None:
            self._paint(box)
        jobs, self._unbaked = [], 0
        ready = pages.ready if pages is not None else {}
        for m in models:
            how, page, _ = m.kind
            page = page if how != "grid" and page in ready else None
            made = (how == "stored", page, id(ready[page]) if page is not None else 0)  # a stored mesh's own uv
            if m.node not in terrain.cache.fresh and self._baked_with.get(m.node) == made:
                continue
            if len(jobs) == terrainbake.PER_FRAME:
                self._unbaked += 1
                continue
            jobs.append((self._bake_model(m.node, page), terrain.cache.slices[m.node]))
            terrain.cache.baked(m.node)
            self._baked_with[m.node] = made
        self._unbaked += len(jobs)  # busy for the frame that bakes them too: a picture is taken a frame past the last
        self.bake.run(self.r.ctx, jobs)

    def _bake_model(self, node, page):
        """A node's draw into the game's bake: a stored mesh itself, as the engine bakes a node's own mesh (its u
        and v span its own rectangle: ``terraindraw.mesh_rect``), else its height samples; its page's textures when
        it draws with one."""
        t, bake = self.terrain, self.bake
        model, side = t.models.get(node), None
        if model is not None and model.kind[0] == "stored":
            mesh, side = model.mesh, max(model.rect[2:])
        else:
            mesh = terrainbake.bake_mesh(t.heights, t.step, node, t.width, t.normals())
        params = terrainpages.page_params(page, t.size) if page is not None else None
        values = terrainbake.bake_values(node, bake.ground, t.island, params, bake.size, side)
        own = {}
        if page is not None:
            height, normal = self.pages.ready[page]
            own = {"cHeightPageTex": height, "cNormalPageTex": normal, "cTerrainHeightTex": self.pages.coarse}
        return terrainbake.bake_model(self.r.ctx.rhi, bake.program, mesh, values, own, bake.common)

    def _paint(self, box) -> None:
        """The paint changed (``box``: weight texels): the strokes painted into the bake's material maps as the
        island build paints them, the words that changed written, the nodes over them baked again."""
        s, bake = self.r.session, self.bake
        if self._painted is None:
            self._painted = PaintedMaps(bake.ground, self.r.editor.view.draw_order())
        texel = bake.ground.texel
        for x0, z0, x1, z1 in self._painted.update(list(s.edits.get("paint", [])), s.paint_stroke):
            g = bake.ground
            b = g.block
            bake.paint(x0, z0, g.ids[z0 // b : z1 // b, x0 // b : x1 // b], g.alphas[z0:z1, x0:x1])
            self.terrain.cache.spoil([x0 * texel, z0 * texel, x1 * texel, z1 * texel])
        if s.weights is not None:  # what the weights say changed, whatever the strokes give
            tiles = s.size / max(1, s.weights.shape[-1] - 1)
            self.terrain.cache.spoil([v * tiles for v in box])

    def _spoil(self, opts) -> None:
        """The slices whose ground changed: all when the view's ground state changed, else where a paint stroke
        went."""
        terrain, s = self.terrain, self.r.session
        key = self._bake_state(opts)
        box, self._pending.weights = self._pending.weights, None
        if key != self._bake_key:
            self._bake_key = key
            terrain.cache.spoil((0.0, 0.0, s.size, s.size))
        elif box is not None and s.weights is not None:
            tiles = s.size / max(1, s.weights.shape[-1] - 1)  # weight texels -> tiles
            terrain.cache.spoil([b * tiles for b in box])

    def _bake_own(self, opts, shown: list) -> None:
        """The view's own ground drawn from above into the slices of the shown nodes that have none yet or whose
        ground changed (``_spoil``): its colour (linear) and its detail normal, the look's encode passes over them
        (``ENCODES``), and the result where the terrain shader reads it (``_store``)."""
        self._spoil(opts)
        terrain, s = self.terrain, self.r.session
        rects = {n: self._rect(n) for n in shown}  # a node drawn from its stored mesh: over that mesh's rectangle
        todo = [n for n in shown if n in terrain.cache.fresh or self._own_rects.get(n) != rects[n]]
        todo = todo[: self.LANES or None]
        if not todo:
            return
        self._bake_targets()
        prog, ctx = self.r.prog["terrain"], self.r.ctx
        self.r._bind_ground(prog, opts)
        n = terraindraw.SLICE_SIZE
        for i, node in enumerate(todo):
            lane = self._lane(i if self.LANES else 0)
            x0, z0, w, d = self._own_rects[node] = rects[node]
            prog["viewProj"].write(gl(bake_matrix(s.size, x0, z0, w, d)))
            for out, name in ((1, "albedo"), (2, "normal")):
                fbo = lane[name][0]
                ctx.disable(ctx.DEPTH_TEST)
                ctx.disable(ctx.CULL_FACE)
                fbo.use()
                ctx.viewport = (0, 0, n, n)
                fbo.clear(0.0, 0.0, 0.0, 1.0)
                prog["bakeOut"].value = out
                self.r.terrain_vao.render()
            for target, (source, _fs) in self.ENCODES.items():
                self.composites[target].draw(lane[target][0], {source: lane[source][1]}, {}, depth_test=False)
            self._store(lane, terrain.cache.slices[node])
            terrain.cache.baked(node)
        prog["bakeOut"].value = 0
        ctx.enable(ctx.DEPTH_TEST)

    def _rect(self, node) -> tuple:
        """The rectangle (x0, z0, width, depth) a node's u and v span: its model's, else its square."""
        model = self.terrain.models.get(node)
        return model.rect if model is not None and model.rect else (node.x, node.z, node.tiles, node.tiles)

    def _store(self, lane: dict, layer: int) -> None:
        """A node's targets where the shader reads them (``STORES``): the arrays at its slice, or its own
        textures."""
        ctx = self.r.ctx
        into, at = (self.arrays, layer) if self.ARRAYS else (self.terrain.slot_textures(layer), 0)
        for name, source in self.STORES.items():
            tex = lane[source][1]
            copy = lambda a=into[name], t=tex: copy_into(ctx.rhi, a, t.native, (t.width, t.height), at)  # noqa: E731
            ctx.native_pass(lambda cb, batch, copy=copy: carry(cb, batch, None, copy()))

    def _lane(self, i: int) -> dict:
        """The ``i``-th node's bake targets this frame (``LANE_TARGETS``: name -> (framebuffer, texture)), made on
        first use."""
        ctx, n = self.r.ctx, terraindraw.SLICE_SIZE
        while len(self.lanes) <= i:
            lane = {}
            for name, dtype in self.LANE_TARGETS:
                tex = ctx.texture((n, n), 4, dtype=dtype)
                lane[name] = (ctx.framebuffer([tex]), tex)
            self.lanes.append(lane)
        return self.lanes[i]

    def _bake_targets(self) -> None:
        """The encode passes and the arrays the terrain shader reads (``STORES``), made once."""
        ctx, n = self.r.ctx, terraindraw.SLICE_SIZE
        if not self.composites:
            self.composites = {target: Composite(ctx, fs) for target, (_source, fs) in self.ENCODES.items()}
        for name in self.STORES if self.ARRAYS else ():
            if name not in self.arrays:
                fmt = self.ARRAY_FORMATS.get(name, QRhiTexture.Format.RGBA8)
                array = ctx.rhi.newTextureArray(fmt, terraindraw.SLICES, QSize(n, n), 1, QRhiTexture.Flag(0))
                array.create()
                self.arrays[name] = array
            self.terrain.textures[name] = self.arrays[name]

    def _drop_own_bake(self) -> None:
        """The view's own bake's lanes, encode passes and arrays let go (the game's bake draws the ground: 0.1 GB
        on the device at Medium); made again if the game's bake fails."""
        for lane in self.lanes:
            for fbo, tex in lane.values():
                fbo.release()
                tex.release()
        for part in self.composites.values():
            part.release()
        for array in self.arrays.values():
            self.r.ctx.later(array)
        self.lanes, self.composites, self.arrays = [], {}, {}
        self._own_rects = {}
