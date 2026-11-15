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


def painted_layers(weights, mats: list[int], strokes: list[dict]):
    """An empty weight layer for each painted material (0-31) the 2070 layers do not use: a set
    material the island had no texture for, or a 117 one the editor added to the island's set."""
    import numpy as np

    new = sorted({int(s.get("material", -1)) for s in strokes} - set(mats))
    new = [m for m in new if 0 <= m < 32]
    if not new:
        return mats, weights
    return [*mats, *new], np.concatenate([weights, np.zeros((len(new), *weights.shape[1:]), weights.dtype)])


def paint_strokes(weights, mats: list[int], strokes: list[dict], texels_per_tile: int):
    """Hand-painted ground over the composited weights: each stroke (``material``, ``radius`` and
    ``points`` in tiles, ``strength``) lays its material over what is there with a soft round brush,
    in the order the strokes were painted. A material outside the island's set is skipped and reported."""

    index = {m: k for k, m in enumerate(mats)}
    skipped: set[int] = set()
    for st in strokes:
        k = index.get(int(st.get("material", -1)))
        if k is None:
            skipped.add(int(st.get("material", -1)))
            continue
        cover = stroke_cover(st, weights.shape[1], texels_per_tile)
        weights *= 1.0 - cover
        weights[k] += cover
    if skipped:
        warnings.warn(
            f"painted strokes of materials {sorted(skipped)} match nothing: the island's ground has {sorted(mats)}",
            RuntimeWarning,
            stacklevel=2,
        )
    return weights


def stroke_cover(stroke: dict, n: int, texels_per_tile: float):
    """How much of each texel (n x n) a painted stroke covers: a soft round brush (``radius`` in
    tiles) along its ``points``, times its ``strength``. The line between two points is filled in
    at a quarter radius: a fast drag records points far apart."""
    import numpy as np

    cover = np.zeros((n, n), np.float32)
    r = float(stroke.get("radius", 4))
    if r <= 0:
        return cover
    pts = [tuple(map(float, p)) for p in stroke.get("points", [])]
    dabs = pts[:1]
    for (xa, za), (xb, zb) in zip(pts, pts[1:], strict=False):
        steps = max(1, int(math.ceil(math.hypot(xb - xa, zb - za) / (r / 4))))
        dabs += [(xa + (xb - xa) * k / steps, za + (zb - za) * k / steps) for k in range(1, steps + 1)]
    for x, z in dabs:
        x0, x1 = max(0, int((x - r) * texels_per_tile)), min(n, int((x + r) * texels_per_tile) + 1)
        z0, z1 = max(0, int((z - r) * texels_per_tile)), min(n, int((z + r) * texels_per_tile) + 1)
        if x0 >= x1 or z0 >= z1:
            continue
        tx = (np.arange(x0, x1) + 0.5) / texels_per_tile - x
        tz = (np.arange(z0, z1) + 0.5) / texels_per_tile - z
        d = np.hypot(tx[None, :], tz[:, None]) / r
        a = np.where(d < 1, 0.5 * (1 + np.cos(np.pi * np.minimum(d, 1))), 0).astype(np.float32)
        np.maximum(cover[z0:z1, x0:x1], a, out=cover[z0:z1, x0:x1])
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

    o = np.clip(opacity, 0.0, 1.0)
    x = 0.5 - np.sin(np.arcsin(np.clip(1 - 2 * o, -1, 1)) / 3)  # smoothstep inverted
    out = np.clip((x - sharpness * (mean_alpha - 1)) / (sharpness + 1), 0.0, 1.0)
    return np.where(o >= 0.999, 1.0, np.where(o <= 0.0, 0.0, out))


def layer_opacities(weights, ids):
    """Per slot the opacity that makes the game's compositing give ``weights`` (slot axis 0, summing
    to 1; ``ids`` -1 where a slot is unused). The game lays each material over the ones before it in
    the material set's list - ours lists them by ID - not in slot order (rendering/terrain-materials
    1.4): from the highest ID down a = w / remaining, and the lowest present one is opaque."""
    import numpy as np

    top_down = np.argsort(-ids, axis=0, kind="stable")
    ws = np.take_along_axis(weights, top_down, axis=0)
    present = np.take_along_axis(ids, top_down, axis=0) >= 0
    remaining = np.ones(weights.shape[1:])
    op = np.zeros_like(ws, dtype=np.float64)
    for s in range(len(ws)):
        op[s] = np.clip(np.where(remaining > 1e-6, ws[s] / np.maximum(remaining, 1e-6), 0), 0, 1)
        remaining = remaining - ws[s]
    base = present.cumsum(axis=0) == present.sum(axis=0)  # from the lowest present one on
    op = np.where(base & present, 1.0, op)
    out = np.zeros_like(op)
    np.put_along_axis(out, top_down, op, axis=0)
    return out


def slot_weights(slot_ids, slot_alpha, order: dict[int, int]):
    """The visible weight of each slot as the game lays the ground (rendering/terrain-materials 1.2, 1.4), the
    inverse of ``GroundMixin.encode_materials``: a material's coverage is its alphas summed over the slots (at
    most 1), the materials go down in the material set's list order (``order``: ID -> place, an ID the set lacks
    last), each takes its coverage of what the ones above leave, and the first one listed takes the rest. Both
    arrays are [slots, z, x]; the weights come back in that shape and sum to 1 per texel."""
    import numpy as np

    count = slot_ids.shape[0]
    lut = np.array([order.get(i, 32 + i) for i in range(32)], np.int64)
    rank = lut[slot_ids]
    here = slot_alpha > 0
    total = np.stack([np.where(slot_ids == slot_ids[s], slot_alpha, 0).sum(axis=0) for s in range(count)])
    cover = np.minimum(total, 1.0)
    # one slot stands for each material present: a material in two slots lowers what lies below it once
    lead = [here[s] & ~np.any([here[t] & (slot_ids[t] == slot_ids[s]) for t in range(s)], axis=0) for s in range(count)]
    lead = np.stack(lead) if count else np.zeros_like(here)
    left = np.ones(slot_alpha.shape[1:], np.float32)
    first = np.full(slot_alpha.shape[1:], 1 << 30, np.int64)
    out = np.zeros_like(slot_alpha, dtype=np.float32)
    for s in range(count):
        above = np.ones_like(left)
        for t in range(count):
            above = above * np.where(lead[t] & (rank[t] > rank[s]), 1.0 - cover[t], 1.0)
        share = np.where(here[s], slot_alpha[s] / np.maximum(total[s], 1e-9), 0.0)
        out[s] = cover[s] * above * share
        left = left * np.where(lead[s], 1.0 - cover[s], 1.0)
        first = np.where(here[s], np.minimum(first, rank[s]), first)
    for s in range(count):
        share = np.where(here[s] & (rank[s] == first), slot_alpha[s] / np.maximum(total[s], 1e-9), 0.0)
        out[s] += left * share
    nothing = ~here.any(axis=0)
    out[0] = np.where(nothing, 1.0, out[0])
    return out


def beach_map(h, cell: float):
    """The WaterInfos beach byte per cell from the cells' ground (world units, sea level 0): full on
    gentle shores, none on steep ones, from ``BEACH_LAND`` tiles inland to ``BEACH_SEA`` out, fading
    over the next ``BEACH_FADE``; fitted to vanilla's islands (rendering/water.md, the beach byte)."""
    import numpy as np
    from scipy import ndimage

    slope = ndimage.maximum_filter(np.hypot(*np.gradient(h, cell)), 3)  # world units per tile
    gentle = np.clip((BEACH_STEEP - slope) / (BEACH_STEEP - BEACH_GENTLE), 0, 1)
    land = h > 0
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
        weights[k] = ndimage.gaussian_filter(weights[k], sigma, mode="nearest")
    weights /= np.maximum(weights.sum(axis=0), 1e-6)


class GroundMixin:
    """The methods of ``IslandBuilder`` that build the ground."""

    # Terrain materials
    def _composite_chunks(self, texels_per_tile: int, material_of: dict[int, int], index: dict[int, int]):
        """The 2070 layers composited per 16x16-tile chunk, each chunk in its own layer order over a full-coverage
        base (a merged island-wide order cannot express chunks that disagree: a beach paints grass over sand, a river
        bed sand over grass). Layer i keeps a_i of what the layers above it leave, through 2070's paint curve; weights
        of textures that map to one material add up. Returns (weights [materials, H, W], what no layer covered)."""
        import numpy as np

        n = self.size * texels_per_tile
        weights = np.zeros((len(index), n, n), dtype=np.float32)
        remaining = np.ones((n, n), dtype=np.float32)
        curves = self.paint_curves
        for cx, cz, per, layers in self.isl.texture_chunks():
            x0 = (cx * per + self.pad_x) * texels_per_tile  # texels of this chunk in the padded island
            z0 = (cz * per + self.pad_z) * texels_per_tile
            x1, z1 = min(n, x0 + per * texels_per_tile), min(n, z0 + per * texels_per_tile)
            if x1 <= 0 or z1 <= 0 or x0 >= n or z0 >= n:
                continue
            sx0, sz0 = max(0, x0), max(0, z0)
            w = len(layers[0][1])  # sample positions inside the chunk's alpha grid (w x w samples span per tiles)
            gx = ((np.arange(sx0, x1) - x0) + 0.5) / texels_per_tile * (w - 1) / per
            gz = ((np.arange(sz0, z1) - z0) + 0.5) / texels_per_tile * (w - 1) / per
            sub = remaining[sz0:z1, sx0:x1]
            for k, (idx, grid) in reversed(list(enumerate(layers))):  # top layer first: it takes its share first
                mat = material_of.get(idx)
                if mat is None or idx == BASE_TEXTURE:  # foliage layers and the sea bed underneath
                    continue
                a_ = bilinear(np.asarray(grid, dtype=np.float32) / 255.0, gx, gz)
                if k and idx in curves:  # as 2070 lays it on over the chunk's first, opaque layer
                    a_ = np.interp(a_, np.linspace(0, 1, 256), curves[idx]).astype(np.float32)
                take = a_ * sub
                weights[index[mat], sz0:z1, sx0:x1] += take
                sub -= take
            remaining[sz0:z1, sx0:x1] = sub
        return weights, remaining

    def material_weights(self, texels_per_tile: int, material_of: dict[int, int]):
        """Visible weight per 117 material per texel (numpy array [materials, H, W]) from the 2070 texture layers
        (island-build "The ground"): the chunks composited over the sea floor as 2070 lays them,
        blended, 2070's river bed under the rivers (``riverbed``) and the hand-painted strokes laid over."""

        mats = sorted(set(material_of.values()))
        index = {m: k for k, m in enumerate(mats)}
        weights, remaining = self._composite_chunks(texels_per_tile, material_of, index)
        sea = material_of.get(BASE_TEXTURE)
        if sea is not None:  # what no layer covers is the sea floor, 2070's own base
            weights[index[sea]] += remaining
        del remaining
        if MATERIAL_BLEND > 0:
            blend_materials(weights, texels_per_tile)
        weights = lay_river_bed(self, mats, weights, texels_per_tile, material_of)  # per texel, not per tile
        if self.paint:
            mats, weights = painted_layers(weights, mats, self.paint)
            weights = paint_strokes(weights, mats, self.paint, texels_per_tile)
        return mats, weights

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

        mats, w = self.material_weights(texels_per_tile, material_of)
        m, n, _ = w.shape
        nb = n // block
        bw = w.reshape(m, nb, block, nb, block).sum(axis=(2, 4))  # block weights [m, nb, nb]
        order = np.argsort(-bw, axis=0)[:6]  # [<=6, nb, nb] material slots per block
        k = order.shape[0]
        ids = np.zeros((nb, nb), dtype=np.uint32)
        mat_ids = np.asarray(mats, dtype=np.uint32)
        slot_ids = mat_ids[order]  # [k, nb, nb]
        present = np.take_along_axis(bw, order, axis=0) > 1e-6
        present[0] = True
        # present slots carry their material id; unused slots a filler id (vanilla fills them with
        # ascending ids; their opacity is 0, so the value does not matter)
        filler_start = present.sum(axis=0)
        for s in range(6):
            real = slot_ids[s] & 31 if s < k else np.zeros((nb, nb), dtype=np.uint32)
            value = np.where(s < filler_start, real, np.uint32(s))
            ids |= value.astype(np.uint32) << np.uint32(5 * s)
        alphas = np.zeros((n, n), dtype=np.uint32)
        # every step below is per texel: a band of block rows at a time holds a slice of the [k, n, n] arrays,
        # not all of them (the island's memory peak, run-state "Memory by need")
        rows = max(1, ALPHA_BAND_TEXELS // max(1, n * block))
        for b0 in range(0, nb, rows):
            b1 = min(nb, b0 + rows)
            band = w[:, b0 * block : b1 * block]
            alphas[b0 * block : b1 * block] = self._encode_alphas(
                band, order[:, b0:b1], present[:, b0:b1], slot_ids[:, b0:b1], block, transfer
            )
        return ids, alphas, {int(x) for x in mats}

    def _encode_alphas(self, w, order, present, slot_ids, block: int, transfer):
        """The alpha words of a band of texels (``encode_materials``): the block's slots' weights per texel,
        turned into the opacities the game's layering needs, 5 bits each."""
        import numpy as np

        k = order.shape[0]
        up = np.repeat(np.repeat(order, block, axis=1), block, axis=2)  # [k, rows, n]
        tw = np.take_along_axis(w, up, axis=0)
        pres = np.repeat(np.repeat(present, block, axis=1), block, axis=2)
        tw = np.where(pres, tw, 0)
        total = tw.sum(axis=0)
        total[total == 0] = 1
        tw = tw / total
        tid = np.where(pres, np.repeat(np.repeat(slot_ids, block, axis=1), block, axis=2).astype(np.int64), -1)
        del up, pres
        rank = getattr(self, "material_rank", None)  # the set's list order when it is not the IDs' (materialorder)
        if rank:
            lut = np.array([rank.get(i, i) for i in range(32)], np.int64)
            opac = layer_opacities(tw, np.where(tid >= 0, lut[np.clip(tid, 0, 31)], -1))
        else:
            opac = layer_opacities(tw, tid)
        if transfer:
            sharp = np.zeros(32)
            mean = np.ones(32)
            for mid, (s_, a_) in transfer.items():
                sharp[mid], mean[mid] = s_, a_
            known = np.clip(tid, 0, 31)
            opac = blend_alpha(opac, sharp[known], mean[known])
        alphas = np.zeros(w.shape[1:], dtype=np.uint32)
        for s in range(k):
            q = np.rint(opac[s] * 31).astype(np.uint32)
            alphas |= q << (5 * s)
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

        t = self.rd.path("Terrain")
        per_tile = t.find("MaterialMapWidth").u32() // self.size  # 8
        block = t.find("MaterialIDBlockSize").u32()
        out: dict[str, bytes] = {}
        used: set[int] = set()
        for lod in range(4):
            tpt = per_tile >> lod
            if tpt < 1:
                break
            ids, alphas, mats = self.encode_materials(tpt, material_of, block, transfer)
            used |= mats
            for name, arr in (("matid.dds", ids), ("matalpha.dds", alphas)):
                key = f"materialmaps/{lod}/{name}"
                if key in gamedata_files:
                    out[key] = replace_dds_pixels(gamedata_files[key], arr)
            if lod == 3:
                set_bytes(t.find("MaterialIDs2"), ids)
                set_bytes(t.find("MaterialAlphas2"), alphas)
        mask = 0
        for mid in used:
            mask |= 1 << mid
        umb = t.find("UsedMaterialBits")
        if umb is not None:
            umb.data = struct.pack("<I", mask) * (len(umb.data) // 4) + umb.data[len(umb.data) // 4 * 4 :]
        for key, data in gamedata_files.items():
            if key.endswith(".tmc"):
                doc = filedb.read(data)
                ids_ = filedb.name_ids(data)
                changed = False
                for node in doc.iter():
                    if isinstance(node, filedb.Attrib) and node.name == "UsedMaterialBits" and len(node.data) == 4:
                        node.data = struct.pack("<I", mask)
                        changed = True
                if changed:
                    out[key] = filedb.write(doc, *ids_)
        self.report["materials"] = sorted(used)
        return out

    def set_material_set(self, path: str) -> None:
        """Point the terrain at another material set (stored as UTF-16 without a terminator)."""
        self.rd.path("Terrain").find("MaterialSetFileName").data = path.encode("utf-16-le")

    def clear_grit(self) -> bool:
        """Empty the island's grit map (``Terrain/GritMap2``).

        Grit is 117's detail layer over the terrain: the material set names one texture for it
        (the Roman set a blade-of-grass texture at tiling 4) and this map says where it shows.
        A built island inherits a full one, which lays 117's grass over 2070's ground."""
        node = self.rd.path("Terrain/GritMap2")
        m = node.find("map") if node is not None else None
        if m is None:
            return False
        m.data = bytes(len(m.data))
        self.report["grit_cleared"] = len(m.data)
        return True

    def set_prop_set(self, name: str) -> bool:
        """Point the island at a prop set of ``config/engine/prop_sets.xml``: the vegetation the
        engine scatters itself, per terrain material ``GroundType`` (the donor's set is what put
        Roman bushes over a 2070 island). Stored as plain ASCII."""
        node = self.gd.path("GameSessionManager/SessionSettings/VegetationPropSetName")
        if node is None:
            return False
        node.data = name.encode("ascii")
        return True
