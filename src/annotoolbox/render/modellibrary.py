"""Models for the PC application's 3D view (``desktoprender``): a model config resolved into its parts
(``modelparts.model_parts``, nested configs included; a mesh of several materials as one part each), each part's
mesh read from the GLB the editor makes of it (``rdmglb``) or, for banner leaves, the cards of its RDM
(``billboards.read_banner``), and its textures, loaded in background threads; the view draws every model's parts
once for all objects that use it (instancing). Composed in Anno's own space and mirrored once at the end (view x =
size - x); the effects a config places are listed (``placed_effects``) and load when the view asks.
"""

from __future__ import annotations

import io
import json
import struct
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..models import skinning
from .particledraw import SPlaced

BANNER_SHADER, WATER_SHADER, FOAM_SHADER = 28, 7, 31  # drawn by shaders of their own (shaders); 31: the foam sheet's
BANNER_ALBEDO = 1.0  # as the game's shader (shader117.BANNER_ALBEDO, plain 1.0; archived 0.55)
MAX_DEPTH = 4  # nested configs followed this deep (a config that nests itself would never end)
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_TYPES = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}


def trs(position, rotation, scale) -> np.ndarray:
    """A 4x4 placement: translate, then rotate (quaternion x, y, z, w), then scale (a number or per axis)."""
    matrix = np.identity(4, "f4")
    matrix[:3, :3] = skinning.quat_matrix(rotation).astype("f4") * np.broadcast_to(np.asarray(scale, "f4"), 3)[None, :]
    matrix[:3, 3] = position
    return matrix


def mirror(size_tiles: float) -> np.ndarray:
    """Anno's space to the view's: x -> size - x."""
    matrix = np.identity(4, "f4")
    matrix[0, 0], matrix[0, 3] = -1, size_tiles
    return matrix


def instance_matrices(
    positions: np.ndarray, quaternions: np.ndarray, scales: np.ndarray, size_tiles: float
) -> np.ndarray:
    """Many placements at once, mirrored into the view: [n, 4, 4] (rows), from positions [n, 3], quaternions
    [n, 4] and scales [n, 3]; a scale of 0 hides one."""
    unit_quaternions = quaternions / np.maximum(np.linalg.norm(quaternions, axis=1, keepdims=True), 1e-12)
    x, y, z, real = unit_quaternions.T
    rotations = np.empty((len(unit_quaternions), 3, 3), np.float32)
    rotations[:, 0] = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * real), 2 * (x * z + y * real)], -1)
    rotations[:, 1] = np.stack([2 * (x * y + z * real), 1 - 2 * (x * x + z * z), 2 * (y * z - x * real)], -1)
    rotations[:, 2] = np.stack([2 * (x * z - y * real), 2 * (y * z + x * real), 1 - 2 * (x * x + y * y)], -1)
    matrices = np.zeros((len(unit_quaternions), 4, 4), np.float32)
    matrices[:, :3, :3] = rotations * scales[:, None, :]
    matrices[:, :3, 3] = positions
    matrices[:, 3, 3] = 1
    matrices[:, 0, :] *= -1  # the mirror: x -> size - x
    matrices[:, 0, 3] += size_tiles
    return matrices


def read_glb(data: bytes) -> list[tuple[np.ndarray, np.ndarray]]:
    """The triangles of a binary glTF: per primitive (vertices [n, 8] position, normal, uv; indices),
    with the nodes' transforms baked in."""
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB file")
    chunks, offset = {}, 12
    while offset < len(data):
        chunk_length, kind = struct.unpack_from("<I4s", data, offset)
        chunks[kind] = data[offset + 8 : offset + 8 + chunk_length]
        offset += 8 + chunk_length
    doc, binary = json.loads(chunks[b"JSON"]), chunks.get(b"BIN\x00", b"")

    def accessor(index: int) -> np.ndarray:
        accessor_record = doc["accessors"][index]
        view = doc["bufferViews"][accessor_record["bufferView"]]
        component_count, dtype = (
            _COMPONENTS[accessor_record["type"]],
            np.dtype(_TYPES[accessor_record["componentType"]]),
        )
        start = view.get("byteOffset", 0) + accessor_record.get("byteOffset", 0)
        stride = view.get("byteStride", component_count * dtype.itemsize)
        raw = np.frombuffer(
            binary,
            np.uint8,
            count=stride * (accessor_record["count"] - 1) + component_count * dtype.itemsize,
            offset=start,
        )
        rows = np.lib.stride_tricks.as_strided(
            raw, (accessor_record["count"], component_count * dtype.itemsize), (stride, 1)
        )
        values = np.ascontiguousarray(rows).view(dtype).reshape(accessor_record["count"], component_count).astype("f4")
        if accessor_record.get("normalized") and dtype.kind in "iu":
            values /= np.iinfo(dtype).max
        return values

    def local(node: dict) -> np.ndarray:
        if "matrix" in node:
            return np.asarray(node["matrix"], "f4").reshape(4, 4).T
        return trs(node.get("translation", [0, 0, 0]), node.get("rotation", [0, 0, 0, 1]), node.get("scale", [1, 1, 1]))

    primitives = []

    def walk(index: int, parent: np.ndarray) -> None:
        node = doc["nodes"][index]
        world = parent @ local(node)
        for primitive in doc["meshes"][node["mesh"]]["primitives"] if "mesh" in node else []:
            attributes = primitive["attributes"]
            if "POSITION" not in attributes or primitive.get("mode", 4) != 4:
                continue
            positions = accessor(attributes["POSITION"])
            normals = (
                accessor(attributes["NORMAL"])
                if "NORMAL" in attributes
                else np.tile([[0, 1, 0]], (len(positions), 1)).astype("f4")
            )
            uvs = (
                accessor(attributes["TEXCOORD_0"])
                if "TEXCOORD_0" in attributes
                else np.zeros((len(positions), 2), "f4")
            )
            indices = (
                accessor(primitive["indices"]).astype("u4").ravel()
                if "indices" in primitive
                else np.arange(len(positions), dtype="u4")
            )
            positions = positions @ world[:3, :3].T + world[:3, 3]
            normals = normals @ np.linalg.inv(world[:3, :3])  # the inverse transpose, applied from the right
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-8)
            primitives.append((np.concatenate([positions, normals, uvs[:, :2]], axis=1).astype("f4"), indices))
        for child in node.get("children", []):
            walk(child, world)

    scene = doc["scenes"][doc.get("scene", 0)] if doc.get("scenes") else {"nodes": range(len(doc["nodes"]))}
    for index in scene["nodes"]:
        walk(index, np.identity(4, "f4"))
    return primitives


def banner_arrays(banner: dict) -> list[tuple[np.ndarray, np.ndarray]]:
    """A banner mesh's cards as one primitive: vertices [n, 10] (centre, outward direction, uv, corner offset)."""
    positions = np.asarray(banner["positions"], "f4").reshape(-1, 3)
    count = len(positions)
    vertices = np.concatenate(
        [
            positions,
            np.asarray(banner["radial"], "f4").reshape(count, 3),
            np.asarray(banner["uvs"], "f4").reshape(count, 2),
            np.asarray(banner["corners"], "f4").reshape(count, 2),
        ],
        axis=1,
    )
    return [(np.ascontiguousarray(vertices, "f4"), np.asarray(banner["indices"], "u4"))]


def bounds(parts: list[dict], meshes: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """A model's box in its own space (Anno's axes) from its parts' vertices; banner cards widened by their size."""
    low, high = np.full(3, np.inf, "f4"), np.full(3, -np.inf, "f4")
    for part in parts:
        for verts, _ in meshes.get(part["mesh_key"]) or []:
            placed = verts[:, :3] @ part["matrix"][:3, :3].T + part["matrix"][:3, 3]
            pad = np.abs(verts[:, 8:10]).max() if verts.shape[1] >= 10 else 0.0
            low, high = np.minimum(low, placed.min(axis=0) - pad), np.maximum(high, placed.max(axis=0) + pad)
    return (low, high) if np.isfinite(low).all() else None


def box_mesh(primitives: list) -> list[tuple[np.ndarray, np.ndarray]]:
    """The box of a mesh's primitives as one primitive of the same vertex layout (8 corners, 12 triangles; banner
    cards widened by their size): what a part keeps while the game's look draws it (picking, the model's bounds)."""
    found = [vertex_array for vertex_array, _i in primitives if len(vertex_array)]
    if not found:
        return []
    column_count = found[0].shape[1]
    pad = max(
        float(np.abs(vertex_array[:, 8:10]).max()) if vertex_array.shape[1] >= 10 else 0.0 for vertex_array in found
    )
    low = np.min([vertex_array[:, :3].min(axis=0) for vertex_array in found], axis=0) - pad
    high = np.max([vertex_array[:, :3].max(axis=0) for vertex_array in found], axis=0) + pad
    corners = np.array(
        [[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])], "f4"
    )
    verts = np.zeros((8, column_count), "f4")
    verts[:, :3], verts[:, 4] = corners, 1.0  # the normal up: picking reads no light
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = np.array(
        [[first, second, third, first, third, fourth] for first, second, third, fourth in faces], "<u4"
    ).ravel()
    return [(verts, indices)]


class CModelLibrary:
    """What the view draws of each model config, loaded in the background from ``editor`` (its ``model``,
    ``mesh_glb``, ``banner`` and ``texture_png``). ``parts(cfg)`` gives the resolved parts as dicts (mesh,
    textures, colour, shader, flow, flipbook, part matrix in the model's own space, the material's number and fields),
    ``decals(cfg)`` its decals
    (``modelparts._decals`` with their matrix); ``mesh`` and ``texture`` the loaded data. ``changed`` is set whenever something new is ready.
    ``set_full`` names the models loaded in full (meshes, coarser levels, maps); the others keep their parts' boxes
    alone (``box``: the game's look draws them; island-117-view.md "Speed", part 6)."""

    def __init__(self, editor, workers: int = 4) -> None:
        self.m_editor = editor
        self.m_pool = ThreadPoolExecutor(workers, thread_name_prefix="models")
        self._m_lock = threading.Lock()
        self._m_full: set[str] | None = None  # the configs loaded in full (None: every one)
        self._m_boxes: dict[str, list] = {}  # mesh key -> its box as one primitive (``box_mesh``)
        self._m_users: dict[str, set[str]] = {}  # mesh key or map key -> the configs whose parts name it
        self._m_decal_maps: set[str] = set()  # the decals' map keys: the view lays the decals in every look
        self._m_parts: dict[str, list | None] = {}
        self._m_placed: dict[str, list[SPlaced]] = {}  # cfg -> the effects it places, in the model's space
        self._m_decals: dict[str, list[dict]] = {}  # cfg -> the decals it lays, in the model's space
        self._m_effects: dict[str, list | None] = {}  # effect file -> its effects; None loading, [] failed
        self._m_meshes: dict[str, object] = {}  # key -> list of (vertices, indices); [] failed; None loading
        self._m_textures: dict[
            str, object
        ] = {}  # key -> (width, height, components, bytes); None loading, False failed
        self.m_changed = threading.Event()
        self._m_closed = False

    def request(self, cfgs) -> None:
        for cfg in cfgs:
            if not cfg:
                continue
            with self._m_lock:
                if cfg in self._m_parts:
                    continue
                self._m_parts[cfg] = None
            self._submit(self._resolve, cfg)

    def keep_decal_map(self, path: str) -> None:
        """Keep the map at ``path`` loaded as a decal's (a layer of the view that lays its own decals, not a model's)."""
        with self._m_lock:
            self._m_decal_maps.add(f"d:{path}")

    def parts(self, cfg: str) -> list | None:
        """The parts once resolved (a failed config: []); None while it is resolving."""
        with self._m_lock:
            found = self._m_parts.get(cfg)
            return None if found is None else list(found)

    def decals(self, cfg: str) -> list[dict]:
        """The decals a resolved config lays (nested configs included), each with its ``matrix``; empty while it
        resolves."""
        with self._m_lock:
            return list(self._m_decals.get(cfg, ()))

    def mesh(self, key: str):
        return self._m_meshes.get(key)

    def box(self, key: str):
        """A part mesh's box as one primitive ([] for a mesh that does not read), None while it loads."""
        return self._m_boxes.get(key)

    def full(self, cfg: str) -> bool:
        return self._m_full is None or cfg in self._m_full

    def set_full(self, cfgs: set[str] | None) -> bool:
        """Load the models ``cfgs`` in full (None: all), let the full data of the others go: their meshes, levels
        and decoded maps no full model shares (a decal's maps stay: the view lays the decals in every look); whether
        that changed anything."""
        cfgs = None if cfgs is None else set(cfgs)
        if cfgs == self._m_full:
            return False
        before, self._m_full = self._m_full, cfgs
        with self._m_lock:
            resolved = [config for config, parts in self._m_parts.items() if parts is not None]
        for cfg in resolved:
            was, now = before is None or cfg in before, self.full(cfg)
            if now and not was:
                self._submit(self._load_full, cfg)
        if cfgs is not None:
            self._forget_unwanted()
        return True

    def wanted(self) -> tuple[set[str], set[str]]:
        """The mesh keys and map keys the full models use (the decals' maps: every model's)."""
        with self._m_lock:
            users = list(self._m_users.items())
            maps = set(self._m_decal_maps)
        meshes = set()
        for key, cfgs in users:
            if any(self.full(config) for config in cfgs):
                (maps if key[:2] in ("d:", "n:") else meshes).add(key)
        return meshes, maps

    def _wanted(self, key: str) -> bool:
        """Whether a full model (or a decal) still uses the mesh or map ``key``: what finishes loading after its
        model was let go is dropped, not kept for good."""
        if self._m_full is None or key in self._m_decal_maps:
            return True
        with self._m_lock:
            cfgs = tuple(self._m_users.get(key, ()))
        return any(self.full(config) for config in cfgs)

    def _forget_unwanted(self) -> None:
        meshes, maps = self.wanted()
        with self._m_lock:
            for key in [
                loaded_key
                for loaded_key, loaded in self._m_meshes.items()
                if loaded is not None and loaded_key.split("@")[0] not in meshes
            ]:
                del self._m_meshes[key]
            for key in [
                loaded_key
                for loaded_key, loaded in self._m_textures.items()
                if loaded is not None and loaded_key[:2] in ("d:", "n:")
            ]:
                if key not in maps:
                    del self._m_textures[key]
        self.m_changed.set()

    def placed_effects(self, cfg: str) -> list[SPlaced]:
        """The particle effects a resolved config places (nested configs included); empty while it resolves."""
        with self._m_lock:
            return list(self._m_placed.get(cfg, ()))

    def request_effects(self, cfg: str) -> None:
        """Start loading the effect files of ``cfg`` (and their textures); ``effect`` has them when they are in."""
        for placed in self.placed_effects(cfg):
            with self._m_lock:
                if placed.file in self._m_effects:
                    continue
                self._m_effects[placed.file] = None
            self._submit(self._load_effect, placed.file)

    def effect(self, path: str) -> list | None:
        """The effects of a file: None while it loads, [] when it cannot be read."""
        with self._m_lock:
            return self._m_effects.get(path)

    def effects_pending(self) -> int:
        """Effect files still loading and effect textures still loading."""
        with self._m_lock:
            files = sum(1 for value in self._m_effects.values() if value is None)
            return files + sum(1 for key, value in self._m_textures.items() if value is None and key.startswith("fx:"))

    def texture(self, key: str):
        """A map's pixels (width, height, components, bytes; the bytes None once ``drop_pixels``), False when it does
        not load, None while it loads: a part's or decal's map (``d:``, ``n:``) starts loading when first asked for
        (a resolved model asks for its maps after its meshes and coarser levels: the island shows first); a map no
        full model uses is not read (None)."""
        wanted = key[:2] in ("d:", "n:") and key not in self._m_textures and self._wanted(key)
        with self._m_lock:
            if wanted and key not in self._m_textures:
                self._m_textures[key] = None
                self._submit(self._decode_texture, key, key[2:], key.startswith("n:"))
            return self._m_textures.get(key)

    def textures_pending(self) -> int:
        with self._m_lock:
            return sum(1 for value in self._m_textures.values() if value is None)

    def drop_pixels(self, key: str) -> None:
        """Forget a texture's pixels once the GPU has them (its size stays): a continental island's maps took
        gigabytes kept twice."""
        with self._m_lock:
            got = self._m_textures.get(key)
            if got:
                self._m_textures[key] = (*got[:3], None)

    def ready(self, cfg: str) -> bool:
        """Resolved, and every part's mesh loaded (or failed); for a model not loaded in full its boxes."""
        parts = self.parts(cfg)
        found = self._m_meshes if self.full(cfg) else self._m_boxes
        return parts is not None and all(found.get(part["mesh_key"]) is not None for part in parts)

    def close(self) -> None:
        self._m_closed = True
        self.m_pool.shutdown(wait=False, cancel_futures=True)

    def _submit(self, work, *arguments) -> bool:
        """``work(*args)`` on a worker; nothing once the library is closed (a worker still running, or a view's last
        frame, asks for more: what it asked for stays loading)."""
        if self._m_closed:
            return False
        try:
            self.m_pool.submit(work, *arguments)
        except RuntimeError:  # closed between the check and the call
            return False
        return True

    # background work
    def _flatten(self, cfg: str, matrix: np.ndarray, depth: int, parts: list, effects: list, decals: list,
                 deform: bool = False) -> None:  # fmt: skip
        """A config's parts, effects and decals placed by ``matrix``, its nested configs' after them; ``deform``: a
        FILE above bends it to the terrain (``modelparts``)."""
        spec = self.m_editor.model(cfg)
        for decal in spec.get("decals", ()):
            decals.append({**decal, "matrix": matrix @ trs(decal["position"], decal["rotation"], decal["scale"])})
        for effect in spec.get("effects", ()):
            effects.append(
                SPlaced(
                    effect["file"],
                    matrix @ trs(effect["position"], effect["rotation"], effect["scale"]),
                    tuple(effect.get("color") or (1.0, 1.0, 1.0)),
                    float(effect.get("alpha", 1.0)),
                    float(effect.get("speed", 1.0)),
                    bool(effect.get("started", True)),
                    tuple(effect["sheet"]) if effect.get("sheet") else None,
                )
            )
        for part in spec["parts"]:
            groups = part.get("materials") or {}
            # a mesh of several materials draws each submesh group with its own maps (the mesh cut by material)
            for number, material in groups.items() if len(groups) > 1 else [(None, part)]:
                shader = material.get("shader", 8)
                banner = shader == BANNER_SHADER
                color = tuple(min(channel, 1.5) for channel in material.get("color", (1, 1, 1)))
                key = ("banner:" if banner else "glb:") + part["mesh"] + (f"#{number}" if number is not None else "")
                parts.append(
                    {
                        "mesh": part["mesh"],
                        "mesh_key": key,
                        "diffuse": material.get("diffuse") or "",
                        "normal": "" if shader == WATER_SHADER else material.get("normal") or "",
                        "color": tuple(channel * BANNER_ALBEDO for channel in color) if banner else color,
                        "shader": shader,
                        "flow": material.get("flow") or 0.0,
                        "flipbook": material.get("flipbook"),
                        "foam": material.get("foam"),  # a foam sheet: (speed, speed, opacity)
                        "matrix": matrix @ trs(part["position"], part["rotation"], part["scale"]),
                        "material": number,  # the mesh's material number (None: the whole mesh)
                        "values": material.get("values") or {},  # the MATERIAL's fields (the game's own shaders)
                        "lod_materials": part.get("lod_materials") or [],  # per level the config's material per number
                        "deform": deform or bool(part.get("deform")),  # bent to the terrain (the game's shaders)
                    }
                )
        if depth < MAX_DEPTH:
            for nested in spec["files"]:
                try:
                    self._flatten(nested["cfg"], matrix @ trs(nested["position"], nested["rotation"], nested["scale"]), depth + 1, parts, effects,
                                  decals, deform or bool(nested.get("deform")))  # fmt: skip
                except (
                    Exception
                ):  # pattern: ok (loader thread: a nested config that does not read, the rest still draws)
                    continue

    def _resolve(self, cfg: str) -> None:
        parts: list = []
        effects: list = []
        decals: list = []
        try:
            self._flatten(cfg, np.identity(4, "f4"), 0, parts, effects, decals)
        except Exception:  # pattern: ok (loader thread: a config that does not read draws as no parts)
            parts, effects, decals = [], [], []
        with self._m_lock:
            self._m_parts[cfg] = parts
            self._m_placed[cfg] = effects
            self._m_decals[cfg] = decals
            self._m_decal_maps |= {f"d:{decal['diffuse']}" for decal in decals if decal.get("diffuse")}
            for part in parts:
                keys = [part["mesh_key"], *(f"{kind[0]}:{part[kind]}" for kind in ("diffuse", "normal") if part[kind])]
                for key in keys:
                    self._m_users.setdefault(key, set()).add(cfg)
        if not self.full(cfg):
            self._load_boxes(parts)
            return
        self._load_full(cfg)

    def _load_boxes(self, parts: list) -> None:
        for part in parts:
            self._load_box(part["mesh_key"], part["mesh"])
        self.m_changed.set()

    def _load_full(self, cfg: str) -> None:
        """A resolved model's meshes, then its coarser levels, then its maps (the island shows first)."""
        parts = self.parts(cfg) or []
        for part in parts:  # the maps after the meshes and levels (below)
            if not self.full(cfg):  # let go while it waited or loaded: the game's look draws it, by its boxes
                self._load_boxes(parts)
                return
            self._load_mesh(part["mesh_key"], part["mesh"])
        self.m_changed.set()
        decals = self.decals(cfg)
        for part in parts:  # the coarser levels after every part's finest: the island shows first
            self._submit(self._load_levels, part)
        for key in [f"d:{item['diffuse']}" for item in (*parts, *decals) if item["diffuse"]] + [
            f"n:{part['normal']}" for part in parts if part["normal"]
        ]:  # the maps last: none is grey when the camera first comes near (``texture`` starts them)
            self.texture(key)

    def lods(self, part: dict) -> list | None:
        """A part's coarser levels of detail (level 1 first), each a list of (vertices, indices) like ``mesh``; None
        while they load, [] for a part without them."""
        return self._m_meshes.get(part["mesh_key"] + "@levels")

    def _load_levels(self, part: dict) -> None:
        """A part's ``_lodN`` meshes cut to its material (``MaterialLODInfos``) as the game draws them; a level whose
        material is gone, or that does not read, ends the chain (rendering/camera.md 3)."""
        from ..models.configtree import lod_files
        from .cull import level_numbers

        key = part["mesh_key"] + "@levels"
        with self._m_lock:
            if key in self._m_meshes:  # a mesh several configs share: loaded once
                return
            self._m_meshes[key] = None
        exists = getattr(getattr(self.m_editor, "m_files", None), "exists", None)
        try:
            paths = lod_files(part["mesh"], exists)[1:] if exists and not key.startswith("banner:") else []
        except Exception:  # pattern: ok (loader thread: a folder that does not list gives no coarser levels)
            paths = []
        levels: list = []
        for level, path in enumerate(paths, 1):
            numbers = level_numbers(part.get("lod_materials") or [], part.get("material"), level)
            try:
                glbs = (
                    [self.m_editor.mesh_glb(path)]
                    if numbers is None
                    else [self.m_editor.mesh_glb(path, material=material_number) for material_number in numbers]
                )
                found = [prim for glb in glbs for prim in read_glb(glb)]
            except Exception:  # pattern: ok (loader thread: a level that does not read, the finer ones stand)
                break
            if not found:
                break
            levels.append(found)
        self._keep_or_drop(self._m_meshes, key, levels, part["mesh_key"])

    def _keep_or_drop(self, store: dict, key: str, data, user_key: str | None = None) -> None:
        """Loaded ``data`` kept under ``key``, or dropped when no full model wants it any more (its model was let go
        while it loaded: read again when it is asked for)."""
        wanted = self._wanted(user_key or key)
        with self._m_lock:
            if wanted:
                store[key] = data
            else:
                store.pop(key, None)
        self.m_changed.set()

    def _read_mesh(self, key: str, path: str) -> list:
        try:
            if key.startswith("banner:"):
                return banner_arrays(self.m_editor.banner(path))
            if "#" in key:  # one material's submeshes
                return read_glb(self.m_editor.mesh_glb(path, material=int(key.rsplit("#", 1)[1])))
            return read_glb(self.m_editor.mesh_glb(path))
        except Exception:  # pattern: ok (loader thread: an unreadable mesh leaves that part undrawn)
            return []

    def _load_mesh(self, key: str, path: str) -> None:
        with self._m_lock:
            if key in self._m_meshes:  # loaded or loading
                return
            self._m_meshes[key] = None
        data = self._read_mesh(key, path)
        self._m_boxes.setdefault(key, box_mesh(data))
        self._keep_or_drop(self._m_meshes, key, data)

    def _load_box(self, key: str, path: str) -> None:
        """A part mesh's box alone: the mesh read and let go (the game's look draws the part)."""
        with self._m_lock:
            if key in self._m_boxes or key in self._m_meshes:
                return
            self._m_boxes[key] = None
        self._m_boxes[key] = box_mesh(self._read_mesh(key, path))

    def _load_effect(self, path: str) -> None:
        try:
            found = list(self.m_editor.effect(path))
        except Exception:  # pattern: ok (loader thread: an effect file that does not read, the model draws without it)
            found = []
        try:
            for effect in found:
                for index in range(len(effect.materials)):
                    texture = effect.texture_of(index)
                    if texture:
                        self._load_texture("fx:" + texture, texture, normal=False, effect=True)
        except Exception:  # pattern: ok (loader thread: an effect that names no maps, the file is not left loading)
            pass
        with self._m_lock:
            self._m_effects[path] = found
        self.m_changed.set()

    def _load_texture(self, key: str, path: str, normal: bool, effect: bool = False) -> None:
        with self._m_lock:
            if key in self._m_textures:
                return
            self._m_textures[key] = None
        self._decode_texture(key, path, normal, effect)

    def _decode_texture(self, key: str, path: str, normal: bool, effect: bool = False) -> None:
        try:
            from PIL import Image

            mode = "RGB" if normal else "RGBA"
            png = (
                self.m_editor.effect_texture_png(path) if effect else self.m_editor.texture_png(path, is_normal=normal)
            )
            image = Image.open(io.BytesIO(png)).convert(mode)
            got = (image.width, image.height, len(mode), image.tobytes())
        except Exception:  # pattern: ok (loader thread: any decode failure leaves the part untextured for good)
            got = False
        if key[:2] in ("d:", "n:"):  # a part's or decal's map: kept only while a full model or a decal uses it
            self._keep_or_drop(self._m_textures, key, got)
            return
        self._m_textures[key] = got
        self.m_changed.set()
