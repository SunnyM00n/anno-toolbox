"""The baked data file (``.bfg``) of a 117 model config kept in step with an edited config: its entries follow the
config's child configs by position, so a part inserted, removed or moved needs the same change in the ``.bfg``.

``reindex(base, new, bfg)`` is the entry: the ``.bfg`` of the edited config built from the game's (the baked content of
the parts that stayed, none for new ones, the far-view model dropped); ``mismatches(doc, bfg)`` says where a ``.bfg``
does not line up with its config. The format and what was measured: docs/engine-rendering.md "What a .bfg holds".
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass, field

from ..formats import filedb
from ..formats.filedb import SAttrib, STag
from . import cfgpatch
from .cfgdoc import SDoc, SNode, is_config, kind_of, strip

CONFIG_TYPES = {"MAIN": 9, "FILE": 1, "MODEL": 2, "CLOTH": 3, "PARTICLE": 4, "DECAL": 5, "LIGHT": 6, "COLLISION": 7,
                "SEQUENCE": 8}  # fmt: skip
UNLISTED = ("Transformer", "Materials", "Animations")  # lists of the text config that have no entries
FIELD_ORDER = ("ConfigType", "NumDamageImpacts", "DamageImpacts", "LowPolyData", "Files", "Models", "Decals",
               "Sequences", "Clothes", "Particles", "Lights", "ProbeGrid", "Collisions")  # fmt: skip
LISTS = ("Files", "Models", "Decals", "Sequences", "Clothes", "Particles", "Lights", "Collisions")  # lists of entries
NO_FAR_VIEW = {"Vertices": b"", "Indices": b"", "BoxMin": struct.pack("<3f", *[3.4028234663852886e38] * 3),
               "BoxMax": struct.pack("<3f", *[-3.4028234663852886e38] * 3), "Flags": struct.pack("<I", 8)}  # fmt: skip


@dataclass
class SReindexed:
    """The new ``.bfg`` (``data``), whether its lists changed against the game's (``changed``) and what could not be
    done (``problems``: then ``data`` is empty)."""

    data: bytes = b""
    changed: bool = False
    problems: list[str] = field(default_factory=list)


def child_lists(node: SNode) -> dict[str, list[SNode]]:
    """The lists of child configs of a text config the ``.bfg`` has entries for: name -> configs."""
    return {
        strip(child.name): child.children
        for child in node.children
        if child.children
        and all(is_config(grandchild) for grandchild in child.children)
        and strip(child.name) not in UNLISTED
    }


def _type_of(entry: STag) -> int | None:
    found = entry.find("ConfigType")
    return struct.unpack("<I", found.data)[0] if isinstance(found, SAttrib) and len(found.data) == 4 else None


def _entries(tag: STag | None) -> list[STag]:
    return [child for child in tag.children if isinstance(child, STag)] if tag is not None else []


def mismatches(doc: SDoc, bfg: bytes) -> list[str]:
    """Where a ``.bfg`` does not line up with its text config (a list of another length, a ``ConfigType`` another
    kind has): empty when it does."""
    try:
        top = filedb.read(bfg).find("Config")
    except (ValueError, struct.error, IndexError) as exc:
        return [f"the .bfg does not read: {exc}"]
    if not isinstance(top, STag):
        return ["the .bfg has no Config"]
    out: list[str] = []
    _check(doc.parts().node, top, "", out)
    return out


def _check(node: SNode, entry: STag, where: str, out: list[str]) -> None:
    want = CONFIG_TYPES.get(kind_of(node))
    if want is not None and _type_of(entry) != want:
        out.append(f"{where or '/'}: ConfigType {_type_of(entry)}, the config is {kind_of(node)} ({want})")
    lists = child_lists(node)
    for name, configs in lists.items():
        entries = _entries(entry.find(name))
        if len(entries) != len(configs):
            out.append(f"{where}/{name}: {len(entries)} entries for {len(configs)} configs")
            continue
        for k, (config, sub) in enumerate(zip(configs, entries, strict=True)):
            _check(config, sub, f"{where}/{name}/{k}", out)
    out += [f"{where}/{child.name}: entries for configs the text has no list of" for child in entry.children
            if isinstance(child, STag) and child.name not in lists and child.name in LISTS and _entries(child)]  # fmt: skip


def blank_entry(kind: str) -> STag | None:
    """An entry for a part that has no baked content (None for a kind with no entries): the ``ConfigType``, and the
    two empty fields every shipped model entry has."""
    number = CONFIG_TYPES.get(kind)
    if number is None:
        return None
    entry = STag("Config", [SAttrib("ConfigType", struct.pack("<I", number))])
    if kind == "MODEL":
        entry.children += [SAttrib("EntityTransforms", b""), SAttrib("EntityColors", b"")]
    return entry


def no_far_view() -> STag:
    """``LowPolyData`` as the shipped files say "no merged far-view model": empty geometry, flag 8."""
    return STag("LowPolyData", [
        SAttrib("Vertices", NO_FAR_VIEW["Vertices"]), SAttrib("Indices", NO_FAR_VIEW["Indices"]), STag("TextureSets"),
        STag("Materials"), SAttrib("BoxMin", NO_FAR_VIEW["BoxMin"]), SAttrib("BoxMax", NO_FAR_VIEW["BoxMax"]),
        SAttrib("Flags", NO_FAR_VIEW["Flags"]),
    ])  # fmt: skip


def reindex(base: SDoc, new: SDoc, bfg: bytes) -> SReindexed:
    """The ``.bfg`` of ``new`` (an edited text config) from ``bfg`` (the game's, of ``base``): each list of entries
    lined up with the list of configs as a patch lines the parts up. Nothing to write (``changed`` False, ``data``
    the game's bytes) when every list stayed as it was."""
    try:
        document = filedb.read(bfg)
        tags, attribute_ids = filedb.name_ids(bfg)
        version = filedb.version(bfg)
    except (ValueError, struct.error, IndexError) as exc:
        return SReindexed(problems=[f"the game's .bfg does not read: {exc}"])
    top = document.find("Config")
    if not isinstance(top, STag):
        return SReindexed(problems=["the game's .bfg has no Config"])
    lines_up = mismatches(base, bfg)
    if lines_up:
        return SReindexed(problems=[f"the game's .bfg does not line up with its config: {lines_up[0]}"])
    problems: list[str] = []
    changed = _align(base.parts().node, new.parts().node, top, problems)
    if problems:
        return SReindexed(problems=problems)
    if not changed:
        return SReindexed(bfg, False)
    _drop_far_view(top)
    return SReindexed(filedb.write(document, tags, attribute_ids, version), True)


def _align(old: SNode, new: SNode, entry: STag, problems: list[str]) -> bool:
    """Put ``entry``'s lists in step with ``new``'s child lists (``old`` is the game's); whether any changed."""
    old_lists, new_lists = child_lists(old), child_lists(new)
    changed = False
    for name in {*old_lists, *new_lists}:
        olds, news = old_lists.get(name, []), new_lists.get(name, [])
        tag = entry.find(name)
        entries = _entries(tag)
        place = {id(child): k for k, child in enumerate(olds)}
        made: list[STag] = []
        order: list[int | None] = []
        for before, after in cfgpatch.pairs(olds, news, kind_of):
            if after is None:
                continue
            order.append(place[id(before)] if before is not None else None)
            sub = copy.deepcopy(entries[place[id(before)]]) if before is not None else blank_entry(kind_of(after))
            if sub is None:
                problems.append(f"{name}: a {kind_of(after)} has no kind of entry in a .bfg")
                continue
            changed |= before is not None and _align(before, after, sub, problems)
            made.append(sub)
        changed |= order != list(range(len(olds)))
        _put_list(entry, name, tag, made)
    return changed


def _put_list(entry: STag, name: str, tag: STag | None, made: list[STag]) -> None:
    """The list ``name`` of ``entry`` set to ``made`` (made when it is new, taken out when nothing is left)."""
    if tag is not None:
        if made:
            tag.children = made
        else:
            entry.children.remove(tag)
    elif made:
        rank = FIELD_ORDER.index(name) if name in FIELD_ORDER else len(FIELD_ORDER)
        later = (
            i
            for i, child in enumerate(entry.children)
            if child.name in FIELD_ORDER and FIELD_ORDER.index(child.name) > rank
        )
        entry.children.insert(next(later, len(entry.children)), STag(name, made))


def _drop_far_view(top: STag) -> None:
    """The edited config's merged far-view model (when it has geometry) no longer matches its parts: say there is none."""
    for k, child in enumerate(top.children):
        vertices = child.find("Vertices") if child.name == "LowPolyData" and isinstance(child, STag) else None
        if isinstance(vertices, SAttrib) and vertices.data:
            top.children[k] = no_far_view()
