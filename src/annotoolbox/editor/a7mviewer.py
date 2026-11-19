"""The new-engine games' islands in the editor: an Anno 1800 island (``A7mViewer`` over ``formats.a7m.reader``) in
the shape the 117 editor works on, and a 2205 session map (``A6mViewer``). Their edits are built back into the
island (``formats.a7m.edit1800``) and the session (``formats.a7m.edit2205``). Apart from ``gameviewers`` so that an
application restricted to 1800 leaves the 2070-format viewers out. What each shows and lacks: ``docs/island-editor.md``,
"Other games".
"""

from __future__ import annotations

import base64
import hashlib
import weakref
from pathlib import Path

import numpy as np

from ..formats import a7mread
from ..infra.i18n import tr
from ..islands import islandedits
from ..islands.islandview import Files
from . import groundedit, othermaterials
from .assetmodels import ASSETS_1800, ASSETS_2205, AssetModels, Reason
from .formats.a7m.edit117 import CLEARED
from .islandeditor import IslandEditor
from .modelparts import library_group

NO_LIBRARY = {"117": [], "2070": [], "editor": []}  # nothing to place: 2205's models are not made drawable


class A7mViewer(othermaterials.OtherMaterials, IslandEditor):
    """A new-engine island (1800, ``formats.a7m.reader``) in the 117 editor's shape: heights, ground from its material
    maps, rivers, props (``.prp`` files), objects with the model their asset names (drawn through ``rdmglb`` as
    117's are), markers for the rest; its own rivers and lakes for the water tool, the game's decoration in the
    library, a blank island (``clear``) without its objects and props. Its edits, ground included, are built back
    into the island (``formats.a7m.edit1800``)."""

    # the last tree's assets, read once (assets.xml is 290 MB in 1800): (the tree, its assets). The tree by a weak
    # reference: an id() is given again once a tree is gone (another install folder, the other game's tree)
    _game_assets: tuple = (None, None)
    ASSETS = ASSETS_1800
    FINE_MAX = 512  # tiles: a bigger map (a 2205 session, 1024) is read at one height sample per tile
    EDITABLE = a7mread.OBJECT_GROUPS  # the groups formats.a7m.edit1800 edits
    CACHE_FORMAT = b"6"  # changes with what ``island()`` holds: the cached island JSON is named by it

    def __init__(self, game, tree, source: str, cache: Path, edits: Path | None = None) -> None:
        from .formats.a7m.reader import GameFiles

        self.game, self.tree = game, tree
        self.files = Files(GameFiles(tree))
        self.view = self._view(self.files, source)
        self.a7m_path = source
        # a blank island (formats.a7m.edit117.clear) shows without what the build takes off it
        self.blank = bool(islandedits.load(edits).get("clear")) if edits is not None else False
        fingerprint = hashlib.sha1(self.view.raw_a7m + self.CACHE_FORMAT + bytes([self.blank])).hexdigest()[:16]
        meta = {"source": source, "game": game.key, **({"clear": True} if self.blank else {})}
        self._setup(cache, fingerprint, edits, meta)
        self._editor = ([], {})  # no 117 editor-only assets here

    @staticmethod
    def _view(files, source: str):
        from .formats.a7m.reader import Island1800

        return Island1800(files, source)

    def game_assets(self) -> AssetModels:
        """The game's assets, read once per tree (``assetmodels``); raises ``KeyError`` or ``OSError`` without them."""
        tree, found = A7mViewer._game_assets
        if tree is None or tree() is not self.tree:
            found = AssetModels.read(self.tree.read_bytes, self.ASSETS)
            A7mViewer._game_assets = (weakref.ref(self.tree), found)
        return found

    def asset_models(self) -> AssetModels | None:
        """``game_assets``, None without the asset file (as ``IslandEditor.asset_models``)."""
        try:
            return self.game_assets()
        except (OSError, KeyError):
            return None

    def _models(self, placed: list[dict]) -> list[str]:
        """Per placed object its model config, or "" (a marker)."""
        try:
            assets = self.game_assets()
        except (OSError, KeyError) as exc:  # no asset file where the game keeps it: markers only
            self.notes.append(tr("islands.no_assets_xml_objects_markers", game=self.game.name, error=exc))
            return [o["cfg"] for o in placed]
        return [o["cfg"] or assets.first_model(o["guid"]) for o in placed]

    def _asset(self, guid: int):
        """The game's record of an asset (name, template, models), None without the asset file or the asset."""
        try:
            return self.game_assets().asset(guid)
        except (OSError, KeyError):
            return None

    def objects(self) -> list[dict]:
        placed = self.view.objects()
        out = []
        for k, (o, cfg) in enumerate(zip(placed, self._models(placed), strict=True)):
            info = self._asset(o["guid"])
            template, name = (info.template, info.name) if info else ("", "")
            stem = cfg.split("/")[-1].rsplit(".", 1)[0]
            editable = o["group"] in self.EDITABLE and bool(o.get("name"))
            out.append(
                {
                    "key": islandedits.key(o["name"], o["position"]) if editable else f"{self.game.key}:{k}",
                    "group": o["group"],
                    "guid": o["guid"],
                    "cfg": cfg,
                    "marker": None if cfg else o["group"],
                    "label": name or stem or f"guid {o['guid']}",  # the asset's own name first, as 1800's editor
                    "template": template or o["group"],
                    "clone": editable,  # placed again by copying it (formats.a7m.edit1800)
                    "variation": o.get("variation", 0),
                    "position": o["position"],
                    "rotation": o["rotation"],
                    "scale": o["scale"],
                    **({} if cfg else {"unresolved": self._why_no_model(o["guid"])}),
                }
            )
        return out

    def _why_no_model(self, guid: int) -> str:
        """Why an object has no model to draw: the game has no such asset, or its asset names none."""
        return Reason.NO_MODEL if self._asset(guid) is not None else Reason.NO_ASSET

    def island(self) -> dict:
        hm = np.ascontiguousarray(self.view.heights(), "<f4")
        objects = [o for o in self.objects() if not (self.blank and o["group"] in CLEARED)]
        if self.view.size > self.FINE_MAX:
            hm = np.ascontiguousarray(
                hm[:: (hm.shape[0] - 1) // self.view.size, :: (hm.shape[0] - 1) // self.view.size]
            )
        return {
            "mode": "117",
            "width": int(hm.shape[0]),
            "samples_per_tile": (hm.shape[0] - 1) // self.view.size,
            "heights": base64.b64encode(hm.tobytes()).decode("ascii"),
            "splines": [
                {"kind": k, "points": [list(p) for p in pts], "widths": list(w)} for k, pts, w in self.view.splines()
            ],
            "water": groundedit.water_network(self.view.splines()),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": [] if self.blank else self.props(),
            "forest": {"tiles": [], "models": []},
            "game": self.game.key,
            "game_name": self.game.name,
            "readonly": self.edits_path is None,
            **self.active_map(),
            **self.ford_grid(),
            **self.environment_grid(),
            **self.surf_map(),
        }

    def model(self, cfg: str) -> dict:
        if cfg.lower().endswith((".prp", ".brp")):  # a baked .brp is read as the .prp text beside it
            from .formats.a7m.reader import prop_parts

            prp = cfg[:-4] + ".prp"
            return prop_parts(self.files.read(prp).decode("utf-8", errors="replace"))
        return super().model(cfg)

    def library(self) -> dict:
        """What can be placed: the models already on this island and the game's own decoration (its visual objects
        and props: the assets of the game's ``library`` templates), each placed by copying an object of the island."""
        seen, rows = set(), []

        def add(guid: int, name: str, cfg: str, template: str) -> None:
            if guid not in seen:
                seen.add(guid)
                rows.append({"name": name, "cfg": cfg, "guid": guid, "template": template, "group": library_group(cfg)})

        for o in self.objects():
            if o["clone"] and o["cfg"]:
                add(o["guid"], o["label"], o["cfg"], o["template"])
        try:  # the game's own decoration (a 2205 session lists what it carries)
            for p in self.placeables() if self.ASSETS.library else []:
                if p.template in self.ASSETS.library:
                    add(p.guid, p.name or p.cfg.rsplit("/", 1)[-1], p.cfg, p.template)
        except (OSError, KeyError) as exc:  # no asset file: the island's own models only
            self.notes.append(
                tr(
                    "islands.no_assets_xml_library_lists",
                    game=self.game.name,
                    error=exc,
                )
            )
        rows.sort(key=lambda r: (r["group"], r["name"].lower()))
        return {**NO_LIBRARY, self.game.key: rows}

    def placeable_sources(self) -> tuple[list[bytes], str]:
        assets = self.game_assets()
        return assets.files, assets.templates


class A6mViewer(A7mViewer):
    """A 2205 session map (``formats.a7m.session``): heights, the ground from its material maps, the sea, rivers and
    lakes, props, and the objects with the model of their asset's variation (drawn as 1800's are, markers where
    none exists) under the folders the session keeps them in. Edited objects and props are built back into the
    session (``formats.a7m.edit2205``)."""

    ASSETS = ASSETS_2205
    CACHE_FORMAT = b"2205-1"
    EDITABLE = ("GameObject", "EditorObject", "NaturePreset")  # the groups formats.a7m.edit2205 finds objects in

    def _why_no_model(self, guid: int) -> str:
        return Reason.NO_MODEL  # 2205 keeps its variations by asset: none, or a file that is not there

    def island(self) -> dict:
        out = super().island()
        out["sea"] = self.view.has_sea  # a portrait scene has no water at all
        return out

    def objects(self) -> list[dict]:
        """The objects, each with the editor folder it sits in (``folder_path``: the ids from the tree's top)."""
        from .formats.a7m.session import unique_key

        out, paths, seen = super().objects(), self.view.folder_paths(), {}
        for item, o in zip(out, self.view.objects(), strict=True):
            item["key"] = unique_key(item["key"], seen)
            item["folder_path"] = tr(
                "islands.folder_path", path=" / ".join(map(str, paths.get(o["folder"], [o["folder"]])))
            )
        return out

    def props(self) -> list[dict]:
        """The session's ``PropGrid`` props (trees, bushes, rocks, grass; tens of thousands on an Earth map)."""
        from .formats.a7m.session import unique_key

        seen: dict[str, int] = {}
        return [{"key": unique_key(islandedits.key(p["cfg"], p["position"]), seen), **p} for p in self.view.props()]

    @staticmethod
    def _view(files, source: str):
        from .formats.a7m.session import Session2205

        return Session2205(files, source)

    def _models(self, placed: list[dict]) -> list[str]:
        assets, out = self.game_assets(), []
        for o in placed:  # a variation past the list draws the game's placeholder: a marker here
            info = assets.asset(o["guid"])
            models = info.variations if info else []
            cfg = o["cfg"] or (models[o["variation"]] if o["variation"] < len(models) else "")
            out.append(cfg if cfg and self.tree.exists(cfg) else "")
        return out
