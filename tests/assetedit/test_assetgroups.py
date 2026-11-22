"""Cut, copy and paste of assets between groups, and the GUID range a group gives (2205's GUIDRangeMin / Max)."""

from __future__ import annotations

import pytest
from tests.sources.sourcesfakes import asset_game

from annotoolbox.assetedit import assetindex, checks
from annotoolbox.assetedit.assetgame import open_game

EXPORT = "data/base/config/export/assets.xml"


@pytest.fixture
def doc(tmp_path):
    root = asset_game(tmp_path / "117")
    path = root / EXPORT
    text = path.read_text(encoding="utf-8")
    ranged = text.replace(
        "<Group><Name>Goods</Name>",
        "<Group><Name>Goods</Name><GUIDRangeMin>5000</GUIDRangeMin><GUIDRangeMax>5002</GUIDRangeMax>",
    )
    assert ranged != text
    path.write_text(ranged, encoding="utf-8")
    return open_game("anno117", root, tmp_path / "cache").document()


def test_the_scan_reads_the_groups_guid_ranges(doc):
    assert doc.m_index.m_group_ranges == {("Goods",): (5000, 5002)}
    assert doc.group_range(1001) == (5000, 5002) and doc.group_range(2001) is None and doc.group_range(None) is None


def test_the_ranges_survive_the_cached_scan(tmp_path):
    root = asset_game(tmp_path / "117")
    text = (root / EXPORT).read_text(encoding="utf-8")
    (root / EXPORT).write_text(
        text.replace(
            "<Group><Name>Goods</Name>",
            "<Group><Name>Goods</Name><GUIDRangeMin>7</GUIDRangeMin><GUIDRangeMax>9</GUIDRangeMax>",
        ),
        encoding="utf-8",
    )
    game = open_game("anno117", root, tmp_path / "cache")
    again = open_game("anno117", root, tmp_path / "cache")
    assert again.m_index.m_group_ranges == game.m_index.m_group_ranges == {("Goods",): (7, 9)}
    assert assetindex.VERSION >= 2


def test_a_new_asset_beside_one_of_a_group_takes_the_groups_range_and_a_full_group_is_said(doc):
    first, second, third = (doc.new_asset("", "Copy", copy_of=1001) for _ in range(3))
    assert (first, second, third) == (5000, 5001, 5002)
    with pytest.raises(ValueError, match="full"):
        doc.new_asset("", "One too many", copy_of=1001)
    assert not [f for f in checks.check(doc) if "outside" in f.message]  # inside the group's range is fine
    outside = doc.new_asset("", "Elsewhere", copy_of=2001)
    assert doc.m_changes.m_guid_range[0] <= outside <= doc.m_changes.m_guid_range[1]


def test_paste_copies_beside_the_target_with_the_targets_range(doc):
    made = doc.paste([2001, 2002], anchor=1002)
    assert made == [5000, 5001] and all(doc.m_changes.m_new[g].anchor == 1002 for g in made)
    assert [doc.name(g) for g in made] == ["Hut", "Big Hut"]
    doc.m_undo.undo()
    doc.m_undo.undo()
    assert not doc.m_changes.m_new


def test_cut_moves_a_new_asset_keeping_its_guid_and_replaces_a_game_asset_by_a_copy(doc):
    mine = doc.new_asset("", "Mine", copy_of=2001)
    assert doc.m_changes.m_new[mine].anchor == 2001
    assert doc.paste([mine], anchor=1002, cut=True) == [mine] and doc.m_changes.m_new[mine].anchor == 1002
    doc.m_undo.undo()
    assert doc.m_changes.m_new[mine].anchor == 2001
    (copy,) = doc.paste([2001], anchor=1002, cut=True)
    assert copy != 2001 and 2001 in doc.m_changes.m_removed and doc.m_changes.m_new[copy].anchor == 1002
