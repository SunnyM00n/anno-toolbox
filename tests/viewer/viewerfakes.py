"""Stand-ins for the viewer: a game with a house, its props and a sound bank."""

from __future__ import annotations

import array
import json
import struct
import time

import pytest
from tests.ddsfiles import dx10, header
from tests.rdmfiles import animation_file, skinned_file, static_file

from annotoolbox.formats import bnk
from annotoolbox.models.configtree import fnv1a32
from annotoolbox.sources import gametree

VIEWER_HOUSE_CFG = "data/base/graphics/house/house.cfg"


VIEWER_SHARED_HOUSE_CFG = "shared/base/graphics/house/house.cfg"


PROP_PATH = "data/base/graphics/props/prop.cfg"


BANK = "data/sound/generatedsoundbanks/windows/1.bnk"


MODEL = """<Config><ConfigType>MAIN</ConfigType>
<Files><Config><ConfigType>FILE</ConfigType><FileName>{prop}</FileName>
<Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType><Position.x>4</Position.x></Config></Transformer>
</Config><Config><ConfigType>FILE</ConfigType><FileName>data/missing.cfg</FileName></Config></Files>
<Models><Config><ConfigType>MODEL</ConfigType><FileName>{mesh}</FileName>
<Materials><Config><ConfigType>MATERIAL</ConfigType><Name>wall</Name><ShaderID>8</ShaderID>
<cModelDiffTex>data/base/graphics/walls/wall_diff.psd</cModelDiffTex><METALLIC_TEX_ENABLED>1</METALLIC_TEX_ENABLED>
<cModelMetallicTex>data/base/graphics/walls/wall_diff.psd</cModelMetallicTex><DYE_MASK_ENABLED>1</DYE_MASK_ENABLED>
<cDyeMask>data/base/graphics/walls/wall_diff.psd</cDyeMask><GLOW_ENABLED>1</GLOW_ENABLED><cEmissiveColor>3</cEmissiveColor>
<cGlossinessFactor>0.5</cGlossinessFactor></Config>
<Config><ConfigType>MATERIAL</ConfigType><Name>roof</Name><ShaderID>8</ShaderID></Config></Materials>
<MaterialLODInfos><i><Indices><i>0</i><i>1</i></Indices></i><i><Indices><i>1</i><i>0</i></Indices></i></MaterialLODInfos>
</Config></Models>
<Sequences><Config><ConfigType>SEQUENCE</ConfigType><SequenceID>100691457</SequenceID><Looped>1</Looped>
<Track><TrackElement><Type>2</Type><StartTime>0</StartTime><EndTime>500</EndTime></TrackElement></Track>
</Config></Sequences></Config>"""


PROP_CFG = """<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType>
<FileName>data/base/graphics/props/crane.fbx</FileName>
<Animations><Config><ConfigType>ANIMATION</ConfigType><FileName>data/anim/crane_work.fbx</FileName>
<LoopCount>2</LoopCount><Scale>5</Scale></Config></Animations></Config>
<Config><ConfigType>MODEL</ConfigType><FileName>data/base/graphics/props/hook.rdm</FileName>
<Transformer><Config><ConfigType>BONELINK_TRANSFORM</ConfigType><ModelID>0</ModelID><BoneHash>{hand}</BoneHash></Config>
<Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType><Position.y>1</Position.y></Config></Transformer></Config>
<Config><ConfigType>MODEL</ConfigType><FileName>data/base/graphics/props/wheel.rdm</FileName>
<Transformer><Config><ConfigType>ANIMATION_TRANSFORM</ConfigType><Rotation.x>1</Rotation.x>
<RotationSpeed>0.5</RotationSpeed></Config></Transformer></Config></Models>
<Lights><Config><ConfigType>LIGHT</ConfigType><Transformer><Config><ConfigType>ORIENTATION_TRANSFORM</ConfigType>
<Position.y>2</Position.y></Config></Transformer><Type>0</Type><Diffuse.r>1</Diffuse.r><Diffuse.g>0.5</Diffuse.g>
<Diffuse.b>0.25</Diffuse.b><Diffuse.a>3</Diffuse.a><Range>4</Range><Falloff>2</Falloff><GraphDuration>2</GraphDuration>
<ColorGraph><i><t>0</t><c.r>1</c.r><c.g>1</c.g><c.b>1</c.b><c.a>1</c.a></i><i><t>1</t><c.r>1</c.r><c.g>1</c.g>
<c.b>1</c.b><c.a>0</c.a></i></ColorGraph></Config></Lights>
<Decals><Config><ConfigType>DECAL</ConfigType><Extents.x>2</Extents.x><Extents.y>0.25</Extents.y><Extents.z>1</Extents.z>
<Materials><Config><ConfigType>MATERIAL</ConfigType><ShaderID>1</ShaderID>
<cModelDiffTex>data/base/graphics/walls/wall_diff.psd</cModelDiffTex><cOpacity>0.5</cOpacity></Config></Materials>
</Config></Decals>
<Sequences><Config><ConfigType>SEQUENCE</ConfigType><SequenceID>251691777</SequenceID><Looped>1</Looped>
<Track><TrackElement><Type>0</Type><StartTime>0</StartTime><EndTime>2000</EndTime><ModelID>0</ModelID>
<AnimationID>0</AnimationID></TrackElement></Track></Config></Sequences></Config>"""


def settle_texture_view(view, timeout_s: float = 10.0) -> None:
    """Run the event loop until the texture view has no decode or picture left to make (they run off the window's
    thread and land through it)."""
    from PySide6.QtWidgets import QApplication

    end, idle = time.monotonic() + timeout_s, 0
    while time.monotonic() < end:
        QApplication.processEvents()
        # a finished thread's result may still wait in the queue and start the next one: idle for a few rounds
        idle = 0 if view.m_background.busy() or view.m_is_drawing or view.m_wanted else idle + 1
        if idle >= 3:
            return
        time.sleep(0.002)
    raise TimeoutError("the texture view did not settle")


def wait_for_pixels(view, timeout_s: float = 10.0) -> None:
    """Run the event loop until the texture view holds its first pixels (a decode landed)."""
    from PySide6.QtWidgets import QApplication

    end = time.monotonic() + timeout_s
    while view.m_pixels is None:
        if time.monotonic() > end:
            raise TimeoutError("no pixels landed")
        QApplication.processEvents()
        time.sleep(0.002)


def wall_texture() -> bytes:
    """A 4 x 4 RGBA8 DDS with two mip levels: red, then green."""
    return header(4, 4, 2) + dx10(28) + bytes([255, 0, 0, 255]) * 16 + bytes([0, 255, 0, 255]) * 4


def pcm(seconds: float = 0.1) -> bytes:
    return bnk.pcm_wem(array.array("h", [0, 1000] * int(24000 * seconds)), 48000, 1)


def bank_file() -> bytes:
    """A bank with an embedded PCM sound (media 55), a streamed one (media 66) and an event."""
    sound = lambda stream, media: struct.pack("<IBI", bnk.PCM_PLUGIN, stream, media) + bytes(8)  # noqa: E731
    blob = pcm()
    head = bnk.SChunk(b"BKHD", struct.pack("<III", 172, 1, 0))
    bank = bnk.SSoundBank([head, bnk.SChunk(b"DIDX", b""), bnk.SChunk(b"DATA", blob), bnk.SChunk(b"HIRC", b"")])
    bank.hirc = [
        bnk.SHircObject(2, 11, sound(0, 55)),
        bnk.SHircObject(2, 12, sound(1, 66)),
        bnk.SHircObject(4, 13, b""),
    ]
    bank.media = [bnk.SMediaEntry(55, 0, len(blob))]
    return bnk.write(bank)


def bank_json() -> str:
    media = [{"Id": "66", "ShortName": "door_open.wav", "Path": "Media/66.wem"}]
    return json.dumps({"SoundBanksInfo": {"SoundBanks": [{"ShortName": "BNK_TEST", "Media": media,
                                                          "Events": [{"Id": "13", "Name": "evt_door"}]}]}})  # fmt: skip


@pytest.fixture
def viewer_game(tmp_path):
    files = {
        VIEWER_HOUSE_CFG: MODEL.format(prop=PROP_PATH, mesh="data/base/graphics/house/rdm/house_lod0.fbx"),
        VIEWER_SHARED_HOUSE_CFG: MODEL.format(prop=PROP_PATH, mesh="shared/base/graphics/house/rdm/house_lod0.rdm"),
        "shared/base/graphics/house/rdm/house_lod0.rdm": static_file(),
        "shared/base/graphics/house/rdm/house_lod1.rdm": static_file(),
        PROP_PATH: PROP_CFG.replace("{hand}", str(fnv1a32("hand"))),
        "data/base/graphics/props/hook.rdm": static_file(),
        "data/base/graphics/props/wheel.rdm": static_file(),
        "data/base/graphics/props/crane.rdm": skinned_file(),
        "data/anim/crane_work.rdm": animation_file(),
        "data/base/graphics/walls/wall_diff_0.dds": wall_texture(),
        BANK: bank_file(),
        BANK[:-4] + ".json": bank_json(),
        "data/sound/generatedsoundbanks/windows/media/66.wem": pcm(0.2),
        "data/config/settings.xml": "<Settings><A>1</A></Settings>",
    }
    for rel, data in files.items():
        p = tmp_path / "117" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.encode() if isinstance(data, str) else data)
    tree = gametree.loose_tree(tmp_path / "117", "Anno 117")
    tree.m_game = "anno117"
    return tree
