"""How 1701's mesh configs (``.mcf``) are named in an editor: a node by its class type (a mesh, an effect, a group, a
transform ...) and the file or name it points at, instead of the plain tag tree. No Qt.

``label(part)`` is the entry: the text of a part's row, None for a part that is no 1701 node (the caller keeps its
own label). The classes are docs/formats/granny.md "Model configs that name the files"; the fields each holds were
read over all 1,381 files of the install (docs/tool-suite.md section 23, "The typed editors").
"""

from __future__ import annotations

from ..infra.i18n import N_, tr
from .cfgdoc import Node, Part, shown, strip

# m_MCClassType of an m_MeshConfig, and of an m_MeshTransform (101 and up); 50 and 51 are the animation and material nodes
NODE_CLASSES = {
    0: N_("Group"), 1: N_("Mesh"), 2: N_("Nested config"), 3: N_("Effect"), 4: N_("Billboard"), 6: N_("Dummy"),
    7: N_("Instanced vegetation"), 8: N_("Walking unit"), 9: N_("Cloth"),
}  # fmt: skip
TRANSFORM_CLASSES = {
    101: N_("Offset"), 102: N_("Rotation"), 103: N_("Link to a dummy"), 104: N_("Sway"), 105: N_("Level of detail"),
    107: N_("Scale"), 109: N_("Shown on a condition"), 110: N_("Colour"), 111: N_("Circling motion"),
}  # fmt: skip
# the field that says what a node is about, by class: a file, or a name
POINTS_AT = {
    0: ("Description", "Groupname"), 1: ("MeshFilename",), 2: ("MeshConfigFilename",), 3: ("EffectFilename",),
    4: ("BillboardFilename",), 6: ("Dummyname",), 7: ("GrannyObjectFilename",), 9: ("MeshFilename",),
    103: ("DummyName",), 109: ("ConditionTrigger",),
}  # fmt: skip
FILE_ENDINGS = {  # what a file field of a node names (the files check of 1701's install)
    "MeshFilename": (".gr2",), "GrannyObjectFilename": (".gr2",), "MeshConfigFilename": (".mcf",),
    "EffectFilename": (".efc",), "BillboardFilename": (".blb",),
}  # fmt: skip


def _text(node: Node, name: str) -> str:
    found = next((c for c in node.children if strip(c.name) == name and not c.children), None)
    return shown(found.text or "").strip() if found is not None else ""


def class_of(node: Node) -> int | None:
    """The ``m_MCClassType`` of a node, None when it has none."""
    text = _text(node, "MCClassType")
    return int(text) if text.lstrip("-").isdigit() else None


def _stem(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _what(node: Node) -> str:
    """What a node points at: the file name (without its folders) or the name its class gives."""
    for name in POINTS_AT.get(class_of(node), ()):
        text = _text(node, name)
        if text:
            return _stem(text) if "Filename" in name or "filename" in name else text
    return ""


def _title(node: Node) -> str:
    code = class_of(node)
    table = TRANSFORM_CLASSES if strip(node.name) == "MeshTransform" else NODE_CLASSES
    return tr(table[code]) if code in table else strip(node.name) if code is None else f"{strip(node.name)} {code}"


def _join(title: str, what: str) -> str:
    return f"{title}  {what}" if what else title


def _lod(node: Node) -> str:
    low, high = _text(node, "LODMin"), _text(node, "LODMax")
    return f"{low}-{high}" if low or high else ""


def label(part: Part) -> str | None:
    """The row of a part of a 1701 mesh config: ``Mesh  house.gr2``, ``Offset``, ``Children (3)``; None for a part
    that is none of its nodes."""
    node, tag = part.node, strip(part.node.name)
    if tag == "MCChilds":
        return tr("Children ({count})", count=len(part.children))
    if tag == "MCConfig":
        inner = next((c for c in node.children if strip(c.name) == "MeshConfig"), None)
        return _join(_title(inner), _what(inner)) if inner is not None else tr("Config")
    if tag == "MeshConfig":
        inside = part.parent is not None and strip(part.parent.name) == "MCConfig"
        return tr("{title} settings", title=_title(node)) if inside else _join(_title(node), _what(node))
    if tag == "MeshTransform":
        return _join(_title(node), _what(node) or (_lod(node) if class_of(node) == 105 else ""))
    if tag == "Material":
        return _join(tr("Material"), _text(node, "Name"))
    if tag == "Animation":
        return _join(tr("Animation"), _stem(_text(node, "Filename")))
    return None
