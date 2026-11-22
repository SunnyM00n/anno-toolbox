"""The particle editor's audit of 2026-11-14 and its leftovers (docs/particle-editor.md): refused values, edits that
change nothing, the saved copy's preview, the three save modes in each kind of game, new emitters, 1404's ``Blending``,
the 1701 kinds the preview draws, and the viewer's choice of the effect to edit."""

# ruff: noqa: F811  (the fixtures of test_particleedit are imported by name, as a test names them in its arguments)
from __future__ import annotations

import math

import numpy as np
import pytest
from tests.objbuild.objbuildfakes import SHARED_HOUSE_CFG as SHARED
from tests.particleedit.particleeditfakes import (  # noqa: F401
    BRACE_TEXT,
    EFC,
    EFC1701,
    EFC_TEXT,
    PARTICLE_CFG,
    RDP_PATH,
    rdp_bytes,
    tree117,
    window_on,
)
from tests.particleedit.particleeditfakes import PARTICLE_HOUSE_PATH as CFG
from tests.particleedit.particleeditfakes import particle_game as game  # noqa: F401

from annotoolbox.formats import efc1701
from annotoolbox.formats.efc1701edit import CBraceDoc
from annotoolbox.formats.rdp117 import parse_rdp
from annotoolbox.models import cfgpatch, particlebake, particles1701
from annotoolbox.models.cfgdoc import SDoc
from annotoolbox.models.particlebake import CBakeError
from annotoolbox.models.particles2070 import parse_efc
from annotoolbox.particleedit import emitters, fieldinfo, outline, rdptools, saving
from annotoolbox.particleedit.curves import SBraceColors, SBraceGraph, SXmlCurve, usable
from annotoolbox.particleedit.effectdoc import CEffectDoc, naming_leaves
from annotoolbox.paths import REPO_ROOT
from annotoolbox.render.particlefx import turns
from annotoolbox.viewer.effectlink import effect_to_edit
from annotoolbox.viewer.modelscene import CScene, SPlacedEffect


def rows_of(effect: CEffectDoc) -> dict[str, outline.SRow]:
    return {r.name: r for r in outline.outline(effect)[0].rows}


# Refused values


def test_a_typed_value_outside_its_range_or_with_a_line_break_is_refused(tree117, tree2070, tree1701):
    rdp = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    material = outline.outline(rdp)[1].rows
    order = next(r for r in material if r.name == "Order")
    assert "outside 0 to 255" in outline.set_row(rdp, order, "300")
    assert "outside 0 to 255" in outline.set_row(rdp, order, "-1")
    assert not isinstance(outline.set_row(rdp, order, "255"), str)
    frame = rows_of(rdp)["FrameTime"]
    assert "outside 1 to" in outline.set_row(rdp, frame, "0")
    assert "no number a file can hold" in outline.set_row(rdp, frame, "nan")
    assert "no number a file can hold" in outline.set_row(rdp, frame, "inf")
    assert "control character" in outline.set_row(rdp, rows_of(rdp)["TexturePath"], "a\nb")
    fx = CEffectDoc.open(tree2070, "anno2070", EFC)
    assert "is below 0" in outline.set_row(fx, rows_of(fx)["LifeTime"], "-5")
    assert not isinstance(outline.set_row(fx, rows_of(fx)["LifeTime"], "1.5"), str)
    assert "control character" in outline.set_row(fx, rows_of(fx)["Name"], "a\r\nb")
    brace = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    assert "is below 0" in outline.set_row(brace, rows_of(brace)["Duration"], "-1")
    assert "control character" in outline.set_row(brace, rows_of(brace)["Name"], "x\ty")


def test_a_bare_ampersand_breaks_a_tagged_file_but_not_a_1701_one(tree117, tree1701):
    rdp = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    path = rows_of(rdp)["TexturePath"]
    assert "bare &" in outline.set_row(rdp, path, "a&b.psd")
    assert not isinstance(outline.set_row(rdp, path, "a&amp;b.psd"), str)
    brace = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    assert not isinstance(outline.set_row(brace, rows_of(brace)["Name"], "a&b"), str)


def test_a_curve_with_a_number_no_file_can_hold_makes_no_edit(tree2070, tree1701):
    fx = CEffectDoc.open(tree2070, "anno2070", EFC)
    curve = SXmlCurve(fx.m_doc.root.children[0], "PEP")
    assert curve.set_points([(0.0, math.nan)]) is None and curve.set_points([(math.inf, 1.0)]) is None
    assert curve.set_stops([(0.0, (1.0, math.nan, 0.0, 1.0))]) is None
    brace = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    group = next(g for g in brace.m_doc.tree().groups("Effect")[0].groups()[0].groups() if g.name == "Alpha")
    assert SBraceGraph(brace.m_doc, group).set_points([(0.5, math.nan)]) is None
    colors = SBraceColors(brace.m_doc, brace.m_doc.tree().groups("Effect")[0].groups()[0].groups("Color")[0])
    assert colors.set_stops([(0.0, (0.0, 0.0, math.inf, 1.0))]) is None
    assert usable([(0.0, 1.0)], [(1.0, (0.0, 0.5))]) and not usable([(0.0, math.nan)])


def test_a_1701_point_keeps_its_tangents_where_the_file_lists_the_points_out_of_x_order():
    point = "\t\tPoint\n\t\t{{\n\t\t\tp {x} 0.5\n\t\t\ti {i} 0\n\t\t\to {o} 0\n\t\t}}\n"
    text = "Effect\n{\n\tAlpha\n\t{\n\t\tMinimum 0\n\t\tMaximum 1\n\t\tSingle 0\n"
    text += point.format(x="0.8", i="-8", o="8") + point.format(x="0.2", i="-2", o="2") + "\t}\n}\n"
    doc = CBraceDoc.read(text.encode("latin-1"))
    graph = SBraceGraph(doc, doc.tree().groups("Effect")[0].groups("Alpha")[0])
    assert graph.points() == [(0.2, 0.5), (0.8, 0.5)]
    graph.set_points([(0.25, 0.5), (0.8, 0.5)]).do()  # the point at 0.2 is dragged to 0.25
    doc.changed()
    moved = SBraceGraph(doc, doc.tree().groups("Effect")[0].groups("Alpha")[0])
    assert [(p[0], p[2], p[4]) for p in moved.graph().points] == [(0.25, -2.0, 2.0), (0.8, -8.0, 8.0)]


class FakeMouseEvent:
    """A mouse event at a place of the curve view."""

    def __init__(self, x: float, y: float, right: bool = False) -> None:
        self.x, self.y, self.right = x, y, right

    def position(self):
        from PySide6.QtCore import QPointF

        return QPointF(self.x, self.y)

    def button(self):
        from PySide6.QtCore import Qt

        return Qt.MouseButton.RightButton if self.right else Qt.MouseButton.LeftButton


def test_a_1701_colour_stop_is_dragged_and_a_point_removed_in_a_drag_ends_it(qt, tree1701, tree2070):
    from annotoolbox.particleedit.curveview import MARGIN, CCurveView

    view, seen = CCurveView(), []
    view.resize(400, 200)
    left, width = MARGIN, 400 - 1.5 * MARGIN
    brace = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    view.set_curve(SBraceColors(brace.m_doc, brace.m_doc.tree().groups("Effect")[0].groups()[0].groups("Color")[0]))
    view.stops_edited.connect(seen.append)
    view.mousePressEvent(FakeMouseEvent(left, 100))  # the one stop, at time 0
    view.mouseMoveEvent(FakeMouseEvent(left + width / 2, 100))
    view.mouseReleaseEvent(FakeMouseEvent(left + width / 2, 100))
    assert [round(t, 3) for t, _c in seen[-1]] == [0.5]
    view.mouseDoubleClickEvent(FakeMouseEvent(left + width, 100))
    assert [round(t, 3) for t, _c in seen[-1]] == [0.5, 1.0]
    fx = CEffectDoc.open(tree2070, "anno2070", EFC)
    view.set_curve(SXmlCurve(fx.m_doc.root.children[0], "PEP"))
    view.m_points = [(0.0, 0.0), (1.0, 100.0)]
    last = view._to_screen(1.0, 100.0)
    view.mousePressEvent(FakeMouseEvent(last.x(), last.y()))
    view.mousePressEvent(FakeMouseEvent(last.x(), last.y(), right=True))
    view.mouseMoveEvent(FakeMouseEvent(left, 100))  # the point is gone: nothing left to drag
    assert view.m_points == [(0.0, 0.0)]


def test_a_field_range_is_one_of_the_tables_not_a_guess():
    assert fieldinfo.info("rdp", "Order").limits == fieldinfo.BYTE
    assert fieldinfo.info("xml", "WindDependency").limits == (0.0, 1.0)
    assert fieldinfo.info("brace", "TimeScale").limits[0] > 0 and fieldinfo.info("xml", "Name").limits is None


# Edits that change nothing, and the saved effect's preview


def test_an_edit_that_changes_no_byte_is_no_undo_step(tree2070, tree117):
    fx = CEffectDoc.open(tree2070, "anno2070", EFC)
    curve = SXmlCurve(fx.m_doc.root.children[0], "PEP")
    fx.apply(curve.set_points(curve.points()))  # a click on a point: the same six-decimal numbers
    assert fx.m_undo.m_index == 0 and not fx.m_undo.dirty and fx.to_bytes() == fx.m_base
    fx.apply(outline.set_row(fx, rows_of(fx)["Loop"], "1"))  # typed as it already is
    assert fx.m_undo.m_index == 0
    rdp = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    rdp.apply(rdptools.tint(rdp.m_doc, (1.0, 1.0, 1.0, 1.0)))
    assert rdp.m_undo.m_index == 0
    rdp.apply(rdptools.tint(rdp.m_doc, (0.5, 1.0, 1.0, 1.0)))
    assert rdp.m_undo.m_index == 1 and rdp.changed


def test_an_edit_writes_the_document_once_not_once_per_question(tree117):
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    calls = []
    original = effect.m_doc.to_bytes
    effect.m_doc.to_bytes = lambda: calls.append(1) or original()
    effect.apply(rdptools.tint(effect.m_doc, (0.5, 1.0, 1.0, 1.0)))
    for _ in range(5):
        assert effect.changed and effect.differs_from_game and effect.preview_path()
    assert len(calls) <= 3  # before and after the edit, and the sync's (cached per version)


def test_an_effect_opened_from_the_projects_saved_copy_is_previewed_as_saved(tree117):
    saved = rdp_bytes().replace(b"<Looped>1</>", b"<Looped>0</>")
    assert saved != rdp_bytes()
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH, held=lambda p: saved if p == RDP_PATH else None)
    assert effect.to_bytes() == saved and not effect.changed and effect.differs_from_game
    shown = effect.preview_path()
    assert shown != RDP_PATH and effect.m_files.read(shown) == saved  # not the game's file the edits started from
    assert tree117.read(RDP_PATH) == rdp_bytes()
    plain = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    assert plain.preview_path() == RDP_PATH


def test_the_effect_at_its_own_path_is_not_saved_while_it_is_the_games_file(tree117):
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    plan = saving.plan(effect, saving.REPLACE)
    assert plan.problems and not plan.files
    effect.apply(rdptools.tint(effect.m_doc, (0.5, 1.0, 1.0, 1.0)))
    assert not saving.plan(effect, saving.REPLACE).problems


def test_the_tick_tools_keep_to_the_data_and_the_fade_marks_every_particle(tree117):
    doc = CEffectDoc.open(tree117, "anno117", RDP_PATH).m_doc
    assert rdptools.tint(doc, (math.nan, 1.0, 1.0, 1.0)) is None and rdptools.tint(doc, (-1.0, 1.0, 1.0, 1.0)) is None
    assert rdptools.tint(doc, (3.0, 1.0, 1.0, 1.0)) is None
    fade = rdptools.fade(doc)
    fade.do()
    read = parse_rdp(doc.to_bytes())
    alpha, at = np.array(read.colors, np.uint8).reshape(-1, 4)[:, 3], 0
    for i in range(len(read.starts)):
        count = read.ticks_of(i)
        assert alpha[at] == 0 and alpha[at + count - 1] == 0 and alpha[at + 1 : at + count - 1].min() > 0
        at += count
    fade.undo()
    ends = doc.root.find("P_Ends")
    held = ends.data
    ends.data = held[:-1]  # a blob that does not hold together
    assert rdptools.fade(doc) is None
    ends.data = held[:-2]
    assert rdptools.fade(doc) is None
    ends.data = None  # written as text, not as data
    assert rdptools.fade(doc) is None


# The three ways to save, in each kind of game


def effect_tree(game, key: str, shared: bool = False):
    files = {RDP_PATH: rdp_bytes(), CFG: PARTICLE_CFG.format(path=RDP_PATH)}
    if shared:
        files[SHARED] = files[CFG]
    return game(key, files)


def test_the_patch_of_a_copy_turns_the_games_config_into_the_pointed_one(tree117):
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH, CFG)
    effect.apply(rdptools.tint(effect.m_doc, (0.5, 1.0, 1.0, 1.0)))
    plan = saving.plan(effect, saving.COPY)
    copy = "data/base/house/house_smoke.rdp"
    base = SDoc.read(tree117.read(SHARED), SHARED)
    patched, warnings = cfgpatch.apply_patch(base, plan.files[SHARED + ".xml"])
    assert not warnings and naming_leaves(patched, copy) and not naming_leaves(patched, RDP_PATH)
    assert plan.files[copy] == effect.to_bytes() and plan.stale == [SHARED]
    assert saving.plan(effect, saving.REPLACE).files.keys() == {RDP_PATH}  # the config is untouched: no patch


def test_1800_patches_the_config_as_117_does_and_2205_replaces_it_whole(game):
    for key, patched in (("anno1800", True), ("anno2205", False)):
        tree = effect_tree(game, key)
        effect = CEffectDoc.open(tree, key, RDP_PATH, CFG)
        effect.apply(rdptools.tint(effect.m_doc, (0.5, 1.0, 1.0, 1.0)))
        found = {w.way: w for w in saving.ways(effect)}
        assert found[saving.COPY].allowed and ("ModOps patch" in found[saving.COPY].why) is patched
        plan = saving.plan(effect, saving.COPY)
        copy = "data/base/house/house_smoke.rdp"
        assert set(plan.files) == ({copy, CFG + ".xml"} if patched else {copy, CFG}), key
        assert plan.stale == ([CFG] if patched else [CFG + ".xml"])
        if not patched:
            assert b"house_smoke.rdp" in plan.files[CFG] and RDP_PATH.encode() not in plan.files[CFG]


def test_an_edit_of_the_placing_block_and_a_replaced_effect_save_the_config_too(tree117):
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH, CFG)
    row = next(r for r in outline.outline(effect)[-1].rows if r.name == "TimeScale")
    effect.apply(outline.set_row(effect, row, "2"))
    plan = saving.plan(effect, saving.REPLACE)
    assert (
        plan.files.keys() == {RDP_PATH, SHARED + ".xml"} and not plan.problems
    )  # nothing about the file itself changed
    assert b"TimeScale" in plan.files[SHARED + ".xml"]


def test_a_new_file_cannot_leave_the_games_folder_or_take_the_games_files(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    for bad in ("", "x.efc", "mods/x.efc", "C:/x/new.efc", "data/../../x/new.efc", "data/./new.efc", EFC):
        assert saving.plan(effect, saving.NEW, bad).problems or bad == EFC, bad
    assert saving.plan(effect, saving.NEW, "data/graphics/effects/mine.efc").files.keys() == {
        "data/graphics/effects/mine.efc"
    }


# New emitters


def test_a_2070_emitter_is_copied_to_the_end_with_a_name_of_its_own_and_undone(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    assert emitters.count(effect) == 1 and emitters.kinds(effect) == [("particles", 0)]
    for name, value in (("Delay", "250"), ("OffsetX", "3.5")):
        effect.apply(outline.set_row(effect, rows_of(effect)[name], value))
    before = effect.to_bytes()
    found = emitters.add_like(effect, 0)
    effect.apply(found)
    read = parse_efc(effect.to_bytes().decode())
    assert [e.index for e in read] == [0, 1] and read[1].name == "Copy of RenameMe0"
    assert read[1].delay_ms == 0.0 and read[1].offset == (0.0, 0.0, 0.0) and read[0].delay_ms == 250.0
    assert read[1].curve("PEP").at(0.5) == read[0].curve("PEP").at(0.5) and read[1].texture == read[0].texture
    effect.apply(emitters.add_like(effect, 0))
    assert parse_efc(effect.to_bytes().decode())[2].name == "Copy of RenameMe0 2"
    assert [i.key for i in outline.outline(effect)] == ["emitter/0", "emitter/1", "emitter/2"]
    effect.m_undo.undo()
    effect.m_undo.undo()
    assert effect.to_bytes() == before and effect.m_undo.m_index == 2


def test_a_1701_emitter_is_copied_with_its_lines_and_quotes(tree1701):
    effect = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    assert emitters.kinds(effect) == [("BillboardEffect", 0)]
    before = effect.to_bytes()
    effect.apply(emitters.add_like(effect, 0))
    made = CBraceDoc.read(effect.to_bytes())
    read = efc1701.read_efc(effect.to_bytes().decode("latin-1"))
    assert [e.kind for e in read] == ["BillboardEffect", "BillboardEffect"]
    assert read[1].name == "Copy of BillboardEffect0000" and read[1].duration_ms == read[0].duration_ms
    assert read[1].texture == read[0].texture and read[1].graph("Alpha").points == read[0].graph("Alpha").points
    assert made.tree().groups("Effect")[0].groups()[1].entries("Loop")[1].values == ["1"]  # both Loop lines came along
    effect.m_undo.undo()
    assert effect.to_bytes() == before


def test_a_baked_effect_and_a_read_only_one_have_no_emitters_to_copy(tree117):
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    assert emitters.count(effect) == 0 and emitters.kinds(effect) == []
    assert isinstance(emitters.add_like(effect, 0), str)
    empty = CEffectDoc.open(tree117, "anno117", "data/base/fx/empty.rdp")
    assert emitters.count(empty) == 0 and isinstance(emitters.add_like(empty, 0), str)
    assert emitters.emitter_index("emitter/3/PEP") == 3 and emitters.emitter_index("header") is None


def test_the_window_adds_an_emitter_and_selects_it(qt, tree2070, tmp_path):
    window = window_on(tree2070, tmp_path)
    window.open_path(EFC)
    assert window.m_add_like_action.isEnabled()
    window.add_emitter()
    assert window.current_item().key == "emitter/1" and "Copy of RenameMe0" in window.current_item().title
    window.undo()
    assert window.m_emitters.topLevelItemCount() == 1
    window.add_emitter()
    window.remove_emitter()
    assert window.m_emitters.topLevelItemCount() == 1 and window.current_item().key == "emitter/0"
    window._fill_other_kinds()  # the game's other effects are read once (here at once: the window is synchronous)
    assert [d.kind for d in window.game_kinds()] == ["particles"]
    assert window.m_add_other_menu.actions()[0].text() == "(the game has no other kind)"
    window.m_effect.m_undo.mark_clean()
    window.close()


def test_an_emitter_is_removed_and_the_later_ones_close_the_gap(tree2070, tree1701):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    assert isinstance(emitters.remove(effect, 0), str)  # an effect keeps one
    before = effect.to_bytes()
    effect.apply(emitters.add_like(effect, 0))
    effect.apply(emitters.add_like(effect, 0))
    effect.apply(emitters.remove(effect, 0))
    text = effect.to_bytes().decode()
    assert "<Effect0>" in text and "<Effect1>" in text and "<Effect2>" not in text  # the tags stay without a gap
    assert [e.name for e in parse_efc(text)] == ["Copy of RenameMe0", "Copy of RenameMe0 2"]
    for _ in range(3):
        effect.m_undo.undo()
    assert effect.to_bytes() == before
    brace = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    start = brace.to_bytes()
    brace.apply(emitters.add_like(brace, 0))
    brace.apply(emitters.remove(brace, 0))
    assert [e.name for e in efc1701.read_efc(brace.to_bytes().decode("latin-1"))] == ["Copy of BillboardEffect0000"]
    brace.m_undo.undo()
    brace.m_undo.undo()
    assert brace.to_bytes() == start


def test_a_kind_the_file_lacks_comes_from_another_effect_of_the_game(game):
    mesh = (
        "<Effect0><Name>Rock</Name>\r\n<EffectType>2</EffectType>\r\n<Delay>40</Delay>\r\n"
        "<Model>data/graphics/rock.rdm</Model>\r\n</Effect0>\r\n"
    )
    rock = "data/graphics/effects/rock.efc"
    tree = game("anno2070", {EFC: EFC_TEXT, rock: mesh, EFC1701: BRACE_TEXT})
    effect = CEffectDoc.open(tree, "anno2070", EFC)
    donors = emitters.game_kinds(tree, "anno2070", list(tree.m_paths))
    assert sorted((d.kind, d.syntax) for d in donors) == [
        ("BillboardEffect", "brace"),
        ("a mesh", "xml"),
        ("particles", "xml"),
    ]
    missing = emitters.lacking(effect, donors)
    assert [(d.kind, d.path) for d in missing] == [("a mesh", rock)]  # a 1701 kind does not fit a 2070 file
    effect.apply(emitters.add_donor(effect, missing[0]))
    read = parse_efc(effect.to_bytes().decode())
    assert [e.name for e in read] == ["RenameMe0", "Copy of Rock"] and read[1].delay_ms == 0.0
    assert tree.read(rock).decode() == mesh  # the donor file is only read


# 1404's Blending


def blended(**tags: str) -> str:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in tags.items())
    return f"<Effect0><Name>x</Name><EffectType>1</EffectType>{body}</Effect0>"


def test_1404s_blending_one_is_additive_like_2070s_technique_one():
    assert parse_efc(blended(Blending="1"))[0].technique == 1
    assert parse_efc(blended(Blending="0"))[0].technique == 0
    assert parse_efc(blended(Blending="3"))[0].technique == 0 and parse_efc(blended(Blending="2"))[0].technique == 0
    assert parse_efc(blended(Technique="2", Blending="1"))[0].technique == 2  # 2070 writes the other
    assert particlebake.material_of(parse_efc(blended(Blending="1"))[0]).additive
    assert not particlebake.material_of(parse_efc(blended(Blending="0"))[0]).additive


# The 1701 kinds the preview draws


def emitter_text(kind: str, body: str) -> str:
    head = (
        'Name "n"\nStartDelay 0\nDuration 1000\nLoop 0\nStateBlock "x"\nSrcBlend ""\nDestBlend ""\n'
        'Position 0 0 0\nRotation 0 0 0 1\nScaleFactors 1 1 1\nTexture "t.tga"\n'
    )
    colors = "Color\n{\nColor\n{\nRGBA 1 0 0 1\nTime 0\n}\nColor\n{\nRGBA 0 0 1 1\nTime 1\n}\n}\n"
    return f"Effect\n{{\n{kind}\n{{\n{head}{body}{colors}}}\n}}\n"


FIREWORK = (
    "LifeTime 1500\nDelayTime 1000\nFadeTime 500\nMove 1000\nSize 250\nHeight 100\nAlpha 0.5\nCountX2 4\nSegCount 6\n"
)
RING = "LifeTime 1000\nDelayTime 0\nFadeTime 500\nMove 1000\nSpread 40\nAlpha 1\nSegCount 12\nSize 500\nFlat 1\n"


def test_a_firework_is_a_ball_of_sparks_that_opens_and_fades():
    emitter = efc1701.read_efc(emitter_text("FireWorxEffect", FIREWORK + "RandomColorFlag 0\n"))[0]
    baked = particles1701.bake(emitter, "f")
    assert len(baked.particles) == 24 and not baked.looped
    reach = [max(abs(x) for x in p.position[-1][::2]) for p in baked.particles]
    assert max(reach) <= 250.0 + 1e-3 and max(abs(p.position[-1][1]) for p in baked.particles) <= 100.0 + 1e-3
    assert any(abs(p.position[-1][1]) > 50.0 for p in baked.particles)  # up and down: the ring of a ball
    first = baked.particles[0]
    assert first.position[0] == pytest.approx((0.0, 0.0, 0.0)) and first.color[0][3] == pytest.approx(0.5)
    assert first.color[-1][3] == pytest.approx(0.0, abs=1e-6)  # gone when its life ends
    assert first.color[0][:3] != first.color[-1][:3]  # the list runs over its life
    one = efc1701.read_efc(emitter_text("FireWorxEffect", FIREWORK + "RandomColorFlag 1\n"))[0]
    sparks = particles1701.bake(one, "f").particles
    assert len({p.color[0][:3] for p in sparks}) == 1 and sparks[0].color[0][:3] == sparks[0].color[-1][:3]


def test_a_flat_ring_widens_lies_on_the_ground_and_runs_along_its_circle():
    emitter = efc1701.read_efc(emitter_text("SuperNovaEffect", RING))[0]
    baked = particles1701.bake(emitter, "r")
    assert len(baked.particles) == 12 and baked.material.view_aligned == particles1701.FIXED
    for k, p in enumerate(baked.particles):
        angle = 2 * math.pi * k / 12
        end = p.position[-1]
        assert math.hypot(end[0], end[2]) == pytest.approx(500.0, abs=1.0)
        assert end[0] == pytest.approx(500.0 * math.cos(angle), abs=1.0) and p.position[0] == pytest.approx(
            (0, 0, 0), abs=1e-3
        )
        turn = turns(np.array([p.rotation[-1]]))[0]
        assert abs(turn[1, 0]) < 1e-5 and abs(turn[1, 2]) < 1e-5  # the quad's own axes lie in the ground plane
        tangent = np.array([-math.sin(angle), 0.0, math.cos(angle)])
        assert abs(float(np.dot(turn[:, 0], tangent))) == pytest.approx(1.0, abs=1e-4)  # its width along the ring
    assert p.color[-1][3] == pytest.approx(0.0, abs=1e-6) and p.size[-1][0] > p.size[0][0]
    upright = efc1701.read_efc(emitter_text("SuperNovaEffect", RING.replace("Flat 1", "Flat 0")))[0]
    assert particles1701.bake(upright, "r").material.view_aligned == particles1701.CAMERA


def test_the_kinds_that_are_not_drawn_say_why():
    granny = efc1701.read_efc(emitter_text("GrannyEffect", 'FileName "m.gr2"\nLifeTime 100\n'))[0]
    chains = efc1701.read_efc(emitter_text("VolumeChainsEffect", "Stretch 50\nMove 900\n"))[0]
    with pytest.raises(CBakeError, match="GrannyEffect is not drawn: a model"):
        particles1701.bake(granny, "g")
    with pytest.raises(CBakeError, match="not how the links move"):
        particles1701.bake(chains, "c")


def test_every_kind_of_the_1701_preview_is_a_kind_of_the_format_note():
    note = (REPO_ROOT / "docs/formats/anno1701-effects.md").read_text(encoding="utf-8")
    assert all(kind in note for kind in (*particles1701.KINDS, *particles1701.NOT_DRAWN))


# The viewer's choice of the effect to edit


def test_the_viewer_hands_over_the_effect_of_the_selected_part_with_its_config():
    place = np.identity(4)
    scene = CScene("m.cfg", "anno117", "data/m.cfg", m_effects=[SPlacedEffect("Particles/1", "A/One.rdp", place),
                                                              SPlacedEffect("Particles/2", "a/two.rdp", place)])  # fmt: skip
    assert effect_to_edit(scene, "Particles/2") == ("a/two.rdp", "data/m.cfg")
    assert effect_to_edit(scene, "") is None and effect_to_edit(scene, "Other/1") is None
    assert effect_to_edit(None, "x") is None and effect_to_edit(CScene("m", "anno117", "m"), "x") is None
    alone = CScene("a/one.rdp", "anno117", "a/one.rdp", m_effects=[SPlacedEffect("", "a/one.rdp", place)])
    assert effect_to_edit(alone, "") == ("a/one.rdp", "")
    one = CScene("m.cfg", "anno117", "data/m.cfg", m_effects=[SPlacedEffect("Particles/1", "x.rdp", place)])
    assert effect_to_edit(one, "") == ("x.rdp", "data/m.cfg")
