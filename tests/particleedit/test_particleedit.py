"""The particle editor on synthetic files of the three syntaxes (docs/particle-editor.md): each reads and writes back
byte for byte, its tree and fields, the edits of a field, a curve, a colour list and the tick data with their undo, the
ways to save (the effect at its path, a copy for one model with its config pointed at it, a new file), the project's
share, and the window offscreen (the form, the graph, the preview's scene, the play bar, a save)."""

from __future__ import annotations

import math
import os

import pytest
from tests.objbuild.objbuildfakes import SHARED_HOUSE_CFG as SHARED
from tests.particleedit.particleeditfakes import (  # noqa: F401
    BRACE_TEXT,
    CFG2070,
    EFC,
    EFC1701,
    RDP_PATH,
    tree117,
    window_on,
)
from tests.particleedit.particleeditfakes import PARTICLE_HOUSE_PATH as CFG
from tests.particleedit.particleeditfakes import particle_game as game  # noqa: F401

from annotoolbox.formats import efc1701
from annotoolbox.formats.efc1701edit import CBraceDoc, field_text
from annotoolbox.formats.rdp117 import parse_rdp
from annotoolbox.models.particles2070 import parse_efc
from annotoolbox.particleedit import outline, projectpart, rdptools, saving
from annotoolbox.particleedit.curves import COLORS, SBraceColors, SBraceGraph, SXmlCurve
from annotoolbox.particleedit.effectdoc import BRACE, RDP, XML, CEffectDoc
from annotoolbox.suite import buildout
from annotoolbox.suite.project import CProject

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_each_syntax_reads_and_writes_back_byte_for_byte(tree117, tree2070, tree1701):  # noqa: F811
    for tree, path, syntax in ((tree117, RDP_PATH, RDP), (tree2070, EFC, XML), (tree1701, EFC1701, BRACE)):
        effect = CEffectDoc.open(tree, tree.m_game, path)
        assert effect.m_syntax == syntax and effect.m_exact and effect.editable
        assert effect.to_bytes() == tree.read(path) and not effect.changed
        assert effect.preview_path() == path


def test_an_rdp_is_a_header_its_materials_and_the_tick_data(tree117):  # noqa: F811
    items = outline.outline(CEffectDoc.open(tree117, "anno117", RDP_PATH))
    assert [i.key for i in items] == ["header", "material/0", "ticks"]
    header = {r.name: r for r in items[0].rows}
    assert header["FrameTime"].field_info.kind == "number" and header["ParticleCount"].read_only
    assert "Center.x" in header and "T_Colors" not in header
    assert {r.name for r in items[2].rows} >= {"T_Colors", "P_Starts"} and all(r.read_only for r in items[2].rows)
    assert {r.name: r for r in items[1].rows}["ViewAligned"].field_info.choices


def test_a_field_edit_is_undone_to_the_same_bytes(tree117):  # noqa: F811
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    row = next(r for r in outline.outline(effect)[0].rows if r.name == "FrameTime")
    assert isinstance(outline.set_row(effect, row, "nan"), str) and isinstance(outline.set_row(effect, row, "x"), str)
    effect.apply(outline.set_row(effect, row, "66.666672"))
    assert parse_rdp(effect.to_bytes()).frame_time == pytest.approx(66.666672)
    assert effect.preview_path() != RDP_PATH and effect.m_files.read(effect.preview_path()) == effect.to_bytes()
    assert effect.seconds() == pytest.approx(8 * 66.666672 / 1000)
    effect.m_undo.undo()
    assert effect.to_bytes() == tree117.read(RDP_PATH) and effect.preview_path() == RDP_PATH


def test_a_tint_and_a_fade_change_the_ticks_colours_only(tree117):  # noqa: F811
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    before = parse_rdp(effect.to_bytes())
    effect.apply(rdptools.tint(effect.m_doc, (0.5, 1.0, 1.0, 1.0)))
    after = parse_rdp(effect.to_bytes())
    assert after.positions == before.positions and after.starts == before.starts
    assert all(a[2] == round(b[2] * 0.5) and a[0] == b[0] for a, b in zip(after.colors, before.colors, strict=True))
    effect.apply(rdptools.fade(effect.m_doc))
    assert parse_rdp(effect.to_bytes()).colors[0][3] == 0
    effect.m_undo.undo()
    effect.m_undo.undo()
    assert effect.to_bytes() == tree117.read(RDP_PATH)
    assert "3 particles" in rdptools.facts(effect.m_doc)


def test_an_empty_file_opens_read_only(tree117):  # noqa: F811
    effect = CEffectDoc.open(tree117, "anno117", "data/base/fx/empty.rdp")
    assert not effect.editable and effect.m_notes
    assert outline.outline(effect)[0].note
    assert saving.plan(effect, saving.REPLACE).problems


def test_a_2070_emitter_has_its_header_and_its_curves(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    (emitter,) = outline.outline(effect)
    assert "RenameMe0" in emitter.title and "(particle)" in emitter.title
    names = {r.name for r in emitter.rows}
    assert "Technique" in names and "PEPValueMin" not in names and "PEPKnotX_0" not in names
    curves = {c.key.rsplit("/", 1)[-1]: c for c in emitter.children}
    assert set(curves) == {"PEP", "Color"} and curves["Color"].curve.kind == COLORS
    assert {r.name for r in curves["PEP"].rows} == {"ValueMin", "ValueMax", "ValueDef", "Varianz", "Size"}
    assert curves["PEP"].curve.points() == [(1.0, pytest.approx(20.0))] and curves["PEP"].note


def test_a_2070_curve_edit_writes_its_knots_and_is_undone(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    pep = SXmlCurve(effect.m_doc.root.children[0], "PEP")
    effect.apply(pep.set_points([(0.0, 10.0), (0.5, 80.0), (1.0, 40.0)]))
    data = effect.to_bytes()
    assert b"<PEPSize>3</PEPSize>" in data and b"<PEPKnotPos_2>1.000000</PEPKnotPos_2>" in data
    curve = parse_efc(data.decode())[0].curve("PEP")
    assert curve.at(0.5) == pytest.approx(80.0) and curve.at(0.25) == pytest.approx(45.0)
    color = SXmlCurve(effect.m_doc.root.children[0], "Color")
    effect.apply(color.set_stops([(0.0, (1.0, 0.0, 0.0, 1.0)), (1.0, (0.0, 0.0, 1.0, 1.0))]))
    stops = SXmlCurve(effect.m_doc.root.children[0], "Color").stops()
    assert stops[0] == (0.0, (1.0, 0.0, 0.0, 1.0)) and stops[1][1][2] == 1.0
    effect.m_undo.undo()
    effect.m_undo.undo()
    assert effect.to_bytes() == tree2070.read(EFC)


def test_a_1701_emitter_keeps_its_two_loop_lines_and_its_quotes(tree1701):
    effect = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    (emitter,) = outline.outline(effect)
    rows = {r.name: r for r in emitter.rows}
    assert "Loop" in rows and any(name.startswith("Loop (") for name in rows)
    assert rows["Name"].value == "BillboardEffect0000"
    effect.apply(outline.set_row(effect, rows["Name"], "smoke"))
    assert b'Name "smoke"\r\n' in effect.to_bytes()
    assert efc1701.read_efc(effect.to_bytes().decode("latin-1"))[0].name == "smoke"
    effect.m_undo.undo()
    assert effect.to_bytes() == tree1701.read(EFC1701)


def test_a_1701_graph_and_colour_list_are_edited(tree1701):
    effect = CEffectDoc.open(tree1701, "anno1701", EFC1701)
    (emitter,) = outline.outline(effect)
    kinds = {type(c.curve) for c in emitter.children}
    assert kinds == {SBraceGraph, SBraceColors}
    alpha = next(c.curve for c in emitter.children if isinstance(c.curve, SBraceGraph))
    effect.apply(alpha.set_points([(0.0, 0.0), (0.3, 0.8), (1.0, 0.0)]))
    read = efc1701.read_efc(effect.to_bytes().decode("latin-1"))[0]
    graph = read.graph("Alpha")
    assert not graph.single and len(graph.points) == 3 and graph.at(0.3) == pytest.approx(0.8)
    (emitter,) = outline.outline(effect)  # the tree is read again after an edit
    colours = next(c.curve for c in emitter.children if isinstance(c.curve, SBraceColors))
    effect.apply(colours.set_stops([(0.0, (1.0, 0.5, 0.0, 1.0)), (1.0, (0.0, 0.0, 0.0, 0.0))]))
    read = efc1701.read_efc(effect.to_bytes().decode("latin-1"))[0]
    assert read.colors["Color"].at(0.5) == pytest.approx((0.5, 0.25, 0.0, 0.5))
    effect.m_undo.undo()
    effect.m_undo.undo()
    assert effect.to_bytes() == tree1701.read(EFC1701)


def test_the_brace_document_keeps_every_line_and_rewrites_one():
    data = BRACE_TEXT.replace("\r\n", "\n").encode() + b"\t"  # LF and a last line without its end
    doc = CBraceDoc.read(data)
    assert doc.to_bytes() == data
    entry = doc.tree().groups("Effect")[0].groups()[0].entries("Duration")[0]
    assert field_text(entry, ["2000"]) == "\t\tDuration 2000"
    with pytest.raises(efc1701.CEfcError):
        CBraceDoc.read(b"Effect\r\n{\r\n")


def test_a_typed_value_that_breaks_a_file_is_refused(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    rows = {r.name: r for r in outline.outline(effect)[0].rows}
    assert "would break" in outline.set_row(effect, rows["Name"], "a<b")
    assert "is not one of" in outline.set_row(effect, rows["EffectType"], "9")
    assert outline.set_row(effect, rows["Loop"], "2") == "0 or 1"
    assert not isinstance(outline.set_row(effect, rows["Loop"], "0"), str)


def test_a_copy_for_one_model_points_its_117_config_at_it_with_a_patch(tree117):  # noqa: F811
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH, CFG)
    assert effect.m_model.read == SHARED and len(effect.m_model.blocks) == 1
    assert outline.outline(effect)[-1].key == "model/0"
    effect.apply(rdptools.tint(effect.m_doc, (1.0, 1.0, 1.0, 0.5)))
    way = {w.way: w for w in saving.ways(effect)}
    assert way[saving.COPY].allowed and way[saving.COPY].path == "data/base/house/house_smoke.rdp"
    plan = saving.plan(effect, saving.COPY)
    assert not plan.problems and set(plan.files) == {"data/base/house/house_smoke.rdp", SHARED + ".xml"}
    assert b"house_smoke.rdp" in plan.files[SHARED + ".xml"] and plan.stale == [SHARED]
    replace = saving.plan(effect, saving.REPLACE)
    assert set(replace.files) == {RDP_PATH}


def test_a_copy_in_2070_replaces_the_config_whole_in_its_own_slashes(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC, CFG2070)
    plan = saving.plan(effect, saving.COPY)
    copy = "data/graphics/buildings/hut/hut_fire.efc"
    assert set(plan.files) == {copy, CFG2070}
    assert b"data\\graphics\\buildings\\hut\\hut_fire.efc" in plan.files[CFG2070]
    assert plan.files[copy] == tree2070.read(EFC)


def test_a_new_file_needs_its_extension_and_a_free_path(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    assert not {w.way: w for w in saving.ways(effect)}[saving.COPY].allowed
    assert saving.plan(effect, saving.NEW, "data/x/new.rdp").problems
    assert saving.plan(effect, saving.NEW, "../x/new.efc").problems
    assert saving.plan(effect, saving.NEW, CFG2070.replace(".cfg", ".efc")).files


def test_the_project_holds_the_save_and_reopens_it(tree117, tmp_path):  # noqa: F811
    project = CProject(tmp_path / "proj", mod_id="my-mod")
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH)
    row = next(r for r in outline.outline(effect)[0].rows if r.name == "Looped")
    effect.apply(outline.set_row(effect, row, "0"))
    plan = saving.plan(effect, saving.REPLACE)
    projectpart.EDITS.save_announced(project, "anno117", plan.files, plan.stale)
    saved = projectpart.EDITS.saved_files(project, "anno117")
    assert buildout.read_all(projectpart.PART.render(project, None, lambda _m: None)) == {RDP_PATH: effect.to_bytes()}
    again = CEffectDoc.open(tree117, "anno117", RDP_PATH, held=lambda p: saved[p].read_bytes() if p in saved else None)
    assert again.to_bytes() == effect.to_bytes() and again.m_notes and not parse_rdp(again.to_bytes()).looped


def test_the_curve_models_read_what_the_bakers_read(tree2070):
    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    curve = SXmlCurve(effect.m_doc.root.children[0], "PEP")
    baked = parse_efc(tree2070.read(EFC).decode())[0].curve("PEP")
    assert all(math.isclose(curve.at(x), baked.at(x)) for x in (0.0, 0.5, 1.0))


# The window, offscreen


def test_the_window_edits_an_effect_and_undoes_it(qt, tree117, tmp_path):  # noqa: F811
    window = window_on(tree117, tmp_path)
    assert window.open_path(RDP_PATH) and window.m_effect is not None
    assert window.m_emitters.topLevelItemCount() == 3 and window.m_scene.m_effects[0].file == RDP_PATH
    window.m_emitters.setCurrentItem(window.m_emitters.topLevelItem(0))
    row = next(r for r in window.current_item().rows if r.name == "MaxScale")
    window.m_form.edited.emit(row, "2")
    assert b"<MaxScale>2</>" in window.m_effect.to_bytes() and window.windowTitle().startswith("smoke.rdp *")
    window._redraw()
    assert window.m_scene.m_effects[0].file != RDP_PATH and window.current_item().key == "header"
    window.m_form.edited.emit(next(r for r in window.current_item().rows if r.name == "FrameTime"), "abc")
    assert "no number" in window.statusBar().currentMessage()
    window.tint((1.0, 0.0, 0.0, 1.0))
    window.undo()
    window.undo()
    assert not window.m_effect.changed and window.m_tint_button.isVisibleTo(window)
    window.m_effect.m_undo.mark_clean()
    window.close()


def test_a_curve_drawn_in_the_graph_is_one_undo_step(qt, tree2070, tmp_path):
    window = window_on(tree2070, tmp_path)
    window.open_path(EFC)
    top = window.m_emitters.topLevelItem(0)
    pep = next(top.child(k) for k in range(top.childCount()) if top.child(k).text(0).startswith("PEP"))
    window.m_emitters.setCurrentItem(pep)
    assert window.m_curve.m_model is not None and window.m_curve.m_points
    window.m_curve.points_edited.emit([(0.0, 5.0), (1.0, 95.0)])
    assert b"<PEPSize>2</PEPSize>" in window.m_effect.to_bytes()
    assert window.current_item().key.endswith("PEP")  # the same row stays selected after the tree is made again
    window.undo()
    assert not window.m_effect.changed
    window.close()


def test_an_effect_opened_from_a_model_plays_in_it_and_saves_a_copy(qt, tree117, tmp_path):  # noqa: F811
    from annotoolbox.particleedit.particlewindow import effects_of_model
    from annotoolbox.suite import project

    assert effects_of_model(tree117, "anno117", CFG) == [RDP_PATH]
    window = window_on(tree117, tmp_path)
    window.open_effect("anno117", RDP_PATH, CFG)
    assert window.m_effect.m_model is not None and window.m_effect.m_model.read == SHARED
    window.tint((0.5, 0.5, 0.5, 1.0))
    scene = window.preview_scene()
    assert scene.m_config.endswith("house.cfg") and scene.m_effects[0].file == window.m_effect.preview_path()
    window.write(saving.plan(window.m_effect, saving.COPY))
    saved = projectpart.EDITS.saved_files(project.current(window.m_settings), "anno117")
    assert set(saved) == {"data/base/house/house_smoke.rdp", SHARED + ".xml"} and not window.m_effect.m_undo.dirty
    window.close()


def test_the_play_bar_holds_at_the_end_without_loop(qt, tree1701, tmp_path):
    window = window_on(tree1701, tmp_path)
    window.open_path(EFC1701)
    bar = window.m_play
    assert bar.m_length_s == pytest.approx(1.5)
    bar.m_loop.setChecked(False)
    bar.set_playing(True)
    window.m_view.m_state.time = 4.0
    bar.follow()
    assert (
        not window.m_view.m_playing
        and window.m_view.m_state.time == pytest.approx(1.5)
        and bar.m_slider.value() == 1000
    )
    bar.seek(0.75)
    assert bar.m_slider.value() == 500
    window.close()


def test_an_edit_of_the_placing_block_saves_the_config_with_the_effect(tree117):  # noqa: F811
    effect = CEffectDoc.open(tree117, "anno117", RDP_PATH, CFG)
    block = outline.outline(effect)[-1]
    row = next(r for r in block.rows if r.name == "TimeScale")
    effect.apply(outline.set_row(effect, row, "2"))
    assert effect.model_changed and not effect.changed
    assert effect.m_files.read(SHARED) == effect.m_model.doc.to_bytes()
    plan = saving.plan(effect, saving.REPLACE)
    assert set(plan.files) == {RDP_PATH, SHARED + ".xml"} and b"TimeScale" in plan.files[SHARED + ".xml"]


def test_the_effect_asked_for_last_is_the_one_shown(qt, tree117, tmp_path, monkeypatch):  # noqa: F811
    """Two files opened one after the other: the first one's late result does not replace the second."""
    window = window_on(tree117, tmp_path)
    jobs = []
    monkeypatch.setattr(window.m_background, "run", lambda work, done, is_waiting=False: jobs.append((work, done)))
    assert window.open_path(RDP_PATH) and window.open_path("data/base/fx/empty.rdp")
    (first, show_first), (second, show_second) = jobs
    show_second(second())
    show_first(first())  # the slower one comes in last
    assert window.m_effect.m_path == "data/base/fx/empty.rdp"
    window.close()


def test_the_kinds_to_add_are_the_effects_games_not_the_browsed_ones(qt, tree2070, tree1701, tmp_path):
    window = window_on(tree2070, tmp_path)
    window.open_path(EFC)
    window.set_tree(tree1701)  # another game browsed while the 2070 effect stays open
    donors = window.game_kinds()
    assert list(window._m_donors) == ["anno2070"] and donors and all(tree2070.exists(d.path) for d in donors)
    window.close()


def test_a_save_the_project_cannot_take_is_said_and_stays_unsaved(qt, tree117, tmp_path, monkeypatch):  # noqa: F811
    from PySide6.QtWidgets import QMessageBox

    from annotoolbox.particleedit.saving import SPlan
    from annotoolbox.suite.project import SEditsFolder

    def refuse(*_args, **_kwargs):
        raise PermissionError("no room")

    said = []
    monkeypatch.setattr(SEditsFolder, "save_announced", refuse)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: said.append(text))
    window = window_on(tree117, tmp_path)
    window.open_path(RDP_PATH)
    window.tint((1.0, 0.0, 0.0, 1.0))
    window.write(SPlan("new", {"data/base/fx/mine.rdp": b"x"}))
    assert "no room" in said[0] and window.m_effect.m_undo.dirty
    window.m_effect.m_undo.mark_clean()
    window.close()


def test_the_particle_save_dialog_returns_the_chosen_plan(qt, tree2070):
    from annotoolbox.particleedit.savedialog import CSaveDialog

    effect = CEffectDoc.open(tree2070, "anno2070", EFC)
    loop = {r.name: r for r in outline.outline(effect)[0].rows}["Loop"]
    effect.apply(outline.set_row(effect, loop, "0"))
    dialog = CSaveDialog(effect)
    assert dialog.chosen() == saving.REPLACE and dialog.m_path.text().endswith("_mod.efc")
    dialog._save()
    assert set(dialog.m_result_plan.files) == {EFC} and dialog.m_result_plan.way == saving.REPLACE
