import os
import struct
import zlib
from pathlib import Path

import pytest

from annotoolbox.editor.formats.a7m import edit2205 as a7m_edit2205
from annotoolbox.editor.formats.a7m import session as a7m_session
from annotoolbox.editor.formats.a7m.reader import archive_members
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.formats.rda import RdaArchive, RdaEntry
from annotoolbox.islands import islandedits
from annotoolbox.othergames import delivery2205, index2205
from fakes import INSTALL_VARS, FakeFiles
from test_session2205 import KIND, LOOSE_RD3D, PATH, _session

GAMEDATA, MAPINFO = f"{PATH}|gamedata.data", f"{PATH}|mapinfo.info"
RD3D = f"{LOOSE_RD3D}|rd3d.data"  # the loose rd3d.data's member: what the game reads for a map that has one
OLD = "maindata/data3.rda"


def _u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def _entry(path: str, archive: int, position: int, size: int, flags: int = 1) -> Tag:
    fields = [Attrib("FileName", _u16(path))]
    for name, fmt, value in (
        ("ArchiveFileIndex", "<I", archive),
        ("Position", "<Q", position),
        ("CompressedSize", "<Q", size // 2),
        ("UncompressedSize", "<Q", size),
        ("Flags", "<I", flags),
    ):
        if value:
            fields.append(Attrib(name, struct.pack(fmt, value)))
    return Tag("#1", fields)


def _index(drop: str = "") -> bytes:
    """A ``file.db`` as the game ships one in shape: a folder tree of file entries, the archive list."""
    entries = [_entry(GAMEDATA, 1, 4000, 900), _entry(MAPINFO, 1, 5000, 57), _entry(RD3D, 0, 600, 8000)]
    entries = [e for e in entries if e.find("FileName").data != _u16(drop)]
    files = Tag("FileMap", [x for e in entries for x in (Attrib("#32768", _u16("n")), e)])
    tree = Tag("FileTree", [Tag("PathMap", [Attrib("#32768", _u16("data")), Tag("#1", [files])])])
    archives = Tag(
        "ArchiveFiles",
        [Tag("#1", [Attrib("#32768", _u16(p)), Attrib("#32768", b"\0")]) for p in ("maindata/data0.rda", OLD)],
    )
    last = Attrib("LastArchiveFile", _u16(OLD))
    return filedb.write(Tag("", [Tag("ArchiveMap", [tree, archives, last])]), version=1)


def _files() -> FakeFiles:
    return FakeFiles(_session())


def _session2205() -> a7m_session.Session2205:
    return a7m_session.Session2205(_files(), PATH)


def _edits(**kinds) -> dict:
    return {**islandedits.empty(), "source": PATH, "game": "anno2205", **kinds}


def _objects(isl: a7m_session.Session2205, group: str) -> list[Tag]:
    return [o for g, o in isl.object_tags() if g == group]


KEY_TURNED = islandedits.key("guid:1001", [1.5, 2.0, 2.5])
KEY_FREE = islandedits.key("guid:1002", [3.0, 0.0, 0.25])
KEY_TREE = islandedits.key("data/p/tree.prp", [1.0, 0.5, 2.0])
KEY_ROCK = islandedits.key("data/p/rock.prp", [1.0, 0.5, 2.0])


def test_the_archive_index_comes_back_as_it_was_and_points_entries_at_a_new_archive():
    raw = _index()
    idx = index2205.ArchiveIndex(raw)
    assert idx.write() == raw
    assert idx.archive_names() == ["maindata/data0.rda", OLD]
    assert idx.entry(GAMEDATA.upper()) == index2205.IndexEntry(1, 4000, 450, 900, 0, 1)  # found by any case
    member = RdaEntry("x", 1, 123, 77, 300, 0)
    idx.point(GAMEDATA, "maindata/ours.rda", member)
    assert idx.entry(GAMEDATA) == index2205.IndexEntry(2, 123, 77, 300, 0, 1)
    assert idx.archive_index("maindata/ours.rda") == 2 and idx.archive_names()[-1] == "maindata/ours.rda"
    assert idx.entry(MAPINFO) == index2205.IndexEntry(1, 5000, 28, 57, 0, 1)  # the others stay as they were
    again = index2205.ArchiveIndex(idx.write())
    assert again.entry(GAMEDATA) == idx.entry(GAMEDATA) and again.archive_names() == idx.archive_names()
    with pytest.raises(KeyError):
        idx.point("data/nothing.a6m|gamedata.data", "maindata/ours.rda", member)
    with pytest.raises(ValueError, match="archive index"):
        index2205.ArchiveIndex(filedb.write(Tag("", [Tag("Other", [])]), version=1))


def test_the_index_keeps_its_layout():
    """1800's file.db is the same tree in FileDB layout 3: written back in it, not in 2205's layout 1."""
    raw = filedb.write(filedb.read(_index()), *filedb.name_ids(_index()), version=3)
    idx = index2205.ArchiveIndex(raw)
    assert idx.write() == raw and idx.entry(MAPINFO) == index2205.IndexEntry(1, 5000, 28, 57, 0, 1)


@pytest.mark.parametrize(
    ("game", "index"), [("anno2205", "maindata/file.db"), ("anno1800", "maindata/file.db"), ("anno1404", "ahe/file.db")]
)
def test_the_games_indexes_come_back_as_they_were(game: str, index: str):
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not (Path(folder) / index).is_file():
        pytest.skip(f"needs the installed {game}")
    raw = (Path(folder) / index).read_bytes()
    idx = index2205.ArchiveIndex(raw)
    assert idx.archive_names() and idx.write() == raw


def test_an_unedited_session_is_written_back_member_for_member_as_it_was():
    isl = _session2205()
    _, members = archive_members(_files().read(PATH))
    assert a7m_edit2205.member_bytes(isl, "gamedata.data") == members["gamedata.data"]
    loose = archive_members(_files().read(LOOSE_RD3D))[1]["rd3d.data"]
    assert a7m_edit2205.member_bytes(isl, "rd3d.data") == loose
    assert a7m_edit2205.apply(isl, _edits())["members"] == []  # no edit, no member to write


def test_edits_move_turn_scale_delete_and_copy_objects_and_props():
    isl = _session2205()
    edits = _edits(
        objects={
            KEY_TURNED: {"deleted": True},
            KEY_FREE: {"position": [4.0, 1.0, 0.5], "rotation": [0, 0, 1, 0], "scale": 1.5},
            "guid:9|0.00|0.00|0.00": {"deleted": True},
        },
        added_objects=[
            {"guid": 1002, "cfg": "data/g/a0.cfg", "position": [2.0, 0.0, 2.0], "rotation": [0, 0, 0, 1], "scale": 3},
            {"guid": 77, "cfg": "x.cfg", "position": [0, 0, 0], "rotation": [0, 0, 0, 1]},
        ],
        props={KEY_TREE: {"position": [2.0, 0.5, 2.5], "scale": 2.0}, KEY_ROCK: {"deleted": True}},
        added_props=[{"cfg": "data/p/bush.prp", "position": [0.5, 0.0, 0.5], "rotation": [0, 0, 0, 1], "scale": 1.5}],
    )
    report = a7m_edit2205.apply(isl, edits)
    assert report["members"] == ["gamedata.data", "rd3d.data"]
    assert (report["objects changed"], report["objects deleted"], report["objects added"]) == (1, 1, 1)
    assert (report["props changed"], report["props deleted"], report["props added"]) == (1, 1, 1)
    assert report["unmatched"] == [
        "guid:9|0.00|0.00|0.00",
        "guid:77",
    ]  # the edits no object matched, then the added ones with no sample
    assert report["still referenced"] == [KEY_TURNED]  # the label map names the deleted object's ID
    back = a7m_session.Session2205.__new__(a7m_session.Session2205)
    back.gd = filedb.read(a7m_edit2205.member_bytes(isl, "gamedata.data"))
    back.gm = back.gd.path("GameSessionManager")
    objs = back.objects()
    assert [o["guid"] for o in objs] == [
        1002,
        1002,
        1608,
        1001,
        4,
    ]  # the turned one is gone, a copy follows its original
    moved, copy = objs[0], objs[1]
    assert moved["position"] == [4.0, 1.0, 0.5] and moved["rotation"] == [0, 0, 1, 0] and moved["scale"] == 1.5
    assert copy["position"] == [2.0, 0.0, 2.0] and copy["scale"] == 3.0 and copy["folder"] == 7
    ids = [struct.unpack("<II", o.find("ID").data) for _, o in back.object_tags() if o.find("ID") is not None]
    assert (4, KIND) in ids and len(set(ids)) == len(ids)  # the copy has an ID of its own, past the highest serial
    rd = filedb.read(a7m_edit2205.member_bytes(isl, "rd3d.data"))
    props = rd.path("PropGrid/Props").children
    assert [p.find("FileName").data.decode("utf-16-le") for p in props] == [
        "data/p/tree.prp", "data/p/tree.prp", "data/p/bush.prp"
    ]  # fmt: skip
    assert struct.unpack("<3f", props[0].find("Position").data) == (2.0, 0.5, 2.5)
    assert struct.unpack("<3f", props[0].find("Scale").data) == (2.0, 2.0, 2.0)


def test_a_turn_about_the_vertical_axis_alone_changes_direction_and_a_tilt_adds_free_orientation():
    isl = _session2205()
    turned = _objects(isl, "GameObject")[0]
    a7m_edit2205.set_object(turned, {"rotation": [0.0, -0.7071068, 0.0, 0.7071068]})
    assert turned.find("FreeOrientation") is None and turned.find("Direction") is not None
    assert struct.unpack("<q", turned.find("Direction").data)[0] / a7m_session.FIXED == pytest.approx(90.0, abs=0.01)
    a7m_edit2205.set_object(turned, {"rotation": [0.2, 0.0, 0.0, 0.9797959], "position": [1.0, 0.0, 2.0]})
    assert struct.unpack("<4f", turned.path("FreeOrientation/Orientation").data)[0] == pytest.approx(0.2)
    assert a7m_session.object_position(turned) == [1.0, 0.0, 2.0]  # an axis at 0 that was stored stays readable


def test_the_build_writes_our_archive_and_points_the_games_index_at_it(tmp_path: Path):
    edits_dir, out = tmp_path / "edits", tmp_path / "out" / a7m_edit2205.OUT_NAME
    edits_dir.mkdir()
    islandedits.save(
        edits_dir / a7m_edit2205.edit_name(PATH),
        _edits(objects={KEY_FREE: {"position": [4.0, 1.0, 0.5]}}, props={KEY_ROCK: {"deleted": True}}),
    )
    (edits_dir / "2205_other.json").write_text("{}", encoding="utf-8")  # no source: skipped, said so
    log: list[str] = []
    written = a7m_edit2205.build(_files(), _index(), edits_dir, out, log.append)
    assert written == [PATH] and any("no source session" in line for line in log)
    idx = index2205.ArchiveIndex((out / "maindata/file.db").read_bytes())
    assert idx.archive_names() == ["maindata/data0.rda", OLD, a7m_edit2205.ARCHIVE]
    arc = (out / a7m_edit2205.ARCHIVE).read_bytes()
    for path, name in ((GAMEDATA, "gamedata.data"), (RD3D, "rd3d.data")):
        e = idx.entry(path)
        assert e.archive == 2 and e.flags == 1
        raw = arc[e.position : e.position + e.packed]  # as the game reads it: seek, inflate (a level 1 zlib stream)
        assert raw[:2] == b"\x78\x01" and zlib.decompress(raw) == a7m_edit2205.member_bytes(_apply(name), name)
        assert len(zlib.decompress(raw)) == e.size
    assert idx.entry(MAPINFO) == index2205.ArchiveIndex(_index()).entry(MAPINFO)  # what did not change is untouched
    assert RdaArchive(out / a7m_edit2205.ARCHIVE).entries.keys() == {GAMEDATA, RD3D}
    for bad in (tmp_path / "other", out.parent):
        with pytest.raises(ValueError, match="refusing"):
            a7m_edit2205.build(_files(), _index(), edits_dir, bad)


def _apply(member: str) -> a7m_session.Session2205:
    isl = _session2205()
    a7m_edit2205.apply(
        isl, _edits(objects={KEY_FREE: {"position": [4.0, 1.0, 0.5]}}, props={KEY_ROCK: {"deleted": True}})
    )
    return isl


def test_a_session_the_index_does_not_know_is_refused_and_no_edit_writes_nothing(tmp_path: Path):
    edits_dir, out = tmp_path / "edits", tmp_path / a7m_edit2205.OUT_NAME
    edits_dir.mkdir()
    islandedits.save(edits_dir / "2205_x.json", _edits(objects={KEY_FREE: {"position": [4.0, 1.0, 0.5]}}))
    with pytest.raises(ValueError, match="no entry"):  # an index without the session's entries: not this install's
        a7m_edit2205.build(_files(), _index(drop=GAMEDATA), edits_dir, out)
    islandedits.save(edits_dir / "2205_x.json", _edits())
    log: list[str] = []
    assert a7m_edit2205.build(_files(), _index(), edits_dir, out, log.append) == []
    assert any("nothing written" in line for line in log) and not (out / "maindata/file.db").exists()


def test_install_keeps_the_games_own_index_and_uninstall_gives_it_back(tmp_path: Path):
    game = tmp_path / "game"
    backup = delivery2205.backup_file(game)
    (game / "maindata").mkdir(parents=True)
    (game / "maindata" / "file.db").write_bytes(_index())
    edits_dir, built = tmp_path / "edits", tmp_path / a7m_edit2205.OUT_NAME
    edits_dir.mkdir()
    islandedits.save(edits_dir / "2205_x.json", _edits(objects={KEY_FREE: {"position": [4.0, 1.0, 0.5]}}))
    with pytest.raises(RuntimeError, match="nothing built"):
        a7m_edit2205.install(built, game)
    original = a7m_edit2205.original_index(game)
    assert original == _index()
    a7m_edit2205.build(_files(), original, edits_dir, built)
    a7m_edit2205.install(built, game)
    assert backup.read_bytes() == _index() and (game / a7m_edit2205.ARCHIVE).is_file()
    assert a7m_edit2205.is_ours((game / "maindata/file.db").read_bytes())
    assert a7m_edit2205.original_index(game) == _index()  # a rebuild starts from the game's own, not ours
    a7m_edit2205.install(built, game)  # again: the kept copy is not overwritten with ours
    assert backup.read_bytes() == _index()
    assert a7m_edit2205.uninstall(game)
    assert (game / "maindata/file.db").read_bytes() == _index() and not (game / a7m_edit2205.ARCHIVE).exists()
    assert not backup.exists()
    assert not a7m_edit2205.uninstall(game)  # nothing of ours is left to take out


def test_an_index_that_is_ours_without_the_games_copy_is_refused_and_an_old_copy_moved_in(tmp_path: Path):
    """The game's own index once lay in the workspace: a changed workspace left the install without it."""
    game, old = tmp_path / "game", tmp_path / "old-workspace" / "anno2205-file.db-original"
    (game / "maindata").mkdir(parents=True)
    (game / "maindata" / "file.db").write_bytes(_index())
    edits_dir, built = tmp_path / "edits", tmp_path / a7m_edit2205.OUT_NAME
    edits_dir.mkdir()
    islandedits.save(edits_dir / "2205_x.json", _edits(objects={KEY_FREE: {"position": [4.0, 1.0, 0.5]}}))
    a7m_edit2205.build(_files(), _index(), edits_dir, built)
    a7m_edit2205.install(built, game)
    delivery2205.backup_file(game).unlink()  # where it was before: the new place does not have it
    with pytest.raises(RuntimeError, match="verify the files"):
        a7m_edit2205.install(built, game)
    with pytest.raises(RuntimeError, match="verify the files"):
        a7m_edit2205.uninstall(game)
    old.parent.mkdir()
    old.write_bytes((game / "maindata/file.db").read_bytes())  # ours: never taken for the game's own
    with pytest.raises(RuntimeError, match="verify the files"):
        a7m_edit2205.original_index(game, old)
    old.write_bytes(_index())
    assert a7m_edit2205.original_index(game, old) == _index() and not old.exists()  # moved in
    assert a7m_edit2205.uninstall(game) and (game / "maindata/file.db").read_bytes() == _index()


def test_a_prop_that_stores_no_rotation_or_scale_gets_them_when_an_edit_sets_them():
    isl = _session2205()
    tree = isl.prop_tags()[0]
    tree.children = [c for c in tree.children if c.name not in ("Rotation", "Scale")]
    report = a7m_edit2205.apply(isl, _edits(props={KEY_TREE: {"rotation": [0, 1, 0, 0], "scale": 2.0}}))
    assert report["props changed"] == 1 and report["members"] == ["rd3d.data"]
    assert (isl.props()[0]["rotation"], isl.props()[0]["scale"]) == ([0.0, 1.0, 0.0, 0.0], [2.0, 2.0, 2.0])


def test_a_scene_without_an_object_manager_takes_prop_edits_and_lists_object_edits():
    isl = _session2205()
    isl.gm.children = [c for c in isl.gm.children if c.name != "GameObjectManager"]
    report = a7m_edit2205.apply(isl, _edits(objects={KEY_FREE: {"deleted": True}}, props={KEY_ROCK: {"deleted": True}}))
    assert report["unmatched"] == [KEY_FREE] and report["props deleted"] == 1
