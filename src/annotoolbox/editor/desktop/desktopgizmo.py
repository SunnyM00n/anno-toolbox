"""The gizmo of the PC editor's 3D view: its handles and their drawing (``Gizmo``), the quaternion helpers the
view and the properties panel share, and the mouse handling that moves, turns or scales the selection with it
(``GizmoControls``, a mixin of ``desktopview.IslandControls``). Every change goes through the edit session."""

from __future__ import annotations

import math

import numpy as np

from ...infra.i18n import tr
from ...models import meshxform
from ...models.skinning import quat_matrix
from ...render import picking


# quaternions (x, y, z, w): models.meshxform
def to_view_quat(q) -> np.ndarray:
    """Anno's turn in the mirrored view (and back: it is its own inverse)."""
    return np.array([q[0], -q[1], -q[2], q[3]], float)


def euler_of(q) -> tuple[float, float, float]:
    """Turn (about y), then tilts about the model's x and z, in degrees (Anno's axes)."""
    m = quat_matrix(q)
    tx = math.asin(max(-1.0, min(1.0, -m[1, 2])))
    if abs(m[1, 2]) < 0.9999:
        yaw, tz = math.atan2(m[0, 2], m[2, 2]), math.atan2(m[1, 0], m[1, 1])
    else:
        yaw, tz = math.atan2(-m[2, 0], m[0, 0]), 0.0
    return math.degrees(yaw), math.degrees(tx), math.degrees(tz)


def quat_of(yaw: float, tx: float, tz: float) -> list[float]:
    """The inverse of ``euler_of``."""
    q = meshxform.euler_quat(0.0, 0.0, 0.0)
    for axis, degrees in (((0, 1, 0), yaw), ((1, 0, 0), tx), ((0, 0, 1), tz)):
        q = meshxform.qmul(q, meshxform.qaxis(axis, math.radians(degrees)))
    return list(meshxform.qnormalize(q))


class Gizmo:
    """Move (three axes and the ground plane), turn or tilt (rings) and scale (three axes), in the view's space."""

    COLORS = {0: (0.95, 0.3, 0.3, 1), 1: (0.4, 0.9, 0.3, 1), 2: (0.35, 0.55, 1.0, 1)}

    def __init__(self) -> None:
        self.mode = "translate"
        self.tilt = False
        self.axes_only: tuple | None = None  # the water tool's lake: only up and down

    def handles(self, center, quat_v, length: float) -> list[dict]:
        axes = [np.eye(3)[k] for k in range(3)]
        if self.mode == "rotate" and self.tilt or self.mode == "scale":
            axes = [np.array(meshxform.qrotate(tuple(quat_v), tuple(a))) for a in axes]
        out = []
        if self.mode == "rotate":
            for k in (0, 2) if self.tilt else (1,):
                a = axes[k]
                u = np.cross(a, [0, 1, 0] if abs(a[1]) < 0.9 else [1, 0, 0])
                u /= np.linalg.norm(u)
                v = np.cross(a, u)
                ts = np.linspace(0, 2 * math.pi, 49)
                ring = center + length * (np.cos(ts)[:, None] * u + np.sin(ts)[:, None] * v)
                out.append({"axis": k, "dir": a, "ring": ring})
            return out
        for k in range(3):
            if self.axes_only is not None and k not in self.axes_only:
                continue
            out.append({"axis": k, "dir": axes[k], "seg": (np.asarray(center), center + axes[k] * length)})
        if self.mode == "translate" and self.axes_only is None:
            out.append({"axis": "plane", "dir": np.array([0, 1.0, 0]), "seg": (np.asarray(center), np.asarray(center))})
        return out

    def lines(self, handles: list[dict], hot, active=(1, 0.85, 0.25, 1)) -> list:
        rows = []
        for h in handles:
            c = active if h is hot else self.COLORS.get(h["axis"], (1, 1, 1, 1))
            if "ring" in h:
                r = h["ring"]
                for p, q in zip(r[:-1], r[1:], strict=True):
                    rows += [(*p, *c), (*q, *c)]
            elif h["axis"] != "plane":
                p, q = h["seg"]
                rows += [(*p, *c), (*q, *c)]
        return rows

    def points(self, handles: list[dict], hot, active=(1, 0.85, 0.25, 1)) -> list:
        rows = []
        for h in handles:
            if "seg" in h:
                c = active if h is hot else self.COLORS.get(h["axis"], (1, 1, 1, 1))
                rows.append((*h["seg"][1], *c))
        return rows


class GizmoControls:
    """The view's gizmo: what it moves, where its handles are, and the drag; the view brings the camera,
    the session and the selection."""

    def _gizmo_target(self):
        """(view position, view quaternion, kind) of what the gizmo moves, or None."""
        if self.tool == "select" and self.selected is not None and self.selected.shown and self.selected.kind != "tree":
            if self.session.readonly:
                return None
            e = self.selected
            return self.to_view(e.position), to_view_quat(e.rotation), "entry"
        if self.tool == "water" and self.water_sel is not None:
            for h, p in self.water_handles():
                if h == self.water_sel:
                    return p, np.array([0, 0, 0, 1.0]), "water"
        return None

    def _gizmo_handles(self) -> list[dict]:
        t = self._gizmo_target()
        if t is None:
            return []
        center, q, _ = t
        length = float(np.linalg.norm(self.camera.eye() - center)) * 0.15
        if self.tool == "water":
            self.gizmo.mode = "translate"
        return self.gizmo.handles(center, q, length)

    def _hit_handle(self, pos) -> dict | None:
        best, bd = None, 9.0
        for h in self._gizmo_handles():
            pts = h["ring"] if "ring" in h else np.linspace(h["seg"][0], h["seg"][1], 12)
            if h.get("axis") == "plane":
                x, y, _ = self.project(h["seg"][0])
                d = math.hypot(x - pos.x(), y - pos.y()) * 0.8
            else:
                sp = np.array([self.project(p)[:2] for p in pts])
                d = float(np.min(np.hypot(sp[:, 0] - pos.x(), sp[:, 1] - pos.y())))
            if d < bd:
                best, bd = h, d
        return best

    def set_gizmo(self, mode: str, tilt: bool = False) -> None:
        self.gizmo.mode, self.gizmo.tilt = mode, tilt
        self.update()

    # the gizmo
    def _start_gizmo(self, h: dict, pos) -> None:
        center, q, kind = self._gizmo_target()
        o, d = self.ray(pos)
        start = {"handle": h, "center": center, "quat": q, "kind": kind, "pos": pos}
        # the drag stays what its handle is for: W, E and R change the gizmo for the next one
        start["mode"], start["tilt"] = (self.gizmo.mode if kind == "entry" else "translate"), self.gizmo.tilt
        if kind == "entry":
            e = self.selected
            start["entry"], start["undo"] = e, self.session.begin_transform(self.group.moving(e))
            start["scale"] = list(e.scale) if isinstance(e.scale, list) else e.scale
        else:
            start["water"] = self.session.water_snapshot()
        if h["axis"] == "plane":
            start["anchor"] = picking.ray_plane((o, d), center, np.array([0, 1.0, 0]))
        elif "ring" in h:
            p = picking.ray_plane((o, d), center, h["dir"])
            start["anchor"] = None if p is None else p - center
        else:
            start["anchor"] = picking.along_axis((o, d), center, h["dir"]) or 0.0
            sx, sy, _ = self.project(center)
            ex, ey, _ = self.project(h["seg"][1])
            start["screen"] = (sx, sy, ex, ey)
        self._drag = ("gizmo", start)

    def _drag_gizmo(self, pos) -> None:
        st = self._drag[1]
        drags = {"translate": self._drag_move, "rotate": self._drag_turn, "scale": self._drag_scale}
        moved = drags[st["mode"]](st, pos)
        if moved is None:
            return
        if st["kind"] == "entry":
            self._apply_entry_drag(st, *moved)
        elif moved[0] is not None:
            self._apply_water_drag(self.from_view(moved[0]))

    def _drag_move(self, st: dict, pos) -> tuple | None:
        """(new position, None, None) for a drag along an axis or over the ground, None when the ray misses it."""
        h, center = st["handle"], st["center"]
        o, d = self.ray(pos)
        if h["axis"] == "plane":
            p = picking.ray_plane((o, d), center, np.array([0, 1.0, 0]))
            return None if p is None or st["anchor"] is None else (center + (p - st["anchor"]), None, None)
        t = picking.along_axis((o, d), center, h["dir"]) or 0.0
        return center + h["dir"] * (t - st["anchor"]), None, None

    def _drag_turn(self, st: dict, pos) -> tuple | None:
        """(None, new quaternion, None) for a drag round a ring: a tilt about the model's own axis, else a turn."""
        h, center = st["handle"], st["center"]
        o, d = self.ray(pos)
        p = picking.ray_plane((o, d), center, h["dir"])
        if p is None or st["anchor"] is None:
            return None
        u, v = st["anchor"], p - center
        ang = math.atan2(float(np.dot(np.cross(u, v), h["dir"])), float(np.dot(u, v)))
        before = tuple(float(c) for c in st["quat"])
        if st["tilt"]:
            return None, np.array(meshxform.qmul(before, meshxform.qaxis(tuple(np.eye(3)[h["axis"]]), ang))), None
        return None, np.array(meshxform.qmul(meshxform.qaxis(tuple(h["dir"]), ang), before)), None

    def _drag_scale(self, st: dict, pos) -> tuple:
        """(None, None, new scale) for a drag along the handle's screen direction; a prop stretches along one axis (a
        waterfall to reach its pool)."""
        sx, sy, ex, ey = st["screen"]
        axis = np.array([ex - sx, ey - sy])
        n = float(np.linalg.norm(axis)) or 1.0
        moved = np.array([pos.x() - st["pos"].x(), pos.y() - st["pos"].y()])
        k = max(0.05, 1 + float(np.dot(moved, axis / n)) / n)
        s0 = st["scale"]
        if isinstance(s0, list):
            new_s = list(s0)
            new_s[st["handle"]["axis"]] = s0[st["handle"]["axis"]] * k
            return None, None, new_s
        return None, None, s0 * k

    def _apply_entry_drag(self, st: dict, new_pos, new_q, new_s) -> None:
        if new_pos is not None:  # snapped, and the rest of the selection carried along
            new_pos = self.to_view(self.group.place(self.from_view(new_pos), st["entry"]))
            self.group.follow(st["undo"], self.from_view(new_pos))
        rotation, snapped = None, None
        if new_q is not None:  # turned by the turn setting, a building back onto its grid
            rotation, snapped = self.group.turned(st["entry"], list(to_view_quat(new_q)))
        self.session.set_transform(
            st["entry"],
            position=snapped if new_pos is None else self.from_view(new_pos),
            rotation=rotation,
            scale=new_s,
        )
        self.selectionChanged.emit(st["entry"])

    def _apply_water_drag(self, p) -> None:
        w = self.water_sel
        if "lake" in w:
            self.session.set_lake_level(w["lake"], p[1])
        elif "coast" in w:
            self.session.path.move_coast_point(w["coast"], w["point"], p)
        elif "shape" in w:
            self.session.path.move_shape_point(w["shape"], w["k"], w["point"], p)
        elif "outline" in w:
            self.session.move_lake_outline_point(w["outline"], w["point"], p)
        else:
            self.session.move_river_point(w["river"], w["point"], p)
        self.waterChanged.emit(w)

    def _end_gizmo(self, d) -> None:
        st = d[1]
        if st["kind"] == "entry":
            label = {
                "translate": tr("islands.move_lower"),
                "rotate": tr("islands.rotation"),
                "scale": tr("common.scale"),
            }[st["mode"]]
            self.session.end_transform(st["undo"], label)
        else:
            self.session.end_water(st["water"], tr("islands.water"))
        self.update()
