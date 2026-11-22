"""What the parts of a model config may be, per family of games: the lists a part holds and their titles in the
studio's object builder, the kinds a new part of a list can be, a template for each, and the kind of each field.

``family(doc)`` names the family (``new``: 117, 1800, 2205; ``old``: 2070, 1404; ``mcf``: 1701; ``info``, ``feedback``);
``new_part(doc, section, kind)`` is a part for a list: a copy of the file's first part of that kind, else a template in
the file's style, else None (take one from another file). ``field_kind`` types a field for the editor.
docs/tool-suite.md section 23, "The data model".
"""

from __future__ import annotations

import copy
import re

from ..formats import isd2070
from .cfgdoc import SDoc, SEdit, SNode, insert, is_config, kind_of, restyled, strip

# The lists of a config and the studio's titles (the 1404 diary's tree: toolone-public-sources "Names seen")
TITLES = {
    "Files": "Files", "Models": "Models", "Clothes": "Cloths", "Particles": "Effects", "Effects": "Effects",
    "Lights": "Lights", "Decals": "Terrain Decals", "TerrainDecals": "Terrain Decals",
    "InstancingModels": "Model Instancing", "InstancingBBClusters": "Billboard Instancing",
    "BillboardClusters": "Billboard Instancing", "Sequences": "Sequences", "Transformer": "Transformers",
    "Materials": "Materials", "Animations": "Animations", "CollisionObjects": "Collision Objects",
}  # fmt: skip
# The kinds a new part of a list can be (the first is the default)
KINDS = {
    "Files": ("FILE",), "Models": ("MODEL",), "Clothes": ("CLOTH",), "Particles": ("PARTICLE",),
    "Effects": ("EFFECT",), "Lights": ("LIGHT",), "Decals": ("DECAL",), "TerrainDecals": ("TERRAIN_DECAL",),
    "Sequences": ("SEQUENCE",), "Materials": ("MATERIAL",), "Animations": ("ANIMATION",),
    "Transformer": ("ORIENTATION_TRANSFORM", "ANIMATION_TRANSFORM", "BONELINK_TRANSFORM", "VISIBILITY_TRANSFORM"),
}  # fmt: skip
# The lists a part of a kind holds, so an empty one can be offered (a new config's first model)
HOLDS = {
    "MAIN": ("Files", "Models", "Clothes", "Particles", "Lights", "Decals", "Sequences"),
    "ROOT": ("Files", "Models", "Clothes", "Effects", "Lights", "TerrainDecals", "Sequences"),
    "MODEL": ("Materials", "Animations", "Transformer"),
    "CLOTH": ("Materials", "Transformer"),
}  # fmt: skip
_ORIENT = [("Conditions", "0"), *((f"Position.{a}", "0") for a in "xyz"), *((f"Rotation.{a}", "0") for a in "xyz"),
           ("Rotation.w", "1"), ("Scale", "1")]  # fmt: skip
_PLACED = [("Transformer", [("ORIENTATION_TRANSFORM", _ORIENT)])]
_ELEMENT = [("Type", "0"), ("StartTime", "0"), ("EndTime", "1000"), ("ModelID", "0"), ("AnimationID", "0")]
# Minimal parts, written in the file's style: a field (name, text), a list of configs (name, [(kind, fields)]), a
# plain block (name, ("block", fields))
TEMPLATES = {
    "ORIENTATION_TRANSFORM": _ORIENT,
    "VISIBILITY_TRANSFORM": [("Conditions", "0")],
    "FILE": [*_PLACED, ("Name", "new_file"), ("FileName", "")],
    "PARTICLE": [*_PLACED, ("Name", "new_effect"), ("FileName", "")],
    "EFFECT": [*_PLACED, ("FileName", "")],
    "LIGHT": [*_PLACED, ("Name", "new_light"), ("Diffuse.r", "1"), ("Diffuse.g", "0.8"), ("Diffuse.b", "0.6"),
              ("Diffuse.a", "1"), ("Range", "2"), ("Falloff", "1")],
    "ANIMATION": [("FileName", ""), ("LoopCount", "0"), ("Scale", "1")],
    "SEQUENCE": [("SequenceID", "0"), ("Speed", "1"), ("Looped", "1"),
                 ("Track", ("block", [("TrackID", "1"), ("TrackElement", ("block", _ELEMENT))]))],
    "Track": [("TrackID", "1"), ("TrackElement", ("block", _ELEMENT))],
    "TrackElement": _ELEMENT,
}  # fmt: skip
PLAIN = ("Track", "TrackID", "TrackElement", "Type")  # 2070's sequences write these without the m_ prefix
FILE_FIELD = re.compile(r"(FileName|Filename|Tex|Texture|Map|Mask|File)(\.|$)|^c\w+Tex\w*$")
FILE_VALUE = re.compile(r"\.(psd|png|tga|dds|rdm|fbx|cfg|rdp|efc|gr2|fc|ifo|prp|mcf)$", re.I)
SWITCH = re.compile(r"_ENABLED$|^(Looped|DisableEffects|RestartEffects|AdaptTerrainHeight|DeformToTerrainHeight|"
                    r"DOUBLE_SIDED|hasValue|MainObject)$")  # fmt: skip
NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
COMPONENT = re.compile(r"\.(x|y|z|w|xf|yf|zf|wf)$")
COLOUR = re.compile(r"\.(r|g|b|a)$")


def family(doc: SDoc) -> str:
    """``new`` (117, 1800, 2205), ``old`` (2070, 1404), ``mcf`` (1701), ``info`` (``.ifo``), ``feedback`` (``.fc``)."""
    path = doc.path.lower()
    if path.endswith(".ifo"):
        return "info"
    if path.endswith(".fc"):
        return "feedback"
    names = {child.name for child in doc.root.children}
    if "m_MeshConfig" in names:
        return "mcf"
    return "old" if "m_Config" in names else "new"


def title(section: str) -> str:
    return TITLES.get(strip(section), strip(section))


def kinds_for(section: str) -> tuple[str, ...]:
    return KINDS.get(strip(section), ())


def lists_of(node: SNode) -> tuple[str, ...]:
    """The lists a config part of this kind may hold (``HOLDS``), the ones it has first."""
    own = tuple(
        strip(child.name)
        for child in node.children
        if child.children and all(is_config(grandchild) for grandchild in child.children)
    )
    return own + tuple(list_name for list_name in HOLDS.get(kind_of(node), ()) if list_name not in own)


def list_node(doc: SDoc, part: SNode, section: str) -> tuple[SNode, SEdit | None]:
    """The list ``section`` of a part, and the edit that makes it when the part has none yet (in the family's spelling,
    before the part's closing ``ConfigType`` in 2070's files)."""
    for child in part.children:
        if strip(child.name) == section:
            return child, None
    old = family(doc) == "old"
    node = SNode(_name(section, old), tail=doc.newline)
    insert_index = len(part.children) - (1 if old and part.children and part.children[-1].name == "m_ConfigType" else 0)
    return node, insert(part, insert_index, [node], f"add the list {section}")


def first_of(doc: SDoc, kind: str) -> SNode | None:
    """The file's first config part of a kind (or plain block of that name: ``Track``)."""
    for node in doc.root.iter():
        if (is_config(node) and kind_of(node) == kind) or (not is_config(node) and node.name == kind and node.children):
            return node
    return None


def new_part(doc: SDoc, kind: str, other: SDoc | None = None) -> SNode | None:
    """A new part of ``kind``: a copy of the first one of this file (or of ``other``, made in this file's line ends),
    else a template in the file's family, else None."""
    for source in (doc, other):
        found = first_of(source, kind) if source is not None else None
        if found is not None:
            return copy.deepcopy(found) if source is doc else restyled(found, doc.newline)
    return template_part(doc, kind)


def template_part(doc: SDoc, kind: str) -> SNode | None:
    """A new part of ``kind`` from the template alone (none of the file's own values), in the file's style."""
    spec = TEMPLATES.get(kind)
    if spec is None:
        return None
    old = family(doc) == "old"
    plain = kind in ("Track", "TrackElement")
    text = _block(kind, spec, old) if plain else _config(kind, spec, old)
    return isd2070.parse(text.replace("\n", doc.newline.decode()).encode("latin-1")).children[0]


def _name(name: str, old: bool) -> str:
    return f"m_{name}" if old and name not in PLAIN else name


def _fields(spec: list, old: bool) -> str:
    out = []
    for name, value in spec:
        tag = _name(name, old)
        if isinstance(value, str):
            out.append(f"<{tag}>{value}</{tag}>\n")
        elif isinstance(value, tuple):
            out.append(_block(tag, value[1], old))
        else:
            out.append(f"<{tag}>" + "".join(_config(kind, fields, old) for kind, fields in value) + f"</{tag}>\n")
    return "".join(out)


def _block(tag: str, spec: list, old: bool) -> str:
    return f"<{tag}>{_fields(spec, old)}</{tag}>\n"


def _config(kind: str, spec: list, old: bool) -> str:
    """A config in its family's order: 2070 and 1404 write ``m_ConfigType`` last, the newer games first."""
    if old:
        return f"<m_Config>{_fields(spec, old)}<m_ConfigType>{kind}</m_ConfigType>\n</m_Config>\n"
    return f"<Config><ConfigType>{kind}</ConfigType>\n{_fields(spec, old)}</Config>\n"


def field_kind(name: str, value: str) -> str:
    """How the editor shows a field: ``switch``, ``number``, ``file``, ``colour``, ``vector``, ``kind`` or ``text``."""
    bare = strip(name)
    if bare in ("ConfigType", "MCClassType"):
        return "kind"
    if SWITCH.search(bare) and value in ("0", "1"):
        return "switch"
    if FILE_FIELD.search(bare) or FILE_VALUE.search(value.strip()):
        return "file"
    if COLOUR.search(bare):
        return "colour"
    if COMPONENT.search(bare):
        return "vector"
    return "number" if NUMBER.match(value.strip()) else "text"
