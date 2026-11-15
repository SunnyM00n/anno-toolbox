"""The asset editor on the installed older games (gated on ANNO2070_DIR, ANNO1404_DIR, ANNO1701_DIR): the icons that
``icons.xml`` gives their GUIDs, and an archive of the game's own files imported as no change."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from annotoolbox.assetedit import archiveimport
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.icons import IconSource

VARIABLES = {"anno2070": "ANNO2070_DIR", "anno1404": "ANNO1404_DIR", "anno1701": "ANNO1701_DIR"}


def installed_game(key: str, tmp_path: Path):
    folder = os.environ.get(VARIABLES[key])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"{VARIABLES[key]} is not set")
    return open_game(key, Path(folder), tmp_path)


@pytest.mark.parametrize("key", VARIABLES)
def test_the_assets_of_the_game_get_their_atlas_tiles(key, tmp_path):
    QApplication.instance() or QApplication([])
    game = installed_game(key, tmp_path)
    doc, source = game.document(), IconSource(game.files.read)
    source.use_atlases(*game.icon_data)
    keyed = [(e.guid, source.key_of(doc, e.guid)) for e in game.index.entries[:1500]]
    keyed = [(g, k) for g, k in keyed if k]
    assert len(keyed) > 100
    assert all(source.image(k) is not None for _g, k in keyed)  # every tile cuts out of an atlas picture that decodes
    guid, key_ = keyed[0]
    ref = game.icon_data[1][guid][0]
    atlas = game.icon_data[0][ref.file_id]
    tile = source.image(key_)
    assert (tile.width(), tile.height()) == (atlas.tile_width, atlas.tile_height)
    assert source.describe(key_).startswith("tile ") and not source.picture(key_).isNull()


def test_the_gamevalues_and_the_balancing_of_1701_are_assets_of_the_open_game(tmp_path):
    game = installed_game("anno1701", tmp_path)
    doc = game.document()
    assert doc.exists(-1) and doc.layers(-1).lookup(("NeedLevels", "Pioneer", "PioneerNeedsDraperyCount")).text == "48"
    assert any(doc.template(e.guid) == "Balancing only" for e in game.index.entries)


@pytest.mark.parametrize("key", ["anno2070", "anno1404"])
def test_an_archive_of_the_games_own_files_is_no_change(key, tmp_path):
    from annotoolbox.formats.rda import write_v20

    game = installed_game(key, tmp_path)
    path = game.layout.assets[0]
    archive = tmp_path / "same.rda"
    archive.write_bytes(write_v20({path: game.files.read(path)}))
    result = archiveimport.import_archive(game, archive, tmp_path / "carry")
    assert not result.changes.assets and not result.changes.new and not result.changes.removed
    assert result.carried == [] and result.changes.texts == {}
