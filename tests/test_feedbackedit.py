"""The object builder's feedback timeline (``models.feedbackedit``, ``objbuild.feedbackline``): a loop's elements as
bars, their lengths, places, new and removed elements as edits of the ``.fc``, saved as the other edits (a whole file
or a ``.fc.xml`` patch the loader reproduction applies); with ``ANNO117_DIR`` an installed file's loops."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.models import cfgdoc, cfgpatch, feedbackedit
from annotoolbox.modloader.engine import run_text
from annotoolbox.modloader.patchdoc import PatchDoc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NL = "\r\n"


def _x(tag: str, *inner: str, text: str = "") -> str:
    return f"<{tag}>{text}{NL if inner else ''}{''.join(inner)}</{tag}>{NL}"


def _element(kind: int, **fields: str) -> str:
    return _x(
        "i", _x("hasValue", text="1"), _x("elementType", text=str(kind)), *(_x(k, text=v) for k, v in fields.items())
    )


WAIT = _element(2, MinTime="1000", MaxTime="3000")
PLAY = _element(1, m_IdleSequenceID="9", MinPlayCount="1", MinPlayTime="2000")
WALK = _element(0, WalkSequence="7", SpeedFactorF="0.500000", TargetDummyId="2", WalkFromCurrentPosition="1")


def _loop(n: int, *elements: str) -> str:
    state = _x("DefaultState", _x("DummyId", text="1"), _x("Visible", text="1"))
    return _x(f"Loop{n}", _x("hasValue", text="1"), state, _x("ElementContainer", _x("Elements", *elements)))


def _dummy(name: str, ident: int, x: float) -> str:
    position = _x("Position", _x("x", text=str(x)), _x("y", text="0"), _x("z", text="0"))
    return _x("i", _x("Name", text=name), position, _x("Id", text=str(ident)))


FC = (
    _x("DummyRoot", _x("Groups", _x("i", _x("Name", text="g"), _x("Dummies", _dummy("a", 1, 0), _dummy("b", 2, 1)))))
    + _x("FeedbackDefinition", _x("FeedbackConfigs", _x("i", _x("MainObject", text="0"),
         _x("FeedbackLoops", _x("k", text="1"), _x("v", text="0")),
         _x("SequenceDefinitions", _x("i", _loop(0, WAIT), _loop(1, WALK, PLAY, WAIT)))))))  # fmt: skip


def _doc() -> cfgdoc.Doc:
    doc = cfgdoc.Doc.read(FC.encode(), "data/x/x.fc")
    assert doc.exact
    return doc


def test_a_loops_elements_are_bars_of_the_time_a_play_gives_them():
    doc = _doc()
    (item,) = feedbackedit.items(doc)
    assert item.states == {1: 0}
    bars = feedbackedit.bars(doc, item, 1, 1)
    assert [b.kind for b in bars] == [0, 1, 2] and all(b.played for b in bars)
    assert bars[0].end - bars[0].start == pytest.approx(2.0)  # one unit at 0.5 a second
    assert bars[1].end - bars[1].start == pytest.approx(2.0) and bars[1].start == bars[0].end
    assert 1.0 <= bars[2].end - bars[2].start <= 3.0
    assert feedbackedit.bars(doc, item, 1, 2) == [] and feedbackedit.bars(doc, item, 5, 1) == []
    path = feedbackedit.part_path(doc, bars[1].node)
    assert path.endswith("Elements#0/i#1") and doc.find(path).node is bars[1].node


def test_lengths_places_new_and_removed_elements_are_edits():
    doc = _doc()
    item = feedbackedit.items(doc)[0]
    walk, play, wait = feedbackedit.bars(doc, item, 1, 1)
    for e in feedbackedit.retimed(play, 3.5):
        e.do()
    assert play.node.value("MinPlayTime") == "3500"
    for e in feedbackedit.retimed(wait, wait.end - wait.start + 0.5):
        e.do()
    assert (wait.node.value("MinTime"), wait.node.value("MaxTime")) == ("1500", "3500")
    for e in feedbackedit.retimed(walk, 4.0):
        e.do()
    assert walk.node.value("SpeedFactorF") == "0.250000"  # twice as long: half the pace
    assert feedbackedit.retimed(feedbackedit.Bar(walk.node, walk.parent, 6, 0, 1, True), 2.0) == []
    edit = feedbackedit.moved(wait, 0)
    edit.do()
    assert [feedbackedit.element_kind(n) for n in walk.parent.findall("i")] == [2, 0, 1]
    edit.undo()
    new = feedbackedit.new_element(doc, 2)
    assert new is not None and new is not wait.node and new.value("MinTime") == "1000"  # a copy of the file's first
    feedbackedit.added(walk.parent, walk.node, new, 2).do()
    assert walk.parent.findall("i")[1] is new
    assert feedbackedit.new_element(doc, 6).value("FadingMode") == "1"  # none in the file: the template
    feedbackedit.removed(feedbackedit.bars(doc, item, 1, 1)[0]).do()
    assert len(walk.parent.findall("i")) == 3 and cfgdoc.Doc.read(doc.to_bytes()).exact


def test_an_edited_feedback_file_saves_as_a_patch_the_loader_applies():
    doc = _doc()
    item = feedbackedit.items(doc)[0]
    walk, play, wait = feedbackedit.bars(doc, item, 1, 1)
    for e in [*feedbackedit.retimed(play, 1.25), feedbackedit.removed(walk)]:
        e.do()
    patch = cfgpatch.render_patch(FC.encode(), doc)
    assert patch.count >= 2 and not patch.problems
    loader = PatchDoc.of(*(cfgpatch.as_element(c) for c in cfgdoc.Doc.read(FC.encode()).root.children))
    events = run_text(loader, patch.text)
    assert not [e for e in events if "No matching" in e.line()], events
    want = b"".join(ET.tostring(cfgpatch.as_element(c)) for c in doc.root.children)
    assert b"".join(ET.tostring(r) for r in loader.roots) == want  # the mod loader's reproduction gives the edit
    rebuilt, warnings = cfgpatch.apply_patch(cfgdoc.Doc.read(FC.encode(), "data/x/x.fc"), patch.text)
    assert not warnings and rebuilt.to_bytes() == doc.to_bytes()


def test_the_window_edits_the_loops(qt_app):
    from annotoolbox.objbuild.feedbackline import FeedbackLine

    line = FeedbackLine()
    made = []
    line.edits.connect(made.append)
    doc = _doc()
    line.show_doc(doc)
    assert line.item.count() == 1 and line.state.currentData() == 1 and len(line.view.bars) == 3
    play = line.view.bars[1]
    line.view.retime.emit(play, 4.0)
    assert len(made) == 1
    for e in made[-1]:
        e.do()
    line.refresh()
    assert line.view.bars[1].end - line.view.bars[1].start == pytest.approx(4.0)
    line.view.current = line.view.bars[2]
    line.later.click()
    assert made[-1] is not None and len(made) == 1  # the last cannot go later
    line.earlier.click()
    assert len(made) == 2
    line.kind.setCurrentIndex(line.kind.findData(2))
    line.add.click()
    assert len(made) == 3
    line.close()


@pytest.fixture(scope="module")
def qt_app():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_an_installed_games_loops_show_as_bars():
    from annotoolbox.sources.gametree import open_game

    folder = os.environ.get(INSTALL_VARS["anno117"])
    if not folder or not Path(folder).is_dir():
        pytest.skip("needs ANNO117_DIR")
    tree = open_game("anno117", Path(folder))
    shown = 0
    for path in [p for p in tree.paths if p.endswith(".fc")][:40]:
        doc = cfgdoc.Doc.read(tree.read(path), path)
        for item in feedbackedit.items(doc):
            for state in item.states:
                for loop in range(3):
                    bars = feedbackedit.bars(doc, item, state, loop)
                    assert all(b.end > b.start for b in bars), path
                    shown += len(bars)
    assert shown > 100


def test_the_object_builder_applies_the_timelines_edits_to_the_feedback_file(qt_app, tmp_path):
    from annotoolbox.objbuild.objwindow import ObjectWindow
    from annotoolbox.sources import gametree
    from annotoolbox.suite.settings import Settings

    folder = tmp_path / "117" / "data" / "x"
    folder.mkdir(parents=True)
    (folder / "x.fc").write_bytes(FC.encode())
    tree = gametree.loose_tree(tmp_path / "117", "Anno 117")
    tree.game = "anno117"
    window = ObjectWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(tree)
    assert window.open_path("data/x/x.fc") and len(window.feedback.view.bars) == 3
    window.feedback.view.retime.emit(window.feedback.view.bars[1], 5.0)
    assert window.obj.dirty() == ["fc"] and b"<MinPlayTime>5000</MinPlayTime>" in window.obj.doc("fc").to_bytes()
    assert window.feedback.view.bars[1].end - window.feedback.view.bars[1].start == pytest.approx(5.0)
    window.undo()
    assert not window.obj.dirty()
    window.obj.undo.mark_clean()
    window.close()
