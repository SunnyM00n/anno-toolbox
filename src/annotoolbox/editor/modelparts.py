"""What a model config draws, as the island editor's view takes it, and where the model library files a config.

``model_parts`` reads a config (``models.configtree``) into its models (mesh, materials, place), the configs it
nests, its particle effects and its decals; ``library_group`` names the library group of a config from its folder
(``docs/island-editor.md``).
"""

from __future__ import annotations

from ..models import configtree, fileresolve
from ..render import decalmesh
from ..render.modellibrary import FOAM_SHADER

FOAM_ALPHA_SCALE = 0.25  # model_foamfall.fx: the vertex alpha x 0.25 is the opacity (docs/falls.md, 2070's fall shader)


def _placed(node: configtree.SNode) -> dict:
    """A part's place (``configtree.placement``: its orientation transforms nested) as the view's lists."""
    placement = configtree.placement(node)
    return {"position": list(placement.position), "rotation": list(placement.rotation), "scale": list(placement.scale)}


def _deforms(node: configtree.SNode) -> bool:
    """A MODEL's or FILE's ``DeformToTerrainHeight``: the engine bends its meshes (a FILE's: all it nests) to the
    terrain (rendering/island-117-view.md "Phase 5")."""
    return node.get("DeformToTerrainHeight").strip() == "1"


def _material(material_fields: configtree.SMaterialFields, texture=None, values: dict | None = None) -> dict:
    """A material as the view takes it: maps as ``texture`` finds them (``fileresolve.view_textures``), else as the
    config names them (``texture_png`` finds their files); ``values``: the MATERIAL's fields as written (the game's
    own shaders read them)."""
    texture = texture or (lambda name: name)
    values = values or {}
    out = {
        "diffuse": texture(material_fields.diffuse),
        "normal": texture(material_fields.normal),
        "color": list(material_fields.color),
        "shader": material_fields.shader,
        "flow": material_fields.flow,
        "flipbook": material_fields.flipbook,
        "values": values,
    }
    if (
        material_fields.shader == FOAM_SHADER
    ):  # a foam sheet (the mod's fall shader): the two scroll speeds, the opacity between its ends
        alpha = float(values.get("cAlphaFactor", 1.0)) * FOAM_ALPHA_SCALE
        out["foam"] = [float(values.get("cFloat3Value.x", 0.5)), float(values.get("cFloat3Value.y", 1.0)), alpha]
    return out


def model_parts(cfg_text: str, resolve: fileresolve.CResolver | None = None, owner: str = "") -> dict:
    """What a config draws: its models (mesh, per material number of the mesh its maps, colour, shader, flow and
    flipbook, the first material's also as the part's own; the transform) and the configs it nests
    (``models.configtree``). ``shader`` 7 is the water shader of waterfalls and splashes, which scrolls by ``flow``.
    ``resolve`` finds the files as the game does and keeps what it did not find (``owner``: the config read)."""
    tree = configtree.read_tree(cfg_text)
    parts = []
    for node in tree.section_of("Models"):
        mesh = configtree.mesh_file(node)
        if resolve is not None:  # one the game lacks keeps its name: listed in the Checks tab, its load fails
            mesh = resolve.mesh(node.get("FileName"), owner, node.path) or mesh
        if not mesh:
            continue
        materials = {}
        for k, material_fields in configtree.materials_at(node).items():
            found = (
                None
                if resolve is None
                else fileresolve.material_textures(material_fields, resolve, owner, material_fields.path)
            )
            materials[k] = _material(configtree.material_fields(material_fields), found and fileresolve.view_textures(found),
                                     configtree.first_values(material_fields.fields))  # fmt: skip
        first = next(iter(materials.values()), _material(configtree.SMaterialFields()))
        parts.append({"mesh": mesh, **first, "materials": materials, "lod_materials": configtree.material_lods(node),
                      "deform": _deforms(node), **_placed(node)})  # fmt: skip
    files = [
        {"cfg": file_tag.get("FileName").replace("\\", "/"), "deform": _deforms(file_tag), **_placed(file_tag)}
        for file_tag in tree.section_of("Files")
        if file_tag.get("FileName").lower().endswith(".cfg")
        and (resolve is None or resolve.config(file_tag.get("FileName"), owner, file_tag.path, prefers_shared=True))
    ]
    files += _props(tree)
    return {"parts": parts, "files": files, "effects": _effects(tree), "decals": _decals(tree, resolve, owner)}


def _props(tree: configtree.SNode) -> list[dict]:
    """The ``.prp`` props of 1800's and 2205's PROPCONTAINERs (a box pile, a slot module's bushes, an ice floe), as
    nested files at their own place (a PROP carries Position, Rotation and Scale itself). Of the containers that vary
    (``VariationEnabled``) the first is drawn: the game picks one by its probability."""
    out, varied = [], False
    for container in tree.section_of("PropContainers"):
        if container.number("VariationEnabled") != 0:
            if varied:
                continue
            varied = True
        for prop in container.section_of("Props"):
            name = prop.get("FileName").replace("\\", "/")
            if not name.lower().endswith(".prp"):
                continue
            out.append({
                "cfg": name, "deform": False,
                "position": [prop.number(f"Position.{axis}") for axis in "xyz"],
                "rotation": [prop.number(f"Rotation.{axis}") for axis in "xyz"] + [prop.number("Rotation.w", 1.0)],
                "scale": [prop.number(f"Scale.{axis}", 1.0) for axis in "xyz"],
            })  # fmt: skip
    return out


def _decals(tree: configtree.SNode, resolve: fileresolve.CResolver | None, owner: str) -> list[dict]:
    """The DECALs a config lays on the ground: their first material's maps, colour and opacity, half sizes (x, z),
    ``Type`` and ``OnStreets`` (``render.decalmesh``) and place; one shown only in a state is left out."""
    out = []
    for node in tree.section_of("Decals"):
        materials = node.section_of("Materials")
        extents = (node.number("Extents.x"), node.number("Extents.z"))
        if not materials or min(extents) <= 0 or not configtree.shown_always(node):
            continue
        material_fields = materials[0]
        found = (
            None
            if resolve is None
            else fileresolve.material_textures(material_fields, resolve, owner, material_fields.path)
        )
        fields = configtree.material_fields(material_fields)
        kind, on_streets = decalmesh.read_decal(node)
        out.append({**_material(fields, found and fileresolve.view_textures(found), configtree.first_values(material_fields.fields)),
                    "opacity": fields.opacity,
                    "extents": extents, "kind": kind, "on_streets": on_streets,
                    "texcoords": [node.number(f"TexCoords.{k}", default) for k, default in zip("xyzw", (0, 0, 1, 1), strict=True)],
                    **_placed(node)})  # fmt: skip
    return out


def _numbers(node: configtree.SNode, block: str, default: float) -> list[float]:
    """The x, y, z of a structured value (``Color``, ``SubImageSize``)."""
    found = node.blocks.get(block)
    values = {child.tag: child.text for child in found} if found is not None else {}
    out = []
    for axis in "xyz":
        try:
            out.append(float(values.get(axis) or default))
        except ValueError:
            out.append(default)
    return out


def _effects(tree: configtree.SNode) -> list[dict]:
    """The particle effects a config places: the file, its placement, colour, opacity and pace. One shown only in
    a state of the object (a visibility mask) or hung on a bone is left out: the view shows no states or poses."""
    out = []
    for particle in tree.section_of("Particles"):
        name = particle.get("FileName").replace("\\", "/")
        if not name.lower().endswith(".rdp") or not configtree.shown_always(particle):
            continue
        scale = particle.number("TimeScale", 1.0)
        sheet = [int(value) for value in _numbers(particle, "SubImageSize", 1)]  # a flipbook: columns, rows, frames
        out.append({
            "file": name, "color": _numbers(particle, "Color", 1.0), "alpha": particle.number("Alpha", 1.0),
            "speed": 1.0 / scale if scale > 0 else 1.0, "sheet": sheet if sheet[0] * sheet[1] > 1 else None,
            **_placed(particle),
        })  # fmt: skip
    return out


def library_group(path: str) -> str:
    """The library's group of a model config, from its folder: ``props / fences`` (117 adds the
    culture when it is not the shared library: ``buildings / ornaments (roman)``)."""
    parts = path.replace("\\", "/").split("/")
    if "graphics" not in parts:
        return "other"
    rest = parts[parts.index("graphics") + 1 : -1]
    if rest and rest[0] in ("library", "roman", "celtic", "roman_celtic", "egyptian"):
        culture, kinds = rest[0], rest[1:3]
        return " / ".join(kinds or ["other"]) + ("" if culture == "library" else f" ({culture})")
    return " / ".join(rest[:2] or ["other"])
