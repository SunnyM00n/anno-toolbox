"""Stand-ins for the island editor: an edit document, the editor and window stand-ins, a placed-objects game, a 1404
install and the region conversion's tables."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from tests.fakes import FakeFiles
from tests.formats.isdfakes import isd2070_bytes

from annotoolbox.editor import archivedelivery, editorapp, footprints, islandeditor
from annotoolbox.editor.desktop import workbench
from annotoolbox.formats import filedb, rda
from annotoolbox.formats.filedb import SAttrib
from annotoolbox.formats.filedb import STag as STag_filedb
from annotoolbox.formats.rda import write_v22
from annotoolbox.islands import islandedits
from annotoolbox.suite.settings import CSettings

EDIT_SIZE, EDIT_SPT = 16, 2  # the island of ``edit_island``: tiles a side, height samples a tile


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


class FakeEditor:
    """An opened island as the app hands it to the window: the synthetic one of test_editsession."""

    m_edits_path = None
    m_notes = ["a note from the reader"]

    def island_json(self):
        import json

        return json.dumps(self.island_data())

    def island_data(self):

        return edit_island()

    def edits(self):
        return {}

    def materials(self):
        return [{"id": 0, "name": "a2070_n_gras_01", "diffuse": "data/anno2070/t/gras.psd", "order": 0}]

    def vanilla_materials(self):
        return [{"file": "data/base/m/grass.cfg", "set": "data/base/s.xml", "name": "grass", "diffuse": "", "rank": 3}]

    def library(self):
        return {"117": [], "2070": [], "editor": []}

    def texture_png(self, *a, **k):
        raise FileNotFoundError("no textures here")


def desktop_window(tmp_path):
    from PySide6.QtWidgets import QApplication

    qt = QApplication.instance() or QApplication([])
    settings = CSettings(anno117=str(tmp_path), anno2070=str(tmp_path), mods=str(tmp_path / "mods"))
    path = tmp_path / "settings.json"
    settings.save(path)
    return qt, workbench.CMainWindow(editorapp.CApp(path))


class FakeOpened:
    """What ``CApp.open_path`` hands to ``IslandEditor``."""

    calls: list = []

    def __init__(self, files, a7m_path, cache, mod_assets="", edits=None, tree2070=None):
        self.m_edit_meta = {}
        FakeOpened.calls.append((a7m_path, files.m_mod_root, mod_assets, edits, tree2070))


ISLE = "data/levels/islands/normal/isle.isd"


VENICE = "addondata/levels/islands/north/venice.isd"


LOG = [].append


def save_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def install_1404(root: Path) -> Path:
    save_bytes(root / "Anno1404.exe", b"")
    save_bytes(root / "maindata/data0.rda", rda.write_v20({ISLE: isd2070_bytes(), "data/a.txt": b"a"}, 100))
    save_bytes(root / "maindata/data1.rda", rda.write_v20({"data/b.txt": b"b"}, 100))
    save_bytes(root / "maindata/data2.rda", rda.write_v20({"data/system/shader.fx": b"fx"}, 100))
    save_bytes(root / "addon/data0.rda", rda.write_v20({VENICE: isd2070_bytes()}, 100))
    save_bytes(root / "addon/data1.rda", rda.write_v20({"addondata/c.txt": b"c"}, 100))
    return root


def built_old_engine(key: str, game: Path, edits_dir: Path) -> tuple[dict[str, bytes], list[str], CSettings]:
    """The archives the editor's build makes for a game (``SIslandDelivery``: edited, then package), what it wrote
    and the settings (their workspace holds the backups)."""
    settings = CSettings(**{key: str(game)}, workspace=str(edits_dir.parent / "ws"))
    d = archivedelivery.ISLAND_DELIVERIES[key]
    tree = d.tree(settings)
    files, written = d.edited(tree, edits_dir, LOG)
    return d.package(files, written, settings, LOG, tree), written, settings


def edits(edits_dir: Path, prefix: str, source: str, **more) -> None:
    edits_dir.mkdir(exist_ok=True)
    name = f"{prefix}{Path(source).stem}.json"
    (edits_dir / name).write_text(json.dumps({"source": source, **more}), encoding="utf-8")


def entries(path: Path) -> dict[str, bytes]:
    arc = rda.CRdaArchive(path)
    return {e.path: arc.read_entry(e) for e in arc}


KONTOR = "data/base/graphics/roman/buildings/harbor/kontor_01/kontor_01.cfg"


BLOCKER = "data/base/graphics/roman/environment/blockers/blocker_01.cfg"


PLACED_ASSETS = f"""<Assets>
<Asset><Template>Slot</Template><Values><Standard><GUID>2882</GUID><Name>Slot Mountain</Name></Standard>
<Object><Variations><Item><Filename>{BLOCKER}</Filename></Item><Item><Filename>data/b/second.cfg</Filename></Item>
</Variations></Object></Values></Asset>
<Asset><BaseAssetGUID>2882</BaseAssetGUID><Values><Standard><GUID>37926</GUID><Name>Fixed Slot</Name></Standard>
</Values></Asset>
<Asset><Template>HarborWarehouse</Template><Values><Standard><GUID>3402</GUID><Name>Harbor Warehouse</Name></Standard>
<Object><Type>GameObject</Type><Variations><Item><Filename>{KONTOR}</Filename></Item></Variations></Object>
</Values></Asset>
<Asset><Template>Marker</Template><Values><Standard><GUID>42256</GUID><Name>Camera Marker</Name></Standard>
<Object><Variations><Item><Filename>data/base/graphics/ui/placeholder/constraint_dummy.cfg</Filename></Item>
</Variations></Object></Values></Asset>
<Asset><Template>Quest</Template><Values><Standard><GUID>55771</GUID><Name>No Model Thing</Name></Standard></Values>
</Asset>
<Asset><Template>Lamp</Template><Values><Standard><GUID>70001</GUID><Name>Lamp</Name></Standard></Values></Asset>
</Assets>""".encode()


PLACED_TEMPLATES = (
    "<Templates><Template><Name>Lamp</Name><Properties><Object><Variations>"
    "<Item><Filename>data/b/lamp_default.cfg</Filename></Item></Variations></Object></Properties></Template></Templates>"
)


CONFIG = "<Config><ConfigType>MAIN</ConfigType><Models><Config><ConfigType>MODEL</ConfigType><FileName>data/x/a.rdm</FileName></Config></Models></Config>"


EMPTY_CONFIG = "<Config><ConfigType>MAIN</ConfigType></Config>"


PLACED_IFO = """<Info><BuildBlocker><Position><xf>7</xf><zf>-3.5</zf></Position><Position><xf>-1</xf><zf>-3.5</zf></Position>
<Position><xf>-1</xf><zf>3.5</zf></Position><Position><xf>7</xf><zf>3.5</zf></Position></BuildBlocker></Info>"""


class FakeSignal:
    """A Qt signal that records what it was sent."""

    def __init__(self):
        self.sent = []

    def emit(self, value):
        self.sent.append(value)


class FakeGame(FakeFiles):
    pass


def _editor(objects: list[STag_filedb]):
    game = FakeGame(
        {
            "data/base/config/export/assets.xml": PLACED_ASSETS,
            "data/base/config/export/templates.xml": PLACED_TEMPLATES.encode(),
            KONTOR: CONFIG.encode(),
            BLOCKER: EMPTY_CONFIG.encode(),
            "data/b/second.cfg": CONFIG.encode(),
            footprints.info_path(KONTOR): PLACED_IFO.encode(),
        }
    )
    files = SimpleNamespace(m_game=game, read=game.read, where=lambda p: "game" if game.exists(p) else None)
    area = STag_filedb("doc", [STag_filedb("GameObject", [STag_filedb("objects", objects)])])
    gm = STag_filedb(
        "GameSessionManager",
        [STag_filedb("AreaManagerData", [STag_filedb("#1", [SAttrib("Data", filedb.write(area))])])],
    )
    ed = islandeditor.CIslandEditor.__new__(islandeditor.CIslandEditor)
    ed.m_mod_assets, ed._m_editor, ed._m_assets, ed._m_problems, ed.m_notes = "", ([], {}), None, {}, []
    ed.m_files, ed.m_view = files, SimpleNamespace(game_session_manager=gm)
    return ed


def placed_game_object(serial: int, guid: int, pos, variation: int | None = None, yaw: float = 0.0) -> STag_filedb:
    kids = [SAttrib("guid", struct.pack("<I", guid)), SAttrib("ID", struct.pack("<I", serial) + b"\x01\x00\x00\x00")]
    if variation is not None:
        kids.append(SAttrib("Variation", struct.pack("<I", variation)))
    kids += [
        SAttrib("Position", struct.pack("<3f", *pos)),
        SAttrib("Direction", struct.pack("<f", yaw)),
        STag_filedb("Mesh", [SAttrib("Orientation", struct.pack("<4f", 0, 0, 0, 1))]),
    ]
    return STag_filedb("#1", kids)


def opened_island():
    objs = [
        placed_game_object(1, 3402, (8.0, 0.5, 8.5)),
        placed_game_object(2, 2882, (3.0, 0.5, 3.0), variation=1),
        placed_game_object(3, 37926, (12.0, 0.5, 3.0)),
        placed_game_object(4, 42256, (3.0, 0.5, 12.0)),
        placed_game_object(5, 55771, (4.0, 0.5, 12.0)),
        placed_game_object(6, 123, (5.0, 0.5, 12.0)),
    ]
    return _editor(objs)


def opened_tags() -> list[STag_filedb]:
    return [
        placed_game_object(1, 3402, (8.0, 0.5, 8.5)),
        placed_game_object(2, 2882, (3.0, 0.5, 3.0), variation=1),
        placed_game_object(3, 37926, (12.0, 0.5, 3.0)),
    ]


def synthetic_file(ed, tags: list[STag_filedb] | None = None) -> tuple[bytes, bytes]:
    area = STag_filedb(
        "doc", [STag_filedb("GameObject", [STag_filedb("objects", tags if tags is not None else opened_tags())])]
    )
    gm = STag_filedb(
        "GameSessionManager",
        [
            STag_filedb(
                "WorldManager",
                [
                    STag_filedb(
                        "Water",
                        [
                            SAttrib("x", struct.pack("<I", 4)),
                            SAttrib("y", struct.pack("<I", 4)),
                            SAttrib("bits", b"\xff" * 16),
                        ],
                    )
                ],
            ),
            STag_filedb("AreaManagerData", [STag_filedb("#1", [SAttrib("Data", filedb.write(area))])]),
        ],
    )
    a7m = write_v22(
        {
            "gamedata.data": filedb.write(STag_filedb("root", [gm])),
            "rd3d.data": filedb.write(STag_filedb("root", [STag_filedb("PropGrid", [])])),
        }
    )
    return a7m, filedb.write(STag_filedb("root", [STag_filedb("Info", [])]))


def region_set(*entries: tuple[int, str, str, str]) -> str:
    body = "".join(
        f"<Material><ID>{i}</>\n<ImportLabel>{label}</>\n<FileName>{file}</>\n<GroundType>{ground}</>\n</>\n"
        for i, label, ground, file in entries
    )
    return f"<MaterialSet><ShaderType>0</>\n{body}</>\n"


ROMAN = region_set(
    (7, "sand_base", "None", "data/m/sand/beach_04/beach_04.xml"),
    (1, "grass", "Grass", "data/m/grass/grass_01_e/grass_01_e.xml"),
    (14, "rock_large", "Rock", "data/m/rock/cliff_limestone_01/cliff_limestone_01.xml"),
)


CELTIC = region_set(
    (21, "", "None", "data/m/sand/beach_01/beach_01.xml"),
    (3, "Grass", "Grass", "data/m/grass/grass_01/grass_01.xml"),
    (9, "Rock3", "", "data/m/rock/celtic_cliffs_06/celtic_cliffs_06.xml"),
    (4, "Grass_variance", "GrassDry", "data/m/grass/grass_01_b_light/grass_01_b_light.xml"),
)


def ids_word(*mids: int) -> int:
    return sum(m << (5 * s) for s, m in enumerate(mids))
