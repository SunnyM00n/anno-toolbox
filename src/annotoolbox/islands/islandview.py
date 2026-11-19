"""Offline preview and checks for Anno 117 islands (vanilla or converted), without starting the game.

Reads an island the way the game assembles it (a mod folder first, then the game archives) and
writes ``top.png`` (the material maps composited with the materials' diffuse textures, hill-shaded
from the heightmap, water, coast lines in red), ``oblique.png`` (the heightmap in 3D), ``lod.png``
(the heightmap, then the stored meshes of LOD 2 and up; holes in magenta, mesh outside the active
area in red) and ``report.txt`` (``check``: the traps found in game so far). Formats and units: docs/rendering/terrain-materials.md 1.1.
"""

from __future__ import annotations

import os
import re
import struct
import tempfile
from pathlib import Path

import numpy as np

from ..formats import a7mread, filedb
from ..formats.rda import RdaArchive, RdaError
from .islandchecks import IslandChecks
from .islandgrid import read_bits
from .islandground import slot_weights
from .islandterrain import node_tiles
from .materialorder import set_ranks


class Files:
    """Game paths resolved in a mod folder first, then in the game (``Anno117Files``)."""

    def __init__(
        self, game, mod_root: Path | None = None, exclude: tuple[str, ...] = (), archives: bool = False
    ) -> None:
        """``exclude``: folders of the mod (game paths, ``data/x/``) left out of the index, for a caller that
        reads models only and would walk tens of thousands of island files for nothing. ``archives``: the mod's
        ``.rda`` files are indexed as well, their members found by game path after the mod's loose files (the game's
        mod loader opens none itself: engine-modloader "Archives (.rda) in a mod")."""
        self.game = game
        self.mod_root = Path(mod_root) if mod_root else None
        self.exclude = tuple(e.lower() for e in exclude)
        self.archives = archives
        self._mod_index: dict[str, Path] = {}
        self._archive_index: dict[str, tuple[RdaArchive, str]] = {}
        self.unreadable: list[str] = []  # archives of the mod that could not be read
        self.refresh()

    def refresh(self) -> None:
        """Index the mod folder again (after files were written into it)."""
        if self.mod_root and self.mod_root.is_dir():
            for folder, dirs, names in os.walk(self.mod_root):
                rel = Path(folder).relative_to(self.mod_root).as_posix().lower()
                prefix = "" if rel == "." else rel + "/"
                dirs[:] = [d for d in dirs if f"{prefix}{d.lower()}/" not in self.exclude]
                for name in names:
                    if self.archives and name.lower().endswith(".rda"):
                        self._index_archive(Path(folder) / name)
                    else:
                        self._mod_index[prefix + name.lower()] = Path(folder) / name

    def _index_archive(self, path: Path) -> None:
        """The members of a mod's archive; a later archive (by name) wins over an earlier one, as in the game's mounts."""
        try:
            archive = RdaArchive(path)
            for member in archive.entries:
                self._archive_index[member.replace("\\", "/").lower()] = (archive, member)
        except (OSError, ValueError, RdaError):
            self.unreadable.append(path.name)  # not an archive the editor reads: listed, not fatal

    def where(self, path: str) -> str | None:
        key = path.replace("\\", "/").lower()
        if key in self._mod_index or key in self._archive_index:
            return "mod"
        try:
            self.game.read(key)
            return "game"
        except Exception:
            return None

    def exists(self, path: str) -> bool:
        """Whether the mod or the game has the file (without reading it, where the game's tree can tell)."""
        key = path.replace("\\", "/").lower()
        if key in self._mod_index or key in self._archive_index:
            return True
        found = getattr(self.game, "exists", None)
        return found(key) if found is not None else self.where(key) is not None

    @property
    def deleted(self) -> list[str]:
        """The paths the game's archive map takes away (117; empty for the others)."""
        found = getattr(self.game, "deleted", ())
        return list(found() if callable(found) else found)

    def read(self, path: str) -> bytes:
        key = path.replace("\\", "/").lower()
        if key in self._mod_index:
            return self._mod_index[key].read_bytes()
        if key in self._archive_index:
            archive, member = self._archive_index[key]
            return archive.read(member)
        return self.game.read(key)

    def mod_paths(self, suffix: str = "", prefix: str = "") -> list[str]:
        """What the mod holds by game path - loose files and archive members - sorted, optionally only those under
        ``prefix`` and ending in ``suffix``."""
        keys = {*self._mod_index, *self._archive_index}
        return sorted(k for k in keys if k.startswith(prefix.lower()) and k.endswith(suffix.lower()))

    def list(self, prefix: str) -> list[str]:
        prefix = prefix.replace("\\", "/").lower()
        found = {k for k in self._mod_index if k.startswith(prefix)}
        found |= {k for k in self._archive_index if k.startswith(prefix)}
        try:
            found |= {p.lower() for p in self.game.iter_paths(prefix)}
        except Exception:
            pass
        return sorted(found)


class IslandView(IslandChecks):
    def __init__(self, files: Files, a7m_path: str) -> None:
        self.files = files
        self.path = a7m_path.replace("\\", "/").lower()
        self.name = Path(self.path).stem
        self.folder = self.path.rsplit("/", 1)[0] + "/"
        self.gamedata_dir = f"{self.folder}_gamedata/{self.name}/"
        raw = files.read(self.path)
        self.raw_a7m = raw
        self.archive = RdaArchive.from_bytes(raw, self.path)
        gamedata, rd3d = a7mread.members(self.archive, "gamedata.data", "rd3d.data")
        self.gd, self.rd = filedb.read(gamedata), filedb.read(rd3d)
        self.info = filedb.read(files.read(self.folder + self.name + ".a7minfo"))
        self.gm = self.gd.path("GameSessionManager")
        t = self.rd.path("Terrain")
        w, steps = a7mread.coarse_steps(self.rd)
        self.hm = np.frombuffer(steps, np.int16).reshape(w, w).astype(np.float32) / a7mread.HEIGHT_STEPS
        self.size = (w - 1) // 2
        self.texels = t.find("MaterialMapWidth").u32() // self.size
        self.land = np.asarray(read_bits(self.gm.path("WorldManager/Water")), dtype=bool)
        self.material_set = t.find("MaterialSetFileName").data.decode("utf-16-le").replace("\\", "/")
        self._tex_cache: dict[str, np.ndarray] = {}
        self._lod_cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    # Materials
    def materials(self) -> dict[int, dict]:
        """Material ID -> {file, repetition, sharpness, order, floor, ceil, add, maps, diffuse} from the island's
        material set (``order``: the position in the set's list, the order the game lays the materials down in;
        ``floor``, ``ceil``, ``add``: its displacement)."""
        text = self.files.read(self.material_set).decode("utf-8", "ignore")
        out = {}
        for block in re.findall(r"<Material>(.*?)\r?\n</>", text, re.S):
            mid = int(re.search(r"<ID>(\d+)</>", block).group(1))
            fname = re.search(r"<FileName>([^<]*)</>", block).group(1).replace("\\", "/")
            rep = float((re.search(r"<Repetition>([^<]*)</>", block) or [0, "4"])[1])
            sharp = float((re.search(r"<AlphaSharpness>([^<]*)</>", block) or [0, "1"])[1])
            out[mid] = {"file": fname, "repetition": rep, "sharpness": sharp, "order": len(out)}
            for key, tag in (("floor", "DisplacementFloor"), ("ceil", "DisplacementCeil"), ("add", "DisplacementAdd")):
                out[mid][key] = float((re.search(rf"<{tag}>([^<]*)</>", block) or [0, "0"])[1])
        for m in out.values():
            try:
                mat = self.files.read(m["file"]).decode("utf-8", "ignore")
                d = re.search(r"<DiffuseFileName>([^<]*)</(?:DiffuseFileName)?>", mat)
                m["maps"] = {
                    k: v.replace("\\", "/") for k, v in re.findall(r"<(\w+)FileName>([^<]*)</(?:\1FileName)?>", mat)
                }
                m["diffuse"] = d.group(1).replace("\\", "/") if d else None
            except Exception:
                m["maps"], m["diffuse"] = {}, None
        return out

    def texture(self, psd_path: str, size: int = 128) -> np.ndarray | None:
        """A material map as RGB float array (size x size): ``x.psd`` resolves to ``x_0.dds``."""
        if psd_path in self._tex_cache:
            return self._tex_cache[psd_path]
        from PIL import Image

        from ..models.textures import dds_to_png

        dds_path = re.sub(r"\.(psd|png|tga)$", "_0.dds", psd_path)
        try:
            data = self.files.read(dds_path)
        except Exception:
            self._tex_cache[psd_path] = None
            return None
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            src = Path(tmp) / Path(dds_path).name
            src.write_bytes(data)
            img = Image.open(dds_to_png(src, Path(tmp))).convert("RGB").resize((size, size))
        arr = np.asarray(img, dtype=np.float32) / 255
        self._tex_cache[psd_path] = arr
        return arr

    def material_maps(self) -> tuple[np.ndarray, np.ndarray]:
        def load(name):
            d = self.files.read(f"{self.gamedata_dir}materialmaps/0/{name}")
            header = 148 if d[84:88] == b"DX10" else 128
            w = int.from_bytes(d[16:20], "little")
            return np.frombuffer(d[header:], "<u4").reshape(-1, w)

        return load("matid.dds"), load("matalpha.dds")

    def coverage(self) -> tuple[np.ndarray, np.ndarray]:
        """Per texel and slot: material id and its alpha as the game reads them (no layering: the
        game sums a material's alphas over the slots and lays the materials down in the set's
        order; rendering/terrain-materials.md 1.2)."""
        ids, al = self.material_maps()
        block = al.shape[0] // ids.shape[0]
        up = np.repeat(np.repeat(ids, block, 0), block, 1)
        mids = np.stack([(up >> (5 * s)) & 31 for s in range(6)])
        alpha = np.stack([((al >> (5 * s)) & 31).astype(np.float32) / 31 for s in range(6)])
        return mids, alpha

    def weights(self) -> tuple[np.ndarray, np.ndarray]:
        """Per texel and slot: material id and visible weight, the ground laid down in the material set's list
        order, not the slot order (``islandground.slot_weights``)."""
        mids, alpha = self.coverage()
        return mids, slot_weights(mids, alpha, self.draw_order())

    def draw_order(self) -> dict[int, int]:
        """Material ID -> its place in the island's material set, the order the game draws them in (an ID by
        itself when the set is missing)."""
        try:
            return set_ranks(self.files.read(self.material_set).decode("utf-8", "ignore"))
        except (KeyError, FileNotFoundError):
            return {}

    # Renders
    def albedo(self, res_per_tile: int = 4) -> np.ndarray:
        """Terrain colour from above, res_per_tile pixels per tile."""
        mids, wts = self.weights()
        step = max(1, self.texels // res_per_tile)
        mids, wts = mids[:, ::step, ::step], wts[:, ::step, ::step]
        n = mids.shape[1]
        mats = self.materials()
        out = np.zeros((n, n, 3), np.float32)
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32) / (n / self.size)  # tile coordinates
        for mid in np.unique(mids):
            m = mats.get(int(mid))
            tex = self.texture(m["diffuse"]) if m and m.get("diffuse") else None
            if tex is None:
                color = np.broadcast_to(np.array([1.0, 0.0, 1.0], np.float32), (n, n, 3))  # missing: magenta
            else:
                rep = max(0.25, m["repetition"])
                ts = tex.shape[0]
                u = ((xx / rep) % 1 * ts).astype(int)
                v = ((yy / rep) % 1 * ts).astype(int)
                color = tex[v, u]
            wsum = np.where(mids == mid, wts, 0).sum(axis=0)
            out += color * wsum[..., None]
        return np.clip(out, 0, 1)

    def shaded(self, res_per_tile: int = 4) -> np.ndarray:
        alb = self.albedo(res_per_tile)
        n = alb.shape[0]
        h = self._resample(self.hm, n)
        gz, gx = np.gradient(h, 1.0 / res_per_tile)
        normal = np.dstack([-gx, np.ones_like(h), -gz])
        normal /= np.linalg.norm(normal, axis=2, keepdims=True)
        light = np.array([-0.5, 0.8, -0.35])
        light /= np.linalg.norm(light)
        shade = np.clip(normal @ light, 0, 1) * 0.75 + 0.35
        img = alb * shade[..., None]
        # sea: tint by depth (the water plane is at height 0)
        depth = np.clip(-h / 20, 0, 1)
        water = np.array([0.10, 0.28, 0.40])
        under = h < 0
        img[under] = img[under] * (1 - 0.35 - 0.55 * depth[under, None]) + water * (0.35 + 0.55 * depth[under, None])
        self._draw_splines(img, n / self.size)
        return np.clip(img, 0, 1)

    def _draw_splines(self, img: np.ndarray, scale: float) -> None:
        from PIL import Image, ImageDraw

        pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
        d = ImageDraw.Draw(pil)
        for kind, pts, widths in self.splines():
            xy = [(x * scale, z * scale) for x, _, z in pts]
            if kind == "River":
                wmean = float(np.mean(widths)) if widths else 3
                d.line(xy, fill=(40, 110, 150), width=max(1, int(wmean * scale)))
            elif kind == "Lake":
                d.polygon(xy, fill=(40, 110, 150))
            elif kind == "CoastLine":
                d.line(xy, fill=(230, 40, 40), width=max(2, int(scale)))
        img[...] = np.asarray(pil, np.float32) / 255

    def splines(self):
        """The island's splines (kind, points, widths), read once: a continental island's walk over its objects
        took seconds, and its JSON asks twice."""
        if getattr(self, "_splines", None) is None:
            splines = (bezier_spline(o) for _group, o in a7mread.object_tags(self.gm, ("EditorObject",)))
            self._splines = [spline for spline in splines if spline]
        return list(self._splines)

    def inland_waters(self) -> list[dict]:
        """The rivers and lakes of the island file in the order ``splines`` gives them (``inland_water``)."""
        found = (inland_water(o) for _group, o in a7mread.object_tags(self.gm, ("EditorObject",)))
        return [w for w in found if w is not None]

    @staticmethod
    def _resample(hm: np.ndarray, n: int) -> np.ndarray:
        w = hm.shape[0]
        c = np.linspace(0, w - 1, n)
        x0 = np.clip(c.astype(int), 0, w - 2)
        t = c - x0
        rows = hm[x0] * (1 - t)[:, None] + hm[x0 + 1] * t[:, None]
        return rows[:, x0] * (1 - t)[None, :] + rows[:, x0 + 1] * t[None, :]

    def oblique(
        self, color: np.ndarray, width: int = 1600, height: int = 900, pitch: float = 0.55, height_scale: float = 1.0
    ) -> np.ndarray:
        """Voxel-space view from the south (+z) looking north, drawn front to back."""
        n = color.shape[0]
        h = self._resample(self.hm, n)
        sky = np.array([0.72, 0.80, 0.88], np.float32)
        out = np.tile(sky, (height, width, 1))
        ybuf = np.full(width, height, dtype=np.int32)
        cols = (np.arange(width) * n / width).astype(int)
        px = width / n
        horizon = height * 0.15
        for z in range(n - 1, -1, -1):  # near (bottom) to far (top)
            depth_row = n - 1 - z
            base = height - 1 - depth_row * (height - horizon) / n
            hz = np.maximum(h[z, cols], 0.0)  # the water surface hides the sea bed
            ys = (base - hz * px * height_scale * pitch * 4).astype(np.int32)
            c = color[z, cols].copy()
            wet = h[z, cols] < 0
            c[wet] = c[wet] * 0.35 + np.array([0.10, 0.28, 0.40]) * 0.65
            for x in np.nonzero(ys < ybuf)[0]:
                y0 = max(0, ys[x])
                out[y0 : ybuf[x], x] = c[x]
                ybuf[x] = y0
        return out

    def active_tiles(self) -> np.ndarray:
        """The rd3d ActiveMap at tile resolution."""
        am = self.rd.path("Terrain/ActiveMap")
        active = np.asarray(read_bits(am), dtype=bool)
        cell = self.size // active.shape[0]
        return np.repeat(np.repeat(active, cell, 0), cell, 1)

    def lod_heights(self, lod: int = 2) -> tuple[np.ndarray, np.ndarray]:
        """Rasterise the stored LOD meshes onto a per-tile grid: (height, coverage)."""
        if lod in self._lod_cache:
            return self._lod_cache[lod]
        n = self.size
        height = np.full((n, n), np.nan, np.float32)
        for key in self.files.list(f"{self.gamedata_dir}meshes/{lod}/"):
            if not key.endswith(".tmc"):
                continue
            tx, tz = [int(v) for v in Path(key).stem.split("x")]
            doc = filedb.read(self.files.read(key))
            for e in doc.path("Meshes/map").children:
                if not isinstance(e, filedb.Tag) or e.find("Vertices") is None or not e.find("Vertices").data:
                    continue
                v = e.find("Vertices").data
                ix = struct.unpack(f"<{len(e.find('Indices').data) // 2}H", e.find("Indices").data)
                pts = np.array([struct.unpack_from("<HHH", v, j * 16) for j in range(len(v) // 16)], np.float32)
                wx = pts[:, 0] / 32  # island coordinates in every .tmc tile
                wz = pts[:, 2] / 32
                wy = (pts[:, 1] - 32768) / 64
                for t in range(0, len(ix), 3):
                    a, b, c = ix[t : t + 3]
                    xs, zs, ys = wx[[a, b, c]], wz[[a, b, c]], wy[[a, b, c]]
                    x0, x1 = int(max(0, np.floor(xs.min()))), int(min(n - 1, np.ceil(xs.max())))
                    z0, z1 = int(max(0, np.floor(zs.min()))), int(min(n - 1, np.ceil(zs.max())))
                    if x1 < x0 or z1 < z0:
                        continue
                    det = (zs[1] - zs[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (zs[0] - zs[2])
                    if abs(det) < 1e-9:
                        continue
                    gz_, gx_ = np.mgrid[z0 : z1 + 1, x0 : x1 + 1].astype(np.float32) + 0.5
                    l1 = ((zs[1] - zs[2]) * (gx_ - xs[2]) + (xs[2] - xs[1]) * (gz_ - zs[2])) / det
                    l2 = ((zs[2] - zs[0]) * (gx_ - xs[2]) + (xs[0] - xs[2]) * (gz_ - zs[2])) / det
                    l3 = 1 - l1 - l2
                    inside = (l1 >= -1e-4) & (l2 >= -1e-4) & (l3 >= -1e-4)
                    vals = l1 * ys[0] + l2 * ys[1] + l3 * ys[2]
                    region = height[z0 : z1 + 1, x0 : x1 + 1]
                    region[inside] = vals[inside]
        self._lod_cache[lod] = (height, ~np.isnan(height))
        return self._lod_cache[lod]


def bezier_spline(o: filedb.Tag) -> tuple[str, list, list] | None:
    """A river, lake or coast line object as (kind, points, widths): the Bezier path's points in world units.
    Shared by the new engine's games (117, 1800, 2205); None for any other object."""
    kind = next((k for k in ("River", "Lake", "CoastLine") if o.find(k) is not None), None)
    curve = o.path("BezierPath/Path/BezierCurve") if kind else None
    if curve is None:
        return None
    pts, widths = [], []
    for p in curve.children:
        if isinstance(p, filedb.Tag) and p.find("p") is not None:
            pts.append(struct.unpack("<3f", p.find("p").data))
            if p.find("w") is not None:
                widths.append(struct.unpack("<f", p.find("w").data)[0])
    return kind, pts, widths


def inland_water(o: filedb.Tag) -> dict | None:
    """A river or lake object's flow and look: ``kind``, per point of its path its flow speeds (``u0``: the left
    shore's, the centre's, the right shore's; None for a point without), and its ``River`` or ``Lake`` block's
    values by field (floats, a tuple for a colour, ``RenderOrder`` and ``RenderFlags`` integers, 1800's
    ``WaterColor`` its red, green and blue bytes); None for any
    other object (rendering/water.md 1.10)."""
    kind = next((k for k in ("River", "Lake") if o.find(k) is not None), None)
    curve = o.path("BezierPath/Path/BezierCurve") if kind else None
    if curve is None:
        return None
    speeds = []
    for p in curve.children:
        if isinstance(p, filedb.Tag) and p.find("p") is not None:
            u0 = p.find("u0")
            speeds.append(struct.unpack("<3f", u0.data) if u0 is not None and len(u0.data) == 12 else None)
    look = {}
    for field in o.find(kind).children:
        data = getattr(field, "data", None)
        if field.name in ("RenderOrder", "RenderFlags") and data and len(data) == 4:  # integers
            look[field.name] = struct.unpack("<i", data)[0]
        elif field.name == "WaterColor" and data and len(data) == 4:  # 1800's packed colour, bytes b, g, r, a
            look[field.name] = tuple(data[2::-1])
        elif not isinstance(field, filedb.Tag) and data and len(data) in (4, 12):
            values = struct.unpack(f"<{len(data) // 4}f", data)
            look[field.name] = values[0] if len(values) == 1 else values
    return {"kind": kind, "speeds": speeds, "look": look}


def render_all(view: IslandView, out: Path, res_per_tile: int = 4) -> list[str]:
    from PIL import Image

    out.mkdir(parents=True, exist_ok=True)
    top = view.shaded(res_per_tile)
    Image.fromarray((top * 255).astype(np.uint8)).save(out / "top.png")
    obl = view.oblique(top)
    Image.fromarray((obl * 255).astype(np.uint8)).save(out / "oblique.png")
    n = view.size

    def to_img(h):
        g = np.clip((np.nan_to_num(h, nan=-60) + 60) / 100, 0, 1)
        return np.dstack([g, g, g])

    # LOD sheet: the heightmap, then the stored meshes of LOD 2 and up; magenta = flagged node
    # without mesh, red = mesh outside the active area
    active = view.active_tiles()
    tiles = [to_img(view._resample(view.hm, n))]
    levels = view.rd.path("Terrain/Levels").findall("#1")
    for lod in range(2, len(levels)):
        lh, cov = view.lod_heights(lod)
        img = to_img(lh)
        flags = np.asarray(read_bits(levels[lod].path("HasMeshArray")), dtype=bool)
        node = node_tiles(n, flags.shape[0])
        has_nodes = np.repeat(np.repeat(flags, node, 0), node, 1)[:n, :n]
        img[has_nodes & ~cov & active] = (1, 0, 1)
        img[cov & ~active] = (1, 0, 0)
        tiles.append(img)
    cols = 4
    rows_ = -(-len(tiles) // cols)
    sheet = np.ones((rows_ * (n + 8), cols * (n + 8), 3))
    for k, img in enumerate(tiles):
        r, c = divmod(k, cols)
        sheet[r * (n + 8) : r * (n + 8) + n, c * (n + 8) : c * (n + 8) + n] = img
    Image.fromarray((sheet * 255).astype(np.uint8)).save(out / "lod.png")
    report = view.check()
    (out / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    return report
