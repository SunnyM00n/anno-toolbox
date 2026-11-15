"""Buildings and other objects an island holds by asset GUID: the model they draw, the rules they are held to, and the
copies and moves written back into the island file."""

import base64
import math
import struct
from types import SimpleNamespace

import numpy as np

from annotoolbox.editor import assetmodels, footprints, islandeditor, islandrules, objectclones
from annotoolbox.editor.assetmodels import Reason
from annotoolbox.editor.editsession import EditSession
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.editor.selection import copy, paste
from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands import islandedits
from fakes import FakeFiles

KONTOR = "data/base/graphics/roman/buildings/harbor/kontor_01/kontor_01.cfg"
BLOCKER = "data/base/graphics/roman/environment/blockers/blocker_01.cfg"
ASSETS = f"""<Assets>
<Asset><Template>Slot</Template><Values><Standard><GUID>2882</GUID><Name>Slot Mountain</Name></Standard>
<Object><Variations><Item><Filename>{BLOCKER}</Filename></Item><Item><Filename>data/b/second.cfg</Filename></Item>
</Variations></Object></Values></Asset>
<Asset><BaseAssetGUID>2882</BaseAssetGUID><Values><Standard><GUID>37926</GUID><Name>Fixed Slot</Name></Standard>
</Values></Asset>
<Asset><Template>HarborWarehouse</Template><Values><Standard><GUID>3402</GUID><Name>Harbor Warehouse</Name></Standard>
<Object><Type>GameObject</Type><Variations><Item><Filename>{KONTOR}</Filename></Item></Variations></Object>
</Values></Asset>
<Asset><Template>Marker</Template><Values><Standard><GUID>42256</GUID><Name>Camera Marker</Name></Standard>
<Object><Variations><Item><Filename>data/base/graphics/ui/placeholder/constraint_dummy.cfg</Filename></Item>
</Variations></Object></Values></Asset>
<Asset><Template>Quest</Template><Values><Standard><GUID>55771</GUID><Name>No Model Thing</Name></Standard></Values>
</Asset>
<Asset><Template>Lamp</Template><Values><Standard><GUID>70001</GUID><Name>Lamp</Name></Standard></Values></Asset>
</Assets>""".encode()
TEMPLATES = (
    "<Templates><Template><Name>Lamp</Name><Properties><Object><Variations>"
    "<Item><Filename>data/b/lamp_default.cfg</Filename></Item></Variations></Object></Properties></Template></Templates>"
)
CONFIG = "<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType><FileName>data/x/a.rdm</FileName></Config></Models></Config>"
EMPTY_CONFIG = "<Config><ConfigType>MAIN</ConfigType></Config>"
IFO = """<Info><BuildBlocker><Position><xf>7</xf><zf>-3.5</zf></Position><Position><xf>-1</xf><zf>-3.5</zf></Position>
<Position><xf>-1</xf><zf>3.5</zf></Position><Position><xf>7</xf><zf>3.5</zf></Position></BuildBlocker></Info>"""


def test_asset_models_follow_variation_base_template_and_placeholder():
    models = assetmodels.AssetModels(ASSETS, TEMPLATES)
    assert models.model_for(2882, 1).cfg == "data/b/second.cfg"
    assert models.model_for(2882, 9).cfg == BLOCKER  # a variation past the list: the first
    fixed = models.model_for(37926)  # no model of its own: its base asset's
    assert fixed.cfg == BLOCKER and fixed.name == "Fixed Slot" and fixed.template == "Slot"
    assert models.model_for(70001).cfg == "data/b/lamp_default.cfg"  # the template's default
    assert models.model_for(42256).why == Reason.PLACEHOLDER
    assert models.model_for(55771).why == Reason.NO_MODEL
    assert models.model_for(1).why == Reason.NO_ASSET


class Game(FakeFiles):
    pass


def _editor(objects: list[Tag]):
    game = Game(
        {
            "data/base/config/export/assets.xml": ASSETS,
            "data/base/config/export/templates.xml": TEMPLATES.encode(),
            KONTOR: CONFIG.encode(),
            BLOCKER: EMPTY_CONFIG.encode(),
            "data/b/second.cfg": CONFIG.encode(),
            footprints.info_path(KONTOR): IFO.encode(),
        }
    )
    files = SimpleNamespace(game=game, read=game.read, where=lambda p: "game" if game.exists(p) else None)
    area = Tag("doc", [Tag("GameObject", [Tag("objects", objects)])])
    gm = Tag("GameSessionManager", [Tag("AreaManagerData", [Tag("#1", [Attrib("Data", filedb.write(area))])])])
    ed = islandeditor.IslandEditor.__new__(islandeditor.IslandEditor)
    ed.mod_assets, ed._editor, ed._assets, ed._problems, ed.notes = "", ([], {}), None, {}, []
    ed.files, ed.view = files, SimpleNamespace(gm=gm)
    return ed


def _game_object(serial: int, guid: int, pos, variation: int | None = None, yaw: float = 0.0) -> Tag:
    kids = [Attrib("guid", struct.pack("<I", guid)), Attrib("ID", struct.pack("<I", serial) + b"\x01\x00\x00\x00")]
    if variation is not None:
        kids.append(Attrib("Variation", struct.pack("<I", variation)))
    kids += [
        Attrib("Position", struct.pack("<3f", *pos)),
        Attrib("Direction", struct.pack("<f", yaw)),
        Tag("Mesh", [Attrib("Orientation", struct.pack("<4f", 0, 0, 0, 1))]),
    ]
    return Tag("#1", kids)


def _opened():
    objs = [
        _game_object(1, 3402, (8.0, 0.5, 8.5)),
        _game_object(2, 2882, (3.0, 0.5, 3.0), variation=1),
        _game_object(3, 37926, (12.0, 0.5, 3.0)),
        _game_object(4, 42256, (3.0, 0.5, 12.0)),
        _game_object(5, 55771, (4.0, 0.5, 12.0)),
        _game_object(6, 123, (5.0, 0.5, 12.0)),
    ]
    return _editor(objs)


def test_the_islands_objects_get_the_model_of_their_asset_and_variation():
    items = {o["guid"]: o for o in _opened().objects()}
    assert items[3402]["cfg"] == KONTOR and items[3402]["clone"] and not items[3402]["marker"]
    assert items[2882]["cfg"] == "data/b/second.cfg" and items[2882]["variation"] == 1
    assert items[37926]["cfg"] == BLOCKER and items[37926]["marker"] == "NoModel"  # its config draws nothing
    assert items[37926]["unresolved"] == Reason.EMPTY
    assert items[42256]["marker"] == "NoModel" and items[42256]["unresolved"] == Reason.PLACEHOLDER
    assert items[55771]["marker"] == "NoModel" and items[55771]["unresolved"] == Reason.NO_MODEL
    assert items[123]["marker"] == "Unresolved" and items[123]["unresolved"] == Reason.NO_ASSET


def test_footprints_are_read_for_the_buildings_and_the_checks_list_the_pins():
    ed = _opened()
    objects = ed.objects()
    assert ed.footprints(objects) == {KONTOR: [[(7.0, -3.5), (-1.0, -3.5), (-1.0, 3.5), (7.0, 3.5)]]}
    session = EditSession(_island(objects, ed.footprints(objects)))
    rows = islandrules.check(session, "117")
    pins = [f.message for f in rows if "drawn as a pin" in f.message]
    assert len(pins) == 4 and any("no asset with this GUID" in m for m in pins)
    assert next(f for f in rows if "no asset" in f.message).level == "warning"
    assert next(f for f in rows if "Fixed Slot" in f.message).level == "info"
    assert not [f for f in rows if "build grid" in f.message]  # untouched objects are not held to it


def _island(objects: list[dict], table: dict) -> dict:
    w = 33
    heights = np.zeros((w, w), "<f4")
    return {
        "width": w,
        "samples_per_tile": 2,
        "heights": base64.b64encode(heights.tobytes()).decode(),
        "objects": objects,
        "footprints": table,
    }


def test_a_building_moved_off_its_grid_or_onto_another_is_flagged():
    ed = _opened()
    objects = ed.objects()
    session = EditSession(_island(objects, ed.footprints(objects)))
    (kontor,) = [e for e in session.entries if e.cfg == KONTOR]
    session.transform(kontor, position=[8.3, 0.5, 8.5])
    rows = [f.message for f in islandrules.check(session, "117") if f.level == "warning"]
    assert any("Harbor Warehouse stands off its build grid" in m for m in rows)
    session.transform(kontor, position=[9.0, 0.5, 8.5])
    assert not [m for m in (f.message for f in islandrules.check(session, "117")) if "build grid" in m]
    session.transform(kontor, rotation=[0, 0.1, 0, 0.995])  # about 11.5 degrees
    assert any("turned" in f.message and "eighths" in f.message for f in islandrules.check(session, "117"))
    session.transform(kontor, rotation=[0, 0, 0, 1])
    copy_of = session.duplicate(kontor)
    session.transform(copy_of, position=[kontor.position[0] + 2, 0.5, kontor.position[2]])
    assert any("overlap" in f.message for f in islandrules.check(session, "117"))
    session.transform(copy_of, position=[kontor.position[0] + 8, 0.5, kontor.position[2]])
    assert not any("overlap" in f.message for f in islandrules.check(session, "117"))


def test_the_footprint_snap_puts_the_corners_on_tile_corners():
    poly = [[(7.0, -3.5), (-1.0, -3.5), (-1.0, 3.5), (7.0, 3.5)]]
    turn = [0.0, 0.0, 0.0, 1.0]
    assert footprints.snap_origin(poly, (8.3, 0, 8.2), turn) == (8.0, 8.5)
    assert footprints.on_grid(poly, (8.0, 0, 8.5), turn) and not footprints.on_grid(poly, (8.2, 0, 8.5), turn)
    quarter = [0.0, 0.7071068, 0.0, 0.7071068]  # a quarter turn swaps the parities
    x, z = footprints.snap_origin(poly, (8.3, 0, 8.2), quarter)
    assert footprints.on_grid(poly, (x, 0, z), quarter) and (x, z) == (8.5, 8.0)
    eighth = [0.0, 0.3826834, 0.0, 0.9238795]
    x, z = footprints.snap_origin(poly, (8.3, 0, 8.2), eighth)
    assert abs((x + z) - round(x + z)) < 1e-9 and abs((z - x) - round(z - x)) < 1e-9
    assert footprints.eighth_of(eighth) == 1 and footprints.eighth_of([0, 0.1, 0, 0.995]) is None
    a = np.array([[0, 0], [2, 0], [2, 2], [0, 2.0]])
    assert footprints.overlap(a, a + (1, 0)) > 0.9 and footprints.overlap(a, a + (2, 0)) == 0.0


def test_a_copied_building_keeps_its_asset_and_variation_and_is_written_back():
    ed = _opened()
    objects = ed.objects()
    session = EditSession(_island(objects, ed.footprints(objects)))
    (slot,) = [e for e in session.entries if e.base["guid"] == 2882]
    clip = copy([slot])
    (new,) = paste(session, clip, [20.0, 0.5, 20.0])
    assert new.added["guid"] == 2882 and new.added["variation"] == 1 and new.added["clone"] and new.cfg == slot.cfg
    dup = session.duplicate(slot)
    assert dup.added["variation"] == 1 and dup.added["guid"] == 2882
    session.undo.undo()
    assert not dup.alive
    # the edit file as the build reads it: one object moved, one deleted, the paste written as a copy
    (kontor,) = [e for e in session.entries if e.cfg == KONTOR]
    session.transform(kontor, position=[9.0, 0.5, 8.5], rotation=[0.0, 0.7071068, 0.0, 0.7071068])
    session.delete(next(e for e in session.entries if e.base["guid"] == 37926))
    a7m, info = _synthetic_file(ed)
    files = FakeFiles({"data/i/i.a7m": a7m, "data/i/i.a7minfo": info})
    done = a7m_edit117.apply(files, "data/i/i.a7m", {**islandedits.empty(), **session.edits}, template=None)
    assert (
        done.report["objects added"] == 1
        and done.report["objects changed"] == 1
        and done.report["objects deleted"] == 1
    )
    assert done.report["unmatched"] == []
    back = a7m_edit117.IslandFile(done.a7m, info)
    rows = {}
    for _g, _objs, o, _name in islandedits._objects(back):
        rows.setdefault(struct.unpack_from("<I", o.find("guid").data)[0], []).append(o)
    moved = rows[3402][0]
    assert struct.unpack("<3f", moved.find("Position").data) == (9.0, 0.5, 8.5)
    assert struct.unpack("<f", moved.find("Direction").data)[0] != 0  # the turn is kept in both places
    assert len(rows[2882]) == 2 and 37926 not in rows
    copy_object = rows[2882][1]
    assert struct.unpack("<3f", copy_object.find("Position").data) == (20.0, 0.5, 20.0)
    assert struct.unpack("<I", copy_object.find("Variation").data)[0] == 1
    assert struct.unpack_from("<I", copy_object.find("ID").data)[0] == 4  # the next serial of the list


def _synthetic_file(ed, tags: list[Tag] | None = None) -> tuple[bytes, bytes]:
    area = Tag("doc", [Tag("GameObject", [Tag("objects", tags if tags is not None else _opened_tags())])])
    gm = Tag(
        "GameSessionManager",
        [
            Tag(
                "WorldManager",
                [
                    Tag(
                        "Water",
                        [
                            Attrib("x", struct.pack("<I", 4)),
                            Attrib("y", struct.pack("<I", 4)),
                            Attrib("bits", b"\xff" * 16),
                        ],
                    )
                ],
            ),
            Tag("AreaManagerData", [Tag("#1", [Attrib("Data", filedb.write(area))])]),
        ],
    )
    a7m = write_v22(
        {
            "gamedata.data": filedb.write(Tag("root", [gm])),
            "rd3d.data": filedb.write(Tag("root", [Tag("PropGrid", [])])),
        }
    )
    return a7m, filedb.write(Tag("root", [Tag("Info", [])]))


def _opened_tags() -> list[Tag]:
    return [
        _game_object(1, 3402, (8.0, 0.5, 8.5)),
        _game_object(2, 2882, (3.0, 0.5, 3.0), variation=1),
        _game_object(3, 37926, (12.0, 0.5, 3.0)),
    ]


def test_a_copy_with_no_sample_on_the_island_is_reported_not_dropped():
    found = {}
    edits = {"added_objects": [{"guid": 77, "clone": True, "position": [1, 0, 1], "rotation": [0, 0, 0, 1]}]}
    rest, added, missing = objectclones.place_copies(edits, found)
    assert added == 0 and missing == ["guid:77"] and rest["added_objects"] == []


def test_a_turn_with_a_negative_w_is_stored_as_a_direction_in_zero_to_two_pi():
    o = _game_object(1, 3402, (8.0, 0.5, 8.5))
    islandedits.set_object(o, {"rotation": [0, 0.7071068, 0, -0.7071068]})
    assert 0 <= struct.unpack("<f", o.find("Direction").data)[0] < 2 * math.pi
