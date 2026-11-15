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
from ...render.camera import OrbitCamera, gl, look_at, ortho
from ...render.cull import CellTable, InstanceCells, LodCamera, TerrainChunks, frustum_planes, reach
from ...render.light import light, srgb_hex
from ...render.loader import Loader
from ...render.modellibrary import BANNER_SHADER, WATER_SHADER, ModelLibrary, bounds, instance_matrices
from ...render.particledraw import ParticleLayer
from ...render.quality import Quality, lod_rules, remakes
from ..editchange import Change, union_box
from ..editsession import EditSession, Entry
from .desktopbatches import PASSES, Batch, InstanceSets, level_meshes
from .desktopdecals import DecalLayer
from .desktopframe import Frame, FramePasses
from .desktopgeometry import pin_mesh, terrain_vertices
from .desktopground import GroundDraw
from .desktoppick import Picking

LAYERS = ("objects", "markers", "props", "grass", "forest", "water")
SHADOW_SIZE = 2048  # the view's own sun shadow map at the best quality (``render.quality``: half the game frame's)


@dataclass
class ViewOptions:
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
    native: bool = False  # the island's game's own shaders on Direct3D (``desktopgame.GameIsland``), in game light
    quality: Quality | None = None  # the rendering quality (``render.quality``); None: the best


def shrink(raw: bytes, size: tuple[int, int], comps: int, most: int) -> tuple[tuple[int, int], bytes]:
    """A map's pixels halved (each texel the mean of four) until neither side is above ``most`` (0: as it is)."""
    w, h = size
    if not most or max(w, h) <= most:
        return size, raw
    img = np.frombuffer(raw, np.uint8).reshape(h, w, comps).astype(np.float32)
    while max(w, h) > most and min(w, h) >= 2:
        h, w = h // 2, w // 2
        img = img[: h * 2, : w * 2].reshape(h, 2, w, 2, comps).mean(axis=(1, 3))
    return (w, h), np.ascontiguousarray(np.round(img).astype(np.uint8)).tobytes()


def shadow_side(q: Quality | None) -> int:
    """The view's own shadow map's side at quality ``q``: half the game frame's atlas (a cascade's), at most
    ``SHADOW_SIZE``."""
    return SHADOW_SIZE if q is None else max(512, min(SHADOW_SIZE, q.shadow_size // 2))


class Renderer(GroundDraw, FramePasses, Picking):
    """Draws an ``EditSession``; ``editor`` (an ``IslandEditor``) supplies models, textures and the game data."""

    def __init__(self, ctx, session: EditSession, editor=None) -> None:
        self.ctx, self.session, self.editor = ctx, session, editor
        self.size = float(session.size)
        P = ctx.program
        self.prog = {
            "terrain": P(vertex_shader=S.TERRAIN_VS, fragment_shader=S.TERRAIN_FS),
            "model": P(vertex_shader=S.MODEL_VS, fragment_shader=S.MODEL_FS),
            "banner": P(vertex_shader=S.BANNER_VS, fragment_shader=S.MODEL_FS),
            "depth_model": P(vertex_shader=S.MODEL_VS, fragment_shader=S.DEPTH_FS),
            "depth_banner": P(vertex_shader=S.BANNER_VS, fragment_shader=S.DEPTH_FS),
            "pick_model": P(vertex_shader=S.pick_vs(S.MODEL_VS), fragment_shader=S.PICK_FS),
            "pick_banner": P(vertex_shader=S.pick_vs(S.BANNER_VS), fragment_shader=S.PICK_FS),
            "flat": P(vertex_shader=S.FLAT_VS, fragment_shader=S.FLAT_FS),
            "sky": P(vertex_shader=S.SKY_VS, fragment_shader=S.SKY_FS),
            "water": P(vertex_shader=S.WATER_VS, fragment_shader=S.WATER_PLAIN_FS),
            "final": P(vertex_shader=S.FINAL_VS, fragment_shader=S.FINAL_FS),
            "overlay": P(vertex_shader=S.OVERLAY_VS, fragment_shader=S.OVERLAY_FS),
        }
        self.fullscreen = {k: ctx.vertex_array(self.prog[k], []) for k in ("sky", "final")}
        self.library = ModelLibrary(editor) if editor is not None else None
        self.particles = ParticleLayer(ctx, self.library, self._all_batches, self._gpu_texture)
        self.decals = DecalLayer(ctx)
        self._ground_version = 0  # counts the terrain's changes (the decals are laid again)
        self.loader = Loader()
        self.batches: dict[tuple, Batch] = {}
        self.textures: dict = {}
        self.meshes: dict = {}
        self.stats: dict[str, int] = {}  # the last frame: draw calls, instances and terrain indices drawn / in all
        self._pending = Change(structure=True, heights=(0, 0, session.size, session.size), water=True)
        self._lock = threading.Lock()
        self.messages: list[str] = []
        self.targets = None
        self._shadow = None
        self._pick = None
        self.ghost: Batch | None = None  # the library pick under the cursor (Place)
        self._start = time.monotonic()
        self.animated = False  # flowing water or a flipbook is on screen: the view keeps ticking
        session.listeners.append(self.on_change)
        self._upload_terrain()
        self._dummy = ctx.texture((1, 1), 4, bytes([128, 128, 128, 255]))
        self._dummy_array = ctx.texture_array((1, 1, 1), 1, bytes(1))
        self._dummy_u = ctx.texture((1, 1), 1, bytes([255]), dtype="u1")
        self._dummy_u.filter = (ctx.NEAREST if hasattr(ctx, "NEAREST") else 0x2600,) * 2
        self._dummy_3d = ctx.texture3d((2, 2, 2), 3, np.zeros(24, "f4").tobytes(), dtype="f4")
        self._dummy_shadow = ctx.depth_texture((1, 1), np.ones(1, "f4").tobytes())
        self._dummy_shadow.compare_func = "<="
        self.ground = {"ids": None, "known": None, "asked": None, "size": 0, "alpha2070": None}
        self._weights_tex = None
        self._weights_dirty = None
        self._water_meshes = None
        self._dots = None
        self.game = None  # desktopgame.GameIsland, made when the look first asks for it
        self.seas: list = []  # desktopsea.IslandSea: the old games' seas by their own shaders (2070, 1404, 1701), on QRhi
        self.game_parts: set[tuple] = set()  # (batch key, part index) the game's shaders drew this frame
        self.game_sea = False  # the game's sea drew this frame (the view's own sea is left out)
        self.drawn_opaque: list = []  # this frame's opaque (batch, part, VAOs) in view
        self.game_inland = False  # the game's rivers and lakes drew this frame (the view's own are left out)
        self.game_decals = False  # the game's decal shader drew the config decals (the view's own are left out)
        self.baked_decal = None  # which decals the game's ground bake drew this frame (1800's; the view's leaves them)
        self._members: dict = {}  # program -> its uniform names (``_set_light``)
        self._lit: dict = {}  # program -> whether this frame's light is set with the shadow (``_draw_part``)
        self._table = None  # (key, CellTable, batch index): every batch's cells (``_pass_codes``)
        self._frame_codes: dict = {}  # pass -> this frame's codes of every cell
        self._frame_lod: LodCamera | None = None
        self._max_texture = 0  # the largest side a model's map is uploaded at (0: as it is; ``render.quality``)
        # the models are asked for by the first frame, once its look says which load in full (``_sync_full``)
        if editor is not None:
            self.loader.get("albedo", self._albedo)
            self.loader.get("ground", self._ground_info)

    # changes from the session
    def on_change(self, change: Change) -> None:
        with self._lock:
            p = self._pending
            p.entries |= change.entries
            p.structure |= change.structure
            p.water |= change.water
            p.materials |= change.materials
            for name in ("heights", "weights"):
                box = getattr(change, name)
                if box is not None:
                    setattr(p, name, union_box(getattr(p, name), box))

    def _take_pending(self) -> Change:
        with self._lock:
            p, self._pending = self._pending, Change()
        return p

    def _request_models(self) -> None:
        if self.library is not None:
            self.library.request({k[1] for k in self.session.batches if k[0] != "markers" and k[1]})

    def busy(self) -> int:
        """Things still loading: models, textures, ground data."""
        n = self.loader.pending()
        if self.library is not None:
            n += sum(1 for k in self.session.batches if k[0] != "markers" and k[1] and not self.library.ready(k[1]))
            n += sum(1 for b in self.batches.values() if b.lods_waiting)  # their coarser levels
            n += self.library.textures_pending()
        if self.editor is not None and self.ground["ids"] != tuple(self.session.ground_ids):
            n += 1  # the ground's materials
        return n + self.particles.pending() + (self.game.busy() if self.game is not None else 0)

    def changed(self) -> bool:
        """Something new arrived from the background since the last frame."""
        out = False
        for ev in (self.loader.changed, self.library.changed if self.library else None):
            if ev is not None and ev.is_set():
                ev.clear()
                out = True
        return out

    # GPU uploads
    def _upload_terrain(self) -> None:
        s = self.session
        w = s.width
        self._terrain_heights = s.heights
        self.terrain_vbo = self.ctx.buffer(terrain_vertices(s.heights, s.step).tobytes(), dynamic=True)
        self.terrain_chunks = TerrainChunks(w, s.step)
        self.terrain_chunks.set_heights(s.heights)
        self.terrain_ibo = self.ctx.buffer(self.terrain_chunks.indices.tobytes())
        self.terrain_vao = self.ctx.vertex_array(
            self.prog["terrain"],
            [(self.terrain_vbo, "3f 3f", "pos", "normal")],
            index_buffer=self.terrain_ibo,
            index_element_size=4,
            skip_errors=True,
        )
        self._shown_game_terrain = False

    def _update_terrain(self, box, game: np.ndarray | None) -> None:
        s = self.session
        heights = game if game is not None else s.heights
        if box is None and (game is not None) == self._shown_game_terrain:
            return
        if (game is not None) != self._shown_game_terrain:
            box = (0, 0, s.size, s.size)
            self._shown_game_terrain = game is not None
        j0, j1 = max(0, box[1] * s.spt - 1), min(s.width, box[3] * s.spt + 2)
        verts = terrain_vertices(heights, s.step, slice(j0, j1))
        self.terrain_vbo.write(verts.tobytes(), offset=j0 * s.width * 24)
        self.terrain_chunks.set_heights(heights)

    def _gpu_texture(self, key: str, srgb: bool):
        if key in self.textures:
            return self.textures[key]
        data = self.library.texture(key) if self.library else None
        if data is None:
            return None  # still loading
        if data is False:  # a colour map that is not there or does not decode: the missing-file checker
            missing = key[:2] in ("d:", "fx") and viewcolors.missing_texture(self.ctx, self.textures)
            return self.textures.setdefault(key, missing or None)
        w, h, comps, raw = data
        if raw is None:  # uploaded once, the pixels dropped (``drop_pixels``)
            return self.textures.get(key)
        fmt = (0x8C43 if comps == 4 else 0x8C41) if srgb else None  # SRGB8_ALPHA8, SRGB8
        (w, h), raw = shrink(raw, (w, h), comps, self._max_texture)
        tex = self.ctx.texture((w, h), comps, raw, internal_format=fmt)
        tex.build_mipmaps()
        tex.anisotropy = 8.0
        tex.repeat_x = tex.repeat_y = True
        self.textures[key] = tex
        self.library.drop_pixels(key)  # the particles read only an effect map's size
        return tex

    def _vaos(self, verts: np.ndarray, idx: np.ndarray, banner: bool) -> dict:
        ctx = self.ctx
        vbo, ibo = ctx.buffer(verts.tobytes()), ctx.buffer(idx.tobytes())
        fmt = ("3f 3f 2f 2f", "pos", "normal", "uv", "corner") if banner else ("3f 3f 2f", "pos", "normal", "uv")
        out = {}
        for name in ("banner", "depth_banner", "pick_banner") if banner else ("model", "depth_model", "pick_model"):
            out[name.split("_")[0]] = (vbo, ibo, fmt, self.prog[name])
        return out

    def _bind(self, spec, inst) -> object:
        """A mesh's VAO with the instance buffer ``inst``."""
        vbo, ibo, fmt, prog = spec
        return self.ctx.vertex_array(
            prog, [(vbo, *fmt), (inst, "16f/i", "inst")], index_buffer=ibo, index_element_size=4, skip_errors=True
        )

    def _make_vaos(self, b: Batch) -> None:
        """The batch's instance sets (one buffer per level and pass) and its meshes' VAOs: per pass and level the
        meshes drawn at that level (``level_meshes``), picking's over every instance."""
        b.release_draws()
        b.sets = {name: InstanceSets(self.ctx, b.count, b.levels) for name in PASSES}
        b.vaos = []
        for (p, spec), per in zip(b.parts, level_meshes(b.parts, b.lods, b.levels), strict=True):
            kind = "banner" if p.get("shader") == BANNER_SHADER else "model"
            vaos = {"pick": self._bind(spec["pick"], b.inst)}
            for name in PASSES:
                buffers = b.sets[name].buffers
                vaos[name] = [[self._bind(s[kind if name == "model" else "depth"], buffers[k]) for s in specs]
                              for k, specs in enumerate(per)]  # fmt: skip
            b.vaos.append((p, vaos))

    def _mesh_specs(self, key: tuple, verts: np.ndarray, idx: np.ndarray, banner: bool) -> dict:
        """A mesh's buffers and programs per pass (``_vaos``), made once per mesh."""
        if key not in self.meshes:
            self.meshes[key] = self._vaos(verts, idx, banner=banner)
        return self.meshes[key]

    def _batch_parts(self, batch: Batch) -> list | None:
        layer, name = batch.key
        if layer == "markers":
            if "pin" not in self.meshes:
                self.meshes["pin"] = self._vaos(*pin_mesh(), banner=False)
            c = viewcolors.MARKER_COLORS.get(name, 0xFFFFFF)
            part = {"shader": 8, "color": srgb_hex(c), "diffuse": "", "normal": "", "matrix": np.identity(4, "f4"),
                    "emissive": 0.35, "marker": True}  # fmt: skip
            batch.box = (np.array([-0.55, 0, -0.55], "f4"), np.array([0.55, 2.45, 0.55], "f4"))
            batch.reach = reach(batch.box)
            return [(part, self.meshes["pin"])]
        if self.library is None or not name or not self.library.ready(name):
            return None
        parts = self.library.parts(name)
        meshes, out = {}, []
        batch.lods, batch.levels, batch.lods_waiting = {}, 1, []
        batch.lean = not self.library.full(name)  # the game's look draws it: its parts' boxes alone (picking)
        for p in parts:
            prims = (self.library.box(p["mesh_key"]) if batch.lean else self.library.mesh(p["mesh_key"])) or []
            meshes[p["mesh_key"]] = prims
            kind = "box" if batch.lean else "mesh"
            for k, (verts, idx) in enumerate(prims):
                out.append((p, self._mesh_specs((p["mesh_key"], kind, k), verts, idx, p["shader"] == BANNER_SHADER)))
            if batch.lean:
                continue
            levels = self.library.lods(p)
            if levels is None:
                batch.lods_waiting.append(p)
            if levels and p["shader"] != BANNER_SHADER:
                batch.lods[id(p)] = [[self._mesh_specs((p["mesh_key"], "lod", level, k), v, i, False)
                                      for k, (v, i) in enumerate(prims)]
                                     for level, prims in enumerate(levels, 1)]  # fmt: skip
                batch.levels = max(batch.levels, len(levels) + 1)
        batch.box = bounds(parts, meshes)
        batch.reach = reach(batch.box)
        if batch.box is not None:
            batch.radius = max(1.0, float(np.linalg.norm(batch.box[1] - batch.box[0])) / 2)
        return out

    def _sync_batches(self, change: Change) -> None:
        s = self.session
        if change.structure:
            self._request_models()
            for key in [k for k in self.batches if k not in s.batches]:
                self.batches.pop(key).release_draws()
            for key, rows in s.batches.items():
                b = self.batches.get(key)
                if b is None:
                    self.batches[key] = Batch(key, rows)
                else:
                    b.entries, b.dirty = rows, True
            self._dots = None
        for e in change.entries:
            b = self.batches.get(e.batch)
            if b is not None:
                b.dirty = True
                if b.parts is None:
                    self._dots = None
        for b in self._all_batches():
            if b.dirty or b.inst is None:
                self._write_instances(b)
            if b.parts is None or any(self.library.lods(p) is not None for p in b.lods_waiting):
                parts = self._batch_parts(b)  # the model arrived, or coarser levels of its parts did
                if parts is not None:
                    self._dots = None if b.parts is None else self._dots
                    b.parts = parts
                    self._make_vaos(b)

    def _write_instances(self, b: Batch) -> None:
        rows = b.entries
        n = len(rows)
        pos = np.array([e.position for e in rows], "f4").reshape(n, 3)
        quat = np.array([e.rotation for e in rows], "f4").reshape(n, 4)
        scale = np.array([e.scale if isinstance(e.scale, list) else [e.scale] * 3 for e in rows], "f4").reshape(n, 3)
        scale[[not e.shown for e in rows]] = 0
        mats = instance_matrices(pos, quat, scale, self.size) if n else np.zeros((0, 4, 4), "f4")
        b.mats, b.version = mats, b.version + 1
        data = np.ascontiguousarray(mats.transpose(0, 2, 1)).tobytes()
        b.cells = InstanceCells(mats) if n else None
        if b.inst is None or b.count != n:
            if b.inst is not None:
                b.inst.release()
            b.inst = self.ctx.buffer(data or bytes(64), dynamic=True)
            b.count = n
            if b.parts is not None:
                self._make_vaos(b)
        elif data:
            b.inst.write(data)
        for sets in b.sets.values():
            sets.key = None  # the instances moved: written again
        b.dirty = False

    def _cull(self, b: Batch, planes, lod: LodCamera | None = None) -> int:
        """Put the batch's instances in view into its main pass's buffers by level of detail (``planes``: None draws
        all at the finest; ``lod`` None: every instance in view at the finest, none left out as too small); how
        many."""
        n = self._sort(b, "model", planes, lod if b.key[0] != "markers" else None)
        self.stats["instances"] += n
        self.stats["instances_of"] += b.count
        return n

    def _sort(self, b: Batch, name: str, planes, lod: LodCamera | None) -> int:
        """The batch's instances into pass ``name``'s buffers by level: by 117's rule for ``lod``, those in view at
        the finest without; how many."""
        if b.cells is None or name not in b.sets:
            return 0
        cells = b.cells
        if planes is None:
            codes = np.zeros(len(cells.counts), np.int64)
        else:
            table, index, every = self._pass_codes(name, planes, lod or self._frame_lod)
            k = index.get(id(b))
            codes = table.of(every, k) if k is not None else np.where(cells.visible(planes, b.reach), 0, -2)
            if lod is None:  # a marker: whatever its size on screen
                codes = np.where(codes > -2, 0, -2)
        return b.sets[name].update(cells, codes)

    def _pass_codes(self, name: str, planes, lod: LodCamera | None) -> tuple:
        """(table, batch index, every cell's code) of pass ``name`` this frame (``CellTable.codes``), worked out once
        a frame over the cells of every batch; the table is made again when a batch's cells changed."""
        got = self._frame_codes.get(name)
        if got is None:
            batches = [b for b in self._all_batches() if b.cells is not None and b.sets]
            # the objects themselves, not their ids: a freed batch's or cells' id is soon another's
            key = tuple((b, b.cells, b.reach, b.levels) for b in batches)
            if self._table is None or self._table[0] != key:
                table = CellTable([(b.cells, b.reach, b.levels) for b in batches])
                self._table = (key, table, {id(b): k for k, b in enumerate(batches)})
            got = self._frame_codes[name] = (*self._table[1:], self._table[1].codes(planes, lod))
        return got

    def _draw_terrain(self, planes) -> None:
        """The terrain chunks in view, neighbours as one draw (``planes`` None: all in one)."""
        total = len(self.terrain_chunks.indices)
        runs = self.terrain_chunks.runs(planes) if planes is not None else [(0, total)]
        if sum(n for _, n in runs) == total:
            runs = [(0, total)]
        for first, count in runs:
            self.terrain_vao.render(vertices=count, first=first)
        self.stats["terrain"], self.stats["terrain_of"] = sum(n for _, n in runs), total
        self.stats["calls"] += len(runs)

    def _dots_vao(self):
        if self._dots is None:
            rows = []
            for b in self.batches.values():
                if b.parts is None or not b.parts:
                    for e in b.entries:
                        if e.shown:
                            x, y, z = e.position
                            rows.append((self.size - x, y + 0.3, z, 0.95, 0.95, 0.95))
            pts = np.asarray(rows, "f4").reshape(-1, 6)
            self._dots = (
                self.ctx.vertex_array(self.prog["flat"], [(self.ctx.buffer(pts.tobytes()), "3f 3f", "pos", "rgb")])
                if len(pts)
                else False
            )
        return self._dots

    def _message(self, text: str) -> None:
        if text not in self.messages:
            self.messages.append(text)

    def _names(self, prog):
        """A program's uniform names, for ``in`` (moderngl's own walks every member: 40 % of a frame of many
        parts)."""
        names = self._members.get(prog)
        if names is None:
            names = self._members[prog] = set(prog) if hasattr(prog, "__iter__") else prog
        return names

    def _set_light(self, prog, lt: dict, eye, shadow, env: float = 1.0) -> None:
        names = self._names(prog)

        def put(name, value):
            if name in names:
                prog[name].value = value

        put("sunDir", tuple(float(v) for v in lt["sunDir"]))
        put("sunColor", lt["sunColor"])
        put("ambientMode", lt["ambientMode"])
        put("hemiSky", lt.get("hemiSky", (0, 0, 0)))
        put("hemiGround", lt.get("hemiGround", (0, 0, 0)))
        put("envLight", lt.get("envLight", 0.0) * env)
        put("flatAmbient", lt.get("flatAmbient", (0, 0, 0)))
        put("shFactor", lt.get("shFactor", 0.0))
        if "sh" in lt and "sh" in names:
            prog["sh"].write(np.asarray(lt["sh"], "f4").tobytes())
        near, far = lt.get("fog", (self.size * 1.6, self.size * 4))
        put("fogColor", lt["fogColor"])
        put("fogNear", near)
        put("fogFar", far)
        put("eye", tuple(float(v) for v in eye))
        put("uwLight", lt.get("uwLight", (0, 0, 0)))  # all zero: not the underwater look
        put("uwFx", (lt.get("uwCaustics", 0.0), lt["uwTime"]))
        put("shadowOn", shadow is not None)
        if "shadowMatrix" in names:
            prog["shadowMatrix"].write(gl(shadow[1] if shadow is not None else np.identity(4)))
        if "shadowMap" in names:
            (shadow[0] if shadow is not None else self._dummy_shadow).use(7)
            prog["shadowMap"].value = 7

    # frames
    def _targets(self, w: int, h: int, single: bool = False):
        """The frame's targets, multisampled unless ``single`` (the game's 2070 sea reads the frame's depth)."""
        samples = 1 if single else min(4, self.ctx.max_samples)
        if self.targets is None or self.targets["size"] != (w, h) or self.targets["samples"] != samples:
            if self.targets is not None:
                for k in ("color", "depth", "msaa", "resolved", "resolve"):
                    self.targets[k].release()
            ctx = self.ctx
            color = ctx.renderbuffer((w, h), 4, samples=samples, dtype="f2")
            depth = ctx.depth_renderbuffer((w, h), samples=samples)
            resolved = ctx.texture((w, h), 4, dtype="f2")
            resolved.filter = (0x2601, 0x2601)
            self.targets = {
                "size": (w, h),
                "samples": samples,
                "color": color,
                "depth": depth,
                "msaa": ctx.framebuffer([color], depth),
                "resolved": resolved,
                "resolve": ctx.framebuffer([resolved]),
            }
        return self.targets

    def _shadow_pass(self, view_target, distance: float, lt: dict, opts: ViewOptions, lod: LodCamera | None):
        ctx = self.ctx
        side = shadow_side(opts.quality)
        if self._shadow is not None and self._shadow[0].size[0] != side:  # the quality changed
            for part in self._shadow:
                part.release()
            self._shadow = None
        if self._shadow is None:
            tex = ctx.depth_texture((side, side))
            tex.compare_func = "<="
            tex.repeat_x = tex.repeat_y = False
            self._shadow = (tex, ctx.framebuffer(depth_attachment=tex))
        tex, fbo = self._shadow
        reach = min(400.0, max(40.0, distance * 0.9))
        sun = np.asarray(lt["sunDir"], "f4")
        target = np.asarray(view_target, "f4")
        lview = look_at(target + sun * reach * 2, target)
        matrix = ortho(reach, 1.0, reach * 4) @ lview
        fbo.use()
        fbo.clear(depth=1.0)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.BLEND)
        planes, texel = frustum_planes(matrix), 2 * reach / side
        progs = {kind: self.prog["depth_" + kind] for kind in ("model", "banner")}
        for prog in progs.values():  # the sun's camera, once a frame
            prog["viewProj"].write(gl(matrix))
            if "view" in self._names(prog):
                prog["view"].write(gl(lview))
        for b in self.batches.values():
            if not self._layer_on(b, opts) or b.key[0] in ("grass", "markers") or not b.parts or not b.count or b.lean:
                continue
            if b.radius * b.cells.scale < texel or not self._sort(b, "depth", planes, lod):
                continue  # smaller than a shadow texel, or outside the sun's box
            counts = b.sets["depth"].counts
            for p, vaos in b.vaos:
                if p["shader"] == WATER_SHADER:
                    continue
                prog = progs["banner" if p["shader"] == BANNER_SHADER else "model"]
                prog["part"].write(gl(p["matrix"]))
                tex2 = self._gpu_texture("d:" + p["diffuse"], True) if p["diffuse"] else None
                if "textured" in self._names(prog):
                    prog["textured"].value = tex2 is not None
                if tex2 is not None:
                    tex2.use(0)
                    prog["diffuseTex"].value = 0
                self.stats["shadow_calls"] += self._render_levels(vaos["depth"], counts)
        return tex, matrix

    def _layer_on(self, b: Batch, opts: ViewOptions) -> bool:
        return opts.layers.get(b.key[0], True)

    def render(
        self, fbo, size: tuple[int, int], camera: OrbitCamera, opts: ViewOptions, scale: float = 1.0, overlays=()
    ) -> None:
        """Draw a frame into ``fbo`` (``size`` pixels) as ``camera`` sees it; the scene at ``scale`` of that (the
        GPU's load). The water surfaces are the saved island's: the water tool draws the edited network itself."""
        ctx = self.ctx
        if opts.native:  # the game's shaders light by the game's light
            opts = replace(opts, game_light=True, look2070=False, underwater=False, caustics=False)
        game = self._game_drawable(opts)  # the game's frame casts its own shadows: the view's pass would be waste
        self._sync_full(opts)
        game_heights = self._sync_scene(opts)
        lt = light(opts, self.size)
        lt["uwTime"] = opts.particle_time if opts.particle_time is not None else time.monotonic() - self._start
        self.stats = dict.fromkeys(("calls", "instances", "instances_of", "terrain", "terrain_of", "shadow_calls"), 0)
        self._lit, self._frame_codes = {}, {}
        q = opts.quality
        scale *= q.render_scale if q is not None else 1.0
        self._max_texture = q.max_texture if q is not None else 0  # maps uploaded from now on
        w, h = max(1, int(size[0] * scale)), max(1, int(size[1] * scale))
        game_sea = self._game_sea(opts)
        t = self._targets(w, h, single=game_sea is not None)
        view, eye = camera.view(), camera.eye()
        proj = camera.proj(size[0] / max(1, size[1]))
        view_proj = proj @ view
        planes = frustum_planes(view_proj) if opts.cull else None
        rules = lod_rules(q)
        lod = self._frame_lod = LodCamera(np.asarray(eye, float), view, proj, (w, h), rules) if opts.cull else None
        cast = opts.shadows and (q is None or q.shadows) and not game
        shadow = self._shadow_pass(camera.target, camera.distance, lt, opts, lod) if cast else None
        t["msaa"].use()
        ctx.viewport = (0, 0, w, h)
        t["msaa"].clear(0, 0, 0, 1, depth=1.0)
        self._draw_sky(view_proj, lt, eye)
        ctx.enable(ctx.DEPTH_TEST)
        ctx.disable(ctx.CULL_FACE)
        frame = Frame(view, view_proj, eye, planes, camera.distance, lt, shadow, scale, game_heights)
        frame.lod = lod
        self.game_parts, self.game_sea, self.game_inland, self.game_decals = set(), False, False, False
        self.baked_decal = None
        drew = game and self._draw_game(frame, opts, t["msaa"], (w, h), camera)
        if not drew:
            self._draw_ground(frame, opts)
        # the view keeps drawing while the game's look makes parts ahead of their first draw
        # and while its water and clouds move with the clock or it settles (bake, pages, cascades: ``GameIsland``)
        game_moves = drew and (self.game.objects.ahead_left or self.game.animated)
        self.animated = bool(opts.underwater and opts.caustics) or bool(game_moves)
        frame.now = time.monotonic() - self._start
        streets, models, translucent = self._visible_parts(frame, opts)
        self.drawn_opaque = streets + models  # the game's sea mirrors them (``desktopsea``)
        self._lay_decals(frame, opts)
        if not self.game_decals:  # the game's decal shader drew them into its G-buffer
            self.decals.draw(self, frame, decalmesh.BEFORE_STREETS)  # the game's ground stage (render.decalmesh)
        for b, p, vaos in streets:
            self._draw_part(b, p, vaos, frame)
        if not self.game_decals:
            self.decals.draw(self, frame, decalmesh.AFTER_STREETS)
        for b, p, vaos in models:
            self._draw_part(b, p, vaos, frame)
        if (
            not self.game_decals
        ):  # the game lays vehicle tracks only behind moving vehicles (island-117-view "Round 12")
            self.decals.draw(self, frame, decalmesh.VEHICLE)
        self._draw_dots(frame)
        if game_sea is not None:
            self.game_sea = game_sea.draw(frame, opts, t, camera, (w, h))
        self._draw_sea(frame, opts)
        ctx.enable(ctx.BLEND)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        t["msaa"].depth_mask = False
        for b, p, vaos in translucent:
            self._draw_part(b, p, vaos, frame)
        self.animated |= self.particles.draw(opts, camera, view_proj, planes, frame.now)
        t["msaa"].depth_mask = True
        ctx.disable(ctx.BLEND)
        self._finish(t, fbo, size, lt, drew)
        self.draw_overlays(view_proj, overlays)

    def _game_drawable(self, opts: ViewOptions) -> bool:
        """Whether the look asks for the game's own shaders and they can draw now (``desktopgame``; why not is said
        once)."""
        if not opts.native:
            return False
        fallback = set()
        if self.game is not None and self.game.terrain is not None and remakes(self.game.quality, opts.quality):
            self.game.release()  # built for another quality: its shadow map, maps and terrain programs anew
            fallback, self.game = self.game.objects.fallback, None  # the view's look keeps drawing what it drew
        if self.game is None:
            from .desktopgame import GameIsland  # Qt's QRhi: only for this look

            self.game = GameIsland(self)
            self.game.objects.fallback = set(fallback)
        why = self.game.why_not()
        if why:
            self._message(why)
            return False
        return self.game.compiled()

    def _game_sea(self, opts: ViewOptions):
        """The island's sea by its game's own shaders where it draws (2070's ``desktopsea2070``, 1404's
        ``desktopsea1404``, 1701's ``desktopsea1701``), else None."""
        if not hasattr(self.ctx, "native_pass"):
            return None
        if not self.seas:
            from .desktopsea1404 import GameSea1404  # Qt's QRhi: only on its device
            from .desktopsea1701 import GameSea1701
            from .desktopsea2070 import GameSea2070

            self.seas = [GameSea2070(self), GameSea1404(self), GameSea1701(self)]
        return next((sea for sea in self.seas if sea.wanted(opts)), None)

    def _draw_game(self, f: Frame, opts: ViewOptions, target, size: tuple[int, int], camera) -> bool:
        """The terrain by the game's own shaders (``_game_drawable`` said they can); False: the view draws its own
        ground."""
        return self.game.draw(f, opts, target, size, camera)

    def _draw_part(self, b: Batch, p: dict, vaos: dict, f: Frame) -> None:
        banner = p["shader"] == BANNER_SHADER
        water = p["shader"] == WATER_SHADER
        prog = self.prog["banner" if banner else "model"]
        names = self._names(prog)
        if self._lit.get(prog) != water:  # the frame's camera and light, once per program and kind of part
            prog["viewProj"].write(gl(f.view_proj))
            if "view" in names:
                prog["view"].write(gl(f.view))
            self._set_light(prog, f.lt, f.eye, None if water else f.shadow)
            self._lit[prog] = water
        prog["part"].write(gl(p["matrix"]))
        tex = self._gpu_texture("d:" + p["diffuse"], True) if p.get("diffuse") else None
        nrm = self._gpu_texture("n:" + p["normal"], False) if p.get("normal") and not banner else None
        prog["textured"].value = tex is not None
        (tex or self._dummy).use(0)
        prog["diffuseTex"].value = 0
        if "hasNormal" in names:
            prog["hasNormal"].value = nrm is not None
            (nrm or self._dummy).use(2)
            prog["normalTex"].value = 2
        color = p["color"] if tex is not None or p.get("marker") or banner or water else (0.6, 0.6, 0.6)
        if water:
            white = any(w in (p.get("diffuse") or "").lower() for w in ("white", "foam", "splash", "spray", "mist"))
            color = (1.0, 1.0, 1.0) if white else srgb_hex(0xA9D8EE)
            prog["opacity"].value = 0.95 if white else 0.8
        prog["tint"].value = tuple(float(c) for c in color)
        prog["emissive"].value = float(p.get("emissive", 0.0))
        prog["kind"].value = 1 if water else 2 if banner else 0
        flip = p.get("flipbook") if water else None
        if "flip" in names:
            if flip:
                frame = int(f.now / max(0.01, flip["duration"])) % max(1, flip["frames"])
                prog["flip"].value = (frame % flip["x"], frame // flip["x"], flip["x"], flip["y"])
                prog["flipTiling"].value = tuple(flip["tiling"])
            else:
                prog["flip"].value = (0, 0, 0, 0)
        if "uvOffset" in names:
            speed = max(0.05, p.get("flow") or 0.25) if water and not flip and tex is not None else 0.0
            prog["uvOffset"].value = (0.0, (speed * f.now) % 1.0)
        if water:
            self.animated = True
        self.stats["calls"] += self._render_levels(vaos["model"], b.sets["model"].counts)

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
        ctx = self.ctx
        prog = self.prog["overlay"]
        prog["viewProj"].write(gl(view_proj))
        prog["pointSize"].value = 9.0  # handles and check marks
        ctx.enable(ctx.BLEND | ctx.PROGRAM_POINT_SIZE)
        ctx.blend_func = ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA
        ctx.disable(ctx.DEPTH_TEST)
        for mode, verts in overlays:
            if not len(verts):
                continue
            buf = ctx.buffer(np.ascontiguousarray(verts, "f4").tobytes())
            vao = ctx.vertex_array(prog, [(buf, "3f 4f", "pos", "rgba")])
            vao.render({"lines": ctx.LINES, "points": ctx.POINTS, "triangles": ctx.TRIANGLES}[mode])
            vao.release()
            buf.release()
        ctx.disable(ctx.BLEND)

    def _all_batches(self) -> list[Batch]:
        return [*self.batches.values(), *([self.ghost] if self.ghost is not None else [])]

    def set_ghost(self, cfg: str | None, marker: str | None = None, position=None, rotation=None) -> None:
        """The model about to be placed, drawn under the cursor (not part of the island); None: none."""
        if not cfg and not marker:
            self.ghost = None
            return
        key = ("markers", marker) if marker else ("ghost", cfg)
        e = Entry("object", "ghost", {"cfg": cfg, "marker": marker}, position=list(position), rotation=list(rotation))
        if self.ghost is None or self.ghost.key != key:
            self.ghost = Batch(key, [e])
            if cfg and self.library is not None:
                self.library.request([cfg])
        self.ghost.entries, self.ghost.dirty = [e], True

    def batch_of(self, entry) -> Batch | None:
        return self.batches.get(entry.batch)

    def release(self) -> None:
        if self.game is not None:
            self.game.release()
        for sea in self.seas:
            sea.release()
        self.particles.release()
        self.decals.release()
        if self.library is not None:
            self.library.close()
        self.loader.close()
        if self.on_change in self.session.listeners:
            self.session.listeners.remove(self.on_change)
