"""What a model config draws, as the island editor's view takes it, and where the model library files a config.

``model_parts`` reads a config (``models.configtree``) into its models (mesh, materials, place), the configs it
nests, its particle effects and its decals; ``library_group`` names the library group of a config from its folder
(``docs/island-editor.md``).
"""

from __future__ import annotations

from ..models import configtree, fileresolve
from ..render import decalmesh


def _placed(node: configtree.Node) -> dict:
    """A part's place (``configtree.placement``: its orientation transforms nested) as the view's lists."""
    p = configtree.placement(node)
    return {"position": list(p.position), "rotation": list(p.rotation), "scale": list(p.scale)}


def _deforms(node: configtree.Node) -> bool:
    """A MODEL's or FILE's ``DeformToTerrainHeight``: the engine bends its meshes (a FILE's: all it nests) to the
    terrain (rendering/island-117-view.md "Phase 5")."""
    return node.get("DeformToTerrainHeight").strip() == "1"


def _material(m: configtree.MaterialFields, texture=None, values: dict | None = None) -> dict:
    """A material as the view takes it: maps as ``texture`` finds them (``fileresolve.view_textures``), else as the
    config names them (``texture_png`` finds their files); ``values``: the MATERIAL's fields as written (the game's
    own shaders read them)."""
    texture = texture or (lambda name: name)
    return {"diffuse": texture(m.diffuse), "normal": texture(m.normal), "color": list(m.color), "shader": m.shader,
            "flow": m.flow, "flipbook": m.flipbook, "values": values or {}}  # fmt: skip


def model_parts(cfg_text: str, resolve: fileresolve.Resolver | None = None, owner: str = "") -> dict:
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
        for k, m in configtree.materials_at(node).items():
            found = None if resolve is None else fileresolve.material_textures(m, resolve, owner, m.path)
            materials[k] = _material(configtree.material_fields(m), found and fileresolve.view_textures(found),
                                     configtree.first_values(m.fields))  # fmt: skip
        first = next(iter(materials.values()), _material(configtree.MaterialFields()))
        parts.append({"mesh": mesh, **first, "materials": materials, "lod_materials": configtree.material_lods(node),
                      "deform": _deforms(node), **_placed(node)})  # fmt: skip
    files = [
        {"cfg": f.get("FileName").replace("\\", "/"), "deform": _deforms(f), **_placed(f)}
        for f in tree.section_of("Files")
        if f.get("FileName").lower().endswith(".cfg")
        and (resolve is None or resolve.config(f.get("FileName"), owner, f.path, shared=True))
    ]
    return {"parts": parts, "files": files, "effects": _effects(tree), "decals": _decals(tree, resolve, owner)}


def _decals(tree: configtree.Node, resolve: fileresolve.Resolver | None, owner: str) -> list[dict]:
    """The DECALs a config lays on the ground: their first material's maps, colour and opacity, half sizes (x, z),
    ``Type`` and ``OnStreets`` (``render.decalmesh``) and place; one shown only in a state is left out."""
    out = []
    for node in tree.section_of("Decals"):
        materials = node.section_of("Materials")
        extents = (node.number("Extents.x"), node.number("Extents.z"))
        if not materials or min(extents) <= 0 or not configtree.shown_always(node):
            continue
        m = materials[0]
        found = None if resolve is None else fileresolve.material_textures(m, resolve, owner, m.path)
        fields = configtree.material_fields(m)
        kind, on_streets = decalmesh.read_decal(node)
        out.append({**_material(fields, found and fileresolve.view_textures(found), configtree.first_values(m.fields)),
                    "opacity": fields.opacity,
                    "extents": extents, "kind": kind, "on_streets": on_streets,
                    "texcoords": [node.number(f"TexCoords.{k}", d) for k, d in zip("xyzw", (0, 0, 1, 1), strict=True)],
                    **_placed(node)})  # fmt: skip
    return out


def _numbers(node: configtree.Node, block: str, default: float) -> list[float]:
    """The x, y, z of a structured value (``Color``, ``SubImageSize``)."""
    found = node.blocks.get(block)
    values = {c.tag: c.text for c in found} if found is not None else {}
    out = []
    for axis in "xyz":
        try:
            out.append(float(values.get(axis) or default))
        except ValueError:
            out.append(default)
    return out


def _effects(tree: configtree.Node) -> list[dict]:
    """The particle effects a config places: the file, its placement, colour, opacity and pace. One shown only in
    a state of the object (a visibility mask) or hung on a bone is left out: the view shows no states or poses."""
    out = []
    for p in tree.section_of("Particles"):
        name = p.get("FileName").replace("\\", "/")
        if not name.lower().endswith(".rdp") or not configtree.shown_always(p):
            continue
        scale = p.number("TimeScale", 1.0)
        sheet = [int(v) for v in _numbers(p, "SubImageSize", 1)]  # a flipbook: columns, rows, frames
        out.append({
            "file": name, "color": _numbers(p, "Color", 1.0), "alpha": p.number("Alpha", 1.0),
            "speed": 1.0 / scale if scale > 0 else 1.0, "sheet": sheet if sheet[0] * sheet[1] > 1 else None,
            **_placed(p),
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
