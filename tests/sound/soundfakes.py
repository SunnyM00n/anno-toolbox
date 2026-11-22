"""Stand-ins for the sound data: sound databases, banks, voices and the sound tree a game holds."""

from __future__ import annotations

import array
import io
import math
import struct
import types
import wave

import pytest
from tests.fakes import FakeTree
from tests.formats.formatfakes import PLAIN_TAIL, sfx_node

from annotoolbox.formats import bnk, bnksfx, riff
from annotoolbox.sources.assetdb import SAsset
from annotoolbox.sources.gametree import CGameTree


# 2070's defaults as properties.xml holds them: per property, the interface sounds, the global sounds
EMITTER_PROPERTIES = """<Properties><Group><Property><Name>Building</Name><DefaultValues>
<Building><BuildSound>98203</BuildSound><DestroySound>98201</DestroySound></Building>
<Selection><SelectSoundGuid>98854</SelectSoundGuid><DeSelectSoundGuid>98879</DeSelectSoundGuid></Selection>
</DefaultValues></Property></Group>
<UISounds><PlaceBuildingNegative>0</PlaceBuildingNegative></UISounds>
<UISounds><PlaceBuildingNegative>98204</PlaceBuildingNegative></UISounds>
<GlobalSounds><SoundFireSmall>98600</SoundFireSmall></GlobalSounds></Properties>"""


EMITTER_DATABASE = """<SoundDataBase><SoundIDs>
<SoundID><ID>11</ID><SoundType>0</SoundType><Filename>data/sound/sfx/place.wav</Filename><MasterVolume>57</MasterVolume></SoundID>
<SoundID><ID>12</ID><SoundType>0</SoundType><Filename>data/sound/sfx/warehouse.wav</Filename><MasterVolume>37</MasterVolume></SoundID>
<SoundID><ID>10</ID><SoundType>0</SoundType><Filename>data/sound/sfx/negative.wav</Filename><MasterVolume>100</MasterVolume></SoundID>
<SoundID><ID>7</ID><SoundType>1</SoundType><Filename>data/sound/sfx/destroy.wav</Filename><MasterVolume>100</MasterVolume></SoundID>
<SoundID><ID>559</ID><SoundType>0</SoundType><Filename>data/sound/sfx/select.wav</Filename><MasterVolume>66</MasterVolume></SoundID>
<SoundID><ID>649</ID><SoundType>4</SoundType><Filename>data/sound/sfx/fire.wav</Filename><MasterVolume>84</MasterVolume>
<MaxDistance>95</MaxDistance></SoundID>
<SoundID><ID>1</ID><SoundType>4</SoundType><Filename>data/sound/sfx/loop.wav</Filename><MasterVolume>50</MasterVolume>
<SoundFlags>1</SoundFlags><MaxDistance>80</MaxDistance></SoundID>
</SoundIDs><ObjectIDs>
<Object><ID>98203</ID><SoundID><ID>11</ID><Propability>52</Propability></SoundID></Object>
<Object><ID>98205</ID><SoundID><ID>12</ID><Propability>50</Propability></SoundID></Object>
<Object><ID>98204</ID><SoundID><ID>10</ID><Propability>52</Propability></SoundID></Object>
<Object><ID>98201</ID><SoundID><ID>7</ID><Propability>52</Propability></SoundID></Object>
<Object><ID>98854</ID><SoundID><ID>559</ID><Propability>49</Propability></SoundID></Object>
<Object><ID>98600</ID><SoundID><ID>649</ID><Propability>50</Propability></SoundID></Object>
<Object><ID>98001</ID><SoundID><ID>1</ID><Propability>40</Propability></SoundID></Object>
</ObjectIDs><AmbientIDs /></SoundDataBase>"""


DB = (
    b"<SoundDataBase><SoundIDs>"
    b"<SoundID><ID>1</ID>\r\n<SoundType>1</SoundType>\r\n<Filename>data/sound/a.wav</Filename>\r\n"
    b"<MasterVolume>80</MasterVolume>\r\n<MaxCount>2</MaxCount>\r\n<MinDistance>10</MinDistance>\r\n"
    b"<MaxDistance>90</MaxDistance>\r\n<Prio>1</Prio>\r\n<SoundFlags>0</SoundFlags>\r\n<VolumeID>1</VolumeID>\r\n"
    b"<RandomPitch>0</RandomPitch>\r\n<SoundGroupID>-1</SoundGroupID>\r\n<XZDistanceFactor>100</XZDistanceFactor>\r\n"
    b"</SoundID>\r\n"
    b"<SoundID><ID>12</ID>\r\n<SoundType>3</SoundType>\r\n<Filename>data/sound/music/b.mp3</Filename>\r\n"
    b"<MasterVolume>50</MasterVolume>\r\n<MaxCount>1</MaxCount>\r\n<MinDistance>0</MinDistance>\r\n"
    b"<MaxDistance>0</MaxDistance>\r\n<Prio>0</Prio>\r\n<SoundFlags>0</SoundFlags>\r\n<VolumeID>2</VolumeID>\r\n"
    b"<RandomPitch>0</RandomPitch>\r\n<SoundGroupID>-1</SoundGroupID>\r\n<XZDistanceFactor>0</XZDistanceFactor>\r\n"
    b"</SoundID>\r\n"
    b"</SoundIDs><SoundGroupIDs><SoundGroupID><ID>5</ID><Name>x</Name></SoundGroupID></SoundGroupIDs><ObjectIDs>"
    b"<Object><ID>40</ID>\r\n<Event><ID>4</ID>\r\n<SoundID><ID>1</ID>\r\n<Propability>40</Propability>\r\n</SoundID>\r\n"
    b"<SoundID><ID>12</ID>\r\n<Propability>10</Propability>\r\n</SoundID>\r\n</Event>\r\n</Object>\r\n"
    b"<Object><ID>98000</ID>\r\n<SoundID><ID>1</ID>\r\n<Propability>50</Propability>\r\n</SoundID>\r\n</Object>\r\n"
    b"</ObjectIDs><AmbientIDs><Ambient><ID>15</ID>\r\n<Name>amb</Name>\r\n<Channel><m_MinPause>0</m_MinPause>\r\n"
    b"<m_MaxPause>5</m_MaxPause>\r\n<SoundID><ID>1</ID>\r\n<Propability>20</Propability>\r\n</SoundID>\r\n</Channel>\r\n"
    b"</Ambient>\r\n</AmbientIDs></SoundDataBase>"
)


def wav_bytes(seconds: float = 0.5, rate: int = 8000) -> bytes:
    pcm = array.array("h", (int(8000 * math.sin(i / 20)) for i in range(int(rate * seconds))))
    return riff.wav_bytes(pcm.tobytes(), rate, 1)


def tree_of(files: dict[str, bytes]) -> CGameTree:
    return CGameTree("test", files.__getitem__, files, lambda _key: "test")


SOUNDS_DATABASE = """<SoundDataBase><SoundIDs>
<SoundID><ID>1</ID><SoundType>4</SoundType><Filename>data/sound/sfx/loop.wav</Filename><MasterVolume>50</MasterVolume>
<SoundFlags>1</SoundFlags><RandomPitch>6</RandomPitch><MinDistance>20</MinDistance><MaxDistance>80</MaxDistance><MaxCount>1</MaxCount><XZDistanceFactor>200</XZDistanceFactor></SoundID>
<SoundID><ID>2</ID><SoundType>1</SoundType><Filename>data\\sound\\sfx\\hit.wav</Filename><MasterVolume>100</MasterVolume>
<SoundFlags>0</SoundFlags><RandomPitch>0</RandomPitch><MaxDistance>100</MaxDistance><MaxCount>2</MaxCount><Prio>2</Prio><XZDistanceFactor>100</XZDistanceFactor></SoundID>
<SoundID><ID>3</ID><SoundType>1</SoundType><Filename>data/sound/sfx/hit2.wav</Filename><MasterVolume>100</MasterVolume>
<SoundFlags>0</SoundFlags><RandomPitch>-17</RandomPitch><MaxDistance>100</MaxDistance><XZDistanceFactor>150</XZDistanceFactor></SoundID>
</SoundIDs><ObjectIDs>
<Object><ID>98001</ID><SoundID><ID>1</ID><Propability>40</Propability></SoundID></Object>
<Object><ID>98002</ID><SoundID><ID>2</ID><Propability>50</Propability></SoundID>
<SoundID><ID>3</ID><Propability>25</Propability></SoundID></Object>
</ObjectIDs><AmbientIDs><Ambient><ID>97001</ID><Name>97001. test</Name>
<Channel><m_MinPause>1000</m_MinPause><m_MaxPause>3000</m_MaxPause><SoundID><ID>2</ID><Propability>50</Propability></SoundID></Channel>
<Channel><m_MinPause>0</m_MinPause><m_MaxPause>0</m_MaxPause><SoundID><ID>1</ID><Propability>0</Propability></SoundID></Channel>
</Ambient></AmbientIDs></SoundDataBase>"""


SND = """<SequenceSounds><m_Filename>x.snd</m_Filename>
<m_SequenceDuration><k>6001</k><v>1000</v><k>6003</k><v>500</v></m_SequenceDuration>
<m_SequenceSoundIDMap><k>6001</k><v><i><SoundID>2</SoundID><Position>0</Position></i>
<i><SoundID>3</SoundID><Position>600</Position></i></v>
<k>6003</k><v><i><SoundID>2</SoundID><Position>0</Position></i></v></m_SequenceSoundIDMap></SequenceSounds>"""


SOUND_CFG = """<m_Config><m_Sequences>
<m_Config><m_SequenceID>6001</m_SequenceID><m_Looped>1</m_Looped><m_Speed>1.0</m_Speed><m_ConfigType>SEQUENCE</m_ConfigType></m_Config>
<m_Config><m_SequenceID>6003</m_SequenceID><m_Looped>0</m_Looped><m_Speed>1.0</m_Speed><m_ConfigType>SEQUENCE</m_ConfigType></m_Config>
</m_Sequences></m_Config>"""


def sine_wav(frames: int = 4800, rate: int = 44100, channels: int = 1, level: int = 8000) -> bytes:
    """A sine wave as a 16-bit WAV file."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(level * math.sin(i / 20))) * channels for i in range(frames)))
    return buf.getvalue()


@pytest.fixture
def tree() -> FakeTree:
    return FakeTree(
        {
            "data/sound/sfx/loop.wav": sine_wav(44100),
            "data/sound/sfx/hit.wav": sine_wav(2000, channels=2),
            "data/sound/sfx/hit2.wav": sine_wav(1000),
            "data/graphics/buildings/mill/mill.snd": SND.encode(),
            "data/graphics/buildings/mill/mill.cfg": SOUND_CFG.encode(),
            "data/sound/sounddatabase.xml": SOUNDS_DATABASE.encode(),
        }
    )


def asset(guid: int, values: dict) -> SAsset:
    return SAsset(guid, f"a{guid}", "FactoryBuilding", ("Buildings",), values, "assets.xml")


def spec_for(*guids, ornaments=()):
    spec = types.SimpleNamespace(
        factories=[types.SimpleNamespace(guid=1000 + g, source=g) for g in guids],
        plants=[],
        specials=[],
        residences=[],
        warehouses=[],
        fields=[],
        publics=[],
        ornaments=[types.SimpleNamespace(guid=3000 + g, source=g) for g in ornaments],
        actors=[types.SimpleNamespace(guid=2000, source=20, template="Inhabitant")],
    )
    return spec


AMBIENT_ASSET = {
    "WareProduction": {"ProductionSound": "98001"},
    "Building": {"BuildSound": "98002"},
    "Mesh": {"AmbientList": {"Item": {"Ambient": "97001"}}},
    "Object": {"Variations": {"Item": {"Filename": "data/graphics/buildings/mill/mill.cfg"}}},
}


# the speech mixer of the shipped BNK_VO_MAIN: positioning, no aux, then the advanced settings (kill newest, 1 instance)
ROOT_TAIL = bytes.fromhex("0308000000000001010100000000000000")


VOICE_BUS, UI_BUS, UI_TOP_BUS = 1534528548, 2383742785, 1524941672


LAYER_TAIL = bytes(6) + b"\0\1" + bytes(8)


def children(ids: list[int]) -> bytes:
    return struct.pack("<I", len(ids)) + b"".join(struct.pack("<I", i) for i in ids)


def vo_bank() -> bnk.SSoundBank:
    """A speech bank: a root mixer with the voice bus, over one plain Sound and its event."""
    source = struct.pack("<IBQIB", bnk.VORBIS_PLUGIN, 1, 4242, 1200, 9)
    sound = bnk.SHircObject(bnksfx.SOUND, 20, source + sfx_node(0, 10, {}, PLAIN_TAIL))
    root = bnk.SHircObject(
        bnksfx.MIXER, 10, sfx_node(VOICE_BUS, 0, {43: struct.pack("<f", 100.0)}, ROOT_TAIL) + children([20])
    )
    header = struct.pack("<III", 172, 7, 0x7D9B903B) + struct.pack("<II", 16, 9136) + bytes(4) + bytes(28)
    return bnk.SSoundBank(chunks=[bnk.SChunk(b"BKHD", header), bnk.SChunk(b"HIRC", b"")], hirc=[sound, root])


def ui_bank() -> bnk.SSoundBank:
    """The shipped chime layout: event -> Play -> layer container -> Sound, under two mixers, the lower one with a bus."""
    sound = bnk.SHircObject(
        bnksfx.SOUND, 50, struct.pack("<IBQIB", bnk.VORBIS_PLUGIN, 0, 77, 3750, 0) + sfx_node(0, 40, {}, PLAIN_TAIL)
    )
    layer = bnk.SHircObject(9, 40, sfx_node(0, 30, {}, LAYER_TAIL) + children([50]))
    low = bnk.SHircObject(bnksfx.MIXER, 30, sfx_node(UI_BUS, 20, {}, LAYER_TAIL) + children([40]))
    top = bnk.SHircObject(bnksfx.MIXER, 20, sfx_node(UI_TOP_BUS, 0, {}, ROOT_TAIL) + children([30]))
    action = bnksfx.play_action(60, 40, 99)
    event = bnksfx.event(1695847355, [60])
    header = struct.pack("<III", 172, 99, bnksfx.BKHD_LANGUAGE) + struct.pack("<II", 16, 9136) + bytes(4) + bytes(16)
    return bnk.SSoundBank(
        chunks=[bnk.SChunk(b"BKHD", header), bnk.SChunk(b"HIRC", b"")], hirc=[sound, layer, low, top, action, event]
    )


def voice_wav(seconds: float = 0.5, rate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(
            b"".join(struct.pack("<h", int(9000 * math.sin(i / 7))) for i in range(int(seconds * rate)))
        )  # fmt: skip
    return buf.getvalue()
