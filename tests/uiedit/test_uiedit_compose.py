"""The UI preview composing a scene as the game assembles it, on synthetic files: nested widget files, an instance's
values passed into its widget file and the file's outputs read back, a group sized by its content, a list placing
its item per element, placeholders for missing game values, effects, soft masks, the game's fonts and the
reference picker."""

from __future__ import annotations

import os
import uuid

import numpy as np
import pytest
from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import (
    GEN,
    IMAGE,
    INNER,
    LIST,
    OUTER,
    image_file,
    image_node,
    root_node,
    scene_instance,
    template_container,
)
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import effects, labels, layout, listlayout, placeholders, uifiles, viewmodels

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _images(frame: layout.SFrame) -> list[tuple[float, float]]:
    return [layout.apply(i.matrix, 0, 0) for i in frame.items if i.kind == "image"]


def test_the_scene_composes_nested_files_inputs_outputs_and_a_list(composed):
    samples = viewmodels.CSamples()
    frame = layout.place("composed", composed.root("composed"), layout.CContext(composed, samples))
    first, second, *rows = _images(frame)
    assert first == (110.0, 70.0)  # the scene's place + the instance's + the input passed into the file
    assert second == (10.0 + 64.0, 20.0)  # placed at the output the first file computed (its picture's width)
    assert rows == [(0.0, 300.0), (0.0, 340.0), (0.0, 380.0)]  # one item per element, 32 high and 8 apart
    assert all(i.kind != "image" or i.alpha > 0 for i in frame.items)  # the hidden picture is not drawn
    (text,) = [i for i in frame.items if i.kind == "text"]
    assert text.text == placeholders.text_for("")  # a placeholder for the game's text, kept as generated
    assert any(k in samples.m_generated for k in samples.m_seen)
    samples.m_fill = False
    frame = layout.place("composed", composed.root("composed"), layout.CContext(composed, samples))
    assert not [i for i in frame.items if i.kind == "text" and i.text]


def test_a_group_is_as_large_as_what_it_holds_and_lists_count_their_elements():
    samples = viewmodels.CSamples()
    samples.m_values["f:g/30000001/90000004/2b2357a9"] = "x"
    assert listlayout.element_count(samples, "f:g") == 5
    assert listlayout.element_count(samples, "f:other") == listlayout.PLACEHOLDER_ITEMS
    grid = listlayout.offsets("grid", {listlayout.GRID_COLUMNS: 2, listlayout.GRID_ROWS: 1}, 5, (10.0, 20.0))
    short = {listlayout.GRID_COLUMNS: 2, listlayout.CELL: (5.0,)}  # a cell size a binding left short: the item's
    assert listlayout.offsets("grid", short, 2, (10.0, 20.0)) == [(0.0, 0.0), (10.0, 0.0)]
    assert grid == [(0.0, 0.0), (10.0, 0.0)]  # the visible cells cap the copies


def test_placeholders_follow_the_target_kind():
    from annotoolbox.uiedit import bindings

    end = bindings.SEnd(0, "path", {}, [(1, None)], [0x10000001, 0x20000002])
    visible = bindings.SEnd(1, "path", {}, [(0, "x")], [0x80000007])
    picture = bindings.SEnd(2, "path", {}, [(0, "x")], [0x8000002A])
    binding = bindings.SBinding({}, "0000", [end], [visible])
    describe = lambda e: placeholders.target_kind(e.ids[-1]) if e.ids else ("value", "")  # noqa: E731
    assert placeholders.guess(binding, 0, describe) is True
    binding.targets = [picture]
    assert placeholders.guess(binding, 0, describe).startswith(placeholders.PICTURE)
    assert placeholders.number_for("UpkeepPercent") == 75.0 and placeholders.text_for("Header") == "Lorem ipsum"


def _two_readers(label_first: bool) -> bytes:
    """A scene whose label text and picture visibility both read one game value, in the order given."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    label = scene_instance("41a404e5", "Title", (0.0, 600.0, 0.0))
    phoenixedit.set_field(label, "TextPlane", (300.0, 1.0))
    picture = image_node("Badge")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in (label, picture):
        wf.add_instance(node)
    wf.add_property("Data", "gameobject")
    text = (wf.end(None, [wf.m_unique_ids["Data"], 0x2B2357A9]), wf.end(label, [phoenixedit.BUILTIN["Text"]]))
    shown = (wf.end(None, [wf.m_unique_ids["Data"], 0x2B2357A9]), wf.end(picture, [phoenixedit.BUILTIN["Visible"]]))
    for source, target in (text, shown) if label_first else (shown, text):
        wf.bind(source, target)
    wf.finish("scene")
    return _write("SC", root)


def test_a_made_up_value_does_not_depend_on_which_binding_read_it_first(tmp_path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    for key, data in (("labelfirst", _two_readers(True)), ("picturefirst", _two_readers(False)),
                      (IMAGE, image_file())):  # fmt: skip
        (gen / key).write_bytes(data)
    files = uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))
    for key in ("labelfirst", "picturefirst"):
        samples = viewmodels.CSamples()
        frame = layout.place(key, files.root(key), layout.CContext(files, samples))
        (text,) = [i for i in frame.items if i.kind == "text"]
        assert text.text == placeholders.text_for("")  # not "True", the visibility's placeholder asked for first
        assert [i for i in frame.items if i.kind == "image"]  # and the picture shows: its own placeholder is True
        assert any(k in samples.m_generated for k in samples.m_seen)


def test_effects_follow_the_shader_rules():
    rgba = np.zeros((8, 8, 4), np.uint8)
    rgba[2:6, 2:6] = (200, 100, 50, 255)
    grey = effects.apply(rgba, {"colorize": {"GrayScale": 1.0, "Brightness": 1.0}})
    assert grey[3, 3, 0] == grey[3, 3, 1] == grey[3, 3, 2]
    white = effects.apply(rgba, {"whiten": {"Enable": True}})
    assert tuple(white[3, 3]) == (255, 255, 255, 255)
    shadow = effects.apply(rgba, {"drop_shadow": {"Strength": 2.0, "Direction": 0.0}})
    assert shadow[3, 6, 3] > 0 and shadow[3, 0, 3] == 0  # a shadow on one side of the square, none on the other
    glow = effects.apply(rgba, {"glow": {"OuterGlowColor": (1.0, 1.0, 0.0), "OuterGlowStrength": 1.0, "BlendMode": 2}})
    assert glow[1, 3, 3] > 0 and glow[1, 3, 0] > glow[1, 3, 2]
    step = effects.soft_step(np.array([0.0, 0.1, 0.5]), {"AlphaThreshold": 0.0, "AlphaGradient": 0.2})
    assert step[0] == 0.0 and 0.0 < step[1] < 1.0 and step[2] == 1.0
    assert effects.kind_of({"AlphaThreshold", "AlphaGradient", "Enabled"}) == "soft_mask"


def test_soft_masks_and_effects_reach_the_drawn_picture(composed):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit import canvas

    QApplication.instance() or QApplication([])
    item = layout.SDrawItem("image", layout.IDENTITY, (40.0, 40.0), (255, 255, 255))  # no picture: a flat tile
    mask = layout.SClip(layout.translate(20.0, 0.0), (40.0, 40.0), soft={"AlphaThreshold": 0.0, "AlphaGradient": 0.1})
    item.clips = [mask]
    frame = layout.SFrame((60.0, 40.0), [item])
    img = canvas.render(frame, None, 1.0, background=(0, 0, 0))
    assert img.pixelColor(10, 20).red() == 0 and img.pixelColor(30, 20).red() > 0  # only inside the mask


def test_labels_take_the_fonts_and_the_overflow_modes(composed):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit import fonts

    QApplication.instance() or QApplication([])
    style = labels.STextStyle(size=20.0, wrap=True)
    laid = fonts.lay_out("one two three four five six", style, 80.0)
    # the game: the first line is the font's own height, each further one a step of leading x size
    assert len(laid.lines) > 1 and laid.first > 0 and laid.line_height == pytest.approx(24.0)
    assert laid.height_px == pytest.approx(laid.first + (len(laid.lines) - 1) * 24.0)
    word = fonts.lay_out("unbelievablylongwordwithoutanyspaces", style, 80.0)  # a word wider than the plane is broken
    assert (
        len(word.lines) > 1 and "".join(word.lines) == "unbelievablylongwordwithoutanyspaces" and word.width_px <= 80.0
    )
    fits = laid.first + 24.0 + 1.0  # a plane two lines high: the vertical overflow modes keep the lines that fit
    cut = fonts.lay_out("one two three four five six", labels.STextStyle(size=20.0, wrap=True, vcut="line"), 80.0, fits)
    assert cut.lines == laid.lines[:2] and cut.height_px == pytest.approx(laid.first + 24.0)
    style = labels.STextStyle(size=20.0, cut="ellipsis")
    assert fonts.lay_out("a very long line of text", style, 60.0).lines[0].endswith(fonts.ELLIPSIS)
    assert fonts.CGameFonts(composed).family(None).family  # no font asset: a stand-in face


def test_the_reference_picker_lists_files_of_a_kind(composed):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.uiedit import pickers

    QApplication.instance() or QApplication([])
    assert pickers.kind_of_field("Image") == ("IM",)
    keys = [k for _t, k in pickers.entries(composed, ("WI",))]
    assert set(keys) == {INNER, OUTER, LIST}
    assert [k for _t, k in pickers.entries(composed, ("WI",), OUTER[:8])] == [OUTER]
    assert pickers.read_mime(pickers.palette_mime("instance", "image")) == ("instance", "image")


def test_the_index_cache_is_kept_whole_and_read_only_for_its_stamp(composed, tmp_path):
    composed.m_cache, composed.m_tree.m_stamp = tmp_path / "cache", "s1"
    assert composed._read_cache("x.json") is None
    composed._write_cache("x.json", {"a": 1})
    assert composed._read_cache("x.json")["a"] == 1
    assert [p.name for p in (tmp_path / "cache").iterdir()] == ["x.json"]  # written atomically: no part file left
    composed.m_tree.m_stamp = "s2"
    assert composed._read_cache("x.json") is None  # made for another tree


def _nested(tmp_path, depth: int) -> uifiles.CUiFiles:
    """A scene placing a chain of ``depth`` distinct widget files, the last one holding a picture."""
    keys = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"nested-{i}")) for i in range(depth)]
    out = {IMAGE: image_file()}
    for i, key in enumerate(keys):
        root = root_node(key)
        wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
        wf.add_instance(
            template_container(f"in-{i}", keys[i + 1]) if i + 1 < depth else image_node("deepest", (5.0, 7.0, 0.0))
        )
        wf.finish(f"nested-{i}")
        out[key] = _write("WI", root)
    scene = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_objects(scene, "WidgetInstances", [template_container("top", keys[0])])
    out["nested"] = _write("SC", scene)
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    for key, data in out.items():
        (gen / key).write_bytes(data)
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))


def test_files_nested_as_deep_as_the_games_are_placed_whole(tmp_path):
    """117's scenes nest up to 24 distinct widget files (statisticsscene); the preview cut them at 14."""
    files = _nested(tmp_path, 24)
    frame = layout.place("nested", files.root("nested"), layout.CContext(files, viewmodels.CSamples()))
    assert _images(frame) == [(5.0, 7.0)]


def _element_reader(tmp_path) -> uifiles.CUiFiles:
    """A list whose item places a widget file given its element: the file shows a field of it as its label's text."""
    shows = str(uuid.uuid5(uuid.NAMESPACE_URL, "shows-element"))
    root = root_node(shows)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    label = wf.add_instance(scene_instance("41a404e5", "Name"))
    phoenixedit.set_field(label, "TextPlane", (100.0, 1.0))
    wf.add_property("Data", "gameobject")
    wf.bind(wf.end(None, [wf.m_unique_ids["Data"], 0x2B2357A9]), wf.end(label, [phoenixedit.BUILTIN["Text"]]))
    wf.finish("shows-element")
    rows = str(uuid.uuid5(uuid.NAMESPACE_URL, "element-rows"))
    lroot = root_node(rows)
    phoenixedit.set_raw(lroot, "WidgetType", phoenix.ref_bytes("ab6b1e33", "dbcf9d78-ce36-4fd1-aaa9-acba02520297"))
    lw = phoenixedit.CWidgetFile(SCHEMA, lroot, versions=VERSIONS)
    item = lw.add_instance(template_container("Row", shows))
    data = phoenixedit.unique_id(0x10000000, "Data")
    lw.bind(lw.end(4, [0x30000002]), lw.end(item, [data]))  # the element the copy stands for, into its file
    lw.finish("element-rows")
    scene = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_objects(scene, "WidgetInstances", [template_container("List", rows)])
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    for key, blob in ((shows, _write("WI", root)), (rows, _write("WI", lroot)), ("rows", _write("SC", scene))):
        (gen / key).write_bytes(blob)
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game")), shows


def test_list_elements_holding_no_given_value_share_what_they_pass_into_their_files(tmp_path, monkeypatch):
    from annotoolbox.uiedit import scope

    files, shows = _element_reader(tmp_path)
    made: list[str] = []
    original = scope.CScope.__init__
    monkeypatch.setattr(
        scope.CScope, "__init__", lambda self, key, *a, **k: (made.append(key), original(self, key, *a, **k))[1]
    )
    samples = viewmodels.CSamples()
    frame = layout.place("rows", files.root("rows"), layout.CContext(files, samples))
    assert [i.text for i in frame.items if i.kind == "text"] == [placeholders.text_for("")] * 3
    assert made.count(shows) == 1  # one evaluation for the three elements: each reads made-up values only
    first = next(k for k in samples.m_seen if k.endswith("/2b2357a9"))
    samples.set(first.replace("90000000", "90000001"), "given")  # a value under the second element separates it
    frame = layout.place("rows", files.root("rows"), layout.CContext(files, samples))
    assert [i.text for i in frame.items if i.kind == "text"][1] == "given"


def test_a_list_is_measured_with_the_elements_it_is_drawn_with(composed):
    """The size a container reports for a list file comes from that instance's elements (here five), not from the
    three placeholder elements a list without an instance would get."""
    from annotoolbox.uiedit import elements

    samples = viewmodels.CSamples()
    scene = composed.root("composed")
    rows = next(e for e in elements.tree(scene).walk() if e.name == "Rows")
    samples.set(viewmodels.CSamples.key(f"composed:{rows.guid}", [0x30000001, listlayout.ELEMENT + 4]), 1.0)
    frame = layout.place("composed", scene, layout.CContext(composed, samples))
    assert len([i for i in frame.items if i.kind == "image" and layout.apply(i.matrix, 0, 0)[1] >= 300.0]) == 5
    assert frame.boxes[id(rows.node)].size == (64.0, 5 * 32.0 + 4 * 8.0)
