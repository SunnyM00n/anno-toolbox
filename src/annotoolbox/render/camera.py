"""The 3D view's camera and its matrices: an orbit camera about a pivot, the projection and view matrices as
GLSL takes them, the ray through a pixel and where it meets the terrain (``ground_hit``). View space is the
scene's: x mirrored (view x = island size - x), y up, z along the island's rows."""

from __future__ import annotations

import math

import numpy as np

from . import picking

MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])  # Anno's left-handed model space into the view's (x mirrored)
MIRROR_Y = np.diag([1.0, -1.0, 1.0, 1.0])  # the world mirrored in the water plane y = 0 (a planar reflection)


def oblique_near(proj: np.ndarray, plane) -> np.ndarray:
    """``proj`` (column vectors, OpenGL's clip depth) with its near plane laid onto ``plane`` (a, b, c, d in view
    space: what is kept on its positive side, the eye on the other), so that all before it is clipped; a planar
    reflection's camera keeps what lies above the water so (Lengyel's oblique frustum)."""
    p = np.asarray(proj, float).copy()
    c = np.asarray(plane, float)
    q = np.linalg.inv(p) @ np.array([np.sign(c[0]), np.sign(c[1]), 1.0, 1.0])
    p[2] = c * (2.0 / float(c @ q)) - p[3]
    return p


def perspective(fov_y: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(fov_y / 2)
    return np.array(
        [
            [f / aspect, 0, 0, 0],
            [0, f, 0, 0],
            [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
            [0, 0, -1, 0],
        ],
        "f4",
    )


def ortho(half: float, near: float, far: float) -> np.ndarray:
    m = np.identity(4, "f4")
    m[0, 0] = m[1, 1] = 1 / half
    m[2, 2], m[2, 3] = -2 / (far - near), -(far + near) / (far - near)
    return m


def look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    eye, target = np.asarray(eye, "f4"), np.asarray(target, "f4")
    f = target - eye
    f = f / np.linalg.norm(f)
    s = np.cross(f, up)
    s = s / max(np.linalg.norm(s), 1e-9)
    u = np.cross(s, f)
    m = np.identity(4, "f4")
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def gl(m: np.ndarray) -> bytes:
    """A row-major matrix as GLSL reads it (column-major)."""
    return np.ascontiguousarray(np.asarray(m, "f4").T).tobytes()


class OrbitCamera:
    """Turns about a pivot: yaw and pitch in radians, distance in world units."""

    MIN_DISTANCE = 4.0  # the pivot keeps this far; zooming on carries it forward
    PAN_NEAR = 30.0  # closer than this, panning speeds up so a drag still crosses ground

    def __init__(self, target, distance: float) -> None:
        self.target = np.asarray(target, "f4")
        self.distance, self.yaw, self.pitch = float(distance), math.radians(-135), math.radians(45)
        self.fov = math.radians(45)
        self.speed = 1.0  # the status bar's Speed: scales panning and zooming

    def eye(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        offset = np.array([cp * math.sin(self.yaw), math.sin(self.pitch), cp * math.cos(self.yaw)], "f4")
        return self.target + offset * self.distance

    def view(self) -> np.ndarray:
        return look_at(self.eye(), self.target)

    def proj(self, aspect: float) -> np.ndarray:
        far = max(4000.0, self.distance * 8)
        return perspective(self.fov, aspect, max(0.1, self.distance / 500), far)

    def matrix(self, aspect: float) -> np.ndarray:
        return self.proj(aspect) @ self.view()

    def turn(self, dx: float, dy: float) -> None:
        self.yaw -= dx * 0.008
        self.pitch = min(math.radians(88), max(math.radians(3), self.pitch + dy * 0.008))

    def pan(self, dx: float, dy: float) -> None:
        right = np.array([math.cos(self.yaw), 0, -math.sin(self.yaw)], "f4")
        ahead = np.array([math.sin(self.yaw), 0, math.cos(self.yaw)], "f4")
        k = self.speed * self.distance * 0.0015 * max(1.0, self.PAN_NEAR / max(self.distance, 1e-3)) ** 0.5
        self.target = self.target - right * dx * k - ahead * dy * k

    def zoom(self, steps: float, towards=None) -> None:
        """Zoom by wheel steps; ``towards`` (a world point under the cursor) draws the pivot to it."""
        before = self.distance
        self.distance = min(5000.0, max(self.MIN_DISTANCE, self.distance * 0.88 ** (steps * self.speed)))
        if steps > 0 and before <= self.MIN_DISTANCE * 1.25:  # all the way in: carry the pivot forward
            fwd = self.target - self.eye()
            self.target = self.target + fwd / max(np.linalg.norm(fwd), 1e-6) * 2.0
        elif towards is not None and before > 0:
            k = 1 - self.distance / before
            self.target = self.target + (np.asarray(towards, "f4") - self.target) * k

    def look(self, point, distance: float, yaw_deg: float = 0.0) -> None:
        """Point at ``point`` (view space) from ``distance``, turned ``yaw_deg`` (the ribbon's go to)."""
        self.target = np.asarray(point, "f4")
        self.distance = max(self.MIN_DISTANCE, float(distance))
        self.yaw, self.pitch = math.atan2(0.4, 0.6) + math.radians(yaw_deg), math.atan2(0.6, math.hypot(0.4, 0.6))

    def ray(self, x: float, y: float, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
        """The ray through pixel (x, y) from the top left: origin and unit direction (view space)."""
        return picking.ray(self.matrix(w / max(1, h)), (w, h), x, y)


def ground_hit(heights: np.ndarray, step: float, origin, direction, far: float = 6000.0):
    """Where a ray (view space) first meets the height grid, or None: marched, then refined."""
    w = heights.shape[0]
    size = (w - 1) * step

    def h_at(px, pz):
        gx, gz = (size - px) / step, pz / step
        inside = (gx >= 0) & (gz >= 0) & (gx <= w - 1) & (gz <= w - 1)
        gx, gz = np.clip(gx, 0, w - 1.001), np.clip(gz, 0, w - 1.001)
        i, j = gx.astype(int), gz.astype(int)
        fx, fz = gx - i, gz - j
        v = (heights[j, i] * (1 - fx) + heights[j, i + 1] * fx) * (1 - fz) + (
            heights[j + 1, i] * (1 - fx) + heights[j + 1, i + 1] * fx
        ) * fz
        return np.where(inside, v, 0.0)  # off the island: the sea level

    o, d = np.asarray(origin, np.float64), np.asarray(direction, np.float64)
    ts = np.concatenate([np.linspace(0, 200, 2001), np.linspace(200.1, far, 4000)])
    p = o[None] + ts[:, None] * d[None]
    above = p[:, 1] - h_at(p[:, 0], p[:, 2])
    below = np.nonzero(above <= 0)[0]
    if not len(below):
        return None
    k = below[0]
    if k == 0:
        return o
    lo, hi = ts[k - 1], ts[k]
    for _ in range(20):
        mid = (lo + hi) / 2
        q = o + mid * d
        if q[1] - h_at(np.array([q[0]]), np.array([q[2]]))[0] > 0:
            lo = mid
        else:
            hi = mid
    return o + hi * d
