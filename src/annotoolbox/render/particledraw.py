"""Particle effects in the 3D view (island-editor-view "Particle effects"): the sprites of every effect placed in a
model, for every instance of that model near the camera, drawn as quads with the effect's texture tile.

``collect`` is the CPU side (no GL): it asks each effect for its sprites at a time and writes one record per
quad, grouped by texture and blending. ``ParticleDrawer`` draws the groups as instanced strips, additive ones
last, the others sorted far to near. The view's depth is tested, never written.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

import numpy as np

from .camera import gl
from .cull import boxes_in_view
from .particlefx import CAMERA, FIXED, UPRIGHT, Effect, quad_axes

MAX_QUADS = 40_000  # a frame's budget; the nearest instances are served first
RANGE = 260.0  # world units from the camera beyond which a model's effects are not drawn
MAX_INSTANCES = 400  # per model
EFFECT_REACH = 14.0  # how far from the model's origin its particles may stray, for the view test
FLOATS = 21  # centre 3, width axis 3, height axis 3, size 2, colour 4, texture tile 4, mode and spin 2
GAIN = 1.0  # the effect textures' values as the 3D view's light takes them
BOOST_SLOPE, BOOST_POWER = 0.07, 2.2  # the game's colour lift of a material: 1 + (boost * 0.07) ^ 2.2

VERTEX = """
#version 330
uniform mat4 viewProj; uniform vec3 camRight; uniform vec3 camUp; uniform vec3 eye;
in vec3 center; in vec3 axisX; in vec3 axisY; in vec2 size; in vec4 color; in vec4 tile; in vec2 modeSpin;
out vec2 vUv; out vec4 vColor;
void main() {
    vec2 c = vec2(float(gl_VertexID & 1), float((gl_VertexID >> 1) & 1)) - 0.5;
    int mode = int(modeSpin.x + 0.5);
    vec3 right = axisX; vec3 up = axisY;
    if (mode == 1) {
        float cs = cos(modeSpin.y); float sn = sin(modeSpin.y);
        right = camRight * cs + camUp * sn; up = camUp * cs - camRight * sn;
    } else if (mode == 2) {
        vec3 to = eye - center; to.y = 0.0;
        up = vec3(0.0, 1.0, 0.0);
        right = length(to) > 1e-4 ? normalize(cross(up, to)) : camRight;
    }
    vec3 p = center + right * c.x * size.x + up * c.y * size.y;
    gl_Position = viewProj * vec4(p, 1.0);
    vUv = tile.xy + vec2(c.x + 0.5, 0.5 - c.y) * tile.zw;
    vColor = color;
}
"""
FRAGMENT = """
#version 330
uniform sampler2D atlas;
in vec2 vUv; in vec4 vColor; out vec4 frag;
void main() {
    vec4 t = texture(atlas, vUv);
    frag = vec4(t.rgb * vColor.rgb, t.a * vColor.a);
}
"""


@dataclass(frozen=True)
class Placed:
    """An effect file placed in a model: ``matrix`` takes its space into the model's, ``tint`` and ``alpha`` are the
    config's colour and opacity, ``speed`` its pace (the config's time scale, inverted); ``sheet`` (columns, rows,
    frames) says the texture is a flipbook: a particle shows the frame its age is at."""

    file: str
    matrix: np.ndarray
    tint: tuple[float, float, float] = (1.0, 1.0, 1.0)
    alpha: float = 1.0
    speed: float = 1.0
    started: bool = True
    sheet: tuple[int, int, int] | None = None


def sheet_tiles(crop: tuple[float, float, float, float], sheet: tuple[int, int, int], life: np.ndarray) -> np.ndarray:
    """The texture tile ([n, 4]: u, v, width, height) of each particle of a flipbook of ``sheet`` laid over ``crop``,
    by how far through its life it is."""
    columns, lines, frames = sheet
    frame = np.minimum((life * frames).astype(np.int64), min(frames, columns * lines) - 1)
    u, v, w, h = crop
    wide, high = np.full(len(life), w / columns), np.full(len(life), h / lines)
    return np.stack([u + frame % columns * wide, v + frame // columns * high, wide, high], -1).astype("f4")


def lift(boost: int) -> float:
    """The game's colour lift of a material with colour boost byte ``boost``."""
    return 1.0 + (boost * BOOST_SLOPE) ** BOOST_POWER if boost else 1.0


def pick_instances(mats: np.ndarray, eye: np.ndarray, planes: np.ndarray | None) -> np.ndarray:
    """Indices into ``mats`` ([n, 4, 4] model placements, scale 0 = hidden) of the instances whose effects can show:
    in range, in view (``planes`` None: wherever), the nearest ``MAX_INSTANCES``, nearest first."""
    if not len(mats):
        return np.zeros(0, np.int64)
    at = mats[:, :3, 3]
    distance = np.linalg.norm(at - eye, axis=1)
    keep = (distance < RANGE) & (np.linalg.norm(mats[:, :3, 0], axis=1) > 1e-6)
    if planes is not None:
        keep &= boxes_in_view(planes, at - EFFECT_REACH, at + EFFECT_REACH)
    found = np.flatnonzero(keep)
    return found[np.argsort(distance[found])][:MAX_INSTANCES]


def records(effect: Effect, sprites, placed: Placed, mats: np.ndarray) -> list[tuple[str, bool, np.ndarray]]:
    """Quad records ([n, FLOATS]) of ``sprites`` of ``effect`` for each placement in ``mats`` ([k, 4, 4], the
    placed model's matrices), as (texture, additive, rows) per material."""
    out = []
    world = mats @ placed.matrix  # effect space to the view's
    linear = world[:, :3, :3]
    scale = np.maximum(np.linalg.norm(linear[:, :, 0], axis=1), 1e-6)
    for index, material in enumerate(effect.materials):
        mine = sprites.material == index
        mine &= sprites.color[:, 3] > 0.004
        if not mine.any():
            continue
        pos, size, rot, color, life = (
            a[mine] for a in (sprites.position, sprites.size, sprites.rotation, sprites.color, sprites.life)
        )
        k, n = len(mats), len(pos)
        center = np.einsum("kij,nj->kni", linear, pos) + world[:, :3, 3][:, None, :]
        right, up = (
            quad_axes(rot) if material.view_aligned == FIXED else (np.zeros((n, 3), "f4"), np.zeros((n, 3), "f4"))
        )
        axis_x = np.einsum("kij,nj->kni", linear, right) / scale[:, None, None]
        axis_y = np.einsum("kij,nj->kni", linear, up) / scale[:, None, None]
        rows = np.empty((k, n, FLOATS), "f4")
        rows[..., 0:3], rows[..., 3:6], rows[..., 6:9] = center, axis_x, axis_y
        rows[..., 9] = size[None, :, 0] * scale[:, None]
        rows[..., 10] = size[None, :, 2] * scale[:, None]
        rgb = color[:, :3] * np.array(placed.tint, "f4") * lift(material.color_boost) * GAIN
        rows[..., 11:14] = rgb[None]
        rows[..., 14] = (color[:, 3] * placed.alpha)[None]
        rows[..., 15:19] = (
            sheet_tiles(material.crop, placed.sheet, life) if placed.sheet else np.array(material.crop, "f4")
        )
        rows[..., 19] = material.view_aligned if material.view_aligned in (FIXED, CAMERA, UPRIGHT) else CAMERA
        rows[..., 20] = -rot[None, :, 1]  # the file's turn about the quad's normal, as the game negates it
        out.append((effect.texture_of(index), material.additive, rows.reshape(-1, FLOATS)))
    return out


def collect(
    items: Iterable[tuple[list[Effect], Placed, np.ndarray]], now: float, eye: np.ndarray, budget: int = MAX_QUADS
) -> dict[tuple[str, bool], np.ndarray]:
    """Every quad record of ``items`` (effects, where they are placed, the model's matrices of the instances that
    show them) at ``now`` seconds, by (texture, additive). Quads beyond ``budget`` are dropped from the farthest
    instances of the last items; blended groups are sorted far to near."""
    groups: dict[tuple[str, bool], list[np.ndarray]] = {}
    left = budget
    for effects, placed, mats in items:
        for effect in effects:
            if left <= 0:
                break
            if not effect.looped and not placed.started:
                continue
            sprites = effect.at(now, placed.speed)
            if not len(sprites):
                continue
            fit = max(1, left // len(sprites))
            for texture, additive, rows in records(effect, sprites, placed, mats[:fit]):
                groups.setdefault((texture, additive), []).append(rows)
                left -= len(rows)
    out = {}
    for key, parts in groups.items():
        rows = np.concatenate(parts)
        if not key[1]:
            rows = rows[np.argsort(-np.linalg.norm(rows[:, 0:3] - eye, axis=1))]
        out[key] = np.ascontiguousarray(rows, "f4")
    return out


class ParticleLayer:
    """What the renderer asks of the effects each frame (``draw``), over the model library that loads the files.
    ``batches()`` gives the renderer's batches, ``texture(key, srgb)`` its GL textures."""

    SKIPPED_LAYERS = ("markers", "grass")  # pins and grass carry no effects

    def __init__(self, ctx, library, batches, texture) -> None:
        self.library, self._ctx, self._batches, self._texture = library, ctx, batches, texture
        self.drawer: ParticleDrawer | None = None
        self.wanted = False  # the view has asked for effects at least once
        self.clock = 0.0  # the live clock of the last frame: what "freeze" holds
        self.quads = 0

    def pending(self) -> int:
        """Effect files and textures still loading (zero until the view asks for effects)."""
        return self.library.effects_pending() if self.wanted and self.library is not None else 0

    def draw(self, opts, camera, view_proj: np.ndarray, planes, now: float) -> bool:
        """Draw the effects of the models ``opts`` shows, at ``opts.particle_time`` or the live clock ``now``;
        True while the picture keeps moving."""
        if not opts.particles or self.library is None:
            return False
        self.wanted, self.clock = True, now
        eye = camera.eye()
        items = []
        for b in self._batches():
            layer, cfg = b.key
            if not b.count or b.cells is None or not cfg or layer in self.SKIPPED_LAYERS:
                continue
            if not opts.layers.get(layer, True):
                continue
            self.library.request_effects(cfg)
            placed = self.library.placed_effects(cfg)
            mats = b.cells.matrices
            near = pick_instances(mats, eye, planes) if placed else ()
            for p in placed if len(near) else ():
                effects = self.library.effect(p.file)
                if effects:
                    items.append((effects, self._flipbook(p, effects), mats[near]))
        still = opts.particle_time is not None
        quality = getattr(opts, "quality", None)  # its budget (``render.quality``), else ours
        groups = collect(items, opts.particle_time if still else now, eye, quality.particles if quality else MAX_QUADS)
        if self.drawer is None:
            self.drawer = ParticleDrawer(self._ctx)
        self.drawer.draw(groups, lambda name: self._texture("fx:" + name, False), camera.view(), view_proj, eye)
        self.quads = self.drawer.quads
        return bool(groups) and not still

    def _flipbook(self, placed: Placed, effects: list[Effect]) -> Placed:
        """``placed``, with the frames of a movie texture laid side by side (wider than high) when its config gives none."""
        name = effects[0].texture_of(0) if effects[0].materials[0].movie_texture else ""
        size = self.library.texture("fx:" + name) if name and placed.sheet is None else None
        if not size or size[0] < 2 * size[1]:
            return placed
        frames = round(size[0] / size[1])
        return replace(placed, sheet=(frames, 1, frames))

    def release(self) -> None:
        if self.drawer is not None:
            self.drawer.release()


class ParticleDrawer:
    """The GL side: one program, a dynamic buffer per group, quads expanded in the vertex shader."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.program = ctx.program(vertex_shader=VERTEX, fragment_shader=FRAGMENT)
        self._buffers: list = []
        self.quads = 0

    def _vao(self, k: int, rows: int):
        while len(self._buffers) <= k:
            self._buffers.append(None)
        have = self._buffers[k]
        if have is None or have[0].size < rows * FLOATS * 4:
            if have is not None:
                have[0].release()
                have[1].release()
            buf = self.ctx.buffer(reserve=max(rows, 1024) * FLOATS * 4, dynamic=True)
            fmt = "3f 3f 3f 2f 4f 4f 2f/i"
            vao = self.ctx.vertex_array(
                self.program,
                [(buf, fmt, "center", "axisX", "axisY", "size", "color", "tile", "modeSpin")],
                skip_errors=True,
            )
            self._buffers[k] = have = (buf, vao)
        return have

    def draw(self, groups: dict[tuple[str, bool], np.ndarray], texture, view: np.ndarray, view_proj: np.ndarray,
             eye: np.ndarray) -> None:  # fmt: skip
        """Draw ``groups``; ``texture(name)`` gives a moderngl texture or None (still loading: skipped)."""
        ctx, prog = self.ctx, self.program
        prog["viewProj"].write(gl(view_proj))
        prog["camRight"].value = tuple(float(v) for v in view[0, :3])
        prog["camUp"].value = tuple(float(v) for v in view[1, :3])
        prog["eye"].value = tuple(float(v) for v in eye)
        prog["atlas"].value = 0
        ctx.enable(ctx.BLEND)
        self.quads = 0
        for k, ((name, additive), rows) in enumerate(sorted(groups.items(), key=lambda g: g[0][1])):
            tex = texture(name)
            if tex is None or not len(rows):
                continue
            ctx.blend_func = (ctx.SRC_ALPHA, ctx.ONE) if additive else (ctx.SRC_ALPHA, ctx.ONE_MINUS_SRC_ALPHA)
            buf, vao = self._vao(k, len(rows))
            buf.write(rows.tobytes())
            tex.use(0)
            vao.render(ctx.TRIANGLE_STRIP, vertices=4, instances=len(rows))
            self.quads += len(rows)

    def release(self) -> None:
        for have in self._buffers:
            if have is not None:
                have[0].release()
                have[1].release()
        self._buffers = []
        self.program.release()
