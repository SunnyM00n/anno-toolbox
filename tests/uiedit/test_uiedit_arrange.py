"""The UI preview's group rules on synthetic data: a stack's order, margin and cross-axis alignment, a background
layout, the loaders that show one child and the radial lists' arc."""

from __future__ import annotations

import math

import pytest
from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import arrange_files as _files
from tests.uiedit.uieditfakes import picture_node, scene_instance

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.uiedit import arrange, elements, listlayout, loaders

STACK, ALIGN, BG = 11, 12, 13  # component ids of the instance


def _slot(x: float, y: float, w: float, h: float, **kw) -> arrange.SSlot:
    return arrange.SSlot(None, (1.0, 0.0, 0.0, 1.0, x, y), (w, h), (x, y, x + w, y + h), True, **kw)


def _rule(**values) -> arrange.SRule:
    overrides = {(STACK, getattr(arrange, k)): v for k, v in values.items()}
    rule = arrange.read_rule({arrange.STACK: STACK}, overrides)
    assert rule is not None
    return rule


def test_a_group_without_stack_or_background_has_no_rule():
    assert arrange.read_rule({}, {}) is None


def test_a_stack_lays_the_last_child_of_the_file_out_first():
    """The game's dump of the options scene (2026-11-16): "Buttons", Middle, 32 apart: ConfirmBtn (the file's second
    child) at 0, ResetBtn at 316 + 32."""
    rule = _rule(STACK_SPACE=32.0)
    reset, confirm = _slot(158, 0, 316, 48), _slot(158, 0, 316, 48)
    arrange.arrange(rule, [reset, confirm])
    assert (confirm.rect[0], reset.rect[0]) == (0.0, 348.0)
    rule = _rule(STACK_SPACE=32.0, STACK_INVERT=True)  # the stored InvertOrder was never true in the dumps: unseen
    reset, confirm = _slot(0, 0, 316, 48), _slot(0, 0, 316, 48)
    arrange.arrange(rule, [reset, confirm])
    assert (reset.rect[0], confirm.rect[0]) == (0.0, 348.0)


def test_a_hidden_child_takes_no_room_in_a_stack():
    """ "ResetBtnGroup" (options scene): the file lists DecoRight, ResetBtn, DecoLeft (hidden); 8 apart."""
    rule = _rule(STACK_SPACE=8.0)
    right, button = _slot(0, 0, 48, 48), _slot(0, 0, 260, 48)
    left = arrange.SSlot(None, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (48.0, 48.0), (0.0, 0.0, 48.0, 48.0), False)
    arrange.arrange(rule, [right, button, left])
    assert (button.rect[0], right.rect[0]) == (0.0, 268.0)


def test_a_hidden_child_that_counts_keeps_its_room_in_a_stack():
    """ "DividerStackingGroup" (trade route scene): its last child "ColumnDivider_01" is hidden but counts (2 wide), and
    the first padded divider starts at 2 + 58 = 60; 16 of 22 such shown stacks fit without it, 23 of 23 with it."""
    rule = _rule()
    group = _slot(0, 0, 2, 140, pad=(58.0, 0.0, 0.0, 0.0))
    hidden = arrange.SSlot(
        None, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (2.0, 140.0), (0.0, 0.0, 2.0, 140.0), False, holds=True
    )
    arrange.arrange(rule, [group, hidden])
    assert (hidden.rect[0], group.rect[0]) == (0.0, 60.0)
    plain = arrange.SSlot(
        None, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (2.0, 140.0), (0.0, 0.0, 2.0, 140.0), False, holds=False
    )
    group = _slot(0, 0, 2, 140, pad=(58.0, 0.0, 0.0, 0.0))
    arrange.arrange(rule, [group, plain])  # counts nothing (ConsiderSize off): no room
    assert group.rect[0] == 58.0


def test_a_stack_aligns_across_with_the_paddings_inside_the_widest_child():
    """ "Header_Button" (omresidence scene): Middle, children 28 high with a top padding of 6 in a box 52 high sit at
    15, as (52 - 34) / 2 + 6; the margin moves no child."""
    rule = _rule(STACK_ALIGN=arrange.MIDDLE, STACK_MARGIN=(10.0, 4.0, 0.0, 0.0))
    close, headlines, info = _slot(0, 0, 28, 28, pad=(0.0, 6.0, 0.0, 0.0)), _slot(0, 0, 214, 52), _slot(0, 0, 28, 28)
    close.pad = info.pad = (0.0, 6.0, 0.0, 0.0)
    arrange.arrange(rule, [close, headlines, info])
    assert (close.rect[1], headlines.rect[1], info.rect[1]) == (15.0, 0.0, 15.0)
    assert info.rect[0] == 0.0 and headlines.rect[0] == 28.0 and close.rect[0] == 242.0


def test_a_stack_aligns_across_by_the_group_or_the_child():
    rule = _rule(STACK_SPACE=10.0)
    slots = [_slot(50, 50, 20, 20), _slot(0, 0, 30, 60), _slot(0, 0, 10, 10, align=arrange.END)]
    arrange.arrange(rule, slots)
    assert [s.rect[0] for s in slots] == [60.0, 20.0, 0.0]  # the last child first, 10 apart
    assert [s.rect[1] for s in slots[:2]] == [0.0, 0.0]  # begin: the group's default, the top edge
    assert slots[2].rect[1] == 50.0  # the child's own end: the bottom of the tallest (60)
    rule.align = arrange.MIDDLE
    slots = [_slot(0, 0, 20, 20), _slot(0, 0, 30, 60)]
    arrange.arrange(rule, slots)
    assert slots[0].rect[1] == 20.0 and slots[1].rect[1] == 0.0


def test_a_vertical_stack_has_its_paddings_and_the_margin_moves_nothing():
    rule = _rule(STACK_ORIENTATION=arrange.VERTICAL, STACK_MARGIN=(4.0, 6.0, 0.0, 0.0))
    first, second = _slot(0, 0, 10, 10, pad=(0.0, 2.0, 0.0, 3.0)), _slot(0, 0, 20, 10)
    arrange.arrange(rule, [first, second])
    assert second.rect[:2] == (0.0, 0.0)  # the last child leads, at the group's origin
    assert first.rect[:2] == (0.0, 10.0 + 2.0)  # then the first with its top padding
    hidden = arrange.SSlot(None, (1.0, 0.0, 0.0, 1.0, 9.0, 9.0), (5.0, 5.0), (9.0, 9.0, 14.0, 14.0), False)
    arrange.arrange(rule, [hidden, first])
    assert hidden.rect == (9.0, 9.0, 14.0, 14.0)  # a hidden child is neither placed nor counted


def test_a_containers_size_is_the_union_of_what_counts_and_a_stacks_margin_grows_it():
    """ "Deco_GoldenS1" (the game's dump): strokes from -187 to 0 and 0 to 187, 6 high, make a box 374 by 6."""
    from annotoolbox.uiedit import layout

    assert layout.union_size([(0.0, 0.0, 187.0, 6.0), (-187.0, 0.0, 0.0, 6.0)]) == (374.0, 6.0)
    assert layout.union_size([(10.0, 5.0, 30.0, 25.0)]) == (20.0, 20.0)  # not the edge from the origin (30, 25)
    assert layout.union_size([]) == (0.0, 0.0)


def test_a_background_covers_the_content_plus_its_margin():
    rule = arrange.read_rule({arrange.BACKGROUND: BG}, {(BG, arrange.BACKGROUND_MARGIN): (2.0, 3.0, 4.0, 5.0)})
    back = _slot(0, 0, 1, 1)
    back.background = True
    arrange.arrange(rule, [back, _slot(10, 20, 30, 40), _slot(50, 20, 10, 10)])
    assert back.rect == (8.0, 17.0, 8.0 + 56.0, 17.0 + 48.0)
    assert back.size == (56.0, 48.0)


def test_a_background_covers_a_hidden_layer_that_counts():
    """ "Group_BG" (the HUD archive's quest tracker, dump 2026-11-23 22:12): the hidden "TEST_Debug_Holder" (230 x 0,
    counting) widens the background to 230 where the shown content is 143.6 wide."""
    rule = arrange.read_rule({arrange.BACKGROUND: BG}, {})
    back = _slot(0, 0, 1, 1)
    back.background = True
    holder = arrange.SSlot(
        None, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (230.0, 0.0), (0.0, 0.0, 230.0, 0.0), False, holds=True
    )
    arrange.arrange(rule, [back, holder, _slot(0, -9.6, 143.6, 19.2)])
    assert back.rect == (0.0, -9.6, 230.0, 9.6)
    plain = arrange.SSlot(
        None, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), (230.0, 0.0), (0.0, 0.0, 230.0, 0.0), False, holds=False
    )
    arrange.arrange(rule, [back, plain, _slot(0, -9.6, 143.6, 19.2)])
    assert back.size == (143.6, 19.2)  # ConsiderSize off: not covered


HOLDER, HOLDING_GROUP = (str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, n)) for n in ("holder", "hg"))


def _holder_file(considered: bool) -> dict:
    """A shown 64 x 32 picture and a hidden one at (100, 50) (the info tip areas' "LayoutArea")."""
    from tests.uiedit.uieditfakes import root_node

    root = root_node(HOLDER)
    hidden = picture_node("LayoutArea", Position=(100.0, 50.0, 0.0), Visible=False, ConsiderSize=considered)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture_node("Shown"))
    wf.add_instance(hidden)
    wf.finish("holder")
    return root


@pytest.mark.parametrize(("considered", "size"), [(True, (164.0, 82.0)), (False, (64.0, 32.0))])
def test_a_containers_size_keeps_a_hidden_child_that_counts(tmp_path, considered, size):
    """The info tip areas (``InfoTipAreaWidget``, dumps of 2026-11-23): the container is as large as its hidden
    "LayoutArea"; over the newest dump of each of 151 scenes, where a hidden child decides, 1,798 containers and groups
    keep it against 86 that do not."""
    from annotoolbox.uiedit import layout

    files = _files(tmp_path, **{HOLDER: _holder_file(considered)})
    placer = layout.CPlacer(layout.CContext(files), layout.SFrame((1920.0, 1080.0)))
    assert placer.measure(HOLDER, files.root(HOLDER), {})[0] == size


def test_a_containers_box_is_the_union_from_its_smallest_corner_not_from_the_origin(tmp_path):
    """Over the newest dump of each of 151 scenes, 1,601 containers and groups whose children lie away from their
    origin have the union's box, none the box from the origin."""
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    root = root_node(HOLDER)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture_node("Away", Position=(10.0, 20.0, 0.0)))
    wf.finish("away")
    files = _files(tmp_path, **{HOLDER: root})
    placer = layout.CPlacer(layout.CContext(files), layout.SFrame((1920.0, 1080.0)))
    assert placer.measure(HOLDER, files.root(HOLDER), {})[0] == (64.0, 32.0)
    assert placer.start_of(HOLDER, files.root(HOLDER), {}) == (10.0, 20.0)


def test_a_groups_size_keeps_a_hidden_child_that_counts(tmp_path):
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    root = root_node(HOLDING_GROUP)
    group_guid = __import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "hg-template")
    template = phoenixedit.new_object(SCHEMA, "2f208351", guid=group_guid.bytes_le, versions=VERSIONS)
    hidden = picture_node("Hidden", Position=(100.0, 0.0, 0.0), Visible=False)
    phoenixedit.set_objects(template, "WidgetInstances", [picture_node("Shown", Position=(10.0, 0.0, 0.0)), hidden])
    phoenixedit.set_objects(root, "Groups", [template])
    group = scene_instance("fa702d0c", "Group")
    phoenixedit.set_raw(group, "GroupTemplate", phoenix.ref_bytes("2f208351", str(group_guid)))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(group)
    wf.finish("group")
    files = _files(tmp_path, **{HOLDING_GROUP: root})
    frame = layout.place(HOLDING_GROUP, files.root(HOLDING_GROUP), layout.CContext(files))
    (box,) = [frame.boxes[id(e.node)] for e in elements.tree(files.root(HOLDING_GROUP)).walk() if e.kind == "group"]
    assert box.size == (154.0, 32.0)
    assert layout.bounds(box.matrix, *box.size) == (10.0, 0.0, 164.0, 32.0)  # from its smallest corner


def test_a_lists_measure_starts_at_its_first_element(tmp_path):
    """Every copy of a list's item keeps its box, so the union from the smallest corner starts at the first one."""
    from tests.uiedit.uieditfakes import LIST, image_node, root_node

    from annotoolbox.uiedit import layout

    root = root_node(LIST)
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", "dbcf9d78-ce36-4fd1-aaa9-acba02520297"))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(image_node("Item"))
    wf.finish("list")
    files = _files(tmp_path, **{LIST: root})
    placer = layout.CPlacer(layout.CContext(files), layout.SFrame((1920.0, 1080.0)))
    size = placer.measure(LIST, files.root(LIST), {})[0]
    assert placer.start_of(LIST, files.root(LIST), {}) == (0.0, 0.0)
    assert size[0] > 64.0 or size[1] > 32.0  # more than one element


ONE_AXIS = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "one-axis"))


def test_a_binding_that_writes_one_axis_of_a_pictures_size_keeps_the_other_as_stored(tmp_path):
    """The buttons' "Selected_Gold" (dumps of 2026-11-23): its binding writes the width (54), the height stays the
    stored 3 (a 64 x 16 picture scaled 0.1875), not 0."""
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    root = root_node(ONE_AXIS)
    picture = picture_node("Bar", Scale=(1.0, 0.5, 1.0))  # 64 x 32 scaled to 64 x 16
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture)
    wf.add_property("Width", "float", 100.0)
    wf.bind(wf.own("Width"), wf.end(picture, [phoenixedit.BUILTIN["Size"]], 2))
    wf.finish("one-axis")
    files = _files(tmp_path, **{ONE_AXIS: root})
    (item,) = layout.place(ONE_AXIS, files.root(ONE_AXIS), layout.CContext(files)).items
    assert layout.bounds(item.matrix, *item.size) == (0.0, 0.0, 100.0, 16.0)


VIDEO_FILE, VIDEO_ASSET = (str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, n)) for n in ("vf", "va"))


def test_a_video_is_as_large_as_its_assets_resolution(tmp_path):
    """The background video scene (dump 2026-11-23): "Video" scaled (12, 6.75) is 1920 x 1080, as its asset's
    Resolution is 160 x 160 (every video asset of 117 stores that), whatever the frames' size."""
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    root = root_node(VIDEO_FILE)
    video = scene_instance("8eeb57e6", "Video")
    phoenixedit.set_field(video, "Scale", (12.0, 6.75, 1.0))
    phoenixedit.set_raw(video, "Video", phoenix.ref_bytes("d6815b4f", VIDEO_ASSET))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(video)
    wf.finish("video")
    asset = phoenixedit.new_object(SCHEMA, "d6815b4f", guid=__import__("uuid").UUID(VIDEO_ASSET).bytes_le, root=True,
                                   versions=VERSIONS)  # fmt: skip
    phoenixedit.set_field(asset, "Resolution", (320.0, 160.0))  # not the one every 117 asset has: it is read
    files = _files(tmp_path, **{VIDEO_FILE: root, VIDEO_ASSET: asset})
    (item,) = layout.place(VIDEO_FILE, files.root(VIDEO_FILE), layout.CContext(files)).items
    assert layout.bounds(item.matrix, *item.size) == (0.0, 0.0, 3840.0, 1080.0)


def _loader_child(name: str, entry: int = 0, template: str | None = None) -> elements.SElement:
    node = scene_instance("0f561333", name)
    phoenixedit.set_field(node, "EnumEntry", entry)
    if template:
        phoenixedit.set_raw(node, "GameObjectTemplate", phoenix.ref_bytes("ab6b1e33", template))
    return elements.SElement(node, "container")


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
    from tests.uiedit.uieditfakes import root_node

    class FakeUiFiles:
        def root(self, guid):
            return None

    root = root_node("11111111-1111-1111-1111-111111111111")
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", "c0222ddb-0000-0000-0000-000000000000"))
    assert loaders.loader_kind(FakeUiFiles(), root) == "enum"
    assert listlayout.list_kind(FakeUiFiles(), root) is None
    phoenixedit.set_raw(root, "WidgetType", phoenix.ref_bytes("ab6b1e33", "f67963fe-0000-0000-0000-000000000000"))
    assert (
        listlayout.list_kind(FakeUiFiles(), root) == "radial-game" and loaders.loader_kind(FakeUiFiles(), root) is None
    )
    assert SCHEMA and VERSIONS


# placing rules of the preview that need a file: bindings that chain, a picture given a size, editor-hidden layers

CHAIN, SIZED, HIDDEN = (str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, n)) for n in ("c", "s", "h"))


def test_a_chain_of_bindings_longer_than_three_links_settles(tmp_path):
    from tests.uiedit.uieditfakes import root_node

    root = root_node(CHAIN)
    names = "abcdef"
    nodes = [picture_node(n) for n in names]
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
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
    frame = layout.place(CHAIN, files.root(CHAIN), layout.CContext(files))
    assert [layout.apply(i.matrix, 0, 0)[0] for i in frame.items if i.kind == "image"] == [7.0] * 6


def test_a_picture_given_a_size_is_scaled_to_it_with_its_pivot(tmp_path):
    from tests.uiedit.uieditfakes import root_node

    root = root_node(SIZED)
    picture = picture_node("Sized", PivotPoint=(32.0, 16.0, 0.0), Position=(100.0, 100.0, 0.0))  # 64 x 32 picture
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture)
    wf.add_property("Wanted", "vec3", (128.0, 64.0, 0.0))
    wf.bind(wf.own("Wanted"), wf.end(picture, [phoenixedit.BUILTIN["Size"]]))
    wf.finish("sized")
    from annotoolbox.uiedit import layout

    files = _files(tmp_path, **{SIZED: root})
    (item,) = layout.place(SIZED, files.root(SIZED), layout.CContext(files)).items
    assert item.size == (64.0, 32.0)  # the picture's own size, scaled by the matrix
    assert layout.bounds(item.matrix, *item.size) == (36.0, 68.0, 164.0, 132.0)  # the pivot went with the scale


def test_layers_the_author_hid_start_hidden_unless_asked(tmp_path):
    from tests.uiedit.uieditfakes import root_node

    root = root_node(HIDDEN)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(picture_node("Shown"))
    wf.add_instance(picture_node("Helper", IsHidden=True))
    wf.finish("hidden")
    from annotoolbox.uiedit import layout

    files = _files(tmp_path, **{HIDDEN: root})
    assert len(layout.place(HIDDEN, files.root(HIDDEN), layout.CContext(files)).items) == 1
    ctx = layout.CContext(files, m_editor_hidden=True)
    assert len(layout.place(HIDDEN, files.root(HIDDEN), ctx).items) == 2


def test_a_centred_label_sized_by_its_text_has_its_box_at_the_text(tmp_path):
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    key = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "label-box"))
    root = root_node(key)
    label = scene_instance("41a404e5", "Centred", (10.0, 0.0, 0.0))
    for field, value in (("Text", "ab"), ("TextPlane", (100.0, 80.0)), ("AlignmentX", 1), ("HorizontalSizing", 1)):
        phoenixedit.set_field(label, field, value)
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(label)
    wf.finish("label-box")
    files = _files(tmp_path, **{key: root})
    ctx = layout.CContext(files, m_measure=lambda text, style, width: (24.0, 30.0))
    frame = layout.place(key, files.root(key), ctx)
    (box,) = frame.boxes.values()
    assert layout.bounds(box.matrix, *box.size) == (48.0, 0.0, 72.0, 30.0)  # 10 + (100 - 24) / 2: the text's place
    (text,) = [i for i in frame.items if i.kind == "text"]
    assert layout.apply(text.matrix, 0, 0) == (10.0, 0.0)  # the text is still drawn in its plane


SIZE_READ, TIP_FILE, TIP_PARENT, BACKED = (
    str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, n)) for n in ("size-read", "tip", "tip-parent", "bg")
)


def test_a_binding_reads_a_pictures_size_times_its_scale(tmp_path):
    """The game reports ``Size`` as the box times ``Scale`` for every widget (40,783 of 40,783 pictures in the
    dumps of 2026-11-23): the war declaration's AnnoButtonWT copies "backing" (16 x 16 scaled 24 x 1.4375) as 384 x
    23, not 16 x 16."""
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    root = root_node(SIZE_READ)
    source = picture_node("Source", Scale=(2.0, 3.0, 1.0))  # 64 x 32 scaled to 128 x 96
    copy = picture_node("Copy", Position=(0.0, 200.0, 0.0))
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(source)
    wf.add_instance(copy)
    size = [phoenixedit.BUILTIN["Size"]]
    wf.bind(wf.end(source, size), wf.end(copy, size))
    wf.finish("size-read")
    files = _files(tmp_path, **{SIZE_READ: root})
    frame = layout.place(SIZE_READ, files.root(SIZE_READ), layout.CContext(files))
    rects = sorted(layout.bounds(item.matrix, *item.size) for item in frame.items)
    assert rects == [(0.0, 0.0, 128.0, 96.0), (0.0, 200.0, 128.0, 296.0)]


def test_an_info_tip_takes_its_reference_widgets_rectangle_and_counts_for_no_size(tmp_path):
    """The placed info tip widgets the game had laid out (dumps of 2026-11-23) stand at their ``ReferenceWidget``'s
    rectangle in 29 of 31 (the war declaration's at its profile, 100 x 193 from -50, -68.6), not at their stored pivot
    (133, 40), and their ``ConsiderSize`` is off."""
    from tests.uiedit.uieditfakes import root_node, template_container

    from annotoolbox.uiedit import layout

    tip_uid = 0x7A9E9033
    tip_root = root_node(TIP_FILE)
    tip_file = phoenixedit.CWidgetFile(SCHEMA, tip_root, versions=VERSIONS)
    tip_file.add_instance(picture_node("HitArea", Scale=(0.0625, 0.0625, 1.0)))
    tip_file.finish("tip")
    phoenixedit.set_objects(tip_root, "Components", [phoenixedit.component(
        SCHEMA, "d2efb1b6-abb2-4ae8-92e1-e844587ef78b", "InfoTip", tip_uid, VERSIONS)])  # fmt: skip
    root = root_node(TIP_PARENT)
    reference = picture_node("Profile", Position=(100.0, 50.0, 0.0), Scale=(2.0, 1.0, 1.0))  # 128 x 32
    tip = template_container("InfoTipWidget", TIP_FILE)
    phoenixedit.set_field(tip, "PivotPoint", (133.0, 40.0, 0.0))
    reference_guid = str(__import__("uuid").UUID(bytes_le=reference["guid"]))
    phoenixedit.set_objects(tip, "PropertyValues", [phoenixedit.property_value(
        SCHEMA, [tip_uid, arrange.REFERENCE_WIDGET], "id", reference_guid, VERSIONS)])  # fmt: skip
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(reference)
    wf.add_instance(tip)
    wf.finish("tip-parent")
    files = _files(tmp_path, **{TIP_FILE: tip_root, TIP_PARENT: root})
    frame = layout.place(TIP_PARENT, files.root(TIP_PARENT), layout.CContext(files))
    top = elements.tree(files.root(TIP_PARENT))
    box = frame.boxes[id(elements.by_guid(top, str(__import__("uuid").UUID(bytes_le=tip["guid"]))).node)]
    assert layout.bounds(box.matrix, *box.size) == (100.0, 50.0, 228.0, 82.0)
    assert not box.counts


def _value(path: list[int], code: int, value) -> dict:
    node = phoenixedit.new_object(SCHEMA, "2df5f3e4", versions=VERSIONS)
    phoenixedit.set_field(node, "PropertyIdPath", path)
    phoenixedit.set_raw(node, "Value", phoenix.boxed(code, value))
    return node


def test_a_groups_box_holds_its_background_and_a_pivot_fraction_is_of_that_box(tmp_path):
    """The badge's "iconComplete" (dumps of 2026-11-23): a group whose background is stretched 5 round its content
    and whose pivot is half its size is centred on its place (-32 to 32), as the background is part of its box."""
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import layout

    root = root_node(BACKED)
    group_guid = __import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "bg-template")
    template = phoenixedit.new_object(SCHEMA, "2f208351", guid=group_guid.bytes_le, versions=VERSIONS)
    back, content = picture_node("Back"), picture_node("Content", Position=(10.0, 10.0, 0.0))
    phoenixedit.set_objects(template, "WidgetInstances", [back, content])
    phoenixedit.set_objects(root, "Groups", [template])
    group = scene_instance("fa702d0c", "Centred")
    phoenixedit.set_raw(group, "GroupTemplate", phoenix.ref_bytes("2f208351", str(group_guid)))
    phoenixedit.set_objects(group, "Components", [
        phoenixedit.component(SCHEMA, "33ee79a6-ebdc-4aae-8fd7-0c41b6dc1dd8", "Background", BG, VERSIONS),
        phoenixedit.component(SCHEMA, "c7b1e237-d1a6-4d84-b103-151d80f94439", "Pivot", ALIGN, VERSIONS)])  # fmt: skip
    phoenixedit.set_objects(group, "PropertyValues", [
        _value([BG, arrange.BACKGROUND_MARGIN], 0x1300, (5.0, 5.0, 5.0, 5.0)),
        _value([ALIGN, 0x752E0643], 0x1200, (0.5, 0.5, 0.0))])  # fmt: skip
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(group)
    wf.finish("bg")
    files = _files(tmp_path, **{BACKED: root})
    frame = layout.place(BACKED, files.root(BACKED), layout.CContext(files))
    (box,) = [frame.boxes[id(e.node)] for e in elements.tree(files.root(BACKED)).walk() if e.kind == "group"]
    assert layout.bounds(box.matrix, *box.size) == (-37.0, -21.0, 37.0, 21.0)


def test_a_list_of_tabs_selects_its_first_entry():
    """The construction menu's category list (21 of 21 dumps with a category open, 2026-11-19 to 23): the open
    entry is the first; an item whose file is no tab gets nothing."""
    from tests.uiedit.uieditfakes import root_node

    tab = root_node(str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "tab")))
    phoenixedit.set_objects(tab, "Components", [phoenixedit.component(
        SCHEMA, "f36d9167-2615-466e-b064-80f9e3d9976a", "Tab", 41, VERSIONS)])  # fmt: skip
    assert listlayout.selected(tab, 0) == {(41, listlayout.TAB_SELECTED): True}
    assert listlayout.selected(tab, 2) == {(41, listlayout.TAB_SELECTED): False}
    assert listlayout.selected(root_node(str(__import__("uuid").uuid4())), 0) == {}


ANIMATED = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "animated"))


@pytest.mark.parametrize("is_started", [True, False])
def test_an_animation_the_opening_starts_has_fired_its_started_event(tmp_path, is_started):
    """A category's "Base_Popup" is shown by its list's ``Open`` animation's Started event, which the selected entry
    starts as the scene opens; in the design view that event has fired, and one that nothing starts has not."""
    from tests.uiedit.uieditfakes import root_node

    from annotoolbox.uiedit import bindings, layout

    root = root_node(ANIMATED)
    popup, anim = picture_node("Popup", Visible=False), picture_node("Anim", Position=(100.0, 0.0, 0.0))
    phoenixedit.set_objects(anim, "Components", [phoenixedit.component(
        SCHEMA, "d2506d55-8f94-47a1-93d0-db3b244a1daa", "Open", 51, VERSIONS)])  # fmt: skip
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(popup)
    wf.add_instance(anim)
    wf.bind(wf.end(anim, [51, 0x7B18ECA5]), wf.end(popup, [phoenixedit.BUILTIN["Visible"]]))
    if is_started:
        wf.bind(wf.end(None, [phoenixedit.BUILTIN["AddedToScene"]]), wf.end(anim, [51, 0x78773C49]))
    wf.finish("animated")
    shown = bindings.objects(root, bindings.BINDINGS)[0]
    bindings.CEditor(root, lambda kind: phoenixedit.new_object(SCHEMA, kind, versions=VERSIONS)).set_operator(
        shown, "c32c7054")  # EventToTrue  # fmt: skip
    files = _files(tmp_path, **{ANIMATED: root})
    frame = layout.place(ANIMATED, files.root(ANIMATED), layout.CContext(files))
    assert len([item for item in frame.items if item.kind == "image"]) == (2 if is_started else 1)
