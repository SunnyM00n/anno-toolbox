"""The ground of a built 117 island: the ground materials of 2070 laid over the island, the hand-painted
strokes, the coverage and the beach."""

from __future__ import annotations

import math
import struct
import warnings

from ..formats import filedb
from .islandterrain import bilinear, replace_dds_pixels, set_bytes
from .riverbed import lay_river_bed

BASE_TEXTURE = 0  # the 2070 ground texture every chunk lays under everything else at full alpha
# (n_water, the sea floor): what the layers' paint curve leaves of a texel shows it, as in 2070 (island-build "The ground")

ALPHA_BAND_TEXELS = 1 << 19  # texels encoded at once: the [6, texels] float arrays of a band take about 200 MB

MATERIAL_BLEND = 0.5  # tiles: how far ground textures fade into each other. 2070 paints one alpha
# value per tile (117 paints 8 per tile), which came out as hard tile-sized patches; the alphas are
# sampled bilinearly, so this only takes the last of the edge off. Measured against 2070's own
# ground (scripts/island_compare.py): 2070 changes texture between neighbouring tiles on 12.6% of
# n_l03, a blend of 0.5 keeps 8.7% and 83% of the tiles on 2070's texture, 2.0 leaves 4.9% and
# 74% - a blurred island is a featureless one, and the fading belongs to AlphaSharpness in-engine


# Stage 1 of the terrain materials: 2070 ground texture GUIDs (data/config/engine/GroundTextures.xml)
# -> materials of the vanilla Roman material set (roman_materialset_02.xml), until the 2070 textures
# are ported as an own set. Grass/bush object layers (94-104) are not textures.
ROMAN_MATERIAL_OF = {
    0: 19,  # water (sea bed) -> sand_01
    77: 7,
    78: 11,
    79: 6,
    83: 3,
    93: 3,  # beach_00..04 -> beach_04, beach_05, beach_03, beach_02
    75: 1,
    52: 13,
    71: 5,
    72: 22,
    29: 1,  # gras_00, gras_01..03, gras_detail -> grass variants
    18: 24,
    76: 0,  # earth_01, earth_02 -> dirt_01, mud_01
    74: 14,
    80: 10,
    86: 2,
    87: 4,
    82: 15,
    85: 9,
    81: 2,
    84: 15,  # mountains -> cliffs, rock
    5: 9,
    61: 9,
    10: 19,
    15: 19,
    108: 19,
    21: 20,
    41: 20,
    27: 9,
    105: 9,  # underwater set
}


def stroke_material(stroke: dict) -> int:
    """The material ID a painted stroke names; -1 (no material, so the stroke is left out and reported as one of a
    material outside the set) where a hand-edited file holds none or no number."""
    try:
        return int(stroke.get("material", -1))
    except (TypeError, ValueError, OverflowError):
        return -1


def painted_layers(weights, material_ids: list[int], strokes: list[dict]):
    """An empty weight layer for each painted material (0-31) the 2070 layers do not use: a set
    material the island had no texture for, or a 117 one the editor added to the island's set."""
    import numpy as np

    new_ids = sorted({stroke_material(stroke) for stroke in strokes} - set(material_ids))
    new_ids = [material_id for material_id in new_ids if 0 <= material_id < 32]
    if not new_ids:
        return material_ids, weights
    return [*material_ids, *new_ids], np.concatenate(
        [weights, np.zeros((len(new_ids), *weights.shape[1:]), weights.dtype)]
    )


def paint_strokes(weights, material_ids: list[int], strokes: list[dict], texels_per_tile: int):
    """Hand-painted ground over the composited weights: each stroke (``material``, ``radius`` and
    ``points`` in tiles, ``strength``) lays its material over what is there with a soft round brush,
    in the order the strokes were painted. A material outside the island's set is skipped and reported."""

    index = {material_id: slot for slot, material_id in enumerate(material_ids)}
    skipped: set[int] = set()
    for stroke in strokes:
        slot = index.get(stroke_material(stroke))
        if slot is None:
            skipped.add(stroke_material(stroke))
            continue
        cover = stroke_cover(stroke, weights.shape[1], texels_per_tile)
        weights *= 1.0 - cover
        weights[slot] += cover
    if skipped:
        warnings.warn(
            f"painted strokes of materials {sorted(skipped)} match nothing: the island's ground has {sorted(material_ids)}",
            RuntimeWarning,
            stacklevel=2,
        )
    return weights


def stroke_dabs(stroke: dict) -> list[tuple[float, float]]:
    """The points a stroke's brush is set down at (tiles): its own, and the line between two of them filled in at a
    quarter radius (a fast drag records points far apart); the stroke's ``radius`` is above 0."""
    radius_tiles = float(stroke.get("radius", 4))
    points = [tuple(map(float, point)) for point in stroke.get("points", [])]
    dabs = points[:1]
    for (from_x, from_z), (to_x, to_z) in zip(points, points[1:], strict=False):
        steps = max(1, int(math.ceil(math.hypot(to_x - from_x, to_z - from_z) / (radius_tiles / 4))))
        dabs += [
            (from_x + (to_x - from_x) * k / steps, from_z + (to_z - from_z) * k / steps) for k in range(1, steps + 1)
        ]
    return dabs


def stroke_cover(stroke: dict, texel_count: int, texels_per_tile: float):
    """How much of each texel (n x n) a painted stroke covers: a soft round brush (``radius`` in
    tiles) along its ``points``, times its ``strength``. The line between two points is filled in
    at a quarter radius: a fast drag records points far apart."""
    import numpy as np

    cover = np.zeros((texel_count, texel_count), np.float32)
    radius_tiles = float(stroke.get("radius", 4))
    if radius_tiles <= 0:
        return cover
    for x, z in stroke_dabs(stroke):
        min_x, max_x = (
            max(0, int((x - radius_tiles) * texels_per_tile)),
            min(texel_count, int((x + radius_tiles) * texels_per_tile) + 1),
        )
        min_z, max_z = (
            max(0, int((z - radius_tiles) * texels_per_tile)),
            min(texel_count, int((z + radius_tiles) * texels_per_tile) + 1),
        )
        if min_x >= max_x or min_z >= max_z:
            continue
        offset_x = (np.arange(min_x, max_x) + 0.5) / texels_per_tile - x
        offset_z = (np.arange(min_z, max_z) + 0.5) / texels_per_tile - z
        distance = np.hypot(offset_x[None, :], offset_z[:, None]) / radius_tiles
        coverage = np.where(distance < 1, 0.5 * (1 + np.cos(np.pi * np.minimum(distance, 1))), 0).astype(np.float32)
        np.maximum(cover[min_z:max_z, min_x:max_x], coverage, out=cover[min_z:max_z, min_x:max_x])
    return cover * float(stroke.get("strength", 1.0))


BEACH_GENTLE, BEACH_STEEP = 0.3, 0.8  # shore slope (world units per tile): a beach below, a cliff above
BEACH_LAND, BEACH_SEA, BEACH_FADE = 4.0, 4.0, 4.0  # tiles of beach on either side of the waterline


def blend_alpha(opacity, sharpness, mean_alpha):
    """The alpha to write for a material so the game covers ``opacity`` with it: the game takes
    smoothstep(sat(s * (a - 1) + (s + 1) * alpha)), ``a`` its diffuse's alpha, ``s`` its
    AlphaSharpness (rendering/terrain-materials 1.3), which an opaque texture amplifies (s + 1)
    times - n_l03's beaches spread over the grass 2070 painted on them (editor, game ground, 2026-10-16).
    Inverted at the texture's mean alpha, so its own alpha still frays the edges; full stays full."""
    import numpy as np

    opacity_clipped = np.clip(opacity, 0.0, 1.0)
    x = 0.5 - np.sin(np.arcsin(np.clip(1 - 2 * opacity_clipped, -1, 1)) / 3)  # smoothstep inverted
    out = np.clip((x - sharpness * (mean_alpha - 1)) / (sharpness + 1), 0.0, 1.0)
    return np.where(opacity_clipped >= 0.999, 1.0, np.where(opacity_clipped <= 0.0, 0.0, out))


def layer_opacities(weights, ids):
    """Per slot the opacity that makes the game's compositing give ``weights`` (slot axis 0, summing
    to 1; ``ids`` -1 where a slot is unused). The game lays each material over the ones before it in
    the material set's list - ours lists them by ID - not in slot order (rendering/terrain-materials
    1.4): from the highest ID down a = w / remaining, and the lowest present one is opaque."""
    import numpy as np

    top_down = np.argsort(-ids, axis=0, kind="stable")
    sorted_weights = np.take_along_axis(weights, top_down, axis=0)
    present = np.take_along_axis(ids, top_down, axis=0) >= 0
    remaining = np.ones(weights.shape[1:])
    slot_opacity = np.zeros_like(sorted_weights, dtype=np.float64)
    for slot in range(len(sorted_weights)):
        slot_opacity[slot] = np.clip(
            np.where(remaining > 1e-6, sorted_weights[slot] / np.maximum(remaining, 1e-6), 0), 0, 1
        )
        remaining = remaining - sorted_weights[slot]
    base = present.cumsum(axis=0) == present.sum(axis=0)  # from the lowest present one on
    slot_opacity = np.where(base & present, 1.0, slot_opacity)
    out = np.zeros_like(slot_opacity)
    np.put_along_axis(out, top_down, slot_opacity, axis=0)
    return out


def slot_weights(slot_ids, slot_alpha, order: dict[int, int]):
    """The visible weight of each slot as the game lays the ground (rendering/terrain-materials 1.2, 1.4), the
    inverse of ``CGroundMixin.encode_materials``: a material's coverage is its alphas summed over the slots (at
    most 1), the materials go down in the material set's list order (``order``: ID -> place, an ID the set lacks
    last), each takes its coverage of what the ones above leave, and the first one listed takes the rest. Both
    arrays are [slots, z, x]; the weights come back in that shape and sum to 1 per texel."""
    import numpy as np

    count = slot_ids.shape[0]
    rank_of_id = np.array([order.get(i, 32 + i) for i in range(32)], np.int64)
    rank = rank_of_id[slot_ids]
    here = slot_alpha > 0
    total = np.stack([np.where(slot_ids == slot_ids[slot], slot_alpha, 0).sum(axis=0) for slot in range(count)])
    cover = np.minimum(total, 1.0)
    # one slot stands for each material present: a material in two slots lowers what lies below it once
    lead = [
        here[slot] & ~np.any([here[other] & (slot_ids[other] == slot_ids[slot]) for other in range(slot)], axis=0)
        for slot in range(count)
    ]
    lead = np.stack(lead) if count else np.zeros_like(here)
    left = np.ones(slot_alpha.shape[1:], np.float32)
    first = np.full(slot_alpha.shape[1:], 1 << 30, np.int64)
    out = np.zeros_like(slot_alpha, dtype=np.float32)
    for slot in range(count):
        above = np.ones_like(left)
        for other in range(count):
            above = above * np.where(lead[other] & (rank[other] > rank[slot]), 1.0 - cover[other], 1.0)
        share = np.where(here[slot], slot_alpha[slot] / np.maximum(total[slot], 1e-9), 0.0)
        out[slot] = cover[slot] * above * share
        left = left * np.where(lead[slot], 1.0 - cover[slot], 1.0)
        first = np.where(here[slot], np.minimum(first, rank[slot]), first)
    for slot in range(count):
        share = np.where(here[slot] & (rank[slot] == first), slot_alpha[slot] / np.maximum(total[slot], 1e-9), 0.0)
        out[slot] += left * share
    nothing = ~here.any(axis=0)
    out[0] = np.where(nothing, 1.0, out[0])
    return out


def beach_map(heights, cell: float):
    """The WaterInfos beach byte per cell from the cells' ground (world units, sea level 0): full on
    gentle shores, none on steep ones, from ``BEACH_LAND`` tiles inland to ``BEACH_SEA`` out, fading
    over the next ``BEACH_FADE``; fitted to vanilla's islands (rendering/water.md, the beach byte)."""
    import numpy as np
    from scipy import ndimage

    slope = ndimage.maximum_filter(np.hypot(*np.gradient(heights, cell)), 3)  # world units per tile
    gentle = np.clip((BEACH_STEEP - slope) / (BEACH_STEEP - BEACH_GENTLE), 0, 1)
    land = heights > 0
    to_sea = ndimage.distance_transform_edt(land) * cell
    to_land = ndimage.distance_transform_edt(~land) * cell
    reach = np.where(land, to_sea, to_land)
    limit = np.where(land, BEACH_LAND, BEACH_SEA)
    band = np.clip(1 - (reach - limit) / BEACH_FADE, 0, 1)
    return np.rint(255 * gentle * band).astype(np.uint8)


def blend_materials(weights, texels_per_tile: int) -> None:
    """A Gaussian of ``MATERIAL_BLEND`` tiles over each material, in place: 2070 paints one alpha per tile, 117
    eight, which came out as hard tile-sized patches."""
    import numpy as np
    from scipy import ndimage

    sigma = MATERIAL_BLEND * texels_per_tile
    for k in range(weights.shape[0]):
        if weights[k].any():  # a material the island does not use stays zero: most of the 31 of the set
            weights[k] = ndimage.gaussian_filter(weights[k], sigma, mode="nearest")
    weights /= np.maximum(weights.sum(axis=0), 1e-6)


def top_slots(block_weights, rows: int):
    """``np.argsort(-bw, axis=0)[:6]`` worked out ``rows`` block rows at a time: each column sorts alone, so the
    slots are the same, without the whole map's index array (eight bytes a material and block: 2.3 GB on a 768
    grid, the island's memory peak; run-state "Memory by need")."""
    import numpy as np

    _material_count, block_rows, block_columns = block_weights.shape
    out = np.empty((min(6, block_weights.shape[0]), block_rows, block_columns), dtype=np.intp)
    for first_row in range(0, block_rows, rows):
        out[:, first_row : first_row + rows] = np.argsort(-block_weights[:, first_row : first_row + rows], axis=0)[:6]
    return out


class CGroundMixin:
    """The methods of ``CIslandBuilder`` that build the ground."""

    # Terrain materials
    def _composite_chunks(self, texels_per_tile: int, material_of: dict[int, int], index: dict[int, int]):
        """The 2070 layers composited per 16x16-tile chunk, each chunk in its own layer order over a full-coverage
        base (a merged island-wide order cannot express chunks that disagree: a beach paints grass over sand, a river
        bed sand over grass). Layer i keeps a_i of what the layers above it leave, through 2070's paint curve; weights
        of textures that map to one material add up. Returns (weights [materials, H, W], what no layer covered)."""
        import numpy as np

        texel_count = self.size * texels_per_tile
        weights = np.zeros((len(index), texel_count, texel_count), dtype=np.float32)
        remaining = np.ones((texel_count, texel_count), dtype=np.float32)
        curves = self.paint_curves
        for chunk_x, chunk_z, tiles_per_chunk, layers in self.island.texture_chunks():
            first_x = (
                chunk_x * tiles_per_chunk + self.pad_x
            ) * texels_per_tile  # texels of this chunk in the padded island
            first_z = (chunk_z * tiles_per_chunk + self.pad_z) * texels_per_tile
            end_x, end_z = (
                min(texel_count, first_x + tiles_per_chunk * texels_per_tile),
                min(texel_count, first_z + tiles_per_chunk * texels_per_tile),
            )
            if end_x <= 0 or end_z <= 0 or first_x >= texel_count or first_z >= texel_count:
                continue
            start_x, start_z = max(0, first_x), max(0, first_z)
            alpha_width = len(
                layers[0][1]
            )  # sample positions inside the chunk's alpha grid (w x w samples span per tiles)
            sample_x = (
                ((np.arange(start_x, end_x) - first_x) + 0.5) / texels_per_tile * (alpha_width - 1) / tiles_per_chunk
            )
            sample_z = (
                ((np.arange(start_z, end_z) - first_z) + 0.5) / texels_per_tile * (alpha_width - 1) / tiles_per_chunk
            )
            uncovered = remaining[start_z:end_z, start_x:end_x]
            for k, (texture_id, grid) in reversed(list(enumerate(layers))):  # top layer first: it takes its share first
                material = material_of.get(texture_id)
                if material is None or texture_id == BASE_TEXTURE:  # foliage layers and the sea bed underneath
                    continue
                alpha = bilinear(np.asarray(grid, dtype=np.float32) / 255.0, sample_x, sample_z)
                if k and texture_id in curves:  # as 2070 lays it on over the chunk's first, opaque layer
                    alpha = np.interp(alpha, np.linspace(0, 1, 256), curves[texture_id]).astype(np.float32)
                take = alpha * uncovered
                weights[index[material], start_z:end_z, start_x:end_x] += take
                uncovered -= take
            remaining[start_z:end_z, start_x:end_x] = uncovered
        return weights, remaining

    def material_weights(self, texels_per_tile: int, material_of: dict[int, int]):
        """Visible weight per 117 material per texel (numpy array [materials, H, W]) from the 2070 texture layers
        (island-build "The ground"): the chunks composited over the sea floor as 2070 lays them,
        blended, 2070's river bed under the rivers (``riverbed``) and the hand-painted strokes laid over."""

        material_ids = sorted(set(material_of.values()))
        index = {material_id: slot for slot, material_id in enumerate(material_ids)}
        weights, remaining = self._composite_chunks(texels_per_tile, material_of, index)
        sea = material_of.get(BASE_TEXTURE)
        if sea is not None:  # what no layer covers is the sea floor, 2070's own base
            weights[index[sea]] += remaining
        del remaining
        if MATERIAL_BLEND > 0:
            blend_materials(weights, texels_per_tile)
        weights = lay_river_bed(self, material_ids, weights, texels_per_tile, material_of)  # per texel, not per tile
        if self.paint:
            material_ids, weights = painted_layers(weights, material_ids, self.paint)
            weights = paint_strokes(weights, material_ids, self.paint, texels_per_tile)
        return material_ids, weights

    def encode_materials(
        self,
        texels_per_tile: int,
        material_of: dict[int, int],
        block: int = 2,
        transfer: dict[int, tuple] | None = None,
    ):
        """117 material maps at one resolution: (id words [H/block, W/block], alpha words [H, W])
        as uint32 arrays. Per block the (at most 6) strongest materials; per texel their opacities in
        5 bits each, layered in the material set's order (the first listed present is the opaque base;
        the list is by ID unless ``material_rank`` says otherwise);
        with ``transfer`` (material ID -> AlphaSharpness, mean diffuse alpha) written so the game's
        blend gives those opacities (``blend_alpha``)."""
        import numpy as np

        material_ids, weights = self.material_weights(texels_per_tile, material_of)
        material_count, texel_count, _ = weights.shape
        block_count = texel_count // block
        block_weights = weights.reshape(material_count, block_count, block, block_count, block).sum(
            axis=(2, 4)
        )  # block weights [m, nb, nb]
        order = top_slots(
            block_weights, max(1, ALPHA_BAND_TEXELS // max(1, block_count * material_count))
        )  # [<=6, nb, nb] material slots per block
        slot_count = order.shape[0]
        ids = np.zeros((block_count, block_count), dtype=np.uint32)
        material_id_array = np.asarray(material_ids, dtype=np.uint32)
        slot_ids = material_id_array[order]  # [k, nb, nb]
        present = np.take_along_axis(block_weights, order, axis=0) > 1e-6
        present[0] = True
        # present slots carry their material id; unused slots a filler id (vanilla fills them with
        # ascending ids; their opacity is 0, so the value does not matter)
        filler_start = present.sum(axis=0)
        for slot in range(6):
            real = slot_ids[slot] & 31 if slot < slot_count else np.zeros((block_count, block_count), dtype=np.uint32)
            value = np.where(slot < filler_start, real, np.uint32(slot))
            ids |= value.astype(np.uint32) << np.uint32(5 * slot)
        alphas = np.zeros((texel_count, texel_count), dtype=np.uint32)
        # every step below is per texel: a band of block rows at a time holds a slice of the [k, n, n] arrays,
        # not all of them (the island's memory peak, run-state "Memory by need")
        rows = max(1, ALPHA_BAND_TEXELS // max(1, texel_count * block))
        for first_block in range(0, block_count, rows):
            end_block = min(block_count, first_block + rows)
            band = weights[:, first_block * block : end_block * block]
            alphas[first_block * block : end_block * block] = self._encode_alphas(
                band,
                order[:, first_block:end_block],
                present[:, first_block:end_block],
                slot_ids[:, first_block:end_block],
                block,
                transfer,
            )
        return ids, alphas, {int(material_id) for material_id in material_ids}

    def _encode_alphas(self, weights, order, present, slot_ids, block: int, transfer):
        """The alpha words of a band of texels (``encode_materials``): the block's slots' weights per texel,
        turned into the opacities the game's layering needs, 5 bits each."""
        import numpy as np

        slot_count = order.shape[0]
        expanded_order = np.repeat(np.repeat(order, block, axis=1), block, axis=2)  # [k, rows, n]
        texel_weights = np.take_along_axis(weights, expanded_order, axis=0)
        texel_present = np.repeat(np.repeat(present, block, axis=1), block, axis=2)
        texel_weights = np.where(texel_present, texel_weights, 0)
        total = texel_weights.sum(axis=0)
        total[total == 0] = 1
        texel_weights = texel_weights / total
        texel_ids = np.where(
            texel_present, np.repeat(np.repeat(slot_ids, block, axis=1), block, axis=2).astype(np.int64), -1
        )
        del expanded_order, texel_present
        rank = getattr(self, "material_rank", None)  # the set's list order when it is not the IDs' (materialorder)
        if rank:
            rank_of_id = np.array([rank.get(i, i) for i in range(32)], np.int64)
            opacities = layer_opacities(
                texel_weights, np.where(texel_ids >= 0, rank_of_id[np.clip(texel_ids, 0, 31)], -1)
            )
        else:
            opacities = layer_opacities(texel_weights, texel_ids)
        if transfer:
            sharpness = np.zeros(32)
            mean_alpha = np.ones(32)
            for material_id, (sharpness_value, mean_value) in transfer.items():
                sharpness[material_id], mean_alpha[material_id] = sharpness_value, mean_value
            known = np.clip(texel_ids, 0, 31)
            opacities = blend_alpha(opacities, sharpness[known], mean_alpha[known])
        alphas = np.zeros(weights.shape[1:], dtype=np.uint32)
        for slot in range(slot_count):
            quantised = np.rint(opacities[slot] * 31).astype(np.uint32)
            alphas |= quantised << (5 * slot)
        return alphas

    def apply_materials(
        self,
        material_of: dict[int, int],
        gamedata_files: dict[str, bytes],
        transfer: dict[int, tuple] | None = None,
    ) -> dict[str, bytes]:
        """Write the material maps: ``rd3d`` MaterialIDs2/MaterialAlphas2 and the
        ``materialmaps/<lod>/matid.dds``/``matalpha.dds`` of ``gamedata_files`` (paths relative to
        the island's ``_gamedata/<name>/`` folder); set the used-material masks in ``rd3d`` and
        in every ``.tmc``. ``transfer``: see ``encode_materials``. Returns the changed ``_gamedata`` files."""

        terrain = self.rd3d.path("Terrain")
        per_tile = terrain.find("MaterialMapWidth").u32() // self.size  # 8
        block = terrain.find("MaterialIDBlockSize").u32()
        out: dict[str, bytes] = {}
        used: set[int] = set()
        for lod in range(4):
            lod_texels_per_tile = per_tile >> lod
            if lod_texels_per_tile < 1:
                break
            ids, alphas, lod_used = self.encode_materials(lod_texels_per_tile, material_of, block, transfer)
            used |= lod_used
            for name, pixels in (("matid.dds", ids), ("matalpha.dds", alphas)):
                key = f"materialmaps/{lod}/{name}"
                if key in gamedata_files:
                    out[key] = replace_dds_pixels(gamedata_files[key], pixels)
            if lod == 3:
                set_bytes(terrain.find("MaterialIDs2"), ids)
                set_bytes(terrain.find("MaterialAlphas2"), alphas)
        mask = 0
        for mid in used:
            mask |= 1 << mid
        used_bits_attribute = terrain.find("UsedMaterialBits")
        if used_bits_attribute is not None:
            used_bits_attribute.data = (
                struct.pack("<I", mask) * (len(used_bits_attribute.data) // 4)
                + used_bits_attribute.data[len(used_bits_attribute.data) // 4 * 4 :]
            )
        for key, data in gamedata_files.items():
            if key.endswith(".tmc"):
                doc = filedb.read(data)
                name_ids = filedb.name_ids(data)
                changed = False
                for node in doc.iter():
                    if isinstance(node, filedb.SAttrib) and node.name == "UsedMaterialBits" and len(node.data) == 4:
                        node.data = struct.pack("<I", mask)
                        changed = True
                if changed:
                    out[key] = filedb.write(doc, *name_ids)
        self.report["materials"] = sorted(used)
        return out

    def set_material_set(self, path: str) -> None:
        """Point the terrain at another material set (stored as UTF-16 without a terminator)."""
        self.rd3d.path("Terrain").find("MaterialSetFileName").data = path.encode("utf-16-le")

    def clear_grit(self) -> bool:
        """Empty the island's grit map (``Terrain/GritMap2``).

        Grit is 117's detail layer over the terrain: the material set names one texture for it
        (the Roman set a blade-of-grass texture at tiling 4) and this map says where it shows.
        A built island inherits a full one, which lays 117's grass over 2070's ground."""
        node = self.rd3d.path("Terrain/GritMap2")
        map_attribute = node.find("map") if node is not None else None
        if map_attribute is None:
            return False
        map_attribute.data = bytes(len(map_attribute.data))
        self.report["grit_cleared"] = len(map_attribute.data)
        return True

    def set_prop_set(self, name: str) -> bool:
        """Point the island at a prop set of ``config/engine/prop_sets.xml``: the vegetation the
        engine scatters itself, per terrain material ``GroundType`` (the donor's set is what put
        Roman bushes over a 2070 island). Stored as plain ASCII."""
        node = self.gamedata.path("GameSessionManager/SessionSettings/VegetationPropSetName")
        if node is None:
            return False
        node.data = name.encode("ascii")
        return True
