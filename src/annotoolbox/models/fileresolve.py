"""Which file a game loads for a name a model config writes: the one set of rules the viewer, the island editor and
the files check (``viewer.filecheck``) find meshes, textures, nested configs and effects by, and why a name finds none.

``Resolver(files, game)`` is the entry: ``texture``, ``mesh``, ``config``, ``effect`` turn a name into the game path
it loads ("" for none) and keep every lookup as a ``Ref`` (``refs``, ``missing``); ``texture_file`` and friends are
the same rules over a bare ``exists`` (``loaded_file``: texture or mesh). ``decode`` reads a texture to RGBA or says why it cannot, ``MISSING_RGBA`` is
the checker a view draws for a file that is not there. The rules and their sources: docs/viewer.md "How files are
found".
"""

from __future__ import annotations

import posixpath
import re
import weakref
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import numpy as np

from ..othergames import installs
from ..sources.overlay import normalize
from . import configtree

NEW_ENGINE = installs.keys(lambda g: g.family == installs.NEW)  # the source name -> _0.dds family (configtree's games)
SOURCE_TEXTURE = re.compile(r"\.(psd|png|tga|dds|bmp|jpg)$")
QUALITY_LEVELS = (1, 2, 3, 4)  # _1.dds ... _4.dds: the same texture at half, quarter ... size (lower quality)

# why a name finds no file (the reasons the files check and the Checks panels count)
ABSENT = "absent"  # no archive of the game carries the file or any name a rule makes of it
DELETED = "deleted by the game"  # the archive map takes it away (117)
NOT_SHIPPED = "folder not shipped"  # no file at all lies in that folder: the developers' test or tool folders
PLACEHOLDER = "placeholder"  # the studio's stand-in for no map (default_*, failure_texture): drawn without one
ELSEWHERE = "absent here, the same name ships elsewhere"  # a stale path: the file moved, the config did not
EMPTY_REASONS = (PLACEHOLDER,)  # not a failure: the game draws its default, the view draws no map
# the studio's stand-ins for "no map": its tools' default and failure textures, 1701's "leer" (German: empty)
PLACEHOLDERS = re.compile(r"(^|/)(default_[^/]*|failure_texture)\.\w+$|^(leer|none|empty)$")

# A newer game's texture slot -> the switch that turns it on (117's material descriptions, its
# data/shaders/materials/*.fxm; 1800 and 2205 write the same fields): rendering/material-fields.md. A slot not
# listed has no switch; a missing switch is off, but for the two the view has always read as on.
SPLAT = ("cModelSplatMap", *(f"cModelSplat{k}Tex{c}" for k in ("Diff", "Normal") for c in "RGBA"))
VAT = ("cVtxAnimPosTex", "cVtxAnimPosTex2", "cVtxAnimColorTex", "cVtxAnimNormalTex", "cVtxAnimRotTex",
       "cPositionTexture", "cPositionTexture2", "cRotationTexture", "cColorTexture", "cSpareColorTexture")  # fmt: skip
SLOT_SWITCHES = {
    "cModelDiffTex": "DIFFUSE_ENABLED", "cModelNormalTex": "NORMAL_ENABLED", "cClothNormalTex": "NORMAL_ENABLED",
    "cNormalTexture": "NORMAL_MAP_ENABLED", "cModelMetallicTex": "METALLIC_TEX_ENABLED",
    "cDyeMask": "DYE_MASK_ENABLED", "cClothDyeMask": "DYE_MASK_ENABLED", "cMultiDyeColorVariationTex": "MULTI_DYE_ENABLED",
    "cHeightMap": "HEIGHT_MAP_ENABLED", "cAlphaTex": "ALPHA_TEXTURE_ENABLED", "cNightGlowMap": "NIGHT_GLOW_ENABLED",
    "cWindRippleTex": "WIND_RIPPLES_ENABLED", "cClothRippleTex": "RIPPLES_ENABLED",
    "c2ndModelDiffTex": "MATERIAL_BLEND_ENABLED", "c2ndModelNormalTex": "MATERIAL_BLEND_NORMAL_MAP_ENABLED",
    "cPropTintTex": "PROP_TINT_ENABLED", "cWaterFlowTex": "FLOW_MAP_ENABLED", "cWaterDetailTex": "DETAIL_MAP_ENABLED",
    "cWaterDetailNormTex": "DETAIL_MAP_ENABLED", "cGlossTex": "GLOSS_TEX_ENABLED",
    "cDiffuseRampTex": "cEnableDiffuseRamp", "cEmissiveRampTex": "cEnableEmissiveRamp",
    **dict.fromkeys(SPLAT, "SPLAT_MAP_ENABLED"), **dict.fromkeys(VAT, "VTX_ANIMATION_TEXTURES_ENABLED"),
}  # fmt: skip
ON_UNLESS_OFF = ("DIFFUSE_ENABLED", "NORMAL_ENABLED")


def key(name: str) -> str:
    """Until the island editor switches to ``normalize(name, studio=True)``."""
    return normalize(name, studio=True)


def _stem(path: str) -> str:
    return SOURCE_TEXTURE.sub("", path)


def texture_candidates(name: str, game: str) -> list[tuple[str, str]]:
    """The game paths a texture name may load, in the order tried, each with its rule: the newer games turn a
    source name (``.psd``, ``.png``, ``.tga``) into its ``_0.dds``; the older ones try the name as written first
    (1701 ships some ``.tga``); then the plain ``.dds`` and the lower quality levels ``_1.dds`` to ``_4.dds``."""
    path = normalize(name, studio=True)
    if not path:
        return []
    stem = _stem(path)
    first = [(stem + "_0.dds", "_0.dds"), (path, "as written")]
    if game not in NEW_ENGINE or path.endswith(".dds"):  # a .dds is loaded as it is named (formats/rdp117)
        first.reverse()
    rest = [(stem + ".dds", ".dds"), (stem + ".tga", ".tga")]
    rest += [(f"{stem}_{n}.dds", f"quality level _{n}") for n in QUALITY_LEVELS]
    seen, out = set(), []
    for candidate, rule in first + rest:
        if candidate not in seen:
            seen.add(candidate)
            out.append((candidate, rule))
    return out


def mesh_candidates(name: str) -> list[tuple[str, str]]:
    """The game paths a mesh name may load: the newer games' configs name the source ``.fbx`` and ship its
    ``.rdm``; a plain ``x.rdm`` the game ships only as its levels ``x_lod0.rdm`` (1800); a ``data/`` mesh 117 ships
    only as its baked ``shared/`` copy (a ``data/`` config read in place of its ``shared/`` one names those)."""
    path = normalize(name, studio=True)
    if not path:
        return []
    rdm = re.sub(r"\.fbx$", ".rdm", path)
    out = [(path, "as written")] if rdm == path else [(rdm, ".fbx -> .rdm")]
    if rdm.endswith(".rdm") and not rdm.endswith("_lod0.rdm"):
        out.append((rdm[: -len(".rdm")] + "_lod0.rdm", "level _lod0"))
    out += [
        (configtree.shared_config(p), f"{rule}, the shared/ copy") for p, rule in out if configtree.shared_config(p)
    ]
    return out


def first_existing(candidates: Iterable[tuple[str, str]], exists: Callable[[str], bool]) -> tuple[str, str]:
    """(path, rule) of the first candidate the game has, ("", "") for none."""
    return next(((p, rule) for p, rule in candidates if exists(p)), ("", ""))


def texture_file(name: str, exists: Callable[[str], bool], game: str = "anno117") -> str:
    """The file a texture name loads ("" when the game has none)."""
    return first_existing(texture_candidates(name, game), exists)[0]


def texture_name(name: str, game: str = "anno117") -> str:
    """The file a texture name loads when the game ships it: the first rule of ``texture_candidates`` ("" for no
    name)."""
    return next(iter(texture_candidates(name, game)), ("", ""))[0]


def mesh_file(name: str, exists: Callable[[str], bool]) -> str:
    """The mesh file a mesh name loads ("" when the game has none)."""
    return first_existing(mesh_candidates(name), exists)[0]


def loaded_file(name: str, exists: Callable[[str], bool], game: str) -> str:
    """The file the game loads for a name a config or an asset writes: the name as written, then the game's texture
    rules, then the mesh rules; "" when none of them exists."""
    path = normalize(name, studio=True)
    candidates = [(path, "as written"), *texture_candidates(path, game), *mesh_candidates(path)]
    return first_existing(candidates if path else [], exists)[0]


@dataclass(frozen=True)
class Ref:
    """One name a config writes and what it loads: ``kind`` (texture, mesh, config, effect, animation), the field
    or slot it sits in, the config and node that name it, the path found ("" for none), the rule that found it
    and, when none, the ``reason``; ``off``: a texture slot the material switches off (the game loads nothing)."""

    kind: str
    name: str
    path: str = ""
    rule: str = ""
    reason: str = ""
    slot: str = ""
    owner: str = ""
    node: str = ""
    off: bool = False

    @property
    def failed(self) -> bool:
        return not self.path and not self.off and self.reason not in EMPTY_REASONS


class Index:
    """A game's folders and file names, read once from its paths (``of`` keeps one per tree): what tells a folder
    the game does not ship and a file that ships under another folder."""

    _kept: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def __init__(self, paths: Iterable[str]) -> None:
        self.folders: set[str] = set()
        self.names: dict[str, str] = {}
        for p in paths:
            self.folders.add(posixpath.dirname(p))
            self.names.setdefault(posixpath.basename(p), p)

    @classmethod
    def of(cls, files) -> Index | None:
        """The index of a tree that lists its ``paths`` (None for one that does not)."""
        listed = getattr(files, "paths", None)
        if not listed:
            return None
        try:
            found = cls._kept.get(files)
            if found is None:
                found = cls._kept[files] = cls(listed)
            return found
        except TypeError:  # a tree that cannot be weakly referenced: indexed for this resolver alone
            return cls(listed)


def _deleted(files) -> set[str]:
    found = getattr(files, "deleted", ()) or ()
    return set(found() if callable(found) else found)


@dataclass
class Resolver:
    """Names resolved against one game's ``files`` (anything with ``exists``; a ``GameTree`` also gives its paths,
    so a miss can say whether the folder ships at all or the name lies elsewhere, and its ``deleted``)."""

    files: object
    game: str = "anno117"
    refs: list[Ref] = field(default_factory=list)
    keep: bool = True  # keep every lookup in ``refs`` (the files check, the Checks panels)

    def __post_init__(self) -> None:
        self._index: Index | None | bool = False  # False: not read yet
        self._deleted = _deleted(self.files)
        found = getattr(self.files, "exists", None)  # files that only say where a file is (``where``) do as well
        self._exists = found or (lambda path: self.files.where(path) is not None)

    def exists(self, path: str) -> bool:
        return bool(path) and self._exists(path)

    def texture(self, name: str, slot: str = "", owner: str = "", node: str = "", off: bool = False) -> str:
        """The file a texture name loads ("" for none: a placeholder, a switched-off slot or a missing file)."""
        return self.texture_ref(name, slot, owner, node, off).path

    def shown(self, name: str, slot: str = "", owner: str = "", node: str = "") -> str:
        """What a view loads for a texture name: the file found; the name itself when none is (the load fails and
        the view draws its missing-file checker); "" for no name or a placeholder."""
        return drawn(self.texture_ref(name, slot, owner, node))

    def texture_ref(
        self, name: str, slot: str = "", owner: str = "", node: str = "", off: bool = False, kind: str = "texture"
    ) -> Ref:
        """The lookup of a texture name as a ``Ref`` (kept in ``refs``); ``kind``: an effect's texture is counted
        apart from a model's."""
        if not normalize(name, studio=True):
            return Ref(kind, name, slot=slot, owner=owner, node=node, off=off)
        if off:
            return self._keep(Ref(kind, name, slot=slot, owner=owner, node=node, off=True))
        found, rule = first_existing(texture_candidates(name, self.game), self.exists)
        if found and PLACEHOLDERS.search(normalize(name, studio=True)):
            rule = f"{rule} (a placeholder the game ships)"
        reason = (
            ""
            if found
            else self.why(texture_candidates(name, self.game), PLACEHOLDERS.search(normalize(name, studio=True)))
        )
        return self._keep(Ref(kind, name, found, rule, reason, slot, owner, node))

    def mesh(self, name: str, owner: str = "", node: str = "", kind: str = "mesh") -> str:
        """The mesh (or animation) file a name loads, "" for none."""
        if not normalize(name, studio=True):
            return ""
        found, rule = first_existing(mesh_candidates(name), self.exists)
        reason = "" if found else self.why(mesh_candidates(name))
        return self._keep(Ref(kind, name, found, rule, reason, "FileName", owner, node)).path

    def config(self, name: str, owner: str = "", node: str = "", shared: bool = False) -> str:
        """The config a nested FILE names: with ``shared``, its baked ``shared/`` copy when the game has one (without,
        the copy only when there is no ``data/`` file)."""
        path = normalize(name, studio=True)
        if not path:
            return ""
        copy = configtree.shared_config(path)
        candidates = [(path, "as written")]
        if copy:  # the game reads the baked copy first; the view without it still draws a config only baked
            candidates.insert(0 if shared else 1, (copy, "the shared/ copy"))
        found, rule = first_existing(candidates, self.exists)
        reason = "" if found else self.why([(path, "")])
        return self._keep(Ref("config", name, found, rule, reason, "FileName", owner, node)).path

    def effect(self, name: str, owner: str = "", node: str = "") -> str:
        """A particle effect file (117's ``.rdp``, 2070's ``.efc``), as written."""
        path = normalize(name, studio=True)
        if not path:
            return ""
        found = path if self.exists(path) else ""
        reason = "" if found else self.why([(path, "")])
        ref = Ref("effect", name, found, "as written" if found else "", reason, "FileName", owner, node)
        return self._keep(ref).path

    def _keep(self, ref: Ref) -> Ref:
        if self.keep:
            self.refs.append(ref)
        return ref

    def missing(self) -> list[Ref]:
        """The lookups that found nothing (placeholders and switched-off slots aside), each once."""
        seen, out = set(), []
        for r in self.refs:
            if r.failed and (r.kind, r.name, r.owner) not in seen:
                seen.add((r.kind, r.name, r.owner))
                out.append(r)
        return out

    def index(self) -> Index | None:
        if self._index is False:
            self._index = Index.of(self.files)
        return self._index

    def why(self, candidates: list[tuple[str, str]], placeholder: object = None) -> str:
        """Why none of ``candidates`` is there, by the game's own index: a placeholder name, deleted, a name that
        ships in another folder, a folder the game does not ship at all, or plainly absent."""
        paths = [p for p, _rule in candidates]
        if placeholder:
            return PLACEHOLDER
        if any(p in self._deleted for p in paths):
            return DELETED
        index = self.index()
        if index is None:
            return ABSENT
        if any(posixpath.basename(p) in index.names for p in paths):
            return ELSEWHERE
        if not any(posixpath.dirname(p) in index.folders for p in paths):
            return NOT_SHIPPED
        return ABSENT

    def elsewhere(self, ref: Ref) -> str:
        """For a stale path: the file of the same name the game ships in another folder ("" for none)."""
        index = self.index()
        if index is None:
            return ""
        texture = ref.kind in ("texture", "effect texture")
        candidates = texture_candidates(ref.name, self.game) if texture else mesh_candidates(ref.name)
        return next((f for c, _r in candidates if (f := index.names.get(posixpath.basename(c)))), "")


def drawn(ref: Ref) -> str:
    """What a view loads for a lookup: the file found, the name when it failed (to draw it as missing), else ""."""
    return ref.path or (normalize(ref.name, studio=True) if ref.failed else "")


def material_textures(node, resolve: Resolver, owner: str = "", path: str = "") -> dict[str, Ref]:
    """Every texture slot a newer game's MATERIAL node (``configtree.Node``) writes, looked up: slot -> its ``Ref``;
    a slot its switch turns off is kept as off, not looked up."""
    out = {}
    for slot, value in node.fields:
        if not value or not SOURCE_TEXTURE.search(value.strip().lower()):
            continue
        switch = SLOT_SWITCHES.get(slot, "")
        off = bool(switch) and node.get(switch, "1" if switch in ON_UNLESS_OFF else "0") == "0"
        out[slot] = resolve.texture_ref(value, slot, owner, path, off=off)
    return out


def view_textures(found: dict[str, Ref]) -> Callable[[str], str]:
    """A name -> what a view loads for it, over a material's looked-up slots (``material_textures``)."""
    by_name: dict[str, str] = {}
    for ref in found.values():  # two slots may name one file: a slot that is on wins over one that is off
        name = normalize(ref.name, studio=True)
        by_name[name] = by_name.get(name) or drawn(ref)
    return lambda name: by_name.get(normalize(name, studio=True), "")


# Decoding

TEXTURE_MAX = 1024  # texels across: the first mip at or below it is drawn (a view needs no more)


class DecodeError(ValueError):
    """A texture file the in-process decoders do not read, with the reason (its format)."""


def decode(data: bytes, limit: int = TEXTURE_MAX) -> np.ndarray:
    """A texture file (DDS of any format ``ddsdecode`` reads, PNG, TGA, JPEG) as ``(h, w, 4)`` uint8 RGBA, the
    first DDS mip at or below ``limit`` texels (a cube's first face). Raises ``DecodeError`` saying why not."""
    from .ddsdecode import DdsFile, UnsupportedDds

    if not data:
        raise DecodeError("an empty file (0 bytes): the game ships it so")
    if data[:4] == b"DDS ":
        try:
            dds = DdsFile(data)
            mip = next((k for k in range(dds.info.mips) if max(dds.size_of(k)) <= limit), dds.info.mips - 1)
            return dds.level(mip)
        except (UnsupportedDds, IndexError, ValueError) as exc:
            raise DecodeError(f"DDS: {exc}") from exc
    try:
        import io

        from PIL import Image, UnidentifiedImageError

        return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise DecodeError(f"not a picture the decoders read ({data[:4]!r}): {exc}") from exc


def check_decode(data: bytes) -> str:
    """Why a texture would not draw ("" when it decodes): every level and face fits the file, and the smallest
    level decodes. A full decode is the view's job; this proves the format and the layout, fast."""
    from .ddsdecode import DdsFile, UnsupportedDds

    if data[:4] != b"DDS ":
        try:
            decode(data[:], 1)
        except DecodeError as exc:
            return str(exc)
        return ""
    try:
        dds = DdsFile(data)
        short = dds.cut_short()
        if short:
            return f"DDS: {dds.info.format}: {short}"
        dds.level(dds.info.mips - 1)
    except (UnsupportedDds, IndexError, ValueError) as exc:
        return f"DDS: {exc}"
    return ""


def _checker(size: int = 64, cell: int = 8) -> np.ndarray:
    """Magenta and black squares: what a view draws where a file the model names is not there."""
    y, x = np.mgrid[0:size, 0:size]
    on = ((x // cell) + (y // cell)) % 2 == 0
    out = np.zeros((size, size, 4), np.uint8)
    out[on] = (255, 0, 255, 255)
    out[~on] = (24, 24, 24, 255)
    return out


MISSING_RGBA = _checker()
