"""``formats/bnkgraph``: the sound graph of synthetic banks in the three layouts the games ship (112, 132 to 135, 172):
sound sources, parents, child lists found by the objects that name the container, a switch container's group and
branches, events and actions; the real banks of the installs behind their variables (skipped without them). Also the
viewer's event walk with a switch or state choice."""

from __future__ import annotations

import os
import struct

import pytest
from tests.fakes import FakeFiles, installed
from tests.formats.formatfakes import MID_BANK, action, container, event, node_base, switch_container
from tests.formats.formatfakes import NEW_BANK as NEW
from tests.formats.formatfakes import OLD_BANK as OLD
from tests.formats.formatfakes import sound_object as sound

from annotoolbox.exporting import banks
from annotoolbox.formats import bnk, bnkgraph


def objects(version: int) -> list[bnk.SHircObject]:
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


@pytest.mark.parametrize("version", (OLD, MID_BANK, NEW))
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


@pytest.mark.parametrize("version", (OLD, MID_BANK, NEW))
def test_events_and_actions_read_in_every_layout(version):
    by_id = {o.id: o for o in objects(version)}
    assert bnkgraph.read_event(by_id[1], version) == [21] and bnkgraph.read_action(by_id[21]).type == bnkgraph.PLAY
    assert bnkgraph.read_action(by_id[23]) == bnkgraph.SAction(0x1901, 2, 900, 2)


def test_an_older_stream_in_the_bank_carries_the_offset_in_the_file_package():
    object_ = bnk.SHircObject(
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
    head = bnk.SChunk(b"BKHD", struct.pack("<III", version, 9, 0))
    out = bnk.SSoundBank([head, bnk.SChunk(b"HIRC", b"")])
    out.hirc = objects(version)
    return bnk.write(out)


@pytest.mark.parametrize("version", (OLD, MID_BANK, NEW))
def test_an_event_walk_takes_every_branch_or_the_chosen_one(version):
    listing = banks.bank_listing(bank(version))
    assert [s.media for s in banks.walk_event(listing, 1).sounds] == [1100, 1200, 1300]
    walk = banks.walk_event(listing, 1, {900: 2})
    assert [s.media for s in walk.sounds] == [1300] and walk.exact
    assert walk.groups[900].values == [1, 2] and walk.groups[900].default == 1
    assert [s.media for s in banks.walk_event(listing, 2).sounds] == [1400]
    assert banks.walk_event(listing, 3).sets == [bnkgraph.SAction(0x1901, 2, 900, 2)]


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
    objs = [sound(OLD, 11, 1, 20), bnk.SHircObject(bnkgraph.MIXER, 20, node_base(OLD, 0))]
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


def test_the_sound_view_offers_the_switch_of_an_event_and_plays_the_chosen_branch(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PySide6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    from annotoolbox.viewer.soundview import CSoundView

    view = CSoundView(cache=tmp_path)
    view.show_bank("data/sound/generatedsoundbanks/windows/9.bnk", bank(NEW), FakeFiles())
    row = view.m_event_rows.index(1)
    view.m_tables["Events"].selectRow(row)
    view._show_choices(row, reset=True)
    assert view.m_choice_form.rowCount() == 1
    box = view.m_choice_form.itemAt(0, widgets.QFormLayout.ItemRole.FieldRole).widget()
    assert [box.itemText(i) for i in range(box.count())] == ["(every value)", "1 (start)", "2"]
    assert len(view.event_clips(1)[0]) == 0  # the media files are not in the game's files: nothing to play, no error
    box.setCurrentIndex(2)
    assert view.m_choices == {900: 2} and view.m_tables["Events"].item(row, 2).text() == "0"
    assert view.m_choice_form.rowCount() == 1
    app.processEvents()


@pytest.mark.parametrize("version", [OLD, MID_BANK, NEW])
def test_a_cut_event_raises_the_readers_own_error(version):
    ev = event(version, 5, [1, 2, 3])
    cut = bnk.SHircObject(ev.type, ev.id, ev.payload[:-2])
    with pytest.raises(ValueError, match="cut short"):
        bnkgraph.read_event(cut, version)
    with pytest.raises(ValueError):
        bnkgraph.read_event(bnk.SHircObject(ev.type, ev.id, b""), version)
