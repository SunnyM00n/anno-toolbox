"""The window's commands for the 3D scene behind the UI preview (View menu): the switch, a picture picked, the
stand-in, a model or an island of the game drawn by the renderers.

``CWorldMenu(window)`` holds them for the ``uiwindow.CUiWindow``; ``apply`` puts the chosen scene behind the preview,
the choice kept in the settings (``world_scene``, and one of ``world_picture``, ``world_model``, ``world_island``).
docs/ui/117/world-effects.md, "What the editor does".
"""

from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from ..infra.i18n import tr
from . import worldscene
from .uiframe import APP

CHOICES = ("world_picture", "world_model", "world_island")  # what the scene is: one of them, else the stand-in


class CWorldMenu:
    def __init__(self, window) -> None:
        self.m_window = window
        self.m_still = None  # the model or island being drawn (``worldrender.CStill``)

    def _settings(self) -> dict:
        return self.m_window.settings.apps.setdefault(APP, {})

    def toggled(self) -> None:
        self.apply()

    def _chosen(self, key: str | None, value: str = "") -> None:
        for other in CHOICES:
            self._settings().pop(other, None)
        if key is not None:
            self._settings()[key] = value
            self.m_window.world_action.setChecked(True)
        self.toggled()

    def pick(self) -> None:
        """A picture file as the scene."""
        name, _ = QFileDialog.getOpenFileName(
            self.m_window, tr("ui.pick_picture_scene"), "", "Pictures (*.png *.jpg *.jpeg *.bmp)"
        )  # i18n: ok (a file filter)
        if name:
            self._chosen("world_picture", name)

    def stand_in(self) -> None:
        self._chosen(None)

    def pick_model(self) -> None:
        """A model config of the open game, drawn by the viewer's renderer as the scene."""
        from . import worldrender  # the renderers are loaded with their first use, not with the editor's start

        self._pick("world_model", lambda game: worldrender.configs(game.m_tree), tr("ui.model_game"),
                   tr("ui.model_drawn_behind_ui"), tr("ui.game_no_model_configs"))  # fmt: skip

    def pick_island(self) -> None:
        """An island of the open game, drawn by the island editor's renderer as the scene."""
        from . import worldrender

        settings = self.m_window.settings
        self._pick("world_island", lambda game: worldrender.islands(settings, game.m_key), tr("ui.island_game"),
                   tr("ui.island_drawn_behind_ui"), tr("ui.game_no_islands_island_editor"))  # fmt: skip

    def _pick(self, key: str, listed, title: str, label: str, none: str) -> None:
        """The game's files of a kind, listed off the window's thread, one of them picked as the scene."""
        window, game = self.m_window, self.m_window.game
        if game is None:
            window.statusBar().showMessage(tr("ui.file_gt_open_game_first"))
            return

        def got(names) -> None:
            if window.game is not game:
                return
            if isinstance(names, Exception) or not names:
                window.statusBar().showMessage(none if not isinstance(names, Exception) else str(names))
                return
            path, is_accepted = QInputDialog.getItem(window, title, label, names, 0, True)
            if is_accepted and path in names:
                self._chosen(key, path)

        window.m_background.run(lambda: listed(game), got, window.m_sync_layout)

    def game_opened(self) -> None:
        """Another game is open: a model or island is one of its files (one of the game before is dropped)."""
        if self.m_still is not None or any(self._settings().get(k) for k in CHOICES[1:]):
            self.apply()

    def close(self) -> None:
        """The window closes: a model or island still being drawn is dropped with its view."""
        if self.m_still is not None:
            self.m_still.cancel()
            self.m_still = None

    def apply(self) -> None:
        """Put the scene behind the preview (the model or island drawn, the picked picture, else the stand-in), or take
        it away; a model or island still being drawn is dropped."""
        window, settings = self.m_window, self._settings()
        self.close()
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

        window, game = self.m_window, self.m_window.game
        if window.preview.m_world is None:
            window.preview.set_world(worldscene.stand_in())
        window.statusBar().showMessage(tr("ui.drawing_behind_ui", path=path))
        if key == "world_model":
            subject = worldrender.CModel(game.m_tree, game.m_key, path)
        else:
            subject = worldrender.CIsland(window.settings, game.m_key, path, tree=game.m_tree)  # its camera.xml

        def done(result) -> None:
            if self.m_still is not still:
                return  # another scene was chosen meanwhile, or the switch turned off
            self.m_still = None
            if isinstance(result, Exception):
                window.statusBar().showMessage(tr("ui.scene_not_drawn", error=result))
                return
            window.preview.set_world(result)
            window.statusBar().showMessage(tr("ui.scene_behind_ui", path=path))

        still = self.m_still = worldrender.CStill(subject, done)
        still.start()
