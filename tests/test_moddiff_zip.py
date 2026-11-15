"""Mods kept in a ``.zip``, read in place as Anno 1800's loader reads them: the archive's layouts, the files and
includes inside, the same report as the mod in a folder (``docs/engine-modloader.md`` "Anno 1800's loader")."""

from __future__ import annotations

import json

import pytest
from tests.test_moddiff_1800 import ASSETS_1800, game_1800, mod_1800, price, zip_of

from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.moddiff import compare, jobs
from annotoolbox.modloader import activations
from annotoolbox.modloader.mods import find_mods, read_mod
from annotoolbox.modloader.zipmods import ZipMod, read_zip_mods

INFO = json.dumps({"ModID": "zipped", "Version": "1.2.0", "ModName": {"English": "Zipped"}})


def test_a_modinfo_at_the_root_of_the_archive_is_one_mod(tmp_path):
    z = zip_of(tmp_path / "Mod.zip", {"modinfo.json": INFO, "data/a.txt": "abc", "readme.md": "x"})
    mods, notes = read_zip_mods(z)
    assert notes == [] and len(mods) == 1 and isinstance(mods[0], ZipMod)
    mod = mods[0]
    assert (mod.mod_id, mod.version, mod.name) == ("zipped", "1.2.0", "Zipped")
    assert mod.files() == ["data/a.txt", "modinfo.json", "readme.md"] and mod.size_of("data/a.txt") == 3
    assert mod.read("data/a.txt") == b"abc" and mod.is_file("readme.md") and not mod.is_file("data")
    assert mod.home == tmp_path and mod.source_of("readme.md").endswith("Mod.zip|readme.md")


def test_a_folder_in_the_archive_and_a_collection_are_mods(tmp_path):
    one = zip_of(tmp_path / "One.zip", {"Inner/modinfo.json": INFO, "Inner/data/a.txt": "1"})
    mods, _notes = read_zip_mods(one)
    assert [m.mod_id for m in mods] == ["zipped"] and mods[0].files() == ["data/a.txt", "modinfo.json"]
    many = zip_of(
        tmp_path / "Many.zip",
        {
            "A/modinfo.json": json.dumps({"ModID": "a"}),
            "B/modinfo.json": json.dumps({"ModID": "b"}),
            "-C/modinfo.json": json.dumps({"ModID": "c"}),  # parked, as a folder is
            "notes.txt": "x",
        },
    )
    mods, _notes = read_zip_mods(many)
    assert [m.mod_id for m in mods] == ["a", "b"] and mods[0].home == tmp_path


def test_an_archive_without_a_mod_or_a_broken_one_says_so(tmp_path):
    mods, notes = read_zip_mods(zip_of(tmp_path / "Empty.zip", {"data/a.txt": "x"}))
    assert mods == [] and "holds no mod" in notes[0]
    (tmp_path / "Bad.zip").write_bytes(b"PK")
    mods, notes = read_zip_mods(tmp_path / "Bad.zip")
    assert mods == [] and "not a readable archive" in notes[0]
    mods, notes = read_zip_mods(zip_of(tmp_path / "Json.zip", {"modinfo.json": "{"}))
    assert mods == [] and "not JSON" in notes[0]


def test_a_mod_without_a_modid_takes_the_name_of_the_archive(tmp_path):
    mods, _notes = read_zip_mods(zip_of(tmp_path / "Plain.zip", {"modinfo.json": "{}"}))
    assert mods[0].mod_id == "Plain"


def test_a_mod_nested_in_a_zip_mod_is_found_as_in_a_folder(tmp_path):
    z = zip_of(
        tmp_path / "Host.zip",
        {"modinfo.json": INFO, "data/a.txt": "1", "lib/modinfo.json": json.dumps({"ModID": "lib"}), "lib/b.txt": "2"},
    )
    host = read_zip_mods(z)[0][0]
    assert host.files() == ["data/a.txt", "modinfo.json"]
    (lib,) = host.nested
    assert lib.mod_id == "lib" and lib.files() == ["b.txt", "modinfo.json"] and lib.read("b.txt") == b"2"


def test_find_mods_reads_zips_only_when_asked(tmp_path):
    mod_1800(tmp_path, "folder-mod", {})
    zip_of(tmp_path / "z.zip", {"modinfo.json": INFO})
    zip_of(tmp_path / "-parked.zip", {"modinfo.json": json.dumps({"ModID": "parked"})})
    assert [m.mod_id for m in find_mods([tmp_path])[0]] == ["folder-mod"]  # 117 does not unpack
    assert sorted(m.mod_id for m in find_mods([tmp_path], zips=True)[0]) == ["folder-mod", "zipped"]


def test_a_zip_mod_is_switched_off_by_the_activations_of_its_folder(tmp_path):
    zip_of(tmp_path / "z.zip", {"modinfo.json": INFO})
    (tmp_path / activations.ACTIVATIONS).write_text('{"disabledIds": ["zipped"]}', encoding="utf-8")
    found = find_mods([tmp_path], zips=True)[0]
    assert activations.switched_on(found, [tmp_path]) == []


def patch_files(price_value: int) -> dict[str, str]:
    inc = '<ModOps><ModOp Type="merge" GUID="1001" Path="/Values/Product"><Price>%d</Price></ModOp></ModOps>'
    return {
        ASSETS_1800: '<ModOps><Include File="/x/more.include.xml" /></ModOps>',
        "x/more.include.xml": inc % price_value,
        "data/models/new.rdm": "model",
    }


def shape(report):
    """What a report says of its targets and files."""
    changes = [(t.ident, [(c.path, c.before, c.after) for c in t.changes]) for t in report.targets]
    return changes, [f.path for f in report.files]


def test_a_zip_mod_gives_the_report_of_the_same_mod_in_a_folder(tmp_path):
    game = open_game("anno1800", game_1800(tmp_path / "1800"), tmp_path / "cache")
    files = patch_files(33)
    folder = read_mod(mod_1800(tmp_path / "mods", "zipped", files))[0]
    zipped = read_zip_mods(zip_of(tmp_path / "zips" / "z.zip", {"modinfo.json": INFO, **files}))[0][0]
    by_folder = compare.mod_against_game(game, folder, [tmp_path / "mods"])
    by_zip = compare.mod_against_game(game, zipped, [tmp_path / "zips"])
    assert shape(by_zip) == shape(by_folder) and by_zip.dropped == by_folder.dropped == []
    assert [(c.path, c.after) for c in by_zip.targets[0].changes] == [("Values/Product/Price", "33")]
    assert [f.path for f in by_zip.files] == ["data/models/new.rdm"] and by_zip.files[0].size == 5


def test_a_zip_mod_is_found_by_its_path_and_its_id(tmp_path):
    zip_of(tmp_path / "mods" / "z.zip", {"modinfo.json": INFO, ASSETS_1800: price(1001, 40)})
    by_path = jobs.find_mod(str(tmp_path / "mods" / "z.zip"), None, "anno1800")
    assert by_path.mod_id == "zipped"
    manager = jobs.ModManager("anno1800", tmp_path / "mods")
    manager.scan()
    assert jobs.find_mod("zipped", manager, "anno1800").mod_id == "zipped"
    with pytest.raises(ValueError):
        jobs.find_mod("zipped", None, "anno117")
