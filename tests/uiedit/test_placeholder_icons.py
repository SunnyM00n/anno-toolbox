"""Where a game's real icon replaces a drawn stand-in: the UI preview's run-time picture slots, and the icon key of
an asset the island editor's Place list shows. Synthetic trees only."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("numpy")
pytest.importorskip("PIL")

import io
from types import SimpleNamespace

from PIL import Image
from PySide6.QtWidgets import QApplication

from annotoolbox.assetedit import icons
from annotoolbox.sources import icons2070
from annotoolbox.uiedit import canvas, layout
from annotoolbox.uiedit.pictures import CPictures


def _png(colour) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (4, 4), colour).save(out, "PNG")
    return out.getvalue()


class FakeGameTree:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.m_paths = files, sorted(files)

    def exists(self, path: str) -> bool:
        return path in self.files

    def read(self, path: str) -> bytes:
        return self.files[path]


def _pictures(files: dict[str, bytes]) -> CPictures:
    return CPictures(SimpleNamespace(m_tree=FakeGameTree(files), root=lambda _a: None))


def test_a_run_time_slot_gets_the_game_icon_whose_name_shares_its_word():
    files = {
        "data/ui/icons/goods_bread.png": _png((200, 0, 0, 255)),
        "data/ui/icons/goods_wine.png": _png((0, 0, 200, 255)),
        "data/ui/other/not_an_icon.png": _png((0, 200, 0, 255)),
    }
    pics = _pictures(files)
    found = pics.sample("WineIcon")
    assert found is not None and tuple(found.rgba[0, 0]) == (0, 0, 200, 255)
    assert pics.sample("WineIcon") is found  # decoded once, kept
    assert not pics.m_standins


def test_a_slot_no_word_matches_gets_one_icon_picked_by_its_name_and_always_the_same():
    files = {"data/ui/icons/a.png": _png((9, 9, 9, 255)), "data/ui/icons/b.png": _png((7, 7, 7, 255))}
    first = _pictures(files).sample("Zzz")
    again = _pictures(files).sample("Zzz")
    assert first is not None and tuple(first.rgba[0, 0]) == tuple(again.rgba[0, 0])


def test_a_game_without_icon_files_keeps_the_tile_and_lists_the_slot():
    pics = _pictures({"data/ui/other/x.png": _png((1, 1, 1, 255))})
    assert pics.sample("Portrait") is None
    assert pics.m_standins == {"Portrait"}


def test_the_canvas_draws_the_icon_for_a_placeholder_picture():
    pics = _pictures({"data/ui/icons/goods_wine.png": _png((0, 0, 200, 255))})
    item = layout.SDrawItem("image", layout.IDENTITY, (4.0, 4.0), image_id="placeholder:WineIcon")
    drawn = []

    class FakeCanvas:
        def quad(self, it, pic):
            drawn.append(pic)

        def text(self, it):
            drawn.append("tile")

    canvas._draw_item(FakeCanvas(), item, pics)
    assert drawn and drawn[0] is not None and drawn[0] != "tile"
    drawn.clear()
    canvas._draw_item(FakeCanvas(), item, _pictures({}))
    assert drawn[-1] == "tile"  # no icon in the game: the tile and its name


def test_an_asset_without_an_icon_file_takes_its_atlas_tile_as_the_icon_key():
    QApplication.instance() or QApplication([])
    source = icons.CIconSource(lambda path: b"")
    atlas = icons2070.SIconAtlas(3, "data/ui/atlas.png", 32, 32)
    source.use_atlases({3: atlas}, {100: [icons2070.SIconRef(3, 5)]})
    assert source.key_for(100) == icons.atlas_key(100)
    assert source.key_for(100, "data/ui/own.png") == "data/ui/own.png"  # a file the asset names wins
    assert source.key_for(101) == ""  # the game has no icon for it: the row says so in its tooltip
