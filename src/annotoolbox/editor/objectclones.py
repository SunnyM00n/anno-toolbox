"""Objects of an island placed again by copying one of its own, in the 117 layout (also 1800's): a building, a slot,
a ruin, a unit or a piece of decoration the island file holds by asset GUID (``guid``, position, turn, ``Variation``,
the blocks the asset's template adds).

An ``added_objects`` entry with ``guid`` and ``clone`` (or the ``cfg`` it draws) is written as a copy of an object of the
same asset on the island, else of one of the same template, else (decoration, or an entry placed from the game's asset
list with ``plain``) of a plain placed object; its place, turn, scale and variation are set, its quest links dropped.
Used by ``formats.a7m.edit117`` and ``formats.a7m.edit1800`` (docs: island-editor "Placed objects")."""

from __future__ import annotations

import struct

from ..formats import filedb
from ..formats.a7mread import area_objects
from ..islands import editorassets, islandedits
from ..islands.islandwater import next_serial
from .templatedonors import CTemplateDonors

Found = tuple[filedb.STag, filedb.STag]  # (the objects list, the first object of that asset)


def samples(island, groups: tuple[str, ...]) -> dict[int, Found]:
    """GUID -> the objects list and the first object of that asset over ``groups`` of the island's areas."""
    found: dict[int, Found] = {}
    for _area, doc, _ids in island._areas_data():
        for group in groups:
            objects_list = area_objects(doc, group)
            for object_tag in objects_list.children if objects_list is not None else []:
                if (
                    isinstance(object_tag, filedb.STag)
                    and object_tag.find("guid") is not None
                    and object_tag.find("Position") is not None
                ):
                    found.setdefault(
                        struct.unpack_from("<I", object_tag.find("guid").data)[0], (objects_list, object_tag)
                    )
    return found


def by_template(found: dict[int, Found], templates: dict[int, str]) -> dict[str, Found]:
    """Template -> an object of the island with an asset of it, for assets the island does not carry; ``""``: a
    plain placed object (a mesh, no prop block), what any decoration can be a copy of."""
    out: dict[str, Found] = {}
    for guid, item in sorted(found.items()):
        if templates.get(guid):  # an unknown template is no key: it would match any entry that has none
            out.setdefault(templates[guid], item)
        if item[1].find("Mesh") is not None and item[1].find("Prop") is None:
            out.setdefault("", item)
    return out


def is_copy(entry: dict) -> bool:
    """An ``added_objects`` entry that places a copy of an object of the island (not an editor-only asset)."""
    return bool(entry.get("guid") and (entry.get("cfg") or entry.get("clone")))


def set_variation(object_tag: filedb.STag, variation: int) -> None:
    """The copy's ``Variation`` (the index into its asset's model list); one the sample does not store (0) is added
    after ``ID`` only for a value other than 0."""
    variation_attrib = object_tag.find("Variation")
    if variation_attrib is not None:
        variation_attrib.data = struct.pack("<I", variation)
    elif variation:
        at = [child.name for child in object_tag.children].index("ID") + 1
        object_tag.children.insert(at, filedb.SAttrib("Variation", struct.pack("<I", variation)))


def _from_donors(donors: CTemplateDonors, entry: dict, home: Found) -> Found | None:
    """A vanilla object of the entry's asset or template from another island, to be added to the island's own objects
    list (``home``'s)."""
    donor = donors.find(int(entry["guid"]), entry.get("template", ""))
    return None if donor is None else (home[0], donor)


def place_copies(
    edits: dict,
    found: dict[int, Found],
    templates: dict[int, str] | None = None,
    plain_templates: tuple[str, ...] = (),
    donors: CTemplateDonors | None = None,
) -> tuple[dict, int, list]:
    """Write the copies of ``edits["added_objects"]`` into the island: of an object of the entry's asset (``found``,
    from ``samples``), else of one of its template (``templates``: GUID -> template), else of one the game's other
    islands carry (``donors``: the blocks of its template), else, for a template of ``plain_templates`` (the game's
    decoration) or an entry with ``plain``, of a plain placed object. Returns the edits without them, how many were
    added and what found nothing to copy."""
    rest, added, missing = [], 0, []
    kinds = by_template(found, templates or {})
    for entry in edits.get("added_objects", []):
        if not is_copy(entry):
            rest.append(entry)
            continue
        sample = found.get(int(entry["guid"]))
        if sample is None and entry.get("template"):
            sample = kinds.get(entry["template"])
        if sample is None and donors is not None and found:
            sample = _from_donors(donors, entry, kinds.get("") or next(iter(found.values())))
        if sample is None and (entry.get("template") in plain_templates or entry.get("plain")):
            sample = kinds.get("")
        if sample is None:
            missing.append(f"guid:{entry['guid']}")
            continue
        objects_list, base = sample
        object_tag = editorassets.clone(
            base, int(entry["guid"]), next_serial(objects_list, base), entry["position"], entry["rotation"]
        )
        islandedits.set_object(
            object_tag, {**{k: entry[k] for k in ("position", "rotation")}, "scale": entry.get("scale", 1)}
        )
        set_variation(object_tag, int(entry.get("variation", 0)))
        objects_list.children.append(object_tag)
        added += 1
    return {**edits, "added_objects": rest}, added, missing
