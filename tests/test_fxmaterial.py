"""``formats/fxmaterial``: the material structures as the engine lays them out (types, rows, the switch words in the
gaps, the padding, the index table by name), on a synthetic description; with an Anno 117 install
(``ANNO117_DIR``, Windows) every structure against the shipped shaders' reflection."""

from __future__ import annotations

import os

import pytest
from tests.fakes import installed

from annotoolbox.formats import fxmaterial

FXM = """material Demo [option: ENABLE_X]
{
    section Common
    {
        bool SOME_SWITCH { default = false }              // a bool field: a float
        Texture2D cMap { name = "Map", }
        Texture2D cOther { name = "Other", }
        define NOT_A_FIELD { type = bool, }               // a bit of DefineFlags0
        color cTint { default = color(1.0), }             // a float3: it does not cross a row
        define COUNT { type = int, default = 2 }          // an int switch: Define_COUNT
        float cGloss [required: ENABLE_X] { default = 0.5 }
    }
}"""


def test_a_structure_is_laid_out_as_the_engines():
    m = fxmaterial.read_material_fields(FXM)
    assert m.name == "Demo" and m.flag_count == 1
    assert m.fields == [("float", "SOME_SWITCH"), ("uint", "cMap"), ("uint", "cOther"), ("float3", "cTint"),
                        ("int", "Define_COUNT"), ("float", "cGloss")]  # fmt: skip
    assert m.layout == [
        ("float", "SOME_SWITCH"), ("uint", "cMap"), ("uint", "cOther"), ("uint", "DefineFlags0"),  # the gap at 12
        ("float3", "cTint"), ("int", "Define_COUNT"), ("float", "cGloss"),
        ("float", "_padding0"), ("float", "_padding1"), ("float", "_padding2"),  # 36 bytes, rounded to 64
        ("float", "_padding3"), ("float", "_padding4"), ("float", "_padding5"), ("float", "_padding6"),
    ]  # fmt: skip
    with pytest.raises(fxmaterial.MaterialError):
        fxmaterial.read_material_fields("section only")


def test_sizes_round_to_a_power_of_two_then_to_rows_of_128_bytes_and_the_table_goes_by_name():
    small = fxmaterial.lay_out([("uint", "a"), ("uint", "b")], 1)
    assert small[-1] == ("float", "_padding0") and len(small) == 4  # 12 bytes -> 16
    big = fxmaterial.lay_out([("float4", f"v{i}") for i in range(9)], 0)
    assert len(big) == 9 + 4 * 7  # 144 bytes -> 256
    header = fxmaterial.material_header([fxmaterial.read_material_fields(FXM.replace("Demo", n)) for n in "BA"])
    table = header[header.index("struct MaterialComponentIndexTable") :]
    assert table.index("uint AIndex;") < table.index("uint BIndex;") and "uint _padding0;" in table


GAME = installed("anno117")


@pytest.mark.skipif(GAME is None or os.name != "nt", reason="needs an Anno 117 install (ANNO117_DIR) and Windows")
def test_every_shipped_structure_has_our_layout():
    from annotoolbox.infra import dxc
    from annotoolbox.models import shadercache

    game, comp = shadercache.GameShaders(GAME), dxc.Dxc.find(anno117=os.environ["ANNO117_DIR"])
    ours = {}
    for p in GAME.iter_paths(shadercache.MATERIAL_DIR):
        m = fxmaterial.read_material_fields(GAME.read(p).decode("utf-8", "replace"))
        at, ours[m.name + "Struct"] = 0, []
        for kind, name in m.layout:
            ours[m.name + "Struct"].append((name, at))
            at += fxmaterial.SIZES.get(kind, 4)
    seen = set()
    for stem in ("cloth", "model_pbr", "model_decal", "decal_pbr", "model_frame_cycle", "model_water"):
        path = game.shipped_of(stem, game.info(stem).vanilla_hash, 1)[0][0]
        for shader in shadercache.read_cached_shaders(GAME.read(path)):
            for cb in comp.reflect(shader.bytecode).cbuffers:
                for v in cb.variables:
                    if v.type in ours:
                        assert [(x.name, x.offset) for x in v.members] == ours[v.type], v.type
                        seen.add(v.type)
    assert {"ClothStruct", "ModelCommonStruct", "PhysicallyBasedSubstanceStruct"} <= seen
