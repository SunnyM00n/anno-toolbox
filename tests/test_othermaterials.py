"""The other game's ground materials of an engine generation for a palette (``editor.othermaterials``): listed from the
game's material sets, brought in under ``data/imported`` in the island's game's style; and the map pictures of an
underwater island redrawn in 2070's underwater look (``islands.mappictures``)."""

import io

import numpy as np
import pytest
from PIL import Image

from annotoolbox.editor import othermaterials
from annotoolbox.islands import mappictures
from fakes import FakeFiles

SET_117 = "data/base/graphics/library/environment/material_sets/roman_materialset_02.xml"
MATERIAL_117 = "data/base/graphics/library/environment/materials/sand/beach_04/beach_04.xml"


def _files() -> FakeFiles:
    return FakeFiles(
        {
            SET_117: f"<MaterialSet><Material><ID>7</>\n<ImportLabel>sand_base</>\n<FileName>{MATERIAL_117}</>\n</>\n".encode(),
            MATERIAL_117: (
                b"<Material>\n<DiffuseFileName>data/base/graphics/library/environment/materials/sand/beach_04/"
                b"beach_04_diff.psd</DiffuseFileName>\n<SpecFileName></SpecFileName>\n</Material>"
            ),
            "data/base/graphics/library/environment/materials/sand/beach_04/beach_04_diff_0.dds": b"tex",
        }
    )


def test_a_games_materials_are_listed_with_their_game():
    (m,) = othermaterials.materials(_files(), "anno117")
    assert (m["file"], m["set"], m["name"], m["game"]) == (MATERIAL_117, SET_117, "sand_base", "anno117")
    assert othermaterials.materials(_files(), "anno1800") == []  # 1800's sets are in another folder


def test_a_117_material_is_brought_into_1800_in_its_style_below_imported():
    entry, files = othermaterials.bring_in(_files(), "anno117", {"file": MATERIAL_117, "set": SET_117}, "anno1800")
    assert entry["file"] == "data/imported/117/base/graphics/library/environment/materials/sand/beach_04/beach_04.xml"
    text = files[entry["file"]].decode()
    assert "<DiffuseFileName>data/imported/117/base/graphics/" in text and "</DiffuseFileName>" not in text
    assert "<SpecFileName></>" in text  # an empty map stays empty
    assert (
        files["data/imported/117/base/graphics/library/environment/materials/sand/beach_04/beach_04_diff_0.dds"]
        == b"tex"
    )
    assert entry["file"] in files[entry["set"]].decode() and entry["from"] == MATERIAL_117
    palette, reader, extra, missing = othermaterials.bring_in_all(
        _files(), {"3": {"file": MATERIAL_117, "set": SET_117, "game": "anno117"}}, "anno1800", None
    )
    assert palette == {} and extra == {} and missing == [f"material {MATERIAL_117} of anno117"]


def test_an_underwater_islands_pictures_are_redrawn_in_its_look():
    size = 8
    old = np.full((2 * size + 1, 2 * size + 1), -40.0)
    old[4:13, 4:13] = -27.0  # the plateau
    new = old.copy()
    new[7:10, 7:10] = -24.0  # a rise on it
    assert mappictures.underwater_floor(new) == -28.0 and mappictures.underwater_floor(new + 50) is None
    buf = io.BytesIO()
    Image.new("RGBA", (2 * size, 2 * size), (90, 120, 60, 0)).save(buf, "PNG")
    done = mappictures.redraw({"gamemapimage.png": buf.getvalue()}, old, new, (0, 0, size, size), size)
    pixels = np.asarray(Image.open(io.BytesIO(done["gamemapimage.png"]))).astype(int)
    assert pixels[8, 8, 3] == 255 and pixels[0, 0, 3] == 0  # the plateau is land, the floor round it is not
    assert pixels[8, 8, 2] > pixels[8, 8, 0]  # teal, not the ground's colour


DIFFUSE = "data/base/graphics/library/environment/materials/sand/beach_04/beach_04_diff"


def _dds() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (4, 4), (200, 120, 40, 90)).save(buf, "DDS")
    return buf.getvalue()


def test_a_listed_material_names_its_textures_where_the_build_puts_them_and_the_editor_reads_them_there():
    files = _files()
    files.files[DIFFUSE + "_0.dds"] = _dds()
    (m,) = othermaterials.materials(files, "anno117")
    assert (
        m["diffuse"] == "data/imported/117/base/graphics/library/environment/materials/sand/beach_04/beach_04_diff.psd"
    )
    assert othermaterials.game_of(m["diffuse"]) == "anno117" and othermaterials.game_of("data/base/x.psd") is None
    assert othermaterials.original_path(m["diffuse"]) == DIFFUSE + ".psd"
    png = Image.open(io.BytesIO(othermaterials.texture_png(files, m["diffuse"], opaque=True)))
    assert png.mode == "RGB" and png.getpixel((0, 0)) == (200, 120, 40)  # the ground's alpha is a blend weight
    mask = Image.open(io.BytesIO(othermaterials.texture_png(files, m["diffuse"], alpha=True)))
    assert mask.getpixel((0, 0))[0] == 90
    with pytest.raises(FileNotFoundError):
        othermaterials.texture_png(files, "data/imported/117/base/none.psd")


def test_an_editor_with_the_mixin_reads_another_games_texture_through_that_game():
    class Own:
        def texture_png(self, psd, normal=False, opaque=False, alpha=False):
            return b"own " + psd.encode()

    class Editor(othermaterials.OtherMaterials, Own):
        pass

    files = _files()
    files.files[DIFFUSE + "_0.dds"] = _dds()
    editor = Editor()
    editor.other_games = {"anno117": files}
    (m,) = editor.vanilla_materials()
    assert editor.texture_png("data/own/x.psd") == b"own data/own/x.psd"
    assert Image.open(io.BytesIO(editor.texture_png(m["diffuse"], opaque=True))).getpixel((1, 1)) == (200, 120, 40)
    assert Editor().vanilla_materials() == []


def test_an_entry_whose_material_file_does_not_exist_is_not_offered():
    files = _files()
    set_text = files.files[SET_117].decode() + "<Material><ID>8</>\n<FileName>data/test/gone.xml</>\n</>\n"
    files.files[SET_117] = set_text.encode()
    assert [m["name"] for m in othermaterials.materials(files, "anno117")] == ["sand_base"]
