"""Anno 117's sun shadow on QRhi: four cascades, one shadow map holding them as quarters, the models drawn into it by
their effects' own shadow technique (``ModelObj2Shadow``: ``vsShadow`` / ``psShadow``), and what the engine's shadow
code reads: ``cbShadow`` by name, ``cShadowInformation`` as a raw buffer (``INFO_BYTES`` a cascade).

The cascades come from the game's own analysis on the GPU (``shadowfit``) or, as its fallback, from ``fit(camera,
box, sun)`` on the CPU (the scene's bounds, the same split rule), written by ``info_bytes``; ``fill(c, cascades)``
writes ``cbShadow``; ``ShadowMap(ctx)`` is the pass. docs/rendering/frame-117-viewer.md section 2.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np

from . import gameshader as gs
from .gameinstances import culled_words
from .nativepass import draw_models

COUNT = 4  # SHADOW_MAP_CASCADE_COUNT
SIZE = 4096  # the whole map; each cascade a quarter (the engine's size is a setting, not read)
MIN_DEPTH = 0.5  # the analysis clamps the nearest view depth to at least this (camera.md 4)
# cShadowPartitionDivision: each cascade's quarter of the map (offset xy, size zw), in 0..1
DIVISIONS = ((0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 0.5, 0.5), (0.0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5))
# ShadowInformation as a structured buffer packs it: member -> byte offset (constants_shadow.fxh)
INFO = {"ShadowMatrix": 0, "ShadowMatrixInv": 64, "DepthTransform": 128, "MinLS": 144, "SplitDistance": 156,
        "MaxLS": 160, "DepthRange": 176, "PartitionSize": 192, "PartitionOffset": 200}  # fmt: skip
INFO_BYTES = 208


def poisson_disc(count: int = 16, seed: int = 117) -> np.ndarray:
    """``cShadowSampleOffsets``: Poisson-disc offsets in -1..1 (the engine's own table is not read: ours, fixed)."""
    rng = np.random.default_rng(seed)
    points: list[np.ndarray] = []
    radius = 0.5
    while len(points) < count:
        for _ in range(2000):
            p = rng.uniform(-1, 1, 2)
            if p @ p <= 1 and all(np.linalg.norm(p - q) >= radius for q in points):
                points.append(p)
                break
        else:
            radius *= 0.9
    return np.asarray(points[:count])


SAMPLES = poisson_disc()


@dataclass(frozen=True)
class Cascade:
    """One cascade as ``ShadowInformation`` holds it: ``matrix`` world -> its clip xy (row vectors), the light-space
    depth axis and range, its light-space box and where it ends in view depth."""

    matrix: np.ndarray
    depth_axis: np.ndarray
    depth_range: tuple[float, float]
    lo: np.ndarray
    hi: np.ndarray
    split: float


@dataclass(frozen=True)
class Cascades:
    """The cascades of a frame: ``cascades`` fitted on the CPU, or none when the game's analysis fits them on the
    GPU (``gpu``) into the information buffer."""

    cascades: tuple
    light: np.ndarray  # cLgtDirection: the way the light travels
    max_distance: float
    gpu: bool = False
    size: int = SIZE  # the map's side (``cShadowMapSize``: the engine's caster culling reads it)
    used: int = COUNT  # the cascades drawn and read (``cUsedShadowPartitions``, the quality's): shadows end sooner


def _corners(lo, hi) -> np.ndarray:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return np.array([[(lo, hi)[i >> k & 1][k] for k in range(3)] for i in range(8)])


def _slice(camera: gs.Camera, z0: float, z1: float) -> np.ndarray:
    """The world corners of the view frustum between view depths ``z0`` and ``z1`` (the engine's view: +z ahead)."""
    p = camera.proj
    sx, sy = 1.0 / abs(p[0, 0]), 1.0 / abs(p[1, 1])
    view = np.array([[x * z * sx, y * z * sy, z, 1.0] for z in (z0, z1) for x in (-1, 1) for y in (-1, 1)])
    return (view @ np.linalg.inv(camera.view))[:, :3]


def light_basis(sun) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(right, up, travel) of the sun's light: ``sun`` points towards the sun."""
    d = gs.light_travel(sun)
    up = np.array([0.0, 1.0, 0.0]) if abs(d[1]) < 0.99 else np.array([0.0, 0.0, 1.0])
    r = np.cross(up, d)
    r /= np.linalg.norm(r)
    return r, np.cross(d, r), d


def fit(camera: gs.Camera, box: tuple, sun, count: int = COUNT) -> Cascades:
    """The cascades for ``camera`` over the scene ``box`` (world lo, hi): the view depth the box covers, split
    logarithmically as the engine splits it, each slice's light-space box clipped to the scene's."""
    corners = _corners(*box)
    hom = np.c_[corners, np.ones(8)]
    zs = (hom @ camera.view)[:, 2]
    near = max(MIN_DEPTH, float(zs.min()), camera.near)
    far = max(near * 1.01, float(zs.max()))
    r, u, d = light_basis(sun)
    scene_ls = np.c_[corners @ r, corners @ u]
    depth = corners @ d
    margin = 0.01 * max(1e-3, float(depth.max() - depth.min())) + 0.05
    d0, d1 = float(depth.min()) - margin, float(depth.max()) + margin
    out, start = [], near
    for i in range(count):
        end = near * (far / near) ** ((i + 1) / count)
        part = _slice(camera, start, end)
        ls = np.c_[part @ r, part @ u]
        lo = np.maximum(ls.min(0), scene_ls.min(0))
        hi = np.minimum(ls.max(0), scene_ls.max(0))
        if np.any(hi <= lo):
            lo, hi = scene_ls.min(0), scene_ls.max(0)
        pad = 0.02 * (hi - lo) + 1e-3
        lo, hi = lo - pad, hi + pad
        c, h = (lo + hi) / 2, (hi - lo) / 2
        m = np.zeros((4, 4))
        m[:3, 0], m[3, 0] = r / h[0], -c[0] / h[0]
        m[:3, 1], m[3, 1] = u / h[1], -c[1] / h[1]
        m[:3, 2], m[3, 2] = d / (d1 - d0), -d0 / (d1 - d0)
        m[3, 3] = 1.0
        out.append(Cascade(m, d, (d0, d1), np.array([*lo, d0]), np.array([*hi, d1]), end))
        start = end
    return Cascades(tuple(out), d, far)


def fill(c: gs.Constants, cascades: Cascades | None) -> None:
    """``cbShadow`` by name (``None``: no cascade used, everything lit)."""
    if cascades is None:
        c.set("cUsedShadowPartitions", 0)
        return
    c.set("cUsedShadowPartitions", cascades.used)
    c.set("cLastFrameBlend", 0.0)
    c.set("cShadowMapSize", [cascades.size, cascades.size])
    c.set("cShadowMaxDistance", cascades.max_distance)
    c.set("cShadowVolumetricCascade", 0)
    c.set_rows("cShadowSampleOffsets", [[x, y, 0, 0] for x, y in SAMPLES])
    c.set_rows("cShadowPartitionDivision", DIVISIONS)


def info_bytes(cascades: Cascades) -> bytes:
    """``cShadowInformation`` of CPU-fitted cascades as the shaders load it (matrices by columns, as the engine's
    column-major default stores them)."""
    out = bytearray(INFO_BYTES * COUNT)

    def put(i: int, member: str, values) -> None:
        raw = np.asarray(values, np.float32).tobytes()
        at = i * INFO_BYTES + INFO[member]
        out[at : at + len(raw)] = raw

    for i, k in enumerate(cascades.cascades):
        put(i, "ShadowMatrix", k.matrix.T)
        put(i, "ShadowMatrixInv", np.linalg.inv(k.matrix).T)
        put(i, "DepthTransform", [*k.depth_axis, 0.0])
        put(i, "MinLS", k.lo)
        put(i, "SplitDistance", [k.split])
        put(i, "MaxLS", k.hi)
        put(i, "DepthRange", k.depth_range)
        put(i, "PartitionSize", DIVISIONS[i][2:])
        put(i, "PartitionOffset", DIVISIONS[i][:2])
    return bytes(out)


def fill_casters(c: gs.Constants, cascades: Cascades, instances: int = 1, joints: int = 0, words=None) -> None:
    """The shadow pass's per-draw data: each of ``instances`` once per cascade, the cascade's index in bits 30-31 of
    the per-frame word (what the engine's culling writes into ``cModelGPUCulledInstanceData``), alpha 15/15
    (``gameinstances.culled_words``; ``words``: a caster's own, culled per cascade); the shared part worked out once
    per layout and frame (``gs.replay``)."""

    def write(c: gs.Constants) -> None:
        fill(c, cascades)
        c.set("cLgtDirection", [*cascades.light, 0.0])
        c.set("cModelPerFrameDataOffset", 0)

    gs.replay(c, "casters", (cascades,), write)
    if words is not None:
        c.set_elements("cModelGPUCulledInstanceData", words)
        return
    words = culled_words(instances, COUNT, joints)
    gs.replay(c, f"casters:{instances}:{joints}", (), lambda c: c.set_elements("cModelGPUCulledInstanceData", words))


def read_info(raw: bytes, light, max_distance: float) -> Cascades:
    """Cascades as ``cShadowInformation`` holds them (``info_bytes``'s layout; the game's analysis writes the same):
    each one's matrix, depth axis and range, light-space box and split."""
    out = []
    for i in range(COUNT):
        cell = np.frombuffer(raw, np.float32, INFO_BYTES // 4, i * INFO_BYTES)

        def at(member: str, n: int, cell=cell) -> np.ndarray:
            k = INFO[member] // 4
            return cell[k : k + n].astype(float)

        matrix = at("ShadowMatrix", 16).reshape(4, 4).T  # stored by columns
        out.append(Cascade(matrix, at("DepthTransform", 3), tuple(at("DepthRange", 2)), at("MinLS", 3),
                           at("MaxLS", 3), float(at("SplitDistance", 1)[0])))  # fmt: skip
    return Cascades(tuple(out), np.asarray(light, float), max_distance)


def cascade_key(cascades: Cascades) -> tuple:
    """What a frame's cascades are (every field of each, the light, the distance, the map's size): equal for a still
    camera's frames."""

    def plain(v):
        return np.asarray(v).tobytes() if isinstance(v, np.ndarray) else v

    each = tuple(tuple(plain(getattr(k, f.name)) for f in fields(k)) for k in cascades.cascades)
    light = np.asarray(cascades.light).tobytes()
    return each, light, float(cascades.max_distance), cascades.gpu, cascades.size, cascades.used


def cascade_mask(cascades: Cascades, centres: np.ndarray, radii: np.ndarray, margin: float = 0.05,
                 texels: int = 0, used: int = COUNT) -> np.ndarray:  # fmt: skip
    """[n, COUNT]: whether a bounding sphere reaches into each of the first ``used`` cascades' squares (seen along the
    light; ``margin`` of the square around it, as cascades read back a frame or two late move a little) and, with
    ``texels`` (a cascade's side), is at least a texel across there (a smaller caster leaves no shadow the shading
    resolves: on a continental island the far cascades' grass and bushes took most of the shadow pass) [ours: the
    engine culls on the GPU]."""
    found = cascades.cascades[: min(COUNT, used)]
    xy = np.concatenate([k.matrix[:, :2] for k in found], axis=1)  # [4, 2 per cascade]
    clip = np.abs(centres @ xy[:3] + xy[3])
    scale = np.sqrt((xy[:3] ** 2).sum(axis=0))[None, :]  # clip units per world unit, per cascade and axis
    reach = radii[:, None] * scale + (1.0 + margin)
    inside = clip <= reach
    if texels:
        inside &= radii[:, None] * scale * texels >= 1.0  # a diameter of a texel at least (the square: 2 clip units)
    inside = inside.reshape(len(centres), len(found), 2).all(axis=2)
    out = np.zeros((len(centres), COUNT), bool)
    out[:, : len(found)] = inside
    return out


class ShadowMap:
    """The map (a depth texture, cleared to 0: the engine's shadow depth is ``1 - distance`` to the light) and its
    pass: ``render(casters, camera, cascades, info)`` records it (``info``: the ``cShadowInformation`` buffer);
    ``texture`` is what the shading samples; ``texture`` given: a depth target of the frame's own (with stencil: the
    cut-out volumes' count, ``gameframe.GameFrame``)."""

    def __init__(self, ctx, size: int = SIZE, texture=None) -> None:
        self.ctx, self.size = ctx, size
        self.texture = texture if texture is not None else ctx.depth_renderbuffer((size, size))
        self.fbo = ctx.framebuffer([], self.texture)

    def render(self, casters: list, camera: gs.Camera, cascades: Cascades, info, ground=None, external=None) -> None:
        """The casters into the cascades; ``ground`` (``gameframe.Ground``): the terrain the bent models read;
        ``external(cb)``: the native backend's casters, recorded first (``nativepass.draw_pass``)."""
        size = (self.size, self.size)
        shared = {"cShadowInformation": info, **(ground.maps if ground is not None else {})}
        terrain = ground.fill() if ground is not None else None

        def before(batch) -> None:
            if not cascades.gpu:  # fitted on the CPU: the information goes up from here
                batch.uploadStaticBuffer(info, info_bytes(cascades))

        seen = cascade_key(cascades)

        def prepare(m, rp, signature, batch) -> None:
            n, joints = getattr(m, "instances", 1), getattr(m, "joints", 0)  # an instanced caster: many a draw
            words = getattr(m, "words", None)  # culled per cascade: its own words

            def fill(c) -> None:
                fill_casters(c, cascades, n, joints, words)
                if terrain is not None:
                    terrain(c)

            fill.key = (seen, n, joints, id(words), terrain and terrain.key)  # what it writes (a still frame skips it)
            fill.common_key = (seen, terrain and terrain.key)  # what it writes into the buffers without instance data
            m.prepare(rp, signature, batch, camera, fill, shared)

        def draw(m, cb) -> None:
            words = getattr(m, "words", None)
            m.draw(cb, size, len(words) if words is not None else COUNT * getattr(m, "instances", 1))

        draw_models(self.ctx, self.fbo, casters, prepare, draw, before=before, targets=0, depth=0.0, external=external)

    def release(self) -> None:
        self.fbo.release()
        self.texture.release()
