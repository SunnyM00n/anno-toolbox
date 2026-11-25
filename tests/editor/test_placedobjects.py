"""Buildings and other objects an island holds by asset GUID: the model they draw, the rules they are held to, and the
copies and moves written back into the island file."""

import base64
import math
import struct

import numpy as np
from tests.editor.editorfakes import BLOCKER, KONTOR, opened_island, placed_game_object, synthetic_file
from tests.editor.editorfakes import PLACED_ASSETS as ASSETS
from tests.editor.editorfakes import PLACED_TEMPLATES as TEMPLATES
from tests.fakes import FakeFiles

from annotoolbox.editor import assetmodels, footprints, islandrules, objectclones
from annotoolbox.editor.assetmodels import CReason
from annotoolbox.editor.editsession import CEditSession
from annotoolbox.editor.formats.a7m import edit117 as a7m_edit117
from annotoolbox.editor.selection import copy, paste
from annotoolbox.islands import islandedits


def test_asset_models_follow_variation_base_template_and_placeholder():
    models = assetmodels.CAssetModels(ASSETS, TEMPLATES)
    assert models.model_for(2882, 1).cfg == "data/b/second.cfg"
    assert models.model_for(2882, 9).cfg == BLOCKER  # a variation past the list: the first
    fixed = models.model_for(37926)  # no model of its own: its base asset's
    assert fixed.cfg == BLOCKER and fixed.name == "Fixed Slot" and fixed.template == "Slot"
    assert models.model_for(70001).cfg == "data/b/lamp_default.cfg"  # the template's default
    assert models.model_for(42256).why == CReason.PLACEHOLDER
    assert models.model_for(55771).why == CReason.NO_MODEL
    assert models.model_for(1).why == CReason.NO_ASSET


def test_a_config_that_is_the_editors_placeholder_file_is_a_placeholder_not_an_unreadable_config():
    ed = opened_island()
    assert (
        ed.model_problem("data/dev/graphics/placeholder/placeholder.cfg") == CReason.PLACEHOLDER
    )  # named, not shipped
    assert ed.model_problem("data/b/missing.cfg").startswith(CReason.UNREADABLE)


def test_the_islands_objects_get_the_model_of_their_asset_and_variation():
    items = {o["guid"]: o for o in opened_island().objects()}
    assert items[3402]["cfg"] == KONTOR and items[3402]["clone"] and not items[3402]["marker"]
    assert items[2882]["cfg"] == "data/b/second.cfg" and items[2882]["variation"] == 1
    assert items[37926]["cfg"] == BLOCKER and items[37926]["marker"] == "NoModel"  # its config draws nothing
    assert items[37926]["unresolved"] == CReason.EMPTY
    assert items[42256]["marker"] == "NoModel" and items[42256]["unresolved"] == CReason.PLACEHOLDER
    assert items[55771]["marker"] == "NoModel" and items[55771]["unresolved"] == CReason.NO_MODEL
    assert items[123]["marker"] == "Unresolved" and items[123]["unresolved"] == CReason.NO_ASSET


def test_footprints_are_read_for_the_buildings_and_the_checks_list_the_pins():
    ed = opened_island()
    objects = ed.objects()
    assert ed.footprints(objects) == {KONTOR: [[(7.0, -3.5), (-1.0, -3.5), (-1.0, 3.5), (7.0, 3.5)]]}
    session = CEditSession(_island(objects, ed.footprints(objects)))
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
    ed = opened_island()
    objects = ed.objects()
    session = CEditSession(_island(objects, ed.footprints(objects)))
    (kontor,) = [e for e in session.m_entries if e.cfg == KONTOR]
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
    ed = opened_island()
    objects = ed.objects()
    session = CEditSession(_island(objects, ed.footprints(objects)))
    (slot,) = [e for e in session.m_entries if e.base["guid"] == 2882]
    clip = copy([slot])
    (new,) = paste(session, clip, [20.0, 0.5, 20.0])
    assert new.added["guid"] == 2882 and new.added["variation"] == 1 and new.added["clone"] and new.cfg == slot.cfg
    dup = session.duplicate(slot)
    assert dup.added["variation"] == 1 and dup.added["guid"] == 2882
    session.m_undo.undo()
    assert not dup.alive
    # the edit file as the build reads it: one object moved, one deleted, the paste written as a copy
    (kontor,) = [e for e in session.m_entries if e.cfg == KONTOR]
    session.transform(kontor, position=[9.0, 0.5, 8.5], rotation=[0.0, 0.7071068, 0.0, 0.7071068])
    session.delete(next(e for e in session.m_entries if e.base["guid"] == 37926))
    a7m, info = synthetic_file(ed)
    files = FakeFiles({"data/i/i.a7m": a7m, "data/i/i.a7minfo": info})
    done = a7m_edit117.apply(files, "data/i/i.a7m", {**islandedits.empty(), **session.m_edits}, template=None)
    assert (
        done.report["objects added"] == 1
        and done.report["objects changed"] == 1
        and done.report["objects deleted"] == 1
    )
    assert done.report["unmatched"] == []
    back = a7m_edit117.CIslandFile(done.a7m, info)
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


def test_a_copy_with_no_sample_on_the_island_is_reported_not_dropped():
    found = {}
    edits = {"added_objects": [{"guid": 77, "clone": True, "position": [1, 0, 1], "rotation": [0, 0, 0, 1]}]}
    rest, added, missing = objectclones.place_copies(edits, found)
    assert added == 0 and missing == ["guid:77"] and rest["added_objects"] == []


def test_a_turn_with_a_negative_w_is_stored_as_a_direction_in_zero_to_two_pi():
    o = placed_game_object(1, 3402, (8.0, 0.5, 8.5))
    islandedits.set_object(o, {"rotation": [0, 0.7071068, 0, -0.7071068]})
    assert 0 <= struct.unpack("<f", o.find("Direction").data)[0] < 2 * math.pi
