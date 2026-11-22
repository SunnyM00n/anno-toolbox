"""The installed Anno 117's shaders as files: the effect, include and material description folders, the shipped
cache's names and headers (its graphics sets and material switches, the compiled shaders it holds), the material
descriptions' switches and how the game builds a material's switch list from a model config.

``CGameShaders(files)`` is the entry (``read_info``, ``global_sets``, ``shipped_of``); ``material_switches`` builds a
variant's switches, ``overlay_reader`` and ``material_fields`` read the files as the game does with a mod folder over
them. The cache names and the rules: docs/rendering/shader-compile.md, docs/engine-rendering.md "Shader compile and
cache".
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import fxeffect, fxmaterial
from ..formats.fnv import fnv
from ..infra import buildcache

SOURCE_DIR = "data/shaders/"
FX_DIR = "data/shaders/fx/"
MATERIAL_DIR = "data/shaders/materials/"
INCLUDE_DIR = "data/shaders/include/"
CACHE_DIR = "data/shaders/cache/windx12/"
# ShaderID of a model config -> the shader file it draws with (engine-rendering "Shaders"; each id checked against
# the shipped cache's variants of the vanilla configs that use it)
SHADER_FILES = {
    0: "cloth",
    1: "decal_pbr",
    5: "model_cutout",
    6: "model_decal",
    7: "model_water",
    8: "model_pbr",
    9: "model_frame_cycle",
    14: "model_icon",
    19: "model_street",
    22: "model_plant",
    28: "model_tech_artist_example",
    29: "model_tech_artist_01",  # the mesh effects (shader117.meshfx_fx)
    30: "model_tech_artist_02",  # the holograms (shader117.hologram_fx)
    31: "model_tech_artist_03",  # the foam falls (fallshader117.fx_text)
    32: "model_tech_artist_04",  # the glow overlays (glow2070.fx_text)
}
GLOBAL_SET_SHADERS = 8  # shaders read for the graphics sets of the shipped cache
SWITCHES_CACHE, SWITCHES_VERSION = "game-shader-switches", 1  # a shipped file's switches kept (``switches``)
# the cache file's tokens: a list starts with its number, an entry with 1, its name and value fields carry a tag
LIST_GLOBAL, LIST_MATERIAL, LIST_END, ENTRY, NAME_TAG, VALUE_TAG = 3, 4, 5, 1, 0x8001, 0x8002

Switches = tuple[tuple[str, str], ...]


def switch_text(switches: Switches) -> str:
    """A switch list as the cache key writes it: ``NAME=VALUE`` joined by ``;``, ``NAME=`` for a switch on."""
    return ";".join(f"{name}={value}" for name, value in switches)


def parse_switch_text(text: str) -> Switches:
    """The inverse of ``switch_text``."""
    return tuple(tuple(item.split("=", 1)) for item in text.split(";") if item)  # type: ignore[misc]


def variant_hash(stem: str, vertex_format: str, global_switches: Switches, material: Switches) -> int:
    """The last part of a variant's cache file name."""
    text = f"{stem}|{vertex_format}|{switch_text(global_switches)}|{switch_text(material)}"
    return fnv(text.encode("utf-8"), 64)


def cache_name(parts: tuple[int, int, int, int]) -> str:
    """The cache file name of a variant: includes, materials, file and variant hash."""
    return "_".join(f"{part:016x}" for part in parts) + ".fxo"


def name_parts(name: str) -> tuple[int, int, int, int] | None:
    """The four hashes of a cache file name, or None for another file."""
    stem = name.rsplit("/", 1)[-1]
    if not stem.lower().endswith(".fxo"):
        return None
    bits = stem[:-4].split("_")
    try:
        return tuple(int(hash_text, 16) for hash_text in bits) if len(bits) == 4 else None  # type: ignore[return-value]
    except ValueError:
        return None


def read_switches(data: bytes) -> tuple[Switches, Switches]:
    """The global and the material switches at the head of a cache file."""
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]  # noqa: E731
    lists: dict[int, list[tuple[str, str]]] = {LIST_GLOBAL: [], LIST_MATERIAL: []}
    current, offset = None, 0
    while offset + 12 <= len(data):
        token = u32(offset)
        if token == LIST_END:
            break
        if token in lists:
            current = lists[token]
        if token != ENTRY or u32(offset + 8) != NAME_TAG:
            offset += 4
            continue
        size = u32(offset + 4)
        name = data[offset + 12 : offset + 12 + size].decode("utf-8")
        offset, value = (offset + 12 + size + 7) & ~7, ""  # fields are padded to 8 bytes from the file's start
        if offset + 8 <= len(data) and u32(offset + 4) == VALUE_TAG:
            size = u32(offset)
            value = data[offset + 8 : offset + 8 + size].decode("utf-8")
            offset = (offset + 8 + size + 7) & ~7
        if current is None:
            raise ValueError("a switch before any list")
        current.append((name, value))
    return tuple(lists[LIST_GLOBAL]), tuple(lists[LIST_MATERIAL])


@dataclass(frozen=True)
class SCachedShader:
    """One compiled shader of a cache file: its entry function, the literal arguments the pass gives it and the
    DXBC container."""

    name: str
    arguments: tuple[str, ...]
    bytecode: bytes


def cache_fields(data: bytes) -> list[tuple[int, bytes]]:
    """Every (tag, bytes) field of a cache file in order: ``u32 size, u32 tag`` then the bytes, padded to 8 from the
    file's start. The tags above ``NAME_TAG`` are numbered by first use in each file, so only their content tells
    them apart."""
    u32 = lambda offset: struct.unpack_from("<I", data, offset)[0]  # noqa: E731
    out, offset = [], 0
    while offset + 8 <= len(data):
        size, tag = u32(offset), u32(offset + 4)
        if NAME_TAG <= tag <= NAME_TAG + 0xFE and offset + 8 + size <= len(data):
            out.append((tag, data[offset + 8 : offset + 8 + size]))
            offset = (offset + 8 + size + 7) & ~7
        else:
            offset += 4
    return out


def read_cached_shaders(data: bytes) -> list[SCachedShader]:
    """The compiled shaders of a cache file, in its order: each record is the entry name, the pass's literal
    arguments (text fields), its type (u32 fields), the bytecode and source hashes, then the bytecode."""
    out, name, rest = [], None, []
    for tag, value in cache_fields(data):
        if value[:4] == b"DXBC":
            if name is not None:  # the two fields before the bytecode are its hashes
                arguments = tuple(
                    field_value.decode("ascii")
                    for field_value in rest[:-2]
                    if field_value and all(0x20 <= byte < 0x7F for byte in field_value)
                )
                out.append(SCachedShader(name, arguments, value))
            name, rest = None, []
        elif tag == NAME_TAG:
            name, rest = value.decode("utf-8", "replace"), []
        elif name is not None:
            rest.append(value)
    return out


_PASS_FILE_RE = re.compile(rb"(\w+)\.fx \| \w")


def pass_file_stem(data: bytes) -> str | None:
    """The shader file a cache file was compiled from, by its passes' names (``<file>.fx | <technique>``)."""
    match = _PASS_FILE_RE.search(data)
    return match.group(1).decode() if match else None


@dataclass
class SDefine:
    """One switch of a material description."""

    name: str
    kind: str  # bool or int
    default: str
    required: tuple[str, ...] = ()  # material options it needs
    after: str = ""  # a switch that must be on for this one to count


@dataclass
class SSection:
    name: str
    required: tuple[str, ...]
    defines: list[SDefine] = field(default_factory=list)


@dataclass
class SMaterial:
    """A material description (``.fxm``): its options and sections, in file order."""

    name: str
    options: tuple[str, ...]
    sections: list[SSection]


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _required(text: str) -> tuple[str, ...]:
    match = re.match(r"\s*\[\s*required\s*:([^\]]*)\]", text)
    return tuple(name.strip() for name in match.group(1).split(",") if name.strip()) if match else ()


def _block(text: str, start: int) -> tuple[str, int]:
    """The body of the ``{...}`` opening at or after ``start`` and the index after it."""
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[i + 1 : j], j + 1
    raise ValueError("an unclosed block")


def parse_material(text: str) -> SMaterial:
    """A ``.fxm``: the material's name, its options and every switch (``define``) of its sections."""
    text = _strip_comments(text)
    head = re.match(r"\s*material\s+(\w+)", text)
    if not head:
        raise ValueError("no material header")
    body, _ = _block(text, head.end())
    options = tuple(re.findall(r"\[\s*option\s*:\s*(\w+)\s*\]", text[head.end() : text.index("{", head.end())]))
    sections = []
    for match in re.finditer(r"\bsection\s+(\w+)", body):
        inner, _end = _block(body, match.end())
        section = SSection(match.group(1), _required(body[match.end() :]))
        for define_match in re.finditer(r"\bdefine\s+(\w+)", inner):
            if re.match(
                r"\s*=", inner[define_match.end() :]
            ):  # "define = X" inside an item: a dependency, not a switch
                continue
            item, _ = _block(inner, define_match.end())
            kind = re.search(r"\btype\s*=\s*(\w+)", item)
            default = re.search(r"\bdefault\s*=\s*([^,\n]+)", item)
            after = re.search(r"\bdefine\s*=\s*(\w+)", item)
            section.defines.append(
                SDefine(
                    define_match.group(1),
                    kind.group(1) if kind else "bool",
                    default.group(1).strip() if default else "false",
                    _required(inner[define_match.end() :]),
                    after.group(1) if after else "",
                )
            )
        sections.append(section)
    return SMaterial(head.group(1), options, sections)


def read_material_references(fx_text: str) -> list[tuple[str, tuple[str, ...]]]:
    """The material descriptions a ``.fx`` references, each with the options it switches on, in order; a description
    named twice (a slot file with the footer written into it) counts once."""
    text = _strip_comments(fx_text)
    refs: dict[str, tuple[str, ...]] = {}
    for match in re.finditer(r"\bMaterialRef\s+(\w+)\s*(?:\[([^\]]*)\])?\s*;", text):
        refs.setdefault(
            match.group(1), tuple(option.strip() for option in (match.group(2) or "").split(",") if option.strip())
        )
    return list(refs.items())


def _truthy(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "", "0.0")


def cfg_values(block: str) -> dict[str, str]:
    """The parameters of a model config's material block, by name (the first of a name wins)."""
    values: dict[str, str] = {}
    for tag, value in re.findall(r"<([A-Za-z_][A-Za-z_0-9.]*)>([^<]*)</\1>", block):
        values.setdefault(tag, value)
    return values


def material_switches(
    refs: list[tuple[str, tuple[str, ...]]], materials: dict[str, SMaterial], values: dict[str, str]
) -> Switches:
    """A material's switches as the game builds them: the bone count, the options the ``.fx`` names, then every
    switch of the referenced descriptions that is on (an int one with its value), in description order."""
    out = [("NUM_BONES_PER_VERTEX", str(int(float(values.get("NumBonesPerVertex", "0") or 0))))]
    out += [(option, "") for _name, options in refs for option in options]
    enabled: set[str] = set()
    for ref, options in refs:
        material = materials.get(ref)
        if material is None:
            continue
        for section in material.sections:
            if not set(section.required) <= set(options):
                continue
            for define in section.defines:
                if not set(define.required) <= set(options) or (define.after and define.after not in enabled):
                    continue
                value = values.get(define.name, define.default)
                if define.kind == "bool":
                    if _truthy(value):
                        enabled.add(define.name)
                        out.append((define.name, ""))
                else:
                    enabled.add(define.name)
                    out.append((define.name, str(int(float(value)))))
    return tuple(out)


def cfg_materials(text: str) -> list[tuple[int, str, dict[str, str]]]:
    """The materials of a model config: (ShaderID, vertex format, parameters)."""
    found = []
    for block in re.findall(r"<Config>\s*<ConfigType>MATERIAL</ConfigType>.*?</Config>", text, re.S):
        values = cfg_values(block)
        if "ShaderID" in values:
            found.append((int(values["ShaderID"]), values.get("VertexFormat", ""), values))
    return found


@dataclass
class SShaderInfo:
    """What the list needs of one shader file of the install."""

    stem: str
    references: list[tuple[str, tuple[str, ...]]]
    vanilla_hash: int


class CGameShaders:
    """The installed game's shaders: sources, material descriptions, the shipped cache's names and headers."""

    def __init__(self, files) -> None:
        self.m_files = files  # sources.anno117.CAnno117Files or anything with read / iter_paths
        self.m_materials: dict[str, SMaterial] = {}
        for path in files.iter_paths(MATERIAL_DIR):
            if path.lower().endswith(".fxm"):
                try:
                    description = parse_material(files.read(path).decode("utf-8", "replace"))
                except ValueError:
                    continue
                self.m_materials[description.name] = description
        self.m_by_file: dict[int, list[str]] = {}  # file hash -> shipped cache paths
        self.m_prefix: tuple[int, int] | None = None  # the includes and materials hash every shipped name starts with
        for path in files.iter_paths(CACHE_DIR):
            parts = name_parts(path)
            if parts:
                self.m_by_file.setdefault(parts[2], []).append(path)
                self.m_prefix = self.m_prefix or (parts[0], parts[1])
        self._m_info: dict[str, SShaderInfo | None] = {}
        self._m_switches: dict[str, tuple[Switches, Switches]] = {}  # a shipped file's, read once (``switches``)
        self._m_global_sets: list[Switches] | None = None
        self._m_stale: dict[str, int] | None = None

    def read_info(self, stem: str) -> SShaderInfo | None:
        if stem not in self._m_info:
            try:
                data = self.m_files.read(f"{FX_DIR}{stem}.fx")
            except FileNotFoundError:
                self._m_info[stem] = None
            else:
                refs = read_material_references(data.decode("utf-8", "replace"))
                self._m_info[stem] = SShaderInfo(stem, refs, fnv(data, 64))
        return self._m_info[stem]

    def shipped(self, file_hash: int, limit: int | None = None) -> list[tuple[str, Switches, Switches]]:
        """(path, global switches, material switches) of the shipped cache files of a shader file (``limit``: at
        most that many)."""
        return [(path, *self.switches(path)) for path in self.m_by_file.get(file_hash, [])[:limit]]

    def switches(self, path: str) -> tuple[Switches, Switches]:
        """``read_switches`` of a shipped cache file, kept in the build cache under its name: the name is the hash of
        what the file was compiled from (shader-compile.md 5), so a later start reads no cache file for them (each
        read from the archives takes tens of milliseconds)."""
        found = self._m_switches.get(path)
        if found is not None:
            return found
        key = buildcache.digest(SWITCHES_VERSION, path)
        kept = buildcache.get(SWITCHES_CACHE, key)
        if kept is not None:
            pair = json.loads(kept)
            found = tuple(map(tuple, pair[0])), tuple(map(tuple, pair[1]))
        else:
            found = read_switches(self.m_files.read(path))
            buildcache.put(SWITCHES_CACHE, key, json.dumps(found).encode("utf-8"))
        return self._m_switches.setdefault(path, found)

    def stale(self) -> dict[str, int]:
        """Shader files whose shipped cache was compiled from another revision of their source (its files carry a
        file hash no installed source has): stem -> that file hash. The game cannot load those files and compiles
        such a shader itself (shader-compile.md, "The caches"); their define lists are still the engine's."""
        if self._m_stale is None:
            paths = [path for path in self.m_files.iter_paths(SOURCE_DIR) if path.lower().endswith((".fx", ".fxh"))]
            sources = {
                fnv(self.m_files.read(path), 64) for path in paths
            }  # the engine loads some headers as effects too
            self._m_stale = {}
            for key, cached in sorted(self.m_by_file.items()):
                if key not in sources:
                    stem = pass_file_stem(self.m_files.read(cached[0]))
                    if stem:
                        self._m_stale.setdefault(stem, key)
        return self._m_stale

    def shipped_of(self, stem: str, file_hash: int, limit: int | None = None) -> list[tuple[str, Switches, Switches]]:
        """``shipped`` of a shader file, or of its stale cache files when none has its hash."""
        found = self.shipped(file_hash, limit)
        return found or self.shipped(self.stale().get(stem, 0), limit)

    def global_sets(self) -> list[Switches]:
        """The global switch sets the shipped cache was built for (one per graphics combination it covers): those
        of the shaders with the fewest files, which have one variant per set."""
        if self._m_global_sets is None:
            smallest = sorted((len(files), file_hash) for file_hash, files in self.m_by_file.items())[
                :GLOBAL_SET_SHADERS
            ]
            # kept as one entry under the files' names: one read at a later start, not one per file
            key = buildcache.digest(
                SWITCHES_VERSION,
                "global sets",
                *(path for _file_count, file_hash in smallest for path in self.m_by_file[file_hash]),
            )
            kept = buildcache.get(SWITCHES_CACHE, key)
            if kept is not None:
                self._m_global_sets = [tuple(map(tuple, graphics_set)) for graphics_set in json.loads(kept)]
            else:
                self._m_global_sets = sorted(
                    {
                        graphics_set
                        for _file_count, file_hash in smallest
                        for _path, graphics_set, _material in self.shipped(file_hash)
                    }
                )
                buildcache.put(SWITCHES_CACHE, key, json.dumps(self._m_global_sets).encode("utf-8"))
        return self._m_global_sets

    def shipped_variants(self, stem: str, file_hash: int, vertex_formats: set[str]) -> list[tuple[str, Switches]]:
        """The (vertex format, material switches) of a shader's shipped cache, each once; the vertex format is not
        in the file, it is the candidate whose key reproduces the file's name."""
        found: dict[tuple[str, Switches], None] = {}
        candidates = sorted(vertex_formats) + [""]
        for path, global_switches, material_switches in self.shipped_of(stem, file_hash):
            want = name_parts(path)[3]
            vertex_format = next(
                (
                    candidate
                    for candidate in candidates
                    if variant_hash(stem, candidate, global_switches, material_switches) == want
                ),
                None,
            )
            if vertex_format is not None:
                found.setdefault((vertex_format, material_switches), None)
        return list(found)


# what a graphics set needs that the viewer's frame does not make: ray tracing (QRhi has no ray-tracing API, so no
# acceleration structure), screen-space reflections, local-light shadows, screen-space ambient occlusion
VIEW_NEEDS = ("ENABLE_RTGI", "ENABLE_RAYTRACED_REFLECTIONS", "ENABLE_SSR", "ENABLE_LOCAL_SHADOWS")


def view_graphics_set(game: CGameShaders) -> Switches:
    """The shipped graphics set the viewer can feed: one sample a pixel, no multisampling, and the fewest inputs
    it cannot make (``VIEW_NEEDS``; SSAO sampling off): the game's own fallback without ray tracing. ``ValueError``
    when the cache has none."""
    sets = [
        graphics_set
        for graphics_set in game.global_sets()
        if ("SHADER_AA_SAMPLES", "1") in graphics_set and all(name != "MSAA_ENABLED" for name, _ in graphics_set)
    ]
    if not sets:
        raise ValueError("the shipped cache has no graphics set with one sample a pixel and no multisampling")

    def cost(graphics_set) -> tuple:
        names = {name for name, _ in graphics_set}
        return (sum(name in names for name in VIEW_NEEDS), "DISABLE_SSAO_SAMPLING" not in names)

    return min(sets, key=cost)


def overlay_reader(mod_root: Path | None, files) -> fxeffect.Read:
    """A file as the game reads it with the mod: the mod's copy first, then the install's (no mod: the install's)."""

    def read(path: str) -> bytes | None:
        own = mod_root / path if mod_root else None
        if own is not None and own.is_file():
            return own.read_bytes()
        return files.read(path) if files.exists(path) else None

    return read


def material_fields(mod_root: Path | None, files) -> list[fxmaterial.SMaterialFields]:
    """Every material description the game reads with the mod (a mod's ``.fxm`` replaces the install's)."""
    paths = {path.lower(): path for path in files.iter_paths(MATERIAL_DIR) if path.lower().endswith(".fxm")}
    own = mod_root / MATERIAL_DIR if mod_root else None
    for path in sorted(own.glob("*.fxm")) if own is not None and own.is_dir() else []:
        paths[f"{MATERIAL_DIR}{path.name}".lower()] = f"{MATERIAL_DIR}{path.name}"
    read = overlay_reader(mod_root, files)
    out = []
    for path in sorted(paths.values()):
        try:
            out.append(fxmaterial.read_material_fields((read(path) or b"").decode("utf-8", "replace")))
        except fxmaterial.CMaterialError:
            continue
    return out
