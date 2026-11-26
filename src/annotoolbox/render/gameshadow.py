"""Anno 117's sun shadow on QRhi: four cascades, one shadow map holding them as quarters, the models drawn into it by
their effects' own shadow technique (``ModelObj2Shadow``: ``vsShadow`` / ``psShadow``), and what the engine's shadow
code reads: ``cbShadow`` by name, ``cShadowInformation`` as a raw buffer (``INFO_BYTES`` a cascade).

The cascades come from the game's own analysis on the GPU (``shadowfit``) or, as its fallback, from ``fit(camera,
box, sun)`` on the CPU (the scene's bounds, the same split rule), written by ``info_bytes``; ``fill(c, cascades)``
writes ``cbShadow``; ``CShadowMap(ctx)`` is the pass. docs/rendering/frame-117-viewer.md section 2.
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
# the members ``fill`` writes, and with them those ``fill_casters`` writes: a buffer with none of them reads no
# cascade (``gamedraw.CGameModel`` fills it again only for its other inputs)
SHADOW_NAMES = frozenset({"cUsedShadowPartitions", "cLastFrameBlend", "cShadowMapSize", "cShadowMaxDistance",
                          "cShadowVolumetricCascade", "cShadowSampleOffsets", "cShadowPartitionDivision"})  # fmt: skip
CASTER_NAMES = SHADOW_NAMES | {"cLgtDirection", "cModelPerFrameDataOffset", "cModelGPUCulledInstanceData"}


def poisson_disc(count: int = 16, seed: int = 117) -> np.ndarray:
    """``cShadowSampleOffsets``: Poisson-disc offsets in -1..1 (the engine's own table is not read: ours, fixed)."""
    rng = np.random.default_rng(seed)
    points: list[np.ndarray] = []
    min_spacing = 0.5
    while len(points) < count:
        for _ in range(2000):
            point = rng.uniform(-1, 1, 2)
            if point @ point <= 1 and all(np.linalg.norm(point - placed) >= min_spacing for placed in points):
                points.append(point)
                break
        else:
            min_spacing *= 0.9
    return np.asarray(points[:count])


SAMPLES = poisson_disc()


@dataclass(frozen=True)
class SCascade:
    """One cascade as ``ShadowInformation`` holds it: ``matrix`` world -> its clip xy (row vectors), the light-space
    depth axis and range, its light-space box and where it ends in view depth."""

    matrix: np.ndarray
    depth_axis: np.ndarray
    depth_range: tuple[float, float]
    min_corner: np.ndarray
    max_corner: np.ndarray
    split: float


@dataclass(frozen=True)
class SCascades:
    """The cascades of a frame: ``cascades`` fitted on the CPU, or none when the game's analysis fits them on the
    GPU (``gpu``) into the information buffer."""

    cascades: tuple
    light: np.ndarray  # cLgtDirection: the way the light travels
    max_distance_m: float
    gpu: bool = False
    size: int = SIZE  # the map's side (``cShadowMapSize``: the engine's caster culling reads it)
    used: int = COUNT  # the cascades drawn and read (``cUsedShadowPartitions``, the quality's): shadows end sooner


def _corners(min_corner, max_corner) -> np.ndarray:
    min_corner, max_corner = np.asarray(min_corner, float), np.asarray(max_corner, float)
    return np.array([[(min_corner, max_corner)[i >> k & 1][k] for k in range(3)] for i in range(8)])


def _slice(camera: gs.SCamera, z0: float, z1: float) -> np.ndarray:
    """The world corners of the view frustum between view depths ``z0`` and ``z1`` (the engine's view: +z ahead)."""
    projection = camera.projection
    sx, sy = 1.0 / abs(projection[0, 0]), 1.0 / abs(projection[1, 1])
    view = np.array([[x * z * sx, y * z * sy, z, 1.0] for z in (z0, z1) for x in (-1, 1) for y in (-1, 1)])
    return (view @ np.linalg.inv(camera.view))[:, :3]


def light_basis(sun) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(right, up, travel) of the sun's light: ``sun`` points towards the sun."""
    travel = gs.light_travel(sun)
    up = np.array([0.0, 1.0, 0.0]) if abs(travel[1]) < 0.99 else np.array([0.0, 0.0, 1.0])
    right = np.cross(up, travel)
    right /= np.linalg.norm(right)
    return right, np.cross(travel, right), travel


def fit(camera: gs.SCamera, box: tuple, sun, count: int = COUNT) -> SCascades:
    """The cascades for ``camera`` over the scene ``box`` (world lo, hi): the view depth the box covers, split
    logarithmically as the engine splits it, each slice's light-space box clipped to the scene's."""
    corners = _corners(*box)
    hom = np.c_[corners, np.ones(8)]
    view_depths = (hom @ camera.view)[:, 2]
    near = max(MIN_DEPTH, float(view_depths.min()), camera.near)
    far = max(near * 1.01, float(view_depths.max()))
    right, up, travel = light_basis(sun)
    scene_ls = np.c_[corners @ right, corners @ up]
    depth = corners @ travel
    margin = 0.01 * max(1e-3, float(depth.max() - depth.min())) + 0.05
    min_depth, max_depth = float(depth.min()) - margin, float(depth.max()) + margin
    out, start = [], near
    for i in range(count):
        end = near * (far / near) ** ((i + 1) / count)
        part = _slice(camera, start, end)
        light_space = np.c_[part @ right, part @ up]
        min_corner = np.maximum(light_space.min(0), scene_ls.min(0))
        max_corner = np.minimum(light_space.max(0), scene_ls.max(0))
        if np.any(max_corner <= min_corner):
            min_corner, max_corner = scene_ls.min(0), scene_ls.max(0)
        pad = 0.02 * (max_corner - min_corner) + 1e-3
        min_corner, max_corner = min_corner - pad, max_corner + pad
        centre, half_size = (min_corner + max_corner) / 2, (max_corner - min_corner) / 2
        matrix = np.zeros((4, 4))
        matrix[:3, 0], matrix[3, 0] = right / half_size[0], -centre[0] / half_size[0]
        matrix[:3, 1], matrix[3, 1] = up / half_size[1], -centre[1] / half_size[1]
        matrix[:3, 2], matrix[3, 2] = travel / (max_depth - min_depth), -min_depth / (max_depth - min_depth)
        matrix[3, 3] = 1.0
        out.append(
            SCascade(
                matrix,
                travel,
                (min_depth, max_depth),
                np.array([*min_corner, min_depth]),
                np.array([*max_corner, max_depth]),
                end,
            )
        )
        start = end
    return SCascades(tuple(out), travel, far)


def fill(constants: gs.CConstants, cascades: SCascades | None) -> None:
    """``cbShadow`` by name (``None``: no cascade used, everything lit)."""
    if cascades is None:
        constants.set("cUsedShadowPartitions", 0)
        return
    constants.set("cUsedShadowPartitions", cascades.used)
    constants.set("cLastFrameBlend", 0.0)
    constants.set("cShadowMapSize", [cascades.size, cascades.size])
    constants.set("cShadowMaxDistance", cascades.max_distance_m)
    constants.set("cShadowVolumetricCascade", 0)
    constants.set_rows("cShadowSampleOffsets", [[x, y, 0, 0] for x, y in SAMPLES])
    constants.set_rows("cShadowPartitionDivision", DIVISIONS)


def fill_key(cascades: SCascades | None) -> tuple:
    """What ``fill`` writes of ``cascades``, comparable by content (a keyed fill's part: ``gamebind.keyed``)."""
    return () if cascades is None else (cascades.used, cascades.size, float(cascades.max_distance_m))


def info_bytes(cascades: SCascades) -> bytes:
    """``cShadowInformation`` of CPU-fitted cascades as the shaders load it (matrices by columns, as the engine's
    column-major default stores them)."""
    out = bytearray(INFO_BYTES * COUNT)

    def put(i: int, member: str, values) -> None:
        raw = np.asarray(values, np.float32).tobytes()
        offset = i * INFO_BYTES + INFO[member]
        out[offset : offset + len(raw)] = raw

    for i, cascade in enumerate(cascades.cascades):
        put(i, "ShadowMatrix", cascade.matrix.T)
        put(i, "ShadowMatrixInv", np.linalg.inv(cascade.matrix).T)
        put(i, "DepthTransform", [*cascade.depth_axis, 0.0])
        put(i, "MinLS", cascade.min_corner)
        put(i, "SplitDistance", [cascade.split])
        put(i, "MaxLS", cascade.max_corner)
        put(i, "DepthRange", cascade.depth_range)
        put(i, "PartitionSize", DIVISIONS[i][2:])
        put(i, "PartitionOffset", DIVISIONS[i][:2])
    return bytes(out)


def fill_casters(
    constants: gs.CConstants, cascades: SCascades, instances: int = 1, joints: int = 0, words=None
) -> None:
    """The shadow pass's per-draw data: each of ``instances`` once per cascade, the cascade's index in bits 30-31 of
    the per-frame word (what the engine's culling writes into ``cModelGPUCulledInstanceData``), alpha 15/15
    (``gameinstances.culled_words``; ``words``: a caster's own, culled per cascade); the shared part worked out once
    per layout and frame (``gs.replay``)."""

    def write(constants: gs.CConstants) -> None:
        fill(constants, cascades)
        constants.set("cLgtDirection", [*cascades.light, 0.0])
        constants.set("cModelPerFrameDataOffset", 0)

    gs.replay(constants, "casters", (cascades,), write)
    if words is not None:
        constants.set_elements("cModelGPUCulledInstanceData", words)
        return
    words = culled_words(instances, COUNT, joints)
    gs.replay(
        constants,
        f"casters:{instances}:{joints}",
        (),
        lambda constants: constants.set_elements("cModelGPUCulledInstanceData", words),
    )


def read_info(raw: bytes, light, max_distance_m: float) -> SCascades:
    """Cascades as ``cShadowInformation`` holds them (``info_bytes``'s layout; the game's analysis writes the same):
    each one's matrix, depth axis and range, light-space box and split."""
    out = []
    for i in range(COUNT):
        cell = np.frombuffer(raw, np.float32, INFO_BYTES // 4, i * INFO_BYTES)

        def member_floats(member: str, count: int, cell=cell) -> np.ndarray:
            start = INFO[member] // 4
            return cell[start : start + count].astype(float)

        matrix = member_floats("ShadowMatrix", 16).reshape(4, 4).T  # stored by columns
        out.append(SCascade(matrix, member_floats("DepthTransform", 3), tuple(member_floats("DepthRange", 2)), member_floats("MinLS", 3),
                           member_floats("MaxLS", 3), float(member_floats("SplitDistance", 1)[0])))  # fmt: skip
    return SCascades(tuple(out), np.asarray(light, float), max_distance_m)


def cascade_key(cascades: SCascades) -> tuple:
    """What a frame's cascades are (every field of each, the light, the distance, the map's size): equal for a still
    camera's frames."""

    def plain(value):
        return np.asarray(value).tobytes() if isinstance(value, np.ndarray) else value

    each = tuple(
        tuple(plain(getattr(cascade, data_field.name)) for data_field in fields(cascade))
        for cascade in cascades.cascades
    )
    light = np.asarray(cascades.light).tobytes()
    return each, light, float(cascades.max_distance_m), cascades.gpu, cascades.size, cascades.used


def cascade_mask(cascades: SCascades, centres: np.ndarray, radii: np.ndarray, margin: float = 0.05,
                 texels: int = 0, used: int = COUNT) -> np.ndarray:  # fmt: skip
    """[n, COUNT]: whether a bounding sphere reaches into each of the first ``used`` cascades' squares (seen along the
    light; ``margin`` of the square around it, as cascades read back a frame or two late move a little) and, with
    ``texels`` (a cascade's side), is at least a texel across there (a smaller caster leaves no shadow the shading
    resolves: on a continental island the far cascades' grass and bushes took most of the shadow pass) [ours: the
    engine culls on the GPU]."""
    found = cascades.cascades[: min(COUNT, used)]
    clip_axes = np.concatenate([cascade.matrix[:, :2] for cascade in found], axis=1)  # [4, 2 per cascade]
    clip = np.abs(centres @ clip_axes[:3] + clip_axes[3])
    scale = np.sqrt((clip_axes[:3] ** 2).sum(axis=0))[None, :]  # clip units per world unit, per cascade and axis
    reach = radii[:, None] * scale + (1.0 + margin)
    inside = clip <= reach
    if texels:
        inside &= radii[:, None] * scale * texels >= 1.0  # a diameter of a texel at least (the square: 2 clip units)
    inside = inside.reshape(len(centres), len(found), 2).all(axis=2)
    out = np.zeros((len(centres), COUNT), bool)
    out[:, : len(found)] = inside
    return out


class CShadowMap:
    """The map (a depth texture, cleared to 0: the engine's shadow depth is ``1 - distance`` to the light) and its
    pass: ``render(casters, camera, cascades, shadow_info_buffer)`` records it (``shadow_info_buffer``: the ``cShadowInformation`` buffer);
    ``texture`` is what the shading samples; ``texture`` given: a depth target of the frame's own (with stencil: the
    cut-out volumes' count, ``gameframe.CGameFrame``)."""

    def __init__(self, ctx, size_px: int = SIZE, texture=None) -> None:
        self.m_ctx, self.m_size_px = ctx, size_px
        self.m_texture = texture if texture is not None else ctx.depth_renderbuffer((size_px, size_px))
        self.m_fbo = ctx.framebuffer([], self.m_texture)

    def render(self, casters: list, camera: gs.SCamera, cascades: SCascades, shadow_info_buffer, ground=None, external=None,
               after=None) -> None:  # fmt: skip
        """The casters into the cascades; ``ground`` (``gameframe.SGround``): the terrain the bent models read;
        ``external(command_buffer)`` / ``after(command_buffer)``: the native backend's casters, recorded first / last (``draw_pass``)."""
        size_px = (self.m_size_px, self.m_size_px)
        shared = {"cShadowInformation": shadow_info_buffer, **(ground.maps if ground is not None else {})}
        terrain = ground.fill() if ground is not None else None

        def before(batch) -> None:
            if not cascades.gpu:  # fitted on the CPU: the information goes up from here
                batch.uploadStaticBuffer(shadow_info_buffer, info_bytes(cascades))

        seen = cascade_key(cascades)

        def prepare(model, render_pass, signature, batch) -> None:
            instance_count = getattr(model, "m_instances", 1)  # an instanced caster: many a draw
            joints = getattr(model, "joints", 0)
            words = getattr(model, "m_words", None)  # culled per cascade: its own words

            def fill(constants) -> None:
                fill_casters(constants, cascades, instance_count, joints, words)
                if terrain is not None:
                    terrain(constants)

            # what it writes, by content (a still frame skips it); the words by their bytes, not an id that a new
            # array can take over
            terrain_key = terrain and terrain.key
            fill.key = (seen, instance_count, joints, gs.value_key(words), terrain_key)
            fill.common_key = (seen, terrain_key)  # what it writes into the buffers without instance data
            fill.names = CASTER_NAMES | terrain.names if terrain is not None else CASTER_NAMES  # the members it writes
            model.prepare(render_pass, signature, batch, camera, fill, shared)

        def draw(model, command_buffer) -> None:
            words = getattr(model, "m_words", None)
            model.draw(
                command_buffer, size_px, len(words) if words is not None else COUNT * getattr(model, "m_instances", 1)
            )

        draw_models(self.m_ctx, self.m_fbo, casters, prepare, draw, before=before, targets=0, depth=0.0, external=external,
                    after=after)  # fmt: skip

    def release(self) -> None:
        self.m_fbo.release()
        self.m_texture.release()
