"""The 3D scene behind the UI preview drawn by the suite's renderers: a model of the game drawn offscreen as the viewer
draws it, or an island as the island editor's view draws it (the game's own shaders on a QRhi device), handed to the
preview as a ``worldscene.Scene``.

``Still(subject, done).start()`` is the entry, ``Model(tree, game, path)`` and ``Island(settings, game, path)`` the
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
from ..editor.desktop.desktoprender import ViewOptions
from ..editor.desktop.desktoprhiview import RhiIslandView3D
from ..infra.i18n import tr
from ..render import rhiview
from ..render.camera import OrbitCamera
from ..suite.background import Background
from ..viewer import modelscene
from ..viewer.modelfiles import ModelFiles
from ..viewer.modelrender import ViewState
from ..viewer.modelview import RhiModelView
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
class GameCamera:
    """A step of the game camera (a ``camera.xml`` ``Preset``): the eye's height over the point it looks at, the angle
    below the horizontal and the vertical field of view, in radians (world-effects.md, "The game's camera")."""

    height: float
    pitch: float
    fov: float

    def place(self, view, session) -> None:
        """Point the island view at the island's middle on its ground (not below the sea), its heading kept."""
        middle = session.size / 2
        ground = max(0.0, float(session.height_at(middle, middle)))
        camera = OrbitCamera(view.to_view([middle, ground, middle]), self.height / math.sin(self.pitch))
        camera.yaw, camera.pitch, camera.fov = view.camera.yaw, self.pitch, self.fov
        view.camera, view.fov = camera, math.degrees(self.fov)


def _number(text: str | None) -> float | None:
    m = re.fullmatch(r"\s*(-?[\d.]+)f?\s*", text or "")
    return float(m.group(1)) if m else None


def game_camera(tree) -> GameCamera | None:
    """The step a session starts at (``Normal/Settings`` ``DefaultPreset``), None for a game without one."""
    path = next((p for p in CAMERA_FILES if tree.exists(p)), None)
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
    preset = next((p for p in normal.iter("Preset") if p.get("ID") == step), None) if step else None
    values = [_number(preset.get(k)) for k in ("Height", "Pitch", "Fov")] if preset is not None else [None]
    if None in values or values[0] <= 0 or not 0 < values[1] < math.pi / 2 or values[2] <= 0:
        return None
    return GameCamera(*values)


def configs(tree) -> list[str]:
    """The model configs of a game: its ``.cfg`` files under a ``graphics`` folder."""
    return sorted(p for p in tree.paths if p.endswith(".cfg") and "/graphics/" in p)


def islands(settings, game: str) -> list[str]:
    """The island files of a game as the island editor lists them (its install folder from ``settings``)."""
    return islandfiles.island_paths(game, editorapp.App(settings=settings, games=(game,)).island_tree(game))


def device() -> str | None:
    """The first QRhi backend this machine can draw with (a ``QApplication`` must exist), None for none."""
    return next((b for b in DEVICES if rhiview.available(b)), None)


class Model:
    """A model config drawn by the viewer's offscreen view (``view``: a test passes its own)."""

    def __init__(self, tree, game: str, path: str, view: Callable[[str], object] | None = None) -> None:
        self.tree, self.game, self.path, self._view = tree, game, path, view
        self.key, self.wait = f"model:{path}", WAIT

    def read(self):
        return modelscene.build(self.tree, self.path, self.game)

    def view(self, backend: str):
        return (self._view or RhiModelView)(backend)

    def show(self, view, scene) -> None:
        view.state = ViewState(look="shaders", particles=False)
        view.show_scene(scene, ModelFiles(self.tree))

    @staticmethod
    def ready(view) -> bool:
        r = view.renderer
        return r is not None and r.box is not None and not r.busy() and r.shown_parts_in(view.state)

    @staticmethod
    def frame(view) -> None:
        view.frame_model()

    @staticmethod
    def release(view) -> None:
        view.release()  # the device's resources, before the widget goes


class Island:
    """An island drawn by the island editor's view, opened as the island editor opens it, read only, from the game's
    default camera step (``tree``: the game's files it is read from; ``view``: a test passes its own; ``open``: what
    reads the island, ``(editor, session)``; ``look``: the view's options for 117's islands, the game's by default)."""

    def __init__(self, settings, game: str, path: str, view: Callable[[str], object] | None = None,
                 open: Callable[[], tuple] | None = None, look: dict | None = None, tree=None) -> None:  # fmt: skip
        self.settings, self.game, self.path, self._view, self._open = settings, game, path, view, open
        self.look, self.tree = GAME_LOOK if look is None else look, tree
        self.key, self.wait = f"island:{path}", ISLAND_WAIT
        self._busy, self._since = -1, 0.0  # the loading count last seen, and since when

    def read(self) -> tuple:
        """``(editor, session, the game's camera step or None)``."""
        camera = game_camera(self.tree) if self.tree is not None else None
        if self._open is not None:
            return (*self._open(), camera)
        app = editorapp.App(settings=self.settings, games=(self.game,))
        info = {**app.open(self.game, self.path), "editable": False}  # nothing is saved from here
        editor, _island, session = open_session(app.editor, info)
        return editor, session, camera

    def view(self, backend: str):
        return (self._view or (lambda b: RhiIslandView3D(backend=b)))(backend)

    def show(self, view, opened: tuple) -> None:
        """Load the island and point the camera before the first frame: the view loads what is near its camera first."""
        editor, session, camera = opened
        view.load(session, editor)
        native = not session.island.get("game") and not session.anno2070_editing  # 117's rules for 117's islands
        view.opts = ViewOptions(scheme=view.opts.scheme, **(self.look if native else {}))
        view.opts.layers["markers"] = False  # the editor's markers are no part of the game's picture
        if camera is not None:
            camera.place(view, session)

    def ready(self, view) -> bool:
        """Nothing loading, or the count unchanged for ``QUIET`` seconds (the island editor's own pictures wait so)."""
        if view.renderer is None:
            return False
        busy, now = view.renderer.busy(), time.monotonic()
        if busy != self._busy:
            self._busy, self._since = busy, now
        return busy == 0 or now - self._since > QUIET

    @staticmethod
    def frame(view) -> None:
        """The camera stays where ``show`` put it (the game's step, else the island view's whole-island start)."""

    @staticmethod
    def release(view) -> None:
        """The view frees its renderer when it is closed."""


class Still(QObject):
    """One subject drawn offscreen at the reference frame's size; ``done(scene or exception)`` once, on the window's
    thread, unless it was ``cancel``led. The caller keeps it while it draws (its timer goes with it)."""

    def __init__(self, subject, done: Callable[[object], None], size: tuple[int, int] = worldscene.SIZE) -> None:
        super().__init__()
        self.subject, self.done, self.size = subject, done, size
        self.view, self.started, self.cancelled = None, 0.0, False
        self.background = Background(self)
        self.timer = QTimer(self)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self._tick)

    def start(self, backend: str | None = None, wait: bool = False) -> None:
        """Read the subject (off the window's thread unless ``wait``), then draw it on ``backend`` (``device()``)."""
        backend = backend or device()
        if backend is None:
            self.done(RuntimeError(tr("no Direct3D or OpenGL device can draw the scene here")))
            return
        self.background.run(self.subject.read, lambda got: self._read(got, backend), wait)

    def cancel(self) -> None:
        """Stop drawing; ``done`` is not called."""
        self.cancelled = True
        self._release()

    def _read(self, got, backend: str) -> None:
        if self.cancelled:
            return
        if isinstance(got, Exception):
            self.done(got)
            return
        try:
            self.view = self.subject.view(backend)
            self.subject.show(self.view, got)
        except (RuntimeError, OSError, ValueError) as error:  # no view on this device, the subject's files unreadable
            self._finish(error)
            return
        self.started = time.monotonic()
        self.timer.start()

    def _tick(self) -> None:
        """One frame: the subject drawn small while its files load; once they are in, framed and drawn at full size."""
        try:
            self.view.picture(*PROBE)
            if self.view.failed:
                raise RuntimeError(self.view.failed)
            if self.subject.ready(self.view):
                self.subject.frame(self.view)
                self.view.picture(*self.size)  # what loaded during the framing frame is drawn in the next
                self._finish(worldscene.from_image(self.view.picture(*self.size), self.subject.key))
            elif time.monotonic() - self.started > self.subject.wait:
                raise RuntimeError(
                    tr("the scene's files did not come in within {n} minutes", n=round(self.subject.wait / 60))
                )
        except (RuntimeError, ValueError) as error:
            self._finish(error)

    def _release(self) -> None:
        self.timer.stop()
        view, self.view = self.view, None
        if view is not None:
            self.subject.release(view)
            view.close()
            view.deleteLater()

    def _finish(self, result) -> None:
        self._release()
        self.done(result)
