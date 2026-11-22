"""The mod loader reproduced outside the game (``modloader``): XPath, every ModOp type and attribute, conditions,
variables and options, includes, what is reported, the mods' folders, profile and order, and where 1800's loader
differs from 117's (``docs/engine-modloader.md`` "Anno 1800's loader"); on synthetic files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from tests.sources.sourcesfakes import asset_list, fake_mod

from annotoolbox.modloader import engine, mods
from annotoolbox.modloader.mods import CMod, CVariables
from annotoolbox.modloader.patchdoc import CPatchDoc, canonical
from annotoolbox.modloader.xpath import CXPathError, evaluate, format_number, select


def doc_of(text: str = "") -> CPatchDoc:
    return CPatchDoc.load((text or asset_list()).encode())


def lazy_doc() -> CPatchDoc:
    """The asset list with every asset kept as bytes (ranges as an index gives them)."""
    data = asset_list().encode()
    ranges = [
        (m.start(), m.end(), int(re.search(rb"<GUID>(\d+)", m.group()).group(1)))
        for m in re.finditer(rb"<Asset>(?:(?!<Asset>).)*?<Standard><GUID>\d+.*?</Asset>(?=<Asset>|</Assets>)", data)
    ]
    return CPatchDoc.load(data, ranges)


def run(doc: CPatchDoc, ops: str, mod=None, variables=None) -> list[engine.SEvent]:
    return engine.run_text(doc, f"<ModOps>\n{ops}\n</ModOps>", mod, variables)


def value(doc: CPatchDoc, guid: int, path: str) -> list[str]:
    return [doc.string(n) for n in select(doc, f"Values/{path}", doc.assets(str(guid))[0])]


def test_xpath_values_functions_and_comparisons():
    doc = doc_of()
    hut = doc.assets("2001")[0]
    assert evaluate(doc, "count(//Asset)", None) == 6.0  # the inline asset of the quest counts
    assert evaluate(doc, "Values/Building/BuildCost/Item[1]/Amount * 2 + 1", hut) == 9.0
    assert evaluate(doc, "round(7 div 2)", hut) == 4.0 and evaluate(doc, "-3 mod 2", hut) == -1.0
    assert evaluate(doc, "Values/Building/Kinds = 'GameObject'", hut) is True
    assert evaluate(doc, "contains(Values/Standard/Name, 'u') and not(Values/Upkeep)", hut) is True
    assert evaluate(doc, "Values/Building/Workers/*[last()]/Count", hut)[0].text == "3"
    assert evaluate(doc, "concat(name(Values/*[2]), '-', string-length('abc'))", hut) == "Building-3"
    assert [a.tag for a in select(doc, "Values/Standard/GUID/.. | Values/Building", hut)] == ["Standard", "Building"]
    assert evaluate(doc, "//Values[Standard/GUID=9]/../Template", None)[0].text == "Good"  # the inline one too
    assert format_number(2.0) == "2" and format_number(0.1 * 3) == "0.3"
    with pytest.raises(CXPathError):
        evaluate(doc, "Values/[", hut)


def test_a_lazy_document_reads_only_the_assets_a_path_can_match():
    doc = lazy_doc()
    assert len(doc.m_lazy) == 5 and doc.assets("2001") and doc.m_materialized == 0
    found = select(doc, "//Values[Standard/GUID='2001']/Building/Kinds", None)
    assert [n.text for n in found] == ["GameObject"] and doc.m_materialized == 1  # only the hut's bytes hold it
    assert [a.findtext("Template") for a in select(doc, "//Asset[Template='Mission']", None)] == ["Mission"]
    assert select(doc, "//Asset[Values/Standard/GUID='9']/Template", None)[0].text == "Good"  # an inline asset
    assert doc.m_materialized == 2  # the quest, read once for both
    full = doc_of()
    run(doc, '<ModOp GUID="1002" Merge="Standard"><Name>Rock</Name></ModOp>')
    run(full, '<ModOp GUID="1002" Merge="Standard"><Name>Rock</Name></ModOp>')
    doc.materialize_all()
    assert canonical(doc.roots[0]) == canonical(full.roots[0])


def test_every_type_and_its_aliases():
    doc = doc_of()
    events = run(
        doc,
        """<ModOp Type="add" GUID="1001" Path="/Values/Product"><Weight>3</Weight></ModOp>
<ModOp Type="addNextSibling" GUID="1001"><Asset><Values><Standard><GUID>1100</GUID></Standard></Values></Asset></ModOp>
<ModOp Type="addAfter" GUID="1100"><Asset><Values><Standard><GUID>1101</GUID></Standard></Values></Asset></ModOp>
<ModOp Type="addPrevSibling" GUID="1001"><Asset><Values><Standard><GUID>1099</GUID></Standard></Values></Asset></ModOp>
<ModOp Type="addBefore" GUID="1099"><Asset><Values><Standard><GUID>1098</GUID></Standard></Values></Asset></ModOp>
<ModOp Type="replace" GUID="2001" Path="/Values/Building/Kinds"><Kinds>EditorObject</Kinds></ModOp>
<ModOp Type="remove" GUID="2001" Path="/Values/Building/Workers"/>
<ModOp Type="merge" GUID="2002" Path="/Values/Building"><Upkeep>4</Upkeep><Extra>1</Extra></ModOp>""",
    )
    assert events == []
    goods = [doc.m_guid_text[a] for a in doc.roots[0].find("Groups/Group/Assets")]
    assert goods == ["1098", "1099", "1001", "1100", "1101", "1002"]
    assert value(doc, 1001, "Product/*") == ["12", "3"]
    assert value(doc, 2001, "Building/Kinds") == ["EditorObject"] and not value(doc, 2001, "Building/Workers")
    assert value(doc, 2002, "Building/*") == ["4", "1"]


def test_new_assets_go_to_the_last_top_level_group_holding_assets():
    doc = doc_of()
    run(
        doc,
        """<Asset><Values><Standard><GUID>5</GUID></Standard></Values></Asset>
<Assets><Asset><Values><Standard><GUID>6</GUID></Standard></Values></Asset></Assets>
<ModOp Type="assets"><Asset><Values><Standard><GUID>7</GUID></Standard></Values></Asset></ModOp>
<ModOp Type="add"><Asset><Values><Standard><GUID>8</GUID></Standard></Values></Asset></ModOp>
<ModOp GUID="5" Merge="Standard"><Name>Found at once</Name></ModOp>""",
    )
    last = doc.roots[0].findall("Groups/Group")[-1].find("Assets")
    assert [doc.m_guid_text.get(a) for a in last] == ["3001", "5", "6", "7", "8"]
    assert value(doc, 5, "Standard/Name") == ["Found at once"]


def test_merge_is_positional_never_shortens_and_has_a_leaf_style():
    doc = doc_of()
    run(
        doc,
        """<ModOp GUID="2001" Merge="Building/BuildCost"><Item><Amount>9</Amount></Item><Item><Product>1</Product></Item></ModOp>
<ModOp GUID="2001" Merge="Building/Workers/High/Count"><Count>8</Count></ModOp>
<ModOp GUID="2001" Merge="Building"><Kinds Kind="x" /></ModOp>""",
    )
    assert value(doc, 2001, "Building/BuildCost/Item/*") == ["1001", "9", "1"]
    assert value(doc, 2001, "Building/Workers/High/Count") == ["8"]
    kinds = select(doc, "Values/Building/Kinds", doc.assets("2001")[0])[0]
    assert kinds.get("Kind") == "x" and kinds.text == "GameObject"  # an empty element keeps the text


def test_shorthands_paths_and_guid_lists():
    doc = doc_of()
    events = run(
        doc,
        """<ModOp GUID="1001,1002" Merge="Standard"><Name>Both</Name></ModOp>
<ModOp Type="merge" GUID="1001" Path="/Standard"><Name>mixed</Name></ModOp>
<ModOp Type="merge" GUID="1001" Path="//Values[Standard/GUID='2002']/Standard/"><Name>absolute</Name></ModOp>
<ModOp Type="remove" Path="//Values[Building/Upkeep &amp;gt; 8]/Building/Upkeep"/>
<ModOp Type="merge" Path="/"><Version>2</Version></ModOp>""",
    )
    assert value(doc, 1001, "Standard/Name") == value(doc, 1002, "Standard/Name") == ["Both"]
    assert [e.code for e in events] == ["nomatch"] and "GUID 1001" in events[0].message  # Type= starts at the asset
    assert value(doc, 2002, "Standard/Name") == ["absolute"] and not value(doc, 2002, "Building/Upkeep")
    assert doc.roots[0].findtext("Version") == "2"


def test_a_file_with_two_roots_is_patched_through_both():
    doc = CPatchDoc.load(b"<Normal><Fov>40</Fov></Normal><Simple><Fov>50</Fov></Simple>")
    run(doc, '<ModOp Type="merge" Path="/"><Fov>45</Fov></ModOp><ModOp Type="remove" Path="/Simple/Fov"/>')
    assert [r.findtext("Fov") for r in doc.roots] == ["45", None]


def test_conditions_variables_repeat_skip_and_allow_no_match():
    me = mods.CMod(Path("."), {}, "me", m_options={"range": "10"})
    other = mods.CMod(Path("."), {}, "other")
    variables = mods.CVariables([me, other], {"me": {"range": "30"}})
    doc = doc_of()
    events = run(
        doc,
        """<ModOp GUID="1001" Merge="Product" Condition="#other and !#gone"><Price>1</Price></ModOp>
<ModOp GUID="1002" Merge="Standard" Condition="!#other"><Name>never</Name></ModOp>
<ModOp GUID="1002" Merge="Standard" Condition="$range &gt; 20"><Name>thirty</Name></ModOp>
<ModOp GUID="1002" Merge="Standard" Condition="$me.range &lt; 20"><Name>ten</Name></ModOp>
<ModOp GUID="1002" Merge="Standard" Condition="$nothere"><Name>x</Name></ModOp>
<ModOp GUID="2001" Add="Building/BuildCost" Condition="count(//Asset[Values/Standard/GUID='2001']/Values/Building/BuildCost/Item) &lt; 3"
 MaxRepeat="5"><Item><Product>1</Product></Item></ModOp>
<ModOp GUID="2001" Remove="Building" Skip="false"/>
<Group Skip=""><ModOp GUID="2001" Remove="Building"/></Group>
<Group Condition="#gone"><ModOp GUID="2001" Remove="Building"/></Group>
<ModOp GUID="404" Remove="Building" AllowNoMatch="0"/>""",
        me,
        variables,
    )
    assert value(doc, 1001, "Product/Price") == ["1"] and value(doc, 1002, "Standard/Name") == ["thirty"]
    assert len(value(doc, 2001, "Building/BuildCost/Item/Product")) == 3 and value(doc, 2001, "Building/Kinds")
    codes = [(e.code, e.level) for e in events]
    assert ("variable", "warning") in codes and ("nomatch", "info") in codes
    assert ("skip", "info") in codes and sum(c == ("modcondition", "info") for c in codes) == 3
    asked = [e for e in events if e.code == "modcondition"]
    assert asked[0].others == ("gone", "other") and "True" in asked[0].message


def test_mod_value_mod_item_and_content():
    me = mods.CMod(Path("."), {}, "me", m_options={"factor": "3"})
    doc = doc_of()
    run(
        doc,
        """<ModOp GUID="2001" Merge="Building/BuildCost/Item[Product='1001']"><Amount><ModValue Insert="self::node() * $factor"/></Amount></ModOp>
<ModOp GUID="2001" Merge="Building"><Kinds><ModValue Merge="EditorObject;GameObject"/></Kinds></ModOp>
<ModOp GUID="2001" Merge="Building/BuildCost"><ModItem Merge="Product"><Product>1001</Product><Amount>5</Amount></ModItem></ModOp>
<ModOp GUID="2001" Merge="Building/BuildCost"><ModItem Merge="Product"><Product>1002</Product><Amount>1</Amount></ModItem></ModOp>
<ModOp GUID="2001" Merge="Building/BuildCost"><ModItem Merge="Product" Prepend="Product='1001'"><Product>7</Product></ModItem></ModOp>
<ModOp GUID="2002" Replace="Building/Upkeep" Content="number(Building/Upkeep) + 1"><Upkeep><ModOpContent/></Upkeep></ModOp>
<ModOp GUID="1002" Add="Standard" Content="Standard"><Copy><ModOpContent SkipParent="1"/></Copy></ModOp>
<ModOp GUID="2001" Merge="Building"><Kinds><ModValue Remove="GameObject"/></Kinds></ModOp>""",
        me,
        mods.CVariables([me]),
    )
    assert value(doc, 2001, "Building/BuildCost/Item/Product") == ["7", "1001", "1002"]
    assert value(doc, 2001, "Building/BuildCost/Item[Product='1001']/Amount") == ["5"]  # 4 * 3, then merged by key
    assert value(doc, 2001, "Building/Kinds") == ["EditorObject"]
    assert value(doc, 2002, "Building/Upkeep") == ["10"] and value(doc, 1002, "Standard/Copy/*") == ["1002", "Stone"]


def test_includes_relative_up_from_the_root_nested_and_missing(tmp_path):
    rename = '<ModOps><ModOp GUID="{g}" Merge="Standard"><Name>{n}</Name></ModOp>{more}</ModOps>'
    mod = fake_mod(tmp_path, "inc", {
        "data/x/assets.xml": '<ModOps><Include File="a/rel.include.xml"/><Include File="../y/up.include.xml"/>'
        '<Include File="/top.include.xml"/><Include File="a/missing.include.xml"/>'
        '<Include File="a/rel.include.xml" Condition="#gone"/></ModOps>',
        "data/x/a/rel.include.xml": rename.format(g=1001, n="rel", more='<Include File="sub/deep.include.xml"/>'),
        "data/x/a/sub/deep.include.xml": rename.format(g=1002, n="deep", more=""),
        "data/y/up.include.xml": rename.format(g=2001, n="up", more=""),
        "top.include.xml": rename.format(g=2002, n="root", more=""),
    })  # fmt: skip
    m, _ = mods.read_mod(mod)
    session = engine.CSession(lambda p: asset_list().encode() if p == "data/x/assets.xml" else None)
    session.apply_mod(m, mods.CVariables([m]))
    doc = session.m_docs["data/x/assets.xml"]
    assert [value(doc, g, "Standard/Name")[0] for g in (1001, 1002, 2001, 2002)] == ["rel", "deep", "up", "root"]
    assert [e.code for e in session.m_events if e.level == "warning"] == ["include"]
    assert [e.file for e in session.m_events if e.code == "include"] == ["data/x/assets.xml:1"]


def test_what_the_loader_drops_or_logs_is_reported_by_file_and_line(tmp_path):
    mod = fake_mod(tmp_path, "bad", {
        "data/x/assets.xml": '<ModOps>\n<Modop GUID="1"/>\n<ModOp GUID="1001"/>\n<ModOp Type="Merge" GUID="1001"/>\n'
        '<ModOp Type="merge" GUID="1001" Path="/Values/[" />\n</ModOps>',
        "data/x/broken.xml": "<ModOps><ModOp></ModOps>",
        "data/x/rooted.xml": "<Patch />",
        "data/x/new.xml": "<ModOps />",
        "data/x/a.cfg.xml": "<ModOps><ModOp Type='merge' Path='/Config'><Scale>2</Scale></ModOp></ModOps>",
        "data/infotips/export.bin.xml": "<ModOps />",
    })  # fmt: skip
    files = {"data/x/assets.xml": asset_list().encode(), "data/x/broken.xml": b"<a/>", "data/x/rooted.xml": b"<a/>",
             "data/x/a.cfg": b"<Config><Scale>1</Scale></Config>"}  # fmt: skip
    m, _ = mods.read_mod(mod)
    session = engine.CSession(files.get)
    session.apply_mod(m, mods.CVariables([m]))
    found = {(e.file, e.code) for e in session.m_events}
    assert {("data/x/assets.xml:2", "child"), ("data/x/assets.xml:3", "notype"), ("data/x/assets.xml:4", "type"),
            ("data/x/assets.xml:5", "xpath"), ("data/x/broken.xml", "parse"), ("data/x/rooted.xml", "root"),
            ("data/x/new.xml", "nobase"), ("data/infotips/export.bin.xml", "binary")} <= found  # fmt: skip
    assert session.m_docs["data/x/a.cfg"].roots[0].findtext("Scale") == "2"


def test_patch_targets_profiles_and_the_load_order(tmp_path):
    assert mods.patch_target("Data/X/a.cfg.xml") == "data/x/a.cfg" and mods.patch_target("a/b.include.xml") is None
    assert mods.patch_target("data/infotips/export.bin.xml") == "data/infotips/export.bin"
    docs, game = tmp_path / "docs", tmp_path / "game"
    fake_mod(docs, "zeta", {}, ModName={"English": "Alpha"}, Category={"English": "Audio"})
    fake_mod(docs, "alpha", {}, ModName={"English": "Zulu"}, Category={"English": "Audio"},
             Dependencies={"LoadAfter": ["beta", "missing"]})  # fmt: skip
    fake_mod(docs, "beta", {}, ModName={"English": "Beta"}, Category={"English": "Cheat"})
    fake_mod(docs, "late", {}, ModName={"English": "AAA"}, Dependencies={"LoadAfter": ["*"]})
    fake_mod(docs, "nocat", {}, ModName={"English": "Name"})
    fake_mod(docs, "old", {}, Version="1.0.0", Dependencies={"Deprecate": ["beta2"]})
    fake_mod(docs / "-parked", "x", {})
    fake_mod(docs, "-parked2", {})
    (docs / "copy").mkdir()
    (docs / "copy" / "modinfo.json").write_text('{"Anno": 8, "ModID": "zeta", "Version": "2.0.0"}')
    fake_mod(game / "mods" / ".ubi", "ubi-mod", {})
    fake_mod(docs, "wrong-game", {}, Anno=7)
    found, notes = mods.find_mods([r.path for r in mods.loader_roots("anno117", game, docs)], anno=8)
    by_id = {m.m_mod_id: m for m in found}
    assert "-parked2" not in by_id and by_id["zeta"].m_version == "2.0.0" and any("version" in n for n in notes)
    profile = mods.read_profile("## head\nDisplayName X\nEnableNewMods\n# beta\nalpha\nubi-mod\n# old\n")
    assert profile.on == ["alpha", "ubi-mod"] and profile.off == ["beta", "old"] and profile.enable_new
    on = mods.enabled(found, profile, (game / "mods", docs))
    assert {m.m_mod_id for m in on} == {"alpha", "ubi-mod", "zeta", "late", "nocat"}
    order = [m.m_mod_id for m in mods.load_order([by_id[k] for k in ("zeta", "alpha", "beta", "late", "nocat")])]
    assert order == ["nocat", "zeta", "beta", "alpha", "late"]  # no category first, LoadAfter, "*" last
    (docs / "profile.txt").write_text("ActiveProfile mine\n")
    assert mods.active_profile(docs) == docs / "profiles" / "mine.txt"
    (docs / "active-options.jsonc").write_text('// comment\n{"alpha": {"range": "5"}}')
    assert mods.read_options(docs) == {"alpha": {"range": "5"}}


def test_a_malformed_modinfo_reads_as_a_mod_without_that_list(tmp_path):
    for name, extra in {
        "a": {"Dependencies": {"Require": 5}},
        "b": {"Dependencies": {"LoadAfter": "other"}},
        "c": {"Options": []},
        "d": {"Dependencies": []},
        "e": {"Dependencies": {"Require": ["x", 3, None]}},
    }.items():
        fake_mod(tmp_path, name, {}, **extra)
    found, _ = mods.find_mods([tmp_path], 8)  # one broken modinfo must not stop the scan
    by_id = {m.m_mod_id: m for m in found}
    assert set(by_id) == set("abcde")
    assert by_id["a"].m_require == [] and by_id["b"].m_load_after == [] and by_id["c"].m_options == {}
    assert by_id["d"].m_require == [] and by_id["e"].m_require == ["x"]


def test_a_top_level_load_after_is_ignored_as_the_loader_does(tmp_path):
    fake_mod(tmp_path, "later", {}, LoadAfter=["base-lib"], ModName={"English": "A"})
    fake_mod(tmp_path, "base-lib", {}, ModName={"English": "Z"})
    found, _ = mods.find_mods([tmp_path], 8)
    later = next(m for m in found if m.m_mod_id == "later")
    assert later.m_load_after == []
    assert [m.m_mod_id for m in mods.load_order(found)] == ["later", "base-lib"]  # by display name, not LoadAfter


@pytest.mark.skipif(sys.platform != "win32", reason="a junction")
def test_a_mod_folder_that_is_a_junction_is_still_in_its_root(tmp_path):
    import _winapi

    source = tmp_path / "devsrc" / "dev"
    fake_mod(tmp_path / "devsrc", "dev", {})
    root = tmp_path / "mods"
    root.mkdir()
    _winapi.CreateJunction(str(source), str(root / "dev"))
    found, _ = mods.find_mods([root], 8)
    on = mods.switched_on(found, mods.SProfile("p", enable_new=True), (root,))
    assert [m.m_mod_id for m in on] == ["dev"]


def test_two_active_profile_lines_the_last_one_names_the_profile(tmp_path):
    (tmp_path / "profile.txt").write_text("ActiveProfile first\nActiveProfile second\n")
    assert mods.active_profile(tmp_path) == tmp_path / "profiles" / "second.txt"
    (tmp_path / "profile.txt").write_text("## nothing named\n")
    assert mods.active_profile(tmp_path) == tmp_path / "profiles" / "default.txt"
    (tmp_path / "profile.txt").write_text("ActiveProfile \n")
    assert mods.active_profile(tmp_path) is None


@pytest.mark.parametrize("raw", [b'{"ModID": "caf\xe9"}', '{"ModID": "x"}'.encode("utf-16")])
def test_a_modinfo_that_is_not_utf8_is_a_skipped_folder_not_a_crash(tmp_path, raw):
    fake_mod(tmp_path, "good", {})
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "modinfo.json").write_bytes(raw)
    (tmp_path / "active-options.jsonc").write_bytes(raw)
    found, notes = mods.find_mods([tmp_path], 8)  # one unreadable modinfo must not stop the scan
    assert [m.m_mod_id for m in found] == ["good"] and any("bad" in n and "not JSON" in n for n in notes)
    assert mods.read_options(tmp_path) == {}


# where the two games' loaders differ


def apply_as(ops: str, game: str, doc: CPatchDoc | None = None, keep=None):
    doc = doc or CPatchDoc.load(asset_list().encode())
    mod = CMod(Path("."), {}, "probe")
    session = engine.CSession(lambda _p: None, m_docs={"patch.xml": doc}, m_game=game, m_keep=keep)
    session.apply_file(mod, "patch.xml", f"<ModOps>{ops}</ModOps>".encode(), CVariables([mod]))
    return doc, session.m_events


def price_of(doc: CPatchDoc) -> str:
    return doc.string(select(doc, "Values/Product/Price", doc.assets("1001")[0])[0])


SHORT = '<ModOp GUID="1001" Merge="Product"><Price>77</Price></ModOp>'
LONG = '<ModOp Type="merge" GUID="1001" Path="/Values/Product"><Price>77</Price></ModOp>'


def test_the_short_form_is_read_by_117_and_ignored_by_1800():
    doc, events = apply_as(SHORT, engine.GAME_117)
    assert price_of(doc) == "77" and not [e for e in events if e.level == engine.WARNING]
    doc, events = apply_as(SHORT, engine.GAME_1800)
    assert price_of(doc) == "12"  # nothing happened
    assert [e.code for e in events if e.level == engine.WARNING] == ["feature", "notype"]
    assert price_of(apply_as(LONG, engine.GAME_1800)[0]) == "77"  # the long form is shared


def test_1800_ignores_property_and_a_bare_asset_and_reads_the_type_in_any_case():
    new = "<Asset><Template>Good</Template><Values><Standard><GUID>5</GUID></Standard></Values></Asset>"
    doc, events = apply_as(new, engine.GAME_1800)
    assert doc.assets("5") == [] and [e.code for e in events] == ["feature"]
    assert apply_as(new, engine.GAME_117)[0].assets("5")
    cased = LONG.replace('"merge"', '"Merge"')
    assert price_of(apply_as(cased, engine.GAME_117)[0]) == "12"  # 117: an unknown type, said
    assert price_of(apply_as(cased, engine.GAME_1800)[0]) == "77"
    prop = '<ModOp Type="merge" GUID="1001" Property="Product" Path="/Values/Product"><Price>77</Price></ModOp>'
    assert [e.code for e in apply_as(prop, engine.GAME_1800)[1]] == ["feature"]


@pytest.mark.parametrize(
    ("repeat", "expected", "game"), [(1, 2, engine.GAME_117), (9, 4, engine.GAME_117), (9, 2, engine.GAME_1800)]
)
def test_a_group_repeats_while_its_condition_holds_up_to_maxrepeat(repeat, expected, game):
    ops = '<ModOp GUID="2001" Type="add" Path="/Values/Building/BuildCost"><Item><Amount>1</Amount></Item></ModOp>'
    counted = "count(//Asset[Values/Standard/GUID='2001']/Values/Building/BuildCost/Item) &lt; 4"
    doc, _events = apply_as(f'<Group Condition="{counted}" MaxRepeat="{repeat}">{ops}</Group>', game)
    assert len(select(doc, "Values/Building/BuildCost/Item", doc.assets("2001")[0])) == expected


def test_assets_a_session_keeps_as_bytes_read_like_the_others():
    add = '<ModOp Type="addNextSibling" GUID="1002"><Asset><Template>Good</Template><Values><Standard><GUID>7</GUID><Name>Ore</Name></Standard></Values></Asset></ModOp>'
    notes = []
    frozen, _events = apply_as(
        add, engine.GAME_117, keep=lambda d, el: notes.append(el.findtext("Values/Standard/Name")) or "n"
    )
    plain, _events = apply_as(add, engine.GAME_117)
    (ore,) = frozen.assets("7")
    assert notes == ["Ore"] and frozen.is_lazy(ore) and frozen.m_notes[ore] == "n" and len(ore) == 0
    assert frozen.peek(ore).findtext("Values/Standard/Name") == "Ore" and frozen.is_lazy(ore)  # read, not kept
    again = apply_as('<ModOp GUID="7" Merge="Standard"><Name>Iron</Name></ModOp>', engine.GAME_117, frozen)[0]
    assert not again.is_lazy(ore) and ore not in again.m_notes  # an operation looked in
    plain_after = apply_as('<ModOp GUID="7" Merge="Standard"><Name>Iron</Name></ModOp>', engine.GAME_117, plain)[0]
    assert canonical(again.roots[0]) == canonical(plain_after.roots[0])
    # a search that must look into every asset finds the kept ones too
    third, _e = apply_as(add, engine.GAME_117, keep=lambda d, el: None)
    assert [n.text for n in select(third, "//Values[Standard/GUID='7']/Standard/Name", None)] == ["Ore"]


def test_a_path_below_an_unread_asset_reads_it_first():
    doc = lazy_doc()
    path = "/AssetList/Groups/Group/Assets/Asset[1]//Name"  # no ``//`` before the asset: nothing reads it on the way
    names = [n.text for n in select(doc, path, None)]
    assert names == [n.text for n in select(doc_of(), path, None)] and "Wood" in names


def test_a_removed_unread_asset_is_not_found_again_by_its_guid():
    doc, plain = lazy_doc(), doc_of()
    for d in (doc, plain):
        run(d, '<ModOp Type="remove" GUID="2001" />')
    assert select(doc, "//Asset[Values/Standard/GUID='2001']", None) == []
    assert doc.to_bytes() == plain.to_bytes()


def test_a_key_test_on_a_tag_that_only_ends_like_the_guid_path_is_not_read_from_the_guid_index():
    doc = doc_of("<R><A><MyStandard><GUID>5</GUID></MyStandard></A><A><Standard><GUID>5</GUID></Standard></A></R>")
    assert len(select(doc, "//A[MyStandard/GUID='5']", None)) == 1


@pytest.mark.parametrize("expr", ["count()", "contains('a')", "substring('a')", "translate('a', 'b')", "count('a')"])
def test_a_function_with_the_wrong_arguments_is_an_xpath_error(expr):
    with pytest.raises(CXPathError):
        evaluate(doc_of(), expr, None)
    events = run(doc_of(), f'<ModOp Type="remove" Path="//Asset[{expr}]" />')
    assert [e.code for e in events] == ["xpath", "nomatch"]


def test_a_game_file_that_is_no_xml_is_one_event_and_the_run_goes_on():
    files = {"data/bad.xml": b"<A><B></A>", "data/good.xml": b"<A><B>1</B></A>"}
    session = engine.CSession(files.get)
    mod = CMod(Path("."), {}, "m")
    remove = b'<ModOps><ModOp Type="remove" Path="/A/B" /></ModOps>'
    for rel in ("data/bad.xml", "data/bad.xml", "data/good.xml"):
        session.apply_file(mod, rel, remove, CVariables([mod]))
    assert [(e.code, e.file) for e in session.m_events] == [("badbase", "data/bad.xml")] * 2
    assert "data/bad.xml" in session.m_events[0].message and session.m_applied["m"] == 1


def test_an_expression_nested_without_end_is_an_xpath_error_not_a_recursion_error():
    from annotoolbox.modloader import xpathparse

    with pytest.raises(xpathparse.CXPathError):
        xpathparse.parse("(" * 5000 + "a" + ")" * 5000)
