"""What the PC editor's 3D view draws over the island besides the brush ring and the gizmo (a mixin of
``desktopview.IslandControls``): the selection's box, the water network's lines and handles, the blocked path cells
and the checks' marks, as (mode, vertices) rows for ``desktoprender.Renderer.draw_overlays``."""

from __future__ import annotations

import math

import numpy as np

from ...render.modellibrary import instance_matrices
from .. import footprints, placedrules
from ..editsession import Entry
from .desktopgeometry import outline, ribbon

SHAPE_COLOURS = {"build": (0.95, 0.3, 0.25), "camera": (0.3, 0.55, 1.0)}  # blocker shapes: red, blue


class OverlayDraw:
    """The overlay rows of the view's tools; the view brings the session, the renderer and its ``checks``."""

    def _selection_box(self, e: Entry) -> list:
        b = self.renderer.batch_of(e) if self.renderer is not None else None
        if b is None or b.box is None:
            return []
        lo, hi = b.box
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        sc = np.asarray(e.scale if isinstance(e.scale, list) else [e.scale] * 3, "f4")[None]
        m = instance_matrices(np.asarray([e.position], "f4"), np.asarray([e.rotation], "f4"), sc, self.session.size)[0]
        w = (m[:3, :3] @ corners.T).T + m[:3, 3]
        edges = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)]
        rows = [(*w[k], *self._rgba("select")) for a, b2 in edges for k in (a, b2)]
        return [("lines", np.asarray(rows, "f4"))]

    def _footprint_under(self, e: Entry) -> list:
        """The footprint of a selected building on the ground: in the selection's colour, red where the game's rules say
        it may not stand there (the live check, the building itself left out)."""
        s = self.session
        polygons = (s.island.get("footprints") or {}).get(e.cfg) if e.cfg else None
        if not polygons or e.kind != "object":
            return []
        kinds = (s.island.get("terrain") or {}).get(str(e.base.get("guid")), ())
        problems = placedrules.check_place(s, polygons, e.position, e.rotation, kinds, skip=e)
        colour = (1.0, 0.25, 0.2, 1.0) if problems else self._rgba("select")
        return outline(footprints.world(polygons, e.position, e.rotation), self.to_view, s.height_at, colour)

    def _water_overlay(self) -> list:
        s, rows, pts = self.session, [], []
        for k in range(len(s.water_net["rivers"])):
            r = s.river(k)
            v = [self.to_view(p) for p in r["points"]]
            for a, b in zip(v[:-1], v[1:], strict=False):
                rows += [(*a, 0.5, 0.83, 1, 0.9), (*b, 0.5, 0.83, 1, 0.9)]
            edges, _ = ribbon(v, r["widths"], 0.05)  # the river's width: its banks either side
            for side in (0, 1):
                e = edges[side::2]
                for a, b in zip(e[:-1], e[1:], strict=False):
                    rows += [(*a, 0.5, 0.83, 1, 0.45), (*b, 0.5, 0.83, 1, 0.45)]
            for i, p in enumerate(v):
                on = self.water_sel == {"river": k, "point": i}
                pts.append((*p, *(self._rgba("select") if on else (1, 1, 1, 1))))
        for k, lake in enumerate(s.water_net["lakes"]):
            c = np.mean(np.asarray(lake["points"], float)[:, :2], axis=0)
            on = self.water_sel == {"lake": k}
            pts.append(
                (*self.to_view([c[0], s.lake_level(k), c[1]]), *(self._rgba("select") if on else (1, 0.62, 0.11, 1)))
            )
        for k in self._outline_lakes():
            v = self._outline_view(k)
            for a, b in zip(v, [*v[1:], v[0]], strict=True):
                rows += [(*a, 1, 0.62, 0.11, 0.6), (*b, 1, 0.62, 0.11, 0.6)]
            for i, p in enumerate(v):
                on = self.water_sel == {"outline": k, "point": i}
                pts.append((*p, *(self._rgba("select") if on else (1, 0.8, 0.45, 1))))
        for k in range(len(s.water_net.get("coasts", []))):
            v = [self._on_ground(p) for p in s.path.coast(k)["points"]]
            for a, b in zip(v[:-1], v[1:], strict=False):
                rows += [(*a, 1, 0.62, 0.11, 0.9), (*b, 1, 0.62, 0.11, 0.9)]
            for i, p in enumerate(v):
                pts.append(
                    (*p, *(self._rgba("select") if self.water_sel == {"coast": k, "point": i} else (1, 0.62, 0.11, 1)))
                )
        self._shape_overlay(rows, pts)
        return [("lines", np.asarray(rows, "f4").reshape(-1, 7)), ("points", np.asarray(pts, "f4").reshape(-1, 7))]

    def _shape_overlay(self, rows: list, pts: list) -> None:
        """The blocker shapes as closed outlines with a handle at each point (build red, camera blue)."""
        s = self.session
        if not (s.water_net or {}).get("blockers"):
            return
        for kind, colour in SHAPE_COLOURS.items():
            for k, shape in enumerate(s.path.shapes(kind)):
                v = [self._on_ground(p) for p in shape["points"]]
                for a, b in zip(v, [*v[1:], v[0]], strict=True):
                    rows += [(*a, *colour, 0.8), (*b, *colour, 0.8)]
                for i, p in enumerate(v):
                    on = self.water_sel == {"shape": kind, "k": k, "point": i}
                    pts.append((*p, *(self._rgba("select") if on else (*colour, 1))))

    def _outline_lakes(self) -> range:
        """The lakes whose outline the water tool edits: those of a new-engine island (2070's own lake file keeps
        levels only)."""
        s = self.session
        return range(0 if s.anno2070_editing or not s.water_net else len(s.water_net["lakes"]))

    def _outline_view(self, k: int) -> list[np.ndarray]:
        """A lake's outline points at the lake's level, in view space."""
        level = self.session.lake_level(k)
        return [self.to_view([x, level, z]) for x, z in self.session.lake_outline(k)]

    def _on_ground(self, p) -> np.ndarray:
        """A coast point (x, z) on the terrain, in view space."""
        return self.to_view([p[0], self.session.height_at(p[0], p[1]) + 0.1, p[1]])

    def _blocker_points(self) -> np.ndarray:
        """The blocked path cells as red points on the ground."""
        s = self.session
        zs, xs = np.nonzero(s.path.blocked)
        tx, tz = (xs + 0.5) / 2, (zs + 0.5) / 2
        h = s.heights[
            np.clip(np.rint(tz / s.step).astype(int), 0, s.width - 1),
            np.clip(np.rint(tx / s.step).astype(int), 0, s.width - 1),
        ]
        pts = np.stack(
            [
                s.size - tx,
                h + 0.15,
                tz,
                np.ones_like(tx),
                0.25 * np.ones_like(tx),
                0.2 * np.ones_like(tx),
                0.8 * np.ones_like(tx),
            ],
            axis=1,
        )
        return pts.astype("f4")

    def water_handles(self) -> list[tuple[dict, np.ndarray]]:
        s, out = self.session, []
        if not s.water_net:
            return out
        for k in range(len(s.water_net["rivers"])):
            for i, p in enumerate(s.river(k)["points"]):
                out.append(({"river": k, "point": i}, self.to_view(p)))
        for k, lake in enumerate(s.water_net["lakes"]):
            c = np.mean(np.asarray(lake["points"], float)[:, :2], axis=0)
            out.append(({"lake": k}, self.to_view([c[0], s.lake_level(k), c[1]])))
        for k in self._outline_lakes():
            out += [({"outline": k, "point": i}, p) for i, p in enumerate(self._outline_view(k))]
        for k in range(len(s.water_net.get("coasts", []))):
            for i, p in enumerate(s.path.coast(k)["points"]):
                out.append(({"coast": k, "point": i}, self._on_ground(p)))
        for kind in SHAPE_COLOURS if s.water_net.get("blockers") else ():
            for k, shape in enumerate(s.path.shapes(kind)):
                out += [
                    ({"shape": kind, "k": k, "point": i}, self._on_ground(p)) for i, p in enumerate(shape["points"])
                ]
        return out

    def _check_overlays(self) -> list:
        out, s = [], self.session
        falls = self.checks.get("falls")
        if falls:
            rows, dots = [], []
            for f in falls:
                if not f.get("clips"):
                    continue
                a, b = self.to_view(f["box"][0]), self.to_view(f["box"][1])
                lo, hi = np.minimum(a, b), np.maximum(a, b)
                c = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
                for i, j in [
                    (0, 1),
                    (2, 3),
                    (4, 5),
                    (6, 7),
                    (0, 2),
                    (1, 3),
                    (4, 6),
                    (5, 7),
                    (0, 4),
                    (1, 5),
                    (2, 6),
                    (3, 7),
                ]:
                    rows += [(*c[i], 1, 0.19, 0.19, 1), (*c[j], 1, 0.19, 0.19, 1)]
                dots += [(*self.to_view(p), 1, 0.19, 0.19, 1) for p in f.get("points", [])]
            out += [("lines", np.asarray(rows, "f4").reshape(-1, 7)), ("points", np.asarray(dots, "f4").reshape(-1, 7))]
        slots = self.checks.get("slots")
        if slots:
            ch = s.corner_heights()
            dots = []
            for sl in slots["slots"]:
                for (x, z), c in [(p, (0.3, 0.9, 0.3, 0.9)) for p in sl["bank"]] + [
                    (p, (1, 0.2, 0.2, 1)) for p in sl["off"]
                ]:
                    dots.append((*self.to_view([x, ch[z, x] + 0.1, z]), *c))
                if not sl["ok"]:
                    p = self.to_view(sl["position"])
                    ts = np.linspace(0, 2 * math.pi, 33)
                    ring = [(p[0] + 4 * math.cos(t), p[1] + 0.3, p[2] + 4 * math.sin(t), 1, 0.2, 0.2, 1) for t in ts]
                    out.append(
                        (
                            "lines",
                            np.asarray([v for a, b in zip(ring[:-1], ring[1:], strict=True) for v in (a, b)], "f4"),
                        )
                    )
            out.append(("points", np.asarray(dots, "f4").reshape(-1, 7)))
        return out
