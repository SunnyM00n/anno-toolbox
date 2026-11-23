"""The island's objects in an Anno 1800 island's look "The game's own shaders": 117's object set
(``desktopgameobjects.CGameObjects``: the shown batches' instances in one table, culled per ground cell, the parts
within reach compiled in the background, let go when far, a part that fails the view's own) with each part drawn by
1800's model shaders many instances a draw (``render.objects1800``), its level of detail by 1800's ``LODDistances``.

``CObjects1800(island)`` is the ``objects`` of ``desktopgame1800.CGameIsland1800``: ``frame(f, opts, shadows, size)``
gives the frame's ``SDrawn`` (``gbuffer``: the opaque runs, ``transparent``: the blended ones, each with its caster);
docs/rendering/island-1800-2205-view.md section 12.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from ...infra.i18n import tr
from ...models.shaderforward import PREZ_NORMALS, CNoPass, block_state
from ...render import cull, gamedraw, objects1800, terrain1800
from ...render.fxcdraw import CDummies
from ...render.quality import SETTINGS_FILE
from ...render.shadowforward import CASTER_BIAS, CASTER_SLOPE
from .desktopgameobjects import CGameObjects, SDrawn, _CPart
from .desktopgameparts import CSharedMaps, level_meshes
from .desktopgametables import part_worlds

JOB = "game-object-{}:{}"  # the game, a part's number
RULES_JOB = "game-object-{}-lod-rules"
IMAGES = (".psd", ".png", ".tga", ".dds")
CUBES = ("cModelEnvTex",)  # cube maps: the frame's stand-in (the viewer reads them as cubes)


@dataclass
class SCompiled:
    """A part's material compiled by 1800's model effect (``compile_part``): its colour pass and states, its caster
    (None: it casts nothing), its meshes per level of detail with the matrices each takes, its maps (the shader's name
    -> (path, ``CDdsFile``)), the material's fields and the part's bounding sphere."""

    colour: tuple
    state: object
    shadow: tuple | None
    levels: list
    maps: dict
    fields: dict
    sphere: tuple
    prepass: tuple | None = None  # its normals-and-depth pass (stages, states) for the occlusion; None: none
    is_prop: bool = False  # drawn by a prop shader (``objects1800.CPropRun``), not a model shader


def part_levels(files, part: dict) -> list:
    """A part's meshes per level of detail, cut to its material (117's rule, ``desktopgameparts.compile_part``)."""
    from ...models import configtree

    levels: list = []
    for path in configtree.lod_files(part["mesh"], files.exists):
        numbers = cull.level_numbers(part.get("lod_materials") or [], part.get("material"), len(levels))
        found = level_meshes(files, path, numbers, strict=not levels)
        levels.append(found or levels[-1])
    return levels


def part_maps(files, values: dict, game: str = "anno1800") -> dict:
    """The maps a material's fields name (a shader's texture name -> (the file the game loads, its ``CDdsFile``)); a
    map that does not read is left to the frame's stand-in."""
    from ...models import ddsdecode, fileresolve

    out = {}
    for name, raw in values.items():
        written = str(raw).replace(chr(92), "/")
        if name in CUBES or not written.lower().endswith(IMAGES):
            continue
        path = fileresolve.texture_file(written, files.exists, game)
        try:
            out[name] = (path, ddsdecode.CDdsFile(files.read(path))) if path else None
        except Exception:  # a map that does not read: white, as the engine's missing map
            out[name] = None
    return {k: loaded for k, loaded in out.items() if loaded is not None}


def compile_part(source: objects1800.CObjectSource, files, part: dict) -> SCompiled:
    """In the background: a part's colour pass (``ModelDefault``), caster (``ModelObj2Shadow``) and an opaque one's
    pre-pass (``ModelNormalsDepth``) by its material's effect, the states the view keeps (as the viewer's: no depth
    written by a blended one, ``LESS_EQUAL`` for the pre-pass's ``EQUAL``), its levels, maps and sphere; ``CNoPass``
    for a part 1800's model shaders do not draw."""
    values = part.get("values") or {}
    if not values:
        raise CNoPass("not a model of the game's shaders")
    if "Type" in values and "ShaderID" not in values:  # a .prp: the game's prop shaders
        return compile_prop(source, files, part)
    technique = source.colour_technique(values)
    colour, state = source.model_pass(values, technique), _forward_state(source.model_state(values, technique))
    shadow = source.model_pass(values, source.CASTER) if source.has_technique(values, source.CASTER) else None
    levels, sphere = _geometry(files, part, int(float(values.get("NumBonesPerVertex", 0) or 0)))
    try:
        prepass = source.prepass(values) if not state.blend else None
    except (CNoPass, RuntimeError):  # drawn all the same, left out of the occlusion
        prepass = None
    return SCompiled(colour, state, shadow, levels, part_maps(files, values, source.m_game), values, sphere, prepass)


def compile_prop(source: objects1800.CObjectSource, files, part: dict) -> SCompiled:
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
    except CNoPass:  # a prop that casts nothing
        shadow = None
    prepass = None
    if not state.blend:
        try:
            prez = (*defines, PREZ_NORMALS)
            prepass = source.effect_pass(fx, prez, normals), source.technique_state(fx, prez, normals)
        except (CNoPass, RuntimeError):  # drawn all the same, left out of the occlusion
            prepass = None
    levels, sphere = _geometry(files, part, 0)
    return SCompiled(
        colour, state, shadow, levels, part_maps(files, values, source.m_game), values, sphere, prepass, True
    )


def _forward_state(state):
    """A technique's states as the view's forward frame draws them (the viewer's: no depth written by a blended pass,
    ``LESS_EQUAL`` for the pre-pass's ``EQUAL``)."""
    if state.blend:
        state = replace(state, depth_write=False)
    if state.depth_func == "equal":
        state = replace(state, depth_func="less_equal", depth_write=True)
    return state


def _geometry(files, part: dict, per_vertex: int) -> tuple[list, tuple]:
    """A part's meshes per level with the matrices each takes, and its bounding sphere; ``CNoPass`` without any."""
    levels = part_levels(files, part)
    if not levels or not levels[0]:
        raise CNoPass("no geometry for the material")
    levels = [[(mesh, mesh.bone_count(per_vertex) if per_vertex else 1) for mesh in level] for level in levels]
    boxes = [mesh.box() for mesh, _bones in levels[0]]
    low, high = np.min([box[0] for box in boxes], axis=0), np.max([box[1] for box in boxes], axis=0)
    return levels, ((low + high) / 2, float(np.linalg.norm(high - low) / 2))


CASTER_STATE = replace(block_state({}), depth_bias=CASTER_BIAS, slope_bias=CASTER_SLOPE)  # the viewer's casters'


@dataclass
class SDevice:
    """What every part's draws share on the device: the maps, the stand-ins, the frame's buffers of the colour pass
    and of the casters' (``objects1800.SharedBuffers``), the frame's instance data (``objects1800.CFrameRows``)."""

    pool: objects1800.CMaps2D
    dummies: CDummies
    colour: objects1800.CSharedBuffers
    cast: objects1800.CSharedBuffers
    rows: objects1800.CFrameRows

    def release(self, ctx) -> None:
        """The maps, stand-ins and rows (the shared buffers are the look's, ``CGameIsland1800.m_shared``)."""
        self.pool.release()
        self.dummies.release(ctx)
        self.rows.release()


class CPart1800(_CPart):
    """A compiled part on the device: per mesh its colour draws and its casters' (``objects1800.CDraws``, kept from
    frame to frame), its maps from the pool; a frame's draws are ``objects1800.CRun``s over its instances (``RUN``,
    ``PROP_RUN``: the game's, ``chunk`` and ``PROP_CHUNK`` the instances a draw holds)."""

    RUN, PROP_RUN, PROP_CHUNK = objects1800.CRun, objects1800.CPropRun, objects1800.PROP_CHUNK

    @staticmethod
    def chunk(bones: int) -> int:
        return objects1800.chunk_size(bones)

    def __init__(self, ctx, got: SCompiled, device: SDevice) -> None:
        self.m_ctx, self.m_got, self.m_device, self.m_pool = ctx, got, device, device.pool
        self.m_is_transparent, self.m_fields = bool(got.state.blend), got.fields
        self.m_passes, self.m_made = {}, set()  # nothing made ahead (117's ``_CPart.ahead``)
        self.m_colour = gamedraw.CProgram(*got.colour)
        self.m_caster = gamedraw.CProgram(*got.shadow) if got.shadow is not None else None
        self.m_prez = (gamedraw.CProgram(*got.prepass[0]), got.prepass[1]) if got.prepass is not None else None
        self.m_paths = {name: path for name, (path, _dds) in got.maps.items()}
        for path, dds in got.maps.values():
            self.m_pool.want(path, dds)
        got.maps = {}  # the pool holds their pixels until their textures are made
        self.m_draws: dict[
            int, tuple
        ] = {}  # id of a mesh -> its colour ``CDraws``, its casters' and pre-pass's or None
        self._m_textures: dict | None = None
        self.begin()

    def begin(self) -> None:
        self.m_runs: list[objects1800.CRun] = []
        for draws in self.m_draws.values():
            for draw in draws:
                if draw is not None:
                    draw.begin()

    def textures(self, batch) -> dict:
        """The maps by the shader's names, made in ``batch`` on first use (``{}`` until a batch comes)."""
        if self._m_textures is None and batch is not None:
            self._m_textures = {name: self.m_pool.take(batch, path) for name, path in self.m_paths.items()}
        return self._m_textures or {}

    def _draws(self, mesh: gamedraw.CMesh) -> tuple:
        got = self.m_draws.get(id(mesh))
        if got is None:
            device, ctx = self.m_device, self.m_ctx
            seen = objects1800.SBinding(
                device.colour, rows=device.rows, is_mirrored=True, fallbacks=dict(terrain1800.FALLBACKS)
            )
            colour = objects1800.CDraws(ctx, self.m_colour, mesh, self.m_got.state, device.dummies, seen)
            cast = prez = None
            # 1800's cascades look along the light with x right and y up: the view's mirror and theirs cancel
            if self.m_caster is not None:
                cast = objects1800.CDraws(ctx, self.m_caster, mesh, CASTER_STATE, device.dummies,
                                         replace(seen, shared=device.cast, is_mirrored=False))  # fmt: skip
            if self.m_prez is not None:  # the frame hands its draws their own shared buffers (the pre-pass's)
                prez = objects1800.CDraws(
                    ctx, self.m_prez[0], mesh, self.m_prez[1], device.dummies, replace(seen, fallbacks={})
                )
            got = self.m_draws[id(mesh)] = (colour, cast, prez)
        return got

    def models(self, level: int, worlds: np.ndarray, shadows: bool, spheres: tuple,
               colours: np.ndarray | None = None) -> tuple[list, list]:  # fmt: skip
        """This frame's runs of ``worlds`` at ``level`` (with their casters when ``shadows``), at most
        ``objects1800.chunk_size`` instances each, a prop run with its props' ``colours`` (their words); (runs,
        [])."""
        out = []
        centres, radii = spheres
        kind = self.PROP_RUN if self.m_got.is_prop else self.RUN
        for mesh, bones in self.m_got.levels[min(level, len(self.m_got.levels) - 1)]:
            colour, cast, prez = self._draws(mesh)
            run_instances = self.PROP_CHUNK if self.m_got.is_prop else self.chunk(bones)
            for start in range(0, len(worlds), run_instances):
                caster = cast.take() if shadows and cast is not None else None
                end = start + run_instances
                run = kind(
                    colour.take(), caster, self, worlds[start:end], (centres[start:end], radii[start:end]), bones
                )
                run.with_prepass(prez.take() if prez is not None else None)
                if colours is not None and self.m_got.is_prop:
                    run.m_colours = colours[start : start + run_instances]
                out.append(run)
        self.m_runs += out
        return out, []

    def ahead(self, shadows: bool) -> tuple[list, list, list]:
        return [], [], []

    def all(self) -> list:
        return list(self.m_runs)

    def release(self) -> None:
        for draws in self.m_draws.values():
            for draw in draws:
                if draw is not None:
                    draw.release()
        self.m_draws = {}
        self.m_pool.give_back(self.m_paths.values())
        self._m_textures, self.m_paths = None, {}
        self.begin()


class CObjects1800(CGameObjects):
    """1800's objects by its own model shaders (module docstring); ``GAME`` and ``PART`` the game's (2205's:
    ``desktopobjects2205``)."""

    GAME, PART = "anno1800", CPart1800

    def __init__(self, island) -> None:
        super().__init__(island)
        self.m_tables: list = []  # the quality levels' ``LODDistances`` (``objects1800.lod_tables``), once read
        self.m_device: SDevice | None = None
        self._m_colour_table, self._m_colour_words = None, np.zeros(0, np.uint32)  # ``_colours``' last

    def _part(self, part: dict, source):
        """The device side of a view part: its ``CPart1800``, an exception (the view draws it) or None while it
        compiles."""
        key = self._key(part)
        found = self.m_parts.get(key)
        if found is not None:
            return found
        job = self.m_jobs.setdefault(key, JOB.format(self.GAME, len(self.m_jobs)))
        self.m_files = self.m_files or CSharedMaps(self.m_renderer.m_editor.m_files)
        files = self.m_files
        got = self.m_renderer.m_loader.get(job, lambda: compile_part(source, files, part))
        if got is None:
            return None
        if isinstance(got, Exception):
            self.m_parts[key] = got
            if not isinstance(got, CNoPass):
                self.m_renderer._message(tr("islands.mesh_games_shader_reason", mesh=part["mesh"],
                                   reason=f"{type(got).__name__}: {got}"[:300]))  # fmt: skip
            return got
        if self.m_device is None:
            quality, rhi = getattr(self.m_island, "m_quality", None), self.m_renderer.m_ctx.m_rhi
            pool = objects1800.CMaps2D(rhi, quality.max_texture if quality is not None else 0)
            self.m_device = SDevice(
                pool, CDummies(rhi), *self.m_island.m_shared, objects1800.CFrameRows(self.m_renderer.m_ctx)
            )
        self.m_parts[key] = self.PART(self.m_renderer.m_ctx, got, self.m_device)
        return self.m_parts[key]

    def _rules(self) -> cull.SLodRules:
        """1800's smallest size drawn (its engine settings; it has no ``LODBias``: 1) and its ``LODDistances``, read
        once in the background."""
        if self.m_rules is None:
            files = getattr(self.m_renderer.m_editor.m_files, "m_game", self.m_renderer.m_editor.m_files)

            def read() -> tuple:
                text = files.read(SETTINGS_FILE[self.GAME]).decode("utf-8", errors="replace")
                return replace(cull.read_lod_rules(text), bias=1.0), objects1800.lod_tables(text)

            got = self.m_renderer.m_loader.get(RULES_JOB.format(self.GAME), read)
            if got is None:
                return cull.SLodRules(bias=1.0)
            self.m_rules, self.m_tables = got if isinstance(got, tuple) else (cull.SLodRules(bias=1.0), [])
        return self.m_rules

    def _lod_table(self) -> tuple:
        """The ``LODDistances`` of the frame's quality level (the best without one)."""
        if not self.m_tables:
            return ()
        quality = getattr(self.m_island, "m_quality", None)
        return self.m_tables[min(quality.level, len(self.m_tables) - 1) if quality is not None else -1]

    def _draws(self, out: SDrawn, drawing: list, view_frame, camera: tuple, shadows: bool, cascades) -> None:
        """117's ``_draws`` with 1800's level of detail (``objects1800.distance_levels``) and its runs: each part's
        instances in view by level into ``out``, the opaque into ``gbuffer``, the blended into ``transparent``."""
        table = self._table([batch for batch, _built in drawing])
        index = table.visible(np.concatenate([self._in_view(batch, view_frame) for batch, _built in drawing]))
        levels = objects1800.distance_levels(
            table.m_centres[index], table.m_radii[index], *camera, self._lod_table(), 5
        )
        counts = np.bincount(levels + 1, minlength=6)
        out.levels, out.instances = [*counts[1:].tolist(), int(counts[0])], int(counts[1:].sum())
        keep = levels >= 0
        index, levels = index[keep], levels[keep]
        order, runs = table.runs(index, levels)
        picked = index[order]
        matrices, centres, radii = table.m_mats[picked], table.m_centres[picked], table.m_radii[picked]
        colours = self._colours(table)[picked]
        for k, (_b, built) in enumerate(drawing):
            for spec, part in built:
                for level, start, end in runs.get(k, ()):
                    worlds = part_worlds(matrices[start:end], spec["matrix"])
                    draws, _cast = part.models(
                        level, worlds, shadows, (centres[start:end], radii[start:end]), colours[start:end]
                    )
                    (out.transparent if part.m_is_transparent else out.gbuffer).extend(draws)

    @staticmethod
    def colour_rows(stored: np.ndarray) -> np.ndarray:
        """The props' colours as the game's prop rows hold them, of the island's stored colours ([n, 4] floats):
        1800's colour words (``objects1800.prop_words``)."""
        return objects1800.prop_words(stored)

    def _colours(self, table) -> np.ndarray:
        """Each instance's prop colour (``colour_rows`` of the island's stored colour; white for what stores none) in
        ``table``'s order, made again with the table."""
        if self._m_colour_table is not table:
            rows = []
            for batch in table.m_batches:
                stored = np.asarray(
                    [entry.color or (1.0, 1.0, 1.0, 1.0) for entry in batch.m_entries], np.float32
                ).reshape(-1, 4)
                got = self.colour_rows(stored)
                rows.append(got[batch.m_cells.m_order] if batch.m_cells is not None else got[:0])
            self._m_colour_table = table
            self._m_colour_words = np.concatenate(rows) if rows else self.colour_rows(np.zeros((0, 4), np.float32))
        return self._m_colour_words

    def place(self, drawn: SDrawn, used: int, cascades: tuple) -> None:
        """The frame's instance data of ``drawn``'s runs into the frame's rows at once, their casters into the
        ``used`` cascades (``objects1800.place_runs``); nothing at a still camera (the same draws and ``cascades``:
        the rows sent last frame stay)."""
        if self.m_device is None:
            return
        rows = self.m_device.rows
        if rows.m_key is not None and rows.m_key[0] is drawn and rows.m_key[1:] == (used, cascades):
            return
        objects1800.place_runs(rows, [*drawn.gbuffer, *drawn.transparent], used)
        rows.m_key = (drawn, used, cascades)

    def release(self) -> None:
        for part in self.m_parts.values():  # before the buffers and maps they bind
            if isinstance(part, _CPart):
                part.release()
        if self.m_device is not None:
            self.m_device.release(self.m_renderer.m_ctx)
            self.m_device = None
        super().release()
