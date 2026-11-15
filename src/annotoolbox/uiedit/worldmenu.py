"""The window's commands for the 3D scene behind the UI preview (View menu): the switch, a picture picked, the
stand-in, a model or an island of the game drawn by the renderers.

``WorldMenu(window)`` holds them for the ``uiwindow.UiWindow``; ``apply`` puts the chosen scene behind the preview,
the choice kept in the settings (``world_scene``, and one of ``world_picture``, ``world_model``, ``world_island``).
docs/ui/117/world-effects.md, "What the editor does".
"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from ..infra.i18n import tr
from . import worldscene
from .uiframe import APP

CHOICES = ("world_picture", "world_model", "world_island")  # what the scene is: one of them, else the stand-in


class WorldMenu:
    def __init__(self, window) -> None:
        self.window = window
        self.still = None  # the model or island being drawn (``worldrender.Still``)

    def _settings(self) -> dict:
        return self.window.settings.apps.setdefault(APP, {})

    def toggled(self) -> None:
        self._settings()["world_scene"] = self.window.world_action.isChecked()
        self.apply()

    def _chosen(self, key: str | None, value: str = "") -> None:
        for other in CHOICES:
            self._settings().pop(other, None)
        if key is not None:
            self._settings()[key] = value
            self.window.world_action.setChecked(True)
        self.toggled()

    def pick(self) -> None:
        """A picture file as the scene."""
        name, _ = QFileDialog.getOpenFileName(
            self.window, tr("Pick a picture of the scene"), "", "Pictures (*.png *.jpg *.jpeg *.bmp)"
        )  # i18n: ok (a file filter)
        if name:
            self._chosen("world_picture", name)

    def stand_in(self) -> None:
        self._chosen(None)

    def pick_model(self) -> None:
        """A model config of the open game, drawn by the viewer's renderer as the scene."""
        from . import worldrender  # the renderers are loaded with their first use, not with the editor's start

        self._pick("world_model", lambda game: worldrender.configs(game.tree), tr("A model of the game"),
                   tr("The model drawn behind the UI:"), tr("this game has no model configs"))  # fmt: skip

    def pick_island(self) -> None:
        """An island of the open game, drawn by the island editor's renderer as the scene."""
        from . import worldrender

        settings = self.window.settings
        self._pick("world_island", lambda game: worldrender.islands(settings, game.key), tr("An island of the game"),
                   tr("The island drawn behind the UI:"), tr("this game has no islands the island editor opens"))  # fmt: skip

    def _pick(self, key: str, listed, title: str, label: str, none: str) -> None:
        """The game's files of a kind, listed off the window's thread, one of them picked as the scene."""
        window, game = self.window, self.window.game
        if game is None:
            window.statusBar().showMessage(tr("File > Open game first: the scene is one of its files"))
            return

        def got(names) -> None:
            if window.game is not game:
                return
            if isinstance(names, Exception) or not names:
                window.statusBar().showMessage(none if not isinstance(names, Exception) else str(names))
                return
            path, ok = QInputDialog.getItem(window, title, label, names, 0, True)
            if ok and path in names:
                self._chosen(key, path)

        window.background.run(lambda: listed(game), got, window.sync_layout)

    def game_opened(self) -> None:
        """Another game is open: a model or island is one of its files (one of the game before is dropped)."""
        if self.still is not None or any(self._settings().get(k) for k in CHOICES[1:]):
            self.apply()

    def apply(self) -> None:
        """Put the scene behind the preview (the model or island drawn, the picked picture, else the stand-in), or take
        it away; a model or island still being drawn is dropped."""
        window, settings = self.window, self._settings()
        if self.still is not None:
            self.still.cancel()
            self.still = None
        if not window.world_action.isChecked():
            window.preview.set_world(None)
            return
        drawn = next(((k, settings[k]) for k in CHOICES[1:] if settings.get(k)), None)
        if drawn and window.game is not None:
            self._draw(*drawn)
            return
        name = settings.get("world_picture")
        try:
            scene = worldscene.load(name) if name else worldscene.stand_in()
        except ValueError as error:
            QMessageBox.warning(window, window.windowTitle(), str(error))
            settings.pop("world_picture", None)
            scene = worldscene.stand_in()
        window.preview.set_world(scene)

    def _draw(self, key: str, path: str) -> None:
        """A model or island drawn by the renderers; the scene shown before stays until it is in."""
        from . import worldrender

        window, game = self.window, self.window.game
        if window.preview.world is None:
            window.preview.set_world(worldscene.stand_in())
        window.statusBar().showMessage(tr("drawing {path} behind the UI ...", path=path))
        if key == "world_model":
            subject = worldrender.Model(game.tree, game.key, path)
        else:
            subject = worldrender.Island(window.settings, game.key, path, tree=game.tree)  # its camera.xml

        def done(result) -> None:
            if self.still is not still:
                return  # another scene was chosen meanwhile, or the switch turned off
            self.still = None
            if isinstance(result, Exception):
                window.statusBar().showMessage(tr("the scene was not drawn: {error}", error=result))
                return
            window.preview.set_world(result)
            window.statusBar().showMessage(tr("{path} is the scene behind the UI", path=path))

        still = self.still = worldrender.Still(subject, done)
        still.start()
