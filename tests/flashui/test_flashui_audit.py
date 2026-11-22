"""Fixes of the Flash UI editor's audit: commands that fail with a sentence instead of a traceback, and the white-base
check of the preview over a large picture."""

from __future__ import annotations

import argparse
import os
import time

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage

from annotoolbox.flashui import uicommands, uirender


def test_a_corrupt_compressed_movie_is_refused_with_a_message(tmp_path, capsys):
    broken = tmp_path / "broken.swf"
    broken.write_bytes(b"CWS" + bytes(5) + b"not zlib data")
    args = argparse.Namespace(input=str(broken), output=str(tmp_path / "out.feu"))
    assert uicommands.cmd_from_swf(args) == 2
    assert "broken.swf" in capsys.readouterr().err
    assert not (tmp_path / "out.feu").exists()


def test_building_from_a_missing_edit_file_says_so_before_looking_for_the_game(tmp_path, capsys):
    args = argparse.Namespace(edits=str(tmp_path / "none.json"), game=None, out=str(tmp_path), loose=False)
    assert uicommands.cmd_build(args) == 2
    assert "none.json" in capsys.readouterr().err


def test_the_white_base_check_reads_a_large_picture_quickly():
    from PySide6.QtGui import QColor

    image = QImage(2048, 2048, QImage.Format.Format_RGBA8888)
    image.fill(QColor(255, 255, 255, 255))
    started = time.monotonic()
    assert uirender._flat_white(image, 240, solid=True)
    assert time.monotonic() - started < 2.0
    image.setPixelColor(100, 100, QColor(10, 10, 10, 255))
    assert not uirender._flat_white(image, 240, solid=True)
    assert np is not None
