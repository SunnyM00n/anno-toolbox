"""The island's objects in an Anno 1800 island's look "The game's own shaders": 117's object set
(``desktopgameobjects.GameObjects``: the shown batches' instances in one table, culled per ground cell, the parts
within reach compiled in the background, let go when far, a part that fails the view's own) with each part drawn by
1800's model shaders many instances a draw (``render.objects1800``), its level of detail by 1800's ``LODDistances``.

``Objects1800(island)`` is the ``objects`` of ``desktopgame1800.GameIsland1800``: ``frame(f, opts, shadows, size)``
gives the frame's ``Drawn`` (``gbuffer``: the opaque runs, ``transparent``: the blended ones, each with its caster);
docs/rendering/island-1800-2205-view.md section 12.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from ...infra.i18n import tr
from ...models.shaderforward import PREZ_NORMALS, NoPass, block_state
from ...render import cull, gamedraw, objects1800, terrain1800
from ...render.fxcdraw import Dummies
from ...render.quality import SETTINGS_FILE
from ...render.shadowforward import CASTER_BIAS, CASTER_SLOPE
from .desktopgameobjects import Drawn, GameObjects, SharedMaps, _level_meshes, _Part, part_worlds

JOB = "game-object-{}:{}"  # the game, a part's number
RULES_JOB = "game-object-{}-lod-rules"
IMAGES = (".psd", ".png", ".tga", ".dds")
CUBES = ("cModelEnvTex",)  # cube maps: the frame's stand-in (the viewer reads them as cubes)


@dataclass
class Compiled:
    """A part's material compiled by 1800's model effect (``compile_part``): its colour pass and states, its caster
    (None: it casts nothing), its meshes per level of detail with the matrices each takes, its maps (the shader's name
    -> (path, ``DdsFile``)), the material's fields and the part's bounding sphere."""

    colour: tuple
    state: object
    shadow: tuple | None
    levels: list
    maps: dict
    fields: dict
    sphere: tuple
    prepass: tuple | None = None  # its normals-and-depth pass (stages, states) for the occlusion; None: none
    prop: bool = False  # drawn by a prop shader (``objects1800.PropRun``), not a model shader


def part_levels(files, part: dict) -> list:
    """A part's meshes per level of detail, cut to its material (117's rule, ``desktopgameobjects.compile_part``)."""
    from ...models import configtree

    levels: list = []
    for path in configtree.lod_files(part["mesh"], files.exists):
        numbers = cull.level_numbers(part.get("lod_materials") or [], part.get("material"), len(levels))
        found = _level_meshes(files, path, numbers, strict=not levels)
        levels.append(found or levels[-1])
    return levels


def part_maps(files, values: dict, game: str = "anno1800") -> dict:
    """The maps a material's fields name (a shader's texture name -> (the file the game loads, its ``DdsFile``)); a
    map that does not read is left to the frame's stand-in."""
    from ...models import ddsdecode, fileresolve

    out = {}
    for name, raw in values.items():
        written = str(raw).replace(chr(92), "/")
        if name in CUBES or not written.lower().endswith(IMAGES):
            continue
        path = fileresolve.texture_file(written, files.exists, game)
        try:
            out[name] = (path, ddsdecode.DdsFile(files.read(path))) if path else None
        except Exception:  # a map that does not read: white, as the engine's missing map
            out[name] = None
    return {k: v for k, v in out.items() if v is not None}


def compile_part(source: objects1800.ObjectSource, files, part: dict) -> Compiled:
    """In the background: a part's colour pass (``ModelDefault``), caster (``ModelObj2Shadow``) and an opaque one's
    pre-pass (``ModelNormalsDepth``) by its material's effect, the states the view keeps (as the viewer's: no depth
    written by a blended one, ``LESS_EQUAL`` for the pre-pass's ``EQUAL``), its levels, maps and sphere; ``NoPass``
    for a part 1800's model shaders do not draw."""
    values = part.get("values") or {}
    if not values:
        raise NoPass("not a model of the game's shaders")
    if "Type" in values and "ShaderID" not in values:  # a .prp: the game's prop shaders
        return compile_prop(source, files, part)
    technique = source.colour_technique(values)
    colour, state = source.model_pass(values, technique), _forward_state(source.model_state(values, technique))
    shadow = source.model_pass(values, source.CASTER) if source.has_technique(values, source.CASTER) else None
    levels, sphere = _geometry(files, part, int(float(values.get("NumBonesPerVertex", 0) or 0)))
    try:
        prepass = source.prepass(values) if not state.blend else None
    except (NoPass, RuntimeError):  # drawn all the same, left out of the occlusion
        prepass = None
    return Compiled(colour, state, shadow, levels, part_maps(files, values, source.game), values, sphere, prepass)


def compile_prop(source: objects1800.ObjectSource, files, part: dict) -> Compiled:
    """In the background: a prop's colour pass, caster and an opaque one's normals-and-depth pass (the occlusion's)
    by its type's prop shader (``objects1800.PROPS``) with the .prp's switches, the states it draws with without the
    depth pre-pass, its levels, maps and sphere (island-1800-2205-view.md 15, 16)."""
    values = part["values"]
    fx, defines, (_stem, technique, slot, caster, normals) = source.prop_material(values)
    colour = source.effect_pass(fx, defines, technique)
    state = _forward_state(source.technique_state(fx, defines, technique, slot))
    if state.blend and state.stencil:  # 2205's decal props test the sea's stencil mark, which the view writes not
        state = replace(state, stencil=False)
    try:
        shadow = source.effect_pass(fx, defines, caster)
    except NoPass:  # a prop that casts nothing
        shadow = None
    prepass = None
    if not state.blend:
        try:
            prez = (*defines, PREZ_NORMALS)
            prepass = source.effect_pass(fx, prez, normals), source.technique_state(fx, prez, normals)
        except (NoPass, RuntimeError):  # drawn all the same, left out of the occlusion
            prepass = None
    levels, sphere = _geometry(files, part, 0)
    return Compiled(colour, state, shadow, levels, part_maps(files, values, source.game), values, sphere, prepass, True)


def _forward_state(state):
    """A technique's states as the view's forward frame draws them (the viewer's: no depth written by a blended pass,
    ``LESS_EQUAL`` for the pre-pass's ``EQUAL``)."""
    if state.blend:
        state = replace(state, depth_write=False)
    if state.depth_func == "equal":
        state = replace(state, depth_func="less_equal", depth_write=True)
    return state


def _geometry(files, part: dict, per_vertex: int) -> tuple[list, tuple]:
    """A part's meshes per level with the matrices each takes, and its bounding sphere; ``NoPass`` without any."""
    levels = part_levels(files, part)
    if not levels or not levels[0]:
        raise NoPass("no geometry for the material")
    levels = [[(m, m.bone_count(per_vertex) if per_vertex else 1) for m in lv] for lv in levels]
    boxes = [m.box() for m, _bones in levels[0]]
    lo, hi = np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)
    return levels, ((lo + hi) / 2, float(np.linalg.norm(hi - lo) / 2))


CASTER_STATE = replace(block_state({}), depth_bias=CASTER_BIAS, slope_bias=CASTER_SLOPE)  # the viewer's casters'


@dataclass
class Device:
    """What every part's draws share on the device: the maps, the stand-ins, the frame's buffers of the colour pass
    and of the casters' (``objects1800.SharedBuffers``), the frame's instance data (``objects1800.FrameRows``)."""

    pool: objects1800.Maps2D
    dummies: Dummies
    colour: objects1800.SharedBuffers
    cast: objects1800.SharedBuffers
    rows: objects1800.FrameRows

    def release(self, ctx) -> None:
        """The maps, stand-ins and rows (the shared buffers are the look's, ``GameIsland1800.shared``)."""
        self.pool.release()
        self.dummies.release(ctx)
        self.rows.release()


class Part1800(_Part):
    """A compiled part on the device: per mesh its colour draws and its casters' (``objects1800.Draws``, kept from
    frame to frame), its maps from the pool; a frame's draws are ``objects1800.Run``s over its instances (``RUN``,
    ``PROP_RUN``: the game's, ``chunk`` and ``PROP_CHUNK`` the instances a draw holds)."""

    RUN, PROP_RUN, PROP_CHUNK = objects1800.Run, objects1800.PropRun, objects1800.PROP_CHUNK

    @staticmethod
    def chunk(bones: int) -> int:
        return objects1800.chunk_size(bones)

    def __init__(self, ctx, got: Compiled, device: Device) -> None:
        self.ctx, self.got, self.device, self.pool = ctx, got, device, device.pool
        self.transparent, self.fields = bool(got.state.blend), got.fields
        self.passes, self.made = {}, set()  # nothing made ahead (117's ``_Part.ahead``)
        self.colour = gamedraw.Program(*got.colour)
        self.caster = gamedraw.Program(*got.shadow) if got.shadow is not None else None
        self.prez = (gamedraw.Program(*got.prepass[0]), got.prepass[1]) if got.prepass is not None else None
        self.paths = {name: path for name, (path, _dds) in got.maps.items()}
        for path, dds in got.maps.values():
            self.pool.want(path, dds)
        got.maps = {}  # the pool holds their pixels until their textures are made
        self.draws: dict[int, tuple] = {}  # id of a mesh -> its colour ``Draws``, its casters' and pre-pass's or None
        self._textures: dict | None = None
        self.begin()

    def begin(self) -> None:
        self.runs: list[objects1800.Run] = []
        for draws in self.draws.values():
            for d in draws:
                if d is not None:
                    d.begin()

    def textures(self, batch) -> dict:
        """The maps by the shader's names, made in ``batch`` on first use (``{}`` until a batch comes)."""
        if self._textures is None and batch is not None:
            self._textures = {name: self.pool.take(batch, path) for name, path in self.paths.items()}
        return self._textures or {}

    def _draws(self, mesh: gamedraw.Mesh) -> tuple:
        got = self.draws.get(id(mesh))
        if got is None:
            d, ctx = self.device, self.ctx
            seen = objects1800.Binding(d.colour, rows=d.rows, mirrored=True, fallbacks=dict(terrain1800.FALLBACKS))
            colour = objects1800.Draws(ctx, self.colour, mesh, self.got.state, d.dummies, seen)
            cast = prez = None
            # 1800's cascades look along the light with x right and y up: the view's mirror and theirs cancel
            if self.caster is not None:
                cast = objects1800.Draws(ctx, self.caster, mesh, CASTER_STATE, d.dummies,
                                         replace(seen, shared=d.cast, mirrored=False))  # fmt: skip
            if self.prez is not None:  # the frame hands its draws their own shared buffers (the pre-pass's)
                prez = objects1800.Draws(ctx, self.prez[0], mesh, self.prez[1], d.dummies, replace(seen, fallbacks={}))
            got = self.draws[id(mesh)] = (colour, cast, prez)
        return got

    def models(self, level: int, worlds: np.ndarray, shadows: bool, spheres: tuple,
               colours: np.ndarray | None = None) -> tuple[list, list]:  # fmt: skip
        """This frame's runs of ``worlds`` at ``level`` (with their casters when ``shadows``), at most
        ``objects1800.chunk_size`` instances each, a prop run with its props' ``colours`` (their words); (runs,
        [])."""
        out = []
        centres, radii = spheres
        kind = self.PROP_RUN if self.got.prop else self.RUN
        for mesh, bones in self.got.levels[min(level, len(self.got.levels) - 1)]:
            colour, cast, prez = self._draws(mesh)
            n = self.PROP_CHUNK if self.got.prop else self.chunk(bones)
            for a in range(0, len(worlds), n):
                caster = cast.take() if shadows and cast is not None else None
                run = kind(colour.take(), caster, self, worlds[a : a + n], (centres[a : a + n], radii[a : a + n]),
                           bones)  # fmt: skip
                run.with_prepass(prez.take() if prez is not None else None)
                if colours is not None and self.got.prop:
                    run.colours = colours[a : a + n]
                out.append(run)
        self.runs += out
        return out, []

    def ahead(self, shadows: bool) -> tuple[list, list, list]:
        return [], [], []

    def all(self) -> list:
        return list(self.runs)

    def release(self) -> None:
        for draws in self.draws.values():
            for d in draws:
                if d is not None:
                    d.release()
        self.draws = {}
        self.pool.give_back(self.paths.values())
        self._textures, self.paths = None, {}
        self.begin()


class Objects1800(GameObjects):
    """1800's objects by its own model shaders (module docstring); ``GAME`` and ``PART`` the game's (2205's:
    ``desktopobjects2205``)."""

    GAME, PART = "anno1800", Part1800

    def __init__(self, island) -> None:
        super().__init__(island)
        self.tables: list = []  # the quality levels' ``LODDistances`` (``objects1800.lod_tables``), once read
        self.device: Device | None = None
        self._colour_table, self._colour_words = None, np.zeros(0, np.uint32)  # ``_colours``' last

    def _part(self, part: dict, source):
        """The device side of a view part: its ``Part1800``, an exception (the view draws it) or None while it
        compiles."""
        key = self._key(part)
        found = self.parts.get(key)
        if found is not None:
            return found
        job = self.jobs.setdefault(key, JOB.format(self.GAME, len(self.jobs)))
        self.files = self.files or SharedMaps(self.r.editor.files)
        files = self.files
        got = self.r.loader.get(job, lambda: compile_part(source, files, part))
        if got is None:
            return None
        if isinstance(got, Exception):
            self.parts[key] = got
            if not isinstance(got, NoPass):
                self.r._message(tr("islands.mesh_games_shader_reason", mesh=part["mesh"],
                                   reason=f"{type(got).__name__}: {got}"[:300]))  # fmt: skip
            return got
        if self.device is None:
            q, rhi = getattr(self.island, "quality", None), self.r.ctx.rhi
            pool = objects1800.Maps2D(rhi, q.max_texture if q is not None else 0)
            self.device = Device(pool, Dummies(rhi), *self.island.shared, objects1800.FrameRows(self.r.ctx))
        self.parts[key] = self.PART(self.r.ctx, got, self.device)
        return self.parts[key]

    def _rules(self) -> cull.LodRules:
        """1800's smallest size drawn (its engine settings; it has no ``LODBias``: 1) and its ``LODDistances``, read
        once in the background."""
        if self.rules is None:
            files = getattr(self.r.editor.files, "game", self.r.editor.files)

            def read() -> tuple:
                text = files.read(SETTINGS_FILE[self.GAME]).decode("utf-8", errors="replace")
                return replace(cull.read_lod_rules(text), bias=1.0), objects1800.lod_tables(text)

            got = self.r.loader.get(RULES_JOB.format(self.GAME), read)
            if got is None:
                return cull.LodRules(bias=1.0)
            self.rules, self.tables = got if isinstance(got, tuple) else (cull.LodRules(bias=1.0), [])
        return self.rules

    def _lod_table(self) -> tuple:
        """The ``LODDistances`` of the frame's quality level (the best without one)."""
        if not self.tables:
            return ()
        q = getattr(self.island, "quality", None)
        return self.tables[min(q.level, len(self.tables) - 1) if q is not None else -1]

    def _draws(self, out: Drawn, drawing: list, f, camera: tuple, shadows: bool, cascades) -> None:
        """117's ``_draws`` with 1800's level of detail (``objects1800.distance_levels``) and its runs: each part's
        instances in view by level into ``out``, the opaque into ``gbuffer``, the blended into ``transparent``."""
        table = self._table([b for b, _built in drawing])
        index = table.visible(np.concatenate([self._in_view(b, f) for b, _built in drawing]))
        levels = objects1800.distance_levels(table.centres[index], table.radii[index], *camera, self._lod_table(), 5)
        counts = np.bincount(levels + 1, minlength=6)
        out.levels, out.instances = [*counts[1:].tolist(), int(counts[0])], int(counts[1:].sum())
        keep = levels >= 0
        index, levels = index[keep], levels[keep]
        order, runs = table.runs(index, levels)
        picked = index[order]
        mats, centres, radii = table.mats[picked], table.centres[picked], table.radii[picked]
        colours = self._colours(table)[picked]
        for k, (_b, built) in enumerate(drawing):
            for p, part in built:
                for level, s, e in runs.get(k, ()):
                    worlds = part_worlds(mats[s:e], p["matrix"])
                    draws, _cast = part.models(level, worlds, shadows, (centres[s:e], radii[s:e]), colours[s:e])
                    (out.transparent if part.transparent else out.gbuffer).extend(draws)

    @staticmethod
    def colour_rows(stored: np.ndarray) -> np.ndarray:
        """The props' colours as the game's prop rows hold them, of the island's stored colours ([n, 4] floats):
        1800's colour words (``objects1800.prop_words``)."""
        return objects1800.prop_words(stored)

    def _colours(self, table) -> np.ndarray:
        """Each instance's prop colour (``colour_rows`` of the island's stored colour; white for what stores none) in
        ``table``'s order, made again with the table."""
        if self._colour_table is not table:
            rows = []
            for b in table.batches:
                stored = np.asarray([e.color or (1.0, 1.0, 1.0, 1.0) for e in b.entries], np.float32).reshape(-1, 4)
                got = self.colour_rows(stored)
                rows.append(got[b.cells.order] if b.cells is not None else got[:0])
            self._colour_table = table
            self._colour_words = np.concatenate(rows) if rows else self.colour_rows(np.zeros((0, 4), np.float32))
        return self._colour_words

    def place(self, drawn: Drawn, used: int, cascades: tuple) -> None:
        """The frame's instance data of ``drawn``'s runs into the frame's rows at once, their casters into the
        ``used`` cascades (``objects1800.place_runs``); nothing at a still camera (the same draws and ``cascades``:
        the rows sent last frame stay)."""
        if self.device is None:
            return
        rows = self.device.rows
        if rows.key is not None and rows.key[0] is drawn and rows.key[1:] == (used, cascades):
            return
        objects1800.place_runs(rows, [*drawn.gbuffer, *drawn.transparent], used)
        rows.key = (drawn, used, cascades)

    def release(self) -> None:
        for part in self.parts.values():  # before the buffers and maps they bind
            if isinstance(part, _Part):
                part.release()
        if self.device is not None:
            self.device.release(self.r.ctx)
            self.device = None
        super().release()
