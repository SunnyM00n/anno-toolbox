"""Stand-ins for the feedback units: walk and play sequences, dummies and the unit and building configs."""

from __future__ import annotations

import struct

from tests.formats.formatfakes import cdata

CRLF = "\r\n"


def fixed_bytes(*values: float) -> bytes:
    return cdata(struct.pack(f"<{len(values)}q", *(round(v * 4096) for v in values)))


def dummy(name: str, x: float, z: float, h: float, quat=(0.0, 0.0, 0.0, 1.0)) -> bytes:
    return (
        f"<i><hasValue>1</hasValue>{CRLF}<Name>{name}</Name>{CRLF}".encode()
        + b"<Position>"
        + fixed_bytes(x, z, h)
        + b"</Position>"
        + b"<Orientation>"
        + cdata(struct.pack("<4f", *quat))
        + f"</Orientation>{CRLF}</i>{CRLF}".encode()
    )


def loop(state: int, elements: str, sequence: int = 2000, dummy_name: str = "Fisher_0") -> str:
    return (
        f"<i><hasValue>1</hasValue><Loop{state}><hasValue>1</hasValue><DefaultState><DummyName>{dummy_name}</DummyName>"
        f"<SequenceID>{sequence}</SequenceID><ForceSequenceRestart>0</ForceSequenceRestart><Visible>1</Visible>"
        "<FadeVisibility>0</FadeVisibility><ResetToDefaultEveryLoop>0</ResetToDefaultEveryLoop>"
        "<StartDummyGroup></StartDummyGroup></DefaultState>"
        f"<ElementContainer><Elements>{elements}</Elements></ElementContainer></Loop{state}></i>"
    )


def item(guid: int = 30022, states=(1,), elements: str = "", main: int = 0, count: int = 1, extra: str = "") -> bytes:
    keys = "".join(f"<k>{s}</k><v>{i}</v>" for i, s in enumerate(states))
    loops = "".join(loop(s, elements) for s in states)
    return (
        b"<i><hasValue>1</hasValue><AssetVariationList2><GuidVariationList>"
        + cdata(struct.pack("<ii", guid, -1))
        + f"</GuidVariationList><AssetGroupNames></AssetGroupNames></AssetVariationList2><MainObject>{main}</MainObject>"
        f"<FeedbackLoops2>{keys}</FeedbackLoops2><SequenceDefinitions>{loops}</SequenceDefinitions>"
        f"<ActorCount>{count}</ActorCount><BoneLink>NoLink</BoneLink>{extra}<Description>Fisher_01</Description></i>".encode()
    )


def fc2070(items: bytes, dummies: bytes = b"", splines: bytes = b"") -> bytes:
    return (
        b"<DummyRoot><hasValue>1</hasValue><Name></Name><Groups></Groups><Dummies>"
        + dummies
        + b"</Dummies></DummyRoot><SplineData>"
        + splines
        + b"</SplineData><FeedbackDefinition><FeedbackConfigs>"
        + items
        + b"</FeedbackConfigs><ValidSequenceIDs></ValidSequenceIDs></FeedbackDefinition>"
    )


WALK = (
    "<i><hasValue>1</hasValue><elementType>0</elementType><StartDummy></StartDummy><TargetDummy>Fisher_1</TargetDummy>"
    "<WalkSequence>1000</WalkSequence><WalkFromCurrentPosition>1</WalkFromCurrentPosition>"
    "<UseTargetDummyDirection>1</UseTargetDummyDirection><DummyGroup></DummyGroup></i>"
)


PLAY = (
    "<i><hasValue>1</hasValue><elementType>1</elementType><m_IdleSequenceID>5000</m_IdleSequenceID>"
    "<MinPlayCount>1</MinPlayCount><MinPlayTime>0</MinPlayTime><MaxPlayCount>0</MaxPlayCount>"
    "<MaxPlayTime>0</MaxPlayTime><ResetStartTimev2>1</ResetStartTimev2></i>"
)


DUMMIES = dummy("Fisher_0", 1.0, 2.0, 0.5, (0.0, 0.5, 0.0, 0.8660254)) + dummy("Fisher_1", 3.0, 4.0, 0.0)


MATERIAL = (
    "<m_Materials><m_Config><m_Name>m</m_Name><m_ShaderIndex>0</m_ShaderIndex>"
    "<m_VertexFormat>P4h_N4b_T2h_I4b</m_VertexFormat><m_NumBonesPerVertex>1</m_NumBonesPerVertex>"
    "<m_ConfigType>MATERIAL</m_ConfigType></m_Config></m_Materials>"
)


def feedback_animations(*names: str) -> str:
    return (
        "<m_Animations>"
        + "".join(
            f"<m_Config><m_FileName>data\\graphics\\units\\anims\\{n}.rdm</m_FileName><m_LoopCount>0</m_LoopCount>"
            "<m_Scale>1.000000</m_Scale><m_ConfigType>ANIMATION</m_ConfigType></m_Config>"
            for n in names
        )
        + "</m_Animations>"
    )


def sequence(sid: int, looped: int, tracks: str, speed: float = 1.0) -> str:
    return (
        f"<m_Config><m_SequenceID>{sid}</m_SequenceID><m_Looped>{looped}</m_Looped><m_Speed>{speed}</m_Speed>{tracks}"
        "<m_ConfigType>SEQUENCE</m_ConfigType></m_Config>"
    )


def track(n: int, kind: int, **fields) -> str:
    inner = "".join(f"<m_{k}>{v}</m_{k}>" for k, v in fields.items())
    return f"<Track><TrackID>{n}</TrackID><TrackElement><Type>{kind}</Type>{inner}</TrackElement></Track>"


UNIT_CFG = (
    "<m_Config><m_Models>"
    "<m_Config><m_FileName>data\\graphics\\units\\u\\rdm\\u_lod0.rdm</m_FileName><m_ConfigType>MODEL</m_ConfigType>"
    + feedback_animations("walk", "work")
    + MATERIAL
    + "</m_Config>"
    "<m_Config><m_FileName>data\\graphics\\units\\u\\rdm\\tool_lod0.rdm</m_FileName><m_ConfigType>MODEL</m_ConfigType>"
    + feedback_animations("tool")
    + MATERIAL
    + "<m_Transformer><m_Config><m_Conditions>64</m_Conditions><m_ConfigType>VISIBILITY_TRANSFORM</m_ConfigType></m_Config>"
    "</m_Transformer></m_Config></m_Models><m_Sequences>"
    + sequence(1000, 1, track(1, 0, StartTime=0, EndTime=256, ModelID=0, AnimationID=0), 0.25)
    + sequence(5000, 1, track(1, 0, StartTime=0, EndTime=900, ModelID=0, AnimationID=0))
    + sequence(
        7500,
        0,
        track(1, 0, StartTime=0, EndTime=1000, ModelID=0, AnimationID=1)
        + track(2, 0, StartTime=0, EndTime=1000, ModelID=1, AnimationID=0)
        + track(3, 2, StartTime=0, EndTime=1000, Condition=64),
    )
    + "</m_Sequences><m_ConfigType>ROOT</m_ConfigType></m_Config>"
)


BUILDING_CFG = (
    "<m_Config><m_Models><m_Config><m_FileName>data\\graphics\\buildings\\b\\rdm\\b_lod0.rdm</m_FileName>"
    "<m_ConfigType>MODEL</m_ConfigType>"
    + feedback_animations("work")
    + MATERIAL
    + "</m_Config></m_Models><m_Sequences>"
    + sequence(6001, 1, track(1, 0, StartTime=0, EndTime=1000, ModelID=0, AnimationID=0))
    + "</m_Sequences><m_ConfigType>ROOT</m_ConfigType></m_Config>"
)
