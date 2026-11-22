import io
from pathlib import Path

import pytest
from tests.fakes import FakeFiles
from tests.flashui.flashuifakes import ICON_PATH as ICON
from tests.flashui.flashuifakes import PICTURE
from tests.othergames.othergamesfakes import MOVIE, PNG_PATH, index_for, ui_files

from annotoolbox.formats import feuassets, feuread
from annotoolbox.othergames import delivery2205, ui2205
from annotoolbox.othergames.index2205 import FILE_DB, CArchiveIndex


def test_a_movies_image_names_turn_into_game_paths():
    assert ui2205.image_path(PICTURE) == PNG_PATH
    assert (
        ui2205.image_path("ExtractedFireFiles\\PNGs\\icons\\DLC3\\x.png")
        == "data/ui/extractedfirefiles/icons/dlc3/x.png"
    )
    assert ui2205.texture_path(PNG_PATH) == "data/ui/extractedfirefiles/uiassets/button_up_0.dds"


def test_the_ui_of_an_install_reads_through_any_file_reader():
    ui = ui2205.CUiFiles(FakeFiles(ui_files()))
    assert ui.movies() == [MOVIE]
    assert ui.image(PICTURE) == b"png" and ui.image("missing.png") is None
    assert ui.sizes().get(PNG_PATH) == (4, 2)
    table, entry = ui.atlas_entry(ICON)
    assert table == "data/ui/blobs/icons.png" and entry.origin == (1, 1)
    assert ui.users(PICTURE.upper()) == [MOVIE]
    assert isinstance(ui.movie(MOVIE), feuread.SMovie)


def test_a_replaced_picture_brings_its_texture_and_its_size():
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGBA", (3, 5), (1, 2, 3, 4)).save(buf, "PNG")
    sizes = feuassets.CPngSizes()
    files = ui2205.image_files(PNG_PATH, buf.getvalue(), sizes)
    assert set(files) == {PNG_PATH, ui2205.texture_path(PNG_PATH)}
    assert feuassets.dds_rgba(files[ui2205.texture_path(PNG_PATH)]) == (3, 5, bytes([1, 2, 3, 4]) * 15)
    assert sizes.get(PNG_PATH) == (3, 5)


def test_a_build_points_the_index_at_our_archive_and_installs_and_uninstalls(tmp_path: Path):
    game = tmp_path / "game"
    (game / "maindata").mkdir(parents=True)
    index = index_for([MOVIE, PNG_PATH])
    (game / FILE_DB).write_bytes(index)
    out = tmp_path / ui2205.OUT_NAME
    backup = delivery2205.backup_file(game)  # shared with the sessions, outside any build folder
    with pytest.raises(ValueError, match="no entry"):
        ui2205.build({"data/ui/new.feu": b"x"}, index, out, log=lambda _m: None)
    with pytest.raises(ValueError, match="refusing"):
        ui2205.build({MOVIE: b"x"}, index, tmp_path / "elsewhere", log=lambda _m: None)
    ui2205.build({MOVIE: b"changed movie"}, index, out, log=lambda _m: None)
    built = CArchiveIndex((out / FILE_DB).read_bytes())
    entry = built.entry(MOVIE)
    assert built.archive_names()[entry.archive] == ui2205.ARCHIVE and entry.flags == 0
    data = (out / ui2205.ARCHIVE).read_bytes()
    assert data[entry.position : entry.position + entry.size] == b"changed movie"
    assert built.entry(PNG_PATH) == CArchiveIndex(index).entry(PNG_PATH)
    ui2205.install(out, game, log=lambda _m: None)
    assert ui2205.is_ours((game / FILE_DB).read_bytes()) and backup.read_bytes() == index
    assert ui2205.base_index(game) == index  # a rebuild starts from the index ours replaced
    assert ui2205.uninstall(game, log=lambda _m: None)
    assert (game / FILE_DB).read_bytes() == index and not (game / ui2205.ARCHIVE).exists()


def test_loose_files_go_under_the_games_folders(tmp_path: Path):
    written = ui2205.write_loose({MOVIE: b"m"}, tmp_path)
    assert written == [tmp_path / MOVIE] and (tmp_path / MOVIE).read_bytes() == b"m"
