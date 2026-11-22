"""A free-fly camera for the 3D view, beside the orbit camera: the camera turns about its own eye, the wheel and the
keys fly it. ``CFlyCamera`` answers what the renderer and the tools ask of an ``COrbitCamera`` (``eye``, ``view``,
``proj``, ``matrix``, ``ray``, ``target``, ``distance``, ``turn``, ``pan``, ``zoom``, ``look``), so it replaces the
orbit camera as it is; ``switch`` changes one into the other and keeps the picture where it is.
"""

from __future__ import annotations

import math

import numpy as np

from ...infra.i18n import N_
from ...render.camera import COrbitCamera

MODES = ("orbit", "fly")
LABELS = {"orbit": N_("islands.orbit"), "fly": N_("islands.free_fly")}
PITCH = (
    math.radians(-80),
    math.radians(88),
)  # looking up to looking down (the orbit camera never goes below the ground)
STEP = 0.12  # the share of the look range one wheel step or key press flies
MIN_RANGE = 20.0  # world units: the least a step counts the look range as


class CFlyCamera(COrbitCamera):
    """Looks from ``position`` along the turn (yaw, pitch); ``distance`` is how far ahead ``target`` lies (what the
    renderer centres its shadows on and picks detail by), so it is a look range, not a distance to keep."""

    def __init__(
        self, position, yaw_rad: float, pitch_rad: float, distance_m: float, fov_rad: float, speed: float = 1.0
    ) -> None:
        self.m_position = np.asarray(position, "f4")
        self.m_yaw_rad, self.m_pitch_rad, self._m_range = float(yaw_rad), float(pitch_rad), float(distance_m)
        self.m_fov_rad, self.m_speed = float(fov_rad), float(speed)

    @classmethod
    def from_orbit(cls, camera: COrbitCamera) -> CFlyCamera:
        return cls(
            camera.eye(), camera.m_yaw_rad, camera.m_pitch_rad, camera.m_distance_m, camera.m_fov_rad, camera.m_speed
        )

    def forward(self) -> np.ndarray:
        cos_pitch = math.cos(self.m_pitch_rad)
        return -np.array(
            [cos_pitch * math.sin(self.m_yaw_rad), math.sin(self.m_pitch_rad), cos_pitch * math.cos(self.m_yaw_rad)],
            "f4",
        )

    def eye(self) -> np.ndarray:
        return self.m_position

    @property
    def m_target(self) -> np.ndarray:
        return self.m_position + self.forward() * self._m_range

    @m_target.setter
    def m_target(self, point) -> None:
        self.m_position = np.asarray(point, "f4") - self.forward() * self._m_range

    @property
    def m_distance_m(self) -> float:
        return self._m_range

    @m_distance_m.setter
    def m_distance_m(self, value: float) -> None:
        """A new look range keeps the point looked at: the camera moves back or forward to it."""
        pivot = self.m_target
        self._m_range = max(1.0, float(value))
        self.m_position = pivot - self.forward() * self._m_range

    def turn(self, delta_x: float, delta_y: float) -> None:
        self.m_yaw_rad -= delta_x * 0.008
        self.m_pitch_rad = min(PITCH[1], max(PITCH[0], self.m_pitch_rad + delta_y * 0.008))

    def pan(self, delta_x: float, delta_y: float) -> None:
        """Slide sideways and along the ground (the arrow keys and the middle button)."""
        right = np.array([math.cos(self.m_yaw_rad), 0, -math.sin(self.m_yaw_rad)], "f4")
        ahead = np.array([math.sin(self.m_yaw_rad), 0, math.cos(self.m_yaw_rad)], "f4")
        k = self.m_speed * max(self._m_range, MIN_RANGE) * 0.0015
        self.m_position = self.m_position - right * delta_x * k - ahead * delta_y * k

    def zoom(self, steps: float, towards=None) -> None:
        """The wheel flies forward (a step towards the viewer flies back)."""
        self.m_position = self.m_position + self.forward() * steps * STEP * self.m_speed * max(self._m_range, MIN_RANGE)

    def lift(self, steps: float) -> None:
        """Up (positive) or down along the vertical axis (Page Up, Page Down)."""
        self.m_position = self.m_position + np.array(
            [0, steps * STEP * self.m_speed * max(self._m_range, MIN_RANGE), 0], "f4"
        )

    def look(self, point, distance_m: float, yaw_deg: float = 0.0) -> None:
        orbit = COrbitCamera(point, max(COrbitCamera.MIN_DISTANCE, distance_m))
        orbit.look(point, distance_m, yaw_deg)
        self.m_yaw_rad, self.m_pitch_rad, self._m_range = orbit.m_yaw_rad, orbit.m_pitch_rad, orbit.m_distance_m
        self.m_position = orbit.eye()


def switch(camera: COrbitCamera, mode: str) -> COrbitCamera:
    """The camera of ``mode`` ("orbit" or "fly") looking where ``cam`` looks, from where it looks; ``cam`` itself
    when it already is one."""
    if mode not in MODES:
        raise ValueError(f"no camera mode {mode!r}: one of {', '.join(MODES)}")
    if mode == "fly":
        return camera if isinstance(camera, CFlyCamera) else CFlyCamera.from_orbit(camera)
    if not isinstance(camera, CFlyCamera):
        return camera
    orbit = COrbitCamera(camera.m_target, max(COrbitCamera.MIN_DISTANCE, camera.m_distance_m))
    orbit.m_yaw_rad, orbit.m_pitch_rad = (
        camera.m_yaw_rad,
        min(math.radians(88), max(math.radians(3), camera.m_pitch_rad)),
    )
    orbit.m_fov_rad, orbit.m_speed = camera.m_fov_rad, camera.m_speed
    return orbit
