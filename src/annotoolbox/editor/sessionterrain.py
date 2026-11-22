"""The ground edits of an editing session (a mixin of ``editsession.CEditSession``): height strokes and their
undo, ground paint (the weight layers, the island's material set), and the forest's tiles and trees. The edit file keeps
``heights`` (corner differences), ``paint`` (strokes) and ``forest`` (tile -> planted or cleared)
(island-editor "The tools")."""

from __future__ import annotations

import math

import numpy as np

from ..infra.i18n import tr
from ..islands.forestpick import tile_pick
from . import brushes, terraintools
from .editchange import SChange, copy_json, num, union_box, yaw_quat

FOREST_LAND = 0.3  # no tree is planted below this height (117 grows none in the water)
MATERIAL_SLOTS = 32  # a material set's IDs are 5 bits


class CTerrainEdits:
    """The session's ground methods; ``CEditSession`` brings the state they use (heights, weights, forest, undo)."""

    # the ground: heights
    def _redraw(self, min_x: int, min_z: int, max_x: int, max_z: int) -> None:
        """The height samples over corners min_x..max_x, min_z..max_z: base plus the corner differences, bilinear."""
        samples_per_tile = self.m_samples_per_tile
        first_row, last_row = max(0, min_z * samples_per_tile), min(self.m_width_samples - 1, max_z * samples_per_tile)
        first_column, last_column = (
            max(0, min_x * samples_per_tile),
            min(self.m_width_samples - 1, max_x * samples_per_tile),
        )
        corner_z, corner_x = (
            np.arange(first_row, last_row + 1) / samples_per_tile,
            np.arange(first_column, last_column + 1) / samples_per_tile,
        )
        low_corner_z = np.minimum(self.m_size_tiles - 1, np.floor(corner_z)).astype(int)
        low_corner_x = np.minimum(self.m_size_tiles - 1, np.floor(corner_x)).astype(int)
        fraction_z, fraction_x = (corner_z - low_corner_z)[:, None], (corner_x - low_corner_x)[None, :]
        deltas = self.m_corner_deltas
        interpolated = (
            deltas[np.ix_(low_corner_z, low_corner_x)] * (1 - fraction_x)
            + deltas[np.ix_(low_corner_z, low_corner_x + 1)] * fraction_x
        ) * (1 - fraction_z) + (
            deltas[np.ix_(low_corner_z + 1, low_corner_x)] * (1 - fraction_x)
            + deltas[np.ix_(low_corner_z + 1, low_corner_x + 1)] * fraction_x
        ) * fraction_z
        self.m_heights[first_row : last_row + 1, first_column : last_column + 1] = (
            self.m_base_heights[first_row : last_row + 1, first_column : last_column + 1] + interpolated
        )

    def corner_heights(self) -> np.ndarray:
        """The current heights at the tile corners, (size + 1)^2, rows z."""
        return self.m_heights[:: self.m_samples_per_tile, :: self.m_samples_per_tile]

    def height_at(self, x: float, z: float) -> float:
        """The nearest height sample."""
        i = min(self.m_width_samples - 1, max(0, math.floor(x / self.m_step_tiles + 0.5)))
        j = min(self.m_width_samples - 1, max(0, math.floor(z / self.m_step_tiles + 0.5)))
        return float(self.m_heights[j, i])

    def heights_at(self, xs: np.ndarray, zs: np.ndarray) -> np.ndarray:
        """``height_at`` for arrays of points at once."""
        i = np.clip(np.floor(xs / self.m_step_tiles + 0.5).astype(int), 0, self.m_width_samples - 1)
        j = np.clip(np.floor(zs / self.m_step_tiles + 0.5).astype(int), 0, self.m_width_samples - 1)
        return self.m_heights[j, i].astype(float)

    def begin_height(self, mode: str, radius_tiles: float, strength: float, x: float, z: float) -> None:
        if self.m_readonly:
            return
        corner_values = self.corner_heights()
        target = float(
            corner_values[
                min(self.m_size_tiles, max(0, math.floor(z + 0.5))), min(self.m_size_tiles, max(0, math.floor(x + 0.5)))
            ]
        )
        self._m_brush = {"kind": "height", "mode": mode, "radius": radius_tiles, "strength": strength, "target": target}
        self._m_brush["start"] = (x, z)
        self._m_brush["touched"] = np.zeros_like(self.m_corner_deltas, bool)
        self._m_brush["before"] = self.m_corner_deltas.copy()
        self._m_brush["heights"] = dict(self.m_edits["heights"])
        self.height_to(x, z)

    def height_to(self, center_x: float, center_z: float) -> None:
        """One step of the height brush at a point: raise, lower, level or smooth inside the radius, by the falloff."""
        brush = self._m_brush
        if not brush or brush["kind"] != "height":
            return
        if brush["mode"] in terraintools.MODES:
            return terraintools.step(self, brush, center_x, center_z)
        radius_tiles, strength, max_corner = brush["radius"], brush["strength"], self.m_size_tiles
        min_z, max_z = max(0, math.floor(center_z - radius_tiles)), min(max_corner, math.ceil(center_z + radius_tiles))
        min_x, max_x = max(0, math.floor(center_x - radius_tiles)), min(max_corner, math.ceil(center_x + radius_tiles))
        grid_z, grid_x = np.mgrid[min_z : max_z + 1, min_x : max_x + 1]
        distance = np.hypot(grid_x - center_x, grid_z - center_z)
        inside = distance <= radius_tiles
        falloff_weight = brushes.falloff(distance, radius_tiles, self.m_falloff)
        current_heights = (
            self.m_base_heights[:: self.m_samples_per_tile, :: self.m_samples_per_tile][
                min_z : max_z + 1, min_x : max_x + 1
            ]
            + self.m_corner_deltas[min_z : max_z + 1, min_x : max_x + 1]
        )
        mode = brush["mode"]
        if mode == "raise":
            delta = 0.15 * strength * falloff_weight
        elif mode == "lower":
            delta = -0.15 * strength * falloff_weight
        elif mode == "flatten":
            delta = (brush["target"] - current_heights) * 0.25 * strength * falloff_weight
        else:  # smooth: interior corners towards the mean of their four neighbours
            full = self.m_base_heights[:: self.m_samples_per_tile, :: self.m_samples_per_tile] + self.m_corner_deltas
            pad = np.pad(full, 1, mode="edge")
            average = (
                pad[min_z + 1 : max_z + 2, min_x : max_x + 1]
                + pad[min_z + 1 : max_z + 2, min_x + 2 : max_x + 3]
                + pad[min_z : max_z + 1, min_x + 1 : max_x + 2]
                + pad[min_z + 2 : max_z + 3, min_x + 1 : max_x + 2]
            ) / 4
            interior = (grid_x > 0) & (grid_z > 0) & (grid_x < max_corner) & (grid_z < max_corner)
            delta = (average - current_heights) * 0.3 * strength * falloff_weight * interior
        self.m_corner_deltas[min_z : max_z + 1, min_x : max_x + 1] += delta.astype(np.float32)
        brush["touched"][min_z : max_z + 1, min_x : max_x + 1] |= inside
        self._redraw(min_x, min_z, max_x, max_z)
        self._notify(SChange(heights=(min_x, min_z, max_x, max_z)))

    def end_height(self) -> None:
        brush, self._m_brush = self._m_brush, None
        if not brush or brush["kind"] != "height":
            return
        heights = self.m_edits["heights"]
        for z, x in np.argwhere(brush["touched"]):
            key = f"{z},{x}"
            value = float(self.m_corner_deltas[z, x])
            if abs(value) < 0.001:
                heights.pop(key, None)
            else:
                heights[key] = num(round(value * 1000) / 1000)
        before, before_h = brush["before"], brush["heights"]
        after, after_h = self.m_corner_deltas.copy(), dict(heights)

        def put(deltas, stored_heights):
            self.m_corner_deltas[:] = deltas
            self.m_edits["heights"] = dict(stored_heights)
            self._redraw(0, 0, self.m_size_tiles, self.m_size_tiles)
            self._notify(SChange(heights=(0, 0, self.m_size_tiles, self.m_size_tiles)))

        self._push(tr("common.height"), lambda: put(before, before_h), lambda: put(after, after_h))

    # the ground: paint
    def attach_ground(self, ids: list[int], weights: np.ndarray, materials: list[dict]) -> None:
        """The ground's weight layers (``IslandEditor.ground()`` / ``ground_weights()``), and the
        island's material set; the saved strokes are laid over them again."""
        self.m_ground_ids = list(ids)
        self.m_weights = np.ascontiguousarray(weights, np.uint8)
        for material in materials:
            self.m_paint_materials.setdefault(material["id"], material)
        for stroke in self.m_edits.get("paint", []):
            material_id = stroke.get("material")
            # a material the palette has not registered yet (the ground and the palette load side by side) still
            # gets its layer: the view draws it plain until the material's look is known
            if (
                isinstance(material_id, int)
                and 0 <= material_id < MATERIAL_SLOTS
                and material_id not in self.m_ground_ids
            ):
                self._add_layer(self.m_paint_materials.get(material_id, {"id": material_id}))
            cover = None
            for k, point in enumerate(stroke["points"]):
                previous_point = stroke["points"][max(0, k - 1)]
                cover = self._stamp_line(stroke, previous_point, point, cover)

    def set_own_materials(self, own: list[dict]) -> None:
        """The island's material set (``IslandEditor.materials``: each with its ``order`` in the set's list)."""
        self.m_own_set = [
            (material["id"], material.get("name", str(material["id"])))
            for material in sorted(own, key=lambda material: material.get("order", material["id"]))
        ]
        for material in own:
            self.m_paint_materials.setdefault(material["id"], material)
        self.reorder_materials()
        self._notify(SChange(materials=True))  # the view loads the look of layers it drew plain until now

    def reorder_materials(self) -> None:
        """Every material's place in the draw order (``order``): the island's set as it is, the 117 materials
        the palette added placed by their family (``materialorder``, as the build lists them)."""
        from ..islands.materialorder import merged_order

        own = self.m_own_set
        if own is None:
            return
        added = {int(key) for key in self.m_edits["materials"]} - {material_id for material_id, _ in own}
        foreign = []
        for material_id in sorted(added):
            material = self.m_paint_materials.get(material_id, {})
            name = (
                material.get("name") or self.m_edits["materials"][str(material_id)].get("file", "").rsplit("/", 1)[-1]
            )
            foreign.append((material_id, name, material.get("rank", 0)))
        for key, material_id in enumerate(merged_order(own, foreign)):
            if material_id in self.m_paint_materials:
                self.m_paint_materials[material_id] = {**self.m_paint_materials[material_id], "order": key}

    def register_vanilla_material(self, material: dict, set_ids) -> int | None:
        """A 117 material for the palette: the free ID of the island's set it takes (``edits.materials``),
        given once; None when the set is full."""
        for slot_key, entry in self.m_edits["materials"].items():
            if entry.get("file") == material["file"]:
                self.m_paint_materials.setdefault(int(slot_key), {**material, "id": int(slot_key)})
                self.reorder_materials()
                return int(slot_key)
        used = set(set_ids) | {int(slot_key) for slot_key in self.m_edits["materials"]}
        free = next((slot_key for slot_key in range(MATERIAL_SLOTS) if slot_key not in used), None)
        if free is None:
            return None
        before = copy_json(self.m_edits["materials"])
        # a material of the other game of the generation keeps its game: the build brings it in (othermaterials)
        self.m_edits["materials"][str(free)] = {
            "file": material["file"],
            "set": material["set"],
            **({"game": material["game"]} if material.get("game") else {}),
        }
        after = copy_json(self.m_edits["materials"])
        self.m_paint_materials[free] = {**material, "id": free}

        def put(entry):
            self.m_edits["materials"] = copy_json(entry)
            self.reorder_materials()
            self._notify(SChange(materials=True))

        self.reorder_materials()
        self._push(tr("islands.material"), lambda: put(before), lambda: put(after))
        self._notify(SChange(materials=True))
        return free

    def _add_layer(self, material: dict) -> None:
        if self.m_weights is None or material["id"] in self.m_ground_ids or len(self.m_ground_ids) >= MATERIAL_SLOTS:
            return
        resolution = self.m_weights.shape[1]
        self.m_weights = np.concatenate([self.m_weights, np.zeros((1, resolution, resolution), np.uint8)])
        self.m_ground_ids.append(material["id"])
        self._notify(SChange(weights=(0, 0, resolution - 1, resolution - 1), materials=True))

    def _stamp(self, stroke: dict, x: float, z: float, cover: np.ndarray | None) -> np.ndarray | None:
        if self.m_weights is None or stroke["material"] not in self.m_ground_ids:
            return cover
        layer = self.m_ground_ids.index(stroke["material"])
        resolution = self.m_weights.shape[1]
        cells_per_tile, radius_tiles = resolution / self.m_size_tiles, stroke["radius"]
        if cover is None:
            cover = np.zeros((resolution, resolution), np.float32)
        min_x, max_x = (
            max(0, math.floor((x - radius_tiles) * cells_per_tile)),
            min(resolution - 1, math.ceil((x + radius_tiles) * cells_per_tile)),
        )
        min_z, max_z = (
            max(0, math.floor((z - radius_tiles) * cells_per_tile)),
            min(resolution - 1, math.ceil((z + radius_tiles) * cells_per_tile)),
        )
        if max_x < min_x or max_z < min_z:
            return cover
        grid_z, grid_x = np.mgrid[min_z : max_z + 1, min_x : max_x + 1]
        distance = np.hypot((grid_x + 0.5) / cells_per_tile - x, (grid_z + 0.5) / cells_per_tile - z) / radius_tiles
        falloff = stroke["strength"] * 0.5 * (1 + np.cos(np.pi * np.minimum(distance, 1)))
        before = cover[min_z : max_z + 1, min_x : max_x + 1]
        is_covered = (distance < 1) & (falloff > before) & (before < 0.999)
        if not is_covered.any():
            return cover
        keep = np.where(is_covered, (1 - falloff) / np.maximum(1 - before, 1e-6), 1.0)
        block = self.m_weights[:, min_z : max_z + 1, min_x : max_x + 1].astype(np.float32)
        others = np.floor(block * keep[None] + 0.5)
        others[layer] = 0
        total = others.sum(axis=0)
        others[layer] = np.maximum(0, 255 - total)
        out = np.where(is_covered[None], others, block)
        self.m_weights[:, min_z : max_z + 1, min_x : max_x + 1] = np.clip(out, 0, 255).astype(np.uint8)
        before[is_covered] = falloff[is_covered]
        self._m_wbox = union_box(self._m_wbox, (min_x, min_z, max_x, max_z))
        return cover

    def _stamp_line(self, stroke: dict, start, end, cover):
        steps = max(1, math.ceil(math.hypot(end[0] - start[0], end[1] - start[1]) / (stroke["radius"] / 4)))
        for k in range(1, steps + 1):
            cover = self._stamp(
                stroke, start[0] + (end[0] - start[0]) * k / steps, start[1] + (end[1] - start[1]) * k / steps, cover
            )
        return cover

    @property
    def paint_stroke(self) -> dict | None:
        """The paint stroke in progress (material, radius, strength, points), None between strokes."""
        brush = self._m_brush
        return brush["stroke"] if brush and brush.get("kind") == "paint" else None

    def begin_paint(self, material: int, radius_tiles: float, strength: float, x: float, z: float) -> None:
        if self.m_readonly:
            return
        known_material = self.m_paint_materials.get(material)
        if known_material is not None and material not in self.m_ground_ids:
            self._add_layer(known_material)
        self._m_wbox = None
        stroke = {"material": material, "radius": num(radius_tiles), "strength": num(strength), "points": []}
        self._m_brush = {
            "kind": "paint",
            "stroke": stroke,
            "cover": None,
            "before": None if self.m_weights is None else self.m_weights.copy(),
        }
        self.paint_to(x, z)

    def paint_to(self, x: float, z: float) -> None:
        brush = self._m_brush
        if not brush or brush["kind"] != "paint":
            return
        stroke, points = brush["stroke"], brush["stroke"]["points"]
        last = points[-1] if points else None
        if last and math.hypot(x - last[0], z - last[1]) < stroke["radius"] * 0.3:
            return
        points.append([num(round(x * 100) / 100), num(round(z * 100) / 100)])
        brush["cover"] = self._stamp_line(stroke, last or [x, z], [x, z], brush["cover"])
        if self._m_wbox:
            self._notify(SChange(weights=self._m_wbox))
            self._m_wbox = None

    def end_paint(self) -> None:
        brush, self._m_brush = self._m_brush, None
        if not brush or brush["kind"] != "paint" or not brush["stroke"]["points"]:
            return
        stroke = brush["stroke"]
        self.m_edits["paint"].append(stroke)
        before = brush["before"]
        after = None if self.m_weights is None else self.m_weights.copy()

        def weights(snapshot):
            if snapshot is not None and self.m_weights is not None and snapshot.shape == self.m_weights.shape:
                self.m_weights[:] = snapshot
                resolution = snapshot.shape[1]
                self._notify(SChange(weights=(0, 0, resolution - 1, resolution - 1)))

        def undo():
            self.m_edits["paint"].remove(stroke)
            weights(before)

        def redo():
            self.m_edits["paint"].append(stroke)
            weights(after)

        self._push(tr("islands.paint_lower"), undo, redo)

    # forest
    def forest_tiles(self) -> set[str]:
        tiles = set(self.m_forest_base)
        for tile, is_planted in self.m_edits.get("forest", {}).items():
            (tiles.add if is_planted else tiles.discard)(tile)
        return tiles

    def _set_tree(self, tile: str, is_planted: bool) -> None:
        entry = self.m_trees.get(tile)
        if entry is None and is_planted and self.m_forest_models:
            x, z = map(int, tile.split(","))
            pick = tile_pick(x, z)
            model_index = pick % len(self.m_forest_models)
            yaw = math.radians(pick % 360)
            item = {
                "key": f"tile:{tile}",
                "cfg": self.m_forest_models[model_index],
                "template": "Tree",
                "tile": tile,
                "position": [x + 0.5, self.height_at(x + 0.5, z + 0.5), z + 0.5],
                "rotation": yaw_quat(yaw),
                "scale": 1.0,
            }
            entry = self._add_entry("tree", item)
            entry.tile = tile
            self.m_trees[tile] = entry
        if entry is not None:
            entry.visible = is_planted

    def _forest_value(self, tile: str, value) -> None:
        if value is None:
            self.m_edits["forest"].pop(tile, None)
        else:
            self.m_edits["forest"][tile] = value

    def _plant(self, tile: str, is_planted: bool) -> None:
        """The edit file's line for tile ``k``: only what differs from the island's own forest."""
        if (tile in self.m_forest_base) == is_planted:
            self.m_edits["forest"].pop(tile, None)
        else:
            self.m_edits["forest"][tile] = 1 if is_planted else 0
        self._set_tree(tile, is_planted)

    def begin_forest(self, is_planting: bool, radius_tiles: float, x: float, z: float) -> None:
        if self.m_readonly:
            return
        self._m_brush = {
            "kind": "forest",
            "plant": is_planting,
            "radius": radius_tiles,
            "before": dict(self.m_edits["forest"]),
        }
        self._m_brush["cleared"] = []
        self.forest_to(x, z)

    def forest_to(self, center_x: float, center_z: float) -> None:
        brush = self._m_brush
        if not brush or brush["kind"] != "forest":
            return
        changed: set = set()
        if self.m_anno2070_editing and not brush["plant"]:  # 2070's trees are objects: clearing deletes them
            self._delete_trees(brush, center_x, center_z, changed)
        self._brush_tiles(brush, center_x, center_z, changed)
        self._notify(SChange(entries=changed, forest=True, structure=brush.pop("structure", False)))

    def _delete_trees(self, brush: dict, center_x: float, center_z: float, changed: set) -> None:
        """The brush's clearing in a 2070 island: each tree object within the radius is deleted (kept in ``b`` for undo)."""
        for entry in self.m_entries:
            if entry.kind != "object" or entry.base.get("template") != "Tree" or not entry.visible or not entry.alive:
                continue
            if math.hypot(entry.position[0] - center_x, entry.position[2] - center_z) > brush["radius"]:
                continue
            brush["cleared"].append((entry, self._state(entry), entry.added is not None))
            if entry.added is not None:
                self.m_edits["added_objects"].remove(entry.added)
                entry.alive = False
            else:
                self.m_edits["objects"][entry.key] = {"deleted": True}
                entry.visible = False
            changed.add(entry)

    def _brush_tiles(self, brush: dict, center_x: float, center_z: float, changed: set) -> None:
        """The forest tiles under the brush planted or cleared; a tree made new sets ``b["structure"]``."""
        radius_tiles, is_planting = brush["radius"], brush["plant"]
        for z in range(math.floor(center_z - radius_tiles), math.floor(center_z + radius_tiles) + 1):
            for x in range(math.floor(center_x - radius_tiles), math.floor(center_x + radius_tiles) + 1):
                if (
                    x < 0
                    or z < 0
                    or x >= self.m_size_tiles
                    or z >= self.m_size_tiles
                    or math.hypot(x + 0.5 - center_x, z + 0.5 - center_z) > radius_tiles
                ):
                    continue
                if is_planting and self.height_at(x + 0.5, z + 0.5) < FOREST_LAND:
                    continue
                tile = f"{x},{z}"
                had = tile in self.m_trees
                self._plant(tile, is_planting)
                if tile in self.m_trees:
                    changed.add(self.m_trees[tile])
                    if not had:
                        brush["structure"] = True

    def end_forest(self) -> None:
        """Close a forest stroke: one undo step over the tiles it changed and the objects it cleared."""
        brush, self._m_brush = self._m_brush, None
        if not brush or brush["kind"] != "forest":
            return
        before, after, cleared = brush["before"], dict(self.m_edits["forest"]), brush["cleared"]
        cleared_after = [(entry, self._state(entry)) for entry, _, _ in cleared]

        def sync():
            want = self.forest_tiles()
            for tile, entry in self.m_trees.items():
                entry.visible = tile in want
            for tile in want - set(self.m_trees):
                self._set_tree(tile, True)
            self._notify(
                SChange(
                    entries=set(self.m_trees.values()) | {entry for entry, *_ in cleared}, forest=True, structure=True
                )
            )

        def undo():
            self.m_edits["forest"] = dict(before)
            for entry, state, was_added in reversed(cleared):
                if was_added:
                    self.m_edits["added_objects"].append(entry.added)
                    entry.alive = True
                self._restore(entry, state)
            sync()

        def redo():
            self.m_edits["forest"] = dict(after)
            for (entry, state), (_, _, was_added) in zip(cleared_after, cleared, strict=True):
                if was_added:
                    self.m_edits["added_objects"].remove(entry.added)
                    entry.alive = False
                else:
                    self._restore(entry, state)
            sync()

        self._push(tr("islands.forest_lower"), undo, redo)
