"""Port Anno 2070's ground textures as an Anno 117 terrain material set.

2070 (``GroundTextures.xml``): per texture a GUID the islands' layers reference, a 512x512 DXT5 diffuse
(RGB albedo, A blend weight) and DXT5 normal map (RG normal, A gloss). 117: a material set lists
materials by ID (0-31), each an XML naming four 1024x1024 maps - diffuse, normal, spec, height. The
diffuse passes through re-encoded, the normal's B is rebuilt from RG, spec comes from the gloss, height
from the diffuse luminance; the set's header and per-material defaults are the vanilla Roman set's, and
one set covers every island (island-build "The ground"; the formats: rendering/rendering-2070.md).
"""

from __future__ import annotations

import functools
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..infra import buildcache
from ..infra.tools import require_tool, run_tool, tool_id
from ..models import fileresolve
from ..models.textures import dds_to_png, encoder_id, png_to_bc7, vector_data
from ..sources.balancingtext import tag_text
from ..sources.overlay import SourceTree
from . import riverbed

CACHE = "ground"  # buildcache namespace: a ground material's maps, by its source maps, the tools and this module

GROUND_TEXTURES = "data/config/engine/GroundTextures.xml"
ROMAN_SET = "data/base/graphics/library/environment/material_sets/roman_materialset_02.xml"
OUT_DIR = "data/anno2070/terrain"
# the mod loader does not pass new .xml files to the game (the set was "not found"); new .cfg files
# do reach it, and the engine reads the set and the material files by the path it is given
SET_PATH = f"{OUT_DIR}/a2070_materialset.cfg"
OBJECT_LAYERS = {94, 95, 96, 98, 99, 104}  # grass/bush placement layers, not textures


@dataclass
class GroundTexture:
    guid: int
    name: str
    diffuse: str  # 2070 path of the _0.dds
    normal: str | None
    mean_color: tuple[int, int, int]


def splat_ids(tree: SourceTree) -> dict[int, int]:
    """Ground texture GUID -> ``SplatID``: 2070 lays a chunk's layers on in ascending SplatID, not in
    the order the island file lists them (rendering-2070 §2.1; ``isd2070.Island.order``)."""
    text = tree.read_bytes(GROUND_TEXTURES).decode("utf-8", "ignore")
    out = {}
    for block in re.findall(r"<Texture>(.*?)</Texture>", text, re.S):
        guid, splat = tag_text(block, "GUID"), tag_text(block, "SplatID")
        if guid and splat:
            out[int(guid)] = int(splat)
    return out


def read_ground_textures(tree: SourceTree) -> list[GroundTexture]:
    text = tree.read_bytes(GROUND_TEXTURES).decode("utf-8", "ignore")
    out = []
    for block in re.findall(r"<Texture>(.*?)</Texture>", text, re.S):
        g = functools.partial(tag_text, block)
        guid = int(g("GUID"))
        diffuse = g("DiffuseTexture").replace("\\", "/").lower()
        if guid in OBJECT_LAYERS or not diffuse or "wiki_dummy" in diffuse:
            continue
        normal = g("NormalTexture").replace("\\", "/").lower() or None
        rgb = tuple(int((re.search(f"<{c}>(\\d+)</{c}>", block) or [0, "255"])[1]) for c in "RGB")
        out.append(
            GroundTexture(
                guid,
                g("Name"),
                diffuse.replace(".png", "_0.dds"),
                normal.replace(".png", "_0.dds") if normal else None,
                rgb,
            )
        )
    return out


def _to_bc4(src_png: Path, dst: Path) -> None:
    texconv = require_tool("texconv")
    with tempfile.TemporaryDirectory(prefix="a2070_bc4_") as tmp:
        res = run_tool(texconv, ["-f", "BC4_UNORM", "-m", "0", "-ft", "dds", "-o", tmp, "-y", "-nologo", str(src_png)])
        produced = Path(tmp) / (src_png.stem + ".dds")
        if not res.ok or not produced.is_file():
            raise RuntimeError(f"texconv BC4 failed on {src_png}: {res.stderr or res.stdout}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(produced.read_bytes())


def paint_curve(alpha):
    """How much of a texel a 2070 ground layer covers for each painted alpha 0..255: 2070 lays a
    layer on with sat(5 * (a - 1) + 6 * c), ``a`` its diffuse's alpha (rendering-2070.md 2.2),
    averaged here over the texture's own alphas. A low-alpha texture (2070's beaches, mean 0.38)
    shows only where it is painted strongly; read as plain coverage, n_l03's beaches spread over the
    grass in 117 (in game, 2026-10-16)."""
    import numpy as np

    a = np.asarray(alpha, np.float32).ravel()[:: max(1, np.asarray(alpha).size // 65536)]
    c = np.linspace(0.0, 1.0, 256, dtype=np.float32)[:, None]
    return np.clip(5 * (a[None, :] - 1) + 6 * c, 0, 1).mean(axis=1)


def convert_texture(tree: SourceTree, tex: GroundTexture, out: Path, work: Path) -> tuple:
    """Write one material (four maps + XML); returns the material XML's game path, the mean
    alpha of its diffuse (117's blend reads it, ``islandground.blend_alpha``) and its 2070 paint
    curve (``paint_curve``). A material converted before comes from the cache."""
    import numpy as np

    key = buildcache.digest(
        "material",
        tool_id("texconv"),
        encoder_id(),
        buildcache.code_version("islands.terrain2070"),
        buildcache.libs_version(),
        tex.diffuse,
        tree.read_bytes(tex.diffuse),
        tex.normal,
        tree.read_bytes(tex.normal) if tex.normal and tree.exists(tex.normal) else None,
    )
    if (hit := buildcache.get_files(CACHE, key)) is not None:
        meta = json.loads(hit.pop("meta"))
        curve = np.frombuffer(hit.pop("curve"), np.float32).copy()
        for rel, data in hit.items():
            buildcache.write_atomic(out / meta["folder"] / rel, data)
        return meta["cfg"], meta["alpha"], curve
    cfg, alpha, curve = _render_texture(tree, tex, out, work)
    folder = cfg.rpartition("/")[0]
    files = {p.name: p.read_bytes() for p in (out / folder).iterdir() if p.is_file()}
    meta = {"folder": folder, "cfg": cfg, "alpha": alpha}
    buildcache.put_files(
        CACHE, key, {"meta": json.dumps(meta).encode(), "curve": np.asarray(curve, np.float32).tobytes(), **files}
    )
    return cfg, alpha, curve


def _render_texture(tree: SourceTree, tex: GroundTexture, out: Path, work: Path) -> tuple:
    from PIL import Image, ImageChops, ImageFilter

    name = "a2070_" + re.sub(r"[^a-z0-9_]+", "_", Path(tex.diffuse).stem.replace("_diff_0", "").lower())
    folder = f"{OUT_DIR}/materials/{name}"
    dst = out / folder
    dst.mkdir(parents=True, exist_ok=True)
    src = work / f"{name}_d.dds"
    src.write_bytes(tree.read_bytes(tex.diffuse))
    diff = Image.open(dds_to_png(src, work)).convert("RGBA")
    diff_png = work / f"{name}_diff.png"
    diff.save(diff_png)
    png_to_bc7(diff_png, dst / f"{name}_diff_0.dds")

    if tex.normal and tree.exists(tex.normal):
        nsrc = work / f"{name}_n.dds"
        nsrc.write_bytes(tree.read_bytes(tex.normal))
        norm = Image.open(dds_to_png(nsrc, work)).convert("RGBA")
    else:
        norm = Image.new("RGBA", diff.size, (128, 128, 0, 10))
    r, g, _, gloss = norm.split()
    # rebuild Z from XY: z = sqrt(1 - x^2 - y^2)
    import numpy as np

    x = np.asarray(r, dtype=np.float32) / 127.5 - 1
    y = np.asarray(g, dtype=np.float32) / 127.5 - 1
    z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
    b = Image.fromarray(np.clip((z + 1) * 127.5, 0, 255).astype(np.uint8))
    full = Image.merge("RGBA", (r, g, b, Image.new("L", norm.size, 255)))
    norm_png = work / f"{name}_norm.png"
    full.save(norm_png)
    with vector_data():
        png_to_bc7(norm_png, dst / f"{name}_norm_0.dds")

    spec = gloss.point(lambda v: max(5, min(90, v * 3)))
    spec_png = work / f"{name}_spec.png"
    Image.merge("RGBA", (spec, spec, spec, Image.new("L", spec.size, 255))).save(spec_png)
    png_to_bc7(spec_png, dst / f"{name}_spec_0.dds")

    lum = diff.convert("L")
    blur = lum.filter(ImageFilter.GaussianBlur(8))
    detail = ImageChops.subtract(lum, blur, scale=1.0, offset=128)  # high-pass around 128
    height = detail.point(lambda v: max(0, min(40, 8 + (v - 128) // 2)))
    height_png = work / f"{name}_height.png"
    height.save(height_png)
    _to_bc4(height_png, dst / f"{name}_height_0.dds")

    xml = (
        f"<Material>\n<DiffuseFileName>{folder}/{name}_diff.psd</DiffuseFileName>\n"
        f"<HeightFileName>{folder}/{name}_height.psd</HeightFileName>\n"
        f"<SpecFileName>{folder}/{name}_spec.psd</SpecFileName>\n"
        f"<NormalFileName>{folder}/{name}_norm.psd</NormalFileName>\n</Material>\n"
    )
    (dst / f"{name}.cfg").write_text(xml, encoding="utf-8", newline="\n")
    alpha = np.asarray(diff.getchannel("A"), np.float32) / 255
    curve = riverbed.bed_curve(alpha) if tex.guid == riverbed.RIVER_BED_GUID else paint_curve(alpha)
    return f"{folder}/{name}.cfg", float(alpha.mean()), curve


# Per material, 117's set carries how hard it blends with its neighbours (``AlphaSharpness``: 0.1
# very soft ... 5.0 crisp in vanilla) and the kind of ground it is (``GroundType``, which also decides
# the vegetation the terrain spawns). 2070 has neither, so they come from the texture's name: the
# medians of the shipped sets per ground type (grass 1.0, bushes 0.6, rock 2.3, soil 3.0, other 0.8).
MATERIAL_STYLES = (  # name fragment -> (AlphaSharpness, GroundType)
    ("gras_detail", (0.4, "GrassOnly")),
    ("gras", (1.0, "Grass")),
    ("beach", (0.8, "None")),
    ("sand", (0.8, "None")),
    ("earth", (1.5, "Soil")),
    ("mountain_smooth", (1.8, "Rock")),
    ("mountain", (2.3, "Rock")),
    ("stone", (2.3, "Rock")),
    ("lava", (1.5, "Rock")),
    ("water", (0.4, "None")),
)
DEFAULT_STYLE = (0.6, "None")
# 2070 tiles every ground texture once per <TextureScale> world units (GroundTextures.xml: 4); 117's
# Repetition is the same measure (terrain_baking.fx: PositionWS / Repetition), one unit per tile in both.
DEFAULT_TEXTURE_SCALE = 4.0
# The materials carry their GroundType (the engine's own greenery was the grit layer, cleared by islandground.clear_grit)
# but no height displacement: ours are synthesised from the 2070 diffuse with twice vanilla's contrast and dug holes in
# the ground (in game, 2026-09-23); island-build "The ground"
NO_DISPLACEMENT = ("DisplacementFloor", "DisplacementCeil")


def material_style(name: str) -> tuple[float, str]:
    """``(AlphaSharpness, GroundType)`` for a 2070 ground texture name."""
    low = name.lower()
    for part, style in MATERIAL_STYLES:
        if part in low:
            return style
    return DEFAULT_STYLE


def texture_scale(tree: SourceTree) -> float:
    """World units one ground texture tile spans, shared by every 2070 ground texture."""
    if not tree.exists(GROUND_TEXTURES):
        return DEFAULT_TEXTURE_SCALE
    m = re.search(
        r"<TextureScale>\s*([0-9.]+)\s*</TextureScale>", tree.read_bytes(GROUND_TEXTURES).decode("utf-8", "ignore")
    )
    scale = float(m.group(1)) if m else 0.0
    return scale if scale > 0 else DEFAULT_TEXTURE_SCALE


def build_material_set(tree: SourceTree, anno117_files, out: Path) -> tuple[dict[int, int], dict[int, tuple], dict]:
    """Turn every 2070 ground texture into a 117 one and write the material set; returns 2070 texture GUID ->
    117 material ID, per material ID its AlphaSharpness and mean diffuse alpha (for
    ``islandground.GroundMixin.apply_materials``), and per 2070 texture GUID its paint curve
    (``paint_curve``, for ``IslandBuilder.paint_curves``)."""
    textures = read_ground_textures(tree)
    if tree.exists(riverbed.BED_DIFFUSE):  # 2070 draws it under every river (riverbed): a material of the set
        name, normal = riverbed.BED_NAME, riverbed.BED_NORMAL
        textures.append(GroundTexture(riverbed.RIVER_BED_GUID, name, riverbed.BED_DIFFUSE, normal, (128, 128, 128)))
    if len(textures) > 32:
        raise ValueError(f"{len(textures)} ground textures; material IDs have 5 bits")
    roman = anno117_files.read(ROMAN_SET).decode("utf-8", "ignore")
    header = roman[: roman.find("<Material>")]
    template = re.search(r"<Material>.*?\r?\n</>\r?\n", roman, re.S).group(0)
    repetition = texture_scale(tree)
    entries = []
    material_of = {}
    transfer = {}
    curves = {}
    with tempfile.TemporaryDirectory(prefix="a2070_terrain_") as tmp:
        work = Path(tmp)
        for mid, tex in enumerate(textures):
            path, mean_alpha, curves[tex.guid] = convert_texture(tree, tex, out, work)
            entry = re.sub(r"<ID>\d+</>", f"<ID>{mid}</>", template)
            entry = re.sub(r"<FileName>[^<]*</>", f"<FileName>{path}</>", entry, count=1)
            entry = re.sub(r"<ImportLabel>[^<]*</>", f"<ImportLabel>{tex.name}</>", entry)
            sharp, ground = material_style(tex.name)
            entry = re.sub(r"<Repetition>[^<]*</>", f"<Repetition>{repetition:.6f}</>", entry)
            for tag in NO_DISPLACEMENT:
                entry = re.sub(rf"<{tag}>[^<]*</>", f"<{tag}>0.000000</>", entry)
            entry = re.sub(r"<AlphaSharpness>[^<]*</>", f"<AlphaSharpness>{sharp:.6f}</>", entry)
            entry = re.sub(r"<GroundType>[^<]*</>", f"<GroundType>{ground}</>", entry)
            entries.append(entry)
            material_of[tex.guid] = mid
            transfer[mid] = (sharp, mean_alpha)
    # 2070 textures that map onto others (MapToGUID) and unknown GUIDs fall back to the sea bed.
    # The set closes after its last material, as vanilla's do; bytes keep its CRLF line ends.
    text = header + "".join(entries) + "</>\r\n"
    (out / SET_PATH).parent.mkdir(parents=True, exist_ok=True)
    (out / SET_PATH).write_bytes(text.encode("utf-8"))
    return material_of, transfer, curves


# The 2070 texture families by name; "soil" is the family an unknown texture falls into.
MATERIAL_CLASSES = (
    ("gras", "grass"),
    ("beach", "sand"),
    ("sand", "sand"),
    ("earth", "grass"),
    ("mountain", "rock"),
    ("stone", "rock"),
    ("lava", "rock"),
    ("water", "water"),
)


def material_class(name: str) -> str:
    low = name.lower()
    for part, kind in MATERIAL_CLASSES:
        if part in low:
            return kind
    return "soil"


VANILLA_SET_DIR = "/graphics/library/environment/material_sets/"
_ENTRY = re.compile(r"<Material>.*?\r?\n</>\r?\n", re.S)


def vanilla_materials(files, set_dir: str = VANILLA_SET_DIR) -> list[dict]:
    """Every ground material of 117's own material sets (``set_dir``: another game's folder of sets, 1800's), once each:
    ``{"file", "set", "name", "diffuse", "normal", "repetition"}`` (``files``: ``islandview.Files``, ``list`` and
    ``read``)."""
    out: dict[str, dict] = {}
    for path in files.list("data/"):
        if set_dir not in path or not path.endswith(".xml") or path.startswith(("data/anno2070/", "data/imported/")):
            continue
        text = files.read(path).decode("utf-8", "ignore")
        for rank, entry in enumerate(_ENTRY.findall(text)):
            fname = (re.search(r"<FileName>([^<]*)</>", entry) or [None, ""])[1].replace("\\", "/")
            if not fname or fname.lower() in out:
                continue
            label = (re.search(r"<ImportLabel>([^<]*)</>", entry) or [None, ""])[1] or Path(fname).stem
            rep = float((re.search(r"<Repetition>([^<]*)</>", entry) or [None, "4"])[1])
            try:
                mat = files.read(fname).decode("utf-8", "ignore")
                d = re.search(r"<DiffuseFileName>([^<]*)</(?:DiffuseFileName)?>", mat)
                diffuse = d.group(1).replace("\\", "/") if d else None
                nm = re.search(r"<NormalFileName>([^<]*)</(?:NormalFileName)?>", mat)
                normal = nm.group(1).replace("\\", "/") if nm else None
            except Exception:
                diffuse = normal = None
            out[fname.lower()] = {
                "file": fname,
                "set": path,
                "name": label,
                "diffuse": diffuse,
                "normal": normal,
                "repetition": rep,
                "rank": rank,  # its place in its set's list: 117's draw order (materialorder)
            }
    return sorted(out.values(), key=lambda m: m["name"].lower())


def material_colour(files, material: str) -> tuple[int, int, int] | None:
    """The mean colour of a 117 ground material's diffuse map (for the island's map pictures)."""
    import numpy as np
    from PIL import Image

    text = files.read(material).decode("utf-8", "ignore")
    m = re.search(r"<DiffuseFileName>([^<]*)</(?:DiffuseFileName)?>", text)
    if not m:
        return None
    dds = re.sub(r"\.(psd|png|tga)$", "", m.group(1).replace("\\", "/"), flags=re.I) + "_0.dds"
    data = files.read(dds)
    try:
        img = Image.fromarray(fileresolve.decode(data), "RGBA").convert("RGB").resize((16, 16))
    except fileresolve.DecodeError:  # a format the in-process decoders do not read: texconv
        with tempfile.TemporaryDirectory(prefix="a2070_mat_") as tmp:
            src = Path(tmp) / "diffuse.dds"
            src.write_bytes(data)
            img = Image.open(dds_to_png(src, Path(tmp))).convert("RGB").resize((16, 16))
    return tuple(int(v) for v in np.asarray(img, np.float32).reshape(-1, 3).mean(axis=0))


def _label(entry: str) -> str:
    m = re.search(r"<ImportLabel>([^<]*)</>", entry) or re.search(r"<FileName>([^<]*)</>", entry)
    return Path(m.group(1)).stem if m else ""


def _extra_entries(files, extra: dict) -> dict[int, tuple[str, int]]:
    """The added 117 materials' set entries (``extra``: ``"id"`` -> ``{"file", "set"}``) with their IDs, and
    each one's place in its own set."""
    entries = {}
    for mid, m in sorted(extra.items(), key=lambda kv: int(kv[0])):
        if not 0 <= int(mid) < 32:
            raise ValueError(f"material ID {mid}: a set holds 0-31")
        source = files.read(m["set"]).decode("utf-8", "ignore")
        entry = next(
            (e for e in _ENTRY.findall(source) if f"<FileName>{m['file']}</>" in e.replace("\\", "/")),
            None,
        )
        if entry is None:
            raise KeyError(f"{m['file']} is not in {m['set']}")
        entries[int(mid)] = (re.sub(r"<ID>\d+</>", f"<ID>{int(mid)}</>", entry), _ENTRY.findall(source).index(entry))
    return entries


def draw_order(text: str, files, extra: dict) -> list[int]:
    """The material IDs of the set ``text`` in 117's draw order when 117 materials were added (``extra``): the set as it
    is, each added one placed by its family (``materialorder``)."""
    from .materialorder import merged_order

    own = [(int(re.search(r"<ID>(\d+)</>", e).group(1)), _label(e)) for e in _ENTRY.findall(text)]
    foreign = [(mid, _label(e), rank) for mid, (e, rank) in _extra_entries(files, extra).items()]
    return merged_order(own, foreign)


def material_draw_order(files, out: Path, extra: dict) -> list[int]:
    """``draw_order`` of the 2070 set the build wrote (``SET_PATH``)."""
    return draw_order((out / SET_PATH).read_bytes().decode("utf-8"), files, extra)


def compose_set(text: str, files, extra: dict) -> str:
    """The material set ``text`` with 117 materials added at the IDs the island editor gave them (``extra``: ``"id"`` ->
    ``{"file", "set"}``), each copied with its set entry's style and listed where ``draw_order`` puts it (117 draws in
    list order); the set's header and footer stay."""
    found = list(_ENTRY.finditer(text))
    by_id = {int(re.search(r"<ID>(\d+)</>", m.group(0)).group(1)): m.group(0) for m in found}
    by_id |= {mid: e for mid, (e, _) in _extra_entries(files, extra).items()}
    body = "".join(by_id[mid] for mid in draw_order(text, files, extra))
    return text[: found[0].start()] + body + text[found[-1].end() :]


def island_material_set(files, out: Path, extra: dict, island: str) -> str:
    """The 2070 set (``SET_PATH``, written by ``build_material_set``) with the added 117 materials (``compose_set``) as
    the island's own set in the mod; returns its path."""
    text = (out / SET_PATH).read_bytes().decode("utf-8")
    path = f"{OUT_DIR}/a2070_materialset_{island}.cfg"
    (out / path).write_bytes(compose_set(text, files, extra).encode("utf-8"))
    return path


def class_share(isl, tree: SourceTree, kind: str):
    """Per tile corner of ``isl`` (rows z), how much of 2070's painted ground is of the class
    ``kind`` (``material_class``), the layers laid in their paint order. A numpy array."""
    import numpy as np

    of_kind = {t.guid for t in read_ground_textures(tree) if material_class(t.name) == kind}
    share = None
    for idx, grid in isl.texture_layers().items():
        a = np.asarray(grid, np.float32) / 255
        if share is None:  # the base layer covers everything
            share = np.full(a.shape, 1.0 if idx in of_kind else 0.0, np.float32)
        else:
            share = share * (1 - a) + a * (1.0 if idx in of_kind else 0.0)
    return share
