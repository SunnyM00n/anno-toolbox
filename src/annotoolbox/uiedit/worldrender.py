"""The 3D scene behind the UI preview drawn by the suite's renderers: a model of the game drawn offscreen as the viewer
draws it, or an island as the island editor's view draws it (the game's own shaders on a QRhi device), handed to the
preview as a ``worldscene.CScene``.

``CStill(subject, done).start()`` is the entry, ``CModel(tree, game, path)`` and ``CIsland(settings, game, path)`` the
two subjects: what a subject shows is read off the window's thread, then drawn one frame per timer tick on it (a QRhi
device belongs to that thread) until its files are in, and ``done`` gets the scene or the error. ``configs``,
``islands`` and ``game_camera`` read what a game offers. docs/ui/117/world-effects.md, "What the editor does".
"""

from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer

from ..editor import editorapp, islandfiles
from ..editor.desktop.desktopeditor import open_session
from ..editor.desktop.desktoprender import SViewOptions
from ..editor.desktop.desktoprhiview import CRhiIslandView3D
from ..infra.i18n import tr
from ..render import rhiview
from ..render.camera import COrbitCamera
from ..suite.background import CBackground
from ..viewer import modelscene
from ..viewer.modelfiles import CModelFiles
from ..viewer.modelrender import SViewState
from ..viewer.modelview import CRhiModelView
from . import worldscene

TICK_MS = 20  # the renderers load their files a few per frame: frames often, small ones
PROBE = (320, 180)  # the frames drawn while the files load: small, the window's thread draws them
WAIT = 180.0  # seconds until a model that does not come in is given up (a residence of 117: about 30)
ISLAND_WAIT = 900.0  # an island's first picture in the game's look compiles its ground's shaders: minutes
QUIET = 20.0  # seconds an island's loading count stays the same before it is taken as in (a part that never is)
DEVICES = ("d3d12", "d3d11", "opengl")
GAME_LOOK = {"game_terrain": True, "native": True}  # the island view's game look
CAMERA_FILES = ("data/base/config/game/camera.xml", "data/config/game/camera.xml")  # 117's, 1800's


@dataclass(frozen=True)
class SGameCamera:
    """A step of the game camera (a ``camera.xml`` ``Preset``): the eye's height over the point it looks at, the angle
    below the horizontal and the vertical field of view, in radians (world-effects.md, "The game's camera")."""

    height_m: float
    pitch_rad: float
    fov_rad: float

    def place(self, view, session) -> None:
        """Point the island view at the island's middle on its ground (not below the sea), its heading kept."""
        middle = session.m_size_tiles / 2
        ground = max(0.0, float(session.height_at(middle, middle)))
        camera = COrbitCamera(view.to_view([middle, ground, middle]), self.height_m / math.sin(self.pitch_rad))
        camera.m_yaw_rad, camera.m_pitch_rad, camera.m_fov_rad = view.m_camera.m_yaw_rad, self.pitch_rad, self.fov_rad
        view.m_camera, view.m_fov_deg = camera, math.degrees(self.fov_rad)


def _number(text: str | None) -> float | None:
    match = re.fullmatch(r"\s*(-?[\d.]+)f?\s*", text or "")
    return float(match.group(1)) if match else None


def game_camera(tree) -> SGameCamera | None:
    """The step a session starts at (``Normal/Settings`` ``DefaultPreset``), None for a game without one."""
    path = next((candidate for candidate in CAMERA_FILES if tree.exists(candidate)), None)
    if path is None:
        return None
    # its comments hold "--" rules, which XML does not allow: they go before it is parsed
    text = re.sub(r"<\?xml[^>]*\?>|<!--.*?-->", "", tree.read(path).decode("utf-8-sig", "replace"), flags=re.S)
    try:
        normal = ET.fromstring(f"<camera>{text}</camera>").find("Normal")  # the file has two roots
    except ET.ParseError:
        return None
    settings = normal.find("Settings") if normal is not None else None
    step = settings.get("DefaultPreset") if settings is not None else None
    preset = (
        next((candidate for candidate in normal.iter("Preset") if candidate.get("ID") == step), None) if step else None
    )
    values = [_number(preset.get(k)) for k in ("Height", "Pitch", "Fov")] if preset is not None else [None]
    if None in values or values[0] <= 0 or not 0 < values[1] < math.pi / 2 or values[2] <= 0:
        return None
    return SGameCamera(*values)


def configs(tree) -> list[str]:
    """The model configs of a game: its ``.cfg`` files under a ``graphics`` folder."""
    return sorted(path for path in tree.m_paths if path.endswith(".cfg") and "/graphics/" in path)


def islands(settings, game: str) -> list[str]:
    """The island files of a game as the island editor lists them (its install folder from ``settings``)."""
    return islandfiles.island_paths(game, editorapp.CApp(settings=settings, games=(game,)).island_tree(game))


def device() -> str | None:
    """The first QRhi backend this machine can draw with (a ``QApplication`` must exist), None for none."""
    return next((backend for backend in DEVICES if rhiview.available(backend)), None)


class CModel:
    """A model config drawn by the viewer's offscreen view (``view``: a test passes its own)."""

    def __init__(self, tree, game: str, path: str, view: Callable[[str], object] | None = None) -> None:
        self.m_tree, self.m_game, self.m_path, self._m_view = tree, game, path, view
        self.m_key, self.m_wait = f"model:{path}", WAIT

    def read(self):
        return modelscene.build(self.m_tree, self.m_path, self.m_game)

    def view(self, backend: str):
        return (self._m_view or CRhiModelView)(backend)

    def show(self, view, scene) -> None:
        view.m_state = SViewState(look="shaders", particles=False)
        view.show_scene(scene, CModelFiles(self.m_tree))

    @staticmethod
    def ready(view) -> bool:
        renderer = view.m_renderer
        return (
            renderer is not None
            and renderer.m_box is not None
            and not renderer.busy()
            and renderer.shown_parts_in(view.m_state)
        )

    @staticmethod
    def frame(view) -> None:
        view.frame_model()

    @staticmethod
    def release(view) -> None:
        view.release()  # the device's resources, before the widget goes


class CIsland:
    """An island drawn by the island editor's view, opened as the island editor opens it, read only, from the game's
    default camera step (``tree``: the game's files it is read from; ``view``: a test passes its own; ``open``: what
    reads the island, ``(editor, session)``; ``look``: the view's options for 117's islands, the game's by default)."""

    def __init__(self, settings, game: str, path: str, view: Callable[[str], object] | None = None,
                 open: Callable[[], tuple] | None = None, look: dict | None = None, tree=None) -> None:  # fmt: skip
        self.m_settings, self.m_game, self.m_path, self._m_view, self._m_open = settings, game, path, view, open
        self.m_look, self.m_tree = GAME_LOOK if look is None else look, tree
        self.m_key, self.m_wait = f"island:{path}", ISLAND_WAIT
        self._m_busy, self._m_since = -1, 0.0  # the loading count last seen, and since when

    def read(self) -> tuple:
        """``(editor, session, the game's camera step or None)``."""
        camera = game_camera(self.m_tree) if self.m_tree is not None else None
        if self._m_open is not None:
            return (*self._m_open(), camera)
        app = editorapp.CApp(settings=self.m_settings, games=(self.m_game,))
        opened = {**app.open(self.m_game, self.m_path), "editable": False}  # nothing is saved from here
        editor, _island, session = open_session(app.m_editor, opened)
        return editor, session, camera

    def view(self, backend: str):
        return (self._m_view or (lambda backend_name: CRhiIslandView3D(backend=backend_name)))(backend)

    def show(self, view, opened: tuple) -> None:
        """Load the island and point the camera before the first frame: the view loads what is near its camera first."""
        editor, session, camera = opened
        view.load(session, editor)
        native = not session.m_island.get("game") and not session.m_anno2070_editing  # 117's rules for 117's islands
        view.m_opts = SViewOptions(scheme=view.m_opts.scheme, **(self.m_look if native else {}))
        view.m_opts.layers["markers"] = False  # the editor's markers are no part of the game's picture
        if camera is not None:
            camera.place(view, session)

    def ready(self, view) -> bool:
        """Nothing loading, or the count unchanged for ``QUIET`` seconds (the island editor's own pictures wait so)."""
        if view.m_renderer is None:
            return False
        busy, now = view.m_renderer.busy(), time.monotonic()
        if busy != self._m_busy:
            self._m_busy, self._m_since = busy, now
        return busy == 0 or now - self._m_since > QUIET

    @staticmethod
    def frame(view) -> None:
        """The camera stays where ``show`` put it (the game's step, else the island view's whole-island start)."""

    @staticmethod
    def release(view) -> None:
        """The view frees its renderer when it is closed."""


class CStill(QObject):
    """One subject drawn offscreen at the reference frame's size; ``done(scene or exception)`` once, on the window's
    thread, unless it was ``cancel``led. The caller keeps it while it draws (its timer goes with it)."""

    def __init__(self, subject, done: Callable[[object], None], size_px: tuple[int, int] = worldscene.SIZE) -> None:
        super().__init__()
        self.m_subject, self.m_done, self.m_size_px = subject, done, size_px
        self.m_view, self.m_started, self.m_cancelled = None, 0.0, False
        self.m_background = CBackground(self)
        self.m_timer = QTimer(self)
        self.m_timer.setInterval(TICK_MS)
        self.m_timer.timeout.connect(self._tick)

    def start(self, backend: str | None = None, is_waiting: bool = False) -> None:
        """Read the subject (off the window's thread unless ``is_waiting``), then draw it on ``backend`` (``device()``)."""
        backend = backend or device()
        if backend is None:
            self.m_done(RuntimeError(tr("ui.no_direct3d_opengl_device_draw")))
            return
        self.m_background.run(self.m_subject.read, lambda got: self._read(got, backend), is_waiting)

    def cancel(self) -> None:
        """Stop drawing; ``done`` is not called."""
        self.m_cancelled = True
        self._release()

    def _read(self, got, backend: str) -> None:
        if self.m_cancelled:
            return
        if isinstance(got, Exception):
            self.m_done(got)
            return
        try:
            self.m_view = self.m_subject.view(backend)
            self.m_subject.show(self.m_view, got)
        except (RuntimeError, OSError, ValueError) as error:  # no view on this device, the subject's files unreadable
            self._finish(error)
            return
        self.m_started = time.monotonic()
        self.m_timer.start()

    def _tick(self) -> None:
        """One frame: the subject drawn small while its files load; once they are in, framed and drawn at full size."""
        try:
            self.m_view.picture(*PROBE)
            if self.m_view.m_failed:
                raise RuntimeError(self.m_view.m_failed)
            if self.m_subject.ready(self.m_view):
                self.m_subject.frame(self.m_view)
                self.m_view.picture(*self.m_size_px)  # what loaded during the framing frame is drawn in the next
                self._finish(worldscene.from_image(self.m_view.picture(*self.m_size_px), self.m_subject.m_key))
            elif time.monotonic() - self.m_started > self.m_subject.m_wait:
                raise RuntimeError(tr("ui.scenes_files_not_come_within", n=round(self.m_subject.m_wait / 60)))
        except (RuntimeError, ValueError) as error:
            self._finish(error)

    def _release(self) -> None:
        self.m_timer.stop()
        view, self.m_view = self.m_view, None
        if view is not None:
            self.m_subject.release(view)
            view.close()
            view.deleteLater()

    def _finish(self, result) -> None:
        self._release()
        self.m_done(result)
