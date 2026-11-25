"""An island opened in the editor: what the 3D view draws and what the tools read and write.

``CIslandEditor`` opens a 117 island (built or vanilla) and offers its heights, ground, water, objects, props
and forest, each model's parts with its mesh as glTF (``rdmglb``) and its textures as PNG (texconv), the edit
file, and the model library. Files come from the mod folder first, then the game (``islandview.CFiles``), so a
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
from ..islands.islandview import CFiles, CIslandView
from ..models import configtree, fileresolve
from ..sources.overlay import normalize
from ..suite import features
from . import assetmodels, cellbrush, footprints, slotkinds
from .islandassets import CIslandAssets, whole_file
from .modelparts import library_group, model_parts

# Big islands are read coarser: the continental island (768 tiles) at the full 2 samples per tile
# made a terrain mesh of 4.7 million triangles and a ground image of 3072 pixels, 21 s to draw.
ISLAND_JSON = 11  # the cached island data's layout: a change makes the editor read the island again
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


class CIslandEditor(CIslandAssets):
    """A 117 island (``files``: the mod folder first, then the game) with its edit file. ``tree2070``: the
    2070 install (``overlay.CSourceTree``), for the library's 2070 models; ``mod_assets``: the mod's
    assets.xml, where a slot or tree asset without a config of its own finds its model."""

    def __init__(
        self,
        files: CFiles,
        a7m_path: str,
        cache: Path,
        mod_assets: str = "",
        edits: Path | None = None,
        tree2070=None,
    ) -> None:
        self.m_files = files
        self.m_view = CIslandView(files, a7m_path)
        self.m_a7m_path = a7m_path
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
        self.m_cache = cache
        self.m_cache.mkdir(parents=True, exist_ok=True)
        self._m_fingerprint = fingerprint
        self.m_edits_path = edits
        self.m_edit_meta: dict = dict(edit_meta or {})
        self.m_tree2070 = tree2070
        self.m_mod_assets = mod_assets
        self.m_notes: list[str] = []  # what could not be read and is left out (the window shows them)
        self._m_editor: tuple[list[dict], dict[int, str]] | None = None
        self._m_assets: assetmodels.CAssetModels | bool | None = None
        self._m_problems: dict[str, str] = {}
        self._m_vanilla: list[dict] | None = None
        self._m_convert_lock = threading.Lock()
        self._m_locks: dict[Path, threading.Lock] = {}
        self._m_locks_lock = threading.Lock()
        self._m_resolver: fileresolve.CResolver | None = None  # the models' file lookups (``resolver``)
        self.m_texture_failures: dict[str, str] = {}  # a texture that did not load -> why (the Checks tab)

    def editor_assets(self) -> tuple[list[dict], dict[int, str]]:
        """117's editor-only assets (``editorassets``) and its GUID -> template map, read once."""
        if self._m_editor is None:
            try:
                self._m_editor = editorassets.load(self.m_files.m_game)
            except Exception as error:  # a game without them (or a test): no markers, and the window says so
                self.m_notes.append(
                    tr("islands.no_editor_only_assets_markers", error=f"{type(error).__name__}: {error}")
                )
                self._m_editor = ([], {})
        return self._m_editor

    def asset_models(self) -> assetmodels.CAssetModels | None:
        """The game's assets and templates as ``assetmodels`` reads them, once; None when the game has none."""
        if self._m_assets is None:
            try:
                self._m_assets = assetmodels.CAssetModels.read(self.m_files.m_game.read, assetmodels.ASSETS_117)
            except Exception as error:  # a test or a game without them: the objects keep what their file names
                self.m_notes.append(tr("islands.no_game_assets_buildings_draw", error=error))
                self._m_assets = False
        return self._m_assets or None

    def objects(self) -> list[dict]:
        """Placed objects: those that draw a model (their own config, the mod's asset or the game's asset and the
        variation the island names) and the editor-only ones, drawn as markers (``marker``: their kind). One
        without a model to draw is a pin kept for it (``marker`` ``NoModel`` or ``Unresolved``) with the reason
        in ``unresolved``; the water's own splines are left out."""
        models = assetmodels.CAssetModels(self.m_mod_assets.encode("utf-8"))
        catalogue, template_of = self.editor_assets()
        names = {entry["guid"]: entry["name"] for entry in catalogue}
        out = []
        for group, object_tag in a7mread.object_tags(self.m_view.game_session_manager, editorassets.GROUPS):
            if object_tag.find("Position") is not None and (
                item := self._object(object_tag, models, template_of, names, group)
            ):
                out.append(item)
        return out

    def _object(
        self,
        object_tag: filedb.STag,
        models: assetmodels.CAssetModels,
        template_of: dict,
        names: dict,
        group: str = "GameObject",
    ) -> dict | None:
        guid = struct.unpack_from("<I", object_tag.find("guid").data)[0] if object_tag.find("guid") is not None else 0
        variation = (
            struct.unpack_from("<I", object_tag.find("Variation").data)[0]
            if object_tag.find("Variation") is not None
            else 0
        )
        cfg_tag = object_tag.path("DynamicVariation/ConfigFilename")
        cfg = (
            cfg_tag.data.decode("utf-16-le").replace("\\", "/")
            if cfg_tag is not None
            else models.first_model(guid) or None
        )
        marker = template_of.get(guid) if guid in names else None
        label, template, why = names.get(guid), "", ""
        assets = self.asset_models()
        if not cfg and not marker and assets is not None:
            if group == "EditorObject" and object_tag.find("BezierPath") is not None:
                return None  # a coast line or a river: the water tool's
            found = assets.model_for(guid, variation)
            cfg, why, label, template = found.cfg or None, found.why, found.name or label, found.template
        if not cfg and not marker and not why:
            return None
        if cfg and not marker:
            why = self.model_problem(cfg)
        if not marker and why:
            marker = "NoModel" if why in assetmodels.NO_PICTURE else "Unresolved"
        orient = object_tag.path("Mesh/Orientation")
        scale = object_tag.path("Mesh/Scale")
        position = list(struct.unpack("<3f", object_tag.find("Position").data))
        return {
            "key": islandedits.key(islandedits.object_name(object_tag), position),
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
        if cfg not in self._m_problems:
            try:
                model_spec = self.model(cfg)
                self._m_problems[cfg] = (
                    ""
                    if model_spec["parts"] or model_spec["files"] or model_spec["effects"]
                    else assetmodels.CReason.EMPTY
                )
            except Exception as error:
                self._m_problems[cfg] = (
                    assetmodels.CReason.PLACEHOLDER  # the editor's placeholder file is named but not shipped
                    if assetmodels.is_missing_placeholder(cfg)
                    else f"{assetmodels.CReason.UNREADABLE}: {type(error).__name__}"
                )
        return self._m_problems[cfg]

    def water_network(self) -> dict | None:
        """The rivers and lakes as the island build computed them (``<island>.water.json`` beside the
        island, written by island_test.py), or None for an island without one (a vanilla one)."""
        path = f"{self.m_view.folder}{self.m_view.name}.water.json"
        try:
            return json.loads(self.m_files.read(path))
        except (KeyError, FileNotFoundError):
            return None
        except ValueError as error:  # a damaged file is not "no network": the water tool stays empty, and says why
            self.m_notes.append(tr("islands.not_valid_json_river_network", path=path, error=error))
            return None

    def read_props(self) -> list[dict]:
        """The island's props (``PropGrid``): grass, bushes, and 2070's waterfalls, fires and lights."""
        prop_grid = self.m_view.rd3d.path("PropGrid")
        names = prop_grid.find("FileNames") if prop_grid is not None else None
        instances_tag = prop_grid.find("Instances") if prop_grid is not None else None
        if names is None or instances_tag is None:
            return []
        files = [name_tag.data.decode("utf-16-le").replace("\\", "/") for name_tag in names.children]
        table = self.tree_table()  # its trees are the forest layer's (forest), not props to edit
        trees = range(table.first_prop, table.first_prop + len(table.trees)) if table is not None else range(0)
        out = []
        for i, prop_tag in enumerate(instances_tag.children):
            if not isinstance(prop_tag, filedb.STag) or prop_tag.find("Index") is None or i in trees:
                continue
            cfg = files[struct.unpack("<I", prop_tag.find("Index").data)[0]]
            position = list(struct.unpack("<3f", prop_tag.find("Position").data))
            colour = prop_tag.find("Color")
            out.append(
                {
                    "key": islandedits.key(cfg, position),
                    "cfg": cfg,
                    "position": position,
                    "rotation": list(struct.unpack("<4f", prop_tag.find("Rotation").data)),
                    "scale": list(struct.unpack("<3f", prop_tag.find("Scale").data)),
                    **({"color": list(struct.unpack("<4f", colour.data))} if colour is not None else {}),
                    **({"unresolved": why} if (why := self.model_problem(cfg)) else {}),
                }
            )
        return out

    def tree_table(self):
        """A built island's numbered trees (a table registered at ``features.FOREST_TABLES``), or None for a 117 island
        (and in an edition without one)."""
        for reader in features.extensions(features.FOREST_TABLES).values():
            table = reader.read(self.m_files, f"{self.m_view.folder}{self.m_view.name}")
            if table is not None:
                return table
        return None

    def forest(self) -> dict:
        """The forest the brush edits: a built island's tree tiles and planting models, or a 117
        island's forest tiles and the region's tree."""
        table = self.tree_table()
        if table is not None:
            return {"tiles": [list(tile) for tile in table.tiles()], "models": table.planting}
        regrow = self.m_view.game_session_manager.path("RegrowManager")
        bits = np.asarray(read_bits(regrow.find("TreeMap")), bool) if regrow is not None else None
        tiles = np.argwhere(bits) if bits is not None else np.zeros((0, 2), int)
        assets = assetmodels.CAssetModels(self.m_files.read(assetmodels.ASSETS_117.assets))
        tree = assets.asset(VANILLA_TREE_GUID)
        return {
            "tiles": [[int(x), int(z)] for z, x in tiles],
            "models": [variation for variation in tree.variations if variation] if tree else [],
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
                self._m_fingerprint,
                hashlib.sha1(self.m_mod_assets.encode("utf-8")).hexdigest()[:12],
                hashlib.sha1(json.dumps(water).encode("utf-8")).hexdigest()[:12],
                "blank" if self.m_edit_meta.get("clear") else "",  # a blank island shows without its objects
            )
        )
        return self._cached(key, ".json")

    def island(self) -> dict:
        """The island as the edit session takes it (``editsession.CEditSession``): heights (float32, base64),
        splines, the water network, objects, props and the forest."""
        heightmap = (self.m_view.height_map / HEIGHT_UNITS).astype("<f4")
        objects = self.objects()
        per_tile = 2
        if (heightmap.shape[0] - 1) // 2 > FINE_TERRAIN_MAX:
            heightmap, per_tile = np.ascontiguousarray(heightmap[::2, ::2]), 1
        return {
            "width": int(heightmap.shape[0]),
            "samples_per_tile": per_tile,
            "heights": base64.b64encode(heightmap.tobytes()).decode("ascii"),
            "splines": [
                {"kind": k, "points": [list(point) for point in points], "widths": list(widths), "flags": flags}
                for (k, points, widths), flags in zip(self.m_view.splines(), self.m_view.spline_flags(), strict=True)
            ],
            "water": self.water_network(),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": self.read_props(),
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
        cfgs = sorted(
            {
                placed_object["cfg"]
                for placed_object in objects
                if placed_object.get("clone") and placed_object["cfg"] and not placed_object.get("marker")
            }
        )
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
        return self.m_files.read(path)

    # what can be placed (placeables)
    def placeable_sources(self) -> tuple[list[bytes], str]:
        """The game's asset files in its order and its templates' text, for ``placeables``."""
        from ..sources.anno117 import ASSETS_XML_PATH, TEMPLATES_XML_PATH

        game = self.m_files.m_game
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
        rd3d = getattr(self.m_view, "rd3d", None)
        tag = rd3d.path("Terrain/ActiveMap") if rd3d is not None else None
        if tag is None or tag.find("bits") is None:
            return {}
        return {"active_map": cellbrush.pack(np.asarray(read_bits(tag), bool))}

    def ford_grid(self) -> dict:
        """The island's ford cells and the river tiles a ford may lie on, for the island JSON (``cellbrush.pack``);
        nothing without a ford grid and a river grid."""
        gamedata = getattr(self.m_view, "game_session_manager", None)
        world_manager = gamedata.path("WorldManager") if gamedata is not None else None
        ford, river = (
            (world_manager.find("FordGrid"), world_manager.find("RiverGrid"))
            if world_manager is not None
            else (None, None)
        )
        if ford is None or river is None:
            return {}
        return {"ford_grid": cellbrush.pack(np.asarray(read_bits(ford), bool), np.asarray(read_bits(river), bool))}

    def herd_grid(self) -> dict:
        """The tiles the island's herds roam for the island JSON (``cellbrush.pack``), the land that is no river as the
        ones the brush may switch on; nothing without the grid."""
        gamedata = getattr(self.m_view, "game_session_manager", None)
        roam = herdareas.read(gamedata) if gamedata is not None else None
        if roam is None:
            return {}
        return {"herd_grid": cellbrush.pack(roam, herdareas.free_tiles(gamedata, roam.shape))}

    def session_settings(self) -> dict:
        """The island's session settings for the island JSON (``sessionsettings.read``); nothing without any."""
        gamedata = getattr(self.m_view, "game_session_manager", None)
        read = sessionsettings.read(gamedata) if gamedata is not None else {}
        return {"session": read} if read else {}

    def slot_list(self) -> dict:
        """The slots the island's file lists, per slot asset GUID as text: how many (``slotlist``, island-editor "The
        slot list"), and the assets' own name and generator values (``slot_generator``); nothing for an island without
        the list."""
        a7minfo = getattr(self.m_view, "a7minfo", None)
        listed = slotlist.read(a7minfo) if a7minfo is not None else {}
        if not listed:
            return {}
        return {
            "slot_list": {str(guid): len(slots) for guid, slots in listed.items()},
            "slot_generator": self.slot_generator(listed),
        }

    def slot_generator(self, guids) -> dict[str, dict]:
        """Per slot asset GUID as text: its ``name`` and what the map generator reads of it, ``count``
        (``MapGeneratorSlotCount``) and ``all`` (``ActivateAll``), each where the asset has it; the mod's asset first."""
        sources = [assetmodels.CAssetModels(self.m_mod_assets.encode("utf-8")), self.asset_models()]
        out: dict[str, dict] = {}
        for guid in guids:
            for models in filter(None, sources):
                if (asset := models.asset(guid)) is not None:
                    values = {
                        key: value
                        for key, tag in (("count", "MapGeneratorSlotCount"), ("all", "ActivateAll"))
                        if (value := models.value_of(guid, tag)) is not None
                    }
                    out[str(guid)] = {"name": asset.name, **values}
                    break
        return out

    def slot_assets(self) -> dict[int, slotkinds.SSlotAsset]:
        """The slot assets of the island's game, the mod's own over the game's, read once (``slotkinds``)."""
        found = getattr(self, "_m_slot_assets", None)
        if found is None:
            found = {}
            for models in (self.asset_models(), assetmodels.CAssetModels(self.m_mod_assets.encode("utf-8"))):
                found |= slotkinds.read_assets(models) if models is not None else {}
            self._m_slot_assets = found
        return found

    def area_grid(self) -> dict:
        """The island's construction area tiles for the island JSON (``cellbrush.pack``), the tiles with another area id as the
        ones the brush may not touch; nothing without the grid."""
        gamedata = getattr(self.m_view, "game_session_manager", None)
        tag = gamedata.path("AreaIDs") if gamedata is not None else None
        if tag is None:
            return {}
        return {"area_grid": cellbrush.pack_area(np.asarray(read_sparse(tag)))}

    def environment_grid(self) -> dict:
        """The island's environment zones for the island JSON (``cellbrush.pack_values``); nothing without the grid."""
        gamedata = getattr(self.m_view, "game_session_manager", None)
        tag = gamedata.path("WorldManager/EnvironmentGrid/EnvironmentGRid") if gamedata is not None else None
        if tag is None or tag.find("val") is None:
            return {}
        grid_tiles = tag.find("x").u32()
        return {
            "environment_map": cellbrush.pack_values(
                np.frombuffer(tag.find("val").data, np.uint8)[: grid_tiles * grid_tiles].reshape(grid_tiles, grid_tiles)
            )
        }

    def surf_map(self) -> dict:
        """The island's coast-wave bytes for the island JSON (``cellbrush.pack_values``); nothing without a sea map."""
        rd3d = getattr(self.m_view, "rd3d", None)
        cells = surfmap.read(self.m_view) if rd3d is not None else None
        return {"surf_map": cellbrush.pack_values(cells)} if cells is not None else {}

    def materials(self) -> list[dict]:
        """The island's ground materials (its material set) for the paint palette."""
        return [
            {
                "id": mid,
                "name": Path(material["file"]).stem,
                "diffuse": material.get("diffuse"),
                "normal": material.get("maps", {}).get("Normal"),
                "repetition": material["repetition"],
                "sharpness": material.get("sharpness", 1.0),
                "order": material.get("order", mid),
            }
            for mid, material in sorted(self.m_view.materials().items())
        ]

    def vanilla_materials(self) -> list[dict]:
        """117's own ground materials, for the paint palette (``terrain2070.vanilla_materials``)."""
        from ..islands.terrain2070 import vanilla_materials

        if self._m_vanilla is None:
            self._m_vanilla = vanilla_materials(self.m_files)
        return self._m_vanilla

    def edits(self) -> dict:
        return islandedits.load(self.m_edits_path)

    def save_edits(self, edits: dict) -> None:
        if self.m_edits_path is None:
            raise FileNotFoundError(tr("islands.island_no_edit_file_vanilla"))
        islandedits.save(self.m_edits_path, {**edits, **self.m_edit_meta})

    # the ground
    def ground_layers(self) -> tuple[list[int], np.ndarray]:
        """The ground as material layers: material IDs and their visible weight per texel
        ([layers, z, x], 0-1), at up to ``GROUND_TEXELS`` per tile and ``GROUND_MAX`` across."""
        mids, weights = self.m_view.weights()
        texel_count = mids.shape[1]
        step = max(1, self.m_view.texels // GROUND_TEXELS, -(-texel_count // GROUND_MAX))
        reduced_count = texel_count // step
        ids, layers = [], []
        for mid in np.unique(mids):
            weight_layer = np.where(mids == mid, weights, 0).sum(axis=0)[: reduced_count * step, : reduced_count * step]
            weight_layer = weight_layer.reshape(reduced_count, step, reduced_count, step).mean(axis=(1, 3))
            if weight_layer.max() > 1 / 255:
                ids.append(int(mid))
                layers.append(weight_layer)
        return ids, np.stack(layers).astype(np.float32)

    def ground(self) -> dict:
        """What the view blends the ground from: the layers' materials (in weight-layer order) and
        the weight maps' size; the weights themselves are ``ground_weights``. Cached on disk."""
        out = self._cached(f"ground|{self._m_fingerprint}", ".json")
        with self._lock(out):
            if not out.is_file():
                ids, weights = self.ground_layers()
                write_atomic(
                    self._cached(f"ground|{self._m_fingerprint}", ".bin"),
                    np.round(weights * 255).astype(np.uint8).tobytes(),
                )
                material_by_id = {material["id"]: material for material in self.materials()}
                layers = [
                    material_by_id.get(
                        material_id, {"id": material_id, "name": str(material_id), "diffuse": None, "repetition": 4}
                    )
                    for material_id in ids
                ]
                write_atomic(out, json.dumps({"width": int(weights.shape[1]), "materials": layers}).encode("utf-8"))
        return json.loads(out.read_text(encoding="utf-8"))

    def ground_weights(self) -> bytes:
        """The weight maps of ``ground()``: uint8 [layer][z][x]."""
        self.ground()
        return self._cached(f"ground|{self._m_fingerprint}", ".bin").read_bytes()

    def ground2070(self) -> tuple[dict, bytes]:
        """The ground as 2070 paints it, chunk by chunk: only an island edited for 2070 has it."""
        raise FileNotFoundError(tr("islands.not_2070_island_no_chunk"))

    def albedo_png(self) -> bytes:
        """The ground as an image (what the view shows until the materials have loaded), at up to 8 pixels
        per tile and ``ALBEDO_MAX`` across; cached."""
        from PIL import Image

        size_tiles = (self.m_view.height_map.shape[0] - 1) // 2
        texels = max(1, min(8, ALBEDO_MAX // max(1, size_tiles)))
        out = self._cached(f"albedo|{self._m_fingerprint}|{texels}", ".png")
        with self._lock(out):
            if not out.is_file():
                image = np.clip(self.m_view.albedo(texels) * 255, 0, 255).astype(np.uint8)
                with whole_file(out) as scratch:
                    Image.fromarray(image).save(scratch, "PNG")
        return out.read_bytes()

    # the game's rules (docs/rendering/)
    def game_heights(self) -> bytes:
        """The ground as the game draws it near the camera (``gameterrain``), on the same grid as
        ``island()``'s heights: float32 world units, cached."""
        from ..islands import gameterrain

        out = self._cached(f"game_heights|{self._m_fingerprint}", ".bin")
        with self._lock(out):
            if not out.is_file():
                step = 0.5 if self.m_view.size <= FINE_TERRAIN_MAX else 1.0
                heights, _ = gameterrain.near_heights(self.m_view, step)
                write_atomic(out, heights.astype("<f4").tobytes())
        return out.read_bytes()

    def grade_lut(self) -> bytes:
        """The game's colour grade as float32 [b][g][r][rgb] (``gamelook``), read from the install
        once and cached in the workspace (Ubisoft's data: never in the repository)."""
        from ..render import gamelook

        out = self._cached("grade_lut|" + gamelook.DEFAULT_AMBIENT, ".bin")
        with self._lock(out):
            if not out.is_file():
                lut = gamelook.read_lut(self.m_files.read(gamelook.lut_path(self.m_files)))
                write_atomic(out, lut.astype("<f4").tobytes())
        return out.read_bytes()

    def water_infos(self) -> bytes:
        """The island's sea shading map (rd3d ``WaterInfos``: depth, coast waves, beach, foam per
        cell, rendering/water.md) as raw RGBA bytes, square."""
        infos_tag = self.m_view.rd3d.path("WaterInfos")
        return infos_tag.find("map").data if infos_tag is not None else b""

    # the model library
    def library(self) -> dict:
        """What can be placed: 117's own models (configs in its model folders) and 2070's landscape
        assets, one entry per model variation (made drawable in the mod when picked, ``convert2070``; an edition
        without that offers the ones earlier builds left in the mod)."""
        game = [
            {"name": Path(path).stem, "cfg": path, "group": library_group(path)}
            for path in self.m_files.list("data/")
            if LIBRARY_117.match(path) and not path.startswith("data/anno2070/")
        ]
        a2070 = []
        if self.m_tree2070 is not None and features.extensions(features.MODEL_IMPORTS):
            from ..islands.objects2070 import read_types

            for object_type in sorted(
                read_types(self.m_tree2070).values(), key=lambda object_type: object_type.name.lower()
            ):
                if object_type.template in LIBRARY_2070:
                    for k, variation in enumerate(object_type.variations):
                        if not self.m_tree2070.exists(variation):  # testdata/: the developers' models, not shipped
                            continue
                        name = object_type.name if len(object_type.variations) == 1 else f"{object_type.name} #{k + 1}"
                        a2070.append(
                            {
                                "name": name,
                                "template": object_type.template,
                                "source": variation,
                                "group": library_group(variation),
                            }
                        )
        else:  # without the install: the files already in the mod
            a2070 = [
                {"name": Path(path).stem, "template": "", "cfg": path, "group": library_group(path)}
                for path in self.m_files.list("data/anno2070/graphics/")
                if path.endswith(".cfg") and self.m_files.where(path) == "mod"
            ]
        editor = [{**entry, "group": entry["template"]} for entry in self.editor_assets()[0]]
        return {"117": game, "2070": a2070, "editor": editor}

    def convert2070(self, source: str) -> str:
        """Make a 2070 model drawable in the mod (as the island build does) and return its 117 path."""
        imports = next(iter(features.extensions(features.MODEL_IMPORTS).values()), None)
        if imports is None:
            raise FileNotFoundError(tr("islands.2070_models_state", state=tr(features.NOT_IN_EDITION)))
        if self.m_tree2070 is None or self.m_files.m_mod_root is None:
            raise FileNotFoundError(tr("islands.2070_models_need_install_mod"))
        with self._m_convert_lock:
            model_path = imports.make(self.m_tree2070, self.m_files.m_mod_root).convert(source)
            self.m_files.refresh()
        return model_path

    # models: meshes and textures (render.modellibrary loads them in the background)
    def _cached(self, key: str, suffix: str) -> Path:
        return self.m_cache / (hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + suffix)

    def _lock(self, path: Path) -> threading.Lock:
        """One job per cached file at a time: the view asks for many in parallel."""
        with self._m_locks_lock:
            return self._m_locks.setdefault(path, threading.Lock())

    def model(self, cfg: str) -> dict:
        """What the config draws, read as the game reads it: the baked ``shared/`` copy first (a mod's own file wins)."""
        shared = configtree.shared_config(cfg)
        if shared and self.m_files.where(cfg) != "mod":
            try:
                return model_parts(self.m_files.read(shared).decode("utf-8", errors="replace"), self.resolver(), shared)
            except (KeyError, FileNotFoundError):
                pass  # no baked copy
        return model_parts(self.m_files.read(cfg).decode("utf-8", errors="replace"), self.resolver(), cfg)

    # what the models name, looked up as the game does (models.fileresolve; viewer.md "How files are found")
    def resolver(self) -> fileresolve.CResolver:
        """The lookups of the models drawn so far: its ``missing`` and ``m_texture_failures`` are the Checks tab's
        list of files (``file_problems``)."""
        if getattr(self, "_m_resolver", None) is None:  # an editor made without ``_setup`` (a test's) too
            self._m_resolver = fileresolve.CResolver(*self.resolver_source())
        return self._m_resolver

    def resolver_source(self) -> tuple[object, str]:
        """The files the models' names are looked up in and the game they follow (another game's island says its own)."""
        return self.m_files, "anno117"

    def texture_file(self, name: str) -> str:
        """The file the game loads for a texture name; raises ``FileNotFoundError`` saying why there is none."""
        resolve = self.resolver()
        found = fileresolve.texture_file(name, resolve.exists, resolve.m_game)
        if not found:
            raise FileNotFoundError(f"{name}: {resolve.why(fileresolve.texture_candidates(name, resolve.m_game))}")
        return found

    def file_problems(self) -> list[tuple[str, str, str]]:
        """(file, why, the config that names it) for each file the models drawn so far name that does not draw."""
        missing = self.resolver().missing()
        named = {
            normalize(problem.name, studio=True) for problem in missing
        }  # a missing map is listed once, not again as failing
        failures = getattr(self, "m_texture_failures", {})
        failed = [
            (path, why, "") for path, why in sorted(failures.items()) if normalize(path, studio=True) not in named
        ]
        return [(problem.name, problem.reason, problem.owner) for problem in missing] + failed
