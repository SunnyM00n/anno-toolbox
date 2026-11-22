"""The consistency checks of an opened island (a mixin of ``islandview.CIslandView``): one method per trap found in
the game so far (compressed members, materials, LOD meshes, height pages against the coarse heightmap, river mouths,
node bounds, herds, rivers). Each returns report lines that start with OK, WARN, FAIL or INFO;
``check`` runs them all (docs/island-terrain.md)."""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import numpy as np

from ..formats import filedb
from ..formats.rda import FLAG_COMPRESSED
from . import gameterrain
from .donor117 import area_keys
from .islandgrid import AREA_ISLAND, BUILD_FLOOR, HEIGHT_UNITS, border_connected, read_bits, read_sparse
from .islandterrain import node_tiles

PAGE_AGREE = 0.05  # world units: the drawn ground may stand this far off the coarse heightmap on buildable ground
# (99.9% of its points; vanilla's islands: 0.02-0.03, island-terrain "Pages and the coarse heightmap")
BRIDGE_MAX = 16  # 117's MeshGraphBalancing/BridgeLengthMax: the longest bridge a street can span


def _in_game(files, path: str) -> bool:
    try:
        files.m_game.read(path.replace("\\", "/").lower())
        return True
    except Exception:
        return False


class CIslandChecks:
    """The checks; ``CIslandView`` brings the island's files and readers they use."""

    # Checks: one method per trap found in game so far; each returns its report lines (OK, WARN, FAIL, INFO)
    def check(self) -> list[str]:
        """Consistency checks; lines start with OK, WARN or FAIL."""
        rep: list[str] = []
        for part in (
            self.check_compression,
            self.check_materials,
            self.check_lod_meshes,
            self.check_lod_coverage,
            self.check_height_pages,
            self.check_page_agreement,
            self.check_river_mouths,
            self.check_node_bounds,
            self.check_displacement,
            self.check_coast_area,
            self.check_area_ids,
            self.check_rivers,
            self.check_herds,
        ):
            rep.extend(part())
        return rep

    def check_compression(self) -> list[str]:
        """The game only opens archive members compressed at zlib level 1 (header 78 01)."""
        rep = []
        for entry in self.archive.entries.values():
            if not entry.flags & FLAG_COMPRESSED:
                continue
            start = entry.blob_offset if entry.contiguous else entry.offset
            head = self.raw_a7m[start : start + 2].hex()
            rep.append(
                ("OK" if head == "7801" else "FAIL")
                + f" .a7m member {entry.path} zlib header {head} (the game opens 78 01 only)"
            )
        return rep

    def check_materials(self) -> list[str]:
        """The material set and every material's maps resolve (a new ``.xml`` in a mod does not reach the game), the
        maps use only materials of the set, ``UsedMaterialBits`` covers them."""
        where = self.files.where(self.material_set)
        if where is None:
            return [f"FAIL material set {self.material_set} not found"]
        rep = []
        if (
            where == "mod"
            and self.material_set.endswith(".xml")
            and self.files.m_game
            and not _in_game(self.files, self.material_set)
        ):
            rep.append(
                f"FAIL material set {self.material_set} is a new .xml in the mod: the mod loader does not pass it to the game"
            )
        else:
            rep.append(f"OK material set {self.material_set} ({where})")
        mats = self.materials()
        missing = []
        for mid, material in sorted(mats.items()):
            if self.files.where(material["file"]) is None:
                missing.append(f"material {mid} file {material['file']}")
                continue
            if (
                material["file"].endswith(".xml")
                and self.files.where(material["file"]) == "mod"
                and not _in_game(self.files, material["file"])
            ):
                missing.append(f"material {mid} {material['file']} is a new .xml in the mod (not passed to the game)")
            for kind, psd in material.get("maps", {}).items():
                if self.files.where(re.sub(r"\.(psd|png|tga)$", "_0.dds", psd)) is None:
                    missing.append(f"material {mid} {kind} map {psd}")
        rep.append(
            ("OK" if not missing else "FAIL")
            + f" {len(mats)} materials resolve"
            + ("" if not missing else ": " + "; ".join(missing[:8]))
        )
        mids, wts = self.weights()
        used = set(np.unique(mids[wts > 0.01]).tolist())
        unknown = used - set(mats)
        rep.append(
            ("OK" if not unknown else "FAIL")
            + f" material maps use {sorted(used)}"
            + (f"; not in the set: {sorted(unknown)}" if unknown else "")
        )
        mask = 0
        for material in used:
            mask |= 1 << material
        umb = self.rd3d.path("Terrain").find("UsedMaterialBits").data
        values = np.frombuffer(umb[: len(umb) // 4 * 4], "<u4")
        covered = int(np.bitwise_or.reduce(values)) if len(values) else 0
        rep.append(
            ("OK" if covered & mask == mask else "WARN")
            + f" rd3d UsedMaterialBits cover {bin(covered & mask).count('1')}/{len(used)} used materials"
        )
        return rep

    def _levels(self) -> list[filedb.STag]:
        return self.rd3d.path("Terrain/Levels").findall("#1")

    def check_lod_meshes(self) -> list[str]:
        """Vanilla stores a mesh for exactly the flagged nodes at every level (a flagged node without a mesh is a
        hole; ``HasMeshArray`` rows are padded to 32 bits - read packed, LOD 4+ looked different)."""
        rep = []
        for level, level_tag in enumerate(self._levels()):
            if level < 2:
                continue
            has = np.asarray(read_bits(level_tag.path("HasMeshArray")), dtype=bool)
            side = has.shape[0]
            stored = np.zeros_like(has)
            for key in self.files.list(f"{self.gamedata_dir}meshes/{level}/"):
                if not key.endswith(".tmc"):
                    continue
                tile_x, tile_z = [int(part) for part in Path(key).stem.split("x")]
                doc = filedb.read(self.files.read(key))
                for k, tag in enumerate(
                    child for child in doc.path("Meshes/map").children if isinstance(child, filedb.STag)
                ):
                    node_x, node_z = tile_x * 32 + k % 32, tile_z * 32 + k // 32
                    if (
                        node_x < side
                        and node_z < side
                        and tag.find("Vertices") is not None
                        and tag.find("Vertices").data
                    ):
                        stored[node_z, node_x] = True
            holes = int((has & ~stored).sum())
            extra = int((stored & ~has).sum())
            rep.append(
                ("OK" if holes == 0 else "FAIL")
                + f" LOD {level}: {int(has.sum())} nodes flagged, {holes} flagged without a stored mesh, {extra} meshes not flagged"
            )
        return rep

    def check_lod_coverage(self) -> list[str]:
        """Vanilla's meshes stay inside the active area at every level; outside it they draw the sea as a flat,
        untextured terrain sheet; a flagged active tile without mesh cover is a hole."""
        rep = []
        active = self.active_tiles()
        levels = self._levels()
        for level in range(2, len(levels)):
            _, cov = self.lod_heights(level)
            outside = int((cov & ~active).sum())
            flags = np.asarray(read_bits(levels[level].path("HasMeshArray")), dtype=bool)
            node = node_tiles(self.size, flags.shape[0])
            flagged = np.repeat(np.repeat(flags, node, 0), node, 1)[: self.size, : self.size] & active
            holes = int((flagged & ~cov).sum())
            rep.append(
                ("OK" if outside <= active.size // 200 and holes <= flagged.sum() // 50 else "FAIL")
                + f" LOD {level}: meshes cover {int(cov.sum())} tiles, {outside} outside the active area, "
                f"{holes} of {int(flagged.sum())} flagged active tiles uncovered"
            )
        return rep

    def check_height_pages(self) -> list[str]:
        """The render heightmaps (``normalmaps/<level>/<x>x<z>.ctt``: the close-up terrain is drawn from these, not
        from the heightmaps; pages left from another island draw its terrain): every flagged page exists and its
        downsampled level follows the heightmap. A level-p page covers 16 * 2^p tiles on every grid; where the grid
        is not a multiple of that (192, 320, ...) the last page reaches past the island (``node_tiles``)."""
        rep = []
        for level, level_tag in enumerate(self.rd3d.path("Terrain/AllNormalMapLevels/Levels").findall("#1")):
            flags_tag = level_tag.find("Flags")
            side = flags_tag.find("width").u32()
            flags = np.frombuffer(flags_tag.find("map").data, "u1")[: side * side].reshape(side, side) > 0
            node = node_tiles(self.size, side)
            missing, errs = 0, []
            for node_z, node_x in zip(*np.nonzero(flags), strict=False):
                key = f"{self.gamedata_dir}normalmaps/{level}/{node_x}x{node_z}.ctt"
                try:
                    raw = zlib.decompress(self.files.read(key)[8:])
                except Exception:
                    missing += 1
                    continue
                downsampled = filedb.read(raw).path("HeightMap/Downsampled")
                downsampled_width = downsampled.find("width").u32()
                downsampled_heights = (
                    np.frombuffer(downsampled.find("map").data, "<u2")
                    .reshape(downsampled_width, downsampled_width)
                    .astype(np.float64)
                    - 32768
                ) / 64
                offsets = (np.arange(downsampled_width) - 0.5) * node / (downsampled_width - 2)
                sample_x = np.clip((node_x * node + offsets) * 2, 0, self.height_map.shape[1] - 1.001)
                sample_z = np.clip((node_z * node + offsets) * 2, 0, self.height_map.shape[0] - 1.001)
                floor_x, floor_z = sample_x.astype(int), sample_z.astype(int)
                fraction_x, fraction_z = (sample_x - floor_x)[None, :], (sample_z - floor_z)[:, None]
                heightmap = self.height_map
                reference = (
                    heightmap[np.ix_(floor_z, floor_x)] * (1 - fraction_x)
                    + heightmap[np.ix_(floor_z, floor_x + 1)] * fraction_x
                ) * (1 - fraction_z) + (
                    heightmap[np.ix_(floor_z + 1, floor_x)] * (1 - fraction_x)
                    + heightmap[np.ix_(floor_z + 1, floor_x + 1)] * fraction_x
                ) * fraction_z
                errs.append(float(np.abs(downsampled_heights - reference).mean()))
            worst = max(errs) if errs else 0.0
            rep.append(
                ("OK" if missing == 0 and worst < 2.0 else "FAIL")
                + f" height pages level {level}: {int(flags.sum())} flagged, {missing} missing, "
                f"worst mean difference to the heightmap {worst:.2f}"
            )
        return rep

    def page_deviation(self, step_tiles: float = 0.25) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """How far the ground the game draws (the height pages, clamped to one unit off the coarse map) stands above
        the coarse heightmap, in world units, on a grid of ``step`` tiles: (deviation, buildable mask, rim mask) with
        the tile masks spread to the grid. Buildable: 2070's build ground (``BUILD_FLOOR``) of the island area, where
        roads and land buildings stand; rim: the rest of the land."""
        island_size = self.size
        near, _ = gameterrain.near_heights(self, step_tiles)
        grid_axis = np.arange(near.shape[0]) * step_tiles
        grid_z, grid_x = np.meshgrid(grid_axis, grid_axis, indexing="ij")
        flat = gameterrain.coarse(self.height_map / HEIGHT_UNITS, grid_x, grid_z)
        deviation = near - flat
        land = self.land[:island_size, :island_size]
        area = self.island_area()[:island_size, :island_size] == AREA_ISLAND
        heights = self.height_map / HEIGHT_UNITS
        corners = np.stack(
            [
                heights[0 : 2 * island_size : 2, 0 : 2 * island_size : 2],
                heights[0 : 2 * island_size : 2, 2::2],
                heights[2::2, 0 : 2 * island_size : 2],
                heights[2::2, 2::2],
            ]
        )
        ground = land & area & (corners.mean(axis=0) > BUILD_FLOOR)
        tile = np.minimum(grid_axis.astype(int), island_size - 1)
        grid = np.ix_(tile, tile)
        return deviation, ground[grid] & ~self.page_cliffs(flat), (land & ~ground)[grid]

    @staticmethod
    def page_cliffs(flat: np.ndarray) -> np.ndarray:
        """Grid points beside a step of the coarse map over what a page can carry (``PAGE_RANGE``): a page texel
        reaching over a shoreline cliff is clamped, as the engine clamps it, and the coarse map has the step too."""
        rising = np.zeros(flat.shape, bool)
        for axis in (0, 1):
            step = np.abs(np.diff(flat, axis=axis)) > gameterrain.PAGE_RANGE
            hit = np.zeros(flat.shape, bool)
            front = [slice(None)] * 2
            back = [slice(None)] * 2
            front[axis], back[axis] = slice(0, -1), slice(1, None)
            hit[tuple(front)] |= step
            hit[tuple(back)] |= step
            rising |= hit
        return rising

    def check_page_agreement(self) -> list[str]:
        """Roads, buildings and slots are laid on the coarse heightmap, the ground is drawn from the height pages: where
        the page stands higher, the road is hidden under it (island-terrain "Pages and the coarse heightmap"). On buildable ground the two
        must agree to ``PAGE_AGREE`` (99.9% of the points); the rims are reported."""
        deviation, build, rim = self.page_deviation()
        parts = []
        for name, mask in (("buildable", build), ("rim", rim)):
            if not mask.any():
                continue
            masked = deviation[mask]
            absolute = np.abs(masked)
            parts.append(
                f"{name} {int(mask.sum())} points: |deviation| p99 {np.percentile(absolute, 99):.3f} p99.9 "
                f"{np.percentile(absolute, 99.9):.3f} max {absolute.max():.3f}, above the coarse map p99.9 {np.percentile(masked, 99.9):.3f}"
            )
        if not build.any():
            return ["INFO height pages vs coarse heightmap: no buildable ground"]
        agrees = np.percentile(np.abs(deviation[build]), 99.9) <= PAGE_AGREE
        return [("OK" if agrees else "FAIL") + " height pages vs coarse heightmap (world units): " + "; ".join(parts)]

    def check_river_mouths(self) -> list[str]:
        """A river ending over terrain below the sea (its mouth) must come down to the sea level there (vanilla:
        -0.3..0.3): water left above it is drawn as a flat sheet over the sea."""
        high = []
        for kind, points, _ in self.splines():
            if kind != "River":
                continue
            for x, y, z in (points[0], points[-1]):
                j, i = int(z * 2), int(x * 2)
                if (
                    0 <= j < self.height_map.shape[0]
                    and 0 <= i < self.height_map.shape[1]
                    and self.height_map[j, i] < 0
                    and y > 0.5
                ):
                    high.append(
                        f"({x:.0f}, {z:.0f}) water {y:.2f} over terrain {self.height_map[j, i] / HEIGHT_UNITS:.2f}"
                    )
        return [
            ("OK" if not high else "FAIL")
            + " river mouths at the sea level"
            + ("" if not high else f": {len(high)} above it, e.g. " + "; ".join(high[:4]))
        ]

    def check_node_bounds(self) -> list[str]:
        """``Levels`` MinY/MaxY per node, in world units: ceil(highest / 4), and lowest / 4 truncated towards zero as
        vanilla's or floored as the build's (``apply_levels``: a box truncated upwards clips a node under the sea)
        (sampled)."""
        bad = 0
        for level_tag in self._levels():
            min_y_tag = level_tag.path("MinYArray")
            side = min_y_tag.find("width").u32()
            min_y_map = np.frombuffer(min_y_tag.find("map").data, "<i2").reshape(side, side)
            max_y_map = np.frombuffer(level_tag.path("MaxYArray").find("map").data, "<i2").reshape(side, side)
            samples_per_node = (
                (self.height_map.shape[0] - 1) // self.size * node_tiles(self.size, side)
            )  # heightmap samples per node
            for j in range(0, side, max(1, side // 32)):
                for i in range(0, side, max(1, side // 32)):
                    block = self.height_map[
                        j * samples_per_node : (j + 1) * samples_per_node + 1,
                        i * samples_per_node : (i + 1) * samples_per_node + 1,
                    ]
                    low = block.min() / HEIGHT_UNITS
                    if min_y_map[j, i] not in (int(np.trunc(low)), int(np.floor(low))) or max_y_map[j, i] != int(
                        np.ceil(block.max() / HEIGHT_UNITS)
                    ):
                        bad += 1
        return [
            ("OK" if bad == 0 else "WARN")
            + f" Levels MinY/MaxY: {bad} sampled nodes differ from trunc or floor(min/4)/ceil(max/4) of the heightmap"
        ]

    def check_displacement(self) -> list[str]:
        displacement = self.game_session_manager.path("TerrainManager/DisplacementHeightMap").find("HeightMap").data
        displacement_values = np.frombuffer(displacement, "<i2")
        coarse_values = np.frombuffer(self.rd3d.path("Terrain").find("CoarseDisplacementMap").find("map").data, "u1")
        return [
            f"INFO displacement: gamedata [{displacement_values.min()}..{displacement_values.max()}], "
            f"rd3d [{coarse_values.min()}..{coarse_values.max()}] (neutral 0 / 128)"
        ]

    def island_area(self) -> np.ndarray:
        return np.asarray(read_sparse(self.game_session_manager.path("AreaIDs")))

    def check_coast_area(self) -> list[str]:
        """Docks need the water in front of a coast line inside the island area."""
        water_area = int(((~self.land) & (self.island_area() == AREA_ISLAND)).sum())
        coasts = sum(1 for k, *_ in self.splines() if k == "CoastLine")
        return [
            ("OK" if coasts == 0 or water_area > 0 else "FAIL")
            + f" {coasts} coast lines, {water_area} water tiles in the island area (docks need them)"
        ]

    def check_area_ids(self) -> list[str]:
        """Every id in ``AreaIDs`` has an area record in ``AreaManagerData``: a tile naming an area the island has no
        record of crashed 1800 (the Cinis port; island-regions "The areas"). In all 345 vanilla islands read the ids
        are a subset of the records (plus 0 for none)."""
        keys = set(area_keys(self.game_session_manager))
        if not keys:
            return []
        found = {int(area_id) for area_id in np.unique(self.island_area())} - {0}
        lacking = sorted(found - keys)
        if lacking:
            return ["FAIL area ids without an area record: " + ", ".join(f"{i:#x}" for i in lacking)]
        return [f"OK area ids {', '.join(f'{i:#x}' for i in sorted(found))} all have an area record"]

    def check_herds(self) -> list[str]:
        """117 spawns wildlife inside roaming areas: one ``HerdGrid`` bit per tile and one
        ``HerdAreas`` entry per connected region of it, carrying that region's tile count. The
        shipped islands give their animals 27-29% of their land."""
        from scipy import ndimage

        animals = self.game_session_manager.path("AnimalManager")
        grid_tag = animals.find("HerdGrid") if animals is not None else None
        areas = animals.find("HerdAreas") if animals is not None else None
        if grid_tag is None or areas is None:
            return []
        grid = np.asarray(read_bits(grid_tag), bool)
        counts = sorted(struct.unpack("<I", area.find("CellsCount").data)[0] for area in areas.children)
        labels, herd_count = ndimage.label(grid)
        sizes = sorted(int((labels == i).sum()) for i in range(1, herd_count + 1))
        share = 100 * grid.sum() / max(1, self.land.sum())
        if not counts:
            return ["WARN no herd areas: the island has no wildlife"]
        return [
            ("OK" if counts == sizes else "FAIL")
            + f" herds: {len(counts)} areas over {int(grid.sum())} tiles ({share:.0f}% of the land, "
            f"vanilla 27-29%), their cell counts {'match' if counts == sizes else 'do NOT match'} the grid"
        ]

    def check_rivers(self) -> list[str]:
        """River banks against what a bridge needs: the island's build area on both sides of a
        crossing of at most ``BRIDGE_MAX`` tiles (117's ``MeshGraphBalancing/BridgeLengthMax``).
        Vanilla river islands keep 85-90% of their bank tiles in the area and 73-75% of their
        crossings bridgeable."""
        from scipy import ndimage

        world = self.game_session_manager.path("WorldManager")
        river = np.asarray(read_bits(world.find("RiverGrid")), bool)
        if not river.any():
            return ["INFO no river on this island"]
        build = self.island_area()[: river.shape[0], : river.shape[1]] == AREA_ISLAND
        bridgeable_count = outside_count = too_long_count = 0
        for axis in (0, 1):
            river_axis, build_axis = (river, build) if axis == 0 else (river.T, build.T)
            for z in range(river_axis.shape[0]):
                x = 0
                while x < river_axis.shape[1]:
                    if river_axis[z, x]:
                        start_x = x
                        while x < river_axis.shape[1] and river_axis[z, x]:
                            x += 1
                        if 0 < start_x < x < river_axis.shape[1]:
                            if x - start_x > BRIDGE_MAX:
                                too_long_count += 1
                            elif build_axis[z, start_x - 1] and build_axis[z, x]:
                                bridgeable_count += 1
                            else:
                                outside_count += 1
                    else:
                        x += 1
        total = bridgeable_count + outside_count + too_long_count or 1
        bank = ndimage.binary_dilation(river) & ~river & self.land[: river.shape[0], : river.shape[1]]
        banks = 100 * build[bank].mean() if bank.any() else 0.0
        report = [
            ("OK" if banks >= 80 else "WARN")
            + f" river banks: {int(bank.sum())} tiles, {banks:.0f}% in the island area (vanilla 85-90%)"
        ]
        report.append(
            ("OK" if 100 * bridgeable_count / total >= 70 else "WARN")
            + f" river crossings: {total}, {100 * bridgeable_count / total:.0f}% bridgeable, "
            f"{100 * outside_count / total:.0f}% with a bank outside the area, "
            f"{100 * too_long_count / total:.0f}% wider than {BRIDGE_MAX} tiles (vanilla 73-75%)"
        )
        open_sea = border_connected((~self.land[: river.shape[0], : river.shape[1]]) & ~river)
        if open_sea.any():
            distance = ndimage.distance_transform_edt(~open_sea)
            near = float(distance[river].min())
            report.append(
                ("OK" if near >= 2 else "WARN")
                + f" river grid stops {near:.0f} tiles from the open sea (117's own rivers are inland waters, 18+ tiles)"
            )
        return report
