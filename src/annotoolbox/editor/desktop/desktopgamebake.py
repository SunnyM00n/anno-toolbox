"""The ground of the island view's game look (a mixin of ``desktopgame.CGameIsland``): the game's bake of the shown
nodes' slices (``render.terrainbake``) from the island's material set and material maps (``island_ground``), the
session's paint strokes painted into those maps as the island build paints them (``CPaintedMaps``), and the view's
own bake (its ground drawn from above into the slices) while the game's has not come in or failed.

``CGameIsland.draw`` calls ``_bake_game(models)`` once the game's bake is made (``_game_bake``), else
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
from ...render.nativepass import CComposite
from ..editchange import union_box

GROUND_JOB = "game-island-ground"


def bake_matrix(
    size_tiles: float, min_x: float, min_z: float, tiles: float, depth_tiles: float | None = None
) -> np.ndarray:
    """The view's axes (x mirrored: ``size_tiles - x``) onto a node's square seen from above: min_x..min_x+tiles
    across, min_z at the bottom row of the target (an offscreen target is kept as OpenGL stores it), depth constant;
    ``depth_tiles``: the rectangle's extent along z when it is not square (a trimmed stored mesh's,
    ``terraindraw.mesh_rect``)."""
    depth = depth_tiles or tiles
    matrix = np.zeros((4, 4))
    matrix[0, 0], matrix[0, 3] = -2 / tiles, 2 * (size_tiles - min_x) / tiles - 1
    matrix[1, 2], matrix[1, 3] = 2 / depth, -2 * min_z / depth - 1
    matrix[3, 3] = 1.0
    return matrix


def map_file(files, name: str):
    """A material's map (``x.psd`` names the ``x_0.dds`` the game loads) as a DDS file, None when the game has none."""
    from ...models import ddsdecode, fileresolve

    path = fileresolve.texture_file(name, files.exists) if name else ""
    try:
        return ddsdecode.CDdsFile(files.read(path)) if path else None
    except (ddsdecode.CUnsupportedDds, KeyError, FileNotFoundError):
        return None


def island_ground(editor, kinds: tuple = terrainbake.MAP_KINDS) -> terrainbake.SGroundSource | None:
    """The island's material set and material maps as the game's bake reads them (None for an island without them:
    a new one, another game's); ``kinds`` the maps of a material it reads (1800's bake reads a normal map too). The
    maps are read again from the island's file when the bake let them go on the device (``SGroundSource.reread``)."""
    view = getattr(editor, "m_view", None)
    if view is None or not all(hasattr(view, k) for k in ("material_maps", "materials", "draw_order")):
        return None
    ids, alphas = view.material_maps()
    splats = []
    for material_id, material in sorted(view.materials().items(), key=lambda item: item[1]["order"]):
        maps = tuple(map_file(view.files, material.get("maps", {}).get(kind, "")) for kind in kinds)
        splats.append(terrainbake.SSplat(material_id, material["repetition"], material["sharpness"], material.get("floor", 0.0),
                                        material.get("ceil", 0.0), bool(material.get("add", 0)), maps))  # fmt: skip
    return terrainbake.SGroundSource(splats, ids.copy(), alphas.copy(), float(view.size), reread=view.material_maps)


def baked_with(how: str, page, ready: dict) -> tuple:
    """What a node's slice is baked with: a stored mesh's own uv, its page and the page's textures themselves (a
    released texture's id is given again to the next one, so an id would call a new page the old one)."""
    return (how == "stored", page, ready[page] if page is not None else None)


class CPaintedMaps:
    """The game's bake's material maps (``ground``: a ``render.terrainbake.SGroundSource``, its words changed in place)
    with the session's paint strokes in them as the island build paints them (``groundedit.paint_map``): the
    committed strokes over the island file's maps, the stroke in progress over those again (``order``: material id ->
    place in the set). Until the first stroke ``ground`` may hold no maps at all (the bake let them go on the device,
    ``SGroundSource.release_maps``): they are read again from the island's file then (Egypt's quadrant: 0.3 GB)."""

    def __init__(self, ground, order: dict[int, int]) -> None:
        self.m_ground, self.m_order = ground, order
        self._m_kept = None  # the island file's maps, when they cannot be read again
        self.m_committed: tuple | None = None  # the maps with the committed strokes (made at the first stroke)
        self.m_strokes: list = []  # the committed strokes painted into ``committed``
        self.m_before: list = []  # per committed stroke its box and the words there before it (``_commit``)
        self.m_shown: tuple | None = None  # the box (alpha texels) the stroke in progress was painted into

    def base(self) -> tuple:
        """A copy of the island file's (ids, alphas)."""
        if self.m_ground.reread is not None:
            return tuple(np.array(layer, np.uint32) for layer in self.m_ground.reread())
        return self._m_kept[0].copy(), self._m_kept[1].copy()

    def _start(self) -> None:
        """The first stroke: the file's maps into ``ground`` (read again if the bake let them go), the copies."""
        ground = self.m_ground
        if not ground.has_maps():
            ground.ids, ground.alphas = self.base()
        elif ground.reread is None:
            self._m_kept = (ground.ids.copy(), ground.alphas.copy())
        self.m_committed = (ground.ids.copy(), ground.alphas.copy())

    def _box(self, strokes: list) -> tuple[int, int, int, int]:
        """The alpha texels (min_x, min_z, max_x, max_z: block aligned, one block wider) the strokes reach."""
        from ..groundedit import stroke_box

        texels, block = self.m_ground.alphas.shape[0], self.m_ground.block
        per = texels / self.m_ground.size
        min_x, min_z, max_x, max_z = stroke_box(strokes)
        low = [max(0, int(coordinate * per) // block * block - block) for coordinate in (min_x, min_z)]
        high = [
            min(texels, -(-int(np.ceil(coordinate * per)) // block) * block + block) for coordinate in (max_x, max_z)
        ]
        return low[0], low[1], high[0], high[1]

    def _paint(self, maps: tuple, strokes: list) -> None:
        from ..groundedit import paint_map

        per = self.m_ground.alphas.shape[0] // max(1, round(self.m_ground.size))
        for stroke in strokes:
            paint_map(maps[0], maps[1], [stroke], per, self.m_order)

    def _commit(self, strokes: list) -> list[tuple[int, int, int, int]]:
        """``strokes`` painted into the committed maps one by one, the words of each one's box kept from before it:
        an undo puts them back, where it read the island's maps again and painted every stroke anew (50 to 496 ms on
        the window's thread, performance.md 6, item 7). Returns the boxes."""
        block, boxes = self.m_ground.block, []
        for stroke in strokes:
            box = min_x, min_z, max_x, max_z = self._box([stroke])
            self.m_before.append(
                (
                    box,
                    self.m_committed[0][min_z // block : max_z // block, min_x // block : max_x // block].copy(),
                    self.m_committed[1][min_z:max_z, min_x:max_x].copy(),
                )
            )
            self._paint(self.m_committed, [stroke])
            boxes.append(box)
        return boxes

    def update(self, committed: list, current: dict | None) -> list[tuple[int, int, int, int]]:
        """The maps for the committed strokes and the one in progress; returns the boxes (alpha texels) whose words
        may have changed."""
        from ..groundedit import painted_strokes

        committed, changed = painted_strokes(committed, self.m_order), []
        current = (painted_strokes([current], self.m_order) or [None])[0] if current is not None else None
        if self.m_committed is None:
            if not committed and current is None:
                return []  # nothing painted yet: the maps are the file's
            self._start()
        elif not self.m_ground.has_maps():  # strokes went in before the bake let the maps go: the committed ones back
            self.m_ground.ids, self.m_ground.alphas = self.m_committed[0].copy(), self.m_committed[1].copy()
        texels, block = self.m_ground.alphas.shape[0], self.m_ground.block
        old = self.m_strokes
        kept = all(old_stroke is stroke for old_stroke, stroke in zip(old, committed, strict=False))
        if kept and len(committed) >= len(old):
            changed += self._commit(committed[len(old) :])
        elif kept:  # the last strokes undone: the words under each put back, the newest first
            for box, ids, alphas in reversed(self.m_before[len(committed) :]):
                min_x, min_z, max_x, max_z = box
                self.m_committed[0][min_z // block : max_z // block, min_x // block : max_x // block] = ids
                self.m_committed[1][min_z:max_z, min_x:max_x] = alphas
                changed.append(box)
            del self.m_before[len(committed) :]
        else:  # replaced: all again from the file's maps
            self.m_committed, self.m_before = self.base(), []
            self._commit(committed)
            changed.append((0, 0, texels, texels))
        self.m_strokes = committed
        if self.m_shown is not None:
            changed.append(self.m_shown)
        self.m_shown = self._box([current]) if current is not None else None
        boxes = changed + ([self.m_shown] if self.m_shown is not None else [])
        for min_x, min_z, max_x, max_z in boxes:  # the shown maps: the committed ones there
            self.m_ground.ids[min_z // block : max_z // block, min_x // block : max_x // block] = self.m_committed[0][
                min_z // block : max_z // block, min_x // block : max_x // block
            ]
            self.m_ground.alphas[min_z:max_z, min_x:max_x] = self.m_committed[1][min_z:max_z, min_x:max_x]
        if current is not None:
            self._paint((self.m_ground.ids, self.m_ground.alphas), [current])
        return boxes


class CIslandBake:
    """The ground's methods of ``CGameIsland``, which brings the state they use (``r``, ``terrain``, ``pages``,
    ``bake``, ``quality`` and the bake's bookkeeping). The view's own bake goes by tables a game's look sets."""

    # a node's bake targets (name, texel type): the ground's albedo (linear) and its detail normal
    LANE_TARGETS = (("albedo", "f2"), ("normal", "f1"))
    ENCODES: dict = {}  # target -> (the target it reads, the pass's fragment shader), after the ground's two
    STORES = {"cBakedDiffTexArray": "albedo", "cBakedNormalTexArray": "normal"}  # the shader's name <- a target
    ARRAYS = True  # into arrays of a slice a node; False: a node's own textures (``terrain.slot_textures``)
    ARRAY_FORMATS = {"cBakedDiffTexArray": QRhiTexture.Format.RGBA16F}  # the rest RGBA8
    LANES = 0  # nodes baked a frame, each into targets of its own; 0: all of them into the same targets
    _m_native_bake_off = False  # the native bake failed once: the current path's bake from then on

    def _bake_state(self, options) -> tuple:
        """What the view's own ground depends on: its layers, which of them have their looks, the weights texture,
        the look."""
        ground = self.m_renderer.m_ground
        # the texture itself, not its id: a released one's id is given again to the next
        return (ground.get("ids"), ground.get("known"), self.m_renderer._m_weights_tex, "albedo" in self.m_renderer.m_textures,
                options.look2070)  # fmt: skip

    def _ground(self) -> tuple:
        """In the background: the island's ground for the game's bake, in a tuple (its ground may be None)."""
        return (island_ground(self.m_renderer.m_editor),)

    def _game_bake(self) -> bool:
        """Whether the game's bake makes the ground: it is made once its shaders and the island's ground are in, the
        nodes made again to read its arrays; an island without a ground (or its error, said) keeps our own."""
        if self.m_bake is not None:
            return True
        if self._m_bake_stages is None:
            return False
        got = self.m_renderer.m_loader.get(GROUND_JOB, self._ground)
        if isinstance(got, Exception):
            self.m_renderer._message(tr("islands.games_ground_bake", reason=f"{type(got).__name__}: {got}"[:300]))
        if not isinstance(got, tuple) or got[0] is None:
            if got is not None:
                self._m_bake_stages = None
            return False
        size_texels = self.m_quality.bake_size if self.m_quality is not None else terrainbake.SIZE
        self.m_bake = self._new_bake(got[0], size_texels)
        self.m_renderer.m_loader.forget(
            GROUND_JOB
        )  # its maps' files go once on the device: a new bake reads the ground again
        session = self.m_renderer.m_session
        # the session's strokes into that ground's maps
        if session.m_edits.get("paint") and session.m_weights is not None:
            edge = session.m_weights.shape[-1] - 1
            self._m_pending.weights = union_box(self._m_pending.weights, (0, 0, edge, edge))
        self.m_terrain.m_textures.update(self.m_bake.m_arrays)
        self.m_terrain.set_heights(self.m_terrain.m_heights, None)
        self._m_baked_with.clear()
        self._drop_own_bake()
        return True

    def _new_bake(self, ground, size_texels: int):
        """The game's bake of ``ground`` at ``size_texels`` texels a node (1800's and 2205's looks: their own bake): by the
        game's shaders on the native backend where they read and the pages are the native ones (or none: it reads
        the module's), else by the current path's."""
        from ...render import bakenative

        quality = self.m_quality
        most = quality.max_texture if quality is not None else 0
        handover = getattr(self, "_m_handover", None)
        pages_ok = self.m_pages is None or getattr(self.m_pages, "m_native", False)
        if handover is not None and pages_ok and bakenative.is_enabled() and not self._m_native_bake_off:
            try:
                return bakenative.CNativeBake(self._m_device, self._m_natives, handover, ground, self._m_bake_stages["flip"],
                                             size_texels=size_texels, max_texture=most)  # fmt: skip
            except (
                Exception
            ) as exc:  # pattern: ok (a call into the native module; said once, the current path's from now on)
                self._m_native_bake_off = True
                self.m_renderer._message(tr("islands.games_native_bake", reason=f"{type(exc).__name__}: {exc}"[:300]))
        return terrainbake.CBake(self.m_renderer.m_ctx, self._m_bake_stages, ground, size_texels=size_texels, alike=self.m_terrain.m_island,
                                max_texture=most)  # fmt: skip

    def _drop_native_bake(self, why: str) -> None:
        """The native bake failed (said once): the current path's bake from the next frame, of the ground read
        again (the native one let its maps go), the nodes made again to read its arrays."""
        self.m_renderer._message(tr("islands.games_native_bake", reason=why[:300]))
        for name in self.m_bake.m_arrays:
            self.m_terrain.m_textures.pop(name, None)
        self.m_bake.release()
        self.m_bake, self._m_painted, self._m_bake_key, self._m_unbaked = None, None, None, 0
        self._m_native_bake_off = True
        self._m_baked_with.clear()
        self.m_terrain.set_heights(self.m_terrain.m_heights, None)

    def _drop_bake(self, why: str) -> None:
        """The game's bake failed (said once): our own bake from now on, the nodes made again without its arrays."""
        self.m_renderer._message(tr("islands.games_ground_bake", reason=why[:300]))
        for name in self.m_bake.m_arrays:
            self.m_terrain.m_textures.pop(name, None)
        self.m_bake.release()
        self.m_bake, self._m_bake_stages, self._m_bake_key, self._m_unbaked = None, None, None, 0
        self.m_terrain.set_heights(self.m_terrain.m_heights, None)

    def _bake_game(self, models: list) -> None:
        """The game's bake of the shown nodes whose slice is new, spoilt or was baked from another page (a few a
        frame, nearest first): each from the page it draws with, else from its vertices."""
        terrain, pages = self.m_terrain, self.m_pages
        box, self._m_pending.weights = self._m_pending.weights, None
        if box is not None:
            self._paint(box)
        jobs, self._m_unbaked = [], 0
        ready = pages.m_ready if pages is not None else {}
        for model in models:
            how, page, _ = model.m_kind
            page = page if how != "grid" and page in ready else None
            made = baked_with(how, page, ready)
            if model.m_node not in terrain.m_cache.m_fresh and self._m_baked_with.get(model.m_node) == made:
                continue
            if len(jobs) == terrainbake.PER_FRAME:
                self._m_unbaked += 1
                continue
            jobs.append((self._bake_model(model.m_node, page), terrain.m_cache.m_slices[model.m_node]))
            terrain.m_cache.baked(model.m_node)
            self._m_baked_with[model.m_node] = made
        self._m_unbaked += len(jobs)  # busy for the frame that bakes them too: a picture is taken a frame past the last
        self.m_bake.run(self.m_renderer.m_ctx, jobs)

    def _bake_model(self, node, page):
        """A node's draw into the game's bake: a stored mesh itself, as the engine bakes a node's own mesh (its u
        and v span its own rectangle: ``terraindraw.mesh_rect``), else its height samples; its page's textures when
        it draws with one."""
        terrain, bake = self.m_terrain, self.m_bake
        model, side = terrain.m_models.get(node), None
        if model is not None and model.m_kind[0] == "stored":
            mesh, side = model.m_mesh, max(model.m_rect[2:])
        else:
            mesh = terrainbake.bake_mesh(
                terrain.m_heights, terrain.m_step_tiles, node, terrain.m_width_samples, terrain.normals()
            )
        page_parameters = terrainpages.page_params(page, terrain.m_size_tiles) if page is not None else None
        values = terrainbake.bake_values(
            node, bake.m_ground, terrain.m_island, page_parameters, bake.m_size_texels, side
        )
        own = {}
        if page is not None:
            height_page, normal_page = self.m_pages.m_ready[page]
            own = {
                "cHeightPageTex": height_page,
                "cNormalPageTex": normal_page,
                "cTerrainHeightTex": self.m_pages.m_coarse,
            }
        return bake.model(mesh, values, own)

    def _paint(self, box) -> None:
        """The paint changed (``box``: weight texels): the strokes painted into the bake's material maps as the
        island build paints them, the words that changed written, the nodes over them baked again."""
        session, bake = self.m_renderer.m_session, self.m_bake
        if self._m_painted is None:
            self._m_painted = CPaintedMaps(bake.m_ground, self.m_renderer.m_editor.m_view.draw_order())
        texel = bake.m_ground.texel
        for min_x, min_z, max_x, max_z in self._m_painted.update(
            list(session.m_edits.get("paint", [])), session.paint_stroke
        ):
            ground = bake.m_ground
            block = ground.block
            bake.paint(
                min_x,
                min_z,
                ground.ids[min_z // block : max_z // block, min_x // block : max_x // block],
                ground.alphas[min_z:max_z, min_x:max_x],
            )
            self.m_terrain.m_cache.spoil([min_x * texel, min_z * texel, max_x * texel, max_z * texel])
        if session.m_weights is not None:  # what the weights say changed, whatever the strokes give
            tiles = session.m_size_tiles / max(1, session.m_weights.shape[-1] - 1)
            self.m_terrain.m_cache.spoil([edge * tiles for edge in box])

    def _spoil(self, options) -> None:
        """The slices whose ground changed: all when the view's ground state changed, else where a paint stroke
        went."""
        terrain, session = self.m_terrain, self.m_renderer.m_session
        key = self._bake_state(options)
        box, self._m_pending.weights = self._m_pending.weights, None
        if key != self._m_bake_key:
            self._m_bake_key = key
            terrain.m_cache.spoil((0.0, 0.0, session.m_size_tiles, session.m_size_tiles))
        elif box is not None and session.m_weights is not None:
            tiles = session.m_size_tiles / max(1, session.m_weights.shape[-1] - 1)  # weight texels -> tiles
            terrain.m_cache.spoil([edge * tiles for edge in box])

    def _bake_own(self, options, shown: list) -> None:
        """The view's own ground drawn from above into the slices of the shown nodes that have none yet or whose
        ground changed (``_spoil``): its colour (linear) and its detail normal, the look's encode passes over them
        (``ENCODES``), and the result where the terrain shader reads it (``_store``)."""
        self._spoil(options)
        terrain, session = self.m_terrain, self.m_renderer.m_session
        # a node drawn from its stored mesh: over that mesh's rectangle
        rects = {node: self._rect(node) for node in shown}
        todo = [node for node in shown if node in terrain.m_cache.m_fresh or self._m_own_rects.get(node) != rects[node]]
        todo = todo[: self.LANES or None]
        if not todo:
            return
        self._bake_targets()
        program, ctx = self.m_renderer.m_prog["terrain"], self.m_renderer.m_ctx
        self.m_renderer._bind_ground(program, options)
        slice_size = terraindraw.SLICE_SIZE
        for i, node in enumerate(todo):
            lane = self._lane(i if self.LANES else 0)
            min_x, min_z, width_tiles, depth_tiles = self._m_own_rects[node] = rects[node]
            program["viewProj"].write(gl(bake_matrix(session.m_size_tiles, min_x, min_z, width_tiles, depth_tiles)))
            for out, name in ((1, "albedo"), (2, "normal")):
                framebuffer = lane[name][0]
                ctx.disable(ctx.DEPTH_TEST)
                ctx.disable(ctx.CULL_FACE)
                framebuffer.use()
                ctx.viewport = (0, 0, slice_size, slice_size)
                framebuffer.clear(0.0, 0.0, 0.0, 1.0)
                program["bakeOut"].value = out
                self.m_renderer.m_terrain_vao.render()
            for target, (source, _shader) in self.ENCODES.items():
                self.m_composites[target].draw(lane[target][0], {source: lane[source][1]}, {}, depth_test=False)
            self._store(lane, terrain.m_cache.m_slices[node])
            terrain.m_cache.baked(node)
        program["bakeOut"].value = 0
        ctx.enable(ctx.DEPTH_TEST)

    def _rect(self, node) -> tuple:
        """The rectangle (min_x, min_z, width, depth) a node's u and v span: its model's, else its square."""
        model = self.m_terrain.m_models.get(node)
        return model.m_rect if model is not None and model.m_rect else (node.x, node.z, node.tiles, node.tiles)

    def _store(self, lane: dict, layer: int) -> None:
        """A node's targets where the shader reads them (``STORES``): the arrays at its slice, or its own
        textures."""
        ctx = self.m_renderer.m_ctx
        into, target_layer = (self.m_arrays, layer) if self.ARRAYS else (self.m_terrain.slot_textures(layer), 0)
        for name, source in self.STORES.items():
            texture = lane[source][1]

            def copy(array=into[name], source_texture=texture):
                return copy_into(
                    ctx.m_rhi, array, source_texture.native, (source_texture.width, source_texture.height), target_layer
                )

            ctx.native_pass(lambda command_buffer, batch, copy=copy: carry(command_buffer, batch, None, copy()))

    def _lane(self, i: int) -> dict:
        """The ``i``-th node's bake targets this frame (``LANE_TARGETS``: name -> (framebuffer, texture)), made on
        first use."""
        ctx, slice_size = self.m_renderer.m_ctx, terraindraw.SLICE_SIZE
        while len(self.m_lanes) <= i:
            lane = {}
            for name, dtype in self.LANE_TARGETS:
                texture = ctx.texture((slice_size, slice_size), 4, dtype=dtype)
                lane[name] = (ctx.framebuffer([texture]), texture)
            self.m_lanes.append(lane)
        return self.m_lanes[i]

    def _bake_targets(self) -> None:
        """The encode passes and the arrays the terrain shader reads (``STORES``), made once."""
        ctx, slice_size = self.m_renderer.m_ctx, terraindraw.SLICE_SIZE
        if not self.m_composites:
            self.m_composites = {
                target: CComposite(ctx, fragment_shader) for target, (_source, fragment_shader) in self.ENCODES.items()
            }
        for name in self.STORES if self.ARRAYS else ():
            if name not in self.m_arrays:
                texture_format = self.ARRAY_FORMATS.get(name, QRhiTexture.Format.RGBA8)
                array = ctx.m_rhi.newTextureArray(
                    texture_format, terraindraw.SLICES, QSize(slice_size, slice_size), 1, QRhiTexture.Flag(0)
                )
                array.create()
                self.m_arrays[name] = array
            self.m_terrain.m_textures[name] = self.m_arrays[name]

    def _drop_own_bake(self) -> None:
        """The view's own bake's lanes, encode passes and arrays let go (the game's bake draws the ground: 0.1 GB
        on the device at Medium); made again if the game's bake fails."""
        for lane in self.m_lanes:
            for framebuffer, texture in lane.values():
                framebuffer.release()
                texture.release()
        for part in self.m_composites.values():
            part.release()
        for array in self.m_arrays.values():
            self.m_renderer.m_ctx.later(array)
        self.m_lanes, self.m_composites, self.m_arrays = [], {}, {}
        self._m_own_rects = {}
