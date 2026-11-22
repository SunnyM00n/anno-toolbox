"""2070's ground materials read for the island editor (``islands.terrain2070``)."""

from tests.fakes import FakeFiles

from annotoolbox.islands import terrain2070

ENTRY = (
    "<Material><ID>{id}</>\r\n<Type>0</>\r\n<FileName>{file}</>\r\n<ImportLabel>{label}</>\r\n"
    "<Repetition>6.000000</>\r\n<AlphaSharpness>1.000000</>\r\n</>\r\n"
)
VANILLA_SET = "data/base/graphics/library/environment/material_sets/roman_materialset_02.xml"
MUD = "data/base/graphics/library/environment/materials/default/default_mud.xml"


def _vanilla() -> FakeFiles:
    text = "<MaterialSet><ShaderType>0</>\r\n" + ENTRY.format(id=3, file=MUD, label="mud") + "</>\r\n"
    return FakeFiles(
        {
            VANILLA_SET: text.encode(),
            MUD: b"<Material>\r\n<DiffuseFileName>data/base/mud_diff.psd</>\r\n<NormalFileName>data/base/mud_norm.psd</>\r\n</>\r\n",
        }
    )


def test_vanilla_materials_lists_each_once_with_its_maps():
    (mud,) = terrain2070.vanilla_materials(_vanilla())
    assert mud == {
        "file": MUD,
        "set": VANILLA_SET,
        "name": "mud",
        "diffuse": "data/base/mud_diff.psd",
        "normal": "data/base/mud_norm.psd",
        "repetition": 6.0,
        "rank": 0,  # its place in its set: 117's draw order
    }


def test_island_material_set_adds_vanilla_materials_at_their_ids(tmp_path):
    base = "<MaterialSet><ShaderType>0</>\r\n" + ENTRY.format(id=0, file="a.cfg", label="a") + "</>\r\n"
    (tmp_path / terrain2070.SET_PATH).parent.mkdir(parents=True)
    (tmp_path / terrain2070.SET_PATH).write_bytes(base.encode())
    path = terrain2070.island_material_set(_vanilla(), tmp_path, {"30": {"file": MUD, "set": VANILLA_SET}}, "isle")
    text = (tmp_path / path).read_bytes().decode()
    assert path.endswith("a2070_materialset_isle.cfg")
    assert text.count("<Material>") == 2 and f"<ID>30</>\r\n<Type>0</>\r\n<FileName>{MUD}</>" in text
    assert text.endswith("</>\r\n</>\r\n") and "\r\r" not in text  # still closed, still CRLF


def test_an_added_material_is_listed_by_its_family(tmp_path):
    """117 draws in list order: an added 117 ground goes under the island's rock (materialorder)."""
    import re

    entries = [(0, "water"), (1, "mountain_00"), (2, "beach_01"), (3, "gras_01"), (4, "mountain_01")]
    base = "<MaterialSet><ShaderType>0</>\r\n" + "".join(
        ENTRY.format(id=i, file=f"{n}.cfg", label=n) for i, n in entries
    )
    (tmp_path / terrain2070.SET_PATH).parent.mkdir(parents=True)
    (tmp_path / terrain2070.SET_PATH).write_bytes((base + "</>\r\n").encode())
    extra = {"30": {"file": MUD, "set": VANILLA_SET}}  # "mud": soil, ranked with the grasses
    order = terrain2070.material_draw_order(_vanilla(), tmp_path, extra)
    assert order == [0, 30, 1, 2, 3, 4]  # before the first rock
    text = (tmp_path / terrain2070.island_material_set(_vanilla(), tmp_path, extra, "isle")).read_bytes().decode()
    assert [int(x) for x in re.findall(r"<ID>(\d+)</>", text)] == order and text.endswith("</>\r\n</>\r\n")


def test_painting_a_material_the_layers_do_not_use_adds_its_layer():
    import numpy as np

    from annotoolbox.islands.islandground import paint_strokes, painted_layers

    weights = np.ones((1, 8, 8), np.float32)
    strokes = [{"material": 30, "radius": 1.0, "strength": 1.0, "points": [[1.0, 1.0]]}, {"material": 40}]
    mats, weights = painted_layers(weights, [0], strokes)
    assert mats == [0, 30] and weights.shape == (2, 8, 8)  # 40 is no material ID (5 bits)
    out = paint_strokes(weights, mats, strokes, 4)
    assert out[1, 4, 4] > 0.9 and np.allclose(out.sum(axis=0), 1.0)


def test_every_material_tiles_at_2070s_global_texture_scale(tmp_path):
    from annotoolbox.sources.overlay import CSourceTree

    rel = "data/config/engine/groundtextures.xml"
    for archive, scale in (("Data1", 4), ("patch1", 6)):  # the patch wins
        (tmp_path / archive / rel).parent.mkdir(parents=True)
        (tmp_path / archive / rel).write_text(
            f"<GroundTextures>\n  <TextureScale>{scale}</TextureScale>\n</GroundTextures>"
        )
    assert terrain2070.texture_scale(CSourceTree(tmp_path)) == 6.0
    empty = tmp_path / "empty"
    (empty / "Data1" / rel).parent.mkdir(parents=True)
    (empty / "Data1" / rel).write_text("<GroundTextures/>")
    assert terrain2070.texture_scale(CSourceTree(empty)) == terrain2070.DEFAULT_TEXTURE_SCALE == 4.0
    assert terrain2070.material_style("u_mountain_01") == (2.3, "Rock")  # the name only sets the blend


def test_a_material_sets_entries_name_their_material_files_by_id():
    from annotoolbox.islands import materialorder

    text = r"<MaterialSet><Material><ID>3</>" + "\r\n" + r"<FileName>data\a\x.xml</>" + "\r\n</>\r\n"
    text += "<Material><ID>7</>\n<Other/>\n</>\n"
    assert materialorder.set_files(text) == {3: "data/a/x.xml"}  # an entry with no file is none
    assert materialorder.set_ranks(text) == {3: 0, 7: 1}
