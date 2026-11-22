"""What placing derives from one widget file alone, once: its element tree, bindings, custom properties and widget
type chain (``root_info``), the defaults its components declare and whether a property ends in a picture. ``scope.CScope``
places a template over these; ``scopevalues`` holds the values it passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..formats import phoenixedit
from . import bindings, elements, fields, placeholders


@dataclass
class SRootInfo:
    """What placing derives from one file alone, once: its element tree, bindings and custom properties."""

    root: dict
    top: elements.SElement
    graph: bindings.SGraph
    by_node: dict[int, elements.SElement]
    children: dict[str, dict]  # instance GUID -> node
    props: list[dict]  # bindings.property_info of each custom property, its widget type chain's after its own
    seeds: dict[str, object] | None = None  # the file's own preview values for lists (listlayout.preview_values)
    atoms: dict[int, int] | None = None  # AtomPropertyId -> UniqueId of those properties (the History Editions)
    types: tuple[tuple[str, int], ...] = ()  # the widget type files read for them: (key, id of the tree)
    subtrees: dict[str, tuple[str, ...]] = field(default_factory=dict)  # instance -> it and the instances below it


def root_info(ctx, root: dict) -> SRootInfo:
    """The ``SRootInfo`` of ``root``, kept in ``ctx.roots`` until the window forgets the file or one of its widget
    types (an edit)."""
    hit = ctx.m_roots.get(id(root))  # pattern: ok (the hit is checked with ``is root``)
    if hit is not None and hit.root is root:
        for key, tree in hit.types:  # what reads them depends on the widget types too
            ctx.m_memo.note(tree)
            ctx.m_memo.note(("file", key))
        return hit
    top = elements.tree(root)
    by_node = {id(e.node): e for e in top.walk()}
    graph = bindings.read(root)
    types, inherited = widget_types(ctx, root)
    props = [*graph.properties, *inherited]
    infos, seen = [], set()
    for entry in props:  # a widget's own declaration of a property wins over its type's
        property_entry = bindings.property_info(entry)
        if property_entry["uid"] not in seen:
            seen.add(property_entry["uid"])
            infos.append(property_entry)
    atoms: dict[int, int] = {}
    for entry in props:  # and its own of an atom id over its type's, as above
        atoms.setdefault(int(fields.get(entry, "AtomPropertyId", 0) or 0), int(fields.get(entry, "UniqueId", 0) or 0))
    atoms.pop(0, None)
    root_record = SRootInfo(root, top, graph, by_node, {e.guid: e.node for e in by_node.values() if e.guid}, infos,
                    atoms=atoms, types=types)  # fmt: skip
    ctx.m_roots[id(root)] = root_record  # pattern: ok (the info holds its root)
    return root_record


def feeds_picture(ctx, root: dict, uid: int, depth: int = 3) -> bool:
    """Whether the property ``uid`` of the file ``root`` ends in a picture's ``DynamicImageId``: a binding writes it
    there, or into a property of a placed file that does (``depth`` files down)."""
    root_data = root_info(ctx, root)
    for binding in root_data.graph.bindings:
        if not any(
            source.ids == [uid] and (source.kind == "own" or (source.kind == "path" and source.mode == bindings.SELF))
            for source in binding.sources
        ):
            continue
        for target in binding.targets:
            if target.kind != "path" or not target.ids:
                continue
            if target.ids[-1] in placeholders.PICTURE_IDS:
                return True
            node = root_data.children.get(target.instance or "") if depth > 0 and len(target.ids) == 1 else None
            placed = ctx.root(fields.get(node, "Template")) if node is not None else None
            if placed is not None and feeds_picture(ctx, placed, target.ids[0], depth - 1):
                return True
    return False


def widget_types(ctx, root: dict) -> tuple[tuple[tuple[str, int], ...], list[dict]]:
    """The widget type files of a widget file (its ``WidgetType`` and that type's parents) and the custom properties
    they declare, which every widget of the type has as its own (bindings read them by their UniqueId)."""
    types, props, key = [], [], fields.get(root, "WidgetType")
    while isinstance(key, str) and key not in {k for k, _ in types}:
        tree = ctx.root(key)
        if tree is None:
            break
        types.append((key, id(tree)))
        props += bindings.custom_properties(tree)
        key = fields.get(tree, "ParentWidgetType")
    return tuple(types), props


def component_default(ctx, node: dict, path: tuple[int, ...]):
    """The default a component's file declares for one of its properties (``path``: the instance's component id, the
    property's id), for an instance that stores no value of it: the state the game starts it in, a UI scale of 1 and
    no gamepad (docs/ui/117/components.md). None for any other path."""
    if len(path) != 2:
        return None
    for component in phoenixedit.objects(node, "Components"):
        if int(fields.get(component, "UniqueId", 0) or 0) == path[0]:
            root = ctx.root(fields.get(component, "ComponentTemplate"))
            property_entry = (
                next((candidate for candidate in root_info(ctx, root).props if candidate["uid"] == path[1]), None)
                if root
                else None
            )
            return (
                property_entry["default"][1]
                if property_entry is not None and isinstance(property_entry["default"], tuple)
                else None
            )
    return None
