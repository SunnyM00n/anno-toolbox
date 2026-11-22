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
from ..formats.rda import CRdaArchive, CRdaError
from .islandchecks import CIslandChecks
from .islandgrid import read_bits
from .islandground import slot_weights
from .islandterrain import node_tiles
from .materialorder import set_ranks


class CFiles:
    """Game paths resolved in a mod folder first, then in the game (``Anno117Files``)."""

    def __init__(
        self, game, mod_root: Path | None = None, exclude: tuple[str, ...] = (), include_archives: bool = False
    ) -> None:
        """``exclude``: folders of the mod (game paths, ``data/x/``) left out of the index, for a caller that
        reads models only and would walk tens of thousands of island files for nothing. ``include_archives``: the mod's
        ``.rda`` files are indexed as well, their members found by game path after the mod's loose files (the game's
        mod loader opens none itself: engine-modloader "Archives (.rda) in a mod")."""
        self.m_game = game
        self.m_mod_root = Path(mod_root) if mod_root else None
        self.m_exclude = tuple(excluded.lower() for excluded in exclude)
        self.m_include_archives = include_archives
        self._m_mod_index: dict[str, Path] = {}
        self._m_archive_index: dict[str, tuple[CRdaArchive, str]] = {}
        self.m_unreadable: list[str] = []  # archives of the mod that could not be read
        self.refresh()

    def refresh(self) -> None:
        """Index the mod folder again (after files were written into it, or removed from it)."""
        self._m_mod_index.clear()
        self._m_archive_index.clear()
        self.m_unreadable.clear()
        if self.m_mod_root and self.m_mod_root.is_dir():
            for folder, dirs, names in os.walk(self.m_mod_root):
                relative_folder = Path(folder).relative_to(self.m_mod_root).as_posix().lower()
                prefix = "" if relative_folder == "." else relative_folder + "/"
                dirs[:] = [directory for directory in dirs if f"{prefix}{directory.lower()}/" not in self.m_exclude]
                for name in names:
                    if self.m_include_archives and name.lower().endswith(".rda"):
                        self._index_archive(Path(folder) / name)
                    else:
                        self._m_mod_index[prefix + name.lower()] = Path(folder) / name

    def _index_archive(self, path: Path) -> None:
        """The members of a mod's archive; a later archive (by name) wins over an earlier one, as in the game's mounts."""
        try:
            archive = CRdaArchive(path)
            for member in archive.entries:
                self._m_archive_index[member.replace("\\", "/").lower()] = (archive, member)
        except (OSError, ValueError, CRdaError):
            self.m_unreadable.append(path.name)  # not an archive the editor reads: listed, not fatal

    def where(self, path: str) -> str | None:
        key = path.replace("\\", "/").lower()
        if key in self._m_mod_index or key in self._m_archive_index:
            return "mod"
        try:
            self.m_game.read(key)
            return "game"
        except Exception:
            return None

    def exists(self, path: str) -> bool:
        """Whether the mod or the game has the file (without reading it, where the game's tree can tell)."""
        key = path.replace("\\", "/").lower()
        if key in self._m_mod_index or key in self._m_archive_index:
            return True
        found = getattr(self.m_game, "exists", None)
        return found(key) if found is not None else self.where(key) is not None

    @property
    def deleted(self) -> list[str]:
        """The paths the game's archive map takes away (117; empty for the others)."""
        found = getattr(self.m_game, "m_deleted", None) or getattr(
            self.m_game, "deleted", ()
        )  # list, or CAnno117Files' method
        return list(found() if callable(found) else found)

    def read(self, path: str) -> bytes:
        key = path.replace("\\", "/").lower()
        if key in self._m_mod_index:
            return self._m_mod_index[key].read_bytes()
        if key in self._m_archive_index:
            archive, member = self._m_archive_index[key]
            return archive.read(member)
        return self.m_game.read(key)

    def mod_paths(self, suffix: str = "", prefix: str = "") -> list[str]:
        """What the mod holds by game path - loose files and archive members - sorted, optionally only those under
        ``prefix`` and ending in ``suffix``."""
        keys = {*self._m_mod_index, *self._m_archive_index}
        return sorted(
            member_key
            for member_key in keys
            if member_key.startswith(prefix.lower()) and member_key.endswith(suffix.lower())
        )

    def list(self, prefix: str) -> list[str]:
        prefix = prefix.replace("\\", "/").lower()
        found = {candidate for candidate in self._m_mod_index if candidate.startswith(prefix)}
        found |= {candidate for candidate in self._m_archive_index if candidate.startswith(prefix)}
        try:
            found |= {game_path.lower() for game_path in self.m_game.iter_paths(prefix)}
        except Exception:
            pass
        return sorted(found)


class CIslandView(CIslandChecks):
    def __init__(self, files: CFiles, a7m_path: str) -> None:
        self.files = files
        self.path = a7m_path.replace("\\", "/").lower()
        self.name = Path(self.path).stem
        self.folder = self.path.rsplit("/", 1)[0] + "/"
        self.gamedata_dir = f"{self.folder}_gamedata/{self.name}/"
        raw = files.read(self.path)
        self.raw_a7m = raw
        self.archive = CRdaArchive.from_bytes(raw, self.path)
        gamedata, rd3d = a7mread.members(self.archive, "gamedata.data", "rd3d.data")
        self.gamedata, self.rd3d = filedb.read(gamedata), filedb.read(rd3d)
        self.a7minfo = filedb.read(files.read(self.folder + self.name + ".a7minfo"))
        self.game_session_manager = self.gamedata.path("GameSessionManager")
        terrain = self.rd3d.path("Terrain")
        coarse_width, steps = a7mread.coarse_steps(self.rd3d)
        self.height_map = (
            np.frombuffer(steps, np.int16).reshape(coarse_width, coarse_width).astype(np.float32) / a7mread.HEIGHT_STEPS
        )
        self.size = (coarse_width - 1) // 2
        self.texels = terrain.find("MaterialMapWidth").u32() // self.size
        self.land = np.asarray(read_bits(self.game_session_manager.path("WorldManager/Water")), dtype=bool)
        self.material_set = terrain.find("MaterialSetFileName").data.decode("utf-16-le").replace("\\", "/")
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
            material_id = int(re.search(r"<ID>(\d+)</>", block).group(1))
            file_name = re.search(r"<FileName>([^<]*)</>", block).group(1).replace("\\", "/")
            repetition = float((re.search(r"<Repetition>([^<]*)</>", block) or [0, "4"])[1])
            sharpness = float((re.search(r"<AlphaSharpness>([^<]*)</>", block) or [0, "1"])[1])
            out[material_id] = {"file": file_name, "repetition": repetition, "sharpness": sharpness, "order": len(out)}
            for key, tag in (("floor", "DisplacementFloor"), ("ceil", "DisplacementCeil"), ("add", "DisplacementAdd")):
                out[material_id][key] = float((re.search(rf"<{tag}>([^<]*)</>", block) or [0, "0"])[1])
        for material in out.values():
            try:
                material_text = self.files.read(material["file"]).decode("utf-8", "ignore")
                diffuse_match = re.search(r"<DiffuseFileName>([^<]*)</(?:DiffuseFileName)?>", material_text)
                material["maps"] = {
                    map_name: map_path.replace("\\", "/")
                    for map_name, map_path in re.findall(r"<(\w+)FileName>([^<]*)</(?:\1FileName)?>", material_text)
                }
                material["diffuse"] = diffuse_match.group(1).replace("\\", "/") if diffuse_match else None
            except Exception:
                material["maps"], material["diffuse"] = {}, None
        return out

    def texture(self, psd_path: str, size_px: int = 128) -> np.ndarray | None:
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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            dds_file = Path(scratch) / Path(dds_path).name
            dds_file.write_bytes(data)
            image = Image.open(dds_to_png(dds_file, Path(scratch))).convert("RGB").resize((size_px, size_px))
        pixels = np.asarray(image, dtype=np.float32) / 255
        self._tex_cache[psd_path] = pixels
        return pixels

    def material_maps(self) -> tuple[np.ndarray, np.ndarray]:
        def load(name):
            data = self.files.read(f"{self.gamedata_dir}materialmaps/0/{name}")
            header = 148 if data[84:88] == b"DX10" else 128
            width = int.from_bytes(data[16:20], "little")
            return np.frombuffer(data[header:], "<u4").reshape(-1, width)

        return load("matid.dds"), load("matalpha.dds")

    def coverage(self) -> tuple[np.ndarray, np.ndarray]:
        """Per texel and slot: material id and its alpha as the game reads them (no layering: the
        game sums a material's alphas over the slots and lays the materials down in the set's
        order; rendering/terrain-materials.md 1.2)."""
        ids, alphas = self.material_maps()
        block = alphas.shape[0] // ids.shape[0]
        expanded_ids = np.repeat(np.repeat(ids, block, 0), block, 1)
        material_ids = np.stack([(expanded_ids >> (5 * slot)) & 31 for slot in range(6)])
        alpha = np.stack([((alphas >> (5 * slot)) & 31).astype(np.float32) / 31 for slot in range(6)])
        return material_ids, alpha

    def weights(self) -> tuple[np.ndarray, np.ndarray]:
        """Per texel and slot: material id and visible weight, the ground laid down in the material set's list
        order, not the slot order (``islandground.slot_weights``)."""
        material_ids, alpha = self.coverage()
        return material_ids, slot_weights(material_ids, alpha, self.draw_order())

    def draw_order(self) -> dict[int, int]:
        """Material ID -> its place in the island's material set, the order the game draws them in (an ID by
        itself when the set is missing)."""
        try:
            return set_ranks(self.files.read(self.material_set).decode("utf-8", "ignore"))
        except (KeyError, FileNotFoundError):
            return {}

    # Renders
    def albedo(self, pixels_per_tile: int = 4) -> np.ndarray:
        """Terrain colour from above, res_per_tile pixels per tile."""
        material_ids, weights = self.weights()
        step = max(1, self.texels // pixels_per_tile)
        material_ids, weights = material_ids[:, ::step, ::step], weights[:, ::step, ::step]
        pixel_count = material_ids.shape[1]
        materials = self.materials()
        out = np.zeros((pixel_count, pixel_count, 3), np.float32)
        tile_z, tile_x = np.mgrid[0:pixel_count, 0:pixel_count].astype(np.float32) / (
            pixel_count / self.size
        )  # tile coordinates
        for material_id in np.unique(material_ids):
            material = materials.get(int(material_id))
            texture = self.texture(material["diffuse"]) if material and material.get("diffuse") else None
            if texture is None:
                color = np.broadcast_to(
                    np.array([1.0, 0.0, 1.0], np.float32), (pixel_count, pixel_count, 3)
                )  # missing: magenta
            else:
                repetition = max(0.25, material["repetition"])
                texture_size = texture.shape[0]
                texture_u = ((tile_x / repetition) % 1 * texture_size).astype(int)
                texture_v = ((tile_z / repetition) % 1 * texture_size).astype(int)
                color = texture[texture_v, texture_u]
            weight_sum = np.where(material_ids == material_id, weights, 0).sum(axis=0)
            out += color * weight_sum[..., None]
        return np.clip(out, 0, 1)

    def shaded(self, pixels_per_tile: int = 4) -> np.ndarray:
        albedo = self.albedo(pixels_per_tile)
        pixel_count = albedo.shape[0]
        heights = self._resample(self.height_map, pixel_count)
        gradient_z, gradient_x = np.gradient(heights, 1.0 / pixels_per_tile)
        normal = np.dstack([-gradient_x, np.ones_like(heights), -gradient_z])
        normal /= np.linalg.norm(normal, axis=2, keepdims=True)
        light = np.array([-0.5, 0.8, -0.35])
        light /= np.linalg.norm(light)
        shade = np.clip(normal @ light, 0, 1) * 0.75 + 0.35
        image = albedo * shade[..., None]
        # sea: tint by depth (the water plane is at height 0)
        depth = np.clip(-heights / 20, 0, 1)
        water = np.array([0.10, 0.28, 0.40])
        under = heights < 0
        image[under] = image[under] * (1 - 0.35 - 0.55 * depth[under, None]) + water * (
            0.35 + 0.55 * depth[under, None]
        )
        self._draw_splines(image, pixel_count / self.size)
        return np.clip(image, 0, 1)

    def _draw_splines(self, image: np.ndarray, pixels_per_tile: float) -> None:
        from PIL import Image, ImageDraw

        pil = Image.fromarray((np.clip(image, 0, 1) * 255).astype(np.uint8))
        draw = ImageDraw.Draw(pil)
        for kind, points, widths in self.splines():
            xy = [(x * pixels_per_tile, z * pixels_per_tile) for x, _, z in points]
            if kind == "River":
                mean_width = float(np.mean(widths)) if widths else 3
                draw.line(xy, fill=(40, 110, 150), width=max(1, int(mean_width * pixels_per_tile)))
            elif kind == "Lake":
                draw.polygon(xy, fill=(40, 110, 150))
            elif kind == "CoastLine":
                draw.line(xy, fill=(230, 40, 40), width=max(2, int(pixels_per_tile)))
        image[...] = np.asarray(pil, np.float32) / 255

    def splines(self):
        """The island's splines (kind, points, widths), read once: a continental island's walk over its objects
        took seconds, and its JSON asks twice."""
        if getattr(self, "_splines", None) is None:
            splines = (
                bezier_spline(object_tag)
                for _group, object_tag in a7mread.object_tags(self.game_session_manager, ("EditorObject",))
            )
            self._splines = [spline for spline in splines if spline]
        return list(self._splines)

    def inland_waters(self) -> list[dict]:
        """The rivers and lakes of the island file in the order ``splines`` gives them (``inland_water``)."""
        found = (
            inland_water(object_tag)
            for _group, object_tag in a7mread.object_tags(self.game_session_manager, ("EditorObject",))
        )
        return [water for water in found if water is not None]

    @staticmethod
    def _resample(heightmap: np.ndarray, output_size: int) -> np.ndarray:
        source_size = heightmap.shape[0]
        sample_positions = np.linspace(0, source_size - 1, output_size)
        low_index = np.clip(sample_positions.astype(int), 0, source_size - 2)
        fraction = sample_positions - low_index
        rows = heightmap[low_index] * (1 - fraction)[:, None] + heightmap[low_index + 1] * fraction[:, None]
        return rows[:, low_index] * (1 - fraction)[None, :] + rows[:, low_index + 1] * fraction[None, :]

    def oblique(
        self,
        color: np.ndarray,
        width_px: int = 1600,
        height_px: int = 900,
        pitch: float = 0.55,
        height_scale: float = 1.0,
    ) -> np.ndarray:
        """Voxel-space view from the south (+z) looking north, drawn front to back."""
        pixel_count = color.shape[0]
        heights = self._resample(self.height_map, pixel_count)
        sky = np.array([0.72, 0.80, 0.88], np.float32)
        out = np.tile(sky, (height_px, width_px, 1))
        y_buffer = np.full(width_px, height_px, dtype=np.int32)
        columns = (np.arange(width_px) * pixel_count / width_px).astype(int)
        pixels_per_cell = width_px / pixel_count
        horizon = height_px * 0.15
        for z in range(pixel_count - 1, -1, -1):  # near (bottom) to far (top)
            depth_row = pixel_count - 1 - z
            base = height_px - 1 - depth_row * (height_px - horizon) / pixel_count
            heights_here = np.maximum(heights[z, columns], 0.0)  # the water surface hides the sea bed
            screen_ys = (base - heights_here * pixels_per_cell * height_scale * pitch * 4).astype(np.int32)
            row_colors = color[z, columns].copy()
            wet = heights[z, columns] < 0
            row_colors[wet] = row_colors[wet] * 0.35 + np.array([0.10, 0.28, 0.40]) * 0.65
            for x in np.nonzero(screen_ys < y_buffer)[0]:
                top_y = max(0, screen_ys[x])
                out[top_y : y_buffer[x], x] = row_colors[x]
                y_buffer[x] = top_y
        return out

    def active_tiles(self) -> np.ndarray:
        """The rd3d ActiveMap at tile resolution."""
        active_map = self.rd3d.path("Terrain/ActiveMap")
        active = np.asarray(read_bits(active_map), dtype=bool)
        cell = self.size // active.shape[0]
        return np.repeat(np.repeat(active, cell, 0), cell, 1)

    def lod_heights(self, lod: int = 2) -> tuple[np.ndarray, np.ndarray]:
        """Rasterise the stored LOD meshes onto a per-tile grid: (height, coverage)."""
        if lod in self._lod_cache:
            return self._lod_cache[lod]
        tile_count = self.size
        height = np.full((tile_count, tile_count), np.nan, np.float32)
        for key in self.files.list(f"{self.gamedata_dir}meshes/{lod}/"):
            if not key.endswith(".tmc"):
                continue
            tile_x, tile_z = [int(vertices) for vertices in Path(key).stem.split("x")]
            doc = filedb.read(self.files.read(key))
            for mesh in doc.path("Meshes/map").children:
                if not isinstance(mesh, filedb.STag) or mesh.find("Vertices") is None or not mesh.find("Vertices").data:
                    continue
                vertices = mesh.find("Vertices").data
                indices = struct.unpack(f"<{len(mesh.find('Indices').data) // 2}H", mesh.find("Indices").data)
                points = np.array(
                    [struct.unpack_from("<HHH", vertices, j * 16) for j in range(len(vertices) // 16)], np.float32
                )
                world_x = points[:, 0] / 32  # island coordinates in every .tmc tile
                world_z = points[:, 2] / 32
                world_y = (points[:, 1] - 32768) / 64
                for first in range(0, len(indices), 3):
                    index_a, index_b, index_c = indices[first : first + 3]
                    corner_xs, corner_zs, corner_ys = (
                        world_x[[index_a, index_b, index_c]],
                        world_z[[index_a, index_b, index_c]],
                        world_y[[index_a, index_b, index_c]],
                    )
                    min_x, max_x = (
                        int(max(0, np.floor(corner_xs.min()))),
                        int(min(tile_count - 1, np.ceil(corner_xs.max()))),
                    )
                    min_z, max_z = (
                        int(max(0, np.floor(corner_zs.min()))),
                        int(min(tile_count - 1, np.ceil(corner_zs.max()))),
                    )
                    if max_x < min_x or max_z < min_z:
                        continue
                    determinant = (corner_zs[1] - corner_zs[2]) * (corner_xs[0] - corner_xs[2]) + (
                        corner_xs[2] - corner_xs[1]
                    ) * (corner_zs[0] - corner_zs[2])
                    if abs(determinant) < 1e-9:
                        continue
                    sample_z, sample_x = np.mgrid[min_z : max_z + 1, min_x : max_x + 1].astype(np.float32) + 0.5
                    weight_1 = (
                        (corner_zs[1] - corner_zs[2]) * (sample_x - corner_xs[2])
                        + (corner_xs[2] - corner_xs[1]) * (sample_z - corner_zs[2])
                    ) / determinant
                    weight_2 = (
                        (corner_zs[2] - corner_zs[0]) * (sample_x - corner_xs[2])
                        + (corner_xs[0] - corner_xs[2]) * (sample_z - corner_zs[2])
                    ) / determinant
                    weight_3 = 1 - weight_1 - weight_2
                    inside = (weight_1 >= -1e-4) & (weight_2 >= -1e-4) & (weight_3 >= -1e-4)
                    values = weight_1 * corner_ys[0] + weight_2 * corner_ys[1] + weight_3 * corner_ys[2]
                    region = height[min_z : max_z + 1, min_x : max_x + 1]
                    region[inside] = values[inside]
        self._lod_cache[lod] = (height, ~np.isnan(height))
        return self._lod_cache[lod]


def bezier_spline(object_tag: filedb.STag) -> tuple[str, list, list] | None:
    """A river, lake or coast line object as (kind, points, widths): the Bezier path's points in world units.
    Shared by the new engine's games (117, 1800, 2205); None for any other object."""
    kind = next(
        (candidate for candidate in ("River", "Lake", "CoastLine") if object_tag.find(candidate) is not None), None
    )
    curve = object_tag.path("BezierPath/Path/BezierCurve") if kind else None
    if curve is None:
        return None
    points, widths = [], []
    for point_tag in curve.children:
        if isinstance(point_tag, filedb.STag) and point_tag.find("p") is not None:
            points.append(struct.unpack("<3f", point_tag.find("p").data))
            if point_tag.find("w") is not None:
                widths.append(struct.unpack("<f", point_tag.find("w").data)[0])
    return kind, points, widths


def inland_water(object_tag: filedb.STag) -> dict | None:
    """A river or lake object's flow and look: ``kind``, per point of its path its flow speeds (``u0``: the left
    shore's, the centre's, the right shore's; None for a point without), and its ``River`` or ``Lake`` block's
    values by field (floats, a tuple for a colour, ``RenderOrder`` and ``RenderFlags`` integers, 1800's
    ``WaterColor`` its red, green and blue bytes); None for any
    other object (rendering/water.md 1.10)."""
    kind = next((candidate for candidate in ("River", "Lake") if object_tag.find(candidate) is not None), None)
    curve = object_tag.path("BezierPath/Path/BezierCurve") if kind else None
    if curve is None:
        return None
    speeds = []
    for point_tag in curve.children:
        if isinstance(point_tag, filedb.STag) and point_tag.find("p") is not None:
            u0 = point_tag.find("u0")
            speeds.append(struct.unpack("<3f", u0.data) if u0 is not None and len(u0.data) == 12 else None)
    look = {}
    for field in object_tag.find(kind).children:
        data = getattr(field, "data", None)
        if field.name in ("RenderOrder", "RenderFlags") and data and len(data) == 4:  # integers
            look[field.name] = struct.unpack("<i", data)[0]
        elif field.name == "WaterColor" and data and len(data) == 4:  # 1800's packed colour, bytes b, g, r, a
            look[field.name] = tuple(data[2::-1])
        elif not isinstance(field, filedb.STag) and data and len(data) in (4, 12):
            values = struct.unpack(f"<{len(data) // 4}f", data)
            look[field.name] = values[0] if len(values) == 1 else values
    return {"kind": kind, "speeds": speeds, "look": look}


def render_all(view: CIslandView, out: Path, pixels_per_tile: int = 4) -> list[str]:
    from PIL import Image

    out.mkdir(parents=True, exist_ok=True)
    top = view.shaded(pixels_per_tile)
    Image.fromarray((top * 255).astype(np.uint8)).save(out / "top.png")
    oblique_image = view.oblique(top)
    Image.fromarray((oblique_image * 255).astype(np.uint8)).save(out / "oblique.png")
    tile_count = view.size

    def to_image(heights):
        gray = np.clip((np.nan_to_num(heights, nan=-60) + 60) / 100, 0, 1)
        return np.dstack([gray, gray, gray])

    # LOD sheet: the heightmap, then the stored meshes of LOD 2 and up; magenta = flagged node
    # without mesh, red = mesh outside the active area
    active = view.active_tiles()
    tiles = [to_image(view._resample(view.height_map, tile_count))]
    levels = view.rd3d.path("Terrain/Levels").findall("#1")
    for lod in range(2, len(levels)):
        lod_height, coverage = view.lod_heights(lod)
        image = to_image(lod_height)
        flags = np.asarray(read_bits(levels[lod].path("HasMeshArray")), dtype=bool)
        node = node_tiles(tile_count, flags.shape[0])
        has_nodes = np.repeat(np.repeat(flags, node, 0), node, 1)[:tile_count, :tile_count]
        image[has_nodes & ~coverage & active] = (1, 0, 1)
        image[coverage & ~active] = (1, 0, 0)
        tiles.append(image)
    cols = 4
    row_count = -(-len(tiles) // cols)
    sheet = np.ones((row_count * (tile_count + 8), cols * (tile_count + 8), 3))
    for k, image in enumerate(tiles):
        row, column = divmod(k, cols)
        sheet[
            row * (tile_count + 8) : row * (tile_count + 8) + tile_count,
            column * (tile_count + 8) : column * (tile_count + 8) + tile_count,
        ] = image
    Image.fromarray((sheet * 255).astype(np.uint8)).save(out / "lod.png")
    report = view.check()
    (out / "report.txt").write_text("\n".join(report) + "\n", encoding="utf-8", newline="\n")
    return report
