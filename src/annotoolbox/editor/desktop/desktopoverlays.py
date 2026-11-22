"""What the PC editor's 3D view draws over the island besides the brush ring and the gizmo (a mixin of
``desktopview.CIslandControls``): the selection's box, the water network's lines and handles, the blocked path cells
and the checks' marks, as (mode, vertices) rows for ``desktoprender.CRenderer.draw_overlays``."""

from __future__ import annotations

import math

import numpy as np

from ...render.modellibrary import instance_matrices
from .. import footprints, placedrules
from ..editsession import SEntry
from .desktopgeometry import outline, ribbon

SHAPE_COLOURS = {"build": (0.95, 0.3, 0.25), "camera": (0.3, 0.55, 1.0)}  # blocker shapes: red, blue
BOX_EDGES = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))


def box_corners(low, high) -> np.ndarray:
    """The eight corners of the box ``lo``..``hi``, in the order ``BOX_EDGES`` counts."""
    return np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])


def box_rows(corners, rgba) -> list[tuple]:
    """The twelve edges of a box (``box_corners``) as line rows in the colour ``rgba``."""
    return [(*corners[k], *rgba) for start_index, end_index in BOX_EDGES for k in (start_index, end_index)]


class COverlayDraw:
    """The overlay rows of the view's tools; the view brings the session, the renderer and its ``checks``."""

    def _selection_box(self, entry: SEntry) -> list:
        batch = self.m_renderer.batch_of(entry) if self.m_renderer is not None else None
        if batch is None or batch.m_box is None:
            return []
        low, high = batch.m_box
        corners = box_corners(low, high)
        scale = np.asarray(entry.scale if isinstance(entry.scale, list) else [entry.scale] * 3, "f4")[None]
        matrix = instance_matrices(
            np.asarray([entry.position], "f4"), np.asarray([entry.rotation], "f4"), scale, self.m_session.m_size_tiles
        )[0]
        world_corners = (matrix[:3, :3] @ corners.T).T + matrix[:3, 3]
        return [("lines", np.asarray(box_rows(world_corners, self._rgba("select")), "f4"))]

    def _footprint_under(self, entry: SEntry) -> list:
        """The footprint of a selected building on the ground: in the selection's colour, red where the game's rules say
        it may not stand there (the live check, the building itself left out)."""
        session = self.m_session
        polygons = (session.m_island.get("footprints") or {}).get(entry.cfg) if entry.cfg else None
        if not polygons or entry.kind != "object":
            return []
        kinds = (session.m_island.get("terrain") or {}).get(str(entry.base.get("guid")), ())
        problems = placedrules.check_place(session, polygons, entry.position, entry.rotation, kinds, skip=entry)
        colour = (1.0, 0.25, 0.2, 1.0) if problems else self._rgba("select")
        return outline(
            footprints.world(polygons, entry.position, entry.rotation), self.to_view, session.height_at, colour
        )

    def _water_overlay(self) -> list:
        session, rows, points = self.m_session, [], []
        for k in range(len(session.m_water_net["rivers"])):
            river = session.river(k)
            view_points = [self.to_view(point) for point in river["points"]]
            for start, end in zip(view_points[:-1], view_points[1:], strict=False):
                rows += [(*start, 0.5, 0.83, 1, 0.9), (*end, 0.5, 0.83, 1, 0.9)]
            edges, _ = ribbon(view_points, river["widths"], 0.05)  # the river's width: its banks either side
            for side in (0, 1):
                side_edges = edges[side::2]
                for start, end in zip(side_edges[:-1], side_edges[1:], strict=False):
                    rows += [(*start, 0.5, 0.83, 1, 0.45), (*end, 0.5, 0.83, 1, 0.45)]
            for i, point in enumerate(view_points):
                is_selected = self.m_water_sel == {"river": k, "point": i}
                points.append((*point, *(self._rgba("select") if is_selected else (1, 1, 1, 1))))
        for k, lake in enumerate(session.m_water_net["lakes"]):
            centre = np.mean(np.asarray(lake["points"], float)[:, :2], axis=0)
            is_selected = self.m_water_sel == {"lake": k}
            points.append(
                (
                    *self.to_view([centre[0], session.lake_level(k), centre[1]]),
                    *(self._rgba("select") if is_selected else (1, 0.62, 0.11, 1)),
                )
            )
        for k in self._outline_lakes():
            view_points = self._outline_view(k)
            for start, end in zip(
                view_points, [*view_points[1:], *view_points[:1]], strict=True
            ):  # (an outline without points draws nothing)
                rows += [(*start, 1, 0.62, 0.11, 0.6), (*end, 1, 0.62, 0.11, 0.6)]
            for i, point in enumerate(view_points):
                is_selected = self.m_water_sel == {"outline": k, "point": i}
                points.append((*point, *(self._rgba("select") if is_selected else (1, 0.8, 0.45, 1))))
        for k in range(len(session.m_water_net.get("coasts", []))):
            view_points = [self._on_ground(point) for point in session.m_path.coast(k)["points"]]
            for start, end in zip(view_points[:-1], view_points[1:], strict=False):
                rows += [(*start, 1, 0.62, 0.11, 0.9), (*end, 1, 0.62, 0.11, 0.9)]
            for i, point in enumerate(view_points):
                points.append(
                    (
                        *point,
                        *(self._rgba("select") if self.m_water_sel == {"coast": k, "point": i} else (1, 0.62, 0.11, 1)),
                    )
                )
        self._shape_overlay(rows, points)
        return [("lines", np.asarray(rows, "f4").reshape(-1, 7)), ("points", np.asarray(points, "f4").reshape(-1, 7))]

    def _shape_overlay(self, rows: list, points: list) -> None:
        """The blocker shapes as closed outlines with a handle at each point (build red, camera blue)."""
        session = self.m_session
        if not (session.m_water_net or {}).get("blockers"):
            return
        for kind, colour in SHAPE_COLOURS.items():
            for k, shape in enumerate(session.m_path.shapes(kind)):
                view_points = [self._on_ground(point) for point in shape["points"]]
                for start, end in zip(view_points, [*view_points[1:], *view_points[:1]], strict=True):
                    rows += [(*start, *colour, 0.8), (*end, *colour, 0.8)]
                for i, point in enumerate(view_points):
                    is_selected = self.m_water_sel == {"shape": kind, "k": k, "point": i}
                    points.append((*point, *(self._rgba("select") if is_selected else (*colour, 1))))

    def _outline_lakes(self) -> range:
        """The lakes whose outline the water tool edits: those of a new-engine island (2070's own lake file keeps
        levels only)."""
        session = self.m_session
        return range(0 if session.m_anno2070_editing or not session.m_water_net else len(session.m_water_net["lakes"]))

    def _outline_view(self, k: int) -> list[np.ndarray]:
        """A lake's outline points at the lake's level, in view space."""
        level = self.m_session.lake_level(k)
        return [self.to_view([x, level, z]) for x, z in self.m_session.lake_outline(k)]

    def _on_ground(self, point) -> np.ndarray:
        """A coast point (x, z) on the terrain, in view space."""
        return self.to_view([point[0], self.m_session.height_at(point[0], point[1]) + 0.1, point[1]])

    def _blocker_points(self) -> np.ndarray:
        """The blocked path cells as red points on the ground."""
        session = self.m_session
        blocked_z, blocked_x = np.nonzero(session.m_path.m_blocked)
        tile_x, tile_z = (blocked_x + 0.5) / 2, (blocked_z + 0.5) / 2
        heights = session.m_heights[
            np.clip(np.rint(tile_z / session.m_step_tiles).astype(int), 0, session.m_width_samples - 1),
            np.clip(np.rint(tile_x / session.m_step_tiles).astype(int), 0, session.m_width_samples - 1),
        ]
        points = np.stack(
            [
                session.m_size_tiles - tile_x,
                heights + 0.15,
                tile_z,
                np.ones_like(tile_x),
                0.25 * np.ones_like(tile_x),
                0.2 * np.ones_like(tile_x),
                0.8 * np.ones_like(tile_x),
            ],
            axis=1,
        )
        return points.astype("f4")

    def water_handles(self) -> list[tuple[dict, np.ndarray]]:
        session, out = self.m_session, []
        if not session.m_water_net:
            return out
        for k in range(len(session.m_water_net["rivers"])):
            for i, point in enumerate(session.river(k)["points"]):
                out.append(({"river": k, "point": i}, self.to_view(point)))
        for k, lake in enumerate(session.m_water_net["lakes"]):
            centre = np.mean(np.asarray(lake["points"], float)[:, :2], axis=0)
            out.append(({"lake": k}, self.to_view([centre[0], session.lake_level(k), centre[1]])))
        for k in self._outline_lakes():
            out += [({"outline": k, "point": i}, point) for i, point in enumerate(self._outline_view(k))]
        for k in range(len(session.m_water_net.get("coasts", []))):
            for i, point in enumerate(session.m_path.coast(k)["points"]):
                out.append(({"coast": k, "point": i}, self._on_ground(point)))
        for kind in SHAPE_COLOURS if session.m_water_net.get("blockers") else ():
            for k, shape in enumerate(session.m_path.shapes(kind)):
                out += [
                    ({"shape": kind, "k": k, "point": i}, self._on_ground(point))
                    for i, point in enumerate(shape["points"])
                ]
        return out

    def _check_overlays(self) -> list:
        out, session = [], self.m_session
        falls = self.m_checks.get("falls")
        if falls:
            rows, dots = [], []
            for fall in falls:
                if not fall.get("clips"):
                    continue
                start, end = self.to_view(fall["box"][0]), self.to_view(fall["box"][1])
                low, high = np.minimum(start, end), np.maximum(start, end)
                rows += box_rows(box_corners(low, high), (1, 0.19, 0.19, 1))
                dots += [(*self.to_view(point), 1, 0.19, 0.19, 1) for point in fall.get("points", [])]
            out += [("lines", np.asarray(rows, "f4").reshape(-1, 7)), ("points", np.asarray(dots, "f4").reshape(-1, 7))]
        slots = self.m_checks.get("slots")
        if slots:
            corner_heights = session.corner_heights()
            dots = []
            for slot in slots["slots"]:
                for (x, z), colour in [(point, (0.3, 0.9, 0.3, 0.9)) for point in slot["bank"]] + [
                    (point, (1, 0.2, 0.2, 1)) for point in slot["off"]
                ]:
                    dots.append((*self.to_view([x, corner_heights[z, x] + 0.1, z]), *colour))
                if not slot["ok"]:
                    point = self.to_view(slot["position"])
                    angles = np.linspace(0, 2 * math.pi, 33)
                    ring = [
                        (point[0] + 4 * math.cos(angle), point[1] + 0.3, point[2] + 4 * math.sin(angle), 1, 0.2, 0.2, 1)
                        for angle in angles
                    ]
                    out.append(
                        (
                            "lines",
                            np.asarray(
                                [
                                    vertex
                                    for start, end in zip(ring[:-1], ring[1:], strict=True)
                                    for vertex in (start, end)
                                ],
                                "f4",
                            ),
                        )
                    )
            out.append(("points", np.asarray(dots, "f4").reshape(-1, 7)))
        return out
