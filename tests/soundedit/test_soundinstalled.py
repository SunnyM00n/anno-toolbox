"""The sound editor's readers and writers against the banks of the installed games (read-only, in memory): every bank
written back byte for byte, the emptied containers found again. Skips without the games' ``*_DIR`` variables."""

from __future__ import annotations

import pytest
from tests.formats.formatfakes import assert_a_playlist_tree
from tests.soundedit.soundeditfakes import installed_banks

from annotoolbox.formats import bnk, bnkgraph, bnkmusic
from annotoolbox.soundedit import containers
from annotoolbox.soundedit.bankdoc import CBankDoc

GAMES = ("anno2205", "anno1800", "anno117")


@pytest.mark.parametrize("game", GAMES)
def test_every_installed_bank_is_written_back_unchanged(game):
    seen = 0
    for path, data in installed_banks(game):
        doc = CBankDoc(data, path)
        assert doc.to_bytes() == data, path
        assert bnk.write(doc.m_bank) == data, path  # the objects re-rendered, not the kept original
        seen += 1
    assert seen


@pytest.mark.parametrize("game", GAMES)
def test_the_containers_the_installed_banks_ship_empty_are_found_and_written_back_equal(game):
    found = 0
    for path, data in installed_banks(game, english=True):
        doc = CBankDoc(data, path)
        for o in doc.m_bank.hirc:
            node = doc.graph.get(o.id)
            if o.type not in (bnkgraph.RANSEQ, bnkgraph.MIXER) or node is None or node.children:
                continue
            try:
                lay = containers.read_layout(o, node, doc.m_version)
            except containers.CLayoutError:
                continue  # its list is in other banks, or its tail is of another shape: refused, not guessed
            assert containers.render(o, lay) == o, (path, o.id)
            found += 1
    assert found


def test_117s_music_objects_render_back_and_a_replaced_clip_fits_a_real_segment():
    import array

    from annotoolbox.soundedit import musicedits

    music_bank = next((d, p) for p, d in installed_banks("anno117") if p.endswith("/2558193660.bnk"))
    doc = CBankDoc(music_bank[0], music_bank[1])
    segments = doc.objects_of(bnkmusic.MUSIC_SEGMENT)
    assert segments
    for seg in segments:
        assert bnkmusic.render_segment(bnkmusic.parse_segment(seg)) == seg
    for cnt in (*doc.objects_of(bnkmusic.MUSIC_RANSEQ_CNTR), *doc.objects_of(bnkmusic.MUSIC_SWITCH_CNTR)):
        start, end = bnkmusic.locate_trans_rules(cnt.payload)
        rules = bnkmusic.parse_trans_rules(bnkmusic.CReader(cnt.payload[start:end]))
        assert bnkmusic.render_trans_rules(rules) == cnt.payload[start:end]
        if cnt.type == bnkmusic.MUSIC_RANSEQ_CNTR:
            assert_a_playlist_tree(bnkmusic.parse_playlist(cnt).nodes)  # the fields' evidence (bank-objects.md)
    one = next(
        (s, t)
        for s in map(bnkmusic.parse_segment, segments)
        for t in [doc.hirc_object(c) for c in s.m_children if c in {o.id for o in doc.objects_of(bnkmusic.MUSIC_TRACK)}]
        if len(s.m_children) == 1 and abs(s.m_duration - musicedits.visible(bnkmusic.parse_track(t).m_playlist[0])) < 1
    )
    seg, trk = one
    wem = bnk.pcm_wem(array.array("h", [0] * 48000 * 5), 48000, 1)
    doc.do("replace_track_audio", track=trk.id, index=0, wem=wem, media=trk.id)
    after = bnkmusic.parse_segment(doc.hirc_object(seg.m_id))
    item = bnkmusic.parse_track(doc.hirc_object(trk.id)).m_playlist[0]
    assert after.m_duration == pytest.approx(musicedits.visible(item), abs=0.01)
    assert all(0 <= m.position <= after.m_duration + 0.001 for m in after.m_markers)


@pytest.mark.parametrize("game", GAMES)
def test_attenuations_layers_and_rtpc_lists_of_the_installs_render_back_unchanged(game):
    from annotoolbox.formats import bnkcurves

    seen = {"attenuation": 0, "layers": 0, "rtpc": 0}
    unread = total = 0
    for path, data in installed_banks(game):
        doc = CBankDoc(data, path)
        for o in doc.objects_of(14):
            att = bnkcurves.read_attenuation(o, doc.m_version)
            assert bnkcurves.render_attenuation(att, doc.m_version) == o.payload, (path, o.id)
            for curve in att.curves:
                bnkcurves.check_points(curve.points)
            seen["attenuation"] += 1
        for o in doc.objects_of(bnkgraph.LAYER):
            node = doc.graph[o.id]
            at = bnkgraph.child_block(o, doc.m_family, set(node.children))
            if at is None or not node.exact:
                continue
            layers, rest = bnkcurves.read_layers(o.payload, at + 4 + 4 * len(node.children))
            assert o.payload[: at + 4 + 4 * len(node.children)] + bnkcurves.render_layers(layers, rest) == o.payload
            seen["layers"] += 1
        for o in doc.m_bank.hirc:
            if o.type in (bnkgraph.SOUND, *bnkgraph.CONTAINERS):
                node = doc.graph.get(o.id)
                kids = node.children if node is not None and node.exact and doc.m_family != bnkgraph.NEW else None
                try:
                    start, end = bnkcurves.rtpc_span(o, doc.m_version, kids)
                except ValueError:  # 117: the few containers whose positioning the node base reader does not know
                    unread += 1
                    continue
                rtpcs = bnkcurves.read_rtpcs(bnkmusic.CReader(o.payload[start:end]))
                assert bnkcurves.render_rtpcs(rtpcs) == o.payload[start:end]
                seen["rtpc"] += len(rtpcs)
                total += 1
    assert seen["attenuation"] and seen["layers"]
    assert seen["rtpc"] and unread <= (total // 100 if game == "anno117" else 0)  # 1800 and 2205: every one reads


@pytest.mark.parametrize("game", GAMES)
def test_curve_edits_that_change_nothing_leave_a_real_banks_objects_as_they_were(game):
    from annotoolbox.formats import bnkcurves
    from annotoolbox.soundedit import curveedits

    done = set()
    for path, data in installed_banks(game):
        doc = CBankDoc(data, path)
        shaped = [o for o in doc.objects_of(14) if bnkcurves.read_attenuation(o, doc.m_version).curves]
        for o in shaped[:3]:
            att = bnkcurves.read_attenuation(o, doc.m_version)
            doc.do(
                "set_attenuation_curve",
                attenuation=o.id,
                curve=0,
                points=[(p.x, p.y, p.interp) for p in att.curves[0].points],
            )
            assert doc.hirc_object(o.id) == o
            done.add("attenuation")
        for o in doc.objects_of(bnkgraph.LAYER):
            refs = [
                r for r in curveedits.curve_refs(doc, o.id) if len(r.points) >= 2
            ]  # the layers have empty curves too
            if refs:
                ref = refs[0]
                doc.do(ref.edit, **ref.args, points=[(p.x, p.y, p.interp) for p in ref.points])
                assert doc.hirc_object(o.id) == o
                done.add("layer")
                break
        for o in doc.m_bank.hirc:  # a Sound's or container's node base ends in the list
            refs = curveedits.curve_refs(doc, o.id) if o.type in (bnkgraph.SOUND, *bnkgraph.CONTAINERS) else []
            if refs and refs[0].edit == "set_rtpc":
                doc.do(refs[0].edit, **refs[0].args, points=[(p.x, p.y, p.interp) for p in refs[0].points])
                assert doc.hirc_object(o.id) == o
                done.add("rtpc")
                break
    assert {"attenuation", "layer", "rtpc"} <= done


@pytest.mark.parametrize("game", GAMES)
def test_the_installed_init_banks_state_manager_reads_exactly_and_takes_a_group_back_out(game):
    from annotoolbox.formats import bnkstmg

    path, data = next((p, d) for p, d in installed_banks(game) if p.endswith("/1355168291.bnk"))
    doc = CBankDoc(data, path)
    sm = bnkstmg.read(doc.chunk(b"STMG"), doc.m_version)
    assert bnkstmg.render(sm) == doc.chunk(b"STMG") and sm.states and sm.switches
    before = doc.to_bytes()
    ident = doc.do("add_state_group", group="st_editor_test", states=["a", "b"], transition=300)
    assert len(bnkstmg.read(doc.chunk(b"STMG"), doc.m_version).states) == len(sm.states) + 1
    reopened = CBankDoc(doc.to_bytes(), path)
    assert [g.id for g in bnkstmg.read(reopened.chunk(b"STMG"), reopened.m_version).states][-1] == ident
    doc.do("remove_state_group", group=ident)
    assert doc.chunk(b"STMG") == bnkstmg.render(sm) and doc.to_bytes() == before  # added and removed: the same bank
