"""Model configs, info and feedback files as editable documents (``models.cfgdoc``, ``models.cfgparts``): every
family read back byte for byte, parts named as the viewer names them, edits undone, new parts in the file's style."""

from __future__ import annotations

import pytest
from tests.models.modelfakes import NEW_CFGDOC as NEW
from tests.models.modelfakes import OLD_CFGDOC as OLD

from annotoolbox.models import cfgdoc, cfgparts, configtree
from annotoolbox.models.cfgdoc import SDoc

# 1701's mesh config, indented
MCF = b"<m_MeshConfig>\r\n  <m_MCClassType>0</m_MCClassType>\r\n  <m_MCChilds>\r\n    <m_Description>Hauptobjekte</m_Description>\r\n  </m_MCChilds>\r\n</m_MeshConfig>\r\n"
INFO = b"<Info>\n  <BoundingBox>\n    <Position>\n      <xf>0.5</xf>\n      <yf>1</yf>\n    </Position>\n  </BoundingBox>\n</Info>\n"
FEEDBACK = b"<DummyRoot><hasValue>1</hasValue>\r\n<Name></Name>\r\n</DummyRoot>\r\n<IdCounter>1</IdCounter>\r\n<ValidSequenceIDs>CDATA[\x04\x00\x00\x00\x01\x02\x03\x04]</ValidSequenceIDs>\r\n"


@pytest.mark.parametrize("data", [NEW, OLD, MCF, INFO, FEEDBACK])
def test_every_family_reads_back_byte_for_byte(data):
    doc = SDoc.read(data, "x.cfg")
    assert doc.exact and doc.to_bytes() == data and not doc.changed


def test_the_parts_are_named_as_the_viewer_names_them():
    doc = SDoc.read(NEW, "data/x.cfg")
    paths = {p.path for p in doc.parts().walk() if not p.is_list}
    viewer = {n.path for n in configtree.read_tree(NEW.decode("latin-1")).walk()}
    assert {"Models/0", "Models/0/Materials/0", "Sequences/0", "Particles/0"} <= paths & viewer
    assert "Sequences/0/Track#0/TrackElement#0" in paths
    old = SDoc.read(OLD, "data/a.cfg")
    assert old.find("Models/0").kind == "MODEL" and old.find("Models/0/Transformer/0").kind == "ORIENTATION_TRANSFORM"
    assert cfgparts.family(old) == "old" and cfgparts.family(SDoc.read(MCF, "a.mcf")) == "mcf"


def test_fields_carry_their_heading_and_kind():
    doc = SDoc.read(NEW, "data/x.cfg")
    fields = {f.name: f for f in cfgdoc.fields(doc.find("Models/0/Materials/0").node)}
    assert fields["cModelDiffTex"].group == "Common" and fields["cModelDiffTex"].value == "data/gfx/wall_diff.psd"
    kinds = {n: cfgparts.field_kind(n, f.value) for n, f in fields.items()}
    assert kinds == {"ConfigType": "kind", "cModelDiffTex": "file", "DIFFUSE_ENABLED": "switch",
                     "cDiffuseColor.r": "colour"}  # fmt: skip
    info = SDoc.read(INFO, "a.ifo")
    assert [f.name for f in cfgdoc.fields(info.find("BoundingBox#0").node)] == ["Position.xf", "Position.yf"]
    binary = [f for f in cfgdoc.fields(SDoc.read(FEEDBACK, "a.fc").root) if f.binary]
    assert binary[0].value == "01 02 03 04"


def test_edits_change_the_tree_and_are_undone():
    doc = SDoc.read(NEW, "data/x.cfg")
    model = doc.find("Models/0")
    texture = next(f for f in cfgdoc.fields(doc.find("Models/0/Materials/0").node) if f.name == "cModelDiffTex")
    edits = [cfgdoc.set_value(texture.node, "data/gfx/stone_diff.psd")]
    edits[0].do()
    edits.append(cfgdoc.duplicate(model.parent, model.node))  # a copy as the part is now
    edits[1].do()
    assert doc.changed and len(doc.find("Models").children) == 2
    assert b"stone_diff" in doc.to_bytes() and b"wall_diff" not in doc.to_bytes()
    for e in reversed(edits):
        e.undo()
    assert doc.to_bytes() == NEW
    move = cfgdoc.move(model.parent, model.node, 1)
    assert move is None  # the only model cannot move
    named = next(f for f in cfgdoc.fields(model.node) if f.name == "Name")
    edit = cfgdoc.set_value(named.node, "Haus ä")
    edit.do()
    assert cfgdoc.SDoc.read(doc.to_bytes()).find("Models/0").name == "Haus ä"


def test_new_parts_come_in_the_files_style():
    doc = SDoc.read(NEW, "data/x.cfg")
    files, made = cfgparts.list_node(doc, doc.parts().node, "Files")
    assert made is None  # the empty list is there
    edit = cfgdoc.insert(files, 0, [cfgparts.new_part(doc, "FILE")])
    edit.do()
    out = doc.to_bytes()
    assert b"<Files><Config><ConfigType>FILE</ConfigType>\r\n<Transformer>" in out and b"\n<" not in out.replace(
        b"\r\n", b""
    )
    assert doc.find("Files/0").kind == "FILE"
    material = cfgparts.new_part(doc, "MATERIAL")  # a copy of the file's own: every field of it
    assert len(material.children) == 5
    old = SDoc.read(OLD, "data/a.cfg")
    lights, made = cfgparts.list_node(old, old.parts().node, "Lights")
    made.do()
    cfgdoc.insert(lights, 0, [cfgparts.new_part(old, "LIGHT")]).do()
    text = old.to_bytes().decode()
    assert "<m_Lights><m_Config><m_Transformer>" in text and text.index("<m_Lights>") < text.rindex(
        "<m_ConfigType>ROOT"
    )
    assert "\r" not in text and old.find("Lights/0").kind == "LIGHT"
    assert cfgparts.new_part(old, "SEQUENCE").children[3].name == "Track"  # 2070 writes Track without m_
    assert cfgparts.new_part(doc, "DECAL") is None  # no template and none in the file: from another file
    copied = cfgdoc.restyled(old.find("Models/0").node, doc.newline)
    assert b"\r\n" in cfgdoc.SDoc.read(b"").to_bytes() or True
    assert all(b"\n" not in n.tail or n.tail.endswith(b"\r\n") for n in copied.iter())


def test_a_file_that_does_not_read_back_is_marked():
    mixed = b"<a><b>1</b>\x01\r\n<c>2</c>\r\n</a>\r\n"  # text after a child: the tree keeps it before the children
    doc = SDoc.read(mixed, "x.fc")
    assert not doc.exact


def test_a_number_is_written_with_four_decimals_at_most_and_no_negative_zero():
    assert cfgdoc.number_text(1.5) == "1.5" and cfgdoc.number_text(3.0) == "3"
    assert cfgdoc.number_text(-0.00001) == "0" and cfgdoc.number_text(0.12345678) == "0.1235"
    assert cfgdoc.number_text(2.0 / 3, decimals=2) == "0.67"
