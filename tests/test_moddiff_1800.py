"""Anno 1800's own loader rules in the Mod diff: a mod is on unless its folder's ``activations.json`` lists it (no
profile), ``.zip`` mods are told about, the mods' top-level dependency lists are read; and a comparison on a synthetic 1800
tree (``docs/engine-modloader.md`` "Anno 1800's loader")."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tests.fakes import ASSET_SCHEMA, asset_list, fake_mod

from annotoolbox.assetedit import modimport
from annotoolbox.assetedit.assetgame import open_game
from annotoolbox.moddiff import compare, jobs
from annotoolbox.modloader import activations
from annotoolbox.modloader.mods import read_mod
from annotoolbox.modmgr.manager import ModManager

EXPORT = "data/config/export/main/asset/"
ASSETS_1800 = EXPORT + "assets.xml"


def mod_1800(root: Path, mod_id: str, files: dict, **info) -> Path:
    """A 1800 mod: no ``Anno`` key, the dependency lists on the top level (``LoadAfterIds``, ``ModDependencies``)."""
    folder = fake_mod(root, mod_id, files)
    data = {"ModID": mod_id, "Version": "1.0.0", "ModName": {"English": mod_id}, **info}
    (folder / "modinfo.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def zip_of(path: Path, files: dict[str, str]) -> Path:
    """A ``.zip`` with the given members (name -> text)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return path


def price(guid: int, value: int) -> str:
    return f'<ModOps><ModOp Type="merge" GUID="{guid}" Path="/Values/Product"><Price>{value}</Price></ModOp></ModOps>'


def game_1800(root: Path) -> Path:
    """A synthetic 1800 tree: the 117 test game's schema and assets under 1800's paths."""
    files = {
        EXPORT + "properties-toolone.xml": ASSET_SCHEMA,
        ASSETS_1800: asset_list(),
        EXPORT + "templates.xml": "<Templates><Group><Name>Objects</Name></Group></Templates>",
    }
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8", newline="\n")
    return root


def test_a_mod_is_on_unless_its_folders_activations_lists_it(tmp_path):
    root = tmp_path / "mods"
    for mod_id in ("alpha", "beta", "gamma"):
        mod_1800(root, mod_id, {})
    other = tmp_path / "other"
    mod_1800(other, "delta", {})
    (root / activations.ACTIVATIONS).write_text('{"disabledIds": ["beta", 7]}', encoding="utf-8")
    (other / activations.ACTIVATIONS).write_text(
        '{"disabledIds": ["alpha"]}', encoding="utf-8"
    )  # another folder's list
    found = [read_mod(p)[0] for p in sorted([*root.iterdir(), *other.iterdir()]) if p.is_dir()]
    on = activations.switched_on([m for m in found if m], [root, other])
    assert sorted(m.mod_id for m in on) == ["alpha", "delta", "gamma"]
    assert activations.read_disabled(root) == {"beta"} and activations.read_disabled(tmp_path) == set()
    (root / activations.ACTIVATIONS).write_text("not json", encoding="utf-8")
    assert activations.read_disabled(root) == set()  # an unreadable file switches nothing off


def test_the_profile_of_1800_is_its_activations(tmp_path):
    mods = tmp_path / "mods"
    mod_1800(mods, "base-mod", {})
    mod_1800(mods, "late-mod", {}, LoadAfterIds=["base-mod"], ModDependencies=["base-mod"])
    mod_1800(mods, "off-mod", {})
    (mods / activations.ACTIVATIONS).write_text('{"disabledIds": ["off-mod"]}', encoding="utf-8")
    zip_of(mods / "extra.zip", {"zipped/modinfo.json": json.dumps({"ModID": "zipped"})})
    manager = ModManager("anno1800", mods)
    manager.scan()
    assert jobs.profile_names(manager) == [activations.ACTIVATIONS]
    order, notes = jobs.profile_order(manager, activations.ACTIVATIONS)
    assert [m.mod_id for m in order] == ["base-mod", "late-mod", "zipped"]
    assert any("activations.json" in n for n in notes) and any("read in place from .zip" in n for n in notes)


def test_a_1800_game_is_compared_by_the_same_rules(tmp_path):
    game = open_game("anno1800", game_1800(tmp_path / "1800"), tmp_path / "cache")
    root = tmp_path / "mods"
    a = read_mod(mod_1800(root, "a-mod", {ASSETS_1800: price(1001, 20)}))[0]
    b = read_mod(mod_1800(root, "b-mod", {ASSETS_1800: price(1001, 20)}, LoadAfterIds=["a-mod"]))[0]
    report = compare.profile_conflicts(game, [a, b], root, "activations.json", notes=["a note"])
    wood = next(t for t in report.targets if t.key == "1001")
    assert [(c.path, c.before, c.after) for c in wood.changes] == [("Values/Product/Price", "12", "20")]
    assert wood.mods == ["a-mod", "b-mod"] and report.notes[0] == "a note"
    assert report.conflicts[0].decided == "LoadAfter"


def test_the_asset_editors_import_follows_1800s_loader(tmp_path):
    game = open_game("anno1800", game_1800(tmp_path / "1800"), tmp_path / "cache")
    short = '<ModOps><ModOp GUID="1001" Merge="Product"><Price>99</Price></ModOp></ModOps>'  # a form of 117's only
    folder = mod_1800(tmp_path / "mods", "short", {ASSETS_1800: short})
    result = modimport.import_folder(game, folder)
    assert not result.changes.assets and "feature" in [e.code for e in result.dropped]
    folder = mod_1800(tmp_path / "mods", "plain", {ASSETS_1800: price(1001, 20)})
    assert modimport.import_folder(game, folder).changed == [1001]
