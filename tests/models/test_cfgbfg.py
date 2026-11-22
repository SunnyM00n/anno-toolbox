"""A 117 model config's baked data file kept in step with an edited config (``models.cfgbfg``): entries follow the
child configs by position, a new part gets an entry without baked content, a far-view model that no longer matches
goes, and a ``.bfg`` that was not touched comes back unchanged. On a synthetic file of the shipped shape, and on 117's
own files when ``ANNO117_DIR`` is set."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.models import cfgbfg, cfgdoc, cfgparts
from annotoolbox.models.cfgdoc import SDoc

NL = "\r\n"


def _config(kind: str, fields: str = "") -> str:
    return f"<Config><ConfigType>{kind}</ConfigType>{NL}{fields}</Config>{NL}"


def _cfg(files: int = 3, models: int = 2) -> SDoc:
    parts = [f"<FileName>data/f{k}.cfg</FileName>{NL}" for k in range(files)]
    text = (
        f"<Config><ConfigType>MAIN</ConfigType>{NL}<Files>{''.join(_config('FILE', p) for p in parts)}</Files>{NL}"
        f"<Models>{''.join(_config('MODEL', f'<FileName>data/m{k}.fbx</FileName>{NL}') for k in range(models))}"
        f"</Models>{NL}</Config>{NL}"
    )
    return SDoc.read(text.encode(), "shared/x/x.cfg")


def _entry(number: int, marker: int | None = None, model: bool = False) -> STag:
    entry = STag("Config", [SAttrib("ConfigType", struct.pack("<I", number))])
    if model:
        entry.children += [SAttrib("EntityTransforms", b""), SAttrib("EntityColors", b""),
                           STag("RaytraceInfo", [STag("#1", [STag("Materials", [])])])]  # fmt: skip
    if marker is not None:  # a baked value only this entry has
        entry.children.append(SAttrib("Opaque", bytes([marker])))
    return entry


def _bfg(files: int = 3, models: int = 2, far: bool = True) -> bytes:
    low = STag("LowPolyData", [SAttrib("Vertices", b"\x01" * 12 if far else b""), SAttrib("Indices", b"\x00\x00"),
                              STag("TextureSets", []), STag("Materials", []), SAttrib("BoxMin", bytes(12)),
                              SAttrib("BoxMax", bytes(12)), SAttrib("Flags", struct.pack("<I", 1))])  # fmt: skip
    top = STag("Config", [SAttrib("ConfigType", struct.pack("<I", 9)), SAttrib("DamageImpacts", bytes(16)), low,
                         STag("Files", [_entry(1, 10 + k) for k in range(files)]),
                         STag("Models", [_entry(2, 20 + k, True) for k in range(models)]),
                         STag("ProbeGrid", [SAttrib("Cells", b"\x07")])])  # fmt: skip
    return filedb.write(STag("", [top]), version=3)


def _markers(data: bytes, name: str) -> list[int | None]:
    top = filedb.read(data).find("Config")
    out = []
    for entry in top.find(name).children:
        found = entry.find("Opaque")
        out.append(found.data[0] if found is not None else None)
    return out


def test_a_bfg_that_lines_up_reads_and_writes_back_the_same():
    data = _bfg()
    tags, attrs = filedb.name_ids(data)
    assert filedb.write(filedb.read(data), tags, attrs, filedb.version(data)) == data
    assert cfgbfg.mismatches(_cfg(), data) == []


def test_what_does_not_line_up_is_said_where():
    found = cfgbfg.mismatches(_cfg(files=4), _bfg(files=3))
    assert found == ["/Files: 3 entries for 4 configs"]
    wrong = filedb.read(_bfg())
    wrong.find("Config").find("Models").children[1].children[0] = SAttrib("ConfigType", struct.pack("<I", 1))
    assert "ConfigType 1, the config is MODEL (2)" in cfgbfg.mismatches(_cfg(), filedb.write(wrong))[0]
    assert "does not read" in cfgbfg.mismatches(_cfg(), b"nonsense")[0]


def test_an_edit_that_keeps_every_list_needs_no_bfg():
    base, edited = _cfg(), _cfg()
    leaf = next(f for f in cfgdoc.fields(edited.find("Models/0").node) if f.name == "FileName")
    cfgdoc.set_value(leaf.node, "data/other.fbx").do()
    found = cfgbfg.reindex(base, edited, _bfg())
    assert not found.changed and not found.problems and found.data == _bfg()


def test_a_removed_part_takes_its_entry_and_the_far_view_goes():
    base, edited = _cfg(), _cfg()
    files = edited.find("Files")
    cfgdoc.remove(files.node, edited.find("Files/1").node).do()
    found = cfgbfg.reindex(base, edited, _bfg())
    assert found.changed and not found.problems
    assert _markers(found.data, "Files") == [10, 12] and _markers(found.data, "Models") == [
        20,
        21,
    ]  # the baked ones stay
    top = filedb.read(found.data).find("Config")
    low = top.find("LowPolyData")
    assert low.find("Vertices").data == b"" and low.find("Flags").data == struct.pack("<I", 8)
    assert (
        low.find("BoxMin").data.hex() == "ffff7f7fffff7f7fffff7f7f"
        and low.find("BoxMax").data.hex() == "ffff7fffffff7fffffff7fff"
    )
    assert top.find("DamageImpacts") is not None and top.find("ProbeGrid") is not None  # kept as they are
    assert cfgbfg.mismatches(edited, found.data) == []  # and it lines up with the edited config


def test_a_new_part_gets_an_entry_without_baked_content_and_a_new_list_is_made_in_its_place():
    base, edited = _cfg(), _cfg()
    models = edited.find("Models")
    cfgdoc.duplicate(models.node, edited.find("Models/1").node).do()
    lights, made = cfgparts.list_node(edited, edited.parts().node, "Lights")
    cfgdoc.insert(lights, 0, [cfgparts.new_part(edited, "LIGHT")]).do()
    made.do()
    found = cfgbfg.reindex(base, edited, _bfg())
    assert not found.problems and found.changed
    assert _markers(found.data, "Models") == [20, 21, None]
    top = filedb.read(found.data).find("Config")
    new_model = top.find("Models").children[2]
    assert [c.name for c in new_model.children] == ["ConfigType", "EntityTransforms", "EntityColors"]
    names = [c.name for c in top.children]
    assert names.index("Models") < names.index("Lights") < names.index("ProbeGrid")  # the shipped order
    assert top.find("Lights").children[0].find("ConfigType").data == struct.pack("<I", 6)
    assert cfgbfg.mismatches(edited, found.data) == []


def test_a_part_moved_keeps_the_entries_of_the_ones_that_stayed_and_the_list_changes():
    base, edited = _cfg(), _cfg()
    cfgdoc.move(edited.find("Files").node, edited.find("Files/2").node, -2).do()
    found = cfgbfg.reindex(base, edited, _bfg())
    assert found.changed and not found.problems
    assert len(_markers(found.data, "Files")) == 3 and cfgbfg.mismatches(edited, found.data) == []


def test_a_far_view_that_has_no_geometry_is_left_alone():
    base, edited = _cfg(), _cfg()
    cfgdoc.remove(edited.find("Files").node, edited.find("Files/0").node).do()
    data = _bfg(far=False)
    low = filedb.read(cfgbfg.reindex(base, edited, data).data).find("Config").find("LowPolyData")
    assert low.find("Flags").data == struct.pack("<I", 1)  # not replaced: it said nothing about geometry


def test_a_bfg_of_another_length_than_its_config_cannot_follow_an_edit():
    found = cfgbfg.reindex(_cfg(files=4), _cfg(files=3), _bfg(files=3))
    assert found.problems and "does not line up" in found.problems[0] and found.data == b""
    assert cfgbfg.reindex(_cfg(), _cfg(), b"nonsense").problems


@pytest.mark.skipif(not os.environ.get("ANNO117_DIR"), reason="needs an Anno 117 install (ANNO117_DIR)")
def test_the_shipped_bfg_line_up_with_their_configs_and_follow_a_removal(tmp_path):
    from annotoolbox.sources import gametree

    game = gametree.open_game("anno117", Path(os.environ["ANNO117_DIR"]), tmp_path)
    pairs = [(p[:-4], p) for p in game.m_paths if p.startswith("shared/") and p.endswith(".bfg")][:400]
    checked = removed = 0
    for stem, bfg in pairs:
        if not game.exists(stem + ".cfg"):
            continue
        base = SDoc.read(game.read(stem + ".cfg"), stem + ".cfg")
        data = game.read(bfg)
        problems = cfgbfg.mismatches(base, data)
        if problems:
            continue  # the two vanilla files whose model lists differ
        checked += 1
        assert (
            cfgbfg.reindex(base, SDoc.read(game.read(stem + ".cfg"), stem), data).data == data
        )  # untouched: the same bytes
        edited = SDoc.read(game.read(stem + ".cfg"), stem + ".cfg")
        lists = cfgbfg.child_lists(edited.parts().node)
        if lists:
            name = next(iter(lists))
            cfgdoc.remove(
                next(c for c in edited.parts().node.children if cfgdoc.strip(c.name) == name), lists[name][0]
            ).do()
            found = cfgbfg.reindex(base, edited, data)
            assert found.changed and cfgbfg.mismatches(edited, found.data) == [], stem
            removed += 1
    assert checked > 300 and removed > 200
