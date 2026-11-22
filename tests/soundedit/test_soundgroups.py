"""The sound editor's state and switch group edits on synthetic Init banks of the three layouts (112, 135, 172); the
chunk's layout is written out byte by byte here so a change of it shows."""

from __future__ import annotations

import struct

import pytest

from annotoolbox.exporting.banks import SBankInfo
from annotoolbox.formats import bnk, bnkgraph, bnkstmg
from annotoolbox.infra.i18n import N_
from annotoolbox.soundedit import groupedits, outline
from annotoolbox.soundedit.bankdoc import CBankDoc
from annotoolbox.soundedit.registry import CEditError

VERSIONS = (112, 135, 172)
HEADS = {112: bytes(6), 135: bytes(8), 172: bytes(14)}
SCREEN, MENU, GAME = (bnk.fnv1_32(n) for n in ("st_screen", "menu", "game"))
ZOOM, CAMERA, CLOSE, FAR = (bnk.fnv1_32(n) for n in ("sw_zoom", "camera_height", "close", "far"))


def raw_stmg(version: int) -> bytes:
    """Head, state group st_screen (1000 ms, one custom transition), one switch group driven by a game parameter
    (two points), two game parameters of 21 bytes and, from 135, the closing u32."""
    out = HEADS[version] + struct.pack("<I", 1) + struct.pack("<IIIIII", SCREEN, 1000, 1, MENU, GAME, 250)
    out += struct.pack("<I", 1) + struct.pack("<IIBI", ZOOM, CAMERA, 0, 2)
    out += struct.pack("<fII", 0.0, CLOSE, 9) + struct.pack("<fII", 50.0, FAR, 9)
    out += struct.pack("<I", 2) + bytes(range(21)) + bytes(range(21, 42))
    return out + (b"" if version == 112 else struct.pack("<I", 0))


def init_bank(version: int, with_stmg: bool = True) -> CBankDoc:
    chunks = [bnk.SChunk(b"BKHD", struct.pack("<III", version, 1355168291, 0))]
    if with_stmg:
        chunks.append(bnk.SChunk(b"STMG", raw_stmg(version)))
    chunks.append(bnk.SChunk(b"HIRC", b""))
    return CBankDoc(bnk.write(bnk.SSoundBank(chunks, [], [])), "data/sound/generatedsoundbanks/windows/1355168291.bnk")


@pytest.mark.parametrize("version", VERSIONS)
def test_the_state_manager_reads_as_pinned_and_renders_back(version):
    raw = raw_stmg(version)
    sm = bnkstmg.read(raw, version)
    assert [(g.id, g.transition, g.custom) for g in sm.states] == [(SCREEN, 1000, [(MENU, GAME, 250)])]
    assert [(s.id, s.parameter, s.points) for s in sm.switches] == [(ZOOM, CAMERA, [(0.0, CLOSE, 9), (50.0, FAR, 9)])]
    assert bnkstmg.render(sm) == raw
    for broken in (raw + b"x", raw[:-1], raw[:-5] if version == 112 else raw[:-9]):
        with pytest.raises(ValueError):
            bnkstmg.read(broken, version)


def test_the_ids_are_the_hashes_of_the_lower_case_names():
    assert groupedits.group_id("St_Screen") == SCREEN == bnk.fnv1_32("st_screen")


@pytest.mark.parametrize("version", VERSIONS)
def test_a_state_group_is_added_changed_and_removed(version):
    doc = init_bank(version)
    before = doc.to_bytes()
    ident = doc.do("add_state_group", group="st_water", states=["above", "under"], transition=1500)
    assert ident == bnk.fnv1_32("st_water")
    sm = bnkstmg.read(doc.chunk(b"STMG"), version)
    assert [(g.id, g.transition) for g in sm.states] == [(SCREEN, 1000), (ident, 1500)]
    assert sm.parameters == bnkstmg.read(raw_stmg(version), version).parameters  # the rest is as it was
    assert (
        doc.m_bank_info.groups[ident].name == "st_water"
        and doc.m_bank_info.groups[ident].values[bnk.fnv1_32("under")] == "under"
    )
    doc.do("set_state_group", group="st_water", transition=0, custom=[("above", "under", 4000), (7, 9, 0)])
    assert bnkstmg.read(doc.chunk(b"STMG"), version).states[1].custom == [
        (bnk.fnv1_32("above"), bnk.fnv1_32("under"), 4000), (7, 9, 0),
    ]  # fmt: skip
    doc.do("remove_state_group", group="st_water")
    assert [g.id for g in bnkstmg.read(doc.chunk(b"STMG"), version).states] == [SCREEN]
    assert CBankDoc(doc.to_bytes()).chunk(b"STMG") == raw_stmg(version)
    for _ in range(3):
        doc.undo()
    assert doc.to_bytes() == before and doc.chunk(b"STMG") == raw_stmg(version) and not doc.changed


@pytest.mark.parametrize("version", VERSIONS)
def test_a_switch_group_driven_by_a_game_parameter_is_added_and_removed(version):
    doc = init_bank(version)
    ident = doc.do("add_switch_group", group="sw_speed", parameter="speed", points=[(0, "slow", 9), (10, "fast", 9)])
    sm = bnkstmg.read(doc.chunk(b"STMG"), version)
    new = next(s for s in sm.switches if s.id == ident)
    assert (new.parameter, new.points) == (
        bnk.fnv1_32("speed"),
        [(0.0, bnk.fnv1_32("slow"), 9), (10.0, bnk.fnv1_32("fast"), 9)],
    )
    assert doc.m_bank_info.groups[ident].values == {bnk.fnv1_32("slow"): "slow", bnk.fnv1_32("fast"): "fast"}
    doc.do("remove_switch_group", group=ident)
    doc.do("remove_switch_group", group=ZOOM)
    assert bnkstmg.read(doc.chunk(b"STMG"), version).switches == []


def test_group_edits_that_do_not_fit_are_refused_and_leave_the_bank():
    doc = init_bank(135)
    before = doc.to_bytes()
    for name, args in (
        ("add_state_group", {"group": "st_screen"}),
        ("add_state_group", {"group": "  "}),
        ("add_state_group", {"group": "x", "transition": -1}),
        ("set_state_group", {"group": "nothing"}),
        ("set_state_group", {"group": SCREEN, "custom": [(1, 2)]}),
        ("remove_state_group", {"group": 99}),
        ("add_switch_group", {"group": "sw_zoom", "parameter": 5, "points": [(0, "a", 9)]}),
        ("add_switch_group", {"group": "sw_new", "parameter": 5, "points": []}),
        ("add_switch_group", {"group": "sw_new", "parameter": 5, "points": [(5, "a", 9), (1, "b", 9)]}),
        ("add_switch_group", {"group": "sw_new", "parameter": 5, "points": [(5, "a", 12)]}),
        ("add_switch_group", {"group": "sw_new", "parameter": 5, "points": [(float("nan"), "a", 9)]}),
        ("remove_switch_group", {"group": 99}),
    ):
        with pytest.raises(CEditError):
            doc.do(name, **args)
    assert doc.to_bytes() == before and not doc.changed


def test_a_bank_without_a_state_manager_says_where_the_groups_are():
    doc = init_bank(172, with_stmg=False)
    with pytest.raises(CEditError, match="Init bank"):
        doc.do("add_state_group", group="st_water")
    assert groupedits.group_lines(doc, SCREEN) == [] and groupedits.group_rows(doc) == []


def test_the_detail_panel_and_the_list_show_groups_by_name():
    doc = init_bank(172)
    doc.m_bank_info = SBankInfo()
    doc.do("add_state_group", group="st_water", states=["above"], transition=500)
    doc.do("set_state_group", group="st_water", custom=[("above", 5, 10)])
    rows = dict(groupedits.group_rows(doc))
    assert rows[bnk.fnv1_32("st_water")] == "state group st_water" and rows[ZOOM] == f"switch group {ZOOM}"
    lines = groupedits.group_lines(doc, bnk.fnv1_32("st_water"))
    assert lines[0].startswith("state group st_water") and "500 ms" in lines[0] and "above to 5: 10 ms" in lines[1]
    assert any("from 50 the switch" in line for line in groupedits.group_lines(doc, ZOOM))
    assert [r.title for r in outline.outline(doc)[N_("assets.groups")]] == sorted(rows.values())
    assert any("state group" in line for line in outline.describe(doc, SCREEN))
    assert bnkgraph.family(172) == "new"


def test_a_new_group_is_planned_as_an_edit_or_named_with_its_ids():
    init, other = init_bank(135), init_bank(135, with_stmg=False)
    assert groupedits.plan_group(init, "state", " st_new ", ["a"]) == (
        "add_state_group",
        {"group": "st_new", "states": ["a"]},
    )
    assert groupedits.plan_group(init, "switch", "sw_new", [], "speed", [(0.0, "slow", 9)])[0] == "add_switch_group"
    assert "needs no definition" in groupedits.plan_group(init, "switch", "sw_free", ["on", "off"])  # no parameter
    said = groupedits.plan_group(other, "state", "st_other", ["a"])
    assert (
        "Init bank" in said
        and str(bnk.fnv1_32("st_other")) in said
        and other.m_bank_info.groups[bnk.fnv1_32("st_other")].name == "st_other"
    )
    assert groupedits.plan_group(other, "switch", "sw_x", ["on"], "speed", [(0.0, "on", 9)]).startswith(
        "the switch group"
    )
    assert groupedits.plan_group(init, "state", " ", []) == "a group needs a name"
    assert other.m_bank_info.group_label(bnk.fnv1_32("st_other")) == "st_other"
    assert other.m_bank_info.group_label(bnk.fnv1_32("st_other"), bnk.fnv1_32("a")) == "a"
    assert other.m_bank_info.group_label(5, 6) == "6"


def test_the_window_picks_a_group_of_the_init_bank_without_a_fault(sound_window):
    window = sound_window
    window.m_doc = init_bank(172)
    window._fill_outline()
    window._select(SCREEN)  # an id of a state group, no object of the bank
    assert "state group" in window.m_facts.toPlainText() and not window.m_music.isVisibleTo(window)
    assert window.m_curves.m_which.count() == 0 and window.m_wave.m_shape is None
    top = window.m_outline.topLevelItem(window.m_outline.topLevelItemCount() - 1)
    window.m_outline.setCurrentItem(top.child(0))  # picked in the list, as a person does
    assert "group" in window.m_facts.toPlainText()
