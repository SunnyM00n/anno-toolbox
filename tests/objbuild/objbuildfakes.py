"""Stand-ins for the object builder: a house and its configs, and a game that holds them."""

from __future__ import annotations

import struct

import pytest
from tests.models.modelfakes import OLD_CFGDOC
from tests.rdmfiles import static_file

from annotoolbox.formats import filedb
from annotoolbox.sources import gametree

HOUSE_CFG = "data/base/house/house.cfg"


SHARED_HOUSE_CFG = "shared/base/house/house.cfg"


OBJECT_CFG = """<Config><ConfigType>MAIN</ConfigType>\r
<Models><Config><ConfigType>MODEL</ConfigType>\r
<Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType>\r
<Position.y>0</Position.y>\r
</Config>\r
</Transformer>\r
<FileName>{mesh}</FileName>\r
<Materials><Config><ConfigType>MATERIAL</ConfigType>\r
<cModelDiffTex>data/base/house/wall.psd</cModelDiffTex>\r
</Config>\r
</Materials>\r
</Config>\r
</Models>\r
<Sequences><Config><ConfigType>SEQUENCE</ConfigType>\r
<SequenceID>7</SequenceID>\r
<Speed>1</Speed>\r
<Track><TrackID>1</TrackID>\r
<TrackElement><Type>0</Type>\r
<StartTime>0</StartTime>\r
<EndTime>2000</EndTime>\r
<ModelID>0</ModelID>\r
<AnimationID>0</AnimationID>\r
</TrackElement>\r
</Track>\r
</Config>\r
</Sequences>\r
</Config>\r
"""


OBJECT_IFO = """<Info>
  <BoundingBox>
    <Position><xf>0</xf><yf>1</yf><zf>0</zf></Position>
    <Rotation><xf>0</xf><yf>0</yf><zf>0</zf><wf>1</wf></Rotation>
    <Extents><xf>1</xf><yf>1</yf><zf>1</zf></Extents>
  </BoundingBox>
  <BuildBlocker>
    <Position><xf>-2</xf><zf>-2</zf></Position>
    <Position><xf>2</xf><zf>-2</zf></Position>
    <Position><xf>2</xf><zf>2</zf></Position>
  </BuildBlocker>
</Info>
"""


def info_entry(number: int, *fields: filedb.SAttrib) -> filedb.STag:
    return filedb.STag("Config", [filedb.SAttrib("ConfigType", struct.pack("<I", number)), *fields])


HOUSE_BFG = filedb.write(  # the baked data of the house: one model, one sequence
    filedb.STag("", [filedb.STag("Config", [
        filedb.SAttrib("ConfigType", struct.pack("<I", 9)),
        filedb.STag("Models", [info_entry(2, filedb.SAttrib("EntityTransforms", b""), filedb.SAttrib("EntityColors", b""))]),
        filedb.STag("Sequences", [info_entry(8)]),
    ])])
)  # fmt: skip


FC = "<DummyRoot><hasValue>1</hasValue>\r\n<Dummies><i><hasValue>1</hasValue>\r\n<Name>door</Name>\r\n<Position><x>1</x>\r\n<y>0</y>\r\n<z>2</z>\r\n</Position>\r\n</i>\r\n</Dummies>\r\n</DummyRoot>\r\n"


@pytest.fixture
def objbuild_game(tmp_path):
    files = {
        HOUSE_CFG: OBJECT_CFG.format(mesh="data/base/house/rdm/house_lod0.fbx"),
        SHARED_HOUSE_CFG: OBJECT_CFG.format(mesh="shared/base/house/rdm/house_lod0.rdm"),
        "shared/base/house/rdm/house_lod0.rdm": static_file(),
        "data/base/house/house.ifo": OBJECT_IFO,
        "data/base/house/house.fc": FC,
        "data/base/lone/lone.cfg": OBJECT_CFG.format(mesh="shared/base/house/rdm/house_lod0.rdm"),
        "data/graphics/old/old.cfg": OLD_CFGDOC,
        "shared/base/house/house.bfg": HOUSE_BFG,
    }
    for rel, data in files.items():
        p = tmp_path / "117" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    tree = gametree.loose_tree(tmp_path / "117", "Anno 117")
    tree.m_game = "anno117"
    return tree
