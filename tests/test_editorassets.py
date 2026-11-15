import struct

from annotoolbox.formats.filedb import Attrib, Tag
from annotoolbox.islands import editorassets, islandedits

TEMPLATES = """<Templates>
<Template><Name>Herd</Name><Properties><Object><OnlyVisibleInEditor>1</OnlyVisibleInEditor></Object></Properties></Template>
<Template><Name>Farm</Name><Properties><Object><OnlyVisibleInEditor>0</OnlyVisibleInEditor></Object></Properties></Template>
</Templates>"""
ASSETS = """<Assets>
<Asset><Template>Herd</Template><Values><Standard><GUID>1003227</GUID><Name>Herd Sheep</Name></Standard></Values></Asset>
<Asset><Template>Farm</Template><Values><Standard><GUID>500</GUID><Name>Sheep Farm</Name></Standard></Values></Asset>
<Asset><Template>StaticBlockingObject</Template><Values><Standard><GUID>101128</GUID><Name>Blocker</Name></Standard>
<Object><PlacableInEditor>1</PlacableInEditor></Object></Values></Asset>
</Assets>"""


def _herd(serial: int) -> Tag:
    return Tag(
        "#1",
        [
            Attrib("guid", struct.pack("<I", 1003227)),
            Attrib("ID", struct.pack("<I", serial) + b"\x01\x00\x1c\x80"),
            Attrib("ObjectFolderID", struct.pack("<I", 7)),
            Attrib("Position", struct.pack("<3f", 1, 2, 3)),
            Tag("QuestObject", []),
            Tag("Herd", [Attrib("Count", struct.pack("<I", 5))]),
            Tag("Mesh", [Attrib("Orientation", struct.pack("<4f", 0, 0, 0, 1))]),
        ],
    )


def test_catalogue_takes_editor_templates_and_flagged_assets():
    cat = editorassets.catalogue(ASSETS, TEMPLATES)
    assert [(e["guid"], e["template"]) for e in cat] == [(1003227, "Herd"), (101128, "StaticBlockingObject")]
    assert editorassets.template_map(ASSETS)[1003227] == "Herd"  # the template stands ahead of the GUID


class FakeBuilder:
    def __init__(self):
        self.objs = Tag("objects", [_herd(4)])
        self.doc = Tag("doc", [Tag("EditorObject", [self.objs])])

    def _areas_data(self):
        return [(None, self.doc, None)]

    def main_objects(self, group):
        return self.doc.path(f"{group}/objects")


def test_place_clones_a_vanilla_instance():
    b = FakeBuilder()
    found = {"Herd": ("EditorObject", _herd(9))}
    entry = {"guid": 1003228, "template": "Herd", "position": [10, 1, 20], "rotation": [0, 0.7071, 0, 0.7071]}
    assert editorassets.place(b, found, entry)
    new = b.objs.children[-1]
    assert struct.unpack("<I", new.find("guid").data)[0] == 1003228
    assert struct.unpack_from("<I", new.find("ID").data)[0] == 5  # after the highest serial in the group
    assert struct.unpack("<3f", new.find("Position").data) == (10, 1, 20)
    assert new.find("Herd") is not None  # the kind's own block comes along
    assert new.find("QuestObject") is None and new.find("ObjectFolderID") is None  # the source island's links do not
    assert not editorassets.place(b, found, {**entry, "template": "Flock"})  # no instance of that kind


def test_edits_name_model_less_objects_by_their_asset():
    b = FakeBuilder()
    b.rd = Tag("rd", [])
    edits = islandedits.empty()
    edits["objects"][islandedits.key("guid:1003227", (1, 2, 3))] = {"deleted": True}
    edits["added_objects"].append({"guid": 1003227, "template": "Herd", "position": [5, 0, 5]})
    report = islandedits.apply(b, edits, None, {"Herd": ("EditorObject", _herd(1))})
    assert report["objects deleted"] == 1 and report["editor objects added"] == 1 and report["unmatched"] == []


def test_clone_turns_the_direction_with_the_orientation():
    import math

    turned = editorassets.clone(_herd(1), 1003227, 2, (0, 0, 0), (0, 0.7071068, 0, 0.7071068))
    names = [c.name for c in turned.children]
    assert names[names.index("Position") + 1] == "Direction"  # where vanilla keeps it
    (yaw,) = struct.unpack("<f", turned.find("Direction").data)
    assert math.isclose(yaw, 1.5 * math.pi, abs_tol=1e-5)  # islandedits._yaw, kept in 0..2pi as vanilla's are

    sample = _herd(1)
    sample.children.insert(4, Attrib("Direction", struct.pack("<f", 1.0)))
    unturned = editorassets.clone(sample, 1003227, 2, (0, 0, 0), (0, 0, 0, 1))
    assert struct.unpack("<f", unturned.find("Direction").data) == (0.0,)  # the sample's own turn is gone
    assert editorassets.clone(_herd(1), 1003227, 2, (0, 0, 0), (0, 0, 0, 1)).find("Direction") is None
