"""What each field of an effect file does, as the particle editor's forms say it: how it is edited (a switch, a
number, a whole number, a closed list, a file name, text, read only) and a line for its tool tip. No Qt.

``info(syntax, name)`` is the entry (``syntax``: ``rdp``, ``xml`` for 2070's and 1404's ``.efc``, ``brace`` for
1701's); a field the tables do not name is text, said as "not described". The meanings are docs/particle-editor.md's
tables, which say where each one comes from.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..infra.i18n import N_

SWITCH, NUMBER, WHOLE, CHOICE, FILE, TEXT, FIXED = "switch", "number", "whole", "choice", "file", "text", "fixed"


@dataclass(frozen=True)
class Info:
    """How a field is edited and what it does (``meaning`` is marked for translation, ``tr`` it when shown)."""

    kind: str = TEXT
    meaning: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # (value as written, its name)
    limits: tuple[float, float] | None = (
        None  # the least and the most a number may be (a value outside breaks the game)
    )


SINCE_ZERO = (0.0, math.inf)
BYTE = (0.0, 255.0)
ALIGNED = (("0", N_("turns with the emitter")), ("1", N_("faces the camera")), ("2", N_("stands upright")))
LINE_TYPES = (("0", N_("a quad")), ("1", N_("a ribbon")), ("2", N_("a ribbon (type 2)")))
KINDS_2070 = (("1", N_("particles")), ("2", N_("a mesh")), ("3", N_("a billboard")), ("4", N_("a line")),
              ("5", N_("mass")), ("6", N_("an arrow")), ("7", N_("a flame")))  # fmt: skip
TECHNIQUES = (("0", N_("alpha blend")), ("1", N_("additive")), ("2", N_("refract")), ("3", N_("depth written")),
              ("4", N_("no depth test")), ("5", N_("the water's wave map")))  # fmt: skip
ALIGNMENT_2070 = (("0", N_("faces the camera")), ("1", N_("upright")), ("2", N_("fixed")))

RDP = {
    "FrameCount": Info(FIXED, N_("frames of the strip (the tick data is made for it)")),
    "FrameTime": Info(NUMBER, N_("milliseconds a frame lasts: the pace of the whole effect"), (), (1.0, 1.0e6)),
    "Looped": Info(SWITCH, N_("the strip repeats; off: it plays once when started")),
    "ParticleCount": Info(FIXED, N_("particles the tick data holds")),
    "TotalTickCount": Info(FIXED, N_("ticks the tick data holds")),
    "SelfOcclusion": Info(SWITCH, N_("shaded from the effect's centre")),
    "Lighting": Info(SWITCH, N_("lit by the sun")),
    "DoSort": Info(SWITCH, N_("particles drawn far to near")),
    "Invincible": Info(SWITCH, N_("kept when the camera is far")),
    "MaxScale": Info(NUMBER, N_("metres a full scale byte means: scales every particle"), (), SINCE_ZERO),
    "Center": Info(NUMBER, N_("the centre of the box the positions are fractions of: moves every particle")),
    "Extents": Info(NUMBER, N_("the half size of that box: stretches the cloud")),
    "TexturePath": Info(FILE, N_("the texture every material shows unless it names its own")),
    "NormalMapPath": Info(FILE, N_("a normal map for the particles")),
    "ViewAligned": Info(CHOICE, N_("how the quad is turned"), ALIGNED),
    "Order": Info(WHOLE, N_("the draw order of the materials (0 to 255)"), (), BYTE),
    "ColorBoost": Info(NUMBER, N_("lifts the colour by 1 + (boost x 0.07) ^ 2.2"), (), BYTE),
    "Additive": Info(SWITCH, N_("adds the colour; off: blends by alpha")),
    "Refractive": Info(SWITCH, N_("bends what is behind it")),
    "WaveParticle": Info(SWITCH, N_("raises the ocean's waves instead of drawing")),
    "OpaqueWithDepth": Info(SWITCH, N_("cut at alpha 0.1 and written to depth")),
    "LineType": Info(CHOICE, N_("a quad or a ribbon"), LINE_TYPES),
    "LineTiling": Info(NUMBER, N_("how often a ribbon repeats its texture"), (), SINCE_ZERO),
    "LineLength": Info(NUMBER, N_("a ribbon's length"), (), SINCE_ZERO),
    "cropUVWH": Info(NUMBER, N_("the part of the texture shown: x, y a corner, z width, w height")),
    "MovieTexPath": Info(FILE, N_("the material's own texture, a flipbook sheet")),
    "MovieNormPath": Info(FILE, N_("the flipbook's normal sheet")),
    "MovieMotionPath": Info(FILE, N_("the flipbook's motion sheet")),
    "MovieFPS": Info(NUMBER, N_("frames a second of the flipbook"), (), SINCE_ZERO),
}  # fmt: skip

XML = {
    "Name": Info(TEXT, N_("the emitter's name")),
    "EffectType": Info(CHOICE, N_("what the emitter draws"), KINDS_2070),
    "Texture0": Info(FILE, N_("the texture; the atlas tile shows a part of it")),
    "Texture1": Info(FILE, N_("a second texture (empty in every file)")),
    "Texture2": Info(FILE, N_("a third texture (empty in every file)")),
    "Shader": Info(FILE, N_("the effect shader")),
    "Model": Info(FILE, N_("the mesh a mesh emitter draws")),
    "Technique": Info(CHOICE, N_("how it is drawn"), TECHNIQUES),
    "Blending": Info(WHOLE, N_("how it is drawn (1404; 0 and 1 taken as 2070's alpha blend and additive)")),
    "Delay": Info(NUMBER, N_("milliseconds after the effect starts"), (), SINCE_ZERO),
    "LifeTime": Info(NUMBER, N_("milliseconds a particle lives"), (), SINCE_ZERO),
    "Duration": Info(NUMBER, N_("milliseconds the emitter emits"), (), SINCE_ZERO),
    "Loop": Info(SWITCH, N_("the emitter repeats")),
    "Bound": Info(SWITCH, N_("the particles follow the emitter")),
    "OffsetX": Info(NUMBER, N_("the emitter's place in the effect")),
    "OffsetY": Info(NUMBER, N_("the emitter's place in the effect")),
    "OffsetZ": Info(NUMBER, N_("the emitter's place in the effect")),
    "Alignment": Info(CHOICE, N_("how a sprite is turned"), ALIGNMENT_2070),
    "WindDependency": Info(NUMBER, N_("0 to 1: how much the wind moves the particles"), (), (0.0, 1.0)),
    "AtlasOffsetU": Info(NUMBER, N_("one corner of the texture's tile")),
    "AtlasOffsetV": Info(NUMBER, N_("one corner of the texture's tile")),
    "AtlasTilingU": Info(NUMBER, N_("the opposite corner of the texture's tile")),
    "AtlasTilingV": Info(NUMBER, N_("the opposite corner of the texture's tile")),
    "TexOffset": Info(NUMBER, N_("a mesh's random texture offset")),
    "ValueMin": Info(NUMBER, N_("the curve's lowest value")),
    "ValueMax": Info(NUMBER, N_("the curve's highest value")),
    "ValueDef": Info(NUMBER, N_("the value when the curve has no points")),
    "Varianz": Info(NUMBER, N_("how much each particle varies it: a factor from 1 - 2 x this to 1")),
    "Size": Info(FIXED, N_("the curve's point count (the graph edits them)")),
}  # fmt: skip

CURVES_2070 = {
    "PEP": N_("milliseconds between two particles"),
    "EmitArea": N_("the radius of the ring particles are born on"),
    "EmitterRotX": N_("the emitter's turn over its duration"),
    "EmitterRotY": N_("the emitter's turn over its duration"),
    "EmitterRotZ": N_("the emitter's turn over its duration"),
    "VelocityX": N_("how far a particle has moved from where it was born, over its life"),
    "VelocityY": N_("how far a particle has moved from where it was born, over its life"),
    "VelocityZ": N_("how far a particle has moved from where it was born, over its life"),
    "Scale": N_("half the particle's size over its life"),
    "Rotation": N_("the particle's angle over its life"),
    "Color": N_("the colour over the particle's life"),
    "Alpha": N_("the opacity over the particle's life"),
}

BRACE = {
    "Name": Info(TEXT, N_("the emitter's name")),
    "StartDelay": Info(NUMBER, N_("milliseconds after the effect starts"), (), SINCE_ZERO),
    "Duration": Info(NUMBER, N_("milliseconds one pass lasts"), (), SINCE_ZERO),
    "Pause": Info(NUMBER, N_("milliseconds between passes"), (), SINCE_ZERO),
    "Loop": Info(SWITCH, N_("the pass repeats")),
    "StateBlock": Info(TEXT, N_("the render state the engine's state-block file names")),
    "SrcBlend": Info(TEXT, N_("the blend of the source, without D3DBLEND_ (empty: the state block's)")),
    "DestBlend": Info(TEXT, N_("the blend of the target (ONE adds)")),
    "Position": Info(TEXT, N_("the emitter's place: x y z (32 a tile)")),
    "Rotation": Info(TEXT, N_("the emitter's turn: a quaternion x y z w")),
    "ScaleFactors": Info(TEXT, N_("the emitter's scale per axis")),
    "Texture": Info(FILE, N_("the texture")),
    "Texture000": Info(FILE, N_("the texture")),
    "Rect": Info(TEXT, N_("the part of the texture shown: left bottom right top")),
    "Alignment": Info(WHOLE, N_("0 faces the camera")),
    "TimeScale": Info(NUMBER, N_("the pace: 1 its own"), (), (0.001, math.inf)),
    "Lighting": Info(SWITCH, N_("lit")),
    "SoundID": Info(WHOLE, N_("a sound started with the emitter (0 none)")),
    "Minimum": Info(NUMBER, N_("the graph's lowest value")),
    "Maximum": Info(NUMBER, N_("the graph's highest value")),
    "Single": Info(SWITCH, N_("the graph is Value throughout; off: its points")),
    "Value": Info(NUMBER, N_("the value when the graph is single")),
    "Linear": Info(SWITCH, N_("straight lines between the points; off: smooth curves")),
    "Emit": Info(NUMBER, N_("particles a second")),
    "LifeTime": Info(NUMBER, N_("milliseconds a particle lives"), (), SINCE_ZERO),
    "Speed": Info(NUMBER, N_("units a second")),
    "PPS": Info(NUMBER, N_("puffs a second")),
    "MinSize": Info(NUMBER, N_("a puff's size when it starts")),
    "MaxSize": Info(NUMBER, N_("a puff's size when it ends")),
    "Velocity": Info(NUMBER, N_("units a second")),
}  # fmt: skip

TABLES = {"rdp": RDP, "xml": XML, "brace": BRACE}


def info(syntax: str, name: str) -> Info:
    """The field ``name`` of a file of ``syntax`` (a vector's part, ``Center.x``, as its vector)."""
    base = name.split(".", 1)[0]
    return TABLES.get(syntax, {}).get(base) or Info()


def curve_meaning(name: str) -> str:
    """What a 2070 or 1404 curve is ("" when not described); ``RotationX`` is ``Rotation``'s."""
    return CURVES_2070.get(name) or CURVES_2070.get(name.rstrip("XYZ"), "")
