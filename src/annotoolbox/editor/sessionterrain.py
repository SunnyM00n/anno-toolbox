"""The ground edits of an editing session (a mixin of ``editsession.EditSession``): height strokes and their
undo, ground paint (the weight layers, the island's material set), and the forest's tiles and trees. The edit file keeps
``heights`` (corner differences), ``paint`` (strokes) and ``forest`` (tile -> planted or cleared)
(island-editor "The tools")."""

from __future__ import annotations

import math

import numpy as np

from ..infra.i18n import tr
from ..islands.forestpick import tile_pick
from . import brushes, terraintools
from .editchange import Change, copy_json, num, union_box, yaw_quat

FOREST_LAND = 0.3  # no tree is planted below this height (117 grows none in the water)
MATERIAL_SLOTS = 32  # a material set's IDs are 5 bits


class TerrainEdits:
    """The session's ground methods; ``EditSession`` brings the state they use (heights, weights, forest, undo)."""

    # the ground: heights
    def _redraw(self, x0: int, z0: int, x1: int, z1: int) -> None:
        """The height samples over corners x0..x1, z0..z1: base plus the corner differences, bilinear."""
        n = self.spt
        j0, j1 = max(0, z0 * n), min(self.width - 1, z1 * n)
        i0, i1 = max(0, x0 * n), min(self.width - 1, x1 * n)
        tz, tx = np.arange(j0, j1 + 1) / n, np.arange(i0, i1 + 1) / n
        cz0 = np.minimum(self.size - 1, np.floor(tz)).astype(int)
        cx0 = np.minimum(self.size - 1, np.floor(tx)).astype(int)
        fz, fx = (tz - cz0)[:, None], (tx - cx0)[None, :]
        D = self.D
        d = (D[np.ix_(cz0, cx0)] * (1 - fx) + D[np.ix_(cz0, cx0 + 1)] * fx) * (1 - fz) + (
            D[np.ix_(cz0 + 1, cx0)] * (1 - fx) + D[np.ix_(cz0 + 1, cx0 + 1)] * fx
        ) * fz
        self.heights[j0 : j1 + 1, i0 : i1 + 1] = self.base_heights[j0 : j1 + 1, i0 : i1 + 1] + d

    def corner_heights(self) -> np.ndarray:
        """The current heights at the tile corners, (size + 1)^2, rows z."""
        return self.heights[:: self.spt, :: self.spt]

    def height_at(self, x: float, z: float) -> float:
        """The nearest height sample."""
        i = min(self.width - 1, max(0, math.floor(x / self.step + 0.5)))
        j = min(self.width - 1, max(0, math.floor(z / self.step + 0.5)))
        return float(self.heights[j, i])

    def heights_at(self, xs: np.ndarray, zs: np.ndarray) -> np.ndarray:
        """``height_at`` for arrays of points at once."""
        i = np.clip(np.floor(xs / self.step + 0.5).astype(int), 0, self.width - 1)
        j = np.clip(np.floor(zs / self.step + 0.5).astype(int), 0, self.width - 1)
        return self.heights[j, i].astype(float)

    def begin_height(self, mode: str, radius: float, strength: float, x: float, z: float) -> None:
        if self.readonly:
            return
        ch = self.corner_heights()
        target = float(ch[min(self.size, max(0, math.floor(z + 0.5))), min(self.size, max(0, math.floor(x + 0.5)))])
        self._brush = {"kind": "height", "mode": mode, "radius": radius, "strength": strength, "target": target}
        self._brush["start"] = (x, z)
        self._brush["touched"] = np.zeros_like(self.D, bool)
        self._brush["before"] = self.D.copy()
        self._brush["heights"] = dict(self.edits["heights"])
        self.height_to(x, z)

    def height_to(self, cx: float, cz: float) -> None:
        """One step of the height brush at a point: raise, lower, level or smooth inside the radius, by the falloff."""
        b = self._brush
        if not b or b["kind"] != "height":
            return
        if b["mode"] in terraintools.MODES:
            return terraintools.step(self, b, cx, cz)
        r, k, s = b["radius"], b["strength"], self.size
        zs, ze = max(0, math.floor(cz - r)), min(s, math.ceil(cz + r))
        xs, xe = max(0, math.floor(cx - r)), min(s, math.ceil(cx + r))
        zz, xx = np.mgrid[zs : ze + 1, xs : xe + 1]
        d = np.hypot(xx - cx, zz - cz)
        inside = d <= r
        w = brushes.falloff(d, r, self.falloff)
        cur = self.base_heights[:: self.spt, :: self.spt][zs : ze + 1, xs : xe + 1] + self.D[zs : ze + 1, xs : xe + 1]
        mode = b["mode"]
        if mode == "raise":
            delta = 0.15 * k * w
        elif mode == "lower":
            delta = -0.15 * k * w
        elif mode == "flatten":
            delta = (b["target"] - cur) * 0.25 * k * w
        else:  # smooth: interior corners towards the mean of their four neighbours
            full = self.base_heights[:: self.spt, :: self.spt] + self.D
            pad = np.pad(full, 1, mode="edge")
            avg = (
                pad[zs + 1 : ze + 2, xs : xe + 1]
                + pad[zs + 1 : ze + 2, xs + 2 : xe + 3]
                + pad[zs : ze + 1, xs + 1 : xe + 2]
                + pad[zs + 2 : ze + 3, xs + 1 : xe + 2]
            ) / 4
            interior = (xx > 0) & (zz > 0) & (xx < s) & (zz < s)
            delta = (avg - cur) * 0.3 * k * w * interior
        self.D[zs : ze + 1, xs : xe + 1] += delta.astype(np.float32)
        b["touched"][zs : ze + 1, xs : xe + 1] |= inside
        self._redraw(xs, zs, xe, ze)
        self._notify(Change(heights=(xs, zs, xe, ze)))

    def end_height(self) -> None:
        b, self._brush = self._brush, None
        if not b or b["kind"] != "height":
            return
        heights = self.edits["heights"]
        for z, x in np.argwhere(b["touched"]):
            key = f"{z},{x}"
            v = float(self.D[z, x])
            if abs(v) < 0.001:
                heights.pop(key, None)
            else:
                heights[key] = num(round(v * 1000) / 1000)
        before, before_h = b["before"], b["heights"]
        after, after_h = self.D.copy(), dict(heights)

        def put(D, h):
            self.D[:] = D
            self.edits["heights"] = dict(h)
            self._redraw(0, 0, self.size, self.size)
            self._notify(Change(heights=(0, 0, self.size, self.size)))

        self._push(tr("common.height"), lambda: put(before, before_h), lambda: put(after, after_h))

    # the ground: paint
    def attach_ground(self, ids: list[int], weights: np.ndarray, materials: list[dict]) -> None:
        """The ground's weight layers (``IslandEditor.ground()`` / ``ground_weights()``), and the
        island's material set; the saved strokes are laid over them again."""
        self.ground_ids = list(ids)
        self.weights = np.ascontiguousarray(weights, np.uint8)
        for m in materials:
            self.paint_materials.setdefault(m["id"], m)
        for st in self.edits.get("paint", []):
            mid = st.get("material")
            # a material the palette has not registered yet (the ground and the palette load side by side) still
            # gets its layer: the view draws it plain until the material's look is known
            if isinstance(mid, int) and 0 <= mid < MATERIAL_SLOTS and mid not in self.ground_ids:
                self._add_layer(self.paint_materials.get(mid, {"id": mid}))
            cover = None
            for k, p in enumerate(st["points"]):
                a = st["points"][max(0, k - 1)]
                cover = self._stamp_line(st, a, p, cover)

    def set_own_materials(self, own: list[dict]) -> None:
        """The island's material set (``IslandEditor.materials``: each with its ``order`` in the set's list)."""
        self.own_set = [
            (m["id"], m.get("name", str(m["id"]))) for m in sorted(own, key=lambda m: m.get("order", m["id"]))
        ]
        for m in own:
            self.paint_materials.setdefault(m["id"], m)
        self.reorder_materials()
        self._notify(Change(materials=True))  # the view loads the look of layers it drew plain until now

    def reorder_materials(self) -> None:
        """Every material's place in the draw order (``order``): the island's set as it is, the 117 materials
        the palette added placed by their family (``materialorder``, as the build lists them)."""
        from ..islands.materialorder import merged_order

        own = self.own_set
        if own is None:
            return
        added = {int(k) for k in self.edits["materials"]} - {mid for mid, _ in own}
        foreign = []
        for mid in sorted(added):
            m = self.paint_materials.get(mid, {})
            name = m.get("name") or self.edits["materials"][str(mid)].get("file", "").rsplit("/", 1)[-1]
            foreign.append((mid, name, m.get("rank", 0)))
        for k, mid in enumerate(merged_order(own, foreign)):
            if mid in self.paint_materials:
                self.paint_materials[mid] = {**self.paint_materials[mid], "order": k}

    def register_vanilla_material(self, m: dict, set_ids) -> int | None:
        """A 117 material for the palette: the free ID of the island's set it takes (``edits.materials``),
        given once; None when the set is full."""
        for k, v in self.edits["materials"].items():
            if v.get("file") == m["file"]:
                self.paint_materials.setdefault(int(k), {**m, "id": int(k)})
                self.reorder_materials()
                return int(k)
        used = set(set_ids) | {int(k) for k in self.edits["materials"]}
        free = next((k for k in range(MATERIAL_SLOTS) if k not in used), None)
        if free is None:
            return None
        before = copy_json(self.edits["materials"])
        # a material of the other game of the generation keeps its game: the build brings it in (othermaterials)
        self.edits["materials"][str(free)] = {
            "file": m["file"],
            "set": m["set"],
            **({"game": m["game"]} if m.get("game") else {}),
        }
        after = copy_json(self.edits["materials"])
        self.paint_materials[free] = {**m, "id": free}

        def put(v):
            self.edits["materials"] = copy_json(v)
            self.reorder_materials()
            self._notify(Change(materials=True))

        self.reorder_materials()
        self._push(tr("islands.material"), lambda: put(before), lambda: put(after))
        self._notify(Change(materials=True))
        return free

    def _add_layer(self, m: dict) -> None:
        if self.weights is None or m["id"] in self.ground_ids or len(self.ground_ids) >= MATERIAL_SLOTS:
            return
        n = self.weights.shape[1]
        self.weights = np.concatenate([self.weights, np.zeros((1, n, n), np.uint8)])
        self.ground_ids.append(m["id"])
        self._notify(Change(weights=(0, 0, n - 1, n - 1), materials=True))

    def _stamp(self, st: dict, x: float, z: float, cover: np.ndarray | None) -> np.ndarray | None:
        if self.weights is None or st["material"] not in self.ground_ids:
            return cover
        layer = self.ground_ids.index(st["material"])
        n = self.weights.shape[1]
        per, r = n / self.size, st["radius"]
        if cover is None:
            cover = np.zeros((n, n), np.float32)
        x0, x1 = max(0, math.floor((x - r) * per)), min(n - 1, math.ceil((x + r) * per))
        z0, z1 = max(0, math.floor((z - r) * per)), min(n - 1, math.ceil((z + r) * per))
        if x1 < x0 or z1 < z0:
            return cover
        jj, ii = np.mgrid[z0 : z1 + 1, x0 : x1 + 1]
        d = np.hypot((ii + 0.5) / per - x, (jj + 0.5) / per - z) / r
        c = st["strength"] * 0.5 * (1 + np.cos(np.pi * np.minimum(d, 1)))
        before = cover[z0 : z1 + 1, x0 : x1 + 1]
        go = (d < 1) & (c > before) & (before < 0.999)
        if not go.any():
            return cover
        keep = np.where(go, (1 - c) / np.maximum(1 - before, 1e-6), 1.0)
        block = self.weights[:, z0 : z1 + 1, x0 : x1 + 1].astype(np.float32)
        others = np.floor(block * keep[None] + 0.5)
        others[layer] = 0
        total = others.sum(axis=0)
        others[layer] = np.maximum(0, 255 - total)
        out = np.where(go[None], others, block)
        self.weights[:, z0 : z1 + 1, x0 : x1 + 1] = np.clip(out, 0, 255).astype(np.uint8)
        before[go] = c[go]
        self._wbox = union_box(self._wbox, (x0, z0, x1, z1))
        return cover

    def _stamp_line(self, st: dict, a, b, cover):
        steps = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / (st["radius"] / 4)))
        for k in range(1, steps + 1):
            cover = self._stamp(st, a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps, cover)
        return cover

    @property
    def paint_stroke(self) -> dict | None:
        """The paint stroke in progress (material, radius, strength, points), None between strokes."""
        b = self._brush
        return b["stroke"] if b and b.get("kind") == "paint" else None

    def begin_paint(self, material: int, radius: float, strength: float, x: float, z: float) -> None:
        if self.readonly:
            return
        m = self.paint_materials.get(material)
        if m is not None and material not in self.ground_ids:
            self._add_layer(m)
        self._wbox = None
        st = {"material": material, "radius": num(radius), "strength": num(strength), "points": []}
        self._brush = {
            "kind": "paint",
            "stroke": st,
            "cover": None,
            "before": None if self.weights is None else self.weights.copy(),
        }
        self.paint_to(x, z)

    def paint_to(self, x: float, z: float) -> None:
        b = self._brush
        if not b or b["kind"] != "paint":
            return
        st, pts = b["stroke"], b["stroke"]["points"]
        last = pts[-1] if pts else None
        if last and math.hypot(x - last[0], z - last[1]) < st["radius"] * 0.3:
            return
        pts.append([num(round(x * 100) / 100), num(round(z * 100) / 100)])
        b["cover"] = self._stamp_line(st, last or [x, z], [x, z], b["cover"])
        if self._wbox:
            self._notify(Change(weights=self._wbox))
            self._wbox = None

    def end_paint(self) -> None:
        b, self._brush = self._brush, None
        if not b or b["kind"] != "paint" or not b["stroke"]["points"]:
            return
        st = b["stroke"]
        self.edits["paint"].append(st)
        before = b["before"]
        after = None if self.weights is None else self.weights.copy()

        def weights(w):
            if w is not None and self.weights is not None and w.shape == self.weights.shape:
                self.weights[:] = w
                n = w.shape[1]
                self._notify(Change(weights=(0, 0, n - 1, n - 1)))

        def undo():
            self.edits["paint"].remove(st)
            weights(before)

        def redo():
            self.edits["paint"].append(st)
            weights(after)

        self._push(tr("islands.paint_lower"), undo, redo)

    # forest
    def forest_tiles(self) -> set[str]:
        tiles = set(self.forest_base)
        for k, v in self.edits.get("forest", {}).items():
            (tiles.add if v else tiles.discard)(k)
        return tiles

    def _set_tree(self, k: str, on: bool) -> None:
        e = self.trees.get(k)
        if e is None and on and self.forest_models:
            x, z = map(int, k.split(","))
            pick = tile_pick(x, z)
            v = pick % len(self.forest_models)
            yaw = math.radians(pick % 360)
            item = {
                "key": f"tile:{k}",
                "cfg": self.forest_models[v],
                "template": "Tree",
                "tile": k,
                "position": [x + 0.5, self.height_at(x + 0.5, z + 0.5), z + 0.5],
                "rotation": yaw_quat(yaw),
                "scale": 1.0,
            }
            e = self._add_entry("tree", item)
            e.tile = k
            self.trees[k] = e
        if e is not None:
            e.visible = on

    def _forest_value(self, k: str, v) -> None:
        if v is None:
            self.edits["forest"].pop(k, None)
        else:
            self.edits["forest"][k] = v

    def _plant(self, k: str, on: bool) -> None:
        """The edit file's line for tile ``k``: only what differs from the island's own forest."""
        if (k in self.forest_base) == on:
            self.edits["forest"].pop(k, None)
        else:
            self.edits["forest"][k] = 1 if on else 0
        self._set_tree(k, on)

    def begin_forest(self, plant: bool, radius: float, x: float, z: float) -> None:
        if self.readonly:
            return
        self._brush = {"kind": "forest", "plant": plant, "radius": radius, "before": dict(self.edits["forest"])}
        self._brush["cleared"] = []
        self.forest_to(x, z)

    def forest_to(self, cx: float, cz: float) -> None:
        b = self._brush
        if not b or b["kind"] != "forest":
            return
        changed: set = set()
        if self.anno2070_editing and not b["plant"]:  # 2070's trees are objects: clearing deletes them
            self._delete_trees(b, cx, cz, changed)
        self._brush_tiles(b, cx, cz, changed)
        self._notify(Change(entries=changed, forest=True, structure=b.pop("structure", False)))

    def _delete_trees(self, b: dict, cx: float, cz: float, changed: set) -> None:
        """The brush's clearing in a 2070 island: each tree object within the radius is deleted (kept in ``b`` for undo)."""
        for e in self.entries:
            if e.kind != "object" or e.base.get("template") != "Tree" or not e.visible or not e.alive:
                continue
            if math.hypot(e.position[0] - cx, e.position[2] - cz) > b["radius"]:
                continue
            b["cleared"].append((e, self._state(e), e.added is not None))
            if e.added is not None:
                self.edits["added_objects"].remove(e.added)
                e.alive = False
            else:
                self.edits["objects"][e.key] = {"deleted": True}
                e.visible = False
            changed.add(e)

    def _brush_tiles(self, b: dict, cx: float, cz: float, changed: set) -> None:
        """The forest tiles under the brush planted or cleared; a tree made new sets ``b["structure"]``."""
        r, on = b["radius"], b["plant"]
        for z in range(math.floor(cz - r), math.floor(cz + r) + 1):
            for x in range(math.floor(cx - r), math.floor(cx + r) + 1):
                if x < 0 or z < 0 or x >= self.size or z >= self.size or math.hypot(x + 0.5 - cx, z + 0.5 - cz) > r:
                    continue
                if on and self.height_at(x + 0.5, z + 0.5) < FOREST_LAND:
                    continue
                k = f"{x},{z}"
                had = k in self.trees
                self._plant(k, on)
                if k in self.trees:
                    changed.add(self.trees[k])
                    if not had:
                        b["structure"] = True

    def end_forest(self) -> None:
        """Close a forest stroke: one undo step over the tiles it changed and the objects it cleared."""
        b, self._brush = self._brush, None
        if not b or b["kind"] != "forest":
            return
        before, after, cleared = b["before"], dict(self.edits["forest"]), b["cleared"]
        cleared_after = [(e, self._state(e)) for e, _, _ in cleared]

        def sync():
            want = self.forest_tiles()
            for k, e in self.trees.items():
                e.visible = k in want
            for k in want - set(self.trees):
                self._set_tree(k, True)
            self._notify(
                Change(entries=set(self.trees.values()) | {e for e, *_ in cleared}, forest=True, structure=True)
            )

        def undo():
            self.edits["forest"] = dict(before)
            for e, st, was_added in reversed(cleared):
                if was_added:
                    self.edits["added_objects"].append(e.added)
                    e.alive = True
                self._restore(e, st)
            sync()

        def redo():
            self.edits["forest"] = dict(after)
            for (e, st), (_, _, was_added) in zip(cleared_after, cleared, strict=True):
                if was_added:
                    self.edits["added_objects"].remove(e.added)
                    e.alive = False
                else:
                    self._restore(e, st)
            sync()

        self._push(tr("islands.forest_lower"), undo, redo)
