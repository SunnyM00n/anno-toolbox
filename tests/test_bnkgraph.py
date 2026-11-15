"""``formats/bnkgraph``: the sound graph of synthetic banks in the three layouts the games ship (112, 132 to 135, 172):
sound sources, parents, child lists found by the objects that name the container, a switch container's group and
branches, events and actions; the real banks of the installs behind their variables (skipped without them). Also the
viewer's event walk with a switch or state choice."""

from __future__ import annotations

import os
import struct

import pytest
from tests.fakes import installed

from annotoolbox.exporting import banks
from annotoolbox.formats import bnk, bnkgraph

OLD, MID, NEW = 112, 135, 172


def node_base(version: int, parent: int, fx: bool = False, override: bool = False) -> bytes:
    """A node base with no properties: an effect when ``fx``, the positioning with its override bit set (and the
    five bytes that follow) when ``override`` (older layouts only)."""
    new = version >= 140
    head = bytes([0, 1, 0]) + bytes([0, 0xAA, 0xBB, 0xCC, 0xDD, 0, 0]) if fx else bytes([0, 0])
    head += bytes(2) if new else bytes(1)  # the metadata list (172), the attachment flag
    head += struct.pack("<I", 0) + struct.pack("<I", parent) + bytes(1) + bytes(2)  # bus, parent, bit vector, props
    positioning = bytes([0xD9, 1, 0, 0, 0, 0]) if override and not new else bytes([0xC0])
    tail = bytes(1) + (struct.pack("<I", 0) if version >= 128 else b"") + bytes(6)
    states = bytes(2) if version >= 128 else bytes(4)
    return head + positioning + tail + states + bytes(2)


def sound(version: int, oid: int, media: int, parent: int, stream: int = 2, **kw) -> bnk.HircObject:
    plugin = bnk.VORBIS_PLUGIN
    if version >= 140:
        source = struct.pack("<IBQIB", plugin, stream, media | 7 << 32, 0, 0)
    elif version >= 128:
        source = struct.pack("<IBIIB", plugin, stream, media, 0, 0)
    else:
        offset = struct.pack("<I", 0) if stream != 2 else b""
        source = struct.pack("<IBII", plugin, stream, media, media) + offset + struct.pack("<IB", 0, 0)
    return bnk.HircObject(2, oid, source + node_base(version, parent, **kw))


def container(version: int, kind: int, oid: int, parent: int, children: list[int], gap: bytes = b"") -> bnk.HircObject:
    ids = struct.pack("<I", len(children)) + b"".join(struct.pack("<I", c) for c in children)
    return bnk.HircObject(kind, oid, node_base(version, parent) + gap + ids)


def switch_container(version: int, oid: int, group: int, default: int, branches: dict[int, list[int]], state=False):
    kids = [c for nodes in branches.values() for c in nodes]
    gap = struct.pack("<BIIB", int(state), group, default, 0)
    table = struct.pack("<I", len(branches))
    for value, nodes in branches.items():
        table += struct.pack("<II", value, len(nodes)) + b"".join(struct.pack("<I", n) for n in nodes)
    obj = container(version, bnkgraph.SWITCH, oid, 0, kids, gap)
    return bnk.HircObject(obj.type, oid, obj.payload + table + struct.pack("<I", 0))


def event(version: int, oid: int, actions: list[int]) -> bnk.HircObject:
    ids = b"".join(struct.pack("<I", a) for a in actions)
    if version >= 140:
        return bnk.HircObject(4, oid, bytes(7) + bytes([len(actions)]) + ids)
    count = struct.pack("<B" if version >= 128 else "<I", len(actions))
    return bnk.HircObject(4, oid, count + ids)


def action(oid: int, kind: int, target: int, group: int = 0, value: int = 0) -> bnk.HircObject:
    head = struct.pack("<HIBBB", kind, target, 0, 0, 0)
    return bnk.HircObject(
        3, oid, head + (struct.pack("<II", group, value) if kind >> 8 in (0x12, 0x19) else b"\x04" + bytes(4))
    )


def objects(version: int) -> list[bnk.HircObject]:
    """Event 1 plays switch container 20 (group 900: value 1 plays sounds 11 and 12, value 2 sound 13); event 2 plays
    the actor-mixer 30 over sound 14 (which has an effect); event 3 sets switch 900 to 2."""
    return [
        sound(version, 11, 1100, 20, override=True), sound(version, 12, 1200, 20), sound(version, 13, 1300, 20),
        sound(version, 14, 1400, 30, fx=True),
        switch_container(version, 20, 900, 1, {1: [11, 12], 2: [13]}),
        container(version, bnkgraph.MIXER, 30, 0, [14]),
        action(21, 0x0403, 20), action(22, 0x0403, 30), action(23, 0x1901, 2, 900, 2),
        event(version, 1, [21]), event(version, 2, [22]), event(version, 3, [23]),
    ]  # fmt: skip


@pytest.mark.parametrize("version", (OLD, MID, NEW))
def test_the_graph_of_each_layout_has_parents_children_sources_and_the_switch_table(version):
    graph = bnkgraph.read_graph(objects(version), version)
    assert all(node.exact for node in graph.values())
    assert graph[11].source.media == 1100 and graph[11].source.stream == 2 and graph[13].parent == 20
    if version < 128:
        assert graph[11].source.file_id == 1100
    assert graph[14].parent == 30 and graph[30].children == [14] and sorted(graph[20].children) == [11, 12, 13]
    switch = graph[20].switch
    assert (switch.group, switch.default, switch.state_group) == (900, 1, False) and switch.branches == {
        1: [11, 12],
        2: [13],
    }


@pytest.mark.parametrize("version", (OLD, MID, NEW))
def test_events_and_actions_read_in_every_layout(version):
    by_id = {o.id: o for o in objects(version)}
    assert bnkgraph.read_event(by_id[1], version) == [21] and bnkgraph.read_action(by_id[21]).type == bnkgraph.PLAY
    assert bnkgraph.read_action(by_id[23]) == bnkgraph.Action(0x1901, 2, 900, 2)


def test_an_older_stream_in_the_bank_carries_the_offset_in_the_file_package():
    object_ = bnk.HircObject(
        2, 5, struct.pack("<IBIIII", bnk.PCM_PLUGIN, 0, 5, 6, 77, 88) + bytes(1) + node_base(OLD, 9)
    )
    node = bnkgraph.read_graph([object_], OLD)[5]
    assert (node.source.media, node.source.file_id, node.source.offset, node.source.size, node.parent) == (
        5,
        6,
        77,
        88,
        9,
    )


def bank(version: int) -> bytes:
    head = bnk.Chunk(b"BKHD", struct.pack("<III", version, 9, 0))
    out = bnk.SoundBank([head, bnk.Chunk(b"HIRC", b"")])
    out.hirc = objects(version)
    return bnk.write(out)


@pytest.mark.parametrize("version", (OLD, MID, NEW))
def test_an_event_walk_takes_every_branch_or_the_chosen_one(version):
    listing = banks.bank_listing(bank(version))
    assert [s.media for s in banks.walk_event(listing, 1).sounds] == [1100, 1200, 1300]
    walk = banks.walk_event(listing, 1, {900: 2})
    assert [s.media for s in walk.sounds] == [1300] and walk.exact
    assert walk.groups[900].values == [1, 2] and walk.groups[900].default == 1
    assert [s.media for s in banks.walk_event(listing, 2).sounds] == [1400]
    assert banks.walk_event(listing, 3).sets == [bnkgraph.Action(0x1901, 2, 900, 2)]


def test_a_state_group_and_names_from_the_info_files():
    objs = [sound(NEW, 11, 1, 20), switch_container(NEW, 20, 5, 7, {7: [11]}, state=True)]
    assert bnkgraph.read_graph(objs, NEW)[20].switch.state_group
    text = (
        '{"SoundBanksInfo": {"SoundBanks": [{"ShortName": "Init", "SwitchGroups": [{"Id": "900", "Name": "sw_x",'
        ' "Switches": [{"Id": "1", "Name": "a"}]}], "StateGroups": [{"Id": "5", "Name": "st_y", "States": []}]}]}}'
    )
    info = banks.bank_info(text)
    assert info.groups[900].name == "sw_x" and info.groups[900].values == {1: "a"} and info.groups[5].name == "st_y"
    more = banks.bank_info('{"SoundBanksInfo": {"SoundBanks": [{"ShortName": "Bank"}]}}')
    assert banks.bank_info(text, more) is more and more.name == "Bank" and 900 in more.groups


def test_a_child_list_that_cannot_be_found_is_said_to_be_inexact():
    objs = [sound(OLD, 11, 1, 20), bnk.HircObject(bnkgraph.MIXER, 20, node_base(OLD, 0))]
    graph = bnkgraph.read_graph(objs, OLD)
    assert not graph[20].exact and graph[20].children == []


@pytest.mark.parametrize("game", ("anno2205", "anno1800", "anno117"))
def test_the_installed_banks_read_to_exact_graphs(game):
    files = installed(game)
    if files is None:
        pytest.skip(f"needs {game} (its INSTALL_VARS variable)")
    read = getattr(files, "read_bytes", None) or files.read
    banks = [p for p in files.iter_paths("data/sound/") if p.endswith(".bnk") and "/de_de/" not in p]
    checked = 0
    for path in banks[:60]:
        data = read(path)
        if len(data) > 5_000_000:
            continue
        parsed = bnk.parse(data)
        graph = bnkgraph.read_graph(parsed.hirc, parsed.version)
        # a few of 117's containers are positioned in ways the layout does not read: nearly all are exact
        assert sum(not n.exact for n in graph.values()) <= len(graph) // 100, path
        for o in parsed.hirc:
            if o.type == bnkgraph.EVENT:
                bnkgraph.read_event(o, parsed.version)
            elif o.type == bnkgraph.ACTION:
                bnkgraph.read_action(o)
        checked += len(graph)
    assert checked > 100


class NoFiles:
    def exists(self, path: str) -> bool:
        return False

    def read(self, path: str) -> bytes:
        raise FileNotFoundError(path)


def test_the_sound_view_offers_the_switch_of_an_event_and_plays_the_chosen_branch(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    from annotoolbox.viewer.soundview import SoundView

    view = SoundView(cache=tmp_path)
    view.show_bank("data/sound/generatedsoundbanks/windows/9.bnk", bank(NEW), NoFiles())
    row = view.event_rows.index(1)
    view.tables["Events"].selectRow(row)
    view._show_choices(row, reset=True)
    assert view.choice_form.rowCount() == 1
    box = view.choice_form.itemAt(0, widgets.QFormLayout.ItemRole.FieldRole).widget()
    assert [box.itemText(i) for i in range(box.count())] == ["(every value)", "1 (start)", "2"]
    assert len(view.event_clips(1)[0]) == 0  # the media files are not in the game's files: nothing to play, no error
    box.setCurrentIndex(2)
    assert view.choices == {900: 2} and view.tables["Events"].item(row, 2).text() == "0"
    assert view.choice_form.rowCount() == 1
    app.processEvents()
