"""The old-engine games' islands in the editor (``othergames.installs``): views on Anno 2070 editing's editor whose
edits go into an edit file of their own (built by ``oldenginebuild``). The new-engine games are ``a7mviewer``. What
each shows and lacks: ``docs/island-editor.md``, "Other games".
"""

from __future__ import annotations

import base64
import hashlib
import re
import threading
import weakref
from pathlib import Path

import numpy as np

from ..infra import buildcache
from ..islands import islandedits
from ..islands.islandview import Files
from ..models import info1701, modelread
from ..othergames import grannymodels, grid1701
from ..sources import gametree
from ..sources.overlay import normalize
from . import footprints
from .a7mviewer import NO_LIBRARY
from .formats.isc.water import network as water_1701
from .formats.isd.edit import Island2070Editor, _NoGame
from .grannyviewer import GrannyModels

MODELS_FORMAT = "granny2"  # in the cached island's name: changes with how objects find their models


def blank_meta(game, source: str, edits: Path | None) -> dict:
    """What an island's edit file keeps besides the edits; ``clear`` for a blank island (its objects and rivers are
    taken off at the build: ``formats.isd.edit``, ``formats.isc.edit``)."""
    blank = bool(islandedits.load(edits).get("clear")) if edits is not None else False
    return {"source": source, "game": game.key, **({"clear": True} if blank else {})}


def cache_suffix(meta: dict) -> str:
    """What the cached island's name adds: how objects find their models, and whether the island is blank."""
    return MODELS_FORMAT + ("blank" if meta.get("clear") else "")


def show_blank(out: dict) -> dict:
    """The island as a blank one shows: without objects, props, rivers and lakes (what the build takes off)."""
    out |= {
        "objects": [],
        "footprints": {},
        "props": [],
        "splines": [s for s in out["splines"] if s["kind"] == "Coast"],
    }
    if out.get("water"):
        out["water"] = {**out["water"], "rivers": [], "lakes": []}
    return out


class IsdViewer(GrannyModels, Island2070Editor):
    """An island in 2070's ``.isd`` format from another game (1404): Anno 2070 editing's terrain, ground
    and rivers, its objects with their Granny models (``grannyviewer``), markers where none reads."""

    def __init__(self, game, tree, source: str, cache: Path, edits: Path | None = None) -> None:
        super().__init__(tree, source, cache, edits)
        self.game = game
        self.edit_meta = blank_meta(game, source, edits)
        self._fingerprint += cache_suffix(self.edit_meta)

    def model_config(self, cfg: str):
        return modelread.read_config(self.tree2070.read_text(cfg), self.textures_of(cfg), cfg)

    def island(self) -> dict:
        out = super().island()
        out["forest"]["models"] = []
        out.update({"game": self.game.key, "game_name": self.game.name, "readonly": self.edits_path is None})
        return show_blank(out) if self.edit_meta.get("clear") else out

    def library(self) -> dict:
        return dict(NO_LIBRARY)


def _ocf_by_config(texts: dict[str, str]) -> dict[str, str]:
    """Model config -> the text of the ``.ocf`` that lists it."""
    return {
        normalize(m): text for text in texts.values() for m in re.findall(r"<MeshConfig>([^<]+)</MeshConfig>", text)
    }


class _Ground1701:
    """A 1701 palette texture in the shape the 2070 view's ground code reads (``terrain2070.GroundTexture``)."""

    def __init__(self, t) -> None:
        self.guid, self.name = t.guid, t.name
        self.diffuse, self.normal = t.filename.replace("\\", "/").lower(), None
        self.mean_color = ((t.color >> 16) & 255, (t.color >> 8) & 255, t.color & 255)


class Isd1701Viewer(IsdViewer):
    """A 1701 island (``isd1701``): heights, the ground from the palette's textures (one per tile,
    blended where tiles meet), coast lines and rivers, the objects with the models their ``.ocf`` names
    (markers where none reads). 1701 counts 32 world units a tile, the view one: heights, object heights
    and models are scaled down."""

    REPETITION = 1.0  # a 1701 ground texture covers one tile (assumed; the view tiles it once a tile)
    # the last tree's object configs, read once: (the tree, GUID -> its .mcf variations, model config -> its .ocf's
    # text). The tree by a weak reference: an id() is given again once a tree is gone (another install folder)
    _ocf_cache: tuple = (None, {}, {})

    def __init__(self, game, tree, source: str, cache: Path, edits: Path | None = None) -> None:
        from ..othergames import isd1701

        self.game, self.source = game, source
        self.isl = isd1701.load(tree, source)
        self.size = self.isl.width
        self.types = {}  # no 2070 assets: nothing is placed, nothing made drawable
        self.files = Files(_NoGame())
        self._setup(
            cache,
            hashlib.sha1(tree.read_bytes(source)).hexdigest()[:16],
            edits,
            blank_meta(game, source, edits),
            tree,
        )
        self._editor = ([], {})
        self._table: dict[int, grid1701.Shape] | None = None  # the shipped islands' footprints, read when first asked
        self._own: dict[int, grid1701.Shape] | None = None  # the island's own objects' footprints
        self._shape_lock = threading.Lock()
        self._ground, self._rgb = None, {}
        prj = "data/project/anno3.prj"
        self._palette = isd1701.palette(tree.read_text(prj)) if tree.exists(prj) else {}
        self._fingerprint += cache_suffix(self.edit_meta)

    def model_config(self, cfg: str):
        from ..othergames.isd1701 import TILE

        tree = self.tree2070
        return grannymodels.read_1701(cfg, tree.read_text, tree.exists, 1 / TILE, self.textures_of(cfg))

    def _ocf_tables(self) -> tuple[dict[int, list[str]], dict[str, str]]:
        """(object GUID -> the ``.mcf`` variations of its ``.ocf``, model config -> the text of the ``.ocf`` that lists
        it), read once per tree."""
        tree = self.tree2070
        cached = Isd1701Viewer._ocf_cache
        if cached[0] is None or cached[0]() is not tree:
            texts = {p: tree.read_text(p) for p in tree.iter_paths("data/", suffix=".ocf")}
            cached = (weakref.ref(tree), grannymodels.object_configs(texts), _ocf_by_config(texts))
            Isd1701Viewer._ocf_cache = cached
        return cached[1], cached[2]

    def _ocf_models(self) -> dict[int, list[str]]:
        """Object GUID -> the ``.mcf`` variations of its ``.ocf``."""
        return self._ocf_tables()[0]

    def info_footprint(self, cfg: str) -> list:
        """A model's footprint from its ``.info`` file (``info1701.footprint``, with its ``.ocf``'s offsets); empty when
        the model has no info or its info holds no footprint cells (landscape pieces: the island's scan then)."""
        tree = self.tree2070
        key = cfg.replace("\\", "/").lower()
        path = footprints.info_path(key)
        if not tree.exists(path):
            return []
        return info1701.footprint(tree.read_text(path), self._ocf_tables()[1].get(key, ""))

    def _object_cfg(self, guid: int, variation: int | None) -> str:
        """The ``.mcf`` an object draws (its ``.ocf``'s variation), "" for a marker."""
        models = self._ocf_models().get(guid, [])
        cfg = models[variation if variation is not None and 0 <= variation < len(models) else 0] if models else ""
        return self.model_cfg(cfg) if cfg and self.tree2070.exists(cfg) else ""

    def ground_textures(self) -> dict:
        return {g: _Ground1701(t) for g, t in self._palette.items() if self.tree2070.exists(t.filename)}

    def objects(self) -> list[dict]:
        from ..othergames.isd1701 import TILE

        cfgs = {(o.guid, o.variation): "" for o in self.isl.objects()}
        for guid, variation in cfgs:
            cfgs[guid, variation] = self._object_cfg(guid, variation)
        return [
            {
                "key": f"{self.game.key}:{k}",
                "guid": o.guid,
                "cfg": cfgs[o.guid, o.variation],
                "marker": None if cfgs[o.guid, o.variation] else o.kind,
                "label": f"{o.kind} {o.guid}",
                "template": o.kind,
                "clone": True,  # placed again as a copy of an object of the island (``formats.isc.edit.apply_added``)
                "variation": o.variation or 0,
                "position": [o.x, o.y / TILE, o.z],
                "rotation": [
                    0.0,
                    float(np.sin(-np.radians(o.direction / 4096) / 2)),
                    0.0,
                    float(np.cos(-np.radians(o.direction / 4096) / 2)),
                ],
                "scale": o.scale or 1.0,
            }
            for k, o in enumerate(self.isl.objects())
        ]

    def shapes(self, scan: bool = False) -> dict[int, grid1701.Shape]:
        """GUID -> footprint: the island's own objects', else the table read off every shipped island (kept in the
        cache; ``scan`` makes it when it is not there yet, minutes the first time)."""
        with self._shape_lock:
            if self._table is None:
                self._table = self._kept_shapes()
            if scan and not self._table:
                self._table = grid1701.scan_shapes(self.tree2070)
                self._keep_shapes(self._table)
            if self._own is None:
                self._own = grid1701.island_shapes(self.isl)
            return {**self._table, **self._own}

    def _shapes_file(self) -> tuple[Path, str]:
        """The kept table's file and the stamp of the archives it was read from."""
        return self.cache / "shapes1701.json", gametree.stamp_of([a.path for a in self.tree2070.archives], "shapes1701")

    def _kept_shapes(self) -> dict[int, grid1701.Shape]:
        path, stamp = self._shapes_file()
        kept = buildcache.read_kept(path, {"stamp": stamp})
        try:
            return grid1701.from_json(kept["shapes"]) if kept else {}
        except (KeyError, ValueError, TypeError):
            return {}

    def _keep_shapes(self, table: dict[int, grid1701.Shape]) -> None:
        path, stamp = self._shapes_file()
        try:
            buildcache.write_kept(path, {"stamp": stamp, "shapes": grid1701.to_json(table)})
        except OSError:
            pass  # the table is made again next time

    def footprints(self, objects: list[dict]) -> dict[str, list]:
        """The footprint of each model the island's objects draw: from its ``.info`` file, else from the shapes known
        without a scan (the island's own objects)."""
        shapes = self.shapes()
        out = {}
        for o in objects:
            if not o.get("cfg") or o.get("marker"):
                continue
            if polygons := self.info_footprint(o["cfg"]):
                out[o["cfg"]] = polygons
            elif o.get("guid") in shapes:
                out[o["cfg"]] = [grid1701.polygon(shapes[o["guid"]])]
        return out

    def footprint_of(self, cfg: str) -> list:
        """The footprint of one model config (the Place tool's pick): its ``.info`` file's, else found through its
        object's GUID, scanning the shipped islands for it when the island has none of its kind."""
        if polygons := self.info_footprint(cfg):
            return polygons
        key = cfg.replace("\\", "/").lower()
        guid = next((g for g, models in self._ocf_models().items() if key in models), None)
        shape = self.shapes(scan=True).get(guid) if guid is not None else None
        return [grid1701.polygon(shape)] if shape else []

    def placeables(self) -> list:
        """1701's objects that can be placed: one per object config (``placeables.from_object_configs``)."""
        from . import placeables

        paths = self.tree2070.iter_paths("data/", suffix=".ocf")
        return placeables.from_object_configs({p: self.tree2070.read_text(p) for p in paths})

    def splines(self) -> list[dict]:
        from ..othergames.isd1701 import TILE

        return [
            {"kind": kind, "points": [[x, y / TILE, z] for x, y, z in line.points], "widths": list(line.widths)}
            for kind, lines in (("River", self.isl.rivers()), ("Coast", self.isl.coasts()))
            for line in lines
        ]

    def island(self) -> dict:
        from ..othergames.isd1701 import TILE

        hm = (np.asarray(self.isl.heightmap(), "<f4") / TILE).astype("<f4")
        objects = self.objects()
        out = {
            "mode": "2070",
            "width": int(hm.shape[0]),
            "samples_per_tile": 1,
            "heights": base64.b64encode(hm.tobytes()).decode("ascii"),
            "splines": self.splines(),
            "water": water_1701(self.isl),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": [],
            "forest": {"tiles": [], "models": []},
            "game": self.game.key,
            "game_name": self.game.name,
            "readonly": self.edits_path is None,
            "model_notes": self.model_notes(),
        }
        return show_blank(out) if self.edit_meta.get("clear") else out

    def materials(self) -> list[dict]:
        used = set(self.isl.texture_ids())
        textures = sorted(self.ground_textures().values(), key=lambda t: (t.guid not in used, t.name.lower()))
        return [
            {"id": t.guid, "name": t.name, "diffuse": t.diffuse, "normal": t.normal, "repetition": self.REPETITION}
            for t in textures
        ]

    def albedo(self, texels: int) -> np.ndarray:
        ids, weights = self.ground_layers(texels)
        n = weights.shape[1]
        tile = (np.arange(n) + 0.5) / texels
        out = np.zeros((n, n, 3), np.float32)
        for k, idx in enumerate(ids):
            tex = self._texture_rgb(idx)
            u = ((tile / self.REPETITION) % 1 * tex.shape[1]).astype(int)
            v = ((tile / self.REPETITION) % 1 * tex.shape[0]).astype(int)
            out += tex[np.ix_(v, u)] * weights[k][..., None]
        return out
