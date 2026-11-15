"""The Saves app's audit of 2026-11-14, one test per fault found: a save is only ever written beside the original and
an unchanged one is its bytes (also with stale blocks in the archive, also after an edit to the same value), the
unknown 2070 preamble is copied, names and numbers the form types, the tag tree's unnumbered nodes."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest
from tests.fakes import fake_save117, fake_save2070

from annotoolbox.formats import bintree, savegame117, savegame2070
from annotoolbox.formats.isd2070 import Node
from annotoolbox.saveedit import container, edits, records, summary


def test_an_unchanged_117_write_is_the_original_with_the_blocks_the_game_left_in_it(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "Session Backup.a8s")
    compact = path.read_bytes()
    parsed = savegame117.read_bytes(compact)
    assert parsed.original == compact and parsed.to_bytes() == compact
    stale = dataclasses.replace(parsed, original=b"an archive laid out as the game left it")  # not rebuildable
    assert stale.to_bytes() == stale.original
    changed = savegame117.set_money(stale, 5)
    assert (
        changed.to_bytes() != stale.original and savegame117.read_money(savegame117.read_bytes(changed.to_bytes())) == 5
    )


def test_a_2070_edit_to_the_same_value_is_the_original(tmp_path: Path):
    path = fake_save2070(tmp_path / "Spielstand.sww")
    save = container.open_file(path, "anno2070")
    assert edits.write_beside(save, {"money": 80000}).read_bytes() == path.read_bytes()


def test_the_2070_preamble_is_copied_from_the_original_whatever_it_holds(tmp_path: Path):
    path = fake_save2070(tmp_path / "Spielstand.sww")
    data = bytearray(path.read_bytes())
    data[524:544] = bytes(range(200, 220))  # the 20 bytes the game's own saves differ in
    path.write_bytes(bytes(data))
    written = edits.write_beside(container.open_file(path, "anno2070"), {"money": 7})
    out = written.read_bytes()
    assert out[520:544] == bytes(data[520:544]) and out != bytes(data)
    assert savegame2070.read_money(savegame2070.read_bytes(out)) == 7


def test_the_original_is_never_touched(tmp_path: Path):
    for game, path in (("anno117", fake_save117(tmp_path / "P" / "a.a8s")),
                       ("anno2070", fake_save2070(tmp_path / "b.sww"))):  # fmt: skip
        path.chmod(0o444)
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        key = "money:41" if game == "anno117" else "money"
        edits.write_beside(container.open_file(path, game), {key: 3})
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
        path.chmod(0o644)


def test_an_edited_save_edited_again_counts_on_instead_of_nesting(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "Marcia.a8s")
    first = edits.write_beside(container.open_file(path, "anno117"), {"money:41": 1})
    second = edits.write_beside(container.open_file(first, "anno117"), {"money:41": 2})
    third = edits.write_beside(container.open_file(path, "anno117"), {})
    assert [p.name for p in (first, second, third)] == ["Marcia (edited).a8s", "Marcia (edited 2).a8s",
                                                         "Marcia (edited 3).a8s"]  # fmt: skip


def test_a_value_the_save_has_no_field_for_is_refused_and_nothing_is_written(tmp_path: Path):
    path = fake_save117(tmp_path / "P" / "a.a8s")
    with pytest.raises(edits.EditError, match="no such value"):
        edits.write_beside(container.open_file(path, "anno117"), {"moneyy:41": 5})
    assert [p.name for p in path.parent.iterdir()] == ["a.a8s"]


def test_the_forms_text_is_read_as_a_number_or_a_name():
    money = edits.Field("money", "m", edits.NUMBER, 1500)
    guid = edits.Field("color", "c", edits.GUID, 91304)
    text = edits.Field("name", "n", edits.TEXT, "Tester")
    assert edits.parse_value(money, "0100") == 100 and edits.parse_value(money, " 1,500,000 ") == 1_500_000
    assert edits.parse_value(guid, "0x1F") == 31 and edits.parse_value(guid, "91306") == 91306
    assert edits.parse_value(text, " Julia ") == " Julia "
    with pytest.raises(ValueError):
        edits.parse_value(money, "lots")
    typed = {"money": "1500 ", "color": "91304", "name": "Tester "}
    assert edits.changed_values([money, guid, text], typed) == {"name": "Tester "}
    assert edits.changed_values([money], {"money": "9"}) == {"money": 9} and edits.changed_values([money], {}) == {}


def test_a_node_whose_number_the_table_lacks_keeps_its_number():
    raw = bintree.write(bintree.Tree([Node("Root", None, None, [Node("#9", "x"), Node("Named", "y")])]))
    back = bintree.read(raw)
    assert back.tags == {"Root": 1, "Named": 2} and back.path("Root/#9").text == "x"
    assert bintree.write(back) == raw


def test_a_2070_summary_survives_numbers_that_are_none(tmp_path: Path):
    path = fake_save2070(tmp_path / "a.sww")
    parsed = savegame2070.read_bytes(path.read_bytes())
    stats = bintree.read(bintree.write(parsed.trees["stats.xml"]))
    stats.path("GameStats").children.append(Node("PlayingTimeMilliSec", "n/a"))
    save = parsed.with_trees({"stats.xml": stats})
    damaged = tmp_path / "b.sww"
    savegame2070.write(save, damaged)
    s = summary.summarize(container.open_file(damaged, "anno2070"))
    assert dict(next(x for x in s.sections if x.title == "Save").rows)["Play time"] == "0:00 h"
    assert records.number("12") == 12 and records.number("x", 7) == 7 and records.number(None) == 0
    assert os.path.exists(damaged)
