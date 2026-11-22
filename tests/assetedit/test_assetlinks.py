"""2205's asset files as one tree: a group that names a ``FileName`` is the root group of that file, which may name
more (assetedit/assetindex.py ``mounts``), and the asset tree nests the groups (assettree.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from annotoolbox.assetedit import assetindex
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.assetedit.gamefiles import LAYOUTS

BASE = "data/config/game/asset/"


def asset(guid: int, name: str, template: str = "Thing") -> str:
    return (
        f"<Asset><Template>{template}</Template><Values><Standard><GUID>{guid}</GUID><Name>{name}</Name></Standard>"
        "</Values></Asset>"
    )


FILES = {
    "assets.xml": (  # the list: two groups name their files, one holds an asset itself
        "<AssetList><ToolOneVersion>8</ToolOneVersion><Groups>"
        "<Group><Name>Objects</Name><GUIDRangeMin>1000</GUIDRangeMin><GUIDRangeMax>1999</GUIDRangeMax>"
        f"<FileName>{BASE}objects.xml</FileName></Group>"
        "<Group><Name>Quests</Name><GUIDRangeMin>3000</GUIDRangeMin><GUIDRangeMax>3999</GUIDRangeMax>"
        f"<FileName>{BASE}Quests.xml</FileName></Group>"
        "<Group><Name>Audio</Name><Assets>" + asset(9001, "Bell") + "</Assets></Group>"
        "</Groups></AssetList>"
    ),
    "objects.xml": (  # a root group without a name, a subgroup that names a file, assets of its own
        "<Group><ToolOneVersion>8</ToolOneVersion><Groups>"
        f"<Group><Name>Buildings</Name><UseParentGUIDRange>1</UseParentGUIDRange><FileName>{BASE}objects/buildings.xml</FileName></Group>"
        "<Group><Name>Props</Name><UseParentGUIDRange>1</UseParentGUIDRange><Assets>"
        + asset(1100, "Rock")
        + "</Assets></Group>"
        "</Groups></Group>"
    ),
    "objects/buildings.xml": (
        "<Group><Groups><Group><Name>Earth</Name><UseParentGUIDRange>1</UseParentGUIDRange><Assets>"
        + asset(1200, "Hut")
        + asset(1201, "Barn")
        + "</Assets></Group></Groups></Group>"
    ),
    "quests.xml": "<Group><Assets>" + asset(3100, "Find rock", "Quest") + "</Assets></Group>",  # named in other case
    "orphan.xml": "<Group><Groups><Group><Name>Loose</Name><Assets>"
    + asset(5000, "Stray")
    + "</Assets></Group></Groups></Group>",
}


@pytest.fixture
def game(tmp_path):
    root = tmp_path / "2205"
    for name, text in FILES.items():
        (root / BASE / name).parent.mkdir(parents=True, exist_ok=True)
        (root / BASE / name).write_text(text, encoding="utf-8", newline="\n")
    return open_game("anno2205", root, tmp_path / "cache")


def test_the_layout_follows_the_links_and_no_other_game_does():
    assert LAYOUTS["anno2205"].follow_links
    assert not any(lay.follow_links for key, lay in LAYOUTS.items() if key != "anno2205")


def test_linked_files_hang_below_the_group_that_names_them(game):
    groups = {e.guid: game.m_index.m_groups[e.group] for e in game.m_index.m_entries}
    assert groups[1100] == ("Objects", "Props")
    assert groups[1200] == groups[1201] == ("Objects", "Buildings", "Earth")  # two links deep
    assert groups[3100] == ("Quests",)  # the link's case differs from the file's name
    assert groups[9001] == ("Audio",)  # an asset the list holds itself
    assert groups[5000] == ("Loose",)  # a file nobody names stays a root


def test_a_group_without_a_range_uses_the_nearest_one_above_it(game):
    doc = game.document()
    assert doc.group_range(1200) == (1000, 1999) and doc.group_range(1100) == (1000, 1999)
    assert doc.group_range(3100) == (3000, 3999) and doc.group_range(9001) is None and doc.group_range(5000) is None
    made = doc.new_asset("", "Shed", copy_of=1200)
    assert 1000 <= made <= 1999 and doc.m_changes.m_new[made].anchor == 1200


def test_the_cached_scan_keeps_the_tree(game, tmp_path):
    again = open_game("anno2205", tmp_path / "2205", tmp_path / "cache")
    assert {e.guid: again.m_index.m_groups[e.group] for e in again.m_index.m_entries} == {
        e.guid: game.m_index.m_groups[e.group] for e in game.m_index.m_entries
    }
    assert assetindex.VERSION >= 3


def test_the_scan_of_files_without_links_is_as_before(tmp_path):
    data = ("<AssetList><Groups><Group><Name>A</Name><Groups><Group><Name>B</Name><Assets>" + asset(1, "x")
            + "</Assets></Group></Groups></Group></Groups></AssetList>").encode()  # fmt: skip
    groups: list = []
    entries, _refs = assetindex.scan_file(data, 0, groups, {})
    assert [groups[e.group] for e in entries] == [("A", "B")]
    found: list = []
    assetindex.scan_file(data, 0, [], {}, None, ("Top",), found)
    assert found == []


def test_the_tree_nests_the_groups_and_selecting_opens_the_way(game):
    from PySide6.QtWidgets import QApplication

    from annotoolbox.assetedit.assettree import GUID_ROLE, CAssetTree

    QApplication.instance() or QApplication([])
    tree = CAssetTree()
    tree.set_doc(game.document())
    tops = [tree.m_tree.topLevelItem(i).text(0) for i in range(tree.m_tree.topLevelItemCount())]
    assert tops == ["Audio (1)", "Loose (1)", "Objects (3)", "Quests (1)"]
    objects = tree.m_tree.topLevelItem(2)
    assert objects.childCount() == 0  # lazy
    tree.select(1200)
    assert tree.m_tree.currentItem().data(0, GUID_ROLE) == 1200 and tree.m_tree.currentItem().text(0) == "Hut"
    assert [objects.child(i).text(0) for i in range(objects.childCount())] == ["Buildings (2)", "Props (1)"]
    assert sorted(tree.selected_group()) == [1200, 1201]  # the group the row is in
    objects.setSelected(True)
    assert sorted(tree.selected_assets()) == [1100, 1200, 1201]
    tree.m_tree.setCurrentItem(objects)
    assert sorted(tree.selected_group()) == [1100, 1200, 1201]  # a group row: its subgroups too
    tree.select(1100)
    assert tree.m_tree.currentItem().text(0) == "Rock"
    assert tree.m_count.text() == "6 assets in 7 groups"


def test_the_installed_2205_is_one_tree_of_a_few_roots(tmp_path):
    from pathlib import Path

    from tests.fakes import installed

    if installed("anno2205") is None:
        pytest.skip("needs an Anno 2205 install (ANNO2205_DIR)")
    game = open_game("anno2205", Path(os.environ["ANNO2205_DIR"]), tmp_path)
    roots = {g[0] for g in game.m_index.m_groups if g}
    assert {"Texts", "Objects", "Quests", "Balancing"} <= roots and len(roots) < 40  # not one root per file
    assert any(g[:3] == ("Objects", "Buildings", "Earth") for g in game.m_index.m_groups)
    guid = next(
        e.guid for e in game.m_index.m_entries if game.m_index.m_groups[e.group][:2] == ("Objects", "Buildings")
    )
    assert game.document().group_range(guid) is not None  # inherited from the group the list gives a range
