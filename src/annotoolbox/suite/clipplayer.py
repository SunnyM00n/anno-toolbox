"""Plays a sound clip for any app of the suite: ``ClipPlayer(parent).play(name, data, folder)`` decodes a clip (a
Wwise ``.wem``, WAV, MP3, Ogg) into ``folder`` once and hands it to Qt's media player; ``stop`` stops it.
``prepare`` is the decoding half, which may run off the window's thread, ``start`` the playing half.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QUrl

from ..exporting import banks
from ..infra.buildcache import write_atomic
from ..infra.i18n import tr


class ClipPlayer(QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._player = None
        self._output = None
        self.last_error = ""

    def play(self, name: str, data: bytes, folder: Path | None) -> str:
        """Play ``data`` (named ``name`` for its form); "" when it plays, else the sentence that says why not."""
        target = self.prepare(name, data, folder)
        if isinstance(target, str):
            return target
        self.start(target)
        return ""

    @staticmethod
    def prepare(name: str, data: bytes, folder: Path | None) -> Path | str:
        """The decoded clip's file in ``folder`` (written once per content), or the sentence that says why it
        cannot be played. Touches nothing of Qt: any thread may run it."""
        try:
            out, suffix = banks.playable(data, name)
        except banks.Unplayable as exc:
            return tr("common.cannot_play_error", error=exc)
        if folder is None:
            return tr("viewer.no_cache_folder_play")
        target = Path(folder) / (hashlib.sha1(data).hexdigest()[:16] + suffix)
        if not target.is_file():
            try:
                write_atomic(target, out)  # a clip cut short by a crash would be taken for the whole one ever after
            except OSError as exc:  # a full disk, a cache folder that cannot be written
                return tr("common.cannot_play_error", error=exc)
        return target

    def start(self, target: Path) -> None:
        """Play a file ``prepare`` gave (the window's thread)."""
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        if self._player is None:
            self._player, self._output = QMediaPlayer(self), QAudioOutput(self)
            self._player.setAudioOutput(self._output)
            self._player.errorOccurred.connect(lambda _e, text: setattr(self, "last_error", text))
        self._player.setSource(QUrl.fromLocalFile(str(target)))
        self._player.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
