"""Anno 2205's particle effects drawn by its own ``particle.fx`` in the forward frame (``frameforward``): an effect's
baked ticks laid out as the track textures the game's vertex shader fetches, the effect's and its materials' constants
by name, one instanced draw per run of materials of one blend.

``tracks(effect)`` is the CPU side (no device): the five textures' texels and the values that go with them;
``effect_values`` the constants of one draw at a moment. ``ForwardEffect`` draws one placed effect with the frame's
models (``prepare`` / ``draw`` as ``frameforward.ForwardModel``). docs/rendering/shader-compile.md section 11
"Particles".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import gameshader as gs
from .frameforward import fill_frame
from .gamedraw import Mesh
from .particledraw import lift
from .particlefx import Effect, turns

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


def not_drawn(effect: Effect) -> str:
    """Why the game's shader does not draw ``effect`` here ("" when it does): lines, refraction, waves and movies
    are other variants and textures (our drawer keeps them)."""
    if len(effect.materials) > MAX_MATERIALS or effect.frame_count > MAX_FRAMES:
        return "more materials or frames than the engine takes"
    for m in effect.materials:
        if m.movie_texture or m.line_type or m.refractive or m.wave:
            return "a movie, line, refractive or wave material"
    return ""


def defines(effect: Effect, additive: bool, shadows: bool = False) -> tuple:
    """The defines of the variant the engine draws a run of ``effect`` with (additive ones are never lit or
    shadowed); ``shadows``: the placing node's ``ReceiveShadows``."""
    if additive:
        return (*LIMITS, ("PARTICLE_ADDITIVE", ""))
    lit = (("PARTICLE_ENABLE_LIGHTING", ""),) if effect.lighting else ()
    occluded = (("PARTICLE_ENABLE_SELF_OCCLUSION", ""),) if effect.self_occlusion else ()
    return (*LIMITS, *occluded, *((SHADOWED,) if shadows else ()), *lit)


def quaternions(m: np.ndarray) -> np.ndarray:
    """[n, 4] unit quaternions (x, y, z, w) of the turns ``m`` ([n, 3, 3], column vectors), as the shaders'
    ``RotateWithQuaternion`` turns a point."""
    t = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    squares = np.stack([1 + m[:, 0, 0] - m[:, 1, 1] - m[:, 2, 2], 1 - m[:, 0, 0] + m[:, 1, 1] - m[:, 2, 2],
                        1 - m[:, 0, 0] - m[:, 1, 1] + m[:, 2, 2], 1 + t], -1) / 4  # fmt: skip
    big = np.argmax(squares, -1)
    root = np.sqrt(np.maximum(squares[np.arange(len(m)), big], 1e-12))
    sums = {(0, 1): m[:, 1, 0] + m[:, 0, 1], (0, 2): m[:, 0, 2] + m[:, 2, 0], (1, 2): m[:, 2, 1] + m[:, 1, 2]}
    diffs = (m[:, 2, 1] - m[:, 1, 2], m[:, 0, 2] - m[:, 2, 0], m[:, 1, 0] - m[:, 0, 1])  # w times x, y, z, x 4
    q = np.zeros((len(m), 4))
    for k in range(4):
        rows = big == k
        if not rows.any():
            continue
        r = root[rows]
        q[rows, k] = r
        for j in range(3):
            if j != k:
                q[rows, j] = (diffs[j][rows] if k == 3 else sums[tuple(sorted((j, k)))][rows]) / (4 * r)
        if k < 3:
            q[rows, 3] = diffs[k][rows] / (4 * r)
    return q


def _continuous(q: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """``q`` with each particle's quaternions signed alike tick to tick (q and -q are one turn; the texture filter
    blends neighbours)."""
    if not len(q):
        return q
    flip = np.sign(np.einsum("ij,ij->i", q[1:], q[:-1]))
    step = np.concatenate([[1.0], np.where(flip == 0, 1.0, flip)])
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    step[starts] = 1.0
    sign = np.cumprod(step)
    return q * (sign * np.repeat(sign[starts], counts))[:, None]


@dataclass
class Tracks:
    """An effect laid out for the shader: ``textures`` by name ([height, width, 4] floats; the tracks are a row per
    particle, a column per tick), ``rows`` the effect's particle in each row, ``runs`` (additive, first row, rows)
    in drawing order, the position box, the largest size."""

    textures: dict
    rows: np.ndarray
    runs: list
    center: np.ndarray
    extents: np.ndarray
    scale_max: float


def tracks(effect: Effect) -> Tracks:
    """The track textures of ``effect``: its particles sorted by their materials' order, each tick at its column (a
    tick where the particle is not alive is blank: size and alpha 0)."""
    frames, mats = effect.frame_count, effect.materials
    rows = np.lexsort((effect.particle_material, [mats[m].order for m in effect.particle_material]))
    n, count, first, start = len(rows), effect.count[rows], effect.first[rows], effect.start[rows]
    row = np.repeat(np.arange(n), count)
    age = np.arange(int(count.sum())) - np.repeat(np.cumsum(count) - count, count)
    src = np.repeat(first, count) + age
    col = (np.repeat(start, count) + age) % frames
    pos = effect.position
    lo, hi = (pos.min(0), pos.max(0)) if len(pos) else (np.zeros(3), np.zeros(3))
    center, extents = (lo + hi) / 2, np.maximum((hi - lo) / 2, 1e-4)
    scale_max = max(float(effect.size.max()) if len(pos) else 0.0, 1e-6)
    blank = np.zeros((n, frames, 4), np.float32)
    position, color, rotation, scale = blank.copy(), blank.copy(), blank.copy(), blank.copy()
    position[...] = (0.5, 0.5, 0.5, 1.0)
    rotation[...] = (0.5, 0.5, 0.5, 1.0)  # no turn
    position[row, col, :3] = (pos[src] - center) / extents * 0.5 + 0.5
    color[row, col] = effect.color[src]
    q = _continuous(quaternions(turns(effect.rotation[src].astype(np.float64))), count)
    rotation[row, col] = q * 0.5 + 0.5
    scale[row, col, :3] = effect.size[src] / scale_max
    material = effect.particle_material[rows]
    scale[..., 3] = (material / 256.0)[:, None]  # the shader reads w x 256
    born = np.zeros((1, n, 4), np.float32)
    born[0, :, 0], born[0, :, 1], born[0, :, 2] = start / 255.0, 1.0, count / 255.0  # g: no tick moved back
    additive = np.array([mats[m].additive for m in material], bool)
    cuts = np.flatnonzero(np.diff(additive.astype(int))) + 1
    edges = [0, *cuts.tolist(), n]
    runs = [(bool(additive[a]), a, b - a) for a, b in zip(edges, edges[1:], strict=False) if b > a]
    textures = dict(zip(TEXTURES, (position, color, rotation, scale, born), strict=True))
    return Tracks(textures, rows, runs, center, extents, scale_max)


def _rows_of(values: list, width: int = 4) -> list:
    """``values`` in float4s, ``width`` to a row (an array the shader indexes by i / 4, i % 4)."""
    padded = list(values) + [0.0] * (-len(values) % width)
    return [padded[k : k + width] for k in range(0, len(padded), width)]


@dataclass
class Placement:
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


def sorted_rows(effect: Effect, tr: Tracks, place: Placement, run: tuple) -> list:
    """The rows of ``run`` far to near from ``place.eye`` at the moment (the particles not alive last)."""
    _additive, first, n = run
    ticks = place.seconds * 1000.0 * place.speed / effect.frame_ms
    frame = int(ticks) % effect.frame_count
    rows = tr.rows[first : first + n]
    age = (frame - effect.start[rows]) % effect.frame_count
    alive = age < effect.count[rows]
    at = effect.position[np.minimum(effect.first[rows] + age, len(effect.position) - 1)]
    world = at @ place.world[:3, :3].T + place.world[:3, 3]
    far = -np.linalg.norm(world - (place.eye if place.eye is not None else 0), axis=1)
    order = np.lexsort((far, ~alive))
    return (first + order).astype(float).tolist()


def effect_values(effect: Effect, tr: Tracks, place: Placement, run: tuple) -> dict:
    """The constants of one draw of ``run`` (``Tracks.runs``) of ``effect`` at ``place``."""
    linear = place.world[:3, :3]
    scale = float(np.linalg.norm(linear[:, 0])) or 1.0
    unscaled = np.identity(4)
    unscaled[:3, :3], unscaled[:3, 3] = linear / scale, place.world[:3, 3]
    frames = effect.frame_count
    age = (place.seconds * 1000.0 * place.speed / effect.frame_ms / frames) % 1.0
    mats = effect.materials
    flags = EMITTER_BOUND | (SOFT if place.soft else 0) | (DEPTH_BIAS if place.depth_bias else 0)
    values = {
        "cParticleEmitterMtxWorld": unscaled.T, "cParticleScale": 1.0, "cParticleColor": [*place.tint, place.alpha],
        "cParticleWindImpactPerFrame": 0.0, "cParticlePerInstanceScaleMax": tr.scale_max,
        "cParticleCenter": tr.center.tolist(), "cParticleExtents": tr.extents.tolist(), "cParticleAge": age,
        "cParticleCount": float(len(tr.rows)), "cParticleFrameCount": float(frames),
        "cParticleInstanceIDOffset": float(run[1]), "cParticleInscatteringFactor": 1.0,
        "cParticleEmitterScales": _rows_of([scale] * MAX_FRAMES),
        "cParticleMaterialCropsOrMovieSpeed": [list(m.crop) for m in mats],
        "cParticleMaterialViewAligned": _rows_of([1.0 if m.view_aligned else 0.0 for m in mats]),
        "cParticleMaterialColorBoost": _rows_of([lift(m.color_boost) for m in mats]),
    }  # fmt: skip
    if effect.sort and run[2] <= MAX_SORTED:
        flags |= SORTED
        values["cSortedParticleIndices"] = _rows_of(sorted_rows(effect, tr, place, run))
    values["cParticleFlags"] = flags
    return values


def quad_mesh() -> Mesh:
    """The four corners of a particle's quad (two triangles), as ``vsParticle`` takes them at its position input."""
    return Mesh(QUAD.tobytes(), 8, ((0, 7, 2, 0),), np.array([0, 1, 2, 0, 2, 3], np.uint32), 4)


class ForwardEffect:
    """One effect file's emitter as the game draws it: ``draws`` (``fxcdraw.FxcDraw`` per run of ``tracks.runs``),
    the track textures made once; ``place`` and ``atlas`` (the material texture) are set per frame."""

    blended = True

    def __init__(self, ctx, effect: Effect, tr: Tracks, draws: list) -> None:
        self.ctx, self.effect, self.tracks, self.draws = ctx, effect, tr, draws
        self.place = Placement(np.identity(4))
        self.atlas = None
        self.textures = {
            name: ctx.texture((a.shape[1], a.shape[0]), 4, np.ascontiguousarray(a, "<f4").tobytes(), dtype="f4")
            for name, a in tr.textures.items()
        }
        for draw, run in zip(draws, tr.runs, strict=True):
            draw.instances = run[2]

    @property
    def world(self) -> np.ndarray:
        return self.place.world

    def prepare(self, rp, signature, batch, cam, light: dict, extra: dict | None = None) -> None:
        maps = {**(extra or {}), **{n: t.native for n, t in self.textures.items()}}
        if self.atlas is not None:
            maps["cParticleMaterialTex"] = self.atlas
        for draw, run in zip(self.draws, self.tracks.runs, strict=True):
            values = effect_values(self.effect, self.tracks, self.place, run)

            def fill(c: dict, values=values) -> None:
                fill_frame(c, cam, light)
                gs.set_values(c.values(), values, ROW_ARRAYS)

            draw.prepare(rp, signature, batch, maps, fill)

    def draw(self, cb, size) -> None:
        if self.atlas is None:  # the material texture still loads
            return
        for draw in self.draws:
            draw.draw(cb, size)

    def release(self) -> None:
        for draw in self.draws:
            draw.release()
        for tex in self.textures.values():
            tex.release()
        self.draws, self.textures = [], {}
