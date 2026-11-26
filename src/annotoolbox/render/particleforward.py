"""Anno 2205's particle effects drawn by its own ``particle.fx`` in the forward frame (``frameforward``): an effect's
baked ticks laid out as the track textures the game's vertex shader fetches, the effect's and its materials' constants
by name, one instanced draw per run of materials of one blend.

``tracks(effect)`` is the CPU side (no device): the five textures' texels and the values that go with them;
``effect_values`` the constants of one draw at a moment. ``CForwardEffect`` draws one placed effect with the frame's
models (``prepare`` / ``draw`` as ``frameforward.CForwardModel``). docs/rendering/shader-compile.md section 11
"Particles".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import gameshader as gs
from .frameforward import fill_frame
from .gamedraw import CMesh
from .particledraw import lift
from .particlefx import SEffect, turns

FX = "data/shaders/fx/particle.fx"
TECHNIQUE = "Particle"
# the engine's limits, as every particle key of 2205's cache.xml gives them
LIMITS = (("PARTICLE_MAX_MATERIAL_COUNT", "16"), ("PARTICLE_MAX_MATERIAL_COUNT_BY_4", "16"),
          ("PARTICLE_MAX_FRAME_COUNT", "256"), ("PARTICLE_MAX_FRAME_COUNT_BY_4", "256"),
          ("PARTICLE_MAX_SORTED_PARTICLE_COUNT_BY_4", "2048"), ("PARTICLE_LINE_SEGMENT_COUNT", "16"))  # fmt: skip
MAX_MATERIALS, MAX_FRAMES, MAX_SORTED = 16, 256, 2048 * 4
# cParticleFlags: the emitter's matrix, the sorted index array, the depth moved nearer, the soft fade
EMITTER_BOUND, SORTED, DEPTH_BIAS, SOFT = 1 << 1, 1 << 2, 1 << 3, 1 << 4
SHADOWED = ("PARTICLE_USE_SHADOWMAP", "1")  # a node's ReceiveShadows; never with PARTICLE_ADDITIVE (cache.xml)
ALPHA_SLOT, ADDITIVE_SLOT = 0, 1  # the technique's state blocks: sbParticleAlpha, sbParticleAdditive
TEXTURES = (
    "cParticlePositionTex",
    "cParticleColorTex",
    "cParticleRotationTex",
    "cParticleScaleTex",
    "cParticleBornTex",
)
ROW_ARRAYS = ("cParticleEmitterScales", "cParticleMaterialCropsOrMovieSpeed", "cParticleMaterialViewAligned",
              "cParticleMaterialColorBoost", "cSortedParticleIndices")  # fmt: skip
QUAD = np.array([[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5]], "<f4")  # the corners vsParticle takes


def not_drawn(effect: SEffect) -> str:
    """Why the game's shader does not draw ``effect`` here ("" when it does): lines, refraction, waves and movies
    are other variants and textures (our drawer keeps them), and so is a strip without a frame time or frames, which
    stands at its first tick there (``SEffect.at``) where the game's constants would divide by it."""
    if effect.frame_ms <= 0 or effect.frame_count <= 0:
        return "no frame time or frames"
    if len(effect.materials) > MAX_MATERIALS or effect.frame_count > MAX_FRAMES:
        return "more materials or frames than the engine takes"
    for material in effect.materials:
        if material.movie_texture or material.line_type or material.refractive or material.wave:
            return "a movie, line, refractive or wave material"
    return ""


def defines(effect: SEffect, additive: bool, shadows: bool = False) -> tuple:
    """The defines of the variant the engine draws a run of ``effect`` with (additive ones are never lit or
    shadowed); ``shadows``: the placing node's ``ReceiveShadows``."""
    if additive:
        return (*LIMITS, ("PARTICLE_ADDITIVE", ""))
    lit = (("PARTICLE_ENABLE_LIGHTING", ""),) if effect.lighting else ()
    occluded = (("PARTICLE_ENABLE_SELF_OCCLUSION", ""),) if effect.self_occlusion else ()
    return (*LIMITS, *occluded, *((SHADOWED,) if shadows else ()), *lit)


def quaternions(matrices: np.ndarray) -> np.ndarray:
    """[n, 4] unit quaternions (x, y, z, w) of the turns ``matrices`` ([n, 3, 3], column vectors), as the shaders'
    ``RotateWithQuaternion`` turns a point."""
    trace = matrices[:, 0, 0] + matrices[:, 1, 1] + matrices[:, 2, 2]
    squares = np.stack([1 + matrices[:, 0, 0] - matrices[:, 1, 1] - matrices[:, 2, 2], 1 - matrices[:, 0, 0] + matrices[:, 1, 1] - matrices[:, 2, 2],
                        1 - matrices[:, 0, 0] - matrices[:, 1, 1] + matrices[:, 2, 2], 1 + trace], -1) / 4  # fmt: skip
    big = np.argmax(squares, -1)
    root = np.sqrt(np.maximum(squares[np.arange(len(matrices)), big], 1e-12))
    sums = {
        (0, 1): matrices[:, 1, 0] + matrices[:, 0, 1],
        (0, 2): matrices[:, 0, 2] + matrices[:, 2, 0],
        (1, 2): matrices[:, 2, 1] + matrices[:, 1, 2],
    }
    diffs = (
        matrices[:, 2, 1] - matrices[:, 1, 2],
        matrices[:, 0, 2] - matrices[:, 2, 0],
        matrices[:, 1, 0] - matrices[:, 0, 1],
    )  # w times x, y, z, x 4
    quaternion_rows = np.zeros((len(matrices), 4))
    for k in range(4):
        rows = big == k
        if not rows.any():
            continue
        root_rows = root[rows]
        quaternion_rows[rows, k] = root_rows
        for j in range(3):
            if j != k:
                quaternion_rows[rows, j] = (diffs[j][rows] if k == 3 else sums[tuple(sorted((j, k)))][rows]) / (
                    4 * root_rows
                )
        if k < 3:
            quaternion_rows[rows, 3] = diffs[k][rows] / (4 * root_rows)
    return quaternion_rows


def _continuous(quaternion_rows: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """``quaternion_rows`` with each particle's quaternions signed alike tick to tick (q and -q are one turn; the texture filter
    blends neighbours)."""
    if not len(quaternion_rows):
        return quaternion_rows
    flip = np.sign(np.einsum("ij,ij->i", quaternion_rows[1:], quaternion_rows[:-1]))
    step = np.concatenate([[1.0], np.where(flip == 0, 1.0, flip)])
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    step[starts] = 1.0
    sign = np.cumprod(step)
    return quaternion_rows * (sign * np.repeat(sign[starts], counts))[:, None]


@dataclass
class STracks:
    """An effect laid out for the shader: ``textures`` by name ([height, width, 4] floats; the tracks are a row per
    particle, a column per tick), ``rows`` the effect's particle in each row, ``runs`` (additive, first row, rows)
    in drawing order, the position box, the largest size."""

    textures: dict
    rows: np.ndarray
    runs: list
    center: np.ndarray
    extents: np.ndarray
    max_scale: float


def tracks(effect: SEffect) -> STracks:
    """The track textures of ``effect``: its particles sorted by their materials' order, each tick at its column (a
    tick where the particle is not alive is blank: size and alpha 0)."""
    frames, materials = effect.frame_count, effect.materials
    rows = np.lexsort(
        (effect.particle_material, [materials[material_index].order for material_index in effect.particle_material])
    )
    row_count, count, first, start = len(rows), effect.count[rows], effect.first[rows], effect.start[rows]
    row = np.repeat(np.arange(row_count), count)
    age = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    source_tick = np.repeat(first, count) + age
    column = (np.repeat(start, count) + age) % frames
    positions = effect.position
    min_box, max_box = (positions.min(0), positions.max(0)) if len(positions) else (np.zeros(3), np.zeros(3))
    center, extents = (min_box + max_box) / 2, np.maximum((max_box - min_box) / 2, 1e-4)
    max_scale = max(float(effect.size.max()) if len(positions) else 0.0, 1e-6)
    blank = np.zeros((row_count, frames, 4), np.float32)
    position, color, rotation, scale = blank.copy(), blank.copy(), blank.copy(), blank.copy()
    position[...] = (0.5, 0.5, 0.5, 1.0)
    rotation[...] = (0.5, 0.5, 0.5, 1.0)  # no turn
    position[row, column, :3] = (positions[source_tick] - center) / extents * 0.5 + 0.5
    color[row, column] = effect.color[source_tick]
    tick_quaternions = _continuous(quaternions(turns(effect.rotation[source_tick].astype(np.float64))), count)
    rotation[row, column] = tick_quaternions * 0.5 + 0.5
    scale[row, column, :3] = effect.size[source_tick] / max_scale
    material = effect.particle_material[rows]
    scale[..., 3] = (material / 256.0)[:, None]  # the shader reads w x 256
    born = np.zeros((1, row_count, 4), np.float32)
    born[0, :, 0], born[0, :, 1], born[0, :, 2] = start / 255.0, 1.0, count / 255.0  # g: no tick moved back
    additive = np.array([materials[material_index].additive for material_index in material], bool)
    cuts = np.flatnonzero(np.diff(additive.astype(int))) + 1
    edges = [0, *cuts.tolist(), row_count]
    runs = [
        (bool(additive[begin]), begin, end - begin) for begin, end in zip(edges, edges[1:], strict=False) if end > begin
    ]
    textures = dict(zip(TEXTURES, (position, color, rotation, scale, born), strict=True))
    return STracks(textures, rows, runs, center, extents, max_scale)


def _rows_of(values: list, width: int = 4) -> list:
    """``values`` in float4s, ``width`` to a row (an array the shader indexes by i / 4, i % 4)."""
    padded = list(values) + [0.0] * (-len(values) % width)
    return [padded[k : k + width] for k in range(0, len(padded), width)]


@dataclass
class SPlacement:
    """Where and how an effect plays this frame: ``world`` (column vectors, the view's axes), the config's tint and
    opacity, the seconds into the effect, its pace, the camera's eye (the sorting), the node's soft fade and depth
    bias."""

    world: np.ndarray
    tint: tuple = (1.0, 1.0, 1.0)
    alpha: float = 1.0
    seconds: float = 0.0
    speed: float = 1.0
    eye: np.ndarray | None = None
    soft: bool = False
    depth_bias: bool = False


def sorted_rows(effect: SEffect, track_set: STracks, place: SPlacement, run: tuple) -> list:
    """The rows of ``run`` far to near from ``place.eye`` at the moment (the particles not alive last)."""
    _additive, first, count = run
    ticks = place.seconds * 1000.0 * place.speed / effect.frame_ms
    frame = int(ticks) % effect.frame_count
    rows = track_set.rows[first : first + count]
    age = (frame - effect.start[rows]) % effect.frame_count
    alive = age < effect.count[rows]
    at = effect.position[np.minimum(effect.first[rows] + age, len(effect.position) - 1)]
    world = at @ place.world[:3, :3].T + place.world[:3, 3]
    far = -np.linalg.norm(world - (place.eye if place.eye is not None else 0), axis=1)
    order = np.lexsort((far, ~alive))
    return (first + order).astype(float).tolist()


def effect_values(effect: SEffect, track_set: STracks, place: SPlacement, run: tuple) -> dict:
    """The constants of one draw of ``run`` (``STracks.runs``) of ``effect`` at ``place``."""
    linear = place.world[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0])) or 1.0
    unscaled = np.identity(4)
    unscaled[:3, :3], unscaled[:3, 3] = linear / scale, place.world[:3, 3]
    frames = effect.frame_count
    age = (place.seconds * 1000.0 * place.speed / effect.frame_ms / frames) % 1.0
    materials = effect.materials
    flags = EMITTER_BOUND | (SOFT if place.soft else 0) | (DEPTH_BIAS if place.depth_bias else 0)
    values = {
        "cParticleEmitterMtxWorld": unscaled.T, "cParticleScale": 1.0, "cParticleColor": [*place.tint, place.alpha],
        "cParticleWindImpactPerFrame": 0.0, "cParticlePerInstanceScaleMax": track_set.max_scale,
        "cParticleCenter": track_set.center.tolist(), "cParticleExtents": track_set.extents.tolist(), "cParticleAge": age,
        "cParticleCount": float(len(track_set.rows)), "cParticleFrameCount": float(frames),
        "cParticleInstanceIDOffset": float(run[1]), "cParticleInscatteringFactor": 1.0,
        "cParticleEmitterScales": _rows_of([scale] * MAX_FRAMES),
        "cParticleMaterialCropsOrMovieSpeed": [list(material.crop) for material in materials],
        "cParticleMaterialViewAligned": _rows_of([1.0 if material.view_aligned else 0.0 for material in materials]),
        "cParticleMaterialColorBoost": _rows_of([lift(material.color_boost) for material in materials]),
    }  # fmt: skip
    if effect.sort and run[2] <= MAX_SORTED:
        flags |= SORTED
        values["cSortedParticleIndices"] = _rows_of(sorted_rows(effect, track_set, place, run))
    values["cParticleFlags"] = flags
    return values


def quad_mesh() -> CMesh:
    """The four corners of a particle's quad (two triangles), as ``vsParticle`` takes them at its position input."""
    return CMesh(QUAD.tobytes(), 8, ((0, 7, 2, 0),), np.array([0, 1, 2, 0, 2, 3], np.uint32), 4)


class CForwardEffect:
    """One effect file's emitter as the game draws it: ``draws`` (``fxcdraw.CFxcDraw`` per run of ``tracks.runs``),
    the track textures made once; ``m_place`` and ``m_atlas`` (the material texture) are set per frame."""

    m_blended = True

    def __init__(self, ctx, effect: SEffect, track_set: STracks, draws: list) -> None:
        self.m_ctx, self.m_effect, self.m_tracks, self.m_draws = ctx, effect, track_set, draws
        self.m_place = SPlacement(np.identity(4))
        self.m_atlas = None
        self.m_textures = {
            name: ctx.texture(
                (array.shape[1], array.shape[0]), 4, np.ascontiguousarray(array, "<f4").tobytes(), dtype="f4"
            )
            for name, array in track_set.textures.items()
        }
        for draw, run in zip(draws, track_set.runs, strict=True):
            draw.instances = run[2]

    @property
    def m_world(self) -> np.ndarray:
        return self.m_place.world

    def prepare(self, render_pass, signature, batch, camera, light: dict, extra: dict | None = None) -> None:
        maps = {**(extra or {}), **{name: texture.native for name, texture in self.m_textures.items()}}
        if self.m_atlas is not None:
            maps["cParticleMaterialTex"] = self.m_atlas
        for draw, run in zip(self.m_draws, self.m_tracks.runs, strict=True):
            values = effect_values(self.m_effect, self.m_tracks, self.m_place, run)

            def fill(constants: dict, values=values) -> None:
                fill_frame(constants, camera, light)
                gs.set_values(constants.values(), values, ROW_ARRAYS)

            draw.prepare(render_pass, signature, batch, maps, fill)

    def draw(self, command_buffer, size_px) -> None:
        if self.m_atlas is None:  # the material texture still loads
            return
        for draw in self.m_draws:
            draw.draw(command_buffer, size_px)

    def release(self) -> None:
        for draw in self.m_draws:
            draw.release()
        for texture in self.m_textures.values():
            texture.release()
        self.m_draws, self.m_textures = [], {}
