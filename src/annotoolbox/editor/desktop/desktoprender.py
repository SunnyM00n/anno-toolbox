"""The PC application's 3D renderer: an edit session's island drawn with moderngl, by 117's rules where the
switches ask for them (``docs/rendering/``): terrain, ground, sea, rivers and lakes, every model instanced once per
config, markers as pins, sun shadows, the sky, and the frame finished as the editor, the game or 2070 does
(island-editor-view "The 3D view", "Drawn by 117's rules").

No Qt here: ``desktopview`` puts it in a window, ``scripts/editor_shots.py`` renders it offscreen. It draws on
demand only (island-editor-view "GPU load").
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np

from ...render import decalmesh, viewcolors
from ...render import shaders as S
from ...render.camera import COrbitCamera, gl, look_at, ortho
from ...render.cull import CCellTable, CInstanceCells, CTerrainChunks, SLodCamera, frustum_planes, reach
from ...render.light import light, srgb_hex
from ...render.loader import VIEW_WORKERS, CLoader
from ...render.modellibrary import BANNER_SHADER, FOAM_SHADER, WATER_SHADER, CModelLibrary, bounds, instance_matrices
from ...render.particledraw import CParticleLayer
from ...render.quality import SQuality, lod_rules, remakes
from ..editchange import SChange, union_box
from ..editsession import CEditSession, SEntry
from .desktopbatches import PASSES, CBatch, CInstanceSets, level_meshes
from .desktopdecals import CDecalLayer
from .desktopframe import CFramePasses, SFrame
from .desktopgeometry import pin_mesh, terrain_vertices
from .desktopground import CGroundDraw
from .desktoppick import CPicking

LAYERS = ("objects", "markers", "props", "grass", "forest", "water")
# The threads that load the models. They parse and decode in Python, so more of them do not load faster, they take
# the window's thread's turns at the interpreter lock: with 4 a continental island's frames took 7 to 13 seconds
# while its models came, with 2 up to 6 and the scene was in sooner (known-issues "Island editor held the window
# while an island opened").
MODEL_WORKERS = 2
FRAME_BUDGET_S = 0.03  # what one frame's scene uploads may take (the rest follows in the next frames)
SHADOW_SIZE_PX = 2048  # the view's own sun shadow map at the best quality (``render.quality``: half the game frame's)


@dataclass
class SViewOptions:
    """What the view draws and how: its layers, the look (the game's own shaders, ``native``, where they draw; the
    view's own look the fallback) and the switches the Rendering menu holds (island-editor-view.md "Looks")."""

    layers: dict = field(default_factory=lambda: dict.fromkeys(LAYERS, True))
    shadows: bool = True
    game_terrain: bool = False  # the saved island's near terrain from its height pages (``gameterrain``), edits on top
    game_light: bool = False  # the game's light, which the game's look draws in (``native`` sets it; no switch)
    look2070: bool = False
    underwater: bool = False  # 2070's look seen under water (``render.light.UNDERWATER``); implies look2070
    caustics: bool = False  # ... with moving light patterns on what lies below
    scheme: str = "light"  # the editor look's sky and fog follow the window's theme (viewcolors)
    cull: bool = True  # draw only what the camera sees (render.cull)
    particles: bool = False  # the effects placed in models (render.particledraw)
    particle_time: float | None = None  # ... played at this many seconds, a still picture (None: the live clock)
    season_ms: float | None = None  # Egypt's seasons this far into the session (None: the view's clock from its start)
    native: bool = False  # the island's game's own shaders on Direct3D (``desktopgame.CGameIsland``), in game light
    quality: SQuality | None = None  # the rendering quality (``render.quality``); None: the best


def shrink(raw: bytes, size_px: tuple[int, int], component_count: int, max_side: int) -> tuple[tuple[int, int], bytes]:
    """A map's pixels halved (each texel the mean of four) until neither side is above ``most`` (0: as it is)."""
    width_px, height_px = size_px
    if not max_side or max(width_px, height_px) <= max_side:
        return size_px, raw
    pixels = np.frombuffer(raw, np.uint8).reshape(height_px, width_px, component_count).astype(np.float32)
    while max(width_px, height_px) > max_side and min(width_px, height_px) >= 2:
        height_px, width_px = height_px // 2, width_px // 2
        pixels = (
            pixels[: height_px * 2, : width_px * 2]
            .reshape(height_px, 2, width_px, 2, component_count)
            .mean(axis=(1, 3))
        )
    return (width_px, height_px), np.ascontiguousarray(np.round(pixels).astype(np.uint8)).tobytes()


def shadow_side(quality: SQuality | None) -> int:
    """The view's own shadow map's side at quality ``q``: half the game frame's atlas (a cascade's), at most
    ``SHADOW_SIZE_PX``."""
    return SHADOW_SIZE_PX if quality is None else max(512, min(SHADOW_SIZE_PX, quality.shadow_size // 2))


class CRenderer(CGroundDraw, CFramePasses, CPicking):
    """Draws an ``CEditSession``; ``editor`` (an ``IslandEditor``) supplies models, textures and the game data."""

    def __init__(self, ctx, session: CEditSession, editor=None) -> None:
        self.m_ctx, self.m_session, self.m_editor = ctx, session, editor
        self.m_size_tiles = float(session.m_size_tiles)
        make_program = ctx.program
        self.m_prog = {
            "terrain": make_program(vertex_shader=S.TERRAIN_VS, fragment_shader=S.TERRAIN_FS),
            "model": make_program(vertex_shader=S.MODEL_VS, fragment_shader=S.MODEL_FS),
            "banner": make_program(vertex_shader=S.BANNER_VS, fragment_shader=S.MODEL_FS),
            "depth_model": make_program(vertex_shader=S.MODEL_VS, fragment_shader=S.DEPTH_FS),
            "depth_banner": make_program(vertex_shader=S.BANNER_VS, fragment_shader=S.DEPTH_FS),
            "pick_model": make_program(vertex_shader=S.pick_vs(S.MODEL_VS), fragment_shader=S.PICK_FS),
            "pick_banner": make_program(vertex_shader=S.pick_vs(S.BANNER_VS), fragment_shader=S.PICK_FS),
            "flat": make_program(vertex_shader=S.FLAT_VS, fragment_shader=S.FLAT_FS),
            "sky": make_program(vertex_shader=S.SKY_VS, fragment_shader=S.SKY_FS),
            "water": make_program(vertex_shader=S.WATER_VS, fragment_shader=S.WATER_PLAIN_FS),
            "final": make_program(vertex_shader=S.FINAL_VS, fragment_shader=S.FINAL_FS),
            "overlay": make_program(vertex_shader=S.OVERLAY_VS, fragment_shader=S.OVERLAY_FS),
        }
        self.m_fullscreen = {name: ctx.vertex_array(self.m_prog[name], []) for name in ("sky", "final")}
        self.m_library = CModelLibrary(editor, MODEL_WORKERS) if editor is not None else None
        self.m_particles = CParticleLayer(ctx, self.m_library, self._all_batches, self._gpu_texture)
        self.m_decals = CDecalLayer(ctx)
        self.m_street_cells: list | None = None  # 2070's stored streets as decals (``_street_cells``)
        self._m_ground_version = 0  # counts the terrain's changes (the decals are laid again)
        self.m_loader = CLoader(VIEW_WORKERS)  # the game shaders compile side by side
        self.m_batches: dict[tuple, CBatch] = {}
        self.m_textures: dict = {}
        self.m_meshes: dict = {}
        self.m_stats: dict[str, int] = {}  # the last frame: draw calls, instances and terrain indices drawn / in all
        self._m_pending = SChange(
            structure=True, heights=(0, 0, session.m_size_tiles, session.m_size_tiles), water=True
        )
        self._m_lock = threading.Lock()
        self.m_messages: list[str] = []
        self.m_targets = None
        self._m_shadow = None
        self._m_pick = None
        self.m_ghost: CBatch | None = None  # the library pick under the cursor (Place)
        self._m_start = time.monotonic()
        self.m_animated = False  # flowing water or a flipbook is on screen: the view keeps ticking
        session.m_listeners.append(self.on_change)
        self._upload_terrain()
        self._m_dummy = ctx.texture((1, 1), 4, bytes([128, 128, 128, 255]))
        self._m_dummy_array = ctx.texture_array((1, 1, 1), 1, bytes(1))
        self._m_dummy_u = ctx.texture((1, 1), 1, bytes([255]), dtype="u1")
        self._m_dummy_u.m_filter = (ctx.NEAREST if hasattr(ctx, "NEAREST") else 0x2600,) * 2
        self._m_dummy_3d = ctx.texture3d((2, 2, 2), 3, np.zeros(24, "f4").tobytes(), dtype="f4")
        self._m_dummy_shadow = ctx.depth_texture((1, 1), np.ones(1, "f4").tobytes())
        self._m_dummy_shadow.compare_func = "<="
        self.m_ground = {"ids": None, "known": None, "asked": None, "size": 0, "alpha2070": None}
        self._m_weights_tex = None
        self._m_weights_dirty = None
        self._m_water_meshes = None
        self._m_dots = None
        self._m_sync_left = 0  # the batches the last ``_sync_batches`` left for the next frame (``FRAME_BUDGET_S``)
        self.m_game = None  # desktopgame.CGameIsland, made when the look first asks for it
        self.m_remake = False  # the game look made anew at the next frame (``remake_game``)
        self.m_seas: list = []  # desktopsea.CIslandSea: the old games' seas by their own shaders (2070, 1404, 1701), on QRhi
        self.m_game_parts: set[tuple] = set()  # (batch key, part index) the game's shaders drew this frame
        self.m_game_sea = False  # the game's sea drew this frame (the view's own sea is left out)
        self.m_drawn_opaque: list = []  # this frame's opaque (batch, part, VAOs) in view
        self.m_game_inland = False  # the game's rivers and lakes drew this frame (the view's own are left out)
        self.m_game_decals = False  # the game's decal shader drew the config decals (the view's own are left out)
        self.m_baked_decal = (
            None  # which decals the game's ground bake drew this frame (1800's; the view's leaves them)
        )
        self._m_members: dict = {}  # program -> its uniform names (``_set_light``)
        self._m_lit: dict = {}  # program -> whether this frame's light is set with the shadow (``_draw_part``)
        self._m_table = None  # (key, CCellTable, batch index): every batch's cells (``_pass_codes``)
        self._m_frame_codes: dict = {}  # pass -> this frame's codes of every cell
        self._m_frame_lod: SLodCamera | None = None
        self._m_max_texture = 0  # the largest side a model's map is uploaded at (0: as it is; ``render.quality``)
        # the models are asked for by the first frame, once its look says which load in full (``_sync_full``)
        if editor is not None:
            self.m_loader.get("albedo", self._albedo)
            self.m_loader.get("ground", self._ground_info)

    # changes from the session
    def on_change(self, change: SChange) -> None:
        with self._m_lock:
            pending = self._m_pending
            pending.entries |= change.entries
            pending.structure |= change.structure
            pending.water |= change.water
            pending.materials |= change.materials
            for name in ("heights", "weights"):
                box = getattr(change, name)
                if box is not None:
                    setattr(pending, name, union_box(getattr(pending, name), box))

    def _take_pending(self) -> SChange:
        with self._m_lock:
            pending, self._m_pending = self._m_pending, SChange()
        return pending

    def _request_models(self) -> None:
        if self.m_library is not None:
            self.m_library.request(
                {batch_key[1] for batch_key in self.m_session.m_batches if batch_key[0] != "markers" and batch_key[1]}
            )

    def busy(self) -> int:
        """Things still loading: models, textures, ground data."""
        count = self.m_loader.pending() + self._m_sync_left + getattr(self.m_ctx, "m_make_left", 0)
        count += self.m_decals.is_laying()
        if self.m_library is not None:
            count += sum(
                1
                for batch_key in self.m_session.m_batches
                if batch_key[0] != "markers" and batch_key[1] and not self.m_library.ready(batch_key[1])
            )
            count += sum(1 for batch in self.m_batches.values() if batch.m_lods_waiting)  # their coarser levels
            count += self.m_library.textures_pending()
        if self.m_editor is not None and self.m_ground["ids"] != tuple(self.m_session.m_ground_ids):
            count += 1  # the ground's materials
        return count + self.m_particles.pending() + (self.m_game.busy() if self.m_game is not None else 0)

    def changed(self) -> bool:
        """Something new arrived from the background since the last frame."""
        out = False
        for event in (self.m_loader.m_changed, self.m_library.m_changed if self.m_library else None):
            if event is not None and event.is_set():
                event.clear()
                out = True
        return out

    # GPU uploads
    def _upload_terrain(self) -> None:
        session = self.m_session
        width = session.m_width_samples
        self._m_terrain_heights = session.m_heights
        self.m_terrain_vbo = self.m_ctx.buffer(
            terrain_vertices(session.m_heights, session.m_step_tiles).tobytes(), dynamic=True
        )
        self.m_terrain_chunks = CTerrainChunks(width, session.m_step_tiles)
        self.m_terrain_chunks.set_heights(session.m_heights)
        self.m_terrain_ibo = self.m_ctx.buffer(self.m_terrain_chunks.m_indices.tobytes())
        self.m_terrain_vao = self.m_ctx.vertex_array(
            self.m_prog["terrain"],
            [(self.m_terrain_vbo, "3f 3f", "pos", "normal")],
            index_buffer=self.m_terrain_ibo,
            index_element_size=4,
            skip_errors=True,
        )
        self._m_shown_game_terrain = False

    def _update_terrain(self, box, game: np.ndarray | None) -> None:
        session = self.m_session
        heights = game if game is not None else session.m_heights
        if box is None and (game is not None) == self._m_shown_game_terrain:
            return
        if (game is not None) != self._m_shown_game_terrain:
            box = (0, 0, session.m_size_tiles, session.m_size_tiles)
            self._m_shown_game_terrain = game is not None
        first_row, last_row = (
            max(0, box[1] * session.m_samples_per_tile - 1),
            min(session.m_width_samples, box[3] * session.m_samples_per_tile + 2),
        )
        vertices = terrain_vertices(heights, session.m_step_tiles, slice(first_row, last_row))
        self.m_terrain_vbo.write(vertices.tobytes(), offset=first_row * session.m_width_samples * 24)
        self.m_terrain_chunks.set_heights(heights)

    def _gpu_texture(self, key: str, srgb: bool):
        if key in self.m_textures:
            return self.m_textures[key]
        data = self.m_library.texture(key) if self.m_library else None
        if data is None:
            return None  # still loading
        if data is False:  # a colour map that is not there or does not decode: the missing-file checker
            missing = key[:2] in ("d:", "fx") and viewcolors.missing_texture(self.m_ctx, self.m_textures)
            return self.m_textures.setdefault(key, missing or None)
        width_px, height_px, component_count, raw = data
        if raw is None:  # uploaded once, the pixels dropped (``drop_pixels``)
            return self.m_textures.get(key)
        internal_format = (0x8C43 if component_count == 4 else 0x8C41) if srgb else None  # SRGB8_ALPHA8, SRGB8
        (width_px, height_px), raw = shrink(raw, (width_px, height_px), component_count, self._m_max_texture)
        texture = self.m_ctx.texture((width_px, height_px), component_count, raw, internal_format=internal_format)
        texture.build_mipmaps()
        texture.anisotropy = 8.0
        texture.repeat_x = texture.repeat_y = True
        self.m_textures[key] = texture
        self.m_library.drop_pixels(key)  # the particles read only an effect map's size
        return texture

    def _vaos(self, vertices: np.ndarray, indices: np.ndarray, banner: bool) -> dict:
        ctx = self.m_ctx
        vbo, ibo = ctx.buffer(vertices.tobytes()), ctx.buffer(indices.tobytes())
        vertex_format = (
            ("3f 3f 2f 2f", "pos", "normal", "uv", "corner") if banner else ("3f 3f 2f", "pos", "normal", "uv")
        )
        out = {}
        for name in ("banner", "depth_banner", "pick_banner") if banner else ("model", "depth_model", "pick_model"):
            out[name.split("_")[0]] = (vbo, ibo, vertex_format, self.m_prog[name])
        return out

    def _bind(self, spec, instances) -> object:
        """A mesh's VAO with the instance buffer ``inst``."""
        vbo, ibo, vertex_format, program = spec
        return self.m_ctx.vertex_array(
            program,
            [(vbo, *vertex_format), (instances, "16f/i", "inst")],
            index_buffer=ibo,
            index_element_size=4,
            skip_errors=True,
        )

    def _make_vaos(self, batch: CBatch) -> None:
        """The batch's instance sets (one buffer per level and pass) and its meshes' VAOs: per pass and level the
        meshes drawn at that level (``level_meshes``), picking's over every instance."""
        batch.release_draws()
        batch.m_sets = {name: CInstanceSets(self.m_ctx, batch.m_count, batch.m_levels) for name in PASSES}
        batch.m_vaos = []
        for (part, spec), per in zip(
            batch.m_parts, level_meshes(batch.m_parts, batch.m_lods, batch.m_levels), strict=True
        ):
            kind = "banner" if part.get("shader") == BANNER_SHADER else "model"
            vaos = {"pick": self._bind(spec["pick"], batch.m_inst)}
            for name in PASSES:
                buffers = batch.m_sets[name].m_buffers
                vaos[name] = [[self._bind(level_spec[kind if name == "model" else "depth"], buffers[k]) for level_spec in specs]
                              for k, specs in enumerate(per)]  # fmt: skip
            batch.m_vaos.append((part, vaos))

    def _mesh_specs(self, key: tuple, vertices: np.ndarray, indices: np.ndarray, banner: bool) -> dict:
        """A mesh's buffers and programs per pass (``_vaos``), made once per mesh."""
        if key not in self.m_meshes:
            self.m_meshes[key] = self._vaos(vertices, indices, banner=banner)
        return self.m_meshes[key]

    def _batch_parts(self, batch: CBatch) -> list | None:
        layer, name = batch.m_key
        if layer == "markers":
            if "pin" not in self.m_meshes:
                self.m_meshes["pin"] = self._vaos(*pin_mesh(), banner=False)
            color = viewcolors.MARKER_COLORS.get(name, 0xFFFFFF)
            part = {"shader": 8, "color": srgb_hex(color), "diffuse": "", "normal": "", "matrix": np.identity(4, "f4"),
                    "emissive": 0.35, "marker": True}  # fmt: skip
            batch.m_box = (np.array([-0.55, 0, -0.55], "f4"), np.array([0.55, 2.45, 0.55], "f4"))
            batch.m_reach = reach(batch.m_box)
            return [(part, self.m_meshes["pin"])]
        if self.m_library is None or not name or not self.m_library.ready(name):
            return None
        parts = self.m_library.parts(name)
        meshes, out = {}, []
        batch.m_lods, batch.m_levels, batch.m_lods_waiting = {}, 1, []
        batch.m_lean = not self.m_library.full(name)  # the game's look draws it: its parts' boxes alone (picking)
        for model_part in parts:
            prims = (
                self.m_library.box(model_part["mesh_key"])
                if batch.m_lean
                else self.m_library.mesh(model_part["mesh_key"])
            ) or []
            meshes[model_part["mesh_key"]] = prims
            kind = "box" if batch.m_lean else "mesh"
            for k, (vertices, indices) in enumerate(prims):
                out.append(
                    (
                        model_part,
                        self._mesh_specs(
                            (model_part["mesh_key"], kind, k), vertices, indices, model_part["shader"] == BANNER_SHADER
                        ),
                    )
                )
            if batch.m_lean:
                continue
            levels = self.m_library.lods(model_part)
            if levels is None:
                batch.m_lods_waiting.append(model_part)
            if levels and model_part["shader"] != BANNER_SHADER:
                batch.m_lods[id(model_part)] = [[self._mesh_specs((model_part["mesh_key"], "lod", level, k), level_vertices, i, False)
                                      for k, (level_vertices, i) in enumerate(prims)]
                                     for level, prims in enumerate(levels, 1)]  # fmt: skip
                batch.m_levels = max(batch.m_levels, len(levels) + 1)
        batch.m_box = bounds(parts, meshes)
        batch.m_reach = reach(batch.m_box)
        if batch.m_box is not None:
            batch.m_radius_m = max(1.0, float(np.linalg.norm(batch.m_box[1] - batch.m_box[0])) / 2)
        return out

    def _sync_batches(self, change: SChange) -> None:
        session = self.m_session
        if change.structure:
            self._request_models()
            for key in [batch_key for batch_key in self.m_batches if batch_key not in session.m_batches]:
                self.m_batches.pop(key).release_draws()
            for key, rows in session.m_batches.items():
                batch = self.m_batches.get(key)
                if batch is None:
                    self.m_batches[key] = CBatch(key, rows)
                else:
                    batch.m_entries, batch.m_dirty = rows, True
            self._m_dots = None
        for entry in change.entries:
            batch = self.m_batches.get(entry.batch)
            if batch is not None:
                batch.m_dirty = True
                if batch.m_parts is None:
                    self._m_dots = None
        start, self._m_sync_left = time.perf_counter(), 0
        for batch in self._all_batches():
            if (batch.m_dirty or batch.m_inst is None) and time.perf_counter() - start > FRAME_BUDGET_S:
                self._m_sync_left += 1  # a continental island's thousands of batches took seconds in one frame
                continue
            if batch.m_dirty or batch.m_inst is None:
                self._write_instances(batch)
                if batch.m_parts is None:
                    self._m_dots = None  # its dots (``_dots_vao``) come from the matrices just written
            if batch.m_parts is None or any(self.m_library.lods(part) is not None for part in batch.m_lods_waiting):
                ready = batch.m_key[0] == "markers" or (
                    self.m_library is not None and self.m_library.ready(batch.m_key[1])
                )
                if ready and time.perf_counter() - start > FRAME_BUDGET_S:
                    self._m_sync_left += 1  # its meshes and buffers on the device: in a later frame
                    continue
                parts = self._batch_parts(batch)  # the model arrived, or coarser levels of its parts did
                if parts is not None:
                    self._m_dots = None if batch.m_parts is None else self._m_dots
                    batch.m_parts = parts
                    self._make_vaos(batch)

    def _write_instances(self, batch: CBatch) -> None:
        rows = batch.m_entries
        count = len(rows)
        positions = np.array([entry.position for entry in rows], "f4").reshape(count, 3)
        rotations = np.array([entry.rotation for entry in rows], "f4").reshape(count, 4)
        scales = np.array(
            [entry.scale if isinstance(entry.scale, list) else [entry.scale] * 3 for entry in rows], "f4"
        ).reshape(count, 3)
        scales[[not entry.shown for entry in rows]] = 0
        matrices = (
            instance_matrices(positions, rotations, scales, self.m_size_tiles) if count else np.zeros((0, 4, 4), "f4")
        )
        batch.m_mats, batch.m_version = matrices, batch.m_version + 1
        data = np.ascontiguousarray(matrices.transpose(0, 2, 1)).tobytes()
        batch.m_cells = CInstanceCells(matrices) if count else None
        if batch.m_inst is None or batch.m_count != count:
            if batch.m_inst is not None:
                batch.m_inst.release()
            batch.m_inst = self.m_ctx.buffer(data or bytes(64), dynamic=True)
            batch.m_count = count
            if batch.m_parts is not None:
                self._make_vaos(batch)
        elif data:
            batch.m_inst.write(data)
        for sets in batch.m_sets.values():
            sets.m_key = None  # the instances moved: written again
        batch.m_dirty = False

    def _cull(self, batch: CBatch, planes, lod: SLodCamera | None = None) -> int:
        """Put the batch's instances in view into its main pass's buffers by level of detail (``planes``: None draws
        all at the finest; ``lod`` None: every instance in view at the finest, none left out as too small); how
        many."""
        count = self._sort(batch, "model", planes, lod if batch.m_key[0] != "markers" else None)
        self.m_stats["instances"] += count
        self.m_stats["instances_of"] += batch.m_count
        return count

    def _sort(self, batch: CBatch, name: str, planes, lod: SLodCamera | None) -> int:
        """The batch's instances into pass ``name``'s buffers by level: by 117's rule for ``lod``, those in view at
        the finest without; how many."""
        if batch.m_cells is None or name not in batch.m_sets:
            return 0
        cells = batch.m_cells
        if planes is None:
            codes = np.zeros(len(cells.m_counts), np.int64)
        else:
            table, index, every = self._pass_codes(name, planes, lod or self._m_frame_lod)
            batch_index = index.get(id(batch))
            codes = (
                table.of(every, batch_index)
                if batch_index is not None
                else np.where(cells.visible(planes, batch.m_reach), 0, -2)
            )
            if lod is None:  # a marker: whatever its size on screen
                codes = np.where(codes > -2, 0, -2)
        return batch.m_sets[name].update(cells, codes)

    def _pass_codes(self, name: str, planes, lod: SLodCamera | None) -> tuple:
        """(table, batch index, every cell's code) of pass ``name`` this frame (``CCellTable.codes``), worked out once
        a frame over the cells of every batch; the table is made again when a batch's cells changed."""
        got = self._m_frame_codes.get(name)
        if got is None:
            batches = [batch for batch in self._all_batches() if batch.m_cells is not None and batch.m_sets]
            # the objects themselves, not their ids: a freed batch's or cells' id is soon another's
            key = tuple((batch, batch.m_cells, batch.m_reach, batch.m_levels) for batch in batches)
            if self._m_table is None or self._m_table[0] != key:
                table = CCellTable([(batch.m_cells, batch.m_reach, batch.m_levels) for batch in batches])
                self._m_table = (key, table, {id(batch): k for k, batch in enumerate(batches)})
            got = self._m_frame_codes[name] = (*self._m_table[1:], self._m_table[1].codes(planes, lod))
        return got

    def _draw_terrain(self, planes) -> None:
        """The terrain chunks in view, neighbours as one draw (``planes`` None: all in one)."""
        total = len(self.m_terrain_chunks.m_indices)
        runs = self.m_terrain_chunks.runs(planes) if planes is not None else [(0, total)]
        if sum(length for _, length in runs) == total:
            runs = [(0, total)]
        for first, count in runs:
            self.m_terrain_vao.render(vertices=count, first=first)
        self.m_stats["terrain"], self.m_stats["terrain_of"] = sum(length for _, length in runs), total
        self.m_stats["calls"] += len(runs)

    def _dots_vao(self):
        if self._m_dots is None:
            # from the instance matrices (the view's x mirrored already; a scale of 0 hides one): a loop over every
            # entry each time a model came took seconds on a continental island's 48,000
            rows = []
            for batch in self.m_batches.values():
                if (batch.m_parts is None or not batch.m_parts) and batch.m_mats is not None and len(batch.m_mats):
                    shown = np.abs(batch.m_mats[:, :3, :3]).sum(axis=(1, 2)) > 0
                    rows.append(batch.m_mats[shown, :3, 3] + np.array([0.0, 0.3, 0.0], "f4"))
            positions = np.concatenate(rows) if rows else np.zeros((0, 3), "f4")
            points = np.hstack([positions, np.full((len(positions), 3), 0.95, "f4")]).astype("f4")
            self._m_dots = (
                self.m_ctx.vertex_array(
                    self.m_prog["flat"], [(self.m_ctx.buffer(points.tobytes()), "3f 3f", "pos", "rgb")]
                )
                if len(points)
                else False
            )
        return self._m_dots

    def _message(self, text: str) -> None:
        if text not in self.m_messages:
            self.m_messages.append(text)

    def _names(self, program):
        """A program's uniform names, for ``in`` (moderngl's own walks every member: 40 % of a frame of many
        parts)."""
        names = self._m_members.get(program)
        if names is None:
            names = self._m_members[program] = set(program) if hasattr(program, "__iter__") else program
        return names

    def _set_light(self, program, lighting: dict, eye, shadow, env: float = 1.0) -> None:
        names = self._names(program)

        def put(name, value):
            if name in names:
                program[name].value = value

        put("sunDir", tuple(float(component) for component in lighting["sunDir"]))
        put("sunColor", lighting["sunColor"])
        put("ambientMode", lighting["ambientMode"])
        put("hemiSky", lighting.get("hemiSky", (0, 0, 0)))
        put("hemiGround", lighting.get("hemiGround", (0, 0, 0)))
        put("envLight", lighting.get("envLight", 0.0) * env)
        put("flatAmbient", lighting.get("flatAmbient", (0, 0, 0)))
        put("shFactor", lighting.get("shFactor", 0.0))
        if "sh" in lighting and "sh" in names:
            program["sh"].write(np.asarray(lighting["sh"], "f4").tobytes())
        near, far = lighting.get("fog", (self.m_size_tiles * 1.6, self.m_size_tiles * 4))
        put("fogColor", lighting["fogColor"])
        put("fogNear", near)
        put("fogFar", far)
        put("eye", tuple(float(component) for component in eye))
        put("uwLight", lighting.get("uwLight", (0, 0, 0)))  # all zero: not the underwater look
        put("uwFx", (lighting.get("uwCaustics", 0.0), lighting["uwTime"]))
        put("shadowOn", shadow is not None)
        if "shadowMatrix" in names:
            program["shadowMatrix"].write(gl(shadow[1] if shadow is not None else np.identity(4)))
        if "shadowMap" in names:
            (shadow[0] if shadow is not None else self._m_dummy_shadow).use(7)
            program["shadowMap"].value = 7

    # frames
    def _targets(self, width_px: int, height_px: int, single: bool = False):
        """The frame's targets, multisampled unless ``single`` (the game's 2070 sea reads the frame's depth)."""
        samples = 1 if single else min(4, self.m_ctx.max_samples)
        if (
            self.m_targets is None
            or self.m_targets["size"] != (width_px, height_px)
            or self.m_targets["samples"] != samples
        ):
            if self.m_targets is not None:
                for name in ("color", "depth", "msaa", "resolved", "resolve"):
                    self.m_targets[name].release()
            ctx = self.m_ctx
            color = ctx.renderbuffer((width_px, height_px), 4, samples=samples, dtype="f2")
            depth = ctx.depth_renderbuffer((width_px, height_px), samples=samples)
            resolved = ctx.texture((width_px, height_px), 4, dtype="f2")
            resolved.m_filter = (0x2601, 0x2601)
            self.m_targets = {
                "size": (width_px, height_px),
                "samples": samples,
                "color": color,
                "depth": depth,
                "msaa": ctx.framebuffer([color], depth),
                "resolved": resolved,
                "resolve": ctx.framebuffer([resolved]),
            }
        return self.m_targets

    def _shadow_pass(
        self, view_target, distance_m: float, lighting: dict, options: SViewOptions, lod: SLodCamera | None
    ):
        ctx = self.m_ctx
        side = shadow_side(options.quality)
        if self._m_shadow is not None and self._m_shadow[0].size[0] != side:  # the quality changed
            for part in self._m_shadow:
                part.release()
            self._m_shadow = None
        if self._m_shadow is None:
            shadow_texture = ctx.depth_texture((side, side))
            shadow_texture.compare_func = "<="
            shadow_texture.repeat_x = shadow_texture.repeat_y = False
            self._m_shadow = (shadow_texture, ctx.framebuffer(depth_attachment=shadow_texture))
        shadow_texture, fbo = self._m_shadow
        reach = min(400.0, max(40.0, distance_m * 0.9))
        sun = np.asarray(lighting["sunDir"], "f4")
        target = np.asarray(view_target, "f4")
        light_view = look_at(target + sun * reach * 2, target)
        matrix = ortho(reach, 1.0, reach * 4) @ light_view
        fbo.use()
        fbo.clear(depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
        planes, texel = frustum_planes(matrix), 2 * reach / side
        programs = {kind: self.m_prog["depth_" + kind] for kind in ("model", "banner")}
        for program in programs.values():  # the sun's camera, once a frame
            program["viewProj"].write(gl(matrix))
            if "view" in self._names(program):
                program["view"].write(gl(light_view))
        for batch in self.m_batches.values():
            if (
                not self._layer_on(batch, options)
                or batch.m_key[0] in ("grass", "markers")
                or not batch.m_parts
                or not batch.m_count
                or batch.m_lean
            ):
                continue
            if batch.m_radius_m * batch.m_cells.m_scale < texel or not self._sort(batch, "depth", planes, lod):
                continue  # smaller than a shadow texel, or outside the sun's box
            counts = batch.m_sets["depth"].m_counts
            for model_part, vaos in batch.m_vaos:
                if model_part["shader"] in (WATER_SHADER, FOAM_SHADER):
                    continue
                program = programs["banner" if model_part["shader"] == BANNER_SHADER else "model"]
                program["part"].write(gl(model_part["matrix"]))
                diffuse_texture = (
                    self._gpu_texture("d:" + model_part["diffuse"], True) if model_part["diffuse"] else None
                )
                if "textured" in self._names(program):
                    program["textured"].value = diffuse_texture is not None
                if diffuse_texture is not None:
                    diffuse_texture.use(0)
                    program["diffuseTex"].value = 0
                self.m_stats["shadow_calls"] += self._render_levels(vaos["depth"], counts)
        return shadow_texture, matrix

    def _layer_on(self, batch: CBatch, options: SViewOptions) -> bool:
        return options.layers.get(batch.m_key[0], True)

    def render(
        self,
        fbo,
        size_px: tuple[int, int],
        camera: COrbitCamera,
        options: SViewOptions,
        scale_factor: float = 1.0,
        overlays=(),
    ) -> None:
        """Draw a frame into ``fbo`` (``size`` pixels) as ``camera`` sees it; the scene at ``scale`` of that (the
        GPU's load). The water surfaces are the saved island's: the water tool draws the edited network itself."""
        ctx = self.m_ctx
        if options.native:  # the game's shaders light by the game's light
            options = replace(options, game_light=True, look2070=False, underwater=False, caustics=False)
        game = self._game_drawable(options)  # the game's frame casts its own shadows: the view's pass would be waste
        self._sync_full(options)
        game_heights = self._sync_scene(options)
        lighting = light(options, self.m_size_tiles)
        lighting["uwTime"] = (
            options.particle_time if options.particle_time is not None else time.monotonic() - self._m_start
        )
        self.m_stats = dict.fromkeys(("calls", "instances", "instances_of", "terrain", "terrain_of", "shadow_calls"), 0)
        self._m_lit, self._m_frame_codes = {}, {}
        quality = options.quality
        scale_factor *= quality.render_scale if quality is not None else 1.0
        self._m_max_texture = quality.max_texture if quality is not None else 0  # maps uploaded from now on
        width_px, height_px = max(1, int(size_px[0] * scale_factor)), max(1, int(size_px[1] * scale_factor))
        game_sea = self._game_sea(options)
        targets = self._targets(width_px, height_px, single=game_sea is not None)
        view, eye = camera.view(), camera.eye()
        proj = camera.proj(size_px[0] / max(1, size_px[1]))
        view_proj = proj @ view
        planes = frustum_planes(view_proj) if options.cull else None
        rules = lod_rules(quality)
        lod = self._m_frame_lod = (
            SLodCamera(np.asarray(eye, float), view, proj, (width_px, height_px), rules) if options.cull else None
        )
        cast = options.shadows and (quality is None or quality.shadows) and not game
        shadow = self._shadow_pass(camera.m_target, camera.m_distance_m, lighting, options, lod) if cast else None
        targets["msaa"].use()
        ctx.viewport = (0, 0, width_px, height_px)
        targets["msaa"].clear(0, 0, 0, 1, depth=1.0)
        self._draw_sky(view_proj, lighting, eye)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        frame = SFrame(view, view_proj, eye, planes, camera.m_distance_m, lighting, shadow, scale_factor, game_heights)
        frame.lod = lod
        self.m_game_parts, self.m_game_sea, self.m_game_inland, self.m_game_decals = set(), False, False, False
        self.m_baked_decal = None
        drew = game and self._draw_game(frame, options, targets["msaa"], (width_px, height_px), camera)
        if not drew:
            self._draw_ground(frame, options)
        # the view keeps drawing while the game's look makes parts ahead of their first draw
        # and while its water and clouds move with the clock or it settles (bake, pages, cascades: ``CGameIsland``)
        game_moves = drew and (self.m_game.m_objects.m_ahead_left or self.m_game.m_animated or self.m_ctx.m_make_left)
        self.m_animated = bool(options.underwater and options.caustics) or bool(game_moves) or self._m_sync_left > 0
        frame.now = time.monotonic() - self._m_start
        streets, models, translucent = self._visible_parts(frame, options)
        self.m_drawn_opaque = streets + models  # the game's sea mirrors them (``desktopsea``)
        if not self.m_game_decals:  # the game's decal shader laid them itself: the view's own are not drawn
            self._lay_decals(frame, options)
        if not self.m_game_decals:  # the game's decal shader drew them into its G-buffer
            # the game's ground stage (render.decalmesh); 2070's stored streets, laid as decals, after the decals
            self.m_decals.draw(self, frame, decalmesh.BEFORE_STREETS, decalmesh.STREET_CELLS)
        for batch, part, vaos in streets:
            self._draw_part(batch, part, vaos, frame)
        if not self.m_game_decals:
            self.m_decals.draw(self, frame, decalmesh.AFTER_STREETS)
        for batch, part, vaos in models:
            self._draw_part(batch, part, vaos, frame)
        if (
            not self.m_game_decals
        ):  # the game lays vehicle tracks only behind moving vehicles (island-117-view "Round 12")
            self.m_decals.draw(self, frame, decalmesh.VEHICLE)
        self._draw_dots(frame)
        if game_sea is not None:
            self.m_game_sea = game_sea.draw(frame, options, targets, camera, (width_px, height_px))
        self._draw_sea(frame, options)
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        targets["msaa"].depth_mask = False
        for batch, part, vaos in translucent:
            self._draw_part(batch, part, vaos, frame)
        self.m_animated |= self.m_particles.draw(options, camera, view_proj, planes, frame.now)
        targets["msaa"].depth_mask = True
        ctx.disable(ctx.BLEND)
        self._finish(targets, fbo, size_px, lighting, drew)
        self.draw_overlays(view_proj, overlays)

    def _game_drawable(self, options: SViewOptions) -> bool:
        """Whether the look asks for the game's own shaders and they can draw now (``desktopgame``; why not is said
        once)."""
        if not options.native:
            return False
        fallback = set()
        again = self.m_remake or (self.m_game is not None and self.m_game.m_terrain is not None
                                and remakes(self.m_game.m_quality, options.quality))  # fmt: skip
        if self.m_game is not None and again:
            self.m_game.release()  # built for another quality or way: its shadow map, maps and terrain programs anew
            fallback, self.m_game = self.m_game.m_objects.m_fallback, None  # the view's look keeps drawing what it drew
        if self.m_remake:  # its shaders read again: the native ones or none (``desktopgame.native_shaders``)
            from .desktopgame import JOB

            self.m_loader.forget(JOB)
            self.m_remake = False
        if self.m_game is None:
            from .desktopgame import CGameIsland  # Qt's QRhi: only for this look

            self.m_game = CGameIsland(self)
            self.m_game.m_objects.m_fallback = set(fallback)
        why = self.m_game.why_not()
        if why:
            self._message(why)
            return False
        return self.m_game.compiled()

    def remake_game(self) -> None:
        """The game look made again from the next frame (View > Rendering's switch of the shipped shaders changed:
        ``render.nativegpu.want``); the view's own look draws while it loads."""
        self.m_remake = True

    def _game_sea(self, options: SViewOptions):
        """The island's sea by its game's own shaders where it draws (2070's ``desktopsea2070``, 1404's
        ``desktopsea1404``, 1701's ``desktopsea1701``), else None."""
        if not hasattr(self.m_ctx, "native_pass"):
            return None
        if not self.m_seas:
            from .desktopsea1404 import CGameSea1404  # Qt's QRhi: only on its device
            from .desktopsea1701 import CGameSea1701
            from .desktopsea2070 import CGameSea2070

            self.m_seas = [CGameSea2070(self), CGameSea1404(self), CGameSea1701(self)]
        return next((sea for sea in self.m_seas if sea.wanted(options)), None)

    def _draw_game(self, frame: SFrame, options: SViewOptions, target, size_px: tuple[int, int], camera) -> bool:
        """The terrain by the game's own shaders (``_game_drawable`` said they can); False: the view draws its own
        ground."""
        return self.m_game.draw(frame, options, target, size_px, camera)

    def _draw_part(self, batch: CBatch, part: dict, vaos: dict, frame: SFrame) -> None:
        banner = part["shader"] == BANNER_SHADER
        water = part["shader"] == WATER_SHADER
        foam = part["shader"] == FOAM_SHADER and part.get("foam")  # a sheet's two speeds and opacity
        program = self.m_prog["banner" if banner else "model"]
        names = self._names(program)
        unshadowed = bool(water or foam)  # these parts take no shadow: the program's light is set for that kind
        if self._m_lit.get(program) != unshadowed:  # the frame's camera and light, once per program and kind of part
            program["viewProj"].write(gl(frame.view_proj))
            if "view" in names:
                program["view"].write(gl(frame.view))
            self._set_light(program, frame.lt, frame.eye, None if unshadowed else frame.shadow)
            self._m_lit[program] = unshadowed
        program["part"].write(gl(part["matrix"]))
        texture = self._gpu_texture("d:" + part["diffuse"], not foam) if part.get("diffuse") else None
        normal_texture = self._gpu_texture("n:" + part["normal"], False) if part.get("normal") and not banner else None
        program["textured"].value = texture is not None
        (texture or self._m_dummy).use(0)
        program["diffuseTex"].value = 0
        if "hasNormal" in names:
            program["hasNormal"].value = normal_texture is not None
            (normal_texture or self._m_dummy).use(2)
            program["normalTex"].value = 2
        color = part["color"] if texture is not None or part.get("marker") or banner or water else (0.6, 0.6, 0.6)
        if water:
            white = any(
                word in (part.get("diffuse") or "").lower() for word in ("white", "foam", "splash", "spray", "mist")
            )
            color = (1.0, 1.0, 1.0) if white else srgb_hex(0xA9D8EE)
            program["opacity"].value = 0.95 if white else 0.8
        program["tint"].value = tuple(float(channel) for channel in color)
        program["emissive"].value = float(part.get("emissive", 0.0))
        program["kind"].value = 4 if foam else 1 if water else 2 if banner else 0
        if foam:
            program["foam"].value = tuple(float(component) for component in foam)
            program["foamTime"].value = float(frame.now)
            self.m_animated = True
        flip = part.get("flipbook") if water else None
        if "flip" in names:
            if flip:
                flip_frame = int(frame.now / max(0.01, flip["duration"])) % max(1, flip["frames"])
                program["flip"].value = (flip_frame % flip["x"], flip_frame // flip["x"], flip["x"], flip["y"])
                program["flipTiling"].value = tuple(flip["tiling"])
            else:
                program["flip"].value = (0, 0, 0, 0)
        if "uvOffset" in names:
            flow_per_s = max(0.05, part.get("flow") or 0.25) if water and not flip and texture is not None else 0.0
            program["uvOffset"].value = (0.0, (flow_per_s * frame.now) % 1.0)
        if water:
            self.m_animated = True
        self.m_stats["calls"] += self._render_levels(vaos["model"], batch.m_sets["model"].m_counts)

    @staticmethod
    def _render_levels(levels: list, counts: list) -> int:
        """Each level's meshes with that level's instances; the draws made."""
        calls = 0
        for vaos, count in zip(levels, counts, strict=False):
            if count:
                for vao in vaos:
                    vao.render(instances=count)
                calls += len(vaos)
        return calls

    def draw_overlays(self, view_proj, overlays) -> None:
        """Lines, points and triangles over the finished frame: (mode, vertices [n, 7] pos + rgba)."""
        if not overlays:
            return
        ctx = self.m_ctx
        program = self.m_prog["overlay"]
        program["viewProj"].write(gl(view_proj))
        program["pointSize"].value = 9.0  # handles and check marks
        ctx.enable(ctx.BLEND | ctx.PROGRAM_POINT_SIZE)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        ctx.disable(ctx.DEPTH_TEST)
        for mode, vertices in overlays:
            if not len(vertices):
                continue
            buffer = ctx.buffer(np.ascontiguousarray(vertices, "f4").tobytes())
            vao = ctx.vertex_array(program, [(buffer, "3f 4f", "pos", "rgba")])
            vao.render({"lines": ctx.LINES, "points": ctx.POINTS, "triangles": ctx.TRIANGLES}[mode])
            vao.release()
            buffer.release()
        ctx.disable(ctx.BLEND)

    def _all_batches(self) -> list[CBatch]:
        return [*self.m_batches.values(), *([self.m_ghost] if self.m_ghost is not None else [])]

    def set_ghost(self, cfg: str | None, marker: str | None = None, position=None, rotation=None) -> None:
        """The model about to be placed, drawn under the cursor (not part of the island); None: none."""
        if not cfg and not marker:
            self.m_ghost = None
            return
        key = ("markers", marker) if marker else ("ghost", cfg)
        entry = SEntry(
            "object", "ghost", {"cfg": cfg, "marker": marker}, position=list(position), rotation=list(rotation)
        )
        if self.m_ghost is None or self.m_ghost.m_key != key:
            self.m_ghost = CBatch(key, [entry])
            if cfg and self.m_library is not None:
                self.m_library.request([cfg])
        self.m_ghost.m_entries, self.m_ghost.m_dirty = [entry], True

    def batch_of(self, entry) -> CBatch | None:
        return self.m_batches.get(entry.batch)

    def release(self) -> None:
        if self.m_game is not None:
            self.m_game.release()
        for sea in self.m_seas:
            sea.release()
        self.m_particles.release()
        self.m_decals.release()
        if self.m_library is not None:
            self.m_library.close()
        self.m_loader.close()
        if self.on_change in self.m_session.m_listeners:
            self.m_session.m_listeners.remove(self.on_change)
