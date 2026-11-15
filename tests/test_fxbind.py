"""``formats/fxbind``: where the engine binds a pass's resources (the global table's slots, the bindless tables, the
effect's tables by kind and name, root constants, the spaces in table order), on synthetic resources."""

from __future__ import annotations

from annotoolbox.formats import fxbind
from annotoolbox.formats.fxbind import Resource


def res(name, kind, *words, slot=None, count=1, file="x.fx"):
    return Resource(name, kind, frozenset(words), slot, None, count, file)


def test_the_global_table_numbers_each_kind_after_the_kinds_before_it():
    binds = fxbind.bind(
        [
            res("cbPerFrame", "CB", "global_table", slot=0),
            res("cTerrainHeightTex", "TX_SRV", "global_table", slot=11),
            res("cCameraBuffer", "SB_SRV", "global_table", slot=4),
            res("cDebugOverlayTarget", "TX_UAV", "global_table", slot=0),
        ]
    )
    assert binds == {
        "cbPerFrame": "register(b0, space0)", "cTerrainHeightTex": "register(t17, space0)",
        "cCameraBuffer": "register(t59, space0)", "cDebugOverlayTarget": "register(u73, space0)",
    }  # fmt: skip


def test_an_effect_table_goes_by_kind_then_name_and_an_array_takes_its_length():
    binds = fxbind.bind(
        [
            res("cDstColor", "TX_UAV"), res("cGBuffer", "TX_SRV", count=3), res("cbShading", "CB"),
            res("cDepth", "TX_SRV"), res("cTiles", "BA_SRV"), res("cDstAlpha", "TX_UAV"),
        ]
    )  # fmt: skip
    assert binds["cbShading"] == "register(b0, space0)"  # no global resource: the effect's table is space 0
    assert [binds[n] for n in ("cDepth", "cGBuffer", "cTiles", "cDstAlpha", "cDstColor")] == [
        "register(t1, space0)", "register(t2, space0)", "register(t5, space0)", "register(u6, space0)",
        "register(u7, space0)",
    ]  # fmt: skip


def test_spaces_follow_the_tables_a_pass_has_and_root_constants_have_their_own():
    resources = [
        res("cCameraBuffer", "SB_SRV", "global_table", slot=4),
        res("cBindlessTextures", "TX_SRV", "bindless", count=0),
        res("cModelCommon", "SB_SRV", "bindless"),
        res("cTLAS", "AS_SRV", "bindless"),
        res("cDepth", "TX_SRV"),
        res("cNodeHeightMap", "TX_SRV", "often_changed"),
        res("cbModel", "CB", "root_constant"),
        res("cbMaterialIndex", "CB", "root_constant"),
    ]
    binds = fxbind.bind(resources)
    assert binds["cBindlessTextures"] == "register(t3, space1)"
    assert binds["cModelCommon"] == "register(t6, space2)"  # the engine's material table, not the sources' order
    assert binds["cTLAS"] == "register(t0, space3)"  # the ray-tracing table after the materials'
    assert binds["cDepth"] == "register(t0, space4)" and binds["cNodeHeightMap"] == "register(t0, space5)"
    assert binds["cbMaterialIndex"] == "register(b0, space1024)" and binds["cbModel"] == "register(b1, space1024)"


def test_a_bindless_table_the_engines_lists_do_not_name_keeps_its_files_order():
    declared = [
        res("cParticleA", "SB_SRV", "bindless", file="p.fx"),
        res("cParticleB", "SB_SRV", "bindless", count=4, file="p.fx"),
        res("cParticleC", "SB_SRV", "bindless", file="p.fx"),
    ]
    binds = fxbind.bind([declared[2]], declared)  # only C is reached; A and B keep their places
    assert binds == {"cParticleC": "register(t5, space0)"}
    assert fxbind.kind_of("RWByteAddressBuffer") == "BA_UAV" and fxbind.kind_of("SamplerState") is None
    assert fxbind.kind_of("Texture2D<float4>") == "TX_SRV" and fxbind.kind_of("RWStructuredBuffer<uint>") == "SB_UAV"
