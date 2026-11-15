"""Stand-ins for the installed games that the tests share: a 2070 source tree, 117's files and its asset list,
each over a dict of game paths, plus the asset database and the ``<Asset>`` text builder and synthetic savegames of
117 and 2070; ``installed`` is the real game for the tests that skip without it."""

from __future__ import annotations

import base64
import fnmatch
import json
import os
import struct
import zlib
from collections.abc import Iterable, Iterator
from pathlib import Path

import numpy as np

from annotoolbox.formats import bintree, filedb, rda, savegame2070
from annotoolbox.formats.isd2070 import Node
from annotoolbox.islands import islandedits
from annotoolbox.othergames import installs
from annotoolbox.sources.anno117 import Anno117Assets, Anno117Files
from annotoolbox.sources.assetdb import Asset
from annotoolbox.sources.overlay import SourceTree
from annotoolbox.suite import livelink

EDIT_SIZE, EDIT_SPT = 16, 2  # the island of ``edit_island``: tiles a side, height samples a tile


def edit_island(land: int = 6, **extra) -> dict:
    """An island as the editor's edit document takes it: 16 tiles at two samples a tile, land at 0.7 in the middle,
    sea round it; two objects, an editor-only marker, two props, three forest tiles, a river and a lake."""
    w = EDIT_SIZE * EDIT_SPT + 1
    h = np.full((w, w), -2.0, "f4")
    h[land : w - land, land : w - land] = 0.7
    out = {
        "width": w,
        "samples_per_tile": EDIT_SPT,
        "heights": base64.b64encode(h.astype("<f4").tobytes()).decode(),
        "splines": [],
        "water": {
            "rivers": [{"points": [[4.0, 0.5, 4.0], [8.0, 0.4, 8.0], [12.0, 0.3, 8.0]], "widths": [2.0, 2.0, 3.0]}],
            "lakes": [{"points": [[9.0, 9.0], [11.0, 9.0], [11.0, 11.0]], "level": 1.0}],
        },
        "objects": [
            edit_object("rock.cfg", [5.5, 0.7, 6.5]),
            edit_object("rock.cfg", [9.0, 0.7, 9.0]),
            {**edit_object("", [7.0, 0.7, 7.0]), "cfg": "", "guid": 99, "marker": "Herd", "label": "Herd Sheep"},
        ],
        "props": [
            {**edit_object("fall.cfg", [10.0, 1.0, 4.0]), "scale": [1.0, 1.0, 1.0]},
            {**edit_object("grass.cfg", [11.0, 0.7, 5.0]), "scale": [1.0, 1.0, 1.0]},
        ],
        "forest": {"tiles": [[5, 5], [6, 5], [7, 5]], "models": ["tree_a.cfg", "tree_b.cfg"]},
    }
    out.update(extra)
    return out


def edit_object(cfg: str, pos) -> dict:
    """One object of ``edit_island`` (a model ``cfg``, or an editor-only asset with ``cfg`` empty) at ``pos``."""
    return {
        "key": islandedits.key(cfg or "guid:99", pos),
        "guid": 1,
        "cfg": cfg,
        "marker": None,
        "label": cfg.removesuffix(".cfg"),
        "position": list(pos),
        "rotation": [0.0, 0.0, 0.0, 1.0],
        "scale": 1.0,
    }


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


class FakeTree:
    """``SourceTree`` over a dict of game paths to text or bytes: the reads, lookups and listings the island build
    makes. A path is found as written or case- and slash-insensitively, as the real tree finds it. ``broken``
    names files whose read fails; ``archive`` is what every file is said to come from."""

    def __init__(
        self, files: dict[str, str | bytes] | None = None, *, archive: str = "Data3", broken: Iterable[str] = ()
    ) -> None:
        self.files: dict[str, str | bytes] = dict(files or {})
        self.archive = archive
        self.broken = tuple(broken)

    def _key(self, rel: str) -> str:
        if rel in self.files:
            return rel
        want = _norm(rel)
        for key in self.files:
            if _norm(key) == want:
                return key
        raise FileNotFoundError(rel)

    def exists(self, rel: str) -> bool:
        try:
            self._key(rel)
        except FileNotFoundError:
            return False
        return True

    def read_bytes(self, rel: str) -> bytes:
        key = self._key(rel)
        if key in self.broken:
            raise OSError("access denied")
        data = self.files[key]
        return data if isinstance(data, bytes) else data.encode("utf-8")

    def read_text(self, rel: str, encoding: str = "utf-8") -> str:
        key = self._key(rel)
        if key in self.broken:
            raise OSError("access denied")
        data = self.files[key]
        return data if isinstance(data, str) else data.decode(encoding)

    def texture_key(self, rel: str) -> str | None:
        """The file a texture name loads, by the real tree's rule: as written, else ``<stem>_0`` or ``<stem>``
        with a texture extension."""
        stem = _norm(rel).rpartition(".")[0]
        found = [rel] + [f"{stem}{n}{ext}" for ext in (".dds", ".png", ".jpg") for n in ("_0", "")]
        return next((self._key(p) for p in found if self.exists(p)), None)

    def iter_paths(self, prefix: str = "", suffix: str | None = None) -> Iterator[str]:
        return (p for p in self.files if p.startswith(prefix) and (suffix is None or p.endswith(suffix)))

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self.files if fnmatch.fnmatchcase(p, pattern))

    def archive_of(self, rel: str) -> str:
        return self.archive


class FakeFiles:
    """``Anno117Files`` over a dict of game paths to bytes; ``default`` is what a path not in it reads as
    (None: not found)."""

    def __init__(self, files: dict[str, bytes] | None = None, *, default: bytes | None = None) -> None:
        self.files = dict(files or {})
        self.default = default

    def read(self, path: str) -> bytes:
        if path in self.files:
            return self.files[path]
        if self.default is not None:
            return self.default
        raise FileNotFoundError(path)

    def exists(self, path: str) -> bool:
        return path in self.files

    def iter_paths(self, prefix: str = "") -> list[str]:
        return sorted(p for p in self.files if p.startswith(prefix))

    def list(self, prefix: str) -> list[str]:
        """The editor's ``islandview.Files.list`` (a mod's files and the game's)."""
        return self.iter_paths(prefix)


class FakeDb:
    """The asset database as far as a reader asks it by GUID."""

    def __init__(self, *assets: Asset) -> None:
        self.assets = {a.guid: a for a in assets}

    def by_guid(self, guid: int) -> Asset | None:
        return self.assets.get(guid)

    def by_template(self, template: str):
        return (a for a in self.assets.values() if a.template == template)


def assets117(text: str) -> Anno117Assets:
    """117's asset list given as text, no install needed."""
    source = Anno117Assets.__new__(Anno117Assets)
    source.text = text
    source._templates = None
    return source


def asset_xml(template: str, guid: int, name: str, body: str = "", *, standard: str = "", tail: str = "") -> str:
    """One ``<Asset>`` as 117's asset list writes it: ``standard`` adds to the Standard block, ``tail`` follows
    the body inside Values."""
    return (
        f"<Asset><Template>{template}</Template><Values><Standard><GUID>{guid}</GUID><Name>{name}</Name>{standard}"
        f"</Standard>{body}{tail}</Values></Asset>"
    )


INSTALL_VARS = {
    "anno1404": "ANNO1404_DIR",
    "anno1701": "ANNO1701_DIR",
    "anno2070": "ANNO2070_DIR",
    "anno2205": "ANNO2205_DIR",
    "anno1800": "ANNO1800_DIR",
    "anno117": "ANNO117_DIR",
}


def installed(game: str):
    """An installed game's files for a real-data test, read by the game's archive rules: the folder its variable in
    ``INSTALL_VARS`` names, or None without one (the test then skips). 117 gives ``Anno117Files``, the rest a tree."""
    folder = os.environ.get(INSTALL_VARS[game])
    if not folder or not Path(folder).is_dir():
        return None
    if game == "anno2070":
        return SourceTree(folder)
    if game == "anno117":
        return Anno117Files(folder)
    return installs.GAMES[game].tree(Path(folder))


def _vd(name: str, kind: str, extra: str = "", items: str = "") -> str:
    inner = f"<Items>{items}</Items>" if items else ""
    return f"<ValueDefinition><DataType>{kind}</DataType><Name>{name}</Name>{extra}{inner}</ValueDefinition>"


ASSET_SCHEMA = (  # the property definitions of ``asset_game`` (117's properties-meta.xml shape)
    "<Properties><Groups><Group><Name />"
    "<Property><Name>Standard</Name>" + _vd("GUID", "Integer") + _vd("Name", "String")
    + _vd("IconFilename", "FileName") + _vd("InfoDescription", "Text") + "</Property>"
    "<Property><Name>Building</Name>"
    + _vd("BuildCost", "Vector", items=_vd("Product", "Asset", "<NeededProperty>Product</NeededProperty>")
          + _vd("Amount", "Integer", "<Min>0</Min><Max>100</Max>"))
    + _vd("Level", "Choice", "<DataSet>Level</DataSet>") + _vd("Kinds", "Flags", "<DataSet>ObjectType</DataSet>")
    + _vd("Upkeep", "Float", "<Min>0</Min>") + _vd("Workers", "Array", "<DataSet>Level</DataSet>",
                                                   _vd("Count", "Integer"))
    + _vd("Model", "Struct", items=_vd("Scale", "Float") + _vd("Visible", "Boolean")) + "</Property>"
    "<Property><Name>Product</Name>" + _vd("Price", "Integer") + "</Property>"
    "<Property><Name>Quest</Name>" + _vd("Reward", "Asset") + "</Property>"
    "</Group></Groups></Properties>"
)  # fmt: skip
ASSET_DEFAULTS = (  # the stripped export beside it: the groups' defaults
    "<Properties><Groups><Group><Name>Default</Name><DefaultContainerValues><Building><BuildCost><Amount>1</Amount>"
    "</BuildCost></Building></DefaultContainerValues><DefaultValues><Building><Upkeep>5</Upkeep></Building>"
    "<Standard /></DefaultValues></Group></Groups></Properties>"
)
ASSET_TEMPLATES = (
    "<Templates><Group><Name>Objects</Name>"
    "<Template><Name>House</Name><Properties><Standard /><Building><Level>Low</Level><Model><Scale>1</Scale>"
    "</Model></Building></Properties></Template>"
    "<Template><Name>Good</Name><Properties><Standard /><Product><Price>10</Price></Product></Properties></Template>"
    "<Template><Name>Mission</Name><Properties><Standard /><Quest /></Properties></Template>"
    "</Group></Templates>"
)
ASSET_DATA_SETS = (
    "<DataSets><Group><Name>Global</Name>"
    "<DataSet><Name>Level</Name><Items><Item><Name>Low</Name><Id>0</Id></Item><Item><Name>High</Name><Id>1</Id>"
    "</Item></Items></DataSet>"
    "<DataSet><Name>ObjectType</Name><Items><Item><Name>GameObject</Name></Item><Item><Name>EditorObject</Name>"
    "</Item></Items></DataSet></Group></DataSets>"
)


def asset_list() -> str:
    """The asset file of ``asset_game``: two goods, a house, a house made from it (a base asset) and a quest that
    holds an inline ``<Asset>`` in its values."""
    goods = asset_xml("Good", 1001, "Wood", "<Product><Price>12</Price></Product>") + asset_xml("Good", 1002, "Stone")
    house = asset_xml(
        "House", 2001, "Hut",
        "<Building><BuildCost><Item><Product>1001</Product><Amount>4</Amount></Item></BuildCost>"
        "<Kinds>GameObject</Kinds><Workers><Low><Count>2</Count></Low><High><Count>3</Count></High></Workers>"
        "</Building>",
        standard="<InfoDescription>-1001</InfoDescription>",
    )  # fmt: skip
    big = "<Asset><BaseAssetGUID>2001</BaseAssetGUID><Values><Standard><GUID>2002</GUID><Name>Big Hut</Name>"
    big += "</Standard><Building><Upkeep>9</Upkeep></Building></Values></Asset>"
    inline = "<Asset><Template>Good</Template><Values><Standard><GUID>9</GUID></Standard></Values></Asset>"
    quest = asset_xml("Mission", 3001, "Find wood", f"<Quest><Reward>1002</Reward><Inline>{inline}</Inline></Quest>")
    return (
        "﻿<AssetList><Groups><Group><Name>Goods</Name><Assets>" + goods + "</Assets></Group>"
        "<Group><Name>Buildings</Name><Groups><Group><Name>Houses</Name><Assets>" + house + big
        + "</Assets></Group></Groups></Group><Group><Assets>" + quest + "</Assets></Group></Groups></AssetList>"
    )  # fmt: skip


def asset_game(root: Path, donor: bool = False) -> Path:
    """A synthetic Anno 117 asset tree of loose files at ``root`` (schema, templates, data sets, assets, English
    texts); ``donor``: an Anno 1800 tree whose schema describes ``Building/Upkeep``, the 117 one borrows it."""
    root = Path(root)
    if donor:
        files = {
            "data/config/export/main/asset/properties-toolone.xml": ASSET_SCHEMA.replace(
                "<Name>Upkeep</Name>", "<Name>Upkeep</Name><Description>Running cost a minute</Description>"
            ),
            "data/config/export/main/asset/assets.xml": "<AssetList />",
        }
    else:
        export = "data/base/config/export/"
        files = {
            export + "properties-meta.xml": ASSET_SCHEMA,
            export + "properties.xml": ASSET_DEFAULTS,
            export + "templates.xml": ASSET_TEMPLATES,
            export + "assets.xml": asset_list(),
            "data/base/config/game/datasets.xml": ASSET_DATA_SETS,
            "data/base/config/gui/texts_english.xml": (
                "<TextExport><Texts><Text><LineId>-1001</LineId><Text>A small hut</Text></Text></Texts></TextExport>"
            ),
        }
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8", newline="\n")
    return root


def fake_mod(root: Path, mod_id: str, files: dict[str, str | bytes], **info) -> Path:
    """A mod folder ``root/<mod_id>`` for the mod loader: ``modinfo.json`` (Anno 8, version 1.0.0, ``info`` on top:
    ``ModName``, ``Category``, ``Dependencies``, ``Options`` ...) and ``files`` by their path in the mod."""
    folder = Path(root) / mod_id
    folder.mkdir(parents=True, exist_ok=True)
    data = {"Anno": 8, "ModID": mod_id, "Version": "1.0.0", "ModName": {"English": mod_id}, **info}
    (folder / "modinfo.json").write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
    for path, body in files.items():
        dest = folder / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            dest.write_bytes(body)
        else:
            dest.write_text(body, encoding="utf-8", newline="\n")
    return folder


class FakeLink(livelink.LiveLink):
    """A connected live link with no transport: it answers ``DONE`` to what ``kinds`` covers and keeps every request
    in ``sent`` (``(name, args)``); ``player(event, payload)`` plays an event of the game."""

    def __init__(self, kinds=livelink.KINDS, game: str = "anno117", build: str = "3.0") -> None:
        super().__init__()
        self.set_status(livelink.LinkStatus(True, game, build, frozenset(kinds)))

    def _transmit(self, request):
        return livelink.Reply(livelink.DONE, data={"name": request.name})

    def player(self, event: str, payload: dict) -> None:
        self.emit(event, payload)

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.sent]


def _attr(name: str, data: bytes) -> filedb.Attrib:
    return filedb.Attrib(name, data)


STOCKS117 = (  # the first area's stock: goods 2068 and 2071 with 186 and 5; the second's: 2173 and 2174 with 20 and 25
    ("StrgLrg", struct.pack("<4I", 2068, 2, 186, 5)),
    ("StrgSmll", bytes.fromhex("7d080000001419")),
)


def _session117(guid: int, areas: int) -> filedb.Tag:
    """A ``GameSessions`` record: its description and its own document (a map template and island areas)."""
    infos = []
    for n in range(areas):
        trends = filedb.Tag("StorageTrends", [_attr("#32768", struct.pack("<I", 2135 + n)), filedb.Tag("#1", [])])
        infos += [_attr("#32768", struct.pack("<H", 0x2041 + 0x80 * n)), filedb.Tag("#1", [
            _attr("CityNameGuid", bytes(range(8))), _attr("OwnerProfile", struct.pack("<I", 32777 + n)),
            filedb.Tag("AreaEconomy", [trends, _attr(*(STOCKS117[n % 2]))]),
        ])]  # fmt: skip
    manager = filedb.Tag("GameSessionManager", [
        filedb.Tag("MapTemplate", [_attr("Filename", "data/maps/test.a7tinfo".encode("utf-16-le"))]),
        filedb.Tag("AreaInfo", infos), filedb.Tag("SessionEffectManager", [_attr("NextEffectIndex", bytes(8))]),
    ])  # fmt: skip
    doc = filedb.write(filedb.Tag("", [_attr("SessionFileVersion", struct.pack("<I", 80)), manager]))
    desc = filedb.Tag("SessionDesc", [_attr("SessionGUID", struct.pack("<I", guid)),
                                      filedb.Tag("MapGeneratorSettings", [_attr("RandomSeed", struct.pack("<I", 7))])])  # fmt: skip
    return filedb.Tag("#1", [desc, filedb.Tag("SessionData", [_attr("BinaryData", doc)])])


def _setup117(root: str, profile: str, mods: dict[str, str]) -> bytes:
    slot = filedb.Tag("Slot", [
        filedb.Tag("ID", [_attr("id", struct.pack("<H", 0))]), _attr("CompanyName", profile.encode("utf-8")),
        _attr("ColorGuid", struct.pack("<I", 91304)), _attr("LogoGuid", struct.pack("<I", 92)),
        _attr("PortraitGuid", struct.pack("<I", 33070)),
    ])  # fmt: skip
    listed = [filedb.Tag("#1", [_attr("ModID", m.encode()), _attr("ModVersion", v.encode())]) for m, v in mods.items()]
    top = filedb.Tag(root, [
        _attr("SavegameProfileDisplayName", profile.encode("utf-16-le")), _attr("GameSeed", struct.pack("<I", 4695)),
        filedb.Tag("SetupTaxes", [filedb.Tag("value", [_attr("id", struct.pack("<H", 1))])]),
        filedb.Tag("ModActivationInfo", [filedb.Tag("Mods", listed), filedb.Tag("Options", [])]),
        filedb.Tag("SetupSlots", [slot]), _attr("TotalMoney", struct.pack("<5i", 1500, -1, -1, -1, -1)),
    ])  # fmt: skip
    return filedb.write(filedb.Tag("", [top]))


def _data117(money: dict[int, int], sessions: int) -> bytes:
    def custom(tag: str, value: bytes) -> filedb.Tag:
        return filedb.Tag(tag, [_attr("#32768", b"\x01"), _attr("#32768", value)])

    human = filedb.Tag("#1", [_attr("guid", struct.pack("<I", 41)), filedb.Tag("MetaParticipant", [
        custom("CustomCompanyName", "Tester".encode("utf-16-le")), custom("CustomCompanyColorGuids", struct.pack("<I", 91304)),
        custom("CustomCompanyLogo", struct.pack("<I", 92)), custom("CustomAvatarPortrait", struct.pack("<I", 33070)),
    ])])  # fmt: skip
    pairs = [filedb.Tag("Pair", [_attr("ParticipantGUID", struct.pack("<I", g)),
                                 filedb.Tag("MetaEconomy", [_attr("StrgLrg", struct.pack("<4I", 91372, 918644, 2, m & 0xFFFFFFFF))])])
             for g, m in money.items()]  # fmt: skip
    games = []
    for n in range(sessions):
        games += [_attr("#32768", struct.pack("<I", 3245 + n)), _session117(3245 + n, 2)]
    manager = filedb.Tag("MetaGameManager", [
        filedb.Tag("MetaGameObjectManager", [filedb.Tag("MetaObjectMap", [human])]),
        filedb.Tag("EconomyManager", pairs), filedb.Tag("GameSessions", games),
    ])  # fmt: skip
    return filedb.write(filedb.Tag("", [manager]))


def fake_save117(path: Path, mods: dict[str, str] | None = None, money: dict[int, int] | None = None,
                 sessions: int = 1, profile: str = "Tester") -> Path:  # fmt: skip
    """A synthetic 117 savegame at ``path``: the four parts with the game's 12-byte tail, the player in all three
    places, ``mods`` (ModID -> version), each participant's ``money``, ``sessions`` with two island areas each."""
    meta = filedb.write(filedb.Tag("", [
        _attr("CorporationFileVersion", struct.pack("<I", 80)), _attr("CorporationVersionName", "Anno 117 - Pax Romana".encode("utf-16-le")),
        _attr("LastModTime", struct.pack("<Q", 1_700_000_000)), _attr("CorporationSaveGameName", "Autosave ".encode("utf-16-le")),
    ]))  # fmt: skip
    docs = {"meta.a7s": meta, "header.a7s": _setup117("CorporationProfile", profile, mods or {}),
            "gamesetup.a7s": _setup117("GameSetupManager", profile, mods or {}),
            "data.a7s": _data117(money or {41: 1500, 31102: 9000}, sessions)}  # fmt: skip
    tail = zlib.compress(b"", 9)
    parts = {n: zlib.compress(d, 9) + tail + struct.pack("<I", len(d)) for n, d in docs.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rda.write_v22_stored(parts))
    return path


def _bin(name: str, text: str | None = None, *children: Node, data: bytes | None = None) -> Node:
    return Node(name, text, data, list(children))


def fake_save2070(path: Path, credits: int = 80000) -> Path:
    """A synthetic 2070 savegame at ``path``: the V2.0 archive, ``ZZZZ`` packed binary tag trees, player 0 the human
    with ``credits`` (stored times 1,024, plus a fraction), one island, 24 bytes in the preamble as the game writes."""
    faction = _bin("i", None, _bin("PlayerId", "0"), _bin("Type", None, _bin("PlayerType", "Human")),
                   _bin("Name", data="Tester".encode("utf-16-le")), _bin("Color", "-7410177"))  # fmt: skip
    game = bintree.Tree([_bin("Parameters", None, _bin("FactionInfos", None, faction), _bin("Seed", "5"))])
    player = _bin("i", None, _bin("hasValue", "1"), _bin("m_PlayerID", "0"),
                  _bin("m_Resource", None, _bin("Credits", str(credits * 1024 + 6))))  # fmt: skip
    island = _bin("v", None, _bin("IslandConfig", None, _bin("IslandID", "1"), _bin("Name", "N_L13"),
                                  _bin("Filename", "data/levels/islands/normal/n_l13.isd")),
                  _bin("ResidentLevels", None, _bin("m_ResidentCountPerLevelEcos1", data=struct.pack("<8i", 12, *[0] * 7))))  # fmt: skip
    world = _bin("WorldConfig", None, _bin("Width", "1792"), _bin("Height", "1792"),
                 _bin("Islands", None, _bin("k", "1"), island))  # fmt: skip
    engine = _bin("m_GameEngine", None, _bin("m_CurPlayerID", "0"), _bin("m_Players", None, player))
    save = bintree.Tree([_bin("SaveGame", None, world, engine)])
    stats = bintree.Tree([_bin("GameStats", None, _bin("GoldAmount", str(credits)), _bin("SyncCheckSum", "649"))])
    parts = {"game2.xml": game, "save.sww": save, "stats.xml": stats}
    raw = {n: savegame2070.pack(bintree.write(t)) for n, t in parts.items()}
    raw["preview.jpg"] = b"\xff\xd8\xff\xe0" + bytes(32)
    data = bytearray(rda.write_v20(raw, 0, flags=0))
    data[520:544] = struct.pack("<I", 20) + bytes(range(20))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return path
