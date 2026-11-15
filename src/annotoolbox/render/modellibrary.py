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
from .particledraw import Placed

BANNER_SHADER, WATER_SHADER = 28, 7  # drawn by shaders of their own (shaders)
BANNER_ALBEDO = 1.0  # as the game's shader (shader117.BANNER_ALBEDO, plain 1.0; archived 0.55)
MAX_DEPTH = 4  # nested configs followed this deep (a config that nests itself would never end)
_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_TYPES = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}


def trs(position, rotation, scale) -> np.ndarray:
    """A 4x4 placement: translate, then rotate (quaternion x, y, z, w), then scale (a number or per axis)."""
    m = np.identity(4, "f4")
    m[:3, :3] = skinning.quat_matrix(rotation).astype("f4") * np.broadcast_to(np.asarray(scale, "f4"), 3)[None, :]
    m[:3, 3] = position
    return m


def mirror(size: float) -> np.ndarray:
    """Anno's space to the view's: x -> size - x."""
    m = np.identity(4, "f4")
    m[0, 0], m[0, 3] = -1, size
    return m


def instance_matrices(pos: np.ndarray, quat: np.ndarray, scale: np.ndarray, size: float) -> np.ndarray:
    """Many placements at once, mirrored into the view: [n, 4, 4] (rows), from positions [n, 3], quaternions
    [n, 4] and scales [n, 3]; a scale of 0 hides one."""
    q = quat / np.maximum(np.linalg.norm(quat, axis=1, keepdims=True), 1e-12)
    x, y, z, w = q.T
    r = np.empty((len(q), 3, 3), np.float32)
    r[:, 0] = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)], -1)
    r[:, 1] = np.stack([2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)], -1)
    r[:, 2] = np.stack([2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], -1)
    m = np.zeros((len(q), 4, 4), np.float32)
    m[:, :3, :3] = r * scale[:, None, :]
    m[:, :3, 3] = pos
    m[:, 3, 3] = 1
    m[:, 0, :] *= -1  # the mirror: x -> size - x
    m[:, 0, 3] += size
    return m


def read_glb(data: bytes) -> list[tuple[np.ndarray, np.ndarray]]:
    """The triangles of a binary glTF: per primitive (vertices [n, 8] position, normal, uv; indices),
    with the nodes' transforms baked in."""
    magic, _version, _length = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB file")
    chunks, off = {}, 12
    while off < len(data):
        n, kind = struct.unpack_from("<I4s", data, off)
        chunks[kind] = data[off + 8 : off + 8 + n]
        off += 8 + n
    doc, binary = json.loads(chunks[b"JSON"]), chunks.get(b"BIN\x00", b"")

    def accessor(k: int) -> np.ndarray:
        a = doc["accessors"][k]
        view = doc["bufferViews"][a["bufferView"]]
        comps, dtype = _COMPONENTS[a["type"]], np.dtype(_TYPES[a["componentType"]])
        start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = view.get("byteStride", comps * dtype.itemsize)
        raw = np.frombuffer(binary, np.uint8, count=stride * (a["count"] - 1) + comps * dtype.itemsize, offset=start)
        rows = np.lib.stride_tricks.as_strided(raw, (a["count"], comps * dtype.itemsize), (stride, 1))
        out = np.ascontiguousarray(rows).view(dtype).reshape(a["count"], comps).astype("f4")
        if a.get("normalized") and dtype.kind in "iu":
            out /= np.iinfo(dtype).max
        return out

    def local(node: dict) -> np.ndarray:
        if "matrix" in node:
            return np.asarray(node["matrix"], "f4").reshape(4, 4).T
        return trs(node.get("translation", [0, 0, 0]), node.get("rotation", [0, 0, 0, 1]), node.get("scale", [1, 1, 1]))

    out = []

    def walk(k: int, parent: np.ndarray) -> None:
        node = doc["nodes"][k]
        world = parent @ local(node)
        for prim in doc["meshes"][node["mesh"]]["primitives"] if "mesh" in node else []:
            attrs = prim["attributes"]
            if "POSITION" not in attrs or prim.get("mode", 4) != 4:
                continue
            pos = accessor(attrs["POSITION"])
            nrm = accessor(attrs["NORMAL"]) if "NORMAL" in attrs else np.tile([[0, 1, 0]], (len(pos), 1)).astype("f4")
            uv = accessor(attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else np.zeros((len(pos), 2), "f4")
            idx = (
                accessor(prim["indices"]).astype("u4").ravel() if "indices" in prim else np.arange(len(pos), dtype="u4")
            )
            pos = pos @ world[:3, :3].T + world[:3, 3]
            nrm = nrm @ np.linalg.inv(world[:3, :3])  # the inverse transpose, applied from the right
            nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
            out.append((np.concatenate([pos, nrm, uv[:, :2]], axis=1).astype("f4"), idx))
        for child in node.get("children", []):
            walk(child, world)

    scene = doc["scenes"][doc.get("scene", 0)] if doc.get("scenes") else {"nodes": range(len(doc["nodes"]))}
    for k in scene["nodes"]:
        walk(k, np.identity(4, "f4"))
    return out


def banner_arrays(banner: dict) -> list[tuple[np.ndarray, np.ndarray]]:
    """A banner mesh's cards as one primitive: vertices [n, 10] (centre, outward direction, uv, corner offset)."""
    pos = np.asarray(banner["positions"], "f4").reshape(-1, 3)
    n = len(pos)
    verts = np.concatenate(
        [
            pos,
            np.asarray(banner["radial"], "f4").reshape(n, 3),
            np.asarray(banner["uvs"], "f4").reshape(n, 2),
            np.asarray(banner["corners"], "f4").reshape(n, 2),
        ],
        axis=1,
    )
    return [(np.ascontiguousarray(verts, "f4"), np.asarray(banner["indices"], "u4"))]


def bounds(parts: list[dict], meshes: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """A model's box in its own space (Anno's axes) from its parts' vertices; banner cards widened by their size."""
    lo, hi = np.full(3, np.inf, "f4"), np.full(3, -np.inf, "f4")
    for p in parts:
        for verts, _ in meshes.get(p["mesh_key"]) or []:
            v = verts[:, :3] @ p["matrix"][:3, :3].T + p["matrix"][:3, 3]
            pad = np.abs(verts[:, 8:10]).max() if verts.shape[1] >= 10 else 0.0
            lo, hi = np.minimum(lo, v.min(axis=0) - pad), np.maximum(hi, v.max(axis=0) + pad)
    return (lo, hi) if np.isfinite(lo).all() else None


def box_mesh(prims: list) -> list[tuple[np.ndarray, np.ndarray]]:
    """The box of a mesh's primitives as one primitive of the same vertex layout (8 corners, 12 triangles; banner
    cards widened by their size): what a part keeps while the game's look draws it (picking, the model's bounds)."""
    found = [v for v, _i in prims if len(v)]
    if not found:
        return []
    cols = found[0].shape[1]
    pad = max(float(np.abs(v[:, 8:10]).max()) if v.shape[1] >= 10 else 0.0 for v in found)
    lo = np.min([v[:, :3].min(axis=0) for v in found], axis=0) - pad
    hi = np.max([v[:, :3].max(axis=0) for v in found], axis=0) + pad
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], "f4")
    verts = np.zeros((8, cols), "f4")
    verts[:, :3], verts[:, 4] = corners, 1.0  # the normal up: picking reads no light
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    idx = np.array([[a, b, c, a, c, d] for a, b, c, d in faces], "<u4").ravel()
    return [(verts, idx)]


class ModelLibrary:
    """What the view draws of each model config, loaded in the background from ``editor`` (its ``model``,
    ``mesh_glb``, ``banner`` and ``texture_png``). ``parts(cfg)`` gives the resolved parts as dicts (mesh,
    textures, colour, shader, flow, flipbook, part matrix in the model's own space, the material's number and fields),
    ``decals(cfg)`` its decals
    (``modelparts._decals`` with their matrix); ``mesh`` and ``texture`` the loaded data. ``changed`` is set whenever something new is ready.
    ``set_full`` names the models loaded in full (meshes, coarser levels, maps); the others keep their parts' boxes
    alone (``box``: the game's look draws them; island-117-view.md "Speed", part 6)."""

    def __init__(self, editor, workers: int = 4) -> None:
        self.editor = editor
        self.pool = ThreadPoolExecutor(workers, thread_name_prefix="models")
        self._lock = threading.Lock()
        self._full: set[str] | None = None  # the configs loaded in full (None: every one)
        self._boxes: dict[str, list] = {}  # mesh key -> its box as one primitive (``box_mesh``)
        self._users: dict[str, set[str]] = {}  # mesh key or map key -> the configs whose parts name it
        self._decal_maps: set[str] = set()  # the decals' map keys: the view lays the decals in every look
        self._parts: dict[str, list | None] = {}
        self._placed: dict[str, list[Placed]] = {}  # cfg -> the effects it places, in the model's space
        self._decals: dict[str, list[dict]] = {}  # cfg -> the decals it lays, in the model's space
        self._effects: dict[str, list | None] = {}  # effect file -> its effects; None loading, [] failed
        self._meshes: dict[str, object] = {}  # key -> list of (vertices, indices); [] failed; None loading
        self._textures: dict[str, object] = {}  # key -> (width, height, components, bytes); None loading, False failed
        self.changed = threading.Event()

    def request(self, cfgs) -> None:
        for cfg in cfgs:
            if not cfg:
                continue
            with self._lock:
                if cfg in self._parts:
                    continue
                self._parts[cfg] = None
            self.pool.submit(self._resolve, cfg)

    def parts(self, cfg: str) -> list | None:
        """The parts once resolved (a failed config: []); None while it is resolving."""
        with self._lock:
            p = self._parts.get(cfg)
            return None if p is None else list(p)

    def decals(self, cfg: str) -> list[dict]:
        """The decals a resolved config lays (nested configs included), each with its ``matrix``; empty while it
        resolves."""
        with self._lock:
            return list(self._decals.get(cfg, ()))

    def mesh(self, key: str):
        return self._meshes.get(key)

    def box(self, key: str):
        """A part mesh's box as one primitive ([] for a mesh that does not read), None while it loads."""
        return self._boxes.get(key)

    def full(self, cfg: str) -> bool:
        return self._full is None or cfg in self._full

    def set_full(self, cfgs: set[str] | None) -> bool:
        """Load the models ``cfgs`` in full (None: all), let the full data of the others go: their meshes, levels
        and decoded maps no full model shares (a decal's maps stay: the view lays the decals in every look); whether
        that changed anything."""
        cfgs = None if cfgs is None else set(cfgs)
        if cfgs == self._full:
            return False
        before, self._full = self._full, cfgs
        with self._lock:
            resolved = [c for c, p in self._parts.items() if p is not None]
        for cfg in resolved:
            was, now = before is None or cfg in before, self.full(cfg)
            if now and not was:
                self.pool.submit(self._load_full, cfg)
        if cfgs is not None:
            self._forget_unwanted()
        return True

    def wanted(self) -> tuple[set[str], set[str]]:
        """The mesh keys and map keys the full models use (the decals' maps: every model's)."""
        with self._lock:
            users = list(self._users.items())
            maps = set(self._decal_maps)
        meshes = set()
        for key, cfgs in users:
            if any(self.full(c) for c in cfgs):
                (maps if key[:2] in ("d:", "n:") else meshes).add(key)
        return meshes, maps

    def _wanted(self, key: str) -> bool:
        """Whether a full model (or a decal) still uses the mesh or map ``key``: what finishes loading after its
        model was let go is dropped, not kept for good."""
        if self._full is None or key in self._decal_maps:
            return True
        with self._lock:
            cfgs = tuple(self._users.get(key, ()))
        return any(self.full(c) for c in cfgs)

    def _forget_unwanted(self) -> None:
        meshes, maps = self.wanted()
        with self._lock:
            for key in [k for k, v in self._meshes.items() if v is not None and k.split("@")[0] not in meshes]:
                del self._meshes[key]
            for key in [k for k, v in self._textures.items() if v is not None and k[:2] in ("d:", "n:")]:
                if key not in maps:
                    del self._textures[key]
        self.changed.set()

    def placed_effects(self, cfg: str) -> list[Placed]:
        """The particle effects a resolved config places (nested configs included); empty while it resolves."""
        with self._lock:
            return list(self._placed.get(cfg, ()))

    def request_effects(self, cfg: str) -> None:
        """Start loading the effect files of ``cfg`` (and their textures); ``effect`` has them when they are in."""
        for placed in self.placed_effects(cfg):
            with self._lock:
                if placed.file in self._effects:
                    continue
                self._effects[placed.file] = None
            self.pool.submit(self._load_effect, placed.file)

    def effect(self, path: str) -> list | None:
        """The effects of a file: None while it loads, [] when it cannot be read."""
        with self._lock:
            return self._effects.get(path)

    def effects_pending(self) -> int:
        """Effect files still loading and effect textures still loading."""
        with self._lock:
            files = sum(1 for v in self._effects.values() if v is None)
            return files + sum(1 for k, v in self._textures.items() if v is None and k.startswith("fx:"))

    def texture(self, key: str):
        """A map's pixels (width, height, components, bytes; the bytes None once ``drop_pixels``), False when it does
        not load, None while it loads: a part's or decal's map (``d:``, ``n:``) starts loading when first asked for
        (a resolved model asks for its maps after its meshes and coarser levels: the island shows first); a map no
        full model uses is not read (None)."""
        wanted = key[:2] in ("d:", "n:") and key not in self._textures and self._wanted(key)
        with self._lock:
            if wanted and key not in self._textures:
                self._textures[key] = None
                self.pool.submit(self._decode_texture, key, key[2:], key.startswith("n:"))
            return self._textures.get(key)

    def textures_pending(self) -> int:
        with self._lock:
            return sum(1 for v in self._textures.values() if v is None)

    def drop_pixels(self, key: str) -> None:
        """Forget a texture's pixels once the GPU has them (its size stays): a continental island's maps took
        gigabytes kept twice."""
        with self._lock:
            got = self._textures.get(key)
            if got:
                self._textures[key] = (*got[:3], None)

    def ready(self, cfg: str) -> bool:
        """Resolved, and every part's mesh loaded (or failed); for a model not loaded in full its boxes."""
        parts = self.parts(cfg)
        found = self._meshes if self.full(cfg) else self._boxes
        return parts is not None and all(found.get(p["mesh_key"]) is not None for p in parts)

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    # background work
    def _flatten(self, cfg: str, matrix: np.ndarray, depth: int, out: list, fx: list, decals: list,
                 deform: bool = False) -> None:  # fmt: skip
        """A config's parts, effects and decals placed by ``matrix``, its nested configs' after them; ``deform``: a
        FILE above bends it to the terrain (``modelparts``)."""
        spec = self.editor.model(cfg)
        for d in spec.get("decals", ()):
            decals.append({**d, "matrix": matrix @ trs(d["position"], d["rotation"], d["scale"])})
        for e in spec.get("effects", ()):
            fx.append(
                Placed(
                    e["file"],
                    matrix @ trs(e["position"], e["rotation"], e["scale"]),
                    tuple(e.get("color") or (1.0, 1.0, 1.0)),
                    float(e.get("alpha", 1.0)),
                    float(e.get("speed", 1.0)),
                    bool(e.get("started", True)),
                    tuple(e["sheet"]) if e.get("sheet") else None,
                )
            )
        for p in spec["parts"]:
            groups = p.get("materials") or {}
            # a mesh of several materials draws each submesh group with its own maps (the mesh cut by material)
            for number, m in groups.items() if len(groups) > 1 else [(None, p)]:
                shader = m.get("shader", 8)
                banner = shader == BANNER_SHADER
                color = tuple(min(c, 1.5) for c in m.get("color", (1, 1, 1)))
                key = ("banner:" if banner else "glb:") + p["mesh"] + (f"#{number}" if number is not None else "")
                out.append(
                    {
                        "mesh": p["mesh"],
                        "mesh_key": key,
                        "diffuse": m.get("diffuse") or "",
                        "normal": "" if shader == WATER_SHADER else m.get("normal") or "",
                        "color": tuple(c * BANNER_ALBEDO for c in color) if banner else color,
                        "shader": shader,
                        "flow": m.get("flow") or 0.0,
                        "flipbook": m.get("flipbook"),
                        "matrix": matrix @ trs(p["position"], p["rotation"], p["scale"]),
                        "material": number,  # the mesh's material number (None: the whole mesh)
                        "values": m.get("values") or {},  # the MATERIAL's fields (the game's own shaders)
                        "lod_materials": p.get("lod_materials") or [],  # per level the config's material per number
                        "deform": deform or bool(p.get("deform")),  # bent to the terrain (the game's shaders)
                    }
                )
        if depth < MAX_DEPTH:
            for f in spec["files"]:
                try:
                    self._flatten(f["cfg"], matrix @ trs(f["position"], f["rotation"], f["scale"]), depth + 1, out, fx,
                                  decals, deform or bool(f.get("deform")))  # fmt: skip
                except Exception:  # a nested config that is missing: the rest of the model still draws
                    continue

    def _resolve(self, cfg: str) -> None:
        parts: list = []
        fx: list = []
        decals: list = []
        try:
            self._flatten(cfg, np.identity(4, "f4"), 0, parts, fx, decals)
        except Exception:
            parts, fx, decals = [], [], []
        with self._lock:
            self._parts[cfg] = parts
            self._placed[cfg] = fx
            self._decals[cfg] = decals
            self._decal_maps |= {f"d:{d['diffuse']}" for d in decals if d.get("diffuse")}
            for p in parts:
                keys = [p["mesh_key"], *(f"{k[0]}:{p[k]}" for k in ("diffuse", "normal") if p[k])]
                for key in keys:
                    self._users.setdefault(key, set()).add(cfg)
        if not self.full(cfg):
            self._load_boxes(parts)
            return
        self._load_full(cfg)

    def _load_boxes(self, parts: list) -> None:
        for p in parts:
            self._load_box(p["mesh_key"], p["mesh"])
        self.changed.set()

    def _load_full(self, cfg: str) -> None:
        """A resolved model's meshes, then its coarser levels, then its maps (the island shows first)."""
        parts = self.parts(cfg) or []
        for p in parts:  # the maps after the meshes and levels (below)
            if not self.full(cfg):  # let go while it waited or loaded: the game's look draws it, by its boxes
                self._load_boxes(parts)
                return
            self._load_mesh(p["mesh_key"], p["mesh"])
        self.changed.set()
        decals = self.decals(cfg)
        for p in parts:  # the coarser levels after every part's finest: the island shows first
            self.pool.submit(self._load_levels, p)
        for key in [f"d:{x['diffuse']}" for x in (*parts, *decals) if x["diffuse"]] + [
            f"n:{p['normal']}" for p in parts if p["normal"]
        ]:  # the maps last: none is grey when the camera first comes near (``texture`` starts them)
            self.texture(key)

    def lods(self, part: dict) -> list | None:
        """A part's coarser levels of detail (level 1 first), each a list of (vertices, indices) like ``mesh``; None
        while they load, [] for a part without them."""
        return self._meshes.get(part["mesh_key"] + "@levels")

    def _load_levels(self, part: dict) -> None:
        """A part's ``_lodN`` meshes cut to its material (``MaterialLODInfos``) as the game draws them; a level whose
        material is gone, or that does not read, ends the chain (rendering/camera.md 3)."""
        from ..models.configtree import lod_files
        from .cull import level_numbers

        key = part["mesh_key"] + "@levels"
        with self._lock:
            if key in self._meshes:  # a mesh several configs share: loaded once
                return
            self._meshes[key] = None
        exists = getattr(getattr(self.editor, "files", None), "exists", None)
        paths = lod_files(part["mesh"], exists)[1:] if exists and not key.startswith("banner:") else []
        levels: list = []
        for level, path in enumerate(paths, 1):
            numbers = level_numbers(part.get("lod_materials") or [], part.get("material"), level)
            try:
                glbs = (
                    [self.editor.mesh_glb(path)]
                    if numbers is None
                    else [self.editor.mesh_glb(path, material=n) for n in numbers]
                )
                found = [prim for glb in glbs for prim in read_glb(glb)]
            except Exception:  # a level that does not read: the finer ones stand
                break
            if not found:
                break
            levels.append(found)
        self._keep_or_drop(self._meshes, key, levels, part["mesh_key"])

    def _keep_or_drop(self, store: dict, key: str, data, user_key: str | None = None) -> None:
        """Loaded ``data`` kept under ``key``, or dropped when no full model wants it any more (its model was let go
        while it loaded: read again when it is asked for)."""
        wanted = self._wanted(user_key or key)
        with self._lock:
            if wanted:
                store[key] = data
            else:
                store.pop(key, None)
        self.changed.set()

    def _read_mesh(self, key: str, path: str) -> list:
        try:
            if key.startswith("banner:"):
                return banner_arrays(self.editor.banner(path))
            if "#" in key:  # one material's submeshes
                return read_glb(self.editor.mesh_glb(path, material=int(key.rsplit("#", 1)[1])))
            return read_glb(self.editor.mesh_glb(path))
        except Exception:  # an unreadable mesh: that part stays undrawn
            return []

    def _load_mesh(self, key: str, path: str) -> None:
        with self._lock:
            if key in self._meshes:  # loaded or loading
                return
            self._meshes[key] = None
        data = self._read_mesh(key, path)
        self._boxes.setdefault(key, box_mesh(data))
        self._keep_or_drop(self._meshes, key, data)

    def _load_box(self, key: str, path: str) -> None:
        """A part mesh's box alone: the mesh read and let go (the game's look draws the part)."""
        with self._lock:
            if key in self._boxes or key in self._meshes:
                return
            self._boxes[key] = None
        self._boxes[key] = box_mesh(self._read_mesh(key, path))

    def _load_effect(self, path: str) -> None:
        try:
            found = list(self.editor.effect(path))
        except Exception:  # an effect file that does not read: the model draws without it
            found = []
        for effect in found:
            for index in range(len(effect.materials)):
                texture = effect.texture_of(index)
                if texture:
                    self._load_texture("fx:" + texture, texture, normal=False, effect=True)
        with self._lock:
            self._effects[path] = found
        self.changed.set()

    def _load_texture(self, key: str, path: str, normal: bool, effect: bool = False) -> None:
        with self._lock:
            if key in self._textures:
                return
            self._textures[key] = None
        self._decode_texture(key, path, normal, effect)

    def _decode_texture(self, key: str, path: str, normal: bool, effect: bool = False) -> None:
        try:
            from PIL import Image

            mode = "RGB" if normal else "RGBA"
            png = self.editor.effect_texture_png(path) if effect else self.editor.texture_png(path, normal=normal)
            img = Image.open(io.BytesIO(png)).convert(mode)
            got = (img.width, img.height, len(mode), img.tobytes())
        except Exception:
            got = False  # failed: drawn untextured for good
        if key[:2] in ("d:", "n:"):  # a part's or decal's map: kept only while a full model or a decal uses it
            self._keep_or_drop(self._textures, key, got)
            return
        self._textures[key] = got
        self.changed.set()
