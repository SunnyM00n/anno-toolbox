"""``refgraph``: who names a file and which files nothing names, on synthetic trees; the browser's "Not referenced" box
offscreen; and, where an install is named by ``ANNO*_DIR``, that files known to be used are found referenced."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import csv
import io
from pathlib import Path

import pytest

from annotoolbox.exporting import banks
from annotoolbox.refgraph import graph, rules, scan
from annotoolbox.sources import gametree

CFG = "data/graphics/house/house.cfg"
FILES = {
    CFG: "<Config><FileName>data\\graphics\\house\\rdm\\house_lod0.rdm</FileName>"
    "<cModelDiffTex>data/graphics/house/maps/house_diff.psd</cModelDiffTex></Config>",
    "data/graphics/house/rdm/house_lod0.rdm": "",
    "data/graphics/house/rdm/house_lod1.rdm": "",
    "data/graphics/house/maps/house_diff_0.dds": "",
    "data/graphics/house/maps/house_diff_2.dds": "",
    "data/graphics/house/house.ifo": "",
    "data/graphics/old/barn.cfg": "<Config><FileName>data/graphics/old/rdm/barn_lod0.rdm</FileName></Config>",
    "data/graphics/old/rdm/barn_lod0.rdm": "",
    "data/graphics/old/maps/leftover_diff_0.dds": "",
    "data/config/export/assets.xml": "<Assets><Asset><Values><Standard><GUID>7</GUID><Name>House</Name></Standard>"
    f"<Building><Model>{CFG}</Model></Building></Values></Asset></Assets>",
    "data/config/settings.xml": "<Settings/>",
    "data/base/islands/pool/small_01/small_01.a7m": "",
    "data/base/islands/pool/small_01/_gamedata/small_01/heights/0.dds": "",
}


def make_tree(tmp_path, game: str = "anno117"):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    tree = gametree.loose_tree(tmp_path)
    tree.game = game
    return tree


def test_a_name_in_a_file_is_an_edge_and_an_asset_gives_its_guid(tmp_path):
    tree = make_tree(tmp_path)
    found = graph.scan_tree(tree)
    assert found.referenced_by("data/graphics/house/rdm/house_lod0.rdm") == [CFG]
    assert found.referenced_by(CFG) == ["data/config/export/assets.xml#7"]
    assert "data/graphics/house/rdm/house_lod0.rdm" in found.references(CFG)
    assert scan.split_source("data/config/export/assets.xml#7") == ("data/config/export/assets.xml", "7")


def test_each_file_has_one_verdict_and_the_unreferenced_ones_are_the_loose_ends(tmp_path):
    tree = make_tree(tmp_path)
    verdicts = graph.build(tree).verdicts(tree.paths)
    assert verdicts[CFG] == rules.REFERENCED
    assert verdicts["data/graphics/house/rdm/house_lod1.rdm"] == rules.IMPLIED  # a level of a named mesh
    assert verdicts["data/graphics/house/maps/house_diff_2.dds"] == rules.IMPLIED  # a quality level of a named map
    assert verdicts["data/graphics/house/house.ifo"] == rules.IMPLIED  # the config's own info file
    assert verdicts["data/config/settings.xml"] == rules.ENTRY  # found by its place
    assert verdicts["data/base/islands/pool/small_01/_gamedata/small_01/heights/0.dds"] == rules.ENTRY  # a pool
    assert verdicts["data/graphics/old/barn.cfg"] == rules.UNREFERENCED
    assert verdicts["data/graphics/old/maps/leftover_diff_0.dds"] == rules.UNREFERENCED
    assert graph.unreferenced(graph.build(tree), tree.paths) == [
        "data/graphics/old/barn.cfg",
        "data/graphics/old/maps/leftover_diff_0.dds",
        "data/graphics/old/rdm/barn_lod0.rdm",
    ]
    assert verdicts["data/graphics/old/rdm/barn_lod0.rdm"] == rules.ORPHANED  # named by a config nothing names


def test_a_pages_folder_belongs_to_the_island_that_is_named(tmp_path):
    named = {"data/s/isl/isl.a7m"}
    paths = [*named, "data/s/isl/_gamedata/isl/h/0.dds", "data/s/other/_gamedata/other/h/0.dds"]
    verdicts = rules.classify(paths, named)
    assert verdicts["data/s/isl/_gamedata/isl/h/0.dds"] == rules.IMPLIED
    assert verdicts["data/s/other/_gamedata/other/h/0.dds"] == rules.UNREFERENCED


def test_a_shared_copy_stands_for_its_data_file(tmp_path):
    named = {"data/base/a/x.cfg"}
    verdicts = rules.classify(["data/base/a/x.cfg", "shared/base/a/x.cfg", "shared/base/a/y.cfg"], named)
    assert verdicts["shared/base/a/x.cfg"] == rules.IMPLIED
    assert verdicts["shared/base/a/y.cfg"] == rules.UNREFERENCED


def test_the_graph_is_kept_by_the_stamp_and_read_again_when_the_files_change(tmp_path):
    tree = make_tree(tmp_path / "game")
    cache = tmp_path / "kept" / "references.json"
    first = graph.build(tree, cache)
    assert cache.exists() and graph.cached(tree, cache).forward == first.forward
    tree.stamp = "another"
    assert graph.cached(tree, cache) is None
    assert graph.cache_file(tmp_path, "anno117").name == "anno117-references.json"


def test_the_list_is_csv_with_path_suffix_archive_and_class(tmp_path):
    tree = make_tree(tmp_path)
    rows = list(csv.reader(io.StringIO(graph.unreferenced_csv(tree, graph.build(tree)))))
    assert rows[0] == ["path", "suffix", "archive", "class"]
    assert [r[0] for r in rows[1:]] == [
        "data/graphics/old/barn.cfg",
        "data/graphics/old/maps/leftover_diff_0.dds",
        "data/graphics/old/rdm/barn_lod0.rdm",
    ]
    assert rows[1][1] == "cfg" and [r[3] for r in rows[1:]] == ["unreferenced", "unreferenced", "orphaned"]


def test_a_sound_bank_names_its_media_by_id_and_its_info_file(monkeypatch):
    listing = banks.BankListing(1, 2, sounds=[banks.BankSound(1, "x", "streamed", 77)])
    monkeypatch.setattr(banks, "bank_listing", lambda data: listing)
    files = {"data/sound/windows/music.bnk", "data/sound/windows/music.json", "data/sound/windows/media/77.wem"}
    found = scan.bank_refs("data/sound/windows/music.bnk", b"", files.__contains__, lambda p: b"{}")
    assert found == ["data/sound/windows/media/77.wem", "data/sound/windows/music.json"]


def test_a_name_with_spaces_in_its_folders_is_found_whole_and_prose_before_it_is_not_taken_for_a_folder(tmp_path):
    files = {
        "data/g/third party/pirates/rdm/gun_lod0.rdm": "",
        "data/g/third party/pirates/gun.cfg": "x",
        "data/g/builder house/maps/h_0.dds": "",
        "data/g/a.cfg": "see data\\g\\third party\\pirates\\rdm\\gun_lod0.rdm and data/g/builder house/maps/h.psd here",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    tree = gametree.loose_tree(tmp_path)
    tree.game = "anno117"
    found = graph.scan_tree(tree)
    assert found.references("data/g/a.cfg") == [
        "data/g/builder house/maps/h_0.dds",
        "data/g/third party/pirates/rdm/gun_lod0.rdm",
    ]


def make_loose(tmp_path, files: dict[str, str | bytes], game: str = "anno117"):
    for rel, content in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    tree = gametree.loose_tree(tmp_path)
    tree.game = game
    return tree


def test_names_written_in_utf16_umlauts_or_as_a_bare_file_name_are_found_and_a_thumbs_db_names_nothing(tmp_path):
    tree = make_loose(
        tmp_path,
        {
            "data/pic/logo_0.dds": "",
            "data/pic/größe_0.dds": "",
            "data/ui/datalog/010c.jpg": "",
            "data/ui/datalog/thumbs.db": "data/ui/datalog/010c.jpg".encode("utf-16-le"),
            "data/loca/credits.txt": b"\xff\xfe" + "IMAGE data/pic/logo.png".encode("utf-16-le"),
            "data/loca/quests.xml": "<Text>010c.jpg</Text><Text>data/pic/größe.png</Text>",
            "data/other/readme.txt": "010c.jpg",
        },
    )
    found = graph.scan_tree(tree)
    assert found.referenced_by("data/pic/logo_0.dds") == ["data/loca/credits.txt"]
    assert found.referenced_by("data/pic/größe_0.dds") == ["data/loca/quests.xml"]
    assert found.referenced_by("data/ui/datalog/010c.jpg") == ["data/loca/quests.xml", "data/other/readme.txt"]


def test_a_script_that_joins_a_literal_folder_head_and_a_literal_tail_names_every_file_that_fits(tmp_path):
    script = (
        'local f = "data/gui/frames/ships/group_" .. tostring(id) .. ".bkg"\n'
        'bg = bodyBg .. "_rounded.bkg"\n'  # no literal folder in its head: out of reach
        'local g = "x/y" .. n .. ".bkg"\n'  # one folder only: too short to be a name
    )
    tree = make_loose(
        tmp_path,
        {
            "data/script/gui.lua": script,
            "data/gui/frames/ships/group_1.bkg": "",
            "data/gui/frames/ships/group_2.bkg": "",
            "data/gui/frames/ships/group_2.bkg.png": "",
            "data/gui/frames/ships/other_1.bkg": "",
            "data/gui/frames/common_rounded.bkg": "",
        },
    )
    found = graph.scan_tree(tree)
    assert found.references("data/script/gui.lua") == [
        "data/gui/frames/ships/group_1.bkg",
        "data/gui/frames/ships/group_2.bkg",
    ]


def test_the_partners_of_a_named_file_are_implied_whatever_the_games_call_them():
    named = {"d/pic/x_0.dds", "d/units/cart.cfg", "d/sound/9.bnk", "d/m/barn.gr2", "d/m/t_512.tga"}
    paths = [
        *named,
        "d/pic/x.abm",
        "d/units/cart.snd",
        "d/sound/9.xml",
        "d/sound/9.txt",
        "d/m/barn_1.dds",  # a Granny mesh's own textures
        "d/m/t_512.txt",
        "d/sound/8.xml",
        "d/pic/y.abm",
    ]
    verdicts = rules.classify(paths, named)
    assert [verdicts[p] for p in paths[5:11]] == [rules.IMPLIED] * 6
    assert verdicts["d/sound/8.xml"] == verdicts["d/pic/y.abm"] == rules.UNREFERENCED


def test_an_island_named_by_its_name_alone_is_found_and_names_the_asset_that_says_it(tmp_path):
    files = {
        "data/p/islands/campaign/Calidus_01/calidus_01.a7m": "",
        "data/p/islands/campaign/Calidus_01/_gamedata/calidus_01/h/0.dds": "",
        "data/p/islands/campaign/other_01/other_01.a7m": "",
        "data/config/assets.xml": "<Assets><Asset><Values><Standard><GUID>9</GUID><Name>x</Name></Standard>"
        "<Island><NameOfIsland>CALIDUS_01</NameOfIsland></Island></Values></Asset></Assets>",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    tree = gametree.loose_tree(tmp_path)
    tree.game = "anno117"
    found = graph.scan_tree(tree)
    assert found.referenced_by("data/p/islands/campaign/calidus_01/calidus_01.a7m") == ["data/config/assets.xml#9"]
    verdicts = found.verdicts(tree.paths)
    assert verdicts["data/p/islands/campaign/calidus_01/_gamedata/calidus_01/h/0.dds"] == rules.IMPLIED
    assert verdicts["data/p/islands/campaign/other_01/other_01.a7m"] == rules.UNREFERENCED


def test_the_other_levels_of_a_shared_mesh_belong_to_it_whether_the_data_or_the_shared_file_is_named():
    named = {"shared/b/rdm/x_lod0.rdm"}
    verdicts = rules.classify(["shared/b/rdm/x_lod0.rdm", "shared/b/rdm/x_lod2.rdm", "data/b/rdm/x_lod1.rdm"], named)
    assert verdicts["shared/b/rdm/x_lod2.rdm"] == verdicts["data/b/rdm/x_lod1.rdm"] == rules.IMPLIED


def test_the_ai_scripts_are_found_by_their_folder_and_a_bank_that_cannot_be_read_stops_nothing(tmp_path):
    assert rules.classify(["data/ai/economy/carrier.csl", "data/other/x.csl"], set()) == {
        "data/ai/economy/carrier.csl": rules.ENTRY,
        "data/other/x.csl": rules.UNREFERENCED,
    }
    tree = make_tree(tmp_path)
    (tmp_path / "data/sound/windows").mkdir(parents=True)
    (tmp_path / "data/sound/windows/bad.bnk").write_bytes(b"not a bank")
    tree = gametree.loose_tree(tmp_path)
    tree.game = "anno117"
    real = tree.read
    tree.read = lambda p: (_ for _ in ()).throw(OSError(p)) if p.endswith(".bnk") else real(p)
    assert graph.scan_tree(tree).referenced_by("data/graphics/house/rdm/house_lod0.rdm") == [CFG]


def test_what_a_file_names_includes_what_the_assets_inside_it_name(tmp_path):
    found = graph.scan_tree(make_tree(tmp_path))
    assert found.references("data/config/export/assets.xml") == [CFG]
    assert found.references("data/graphics/old/barn.cfg") == ["data/graphics/old/rdm/barn_lod0.rdm"]
    assert found.references("data/nothing.cfg") == []


def test_an_asset_names_a_sound_event_or_a_bank_by_its_wwise_id_and_the_event_leads_into_its_bank(
    tmp_path, monkeypatch
):
    sound = banks.BankSound(1, "x", "streamed", 77)
    listing = banks.BankListing(1, 2, sounds=[sound], events=[5])
    monkeypatch.setattr(banks, "bank_listing", lambda data: listing)
    monkeypatch.setattr(banks, "event_sounds", lambda lst, event: ([sound], True))
    monkeypatch.setattr(banks, "read_info", lambda bank, exists, read: banks.BankInfo())
    asset = "<Assets><Asset><Values><Standard><GUID>%d</GUID></Standard><WwiseStandard><WwiseID>%d</WwiseID>"
    tree = make_loose(
        tmp_path,
        {
            "data/config/assets.xml": (asset % (10, 5))
            + "</WwiseStandard></Values></Asset>"
            + (asset % (11, 900))
            + "</WwiseStandard></Values></Asset><Asset><Values><Standard><GUID>12</GUID></Standard>"
            "<WwiseStandard><WwiseID>31</WwiseID></WwiseStandard></Values></Asset></Assets>",
            "data/sound/windows/700.bnk": b"bank",
            "data/sound/windows/900.bnk": b"bank",
            "data/sound/windows/media/77.wem": b"",
        },
    )
    found = graph.scan_tree(tree)
    assert "data/config/assets.xml#10" in found.referenced_by("data/sound/windows/media/77.wem")
    assert found.references("data/config/assets.xml") == [
        "data/sound/windows/700.bnk",
        "data/sound/windows/900.bnk",
        "data/sound/windows/media/77.wem",
    ]
    assert found.referenced_by("data/sound/windows/900.bnk")[-1] == "data/config/assets.xml#11"
    assert not any(s.endswith("#12") for s in found.forward)  # an id no bank has names nothing


# the whole chain


def test_a_file_named_only_by_unused_files_is_orphaned_and_a_chain_from_an_entry_is_live(tmp_path):
    tree = make_loose(
        tmp_path,
        {
            "data/config/start.xml": "<c>data/a/one.cfg</c>",  # an entry names one.cfg, which names two.cfg, ...
            "data/a/one.cfg": "<c>data/a/two.cfg</c>",
            "data/a/two.cfg": "<c>data/a/rdm/two_lod0.rdm</c>",
            "data/a/rdm/two_lod0.rdm": "",
            "data/a/rdm/two_lod1.rdm": "",
            "data/a/two.ifo": "",
            "data/a/dead.cfg": "<c>data/a/dead2.cfg</c>",  # nothing names dead.cfg
            "data/a/dead2.cfg": "<c>data/a/rdm/dead_lod0.rdm</c>",
            "data/a/dead2.ifo": "",
            "data/a/rdm/dead_lod0.rdm": "",
            "data/a/rdm/dead_lod1.rdm": "",
            "data/a/cycle1.cfg": "<c>data/a/cycle2.cfg</c>",  # two files naming each other, no entry among them
            "data/a/cycle2.cfg": "<c>data/a/cycle1.cfg</c>",
        },
    )
    found = graph.scan_tree(tree)
    verdicts = found.verdicts(tree.paths)
    live = ["one.cfg", "two.cfg", "rdm/two_lod0.rdm"]
    assert {p: verdicts["data/a/" + p] for p in live} == dict.fromkeys(live, rules.REFERENCED)
    assert verdicts["data/a/rdm/two_lod1.rdm"] == verdicts["data/a/two.ifo"] == rules.IMPLIED
    assert verdicts["data/a/dead.cfg"] == rules.UNREFERENCED
    orphans = ["dead2.cfg", "dead2.ifo", "rdm/dead_lod0.rdm", "rdm/dead_lod1.rdm", "cycle1.cfg", "cycle2.cfg"]
    assert {p: verdicts["data/a/" + p] for p in orphans} == dict.fromkeys(orphans, rules.ORPHANED)
    assert found.named >= {"data/a/dead2.cfg"}  # the graph still says dead.cfg names it
    assert graph.unused(verdicts) == sorted(["data/a/dead.cfg", *("data/a/" + p for p in orphans)])
    assert set(rules.UNUSED) <= set(rules.CLASSES)


def test_the_files_the_engine_opens_by_name_are_roots_and_make_what_they_list_live(tmp_path):
    roots = ["guidata/__templates.cdf", "data/sound/sounddatabase.xml", "data/graphics/objects/ocflist.ini",
             "data/sound/generatedsoundbanks/windows/soundbanksinfo.json", "data/levels/multiplayer/multiplayer_01.www"]  # fmt: skip
    assert all(rules.is_entry(p) for p in roots)
    assert not rules.is_entry("data/levels/testlevel/jan/a.www") and not rules.is_entry("data/sound/sfx/a.wav")
    tree = make_loose(tmp_path, {"data/sound/sounddatabase.xml": "<x>data/sound/sfx/a.wav</x>", "data/sound/sfx/a.wav": "",
                                 "data/sound/sfx/b.wav": ""}, "anno2070")  # fmt: skip
    verdicts = graph.scan_tree(tree).verdicts(tree.paths)
    assert (
        verdicts["data/sound/sfx/a.wav"] == rules.REFERENCED and verdicts["data/sound/sfx/b.wav"] == rules.UNREFERENCED
    )


def test_what_an_asset_names_is_live_when_its_asset_file_is_an_entry_and_the_files_it_names_go_on(tmp_path):
    tree = make_tree(tmp_path)
    verdicts = graph.scan_tree(tree).verdicts(tree.paths)
    assert verdicts[CFG] == rules.REFERENCED and verdicts["data/graphics/house/rdm/house_lod0.rdm"] == rules.REFERENCED
    assert (
        verdicts["data/graphics/house/maps/house_diff_0.dds"] == rules.REFERENCED
    )  # through the config the asset names


# who uses a file


ASSETS = (
    "<Assets><Asset><Values><Standard><GUID>7</GUID><Name>House</Name></Standard><Building><Model>data/a/house.cfg"
    "</Model><Alt>data/a/rdm/house.rdm</Alt></Building></Values></Asset><Asset><Values><Standard><GUID>8</GUID>"
    "<Name>Hut</Name></Standard><Building><Filename>data/a/house.cfg</Filename></Building></Values></Asset></Assets>"
)


def test_who_uses_a_file_lists_the_files_and_the_assets_with_their_field_and_name(tmp_path):
    tree = make_loose(
        tmp_path,
        {
            "data/a/house.cfg": "<Config><FileName>data/a/rdm/house.rdm</FileName></Config>",
            "data/a/rdm/house.rdm": "",
            "data/config/assets.xml": ASSETS,
        },
    )
    found = graph.scan_tree(tree)
    files, assets = found.users("data/a/rdm/house.rdm")
    assert files == ["data/a/house.cfg"]
    assert [(u.guid, u.name, u.field, u.file) for u in assets] == [(7, "House", "Alt", "data/config/assets.xml")]
    assert [(u.guid, u.name, u.field) for u in found.users("data/a/house.cfg")[1]] == [
        (7, "House", "Model"),
        (8, "Hut", "Filename"),
    ]
    assert found.users("data/nothing.cfg") == ([], [])
    kept = graph.RefGraph.from_json(found.to_json())  # the names and fields are kept with the graph
    assert kept.users("data/a/house.cfg") == found.users("data/a/house.cfg")


def test_a_source_texture_name_finds_its_0_dds_in_a_newer_game_and_a_file_that_ships_as_written_wins(tmp_path):
    files = {
        "data/a/house.cfg": "<Config><cModelDiffTex>data/a/wall.psd</cModelDiffTex>"
        "<cModelNormalTex>data/a/flat.tga</cModelNormalTex></Config>",
        "data/a/wall_0.dds": "",
        "data/a/flat.tga": "",
        "data/a/flat_0.dds": "",
    }
    for game in ("anno117", "anno2070"):
        found = graph.scan_tree(make_loose(tmp_path / game, files, game))
        assert found.users("data/a/wall_0.dds")[0] == ["data/a/house.cfg"]
        assert found.users("data/a/flat.tga")[0] == ["data/a/house.cfg"] and not found.users("data/a/flat_0.dds")[0]


def test_1701s_mcf_configs_name_their_meshes(tmp_path):
    files = {"data/a/house.mcf": "<m_Config><m_FileName>data/a/rdm/house.rdm</m_FileName></m_Config>"}
    found = graph.scan_tree(make_loose(tmp_path, {**files, "data/a/rdm/house.rdm": ""}, "anno1701"))
    assert found.users("data/a/rdm/house.rdm")[0] == ["data/a/house.mcf"]


# 2205's Flash movies


def test_a_2205_movie_names_its_pictures_with_the_exporters_folders_dropped_and_only_in_2205(tmp_path):
    names = (
        b"UEF\x0f\x00ExtractedFireFiles\\pngs\\UIAssets\\Button_Up.png\x00\x00.\\PNGs\\icons\\icon_zoom.png\x00"
        b".\\blobs\\others2.bfd\x00Roboto_Light_Default.ffd\x00other.feu\x00"
    )
    files = {
        "data/ui/screen.feu": names,
        "data/ui/extractedfirefiles/uiassets/button_up.png": b"",
        "data/ui/extractedfirefiles/icons/icon_zoom.png": b"",
        "data/ui/blobs/others2.bfd": b"",
        "data/ui/roboto_light_default.ffd": b"",
        "data/ui/other.feu": b"",
    }
    found = graph.scan_tree(make_loose(tmp_path / "2205", files, "anno2205"))
    assert found.references("data/ui/screen.feu") == sorted(p for p in files if p != "data/ui/screen.feu")
    other = graph.scan_tree(make_loose(tmp_path / "117", files, "anno117")).references("data/ui/screen.feu")
    assert "data/ui/other.feu" in other and not any(p.endswith(".png") for p in other)  # the rule is 2205's
    assert scan.fire_names(".\\PNGs\\icons\\x.png") == ["icons/x.png", "extractedfirefiles/icons/x.png"]
    assert scan.fire_names("ExtractedFireFiles\\pngs\\UIAssets\\x.png") == ["ExtractedFireFiles/UIAssets/x.png"]


# the browser


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_the_browsers_box_filters_to_the_unreferenced_files_with_the_kind_and_the_search(qt, tmp_path):
    from annotoolbox.viewer.browser import FileBrowser

    tree = make_tree(tmp_path)
    browser = FileBrowser()
    browser.set_tree(tree)
    assert browser.unref.isHidden()
    browser.set_unreferenced(graph.unreferenced(graph.build(tree), tree.paths))
    assert not browser.unref.isHidden()
    browser.unref.setChecked(True)
    assert browser.found.count() == 3 and browser.pool() == browser.unreferenced
    browser.search.setText("leftover")
    browser._run_search()
    assert [browser.found.item(i).text() for i in range(browser.found.count())] == [
        "data/graphics/old/maps/leftover_diff_0.dds"
    ]
    browser.set_tree(tree)  # another game opens: the box is gone
    assert browser.unref.isHidden() and not browser.unref.isChecked()


def test_the_viewer_finds_the_unreferenced_files_in_the_background_and_a_later_open_takes_the_kept_graph(qt, tmp_path):
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    tree = make_tree(tmp_path / "game")
    window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(tree)
    assert window.browser.unref.isHidden()
    window.references.find()
    window.background.join()
    qt.processEvents()
    assert not window.browser.unref.isHidden() and len(window.browser.unreferenced) == 3
    window.open_path(CFG)
    top = [window.references.panel.topLevelItem(i).text(0) for i in range(2)]
    assert top == ["Named by (1)", "Names (2)"]
    assert window.users.topLevelItem(1).text(0) == "Assets (1)"  # the same graph fills the Used by panel
    assert window.users.topLevelItem(1).child(0).text(1) == "Model"
    window.set_tree(tree)  # the same install again: the kept graph is taken without a click
    window.background.join()
    qt.processEvents()
    assert len(window.browser.unreferenced) == 3
    window.close()


def test_a_second_find_while_the_first_read_goes_on_reads_nothing_again(qt, tmp_path):
    from annotoolbox.suite.settings import Settings
    from annotoolbox.viewer.viewerwindow import ViewerWindow

    tree = make_tree(tmp_path / "game")
    window = ViewerWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(tree)
    started = []
    window.background.run = lambda work, done: started.append(done)
    window.references.find()
    window.references.find()
    assert len(started) == 1
    started[0](None)  # the read ended without a graph: a new click starts again
    window.references.find()
    assert len(started) == 2
    window.close()


# the installed games: files known to be used are found referenced

INSTALLED = {
    "anno117": (
        "ANNO117_DIR",
        "data/base/provinces/celtic/islands/pool/celtic_island_small_07/celtic_island_small_07.a7m",
        "assets.xml",
    ),
    "anno1800": (
        "ANNO1800_DIR",
        "data/sessions/maps/pool/colony01/colony01_m_01/colony01_m_01.a7t",
        "assets.xml",
    ),
    "anno2205": ("ANNO2205_DIR", "data/sessions/session_earth_02/session_earth_02.a6m", "sessions.xml"),
    "anno2070": (
        "ANNO2070_DIR",
        "data/graphics/buildings/techs/production/fuel_factory_techs/rdm/fuel_factory_techs_lod0.rdm",
        "techs_fuel_factory.cfg",
    ),
    "anno1404": (
        "ANNO1404_DIR",
        "data/graphics/anims/population/c_fat/stand/n_abbot_c_stand01.gr2",
        "n_abbot_c.cfg",
    ),
    "anno1701": (
        "ANNO1701_DIR",
        "data/graphics/objects/addon01/buildings/cultures/asians/asian_barracks/gr2/asian_barracks_lod0.gr2",
        "asian_barracks.mcf",
    ),
}


@pytest.mark.parametrize("key", list(INSTALLED))
def test_installed_a_known_used_file_is_referenced_and_the_counts_are_sane(key, tmp_path_factory):
    var, known, by = INSTALLED[key]
    folder = os.environ.get(var)
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"{var} is not set")
    tree = gametree.open_game(key, Path(folder), tmp_path_factory.mktemp("index"))
    found = graph.build(tree, tmp_path_factory.mktemp("kept") / "refs.json")
    assert any(by in s for s in found.referenced_by(known))
    verdicts = found.verdicts(tree.paths)
    counts = {c: sum(1 for v in verdicts.values() if v == c) for c in rules.CLASSES}
    assert verdicts[known] == rules.REFERENCED
    assert counts[rules.REFERENCED] > 1000 and 0 < counts[rules.UNREFERENCED] < len(tree.paths) // 2
    assert counts[rules.UNREFERENCED] + counts[rules.ORPHANED] < len(tree.paths) // 4  # the roots are in (1701's lists)
    files, assets = found.users(known)
    assert any(by in f for f in files) or any(by in a.file for a in assets)
    for root in INSTALLED_ROOTS.get(key, ()):
        assert verdicts[root] not in rules.UNUSED
    for picture in MOVIE_PICTURES if key == "anno2205" else ():
        assert any(user.endswith(".feu") for user in found.referenced_by(picture))


INSTALLED_ROOTS = {
    "anno2070": ("guidata/__templates.cdf", "data/sound/sounddatabase.xml"),
    "anno1404": ("data/sound/sounddatabase.xml",),
    "anno1701": ("data/graphics/objects/ocflist.ini", "data/graphics/objects/guid_ocflist.ini"),
    "anno2205": ("data/sound/generatedsoundbanks/windows/soundbanksinfo.xml",),
}
MOVIE_PICTURES = (  # the three forms 2205's movies write a picture in (references.md)
    "data/ui/extractedfirefiles/uiassets/dark_overlay.png",  # ``ExtractedFireFiles\UIAssets\x.png``
    "data/ui/extractedfirefiles/uiassets/start_background.png",  # ``ExtractedFireFiles\pngs\UIAssets\x.png``
    "data/ui/extractedfirefiles/icons/icon_zoom.png",  # an atlas entry ``.\PNGs\icons\x.png``
)
