"""117's compiled shader cache file (``.fxo``, one variant of an effect): a FileDB document whose ``EffectFile`` holds
the variant's switch lists, its compiled shaders, the render state blocks, the resources and constant buffers, and the
techniques with their passes' bindings and root signatures (docs/rendering/shader-compile.md section 5).

``CFxoFile.read(data)`` is the entry; ``shaders``, ``techniques`` and ``blocks`` read its parts, ``assemble`` makes
another variant of the effect from parts of others, ``write`` gives the bytes again (a file read and written unchanged
is the same bytes). ``bytecode_hash`` is the engine's hash of a compiled shader.
"""

from __future__ import annotations

import copy
import struct
from dataclasses import dataclass

from . import filedb
from .fnv import fnv

ROOT = "EffectFile"
ITEM = "#1"  # the list items' tag: id 1, which the name table leaves unnamed


def bytecode_hash(data: bytes) -> int:
    """A shader's ``ByteCodeHash`` (and, over its source text, its ``SourceHash``): FNV-1a 64 of the bytes taken as
    signed chars."""
    return fnv(data, 64, is_signed=True)


def engine_name_ids(root: filedb.STag) -> tuple[dict[str, int], dict[str, int]]:
    """The name tables the engine writes for ``root``: each name's id in the order of first use (tags from 2,
    attributes from 0x8001; the list items' ``#`` ids are fixed), the table in the iteration order of the hash map
    it keeps them in (``hash_map_order``; held against every shipped file tried, shader-compile.md section 5)."""
    tags: list[str] = []
    attrs: list[str] = []
    seen: set[tuple[bool, str]] = set()
    for node in root.iter():
        if node is root or node.name.startswith("#"):
            continue
        is_tag = isinstance(node, filedb.STag)
        if (is_tag, node.name) not in seen:
            seen.add((is_tag, node.name))
            (tags if is_tag else attrs).append(node.name)
    tag_ids = {name: 2 + k for k, name in enumerate(tags)}
    attr_ids = {name: 0x8001 + k for k, name in enumerate(attrs)}
    return (
        {name: tag_ids[name] for name in hash_map_order(tags)},
        {name: attr_ids[name] for name in hash_map_order(attrs)},
    )


def hash_map_order(keys: list[str]) -> list[str]:
    """The iteration order of Microsoft's ``std::unordered_map`` after inserting ``keys`` in order, hashed by FNV-1a
    32: 8 buckets to start, eight times more (then twice) when a key would pass one per bucket; a new key goes to the
    front of its bucket's run, or to the end of the list for an empty bucket; a rehash walks the list and moves each
    key to the end of its bucket's run."""
    order: list[str] = []
    buckets = 8

    def bucket(k: str) -> int:
        return fnv(k.encode("utf-8"), 32) & (buckets - 1)

    for key in keys:
        if len(order) + 1 > buckets:
            want = len(order) + 1
            grown = buckets * 8 if buckets < 512 and buckets * 8 >= want else want
            buckets = 1 << (grown - 1).bit_length()
            old, order = order, []
            for k in old:
                last = max((i for i, x in enumerate(order) if bucket(x) == bucket(k)), default=None)
                order.insert(len(order) if last is None else last + 1, k)
        first = next((i for i, x in enumerate(order) if bucket(x) == bucket(key)), None)
        order.insert(len(order) if first is None else first, key)
    return order


@dataclass
class SShader:
    """One compiled shader of the file: its entry function, the pass's literal arguments as one text (``2true`` for
    ``(2, true)``), its source hash and the DXBC container."""

    name: str
    args: str
    source_hash: int
    bytecode: bytes


class CFxoFile:
    """A cache file as its FileDB tree, with the name ids it was read with (so it writes back as it was)."""

    def __init__(self, root: filedb.STag, tag_ids: dict[str, int], attr_ids: dict[str, int]) -> None:
        self.m_root, self.m_tag_ids, self.m_attr_ids = root, tag_ids, attr_ids

    @classmethod
    def read(cls, data: bytes) -> CFxoFile:
        root = filedb.read(data)
        if root.find(ROOT) is None:
            raise ValueError("not a shader cache file (no EffectFile)")
        return cls(root, *filedb.name_ids(data))

    def write(self) -> bytes:
        return filedb.write(self.m_root, self.m_tag_ids, self.m_attr_ids)

    @property
    def effect(self) -> filedb.STag:
        return self.m_root.find(ROOT)

    def switches(self, section: str) -> tuple[tuple[str, str], ...]:
        """``GlobalDefines`` or ``LocalDefines`` as (name, value) pairs; a switch without a value has ``""``."""
        out = []
        for item in self.effect.find(section).children:
            name, value = item.find("Name"), item.find("Value")
            out.append((name.data.decode("utf-8") if name else "", value.data.decode("utf-8") if value else ""))
        return tuple(out)

    def shaders(self) -> list[SShader]:
        out = []
        for item in self.effect.find("Shaders").children:
            values = item.find("Values")
            arguments = values.data.decode("utf-8") if values is not None else ""
            source = struct.unpack("<Q", item.find("SourceHash").data)[0]
            out.append(SShader(item.find("Name").data.decode("utf-8"), arguments, source, item.find("ByteCode").data))
        return out

    def entries(self) -> dict[int, filedb.STag]:
        """The ``Shaders`` entries by source hash."""
        return {_u64(item.find("SourceHash").data): item for item in self.effect.find("Shaders").children}

    def techniques(self) -> dict[str, filedb.STag]:
        return _by_name(self.effect.find("Techniques"))

    def blocks(self) -> dict[str, filedb.STag]:
        """The render state blocks by name."""
        return _by_name(self.effect.find("RenderStateBlocks"))

    def assemble(self, local, shaders: list[tuple[filedb.STag, int, bytes]], blocks: dict, techniques: dict) -> None:
        """Make this file (the base: its switch lists, resources and constant buffers stay) another variant of its
        effect: ``local`` its material switches; ``shaders`` (an entry of another file to copy, source hash,
        bytecode) in order; ``blocks`` name -> a state block entry; ``techniques`` name -> (a technique entry, its
        shaders' source hashes in the order ``refs`` gives them). The parts come from files the caller has held
        against the new variant (``models.shaderprebuild``)."""
        self.effect.find("LocalDefines").children = [_define(name, value) for name, value in local]
        made = []
        for entry, source, bytecode in shaders:
            item = copy.deepcopy(entry)
            _set(item, "SourceHash", struct.pack("<Q", source))
            _set(item, "ByteCodeHash", struct.pack("<Q", bytecode_hash(bytecode)))
            _set(item, "ByteCode", bytecode)
            made.append(item)
        self.effect.find("Shaders").children = made
        section = self.effect.find("RenderStateBlocks")
        section.children = [copy.deepcopy(blocks[_name(block)]) for block in section.children]
        section = self.effect.find("Techniques")
        section.children = [_moved(*techniques[_name(technique_block)]) for technique_block in section.children]


def refs(technique: filedb.STag) -> list[filedb.SAttrib]:
    """The ``SourceHash`` of each shader of a technique's passes, in order."""
    return [
        shader.find("SourceHash") for pass_block in technique.find("Passes").children for shader in pass_block.find("Shaders").children
    ]  # fmt: skip


def ref_names(technique: filedb.STag) -> list[str]:
    """The entry function of each shader of a technique's passes, in ``refs``' order."""
    return [
        shader.find("Name").data.decode("utf-8") for pass_block in technique.find("Passes").children for shader in pass_block.find("Shaders").children
    ]  # fmt: skip


def _moved(technique: filedb.STag, sources: list[int]) -> filedb.STag:
    """A copy of a technique whose passes name the shaders ``sources``."""
    item = copy.deepcopy(technique)
    found = refs(item)
    if len(found) != len(sources):
        raise ValueError(f"{len(sources)} shaders for a technique of {len(found)}")
    for attrib, source in zip(found, sources, strict=True):
        attrib.data = struct.pack("<Q", source)
    return item


def _u64(data: bytes) -> int:
    return struct.unpack("<Q", data)[0]


def _name(item: filedb.STag) -> str:
    return item.find("Name").data.decode("utf-8")


def _by_name(section: filedb.STag) -> dict[str, filedb.STag]:
    return {_name(item): item for item in section.children}


def _set(item: filedb.STag, name: str, data: bytes) -> None:
    item.find(name).data = data


def _define(name: str, value: str) -> filedb.STag:
    """A switch as the lists hold it: its name, and its value unless it has none."""
    item = filedb.STag(ITEM, [filedb.SAttrib("Name", name.encode("utf-8"))])
    if value:
        item.children.append(filedb.SAttrib("Value", value.encode("utf-8")))
    return item
