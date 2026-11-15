"""Plays a sound clip for any app of the suite: ``ClipPlayer(parent).play(name, data, folder)`` decodes a clip (a
Wwise ``.wem``, WAV, MP3, Ogg) into ``folder`` once and hands it to Qt's media player; ``stop`` stops it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, QUrl

from ..exporting import banks
from ..infra.i18n import tr


class ClipPlayer(QObject):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._player = None
        self._output = None
        self.last_error = ""

    def play(self, name: str, data: bytes, folder: Path | None) -> str:
        """Play ``data`` (named ``name`` for its form); "" when it plays, else the sentence that says why not."""
        try:
            out, suffix = banks.playable(data, name)
        except banks.Unplayable as exc:
            return tr("cannot play: {error}", error=exc)
        if folder is None:
            return tr("no cache folder to play from")
        target = Path(folder) / (hashlib.sha1(data).hexdigest()[:16] + suffix)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(out)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        if self._player is None:
            self._player, self._output = QMediaPlayer(self), QAudioOutput(self)
            self._player.setAudioOutput(self._output)
            self._player.errorOccurred.connect(lambda _e, text: setattr(self, "last_error", text))
        self._player.setSource(QUrl.fromLocalFile(str(target)))
        self._player.play()
        return ""

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
