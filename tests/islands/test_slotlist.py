"""The list of an island's random slot objects (``islands.slotlist``)."""

import struct

from annotoolbox.formats.filedb import SAttrib, STag
from annotoolbox.islands import islandedits, slotlist

ROCK, MINE = 2070200011, 2070200012


def _id(serial: int) -> bytes:
    return struct.pack("<I", serial) + b"\x01\x00\x00\x00"


def _info(listed: dict) -> STag:
    info = STag("info", [STag("ObjectMetaInfo", [STag("RandomSlotObjects", [])])])
    slotlist.write(info, listed)
    return info


def _slot(serial: int, guid: int, pos) -> STag:
    return STag(
        "#1",
        [
            SAttrib("ID", _id(serial)),
            SAttrib("guid", struct.pack("<I", guid)),
            SAttrib("Position", struct.pack("<3f", *pos)),
        ],
    )


class FakeBuilder:
    def __init__(self, objects, listed):
        self.objs = STag("objects", objects)
        self.doc = STag("doc", [STag("GameObject", [self.objs])])
        self.a7minfo = _info(listed)

    def _areas_data(self):
        return [(None, self.doc, None)]


def test_the_list_reads_back_as_written():
    listed = {ROCK: [(_id(1), (1.0, 2.0, 3.0)), (_id(2), (4.0, 5.0, 6.0))], MINE: [(_id(3), (7.0, 8.0, 9.0))]}
    assert slotlist.read(_info(listed)) == listed
    assert slotlist.read(STag("info", [])) == {} and not slotlist.write(STag("info", []), listed)


def test_a_slot_moved_deleted_or_added_by_hand_is_listed_where_it_stands():
    listed = {ROCK: [(_id(1), (1.0, 2.0, 3.0)), (_id(2), (4.0, 5.0, 6.0))], MINE: [(_id(3), (7.0, 8.0, 9.0))]}
    # slot 1 moved, slot 2 gone, a third rock slot new, the mine unchanged, a tree of no listed asset ignored
    objects = [_slot(1, ROCK, (10, 2, 3)), _slot(3, MINE, (7, 8, 9)), _slot(9, ROCK, (0, 0, 1)), _slot(8, 5, (1, 1, 1))]
    builder = FakeBuilder(objects, listed)
    assert islandedits.refresh_slot_list(builder) == {"moved": 1, "dropped": 1, "added": 1}
    assert slotlist.read(builder.a7minfo) == {
        ROCK: [(_id(1), (10.0, 2.0, 3.0)), (_id(9), (0.0, 0.0, 1.0))],
        MINE: [(_id(3), (7.0, 8.0, 9.0))],
    }


def test_a_list_that_already_matches_the_objects_is_left_as_it_is():
    listed = {ROCK: [(_id(1), (1.0, 2.0, 3.0))]}
    builder = FakeBuilder([_slot(1, ROCK, (1, 2, 3))], listed)
    before = builder.a7minfo.path(slotlist.NODE).children
    assert islandedits.refresh_slot_list(builder) == {}
    assert builder.a7minfo.path(slotlist.NODE).children is before


def test_an_island_without_a_list_has_nothing_to_refresh():
    builder = FakeBuilder([_slot(1, ROCK, (1, 2, 3))], {})
    assert islandedits.refresh_slot_list(builder) == {}
    assert islandedits.refresh_slot_list(object()) == {}
