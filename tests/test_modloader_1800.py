"""The mod loader's rules where the two games differ: what 1800's loader does not read (117's shorthands, ``Property``,
``MaxRepeat``, a bare ``<Asset>``), ``MaxRepeat`` on a ``Group``, and the assets a session keeps as bytes
(``docs/engine-modloader.md`` "Anno 1800's loader", ``docs/mod-diff.md`` "Speed and memory")."""

from __future__ import annotations

from pathlib import Path

from tests.fakes import asset_list

from annotoolbox.modloader import engine
from annotoolbox.modloader.mods import Mod, Variables
from annotoolbox.modloader.patchdoc import PatchDoc, canonical
from annotoolbox.modloader.xpath import select


def apply(ops: str, game: str, doc: PatchDoc | None = None, keep=None):
    doc = doc or PatchDoc.load(asset_list().encode())
    mod = Mod(Path("."), {}, "probe")
    session = engine.Session(lambda _p: None, docs={"patch.xml": doc}, game=game, keep=keep)
    session.apply_file(mod, "patch.xml", f"<ModOps>{ops}</ModOps>".encode(), Variables([mod]))
    return doc, session.events


def price(doc: PatchDoc) -> str:
    return doc.string(select(doc, "Values/Product/Price", doc.assets("1001")[0])[0])


SHORT = '<ModOp GUID="1001" Merge="Product"><Price>77</Price></ModOp>'
LONG = '<ModOp Type="merge" GUID="1001" Path="/Values/Product"><Price>77</Price></ModOp>'


def test_the_short_form_is_read_by_117_and_ignored_by_1800():
    doc, events = apply(SHORT, engine.GAME_117)
    assert price(doc) == "77" and not [e for e in events if e.level == engine.WARNING]
    doc, events = apply(SHORT, engine.GAME_1800)
    assert price(doc) == "12"  # nothing happened
    assert [e.code for e in events if e.level == engine.WARNING] == ["feature", "notype"]
    assert price(apply(LONG, engine.GAME_1800)[0]) == "77"  # the long form is shared


def test_1800_ignores_property_and_a_bare_asset_and_reads_the_type_in_any_case():
    new = "<Asset><Template>Good</Template><Values><Standard><GUID>5</GUID></Standard></Values></Asset>"
    doc, events = apply(new, engine.GAME_1800)
    assert doc.assets("5") == [] and [e.code for e in events] == ["feature"]
    assert apply(new, engine.GAME_117)[0].assets("5")
    cased = LONG.replace('"merge"', '"Merge"')
    assert price(apply(cased, engine.GAME_117)[0]) == "12"  # 117: an unknown type, said
    assert price(apply(cased, engine.GAME_1800)[0]) == "77"
    prop = '<ModOp Type="merge" GUID="1001" Property="Product" Path="/Values/Product"><Price>77</Price></ModOp>'
    assert [e.code for e in apply(prop, engine.GAME_1800)[1]] == ["feature"]


def test_a_group_repeats_while_its_condition_holds_up_to_maxrepeat():
    ops = '<ModOp GUID="2001" Type="add" Path="/Values/Building/BuildCost"><Item><Amount>1</Amount></Item></ModOp>'
    counted = "count(//Asset[Values/Standard/GUID='2001']/Values/Building/BuildCost/Item) &lt; 4"
    group = f'<Group Condition="{counted}" MaxRepeat="%d">{ops}</Group>'
    for n, expected, game in ((1, 2, engine.GAME_117), (9, 4, engine.GAME_117), (9, 2, engine.GAME_1800)):
        doc, _events = apply(group % n, game)
        assert len(select(doc, "Values/Building/BuildCost/Item", doc.assets("2001")[0])) == expected, (n, game)


def test_assets_a_session_keeps_as_bytes_read_like_the_others():
    add = '<ModOp Type="addNextSibling" GUID="1002"><Asset><Template>Good</Template><Values><Standard><GUID>7</GUID><Name>Ore</Name></Standard></Values></Asset></ModOp>'
    notes = []
    frozen, _events = apply(
        add, engine.GAME_117, keep=lambda d, el: notes.append(el.findtext("Values/Standard/Name")) or "n"
    )
    plain, _events = apply(add, engine.GAME_117)
    (ore,) = frozen.assets("7")
    assert notes == ["Ore"] and frozen.is_lazy(ore) and frozen.notes[ore] == "n" and len(ore) == 0
    assert frozen.peek(ore).findtext("Values/Standard/Name") == "Ore" and frozen.is_lazy(ore)  # read, not kept
    again = apply('<ModOp GUID="7" Merge="Standard"><Name>Iron</Name></ModOp>', engine.GAME_117, frozen)[0]
    assert not again.is_lazy(ore) and ore not in again.notes  # an operation looked in
    plain_after = apply('<ModOp GUID="7" Merge="Standard"><Name>Iron</Name></ModOp>', engine.GAME_117, plain)[0]
    assert canonical(again.roots[0]) == canonical(plain_after.roots[0])
    # a search that must look into every asset finds the kept ones too
    third, _e = apply(add, engine.GAME_117, keep=lambda d, el: None)
    assert [n.text for n in select(third, "//Values[Standard/GUID='7']/Standard/Name", None)] == ["Ore"]
