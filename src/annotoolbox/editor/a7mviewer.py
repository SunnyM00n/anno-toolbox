"""The new-engine games' islands in the editor: an Anno 1800 island (``CA7mViewer`` over ``formats.a7m.reader``) in
the shape the 117 editor works on, and a 2205 session map (``CA6mViewer``). Their edits are built back into the
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
from ..islands.islandview import CFiles
from . import groundedit, othermaterials
from .assetmodels import ASSETS_1800, ASSETS_2205, CAssetModels, CReason
from .formats.a7m.edit117 import CLEARED
from .islandeditor import CIslandEditor
from .modelparts import library_group

NO_LIBRARY = {"117": [], "2070": [], "editor": []}  # nothing to place: 2205's models are not made drawable


class CA7mViewer(othermaterials.COtherMaterials, CIslandEditor):
    """A new-engine island (1800, ``formats.a7m.reader``) in the 117 editor's shape: heights, ground from its material
    maps, rivers, props (``.prp`` files), objects with the model their asset names (drawn through ``rdmglb`` as
    117's are), markers for the rest; its own rivers and lakes for the water tool, the game's decoration in the
    library, a blank island (``clear``) without its objects and props. Its edits, ground included, are built back
    into the island (``formats.a7m.edit1800``)."""

    # the last tree's assets, read once (assets.xml is 290 MB in 1800): (the tree, its assets). The tree by a weak
    # reference: an id() is given again once a tree is gone (another install folder, the other game's tree)
    _m_game_assets: tuple = (None, None)
    ASSETS = ASSETS_1800
    FINE_MAX = 512  # tiles: a bigger map (a 2205 session, 1024) is read at one height sample per tile
    EDITABLE = a7mread.OBJECT_GROUPS  # the groups formats.a7m.edit1800 edits
    CACHE_FORMAT = b"6"  # changes with what ``island()`` holds: the cached island JSON is named by it

    def __init__(self, game, tree, source: str, cache: Path, edits: Path | None = None) -> None:
        from .formats.a7m.reader import CGameFiles

        self.m_game, self.m_tree = game, tree
        self.m_files = CFiles(CGameFiles(tree))
        self.m_view = self._view(self.m_files, source)
        self.m_a7m_path = source
        # a blank island (formats.a7m.edit117.clear) shows without what the build takes off it
        self.m_blank = bool(islandedits.load(edits).get("clear")) if edits is not None else False
        fingerprint = hashlib.sha1(self.m_view.raw_a7m + self.CACHE_FORMAT + bytes([self.m_blank])).hexdigest()[:16]
        meta = {"source": source, "game": game.key, **({"clear": True} if self.m_blank else {})}
        self._setup(cache, fingerprint, edits, meta)
        self._m_editor = ([], {})  # no 117 editor-only assets here

    @staticmethod
    def _view(files, source: str):
        from .formats.a7m.reader import CIsland1800

        return CIsland1800(files, source)

    def game_assets(self) -> CAssetModels:
        """The game's assets, read once per tree (``assetmodels``); raises ``KeyError`` or ``OSError`` without them."""
        tree, found = CA7mViewer._m_game_assets
        if tree is None or tree() is not self.m_tree:
            found = CAssetModels.read(self.m_tree.read_bytes, self.ASSETS)
            CA7mViewer._m_game_assets = (weakref.ref(self.m_tree), found)
        return found

    def asset_models(self) -> CAssetModels | None:
        """``game_assets``, None without the asset file (as ``CIslandEditor.asset_models``)."""
        try:
            return self.game_assets()
        except (OSError, KeyError):
            return None

    def _models(self, placed: list[dict]) -> list[str]:
        """Per placed object its model config, or "" (a marker)."""
        try:
            assets = self.game_assets()
        except (OSError, KeyError) as exc:  # no asset file where the game keeps it: markers only
            self.m_notes.append(tr("islands.no_assets_xml_objects_markers", game=self.m_game.name, error=exc))
            return [placed_object["cfg"] for placed_object in placed]
        return [placed_object["cfg"] or assets.first_model(placed_object["guid"]) for placed_object in placed]

    def _asset(self, guid: int):
        """The game's record of an asset (name, template, models), None without the asset file or the asset."""
        try:
            return self.game_assets().asset(guid)
        except (OSError, KeyError):
            return None

    def objects(self) -> list[dict]:
        placed = self.m_view.objects()
        out = []
        for k, (placed_object, cfg) in enumerate(zip(placed, self._models(placed), strict=True)):
            asset = self._asset(placed_object["guid"])
            template, name = (asset.template, asset.name) if asset else ("", "")
            stem = cfg.split("/")[-1].rsplit(".", 1)[0]
            editable = placed_object["group"] in self.EDITABLE and bool(placed_object.get("name"))
            out.append(
                {
                    "key": islandedits.key(placed_object["name"], placed_object["position"])
                    if editable
                    else f"{self.m_game.key}:{k}",
                    "group": placed_object["group"],
                    "guid": placed_object["guid"],
                    "cfg": cfg,
                    "marker": None if cfg else placed_object["group"],
                    "label": name
                    or stem
                    or f"guid {placed_object['guid']}",  # the asset's own name first, as 1800's editor
                    "template": template or placed_object["group"],
                    "clone": editable,  # placed again by copying it (formats.a7m.edit1800)
                    "variation": placed_object.get("variation", 0),
                    "position": placed_object["position"],
                    "rotation": placed_object["rotation"],
                    "scale": placed_object["scale"],
                    **({} if cfg else {"unresolved": self._why_no_model(placed_object["guid"])}),
                }
            )
        return out

    def _why_no_model(self, guid: int) -> str:
        """Why an object has no model to draw: the game has no such asset, or its asset names none."""
        return CReason.NO_MODEL if self._asset(guid) is not None else CReason.NO_ASSET

    def island(self) -> dict:
        heightmap = np.ascontiguousarray(self.m_view.heights(), "<f4")
        objects = [
            placed_object
            for placed_object in self.objects()
            if not (self.m_blank and placed_object["group"] in CLEARED)
        ]
        if self.m_view.size > self.FINE_MAX:
            heightmap = np.ascontiguousarray(
                heightmap[
                    :: (heightmap.shape[0] - 1) // self.m_view.size, :: (heightmap.shape[0] - 1) // self.m_view.size
                ]
            )
        return {
            "mode": "117",
            "width": int(heightmap.shape[0]),
            "samples_per_tile": (heightmap.shape[0] - 1) // self.m_view.size,
            "heights": base64.b64encode(heightmap.tobytes()).decode("ascii"),
            "splines": [
                {"kind": k, "points": [list(point) for point in points], "widths": list(widths)}
                for k, points, widths in self.m_view.splines()
            ],
            "water": groundedit.water_network(self.m_view.splines()),
            "objects": objects,
            "footprints": self.footprints(objects),
            "props": [] if self.m_blank else self.read_props(),
            "forest": {"tiles": [], "models": []},
            "game": self.m_game.key,
            "game_name": self.m_game.name,
            "readonly": self.m_edits_path is None,
            **self.active_map(),
            **self.ford_grid(),
            **self.environment_grid(),
            **self.surf_map(),
        }

    def model(self, cfg: str) -> dict:
        if cfg.lower().endswith((".prp", ".brp")):  # a baked .brp is read as the .prp text beside it
            from .formats.a7m.reader import prop_parts

            prp = cfg[:-4] + ".prp"
            return prop_parts(self.m_files.read(prp).decode("utf-8", errors="replace"))
        return super().model(cfg)

    def library(self) -> dict:
        """What can be placed: the models already on this island and the game's own decoration (its visual objects
        and props: the assets of the game's ``library`` templates), each placed by copying an object of the island."""
        seen, rows = set(), []

        def add(guid: int, name: str, cfg: str, template: str) -> None:
            if guid not in seen:
                seen.add(guid)
                rows.append({"name": name, "cfg": cfg, "guid": guid, "template": template, "group": library_group(cfg)})

        for placed_object in self.objects():
            if placed_object["clone"] and placed_object["cfg"]:
                add(placed_object["guid"], placed_object["label"], placed_object["cfg"], placed_object["template"])
        try:  # the game's own decoration (a 2205 session lists what it carries)
            for placeable in self.placeables() if self.ASSETS.library else []:
                if placeable.template in self.ASSETS.library:
                    add(
                        placeable.guid,
                        placeable.name or placeable.cfg.rsplit("/", 1)[-1],
                        placeable.cfg,
                        placeable.template,
                    )
        except (OSError, KeyError) as exc:  # no asset file: the island's own models only
            self.m_notes.append(
                tr(
                    "islands.no_assets_xml_library_lists",
                    game=self.m_game.name,
                    error=exc,
                )
            )
        rows.sort(key=lambda row: (row["group"], row["name"].lower()))
        return {**NO_LIBRARY, self.m_game.key: rows}

    def placeable_sources(self) -> tuple[list[bytes], str]:
        assets = self.game_assets()
        return assets.m_files, assets.m_templates


class CA6mViewer(CA7mViewer):
    """A 2205 session map (``formats.a7m.session``): heights, the ground from its material maps, the sea, rivers and
    lakes, props, and the objects with the model of their asset's variation (drawn as 1800's are, markers where
    none exists) under the folders the session keeps them in. Edited objects and props are built back into the
    session (``formats.a7m.edit2205``)."""

    ASSETS = ASSETS_2205
    CACHE_FORMAT = b"2205-1"
    EDITABLE = ("GameObject", "EditorObject", "NaturePreset")  # the groups formats.a7m.edit2205 finds objects in

    def _why_no_model(self, guid: int) -> str:
        return CReason.NO_MODEL  # 2205 keeps its variations by asset: none, or a file that is not there

    def island(self) -> dict:
        out = super().island()
        out["sea"] = self.m_view.has_sea  # a portrait scene has no water at all
        return out

    def objects(self) -> list[dict]:
        """The objects, each with the editor folder it sits in (``folder_path``: the ids from the tree's top)."""
        from .formats.a7m.session import unique_key

        out, paths, seen = super().objects(), self.m_view.folder_paths(), {}
        for item, placed_object in zip(out, self.m_view.objects(), strict=True):
            item["key"] = unique_key(item["key"], seen)
            item["folder_path"] = tr(
                "islands.folder_path",
                path=" / ".join(map(str, paths.get(placed_object["folder"], [placed_object["folder"]]))),
            )
        return out

    def read_props(self) -> list[dict]:
        """The session's ``PropGrid`` props (trees, bushes, rocks, grass; tens of thousands on an Earth map)."""
        from .formats.a7m.session import unique_key

        seen: dict[str, int] = {}
        return [
            {"key": unique_key(islandedits.key(prop["cfg"], prop["position"]), seen), **prop}
            for prop in self.m_view.read_props()
        ]

    @staticmethod
    def _view(files, source: str):
        from .formats.a7m.session import CSession2205

        return CSession2205(files, source)

    def _models(self, placed: list[dict]) -> list[str]:
        assets, out = self.game_assets(), []
        for placed_object in placed:  # a variation past the list draws the game's placeholder: a marker here
            asset = assets.asset(placed_object["guid"])
            models = asset.variations if asset else []
            cfg = placed_object["cfg"] or (
                models[placed_object["variation"]] if placed_object["variation"] < len(models) else ""
            )
            out.append(cfg if cfg and self.m_tree.exists(cfg) else "")
        return out
