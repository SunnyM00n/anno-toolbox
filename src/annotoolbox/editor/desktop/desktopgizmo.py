"""The gizmo of the PC editor's 3D view: its handles and their drawing (``CGizmo``), the quaternion helpers the
view and the properties panel share, and the mouse handling that moves, turns or scales the selection with it
(``CGizmoControls``, a mixin of ``desktopview.CIslandControls``). Every change goes through the edit session."""

from __future__ import annotations

import math

import numpy as np

from ...infra.i18n import tr
from ...models import meshxform
from ...models.skinning import quat_matrix
from ...render import picking


# quaternions (x, y, z, w): models.meshxform
def to_view_quat(quaternion) -> np.ndarray:
    """Anno's turn in the mirrored view (and back: it is its own inverse)."""
    return np.array([quaternion[0], -quaternion[1], -quaternion[2], quaternion[3]], float)


def euler_of(quaternion) -> tuple[float, float, float]:
    """Turn (about y), then tilts about the model's x and z, in degrees (Anno's axes)."""
    matrix = quat_matrix(quaternion)
    tilt_x = math.asin(max(-1.0, min(1.0, -matrix[1, 2])))
    if abs(matrix[1, 2]) < 0.9999:
        yaw, tilt_z = math.atan2(matrix[0, 2], matrix[2, 2]), math.atan2(matrix[1, 0], matrix[1, 1])
    else:
        yaw, tilt_z = math.atan2(-matrix[2, 0], matrix[0, 0]), 0.0
    return math.degrees(yaw), math.degrees(tilt_x), math.degrees(tilt_z)


def quat_of(yaw_deg: float, tilt_x_deg: float, tilt_z_deg: float) -> list[float]:
    """The inverse of ``euler_of``."""
    quaternion = meshxform.euler_quat(0.0, 0.0, 0.0)
    for axis, degrees in (((0, 1, 0), yaw_deg), ((1, 0, 0), tilt_x_deg), ((0, 0, 1), tilt_z_deg)):
        quaternion = meshxform.qmul(quaternion, meshxform.qaxis(axis, math.radians(degrees)))
    return list(meshxform.qnormalize(quaternion))


class CGizmo:
    """Move (three axes and the ground plane), turn or tilt (rings) and scale (three axes), in the view's space."""

    COLORS = {0: (0.95, 0.3, 0.3, 1), 1: (0.4, 0.9, 0.3, 1), 2: (0.35, 0.55, 1.0, 1)}

    def __init__(self) -> None:
        self.m_mode = "translate"
        self.m_tilt = False
        self.m_axes_only: tuple | None = None  # the water tool's lake: only up and down

    def handles(self, center, view_quaternion, length_m: float) -> list[dict]:
        axes = [np.eye(3)[k] for k in range(3)]
        if self.m_mode == "rotate" and self.m_tilt or self.m_mode == "scale":
            axes = [np.array(meshxform.qrotate(tuple(view_quaternion), tuple(axis))) for axis in axes]
        out = []
        if self.m_mode == "rotate":
            for k in (0, 2) if self.m_tilt else (1,):
                axis = axes[k]
                tangent = np.cross(axis, [0, 1, 0] if abs(axis[1]) < 0.9 else [1, 0, 0])
                tangent /= np.linalg.norm(tangent)
                bitangent = np.cross(axis, tangent)
                angles = np.linspace(0, 2 * math.pi, 49)
                ring = center + length_m * (np.cos(angles)[:, None] * tangent + np.sin(angles)[:, None] * bitangent)
                out.append({"axis": k, "dir": axis, "ring": ring})
            return out
        for k in range(3):
            if self.m_axes_only is not None and k not in self.m_axes_only:
                continue
            out.append({"axis": k, "dir": axes[k], "seg": (np.asarray(center), center + axes[k] * length_m)})
        if self.m_mode == "translate" and self.m_axes_only is None:
            out.append({"axis": "plane", "dir": np.array([0, 1.0, 0]), "seg": (np.asarray(center), np.asarray(center))})
        return out

    def lines(self, handles: list[dict], hot, active=(1, 0.85, 0.25, 1)) -> list:
        rows = []
        for handle in handles:
            color = active if handle is hot else self.COLORS.get(handle["axis"], (1, 1, 1, 1))
            if "ring" in handle:
                ring = handle["ring"]
                for start, end in zip(ring[:-1], ring[1:], strict=True):
                    rows += [(*start, *color), (*end, *color)]
            elif handle["axis"] != "plane":
                start, end = handle["seg"]
                rows += [(*start, *color), (*end, *color)]
        return rows

    def points(self, handles: list[dict], hot, active=(1, 0.85, 0.25, 1)) -> list:
        rows = []
        for handle in handles:
            if "seg" in handle:
                color = active if handle is hot else self.COLORS.get(handle["axis"], (1, 1, 1, 1))
                rows.append((*handle["seg"][1], *color))
        return rows


class CGizmoControls:
    """The view's gizmo: what it moves, where its handles are, and the drag; the view brings the camera,
    the session and the selection."""

    def _gizmo_target(self):
        """(view position, view quaternion, kind) of what the gizmo moves, or None."""
        if (
            self.m_tool == "select"
            and self.m_selected is not None
            and self.m_selected.shown
            and self.m_selected.kind != "tree"
        ):
            if self.m_session.m_readonly:
                return None
            entry = self.m_selected
            return self.to_view(entry.position), to_view_quat(entry.rotation), "entry"
        if self.m_tool == "water" and self.m_water_sel is not None:
            for handle, position in self.water_handles():
                if handle == self.m_water_sel:
                    return position, np.array([0, 0, 0, 1.0]), "water"
        return None

    def _gizmo_handles(self) -> list[dict]:
        target = self._gizmo_target()
        if target is None:
            return []
        center, quaternion, _ = target
        length = float(np.linalg.norm(self.m_camera.eye() - center)) * 0.15
        if self.m_tool == "water":
            self.m_gizmo.m_mode = "translate"
        return self.m_gizmo.handles(center, quaternion, length)

    def _hit_handle(self, cursor) -> dict | None:
        best, best_distance = None, 9.0
        for handle in self._gizmo_handles():
            points = handle["ring"] if "ring" in handle else np.linspace(handle["seg"][0], handle["seg"][1], 12)
            if handle.get("axis") == "plane":
                x, y, _ = self.project(handle["seg"][0])
                distance = math.hypot(x - cursor.x(), y - cursor.y()) * 0.8
            else:
                screen_points = np.array([self.project(point)[:2] for point in points])
                distance = float(np.min(np.hypot(screen_points[:, 0] - cursor.x(), screen_points[:, 1] - cursor.y())))
            if distance < best_distance:
                best, best_distance = handle, distance
        return best

    def set_gizmo(self, mode: str, tilt: bool = False) -> None:
        self.m_gizmo.m_mode, self.m_gizmo.m_tilt = mode, tilt
        self.update()

    # the gizmo
    def _start_gizmo(self, handle: dict, cursor) -> None:
        center, quaternion, kind = self._gizmo_target()
        origin, direction = self.ray(cursor)
        start = {"handle": handle, "center": center, "quat": quaternion, "kind": kind, "pos": cursor}
        # the drag stays what its handle is for: W, E and R change the gizmo for the next one
        start["mode"], start["tilt"] = (self.m_gizmo.m_mode if kind == "entry" else "translate"), self.m_gizmo.m_tilt
        if kind == "entry":
            entry = self.m_selected
            start["entry"], start["undo"] = entry, self.m_session.begin_transform(self.m_group.moving(entry))
            start["scale"] = list(entry.scale) if isinstance(entry.scale, list) else entry.scale
        else:
            start["water"] = self.m_session.water_snapshot()
        if handle["axis"] == "plane":
            start["anchor"] = picking.ray_plane((origin, direction), center, np.array([0, 1.0, 0]))
        elif "ring" in handle:
            hit = picking.ray_plane((origin, direction), center, handle["dir"])
            start["anchor"] = None if hit is None else hit - center
        else:
            start["anchor"] = picking.along_axis((origin, direction), center, handle["dir"]) or 0.0
            start_x, start_y, _ = self.project(center)
            end_x, end_y, _ = self.project(handle["seg"][1])
            start["screen"] = (start_x, start_y, end_x, end_y)
        self._m_drag = ("gizmo", start)

    def _drag_gizmo(self, cursor) -> None:
        state = self._m_drag[1]
        drags = {"translate": self._drag_move, "rotate": self._drag_turn, "scale": self._drag_scale}
        moved = drags[state["mode"]](state, cursor)
        if moved is None:
            return
        if state["kind"] == "entry":
            self._apply_entry_drag(state, *moved)
        elif moved[0] is not None:
            self._apply_water_drag(self.from_view(moved[0]))

    def _drag_move(self, state: dict, cursor) -> tuple | None:
        """(new position, None, None) for a drag along an axis or over the ground, None when the ray misses it."""
        handle, center = state["handle"], state["center"]
        origin, direction = self.ray(cursor)
        if handle["axis"] == "plane":
            hit = picking.ray_plane((origin, direction), center, np.array([0, 1.0, 0]))
            return None if hit is None or state["anchor"] is None else (center + (hit - state["anchor"]), None, None)
        along = picking.along_axis((origin, direction), center, handle["dir"])
        if along is None:  # the ray runs along the axis: the handle stays where it was, not jumped back to the start
            return None
        return center + handle["dir"] * (along - state["anchor"]), None, None

    def _drag_turn(self, state: dict, cursor) -> tuple | None:
        """(None, new quaternion, None) for a drag round a ring: a tilt about the model's own axis, else a turn."""
        handle, center = state["handle"], state["center"]
        origin, direction = self.ray(cursor)
        hit = picking.ray_plane((origin, direction), center, handle["dir"])
        if hit is None or state["anchor"] is None:
            return None
        anchor, current = state["anchor"], hit - center
        angle = math.atan2(float(np.dot(np.cross(anchor, current), handle["dir"])), float(np.dot(anchor, current)))
        before = tuple(float(component) for component in state["quat"])
        if state["tilt"]:
            return (
                None,
                np.array(meshxform.qmul(before, meshxform.qaxis(tuple(np.eye(3)[handle["axis"]]), angle))),
                None,
            )
        return None, np.array(meshxform.qmul(meshxform.qaxis(tuple(handle["dir"]), angle), before)), None

    def _drag_scale(self, state: dict, cursor) -> tuple:
        """(None, None, new scale) for a drag along the handle's screen direction; a prop stretches along one axis (a
        waterfall to reach its pool)."""
        start_x, start_y, end_x, end_y = state["screen"]
        axis = np.array([end_x - start_x, end_y - start_y])
        length = float(np.linalg.norm(axis)) or 1.0
        moved = np.array([cursor.x() - state["pos"].x(), cursor.y() - state["pos"].y()])
        k = max(0.05, 1 + float(np.dot(moved, axis / length)) / length)
        start_scale = state["scale"]
        if isinstance(start_scale, list):
            new_scale = list(start_scale)
            new_scale[state["handle"]["axis"]] = start_scale[state["handle"]["axis"]] * k
            return None, None, new_scale
        return None, None, start_scale * k

    def _apply_entry_drag(self, state: dict, new_position, new_quaternion, new_scale) -> None:
        if new_position is not None:  # snapped, and the rest of the selection carried along
            new_position = self.to_view(self.m_group.place(self.from_view(new_position), state["entry"]))
            self.m_group.follow(state["undo"], self.from_view(new_position))
        rotation, snapped = None, None
        if new_quaternion is not None:  # turned by the turn setting, a building back onto its grid
            rotation, snapped = self.m_group.turned(state["entry"], list(to_view_quat(new_quaternion)))
        self.m_session.set_transform(
            state["entry"],
            position=snapped if new_position is None else self.from_view(new_position),
            rotation=rotation,
            scale=new_scale,
        )
        self.selectionChanged.emit(state["entry"])

    def _apply_water_drag(self, point) -> None:
        water = self.m_water_sel
        if "lake" in water:
            self.m_session.set_lake_level(water["lake"], point[1])
        elif "coast" in water:
            self.m_session.m_path.move_coast_point(water["coast"], water["point"], point)
        elif "shape" in water:
            self.m_session.m_path.move_shape_point(water["shape"], water["k"], water["point"], point)
        elif "outline" in water:
            self.m_session.move_lake_outline_point(water["outline"], water["point"], point)
        else:
            self.m_session.move_river_point(water["river"], water["point"], point)
        self.waterChanged.emit(water)

    def _end_gizmo(self, drag) -> None:
        state = drag[1]
        if state["kind"] == "entry":
            label = {
                "translate": tr("islands.move_lower"),
                "rotate": tr("islands.rotation"),
                "scale": tr("common.scale"),
            }[state["mode"]]
            self.m_session.end_transform(state["undo"], label)
        else:
            self.m_session.end_water(state["water"], tr("islands.water"))
        self.update()
