"""An island opened in the editor: what the 3D view draws and what the tools read and write.

``IslandEditor`` opens a 117 island (built or vanilla) and offers its heights, ground, water, objects, props
and forest, each model's parts with its mesh as glTF (``rdmglb``) and its textures as PNG (texconv), the edit
file, and the model library. Files come from the mod folder first, then the game (``islandview.Files``), so a
built island and a vanilla one open the same way; converted meshes and textures are cached under ``cache``.
Anno 2070 editing (``formats.isd.edit``) and the other games' viewers (``gameviewers``) are subclasses that read their
islands into the same shape. The PC application (``desktop``) calls these directly: ``docs/island-editor.md``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import threading
from pathlib import Path

import numpy as np

from ..formats import a7mread, filedb
from ..infra import piecejson
from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from ..islands import editorassets, herdareas, islandedits, sessionsettings, slotlist, surfmap
from ..islands.islandgrid import HEIGHT_UNITS, read_bits, read_sparse
from ..islands.islandview import Files, IslandView
from ..models import configtree, fileresolve
from ..sources.overlay import normalize
from ..suite import features
from . import assetmodels, cellbrush, footprints, slotkinds
from .islandassets import IslandAssets, whole_file
from .modelparts import library_group, model_parts

# Big islands are read coarser: the continental island (768 tiles) at the full 2 samples per tile
# made a terrain mesh of 4.7 million triangles and a ground image of 3072 pixels, 21 s to draw.
ISLAND_JSON = 10  # the cached island data's layout: a change makes the editor read the island again
FINE_TERRAIN_MAX = 512  # tiles: larger islands get 1 height sample per tile
ALBEDO_MAX = 2048  # pixels across the ground image
GROUND_MAX = 2048  # texels across the ground's weight maps (the view blends the materials by them)
GROUND_TEXELS = 4  # weight texels per tile at most
# what the model library offers: 117's model folders, 2070's landscape templates
LIBRARY_117 = re.compile(r"^data/[^/]+/graphics/(library|roman|celtic|roman_celtic)/.+\.cfg$")
LIBRARY_2070 = {
    "NatureMountainObject",
    "SimpleStoneObject",
    "Vegetation",
    "MinePathObject",
    "Tree",
    "SimpleBlocking",
    "SimpleSelectable",
    "SimpleObject",
    "ExplorableObject",
}
VANILLA_TREE_GUID = 27743  # the Roman region's tree: what a 117 island's forest tiles are drawn with


class IslandEditor(IslandAssets):
    """A 117 island (``files``: the mod folder first, then the game) with its edit file. ``tree2070``: the
    2070 install (``overlay.SourceTree``), for the library's 2070 models; ``mod_assets``: the mod's
    assets.xml, where a slot or tree asset without a config of its own finds its model."""

    def __init__(
        self,
        files: Files,
        a7m_path: str,
        cache: Path,
        mod_assets: str = "",
        edits: Path | None = None,
        tree2070=None,
    ) -> None:
        self.files = files
        self.view = IslandView(files, a7m_path)
        self.a7m_path = a7m_path
        self._setup(cache, hashlib.sha1(files.read(a7m_path)).hexdigest()[:16], edits, {}, tree2070, mod_assets)

    def _setup(
        self,
        cache: Path,
        fingerprint: str,
        edits: Path | None = None,
        edit_meta: dict | None = None,
        tree2070=None,
        mod_assets: str = "",
    ) -> None:
        """What every island has, whatever its game (the subclasses build their view, then call this):
        the cache (``fingerprint`` names what is drawn from the island file), the edit file and what is
        kept in it on every save (``edit_meta``), the locks (the view asks for many files at once)."""
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self._fingerprint = fingerprint
        self.edits_path = edits
        self.edit_meta: dict = dict(edit_meta or {})
        self.tree2070 = tree2070
        self.mod_assets = mod_assets
        self.notes: list[str] = []  # what could not be read and is left out (the window shows them)
        self._editor: tuple[list[dict], dict[int, str]] | None = None
        self._assets: assetmodels.AssetModels | bool | None = None
        self._problems: dict[str, str] = {}
        self._vanilla: list[dict] | None = None
        self._convert_lock = threading.Lock()
        self._locks: dict[Path, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._resolver: fileresolve.Resolver | None = None  # the models' file lookups (``resolver``)
        self.texture_failures: dict[str, str] = {}  # a texture that did not load -> why (the Checks tab)

    def editor_assets(self) -> tuple[list[dict], dict[int, str]]:
        """117's editor-only assets (``editorassets``) and its GUID -> template map, read once."""
        if self._editor is None:
            try:
                self._editor = editorassets.load(self.files.game)
            except Exception as exc:  # a game without them (or a test): no markers, and the window says so
                self.notes.append(tr("islands.no_editor_only_assets_markers", error=f"{type(exc).__name__}: {exc}"))
                self._editor = ([], {})
        return self._editor

    def asset_models(self) -> assetmodels.AssetModels | None:
        """The game's assets and templates as ``assetmodels`` reads them, once; None when the game has none."""
        if self._assets is None:
            try:
                self._assets = assetmodels.AssetModels.read(self.files.game.read, assetmodels.ASSETS_117)
            except Exception as exc:  # a test or a game without them: the objects keep what their file names
                self.notes.append(tr("islands.no_game_assets_buildings_draw", error=exc))
                self._assets = False
        return self._assets or None

    def objects(self) -> list[dict]:
        """Placed objects: those that draw a model (their own config, the mod's asset or the game's asset and the
        variation the island names) and the editor-only ones, drawn as markers (``marker``: their kind). One
        without a model to draw is a pin kept for it (``marker`` ``NoModel`` or ``Unresolved``) with the reason
        in ``unresolved``; the water's own splines are left out."""
        models = assetmodels.AssetModels(self.mod_assets.encode("utf-8"))
        catalogue, template_of = self.editor_assets()
        names = {e["guid"]: e["name"] for e in catalogue}
        out = []
        for group, o in a7mread.object_tags(self.view.gm, editorassets.GROUPS):
            if o.find("Position") is not None and (item := self._object(o, models, template_of, names, group)):
                out.append(item)
        return out

    def _object(
        self, o: filedb.Tag, models: assetmodels.AssetModels, template_of: dict, names: dict, group: str = "GameObject"
    ) -> dict | None:
        guid = struct.unpack_from("<I", o.find("guid").data)[0] if o.find("guid") is not None else 0
        variation = struct.unpack_from("<I", o.find("Variation").data)[0] if o.find("Variation") is not None else 0
        cfg_tag = o.path("DynamicVariation/ConfigFilename")
        cfg = (
            cfg_tag.data.decode("utf-16-le").replace("\\", "/")
            if cfg_tag is not None
            else models.first_model(guid) or None
        )
        marker = template_of.get(guid) if guid in names else None
        label, template, why = names.get(guid), "", ""
        assets = self.asset_models()
        if not cfg and not marker and assets is not None:
            if group == "EditorObject" and o.find("BezierPath") is not None:
                return None  # a coast line or a river: the water tool's
            found = assets.model_for(guid, variation)
            cfg, why, label, template = found.cfg or None, found.why, found.name or label, found.template
        if not cfg and not marker and not why:
            return None
        if cfg and not marker:
            why = self.model_problem(cfg)
        if not marker and why:
            marker = "NoModel" if why in assetmodels.NO_PICTURE else "Unresolved"
        orient = o.path("Mesh/Orientation")
        scale = o.path("Mesh/Scale")
        position = list(struct.unpack("<3f", o.find("Position").data))
        return {
            "key": islandedits.key(islandedits.object_name(o), position),
            "guid": guid,
            "group": group,
            "variation": variation,
            "clone": cfg_tag is None and guid not in names,  # placed again by copying it (``objectclones``)
            "cfg": cfg or "",
            "marker": marker,
            "template": template,
            "label": label or (cfg or "").split("/")[-1].removesuffix(".cfg") or f"guid {guid}",
            "position": position,
            "rotation": list(struct.unpack("<4f", orient.data)) if orient is not None else [0, 0, 0, 1],
            "scale": struct.unpack("<f", scale.data)[0] if scale is not None else 1.0,
            **({"unresolved": why} if why else {}),
        }

    def model_problem(self, cfg: str) -> str:
        """Why the config ``cfg`` draws nothing ("" when it draws): unreadable, or no model in it (a decal)."""
        if cfg not in self._problems:
            try:
                spec = self.model(cfg)
                self._problems[cfg] = (
                    "" if spec["parts"] or spec["files"] or spec["effects"] else assetmodels.Reason.EMPTY
                )
            except Exception as exc:
                self._problems[cfg] = f"{assetmodels.Reason.UNREADABLE}: {type(exc).__name__}"
        return self._problems[cfg]

    def water_network(self) -> dict | None:
        """The rivers and lakes as the island build computed them (``<island>.water.json`` beside the
        island, written by island_test.py), or None for an island without one (a vanilla one)."""
        path = f"{self.view.folder}{self.view.name}.water.json"
        try:
            return json.loads(self.files.read(path))
        except (KeyError, FileNotFoundError):
            return None
        except ValueError as exc:  # a damaged file is not "no network": the water tool stays empty, and says why
            self.notes.append(tr("islands.not_valid_json_river_network", path=path, error=exc))
            return None

    def props(self) -> list[dict]:
        """The island's props (``PropGrid``): grass, bushes, and 2070's waterfalls, fires and lights."""
        pg = self.view.rd.path("PropGrid")
        names = pg.find("FileNames") if pg is not None else None
        inst = pg.find("Instances") if pg is not None else None
        if names is None or inst is None:
            return []
        files = [c.data.decode("utf-16-le").replace("\\", "/") for c in names.children]
        table = self.tree_table()  # its trees are the forest layer's (forest), not props to edit
        trees = range(table.first_prop, table.first_prop + len(table.trees)) if table is not None else range(0)
        out = []
        for i, p in enumerate(inst.children):
            if not isinstance(p, filedb.Tag) or p.find("Index") is None or i in trees:
                continue
            cfg = files[struct.unpack("<I", p.find("Index").data)[0]]
            position = list(struct.unpack("<3f", p.find("Position").data))
            colour = p.find("Color")
            out.append(
                {
                    "key": islandedits.key(cfg, position),
                    "cfg": cfg,
                    "position": position,
                    "rotation": list(struct.unpack("<4f", p.find("Rotation").data)),
                    "scale": list(struct.unpack("<3f", p.find("Scale").data)),
                    **({"color": list(struct.unpack("<4f", colour.data))} if colour is not None else {}),
                    **({"unresolved": why} if (why := self.model_problem(cfg)) else {}),
                }
            )
        return out

    def tree_table(self):
        """A built island's numbered trees (a table registered at ``features.FOREST_TABLES``), or None for a 117 island
        (and in an edition without one)."""
        for reader in features.extensions(features.FOREST_TABLES).values():
            table = reader.read(self.files, f"{self.view.folder}{self.view.name}")
            if table is not None:
                return table
        return None

    def forest(self) -> dict:
        """The forest the brush edits: a built island's tree tiles and planting models, or a 117
        island's forest tiles and the region's tree."""
        table = self.tree_table()
        if table is not None:
            return {"tiles": [list(t) for t in table.tiles()], "models": table.planting}
        regrow = self.view.gm.path("RegrowManager")
        bits = np.asarray(read_bits(regrow.find("TreeMap")), bool) if regrow is not None else None
        tiles = np.argwhere(bits) if bits is not None else np.zeros((0, 2), int)
        assets = assetmodels.AssetModels(self.files.read(assetmodels.ASSETS_117.assets))
        tree = assets.asset(VANILLA_TREE_GUID)
        return {
            "tiles": [[int(x), int(z)] for z, x in tiles],
            "models": [v for v in tree.variations if v] if tree else [],
        }

    def island_json(self) -> bytes:
        """``island()`` as JSON, cached on disk: the continental island took 3.6 s to read."""
        return self._keep_island()[0].read_bytes()

    def island_data(self) -> dict:
        """``island()`` through its cache: the data made when missing (handed on, not written and read again), else
        the file read in pieces (``piecejson``: one call over it held the window's thread)."""
        out, data = self._keep_island()
        return data if data is not None else piecejson.loads(out.read_text(encoding="utf-8"))

    def _keep_island(self) -> tuple[Path, dict | None]:
        """The island's cache file, made when missing: its path and the data made (None when it was there)."""
        out = self._island_cache()
        with self._lock(out):
            if out.is_file():
                return out, None
            data = self._island_doc()
            write_atomic(out, piecejson.dumps(data).encode("utf-8"))
            return out, data

    def _island_doc(self) -> dict:
        """What the island's cache file holds."""
        return self.island()

    def _island_cache(self) -> Path:
        water = self.water_network()
        key = "|".join(
            (
                f"island{ISLAND_JSON}",
                self._fingerprint,
                hashlib.sha1(self.mod_assets.encode("utf-8")).hexdigest()[:12],
                hashlib.sha1(json.dumps(water).encode("utf-8")).hexdigest()[:12],
                "blank" if self.edit_meta.get("clear") else "",  # a blank island shows without its objects
            )
        )
        return self._cached(key, ".json")

    def island(self) -> dict:
        """The island as the edit session takes it (``editsession.EditSession``): heights (float32, base64),
        splines, the water network, objects, props and the forest."""
        hm = (self.view.hm / HEIGHT_UNITS).astype("<f4")
        objects = self.objects()
        per_tile = 2
        if (hm.shape[0] - 1) // 2 > FINE_TERRAIN_MAX:
            hm, per_tile = np.ascontiguousarray(hm[::2, ::2]), 1
        return {
            "width": int(hm.shape[0]),
            "samples_per_tile": per_tile,
            "heights": base64.b64encode(hm.tobytes()).decode("ascii"),
            "splines": [
                {"kind": k, "points": [list(p) for p in pts], "widths": list(w)} for k, pts, w in self.view.splines()
            ],
            "water": self.water_network(),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": self.props(),
            "forest": self.forest(),
            **self.active_map(),
            **self.ford_grid(),
            **self.area_grid(),
            **self.herd_grid(),
            **self.slot_list(),
            **self.session_settings(),
            **self.environment_grid(),
            **self.surf_map(),
        }

    def footprints(self, objects: list[dict]) -> dict[str, list]:
        """The build footprint (``footprints``) of each model config of the island's own objects, for the rules and the
        building snap; a model without an info file or blocker is left out."""
        cfgs = sorted({o["cfg"] for o in objects if o.get("clone") and o["cfg"] and not o.get("marker")})
        return {cfg: found for cfg in cfgs if (found := self.footprint_of(cfg))}

    def footprint_of(self, cfg: str) -> list:
        """The build footprint of one model config (the info file beside it); [] when it has none."""
        try:
            text = self.read_game(footprints.info_path(cfg)).decode("utf-8", "replace")
        except (KeyError, FileNotFoundError, OSError):
            return []
        return footprints.read_footprint(text)

    def read_game(self, path: str) -> bytes:
        """A file of the island's game (the mod first, as the view reads it)."""
        return self.files.read(path)

    # what can be placed (placeables)
    def placeable_sources(self) -> tuple[list[bytes], str]:
        """The game's asset files in its order and its templates' text, for ``placeables``."""
        from ..sources.anno117 import ASSETS_XML_PATH, TEMPLATES_XML_PATH

        game = self.files.game
        return [game.read(ASSETS_XML_PATH)], game.read(TEMPLATES_XML_PATH).decode("utf-8", "ignore")

    def placeables(self) -> list:
        """The buildings and other objects of the game's assets that can be placed (``placeables.scan``), cached on
        disk by the asset files and the templates."""
        from . import placeables

        files, templates = self.placeable_sources()
        stamp = hashlib.sha1(placeables.FORMAT.encode())
        for data in (*files, templates.encode("utf-8")):
            stamp.update(b"%d|" % len(data) + data)
        out = self._cached(f"placeables|{stamp.hexdigest()}", ".json")
        with self._lock(out):
            if out.is_file():
                return placeables.from_json(json.loads(out.read_text(encoding="utf-8")))
            rows = placeables.scan(files, templates)
            write_atomic(out, json.dumps(placeables.to_json(rows)).encode("utf-8"))
        return rows

    def drawable(self, cfg: str) -> str:
        """The model config the view draws for a placeable's model (the game's own config here)."""
        return cfg

    def active_map(self) -> dict:
        """The island's active cells for the island JSON (``cellbrush.pack``); nothing when its render data has none."""
        rd = getattr(self.view, "rd", None)
        tag = rd.path("Terrain/ActiveMap") if rd is not None else None
        if tag is None or tag.find("bits") is None:
            return {}
        return {"active_map": cellbrush.pack(np.asarray(read_bits(tag), bool))}

    def ford_grid(self) -> dict:
        """The island's ford cells and the river tiles a ford may lie on, for the island JSON (``cellbrush.pack``);
        nothing without a ford grid and a river grid."""
        gm = getattr(self.view, "gm", None)
        wm = gm.path("WorldManager") if gm is not None else None
        ford, river = (wm.find("FordGrid"), wm.find("RiverGrid")) if wm is not None else (None, None)
        if ford is None or river is None:
            return {}
        return {"ford_grid": cellbrush.pack(np.asarray(read_bits(ford), bool), np.asarray(read_bits(river), bool))}

    def herd_grid(self) -> dict:
        """The tiles the island's herds roam for the island JSON (``cellbrush.pack``), the land that is no river as the
        ones the brush may switch on; nothing without the grid."""
        gm = getattr(self.view, "gm", None)
        roam = herdareas.read(gm) if gm is not None else None
        if roam is None:
            return {}
        return {"herd_grid": cellbrush.pack(roam, herdareas.free_tiles(gm, roam.shape))}

    def session_settings(self) -> dict:
        """The island's session settings for the island JSON (``sessionsettings.read``); nothing without any."""
        gm = getattr(self.view, "gm", None)
        read = sessionsettings.read(gm) if gm is not None else {}
        return {"session": read} if read else {}

    def slot_list(self) -> dict:
        """The slots the island's file lists, per slot asset GUID as text: how many (``slotlist``, island-editor "The
        slot list"), and the assets' own name and generator values (``slot_generator``); nothing for an island without
        the list."""
        info = getattr(self.view, "info", None)
        listed = slotlist.read(info) if info is not None else {}
        if not listed:
            return {}
        return {
            "slot_list": {str(guid): len(slots) for guid, slots in listed.items()},
            "slot_generator": self.slot_generator(listed),
        }

    def slot_generator(self, guids) -> dict[str, dict]:
        """Per slot asset GUID as text: its ``name`` and what the map generator reads of it, ``count``
        (``MapGeneratorSlotCount``) and ``all`` (``ActivateAll``), each where the asset has it; the mod's asset first."""
        sources = [assetmodels.AssetModels(self.mod_assets.encode("utf-8")), self.asset_models()]
        out: dict[str, dict] = {}
        for guid in guids:
            for models in filter(None, sources):
                if (asset := models.asset(guid)) is not None:
                    values = {
                        key: v
                        for key, tag in (("count", "MapGeneratorSlotCount"), ("all", "ActivateAll"))
                        if (v := models.value_of(guid, tag)) is not None
                    }
                    out[str(guid)] = {"name": asset.name, **values}
                    break
        return out

    def slot_assets(self) -> dict[int, slotkinds.SlotAsset]:
        """The slot assets of the island's game, the mod's own over the game's, read once (``slotkinds``)."""
        found = getattr(self, "_slot_assets", None)
        if found is None:
            found = {}
            for models in (self.asset_models(), assetmodels.AssetModels(self.mod_assets.encode("utf-8"))):
                found |= slotkinds.read_assets(models) if models is not None else {}
            self._slot_assets = found
        return found

    def area_grid(self) -> dict:
        """The island's construction area tiles for the island JSON (``cellbrush.pack``), the tiles with another area id as the
        ones the brush may not touch; nothing without the grid."""
        gm = getattr(self.view, "gm", None)
        tag = gm.path("AreaIDs") if gm is not None else None
        if tag is None:
            return {}
        return {"area_grid": cellbrush.pack_area(np.asarray(read_sparse(tag)))}

    def environment_grid(self) -> dict:
        """The island's environment zones for the island JSON (``cellbrush.pack_values``); nothing without the grid."""
        gm = getattr(self.view, "gm", None)
        tag = gm.path("WorldManager/EnvironmentGrid/EnvironmentGRid") if gm is not None else None
        if tag is None or tag.find("val") is None:
            return {}
        n = tag.find("x").u32()
        return {
            "environment_map": cellbrush.pack_values(
                np.frombuffer(tag.find("val").data, np.uint8)[: n * n].reshape(n, n)
            )
        }

    def surf_map(self) -> dict:
        """The island's coast-wave bytes for the island JSON (``cellbrush.pack_values``); nothing without a sea map."""
        rd = getattr(self.view, "rd", None)
        cells = surfmap.read(self.view) if rd is not None else None
        return {"surf_map": cellbrush.pack_values(cells)} if cells is not None else {}

    def materials(self) -> list[dict]:
        """The island's ground materials (its material set) for the paint palette."""
        return [
            {
                "id": mid,
                "name": Path(m["file"]).stem,
                "diffuse": m.get("diffuse"),
                "normal": m.get("maps", {}).get("Normal"),
                "repetition": m["repetition"],
                "sharpness": m.get("sharpness", 1.0),
                "order": m.get("order", mid),
            }
            for mid, m in sorted(self.view.materials().items())
        ]

    def vanilla_materials(self) -> list[dict]:
        """117's own ground materials, for the paint palette (``terrain2070.vanilla_materials``)."""
        from ..islands.terrain2070 import vanilla_materials

        if self._vanilla is None:
            self._vanilla = vanilla_materials(self.files)
        return self._vanilla

    def edits(self) -> dict:
        return islandedits.load(self.edits_path)

    def save_edits(self, edits: dict) -> None:
        if self.edits_path is None:
            raise FileNotFoundError(tr("islands.island_no_edit_file_vanilla"))
        islandedits.save(self.edits_path, {**edits, **self.edit_meta})

    # the ground
    def ground_layers(self) -> tuple[list[int], np.ndarray]:
        """The ground as material layers: material IDs and their visible weight per texel
        ([layers, z, x], 0-1), at up to ``GROUND_TEXELS`` per tile and ``GROUND_MAX`` across."""
        mids, wts = self.view.weights()
        n = mids.shape[1]
        step = max(1, self.view.texels // GROUND_TEXELS, -(-n // GROUND_MAX))
        m = n // step
        ids, layers = [], []
        for mid in np.unique(mids):
            w = np.where(mids == mid, wts, 0).sum(axis=0)[: m * step, : m * step]
            w = w.reshape(m, step, m, step).mean(axis=(1, 3))
            if w.max() > 1 / 255:
                ids.append(int(mid))
                layers.append(w)
        return ids, np.stack(layers).astype(np.float32)

    def ground(self) -> dict:
        """What the view blends the ground from: the layers' materials (in weight-layer order) and
        the weight maps' size; the weights themselves are ``ground_weights``. Cached on disk."""
        out = self._cached(f"ground|{self._fingerprint}", ".json")
        with self._lock(out):
            if not out.is_file():
                ids, weights = self.ground_layers()
                write_atomic(
                    self._cached(f"ground|{self._fingerprint}", ".bin"),
                    np.round(weights * 255).astype(np.uint8).tobytes(),
                )
                mats = {m["id"]: m for m in self.materials()}
                layers = [mats.get(i, {"id": i, "name": str(i), "diffuse": None, "repetition": 4}) for i in ids]
                write_atomic(out, json.dumps({"width": int(weights.shape[1]), "materials": layers}).encode("utf-8"))
        return json.loads(out.read_text(encoding="utf-8"))

    def ground_weights(self) -> bytes:
        """The weight maps of ``ground()``: uint8 [layer][z][x]."""
        self.ground()
        return self._cached(f"ground|{self._fingerprint}", ".bin").read_bytes()

    def ground2070(self) -> tuple[dict, bytes]:
        """The ground as 2070 paints it, chunk by chunk: only an island edited for 2070 has it."""
        raise FileNotFoundError(tr("islands.not_2070_island_no_chunk"))

    def albedo_png(self) -> bytes:
        """The ground as an image (what the view shows until the materials have loaded), at up to 8 pixels
        per tile and ``ALBEDO_MAX`` across; cached."""
        from PIL import Image

        size = (self.view.hm.shape[0] - 1) // 2
        texels = max(1, min(8, ALBEDO_MAX // max(1, size)))
        out = self._cached(f"albedo|{self._fingerprint}|{texels}", ".png")
        with self._lock(out):
            if not out.is_file():
                img = np.clip(self.view.albedo(texels) * 255, 0, 255).astype(np.uint8)
                with whole_file(out) as scratch:
                    Image.fromarray(img).save(scratch, "PNG")
        return out.read_bytes()

    # the game's rules (docs/rendering/)
    def game_heights(self) -> bytes:
        """The ground as the game draws it near the camera (``gameterrain``), on the same grid as
        ``island()``'s heights: float32 world units, cached."""
        from ..islands import gameterrain

        out = self._cached(f"game_heights|{self._fingerprint}", ".bin")
        with self._lock(out):
            if not out.is_file():
                step = 0.5 if self.view.size <= FINE_TERRAIN_MAX else 1.0
                heights, _ = gameterrain.near_heights(self.view, step)
                write_atomic(out, heights.astype("<f4").tobytes())
        return out.read_bytes()

    def grade_lut(self) -> bytes:
        """The game's colour grade as float32 [b][g][r][rgb] (``gamelook``), read from the install
        once and cached in the workspace (Ubisoft's data: never in the repository)."""
        from ..render import gamelook

        out = self._cached("grade_lut|" + gamelook.DEFAULT_AMBIENT, ".bin")
        with self._lock(out):
            if not out.is_file():
                lut = gamelook.read_lut(self.files.read(gamelook.lut_path(self.files)))
                write_atomic(out, lut.astype("<f4").tobytes())
        return out.read_bytes()

    def water_infos(self) -> bytes:
        """The island's sea shading map (rd3d ``WaterInfos``: depth, coast waves, beach, foam per
        cell, rendering/water.md) as raw RGBA bytes, square."""
        wi = self.view.rd.path("WaterInfos")
        return wi.find("map").data if wi is not None else b""

    # the model library
    def library(self) -> dict:
        """What can be placed: 117's own models (configs in its model folders) and 2070's landscape
        assets, one entry per model variation (made drawable in the mod when picked, ``convert2070``; an edition
        without that offers the ones earlier builds left in the mod)."""
        game = [
            {"name": Path(p).stem, "cfg": p, "group": library_group(p)}
            for p in self.files.list("data/")
            if LIBRARY_117.match(p) and not p.startswith("data/anno2070/")
        ]
        a2070 = []
        if self.tree2070 is not None and features.extensions(features.MODEL_IMPORTS):
            from ..islands.objects2070 import read_types

            for t in sorted(read_types(self.tree2070).values(), key=lambda t: t.name.lower()):
                if t.template in LIBRARY_2070:
                    for k, v in enumerate(t.variations):
                        if not self.tree2070.exists(v):  # testdata/: the developers' models, not shipped
                            continue
                        name = t.name if len(t.variations) == 1 else f"{t.name} #{k + 1}"
                        a2070.append({"name": name, "template": t.template, "source": v, "group": library_group(v)})
        else:  # without the install: the files already in the mod
            a2070 = [
                {"name": Path(p).stem, "template": "", "cfg": p, "group": library_group(p)}
                for p in self.files.list("data/anno2070/graphics/")
                if p.endswith(".cfg") and self.files.where(p) == "mod"
            ]
        editor = [{**e, "group": e["template"]} for e in self.editor_assets()[0]]
        return {"117": game, "2070": a2070, "editor": editor}

    def convert2070(self, source: str) -> str:
        """Make a 2070 model drawable in the mod (as the island build does) and return its 117 path."""
        imports = next(iter(features.extensions(features.MODEL_IMPORTS).values()), None)
        if imports is None:
            raise FileNotFoundError(tr("islands.2070_models_state", state=tr(features.NOT_IN_EDITION)))
        if self.tree2070 is None or self.files.mod_root is None:
            raise FileNotFoundError(tr("islands.2070_models_need_install_mod"))
        with self._convert_lock:
            rel = imports.make(self.tree2070, self.files.mod_root).convert(source)
            self.files.refresh()
        return rel

    # models: meshes and textures (render.modellibrary loads them in the background)
    def _cached(self, key: str, suffix: str) -> Path:
        return self.cache / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + suffix)

    def _lock(self, path: Path) -> threading.Lock:
        """One job per cached file at a time: the view asks for many in parallel."""
        with self._locks_lock:
            return self._locks.setdefault(path, threading.Lock())

    def model(self, cfg: str) -> dict:
        """What the config draws, read as the game reads it: the baked ``shared/`` copy first (a mod's own file wins)."""
        shared = configtree.shared_config(cfg)
        if shared and self.files.where(cfg) != "mod":
            try:
                return model_parts(self.files.read(shared).decode("utf-8", errors="replace"), self.resolver(), shared)
            except (KeyError, FileNotFoundError):
                pass  # no baked copy
        return model_parts(self.files.read(cfg).decode("utf-8", errors="replace"), self.resolver(), cfg)

    # what the models name, looked up as the game does (models.fileresolve; viewer.md "How files are found")
    def resolver(self) -> fileresolve.Resolver:
        """The lookups of the models drawn so far: its ``missing`` and ``texture_failures`` are the Checks tab's
        list of files (``file_problems``)."""
        if getattr(self, "_resolver", None) is None:  # an editor made without ``_setup`` (a test's) too
            self._resolver = fileresolve.Resolver(*self.resolver_source())
        return self._resolver

    def resolver_source(self) -> tuple[object, str]:
        """The files the models' names are looked up in and the game they follow (another game's island says its own)."""
        return self.files, "anno117"

    def texture_file(self, name: str) -> str:
        """The file the game loads for a texture name; raises ``FileNotFoundError`` saying why there is none."""
        resolve = self.resolver()
        found = fileresolve.texture_file(name, resolve.exists, resolve.game)
        if not found:
            raise FileNotFoundError(f"{name}: {resolve.why(fileresolve.texture_candidates(name, resolve.game))}")
        return found

    def file_problems(self) -> list[tuple[str, str, str]]:
        """(file, why, the config that names it) for each file the models drawn so far name that does not draw."""
        missing = self.resolver().missing()
        named = {normalize(r.name, studio=True) for r in missing}  # a missing map is listed once, not again as failing
        failures = getattr(self, "texture_failures", {})
        failed = [(p, why, "") for p, why in sorted(failures.items()) if normalize(p, studio=True) not in named]
        return [(r.name, r.reason, r.owner) for r in missing] + failed
