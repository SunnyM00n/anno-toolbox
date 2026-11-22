"""The 3D view's camera and its matrices: an orbit camera about a pivot, the projection and view matrices as
GLSL takes them, the ray through a pixel and where it meets the terrain (``ground_hit``). View space is the
scene's: x mirrored (view x = island size - x), y up, z along the island's rows."""

from __future__ import annotations

import math

import numpy as np

from . import picking

MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])  # Anno's left-handed model space into the view's (x mirrored)
MIRROR_Y = np.diag([1.0, -1.0, 1.0, 1.0])  # the world mirrored in the water plane y = 0 (a planar reflection)


def oblique_near(projection: np.ndarray, plane) -> np.ndarray:
    """``projection`` (column vectors, OpenGL's clip depth) with its near plane laid onto ``plane`` (a, b, c, d in view
    space: what is kept on its positive side, the eye on the other), so that all before it is clipped; a planar
    reflection's camera keeps what lies above the water so (Lengyel's oblique frustum)."""
    oblique = np.asarray(projection, float).copy()
    plane_vector = np.asarray(plane, float)
    corner = np.linalg.inv(oblique) @ np.array([np.sign(plane_vector[0]), np.sign(plane_vector[1]), 1.0, 1.0])
    oblique[2] = plane_vector * (2.0 / float(plane_vector @ corner)) - oblique[3]
    return oblique


def perspective(fov_y_rad: float, aspect: float, near: float, far: float) -> np.ndarray:
    focal = 1.0 / math.tan(fov_y_rad / 2)
    return np.array(
        [
            [focal / aspect, 0, 0, 0],
            [0, focal, 0, 0],
            [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)],
            [0, 0, -1, 0],
        ],
        "f4",
    )


def ortho(half: float, near: float, far: float) -> np.ndarray:
    matrix = np.identity(4, "f4")
    matrix[0, 0] = matrix[1, 1] = 1 / half
    matrix[2, 2], matrix[2, 3] = -2 / (far - near), -(far + near) / (far - near)
    return matrix


def look_at(eye, target, up=(0.0, 1.0, 0.0)) -> np.ndarray:
    eye, target = np.asarray(eye, "f4"), np.asarray(target, "f4")
    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    side = np.cross(forward, up)
    side = side / max(np.linalg.norm(side), 1e-9)
    true_up = np.cross(side, forward)
    matrix = np.identity(4, "f4")
    matrix[0, :3], matrix[1, :3], matrix[2, :3] = side, true_up, -forward
    matrix[:3, 3] = -matrix[:3, :3] @ eye
    return matrix


def gl(matrix: np.ndarray) -> bytes:
    """A row-major matrix as GLSL reads it (column-major)."""
    return np.ascontiguousarray(np.asarray(matrix, "f4").T).tobytes()


class COrbitCamera:
    """Turns about a pivot: yaw and pitch in radians, distance in world units."""

    MIN_DISTANCE = 4.0  # the pivot keeps this far; zooming on carries it forward
    PAN_NEAR = 30.0  # closer than this, panning speeds up so a drag still crosses ground

    def __init__(self, target, distance_m: float) -> None:
        self.m_target = np.asarray(target, "f4")
        self.m_distance_m, self.m_yaw_rad, self.m_pitch_rad = float(distance_m), math.radians(-135), math.radians(45)
        self.m_fov_rad = math.radians(45)
        self.m_speed = 1.0  # the status bar's Speed: scales panning and zooming

    def eye(self) -> np.ndarray:
        cos_pitch = math.cos(self.m_pitch_rad)
        offset = np.array(
            [cos_pitch * math.sin(self.m_yaw_rad), math.sin(self.m_pitch_rad), cos_pitch * math.cos(self.m_yaw_rad)],
            "f4",
        )
        return self.m_target + offset * self.m_distance_m

    def view(self) -> np.ndarray:
        return look_at(self.eye(), self.m_target)

    def proj(self, aspect: float) -> np.ndarray:
        far = max(4000.0, self.m_distance_m * 8)
        return perspective(self.m_fov_rad, aspect, max(0.1, self.m_distance_m / 500), far)

    def matrix(self, aspect: float) -> np.ndarray:
        return self.proj(aspect) @ self.view()

    def turn(self, delta_x: float, delta_y: float) -> None:
        self.m_yaw_rad -= delta_x * 0.008
        self.m_pitch_rad = min(math.radians(88), max(math.radians(3), self.m_pitch_rad + delta_y * 0.008))

    def pan(self, delta_x: float, delta_y: float) -> None:
        right = np.array([math.cos(self.m_yaw_rad), 0, -math.sin(self.m_yaw_rad)], "f4")
        ahead = np.array([math.sin(self.m_yaw_rad), 0, math.cos(self.m_yaw_rad)], "f4")
        speed_scale = (
            self.m_speed * self.m_distance_m * 0.0015 * max(1.0, self.PAN_NEAR / max(self.m_distance_m, 1e-3)) ** 0.5
        )
        self.m_target = self.m_target - right * delta_x * speed_scale - ahead * delta_y * speed_scale

    def zoom(self, steps: float, towards=None) -> None:
        """Zoom by wheel steps; ``towards`` (a world point under the cursor) draws the pivot to it."""
        before = self.m_distance_m
        self.m_distance_m = min(5000.0, max(self.MIN_DISTANCE, self.m_distance_m * 0.88 ** (steps * self.m_speed)))
        if steps > 0 and before <= self.MIN_DISTANCE * 1.25:  # all the way in: carry the pivot forward
            fwd = self.m_target - self.eye()
            self.m_target = self.m_target + fwd / max(np.linalg.norm(fwd), 1e-6) * 2.0
        elif towards is not None and before > 0:
            k = 1 - self.m_distance_m / before
            self.m_target = self.m_target + (np.asarray(towards, "f4") - self.m_target) * k

    def look(self, point, distance_m: float, yaw_deg: float = 0.0) -> None:
        """Point at ``point`` (view space) from ``distance``, turned ``yaw_deg`` (the ribbon's go to)."""
        self.m_target = np.asarray(point, "f4")
        self.m_distance_m = max(self.MIN_DISTANCE, float(distance_m))
        self.m_yaw_rad, self.m_pitch_rad = (
            math.atan2(0.4, 0.6) + math.radians(yaw_deg),
            math.atan2(0.6, math.hypot(0.4, 0.6)),
        )

    def ray(self, x: float, y: float, width_px: int, height_px: int) -> tuple[np.ndarray, np.ndarray]:
        """The ray through pixel (x, y) from the top left: origin and unit direction (view space)."""
        return picking.ray(self.matrix(width_px / max(1, height_px)), (width_px, height_px), x, y)


def ground_hit(heights: np.ndarray, step_tiles: float, origin, direction, far: float = 6000.0):
    """Where a ray (view space) first meets the height grid, or None: marched, then refined."""
    rows, columns = heights.shape[:2]  # z runs down the rows, x along the columns (mirrored)
    size = (columns - 1) * step_tiles

    def h_at(world_x, world_z):
        grid_x, grid_z = (size - world_x) / step_tiles, world_z / step_tiles
        inside = (grid_x >= 0) & (grid_z >= 0) & (grid_x <= columns - 1) & (grid_z <= rows - 1)
        grid_x, grid_z = np.clip(grid_x, 0, columns - 1.001), np.clip(grid_z, 0, rows - 1.001)
        i, j = grid_x.astype(int), grid_z.astype(int)
        fraction_x, fraction_z = grid_x - i, grid_z - j
        height = (heights[j, i] * (1 - fraction_x) + heights[j, i + 1] * fraction_x) * (1 - fraction_z) + (
            heights[j + 1, i] * (1 - fraction_x) + heights[j + 1, i + 1] * fraction_x
        ) * fraction_z
        return np.where(inside, height, 0.0)  # off the island: the sea level

    origin, direction = np.asarray(origin, np.float64), np.asarray(direction, np.float64)
    distances = np.concatenate([np.linspace(0, 200, 2001), np.linspace(200.1, far, 4000)])
    points = origin[None] + distances[:, None] * direction[None]
    above = points[:, 1] - h_at(points[:, 0], points[:, 2])
    below = np.nonzero(above <= 0)[0]
    if not len(below):
        return None
    first_below = below[0]
    if first_below == 0:
        return origin
    low_distance, high_distance = distances[first_below - 1], distances[first_below]
    for _ in range(20):
        mid = (low_distance + high_distance) / 2
        point = origin + mid * direction
        if point[1] - h_at(np.array([point[0]]), np.array([point[2]]))[0] > 0:
            low_distance = mid
        else:
            high_distance = mid
    return origin + high_distance * direction
