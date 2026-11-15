"""Island GUIDs from a project's range (suite/islandguids.py): new islands take the range's numbers, islands numbered
before the range keep theirs."""

from __future__ import annotations

import json

import pytest

from annotoolbox.suite import islandguids, project

RANGE = (1_900_000_000, 1_900_000_099)


class Earlier:
    """The editor's own scheme, as a stand-in: a fixed number per island."""

    def __init__(self) -> None:
        self.saved = 0

    def get(self, source: str) -> int:
        return 2_070_280_000 + len(source)

    def name_assets(self, xml: str) -> str:
        return xml

    def save(self) -> None:
        self.saved += 1


def test_a_new_project_numbers_every_island_from_its_range(tmp_path):
    path = tmp_path / islandguids.FILE
    islandguids.create(path, RANGE)
    numbering = islandguids.ProjectIslandGuids(path, RANGE, Earlier())
    first, second = numbering.get("a.a7m"), numbering.get("b.a7m")
    assert (first, second) == (RANGE[0], RANGE[0] + 1) and numbering.get("a.a7m") == first
    numbering.save()
    again = islandguids.ProjectIslandGuids(path, RANGE, Earlier())
    assert again.get("b.a7m") == second and again.get("c.a7m") == RANGE[0] + 2


def test_islands_numbered_before_the_range_keep_their_number_and_later_ones_use_the_range(tmp_path):
    path = tmp_path / islandguids.FILE
    earlier = Earlier()
    old = islandguids.ProjectIslandGuids(path, RANGE, earlier)  # no file yet: an older workspace
    held = old.get("old.a7m")
    assert held == 2_070_280_000 + len("old.a7m")
    old.save()
    assert earlier.saved == 1
    later = islandguids.ProjectIslandGuids(path, RANGE, earlier)
    assert later.get("old.a7m") == held
    assert later.get("new.a7m") == RANGE[0]
    later.save()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["legacy"] == {"old.a7m": held} and doc["guids"] == {"new.a7m": RANGE[0]}


def test_a_range_that_is_full_or_changed_is_said(tmp_path):
    path = tmp_path / islandguids.FILE
    islandguids.create(path, (5, 6))
    numbering = islandguids.ProjectIslandGuids(path, (5, 6), Earlier())
    numbering.get("a")
    numbering.get("b")
    with pytest.raises(RuntimeError, match="exhausted"):
        numbering.get("c")
    numbering.save()
    with pytest.raises(ValueError, match="range changed"):
        islandguids.ProjectIslandGuids(path, (100, 200), Earlier())


def test_saving_a_new_project_makes_its_registry_but_an_older_workspace_gets_none(tmp_path):
    fresh = project.Project(tmp_path / "fresh", mod_id="fresh", guid_range=RANGE)
    fresh.save()
    assert fresh.island_guids.is_file()
    old = tmp_path / "old"
    (old / "island-edits").mkdir(parents=True)
    (old / "island-edits" / "117_x.json").write_text("{}", encoding="utf-8")
    project.Project(old, mod_id="old", guid_range=RANGE).save()
    assert not (old / islandguids.FILE).exists()
