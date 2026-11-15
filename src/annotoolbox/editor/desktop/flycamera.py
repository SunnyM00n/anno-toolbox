"""A free-fly camera for the 3D view, beside the orbit camera: the camera turns about its own eye, the wheel and the
keys fly it. ``FlyCamera`` answers what the renderer and the tools ask of an ``OrbitCamera`` (``eye``, ``view``,
``proj``, ``matrix``, ``ray``, ``target``, ``distance``, ``turn``, ``pan``, ``zoom``, ``look``), so it replaces the
orbit camera as it is; ``switch`` changes one into the other and keeps the picture where it is.
"""

from __future__ import annotations

import math

import numpy as np

from ...infra.i18n import N_
from ...render.camera import OrbitCamera

MODES = ("orbit", "fly")
LABELS = {"orbit": N_("Orbit"), "fly": N_("Free fly")}
PITCH = (
    math.radians(-80),
    math.radians(88),
)  # looking up to looking down (the orbit camera never goes below the ground)
STEP = 0.12  # the share of the look range one wheel step or key press flies
MIN_RANGE = 20.0  # world units: the least a step counts the look range as


class FlyCamera(OrbitCamera):
    """Looks from ``position`` along the turn (yaw, pitch); ``distance`` is how far ahead ``target`` lies (what the
    renderer centres its shadows on and picks detail by), so it is a look range, not a distance to keep."""

    def __init__(self, position, yaw: float, pitch: float, distance: float, fov: float, speed: float = 1.0) -> None:
        self.position = np.asarray(position, "f4")
        self.yaw, self.pitch, self._range = float(yaw), float(pitch), float(distance)
        self.fov, self.speed = float(fov), float(speed)

    @classmethod
    def from_orbit(cls, cam: OrbitCamera) -> FlyCamera:
        return cls(cam.eye(), cam.yaw, cam.pitch, cam.distance, cam.fov, cam.speed)

    def forward(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        return -np.array([cp * math.sin(self.yaw), math.sin(self.pitch), cp * math.cos(self.yaw)], "f4")

    def eye(self) -> np.ndarray:
        return self.position

    @property
    def target(self) -> np.ndarray:
        return self.position + self.forward() * self._range

    @target.setter
    def target(self, point) -> None:
        self.position = np.asarray(point, "f4") - self.forward() * self._range

    @property
    def distance(self) -> float:
        return self._range

    @distance.setter
    def distance(self, value: float) -> None:
        """A new look range keeps the point looked at: the camera moves back or forward to it."""
        pivot = self.target
        self._range = max(1.0, float(value))
        self.position = pivot - self.forward() * self._range

    def turn(self, dx: float, dy: float) -> None:
        self.yaw -= dx * 0.008
        self.pitch = min(PITCH[1], max(PITCH[0], self.pitch + dy * 0.008))

    def pan(self, dx: float, dy: float) -> None:
        """Slide sideways and along the ground (the arrow keys and the middle button)."""
        right = np.array([math.cos(self.yaw), 0, -math.sin(self.yaw)], "f4")
        ahead = np.array([math.sin(self.yaw), 0, math.cos(self.yaw)], "f4")
        k = self.speed * max(self._range, MIN_RANGE) * 0.0015
        self.position = self.position - right * dx * k - ahead * dy * k

    def zoom(self, steps: float, towards=None) -> None:
        """The wheel flies forward (a step towards the viewer flies back)."""
        self.position = self.position + self.forward() * steps * STEP * self.speed * max(self._range, MIN_RANGE)

    def lift(self, steps: float) -> None:
        """Up (positive) or down along the vertical axis (Page Up, Page Down)."""
        self.position = self.position + np.array([0, steps * STEP * self.speed * max(self._range, MIN_RANGE), 0], "f4")

    def look(self, point, distance: float, yaw_deg: float = 0.0) -> None:
        orbit = OrbitCamera(point, max(OrbitCamera.MIN_DISTANCE, distance))
        orbit.look(point, distance, yaw_deg)
        self.yaw, self.pitch, self._range = orbit.yaw, orbit.pitch, orbit.distance
        self.position = orbit.eye()


def switch(cam: OrbitCamera, mode: str) -> OrbitCamera:
    """The camera of ``mode`` ("orbit" or "fly") looking where ``cam`` looks, from where it looks; ``cam`` itself
    when it already is one."""
    if mode not in MODES:
        raise ValueError(f"no camera mode {mode!r}: one of {', '.join(MODES)}")
    if mode == "fly":
        return cam if isinstance(cam, FlyCamera) else FlyCamera.from_orbit(cam)
    if not isinstance(cam, FlyCamera):
        return cam
    orbit = OrbitCamera(cam.target, max(OrbitCamera.MIN_DISTANCE, cam.distance))
    orbit.yaw, orbit.pitch = cam.yaw, min(math.radians(88), max(math.radians(3), cam.pitch))
    orbit.fov, orbit.speed = cam.fov, cam.speed
    return orbit
