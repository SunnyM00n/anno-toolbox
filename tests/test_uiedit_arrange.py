"""The UI preview's group rules on synthetic data: a stack's order, margin and cross-axis alignment, a background
layout, the loaders that show one child and the radial lists' arc."""

from __future__ import annotations

import math

import pytest
from tests.test_uiedit import SCHEMA, VERSIONS, _instance

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.uiedit import arrange, elements, listlayout, loaders

STACK, ALIGN, BG = 11, 12, 13  # component ids of the instance


def _slot(x: float, y: float, w: float, h: float, **kw) -> arrange.Slot:
    return arrange.Slot(None, (1.0, 0.0, 0.0, 1.0, x, y), (w, h), (x, y, x + w, y + h), True, **kw)


def _rule(**values) -> arrange.Rule:
    overrides = {(STACK, getattr(arrange, k)): v for k, v in values.items()}
    rule = arrange.read_rule({arrange.STACK: STACK}, overrides)
    assert rule is not None
    return rule


def test_a_group_without_stack_or_background_has_no_rule():
    assert arrange.read_rule({}, {}) is None


def test_a_horizontal_stack_aligns_across_by_the_group_or_the_child():
    rule = _rule(STACK_SPACE=10.0)
    slots = [_slot(50, 50, 20, 20), _slot(0, 0, 30, 60), _slot(0, 0, 10, 10, align=arrange.END)]
    arrange.arrange(rule, slots)
    assert [s.rect[0] for s in slots] == [0.0, 30.0, 70.0]  # one after another, 10 apart
    assert [s.rect[1] for s in slots[:2]] == [0.0, 0.0]  # begin: the group's default, the top edge
    assert slots[2].rect[1] == 50.0  # the child's own end: the bottom of the tallest (60)
    rule.align = arrange.MIDDLE
    slots = [_slot(0, 0, 20, 20), _slot(0, 0, 30, 60)]
    arrange.arrange(rule, slots)
    assert slots[0].rect[1] == 20.0 and slots[1].rect[1] == 0.0


def test_a_vertical_stack_has_its_margin_paddings_and_inverted_order():
    rule = _rule(STACK_ORIENTATION=arrange.VERTICAL, STACK_MARGIN=(4.0, 6.0, 0.0, 0.0), STACK_INVERT=True)
    first, second = _slot(0, 0, 10, 10, pad=(0.0, 2.0, 0.0, 3.0)), _slot(0, 0, 20, 10)
    arrange.arrange(rule, [first, second])
    assert second.rect[:2] == (4.0, 6.0)  # inverted: the second child leads; margin left 4, top 6
    assert first.rect[:2] == (4.0, 6.0 + 10.0 + 2.0)  # then the first with its top padding
    hidden = arrange.Slot(None, (1.0, 0.0, 0.0, 1.0, 9.0, 9.0), (5.0, 5.0), (9.0, 9.0, 14.0, 14.0), False)
    arrange.arrange(rule, [hidden, first])
    assert hidden.rect == (9.0, 9.0, 14.0, 14.0)  # a hidden child is neither placed nor counted


def test_a_background_covers_the_content_plus_its_margin():
    rule = arrange.read_rule({arrange.BACKGROUND: BG}, {(BG, arrange.BACKGROUND_MARGIN): (2.0, 3.0, 4.0, 5.0)})
    back = _slot(0, 0, 1, 1)
    back.background = True
    arrange.arrange(rule, [back, _slot(10, 20, 30, 40), _slot(50, 20, 10, 10)])
    assert back.rect == (8.0, 17.0, 8.0 + 56.0, 17.0 + 48.0)
    assert back.size == (56.0, 48.0)


def _loader_child(name: str, entry: int = 0, template: str | None = None) -> elements.Element:
    node = _instance("0f561333", name)
    phoenixedit.set_field(node, "EnumEntry", entry)
    if template:
        phoenixedit.set_raw(node, "GameObjectTemplate", phoenix.ref_bytes("ab6b1e33", template))
    return elements.Element(node, "container")


def test_an_enum_loader_keeps_the_children_of_its_current_value():
    kids = [_loader_child("always"), _loader_child("a", 100), _loader_child("b", 200), _loader_child("b2", 200)]
    names = lambda picked: [k.name for k in picked]  # noqa: E731
    assert names(loaders.chosen("enum", kids, {loaders.CURRENT_ENUM_VALUE: 200}, True)) == ["always", "b", "b2"]
    assert names(loaders.chosen("enum", kids, {}, True)) == ["always", "a"]  # unknown value: the design shows the first
    assert names(loaders.chosen("enum", kids, {}, False)) == ["always"]  # as the game starts: no entry matches
    assert names(loaders.chosen("enum", kids, {loaders.CURRENT_ENUM_VALUE: 300}, False)) == ["always"]


def test_an_object_loader_shows_its_first_classed_child():
    guid = "27f16e62-1ff8-4c02-aa1d-5d60fc751f36"
    kids = [_loader_child("mask"), _loader_child("x", template=guid), _loader_child("y", template=guid)]
    assert [k.name for k in loaders.chosen("object", kids, {}, True)] == ["mask", "x"]
    assert loaders.chosen("object", [], {}, True) == []


def test_radial_lists_place_their_items_on_an_arc():
    half = listlayout.offsets("radial-list", {0x3C1898AF: 0.0, 0x3A606D94: 180.0, 0x37B56040: 100.0}, 3, (1.0, 1.0))
    assert half[0] == pytest.approx((0.0, -100.0)) and half[2] == pytest.approx((0.0, 100.0))  # top to bottom
    assert half[1] == pytest.approx((100.0, 0.0))
    ring = listlayout.offsets("radial-wheel", {}, 4, (1.0, 1.0))  # the wheel's defaults: a full circle, radius 100
    assert ring[1] == pytest.approx((100.0, 0.0)) and math.hypot(*ring[3]) == pytest.approx(100.0)
    assert len({tuple(round(v, 3) for v in p) for p in ring}) == 4  # a full circle leaves its end free


def test_loader_and_radial_widget_types_are_recognised(tmp_path):
    from tests.test_uiedit_compose import _root

    class Files:
        def root(self, guid):
            return None

    root = _root("11111111-1111-1111-1111-111111111111")
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", "c0222ddb-0000-0000-0000-000000000000"))
    assert loaders.loader_kind(Files(), root) == "enum"
    assert listlayout.list_kind(Files(), root) is None
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", "f67963fe-0000-0000-0000-000000000000"))
    assert listlayout.list_kind(Files(), root) == "radial-game" and loaders.loader_kind(Files(), root) is None
    assert SCHEMA and VERSIONS


# placing rules of the preview that need a file: bindings that chain, a picture given a size, editor-hidden layers

CHAIN, SIZED, HIDDEN = (str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, n)) for n in ("c", "s", "h"))


def _files(tmp_path, **named):
    from tests.test_uiedit import GEN, IMAGE, _write, image_file

    from annotoolbox.sources import gametree
    from annotoolbox.uiedit import uifiles

    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    (gen / IMAGE).write_bytes(image_file())
    for key, root in named.items():
        (gen / key).write_bytes(_write("WI", root))
    return uifiles.UiFiles(gametree.loose_tree(tmp_path / "game"))


def _picture(name: str, **fields) -> dict:
    from tests.test_uiedit_compose import _image

    node = _image(name)
    for field, value in fields.items():
        phoenixedit.set_field(node, field, value)
    return node


def test_a_chain_of_bindings_longer_than_three_links_settles(tmp_path):
    from tests.test_uiedit_compose import _root

    root = _root(CHAIN)
    names = "abcdef"
    nodes = [_picture(n) for n in names]
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    for node in nodes:
        wf.add_instance(node)
    wf.add_property("Offset", "vec3", (7.0, 0.0, 0.0))
    position = [phoenixedit.BUILTIN["Position"]]
    for src, dst in reversed(list(zip(nodes, nodes[1:], strict=False))):  # the links in the order that settles last
        wf.bind(wf.end(src, position), wf.end(dst, position))
    wf.bind(wf.own("Offset"), wf.end(nodes[0], position))
    wf.finish("chain")
    from annotoolbox.uiedit import layout

    files = _files(tmp_path, **{CHAIN: root})
    frame = layout.place(CHAIN, files.root(CHAIN), layout.Context(files))
    assert [layout.apply(i.matrix, 0, 0)[0] for i in frame.items if i.kind == "image"] == [7.0] * 6


def test_a_picture_given_a_size_is_scaled_to_it_with_its_pivot(tmp_path):
    from tests.test_uiedit_compose import _root

    root = _root(SIZED)
    picture = _picture("Sized", PivotPoint=(32.0, 16.0, 0.0), Position=(100.0, 100.0, 0.0))  # 64 x 32 picture
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture)
    wf.add_property("Wanted", "vec3", (128.0, 64.0, 0.0))
    wf.bind(wf.own("Wanted"), wf.end(picture, [phoenixedit.BUILTIN["Size"]]))
    wf.finish("sized")
    from annotoolbox.uiedit import layout

    files = _files(tmp_path, **{SIZED: root})
    (item,) = layout.place(SIZED, files.root(SIZED), layout.Context(files)).items
    assert item.size == (64.0, 32.0)  # the picture's own size, scaled by the matrix
    assert layout.bounds(item.matrix, *item.size) == (36.0, 68.0, 164.0, 132.0)  # the pivot went with the scale


def test_layers_the_author_hid_start_hidden_unless_asked(tmp_path):
    from tests.test_uiedit_compose import _root

    root = _root(HIDDEN)
    wf = phoenixedit.WidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(_picture("Shown"))
    wf.add_instance(_picture("Helper", IsHidden=True))
    wf.finish("hidden")
    from annotoolbox.uiedit import layout

    files = _files(tmp_path, **{HIDDEN: root})
    assert len(layout.place(HIDDEN, files.root(HIDDEN), layout.Context(files)).items) == 1
    ctx = layout.Context(files, editor_hidden=True)
    assert len(layout.place(HIDDEN, files.root(HIDDEN), ctx).items) == 2
