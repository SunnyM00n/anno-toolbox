"""Synthetic extracted-2070 tree used by the tests."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from tests import hangguard, memorygate, qtcleanup
from tests.ddsfiles import make_bc7_dds
from tests.models.modelfakes import BAKERY_CFG, TREE_CFG

from annotoolbox.models import textures
from annotoolbox.sources import gametree
from annotoolbox.sources.overlay import CSourceTree
from annotoolbox.uiedit import layout as layout_uiedit
from annotoolbox.uiedit import uifiles

from tests.models.feedbackfakes import BUILDING_CFG, DUMMIES, PLAY, UNIT_CFG, WALK, fc2070, item
from tests.uiedit.uieditfakes import (
    GEN,
    IMAGE,
    INNER,
    LIST,
    OUTER,
    WIDGET,
    composed_scene_file,
    image_file,
    inner_file,
    list_file,
    outer_file,
    scene_file,
    settle_window,
    widget_file,
)

# numpy's and scipy's own OpenBLAS each commit about 750 MB at import, one buffer per core; in every xdist worker that
# ran the machine out of commit (Errno 22, dead workers): one BLAS thread a test process (CONTRIBUTING "Parallel runs")
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_var, "1")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """The windows the tests left open are deleted before the interpreter's shutdown, which aborts on them."""
    qtcleanup.release_windows()


@pytest.fixture(autouse=True)
def _memory_gate():
    """A test waits for free commit when the machine has run short of it (tests/memorygate.py)."""
    memorygate.wait()


@pytest.fixture(autouse=True)
def _hang_guard(request):
    """A test that hangs ends the run with the stacks of every thread (tests/hangguard.py) instead of blocking it."""
    capture = request.config.pluginmanager.getplugin("capturemanager")
    disarm = hangguard.arm(
        request.node.nodeid, hangguard.seconds(), capture.suspend_global_capture if capture else None
    )
    yield
    disarm()


@pytest.fixture(autouse=True)
def _views_on_opengl(monkeypatch):
    """The windows a test opens draw with moderngl, as on a machine without Direct3D 12 (the app's default is
    Direct3D 12 where there is one): a test that wants a QRhi device names it."""
    import os

    if not os.environ.get("ANNO_RENDERER"):
        monkeypatch.setenv("ANNO_RENDERER", "gl")


@pytest.fixture(autouse=True)
def _no_real_game_link(monkeypatch):
    """A test never finds the session file of a game that really runs (under Documents) or its pipe: the link looks
    only in a folder the test names."""
    from annotoolbox.suite import linkprotocol

    read = linkprotocol.read_session
    monkeypatch.setattr(
        linkprotocol,
        "read_session",
        lambda user_dir=None, game=linkprotocol.DEFAULT_GAME: read(user_dir, game) if user_dir else None,
    )


@pytest.fixture(autouse=True)
def _no_repo_edits(monkeypatch):
    """No test writes an edit file into the checkout's own island-edits/ folder."""
    from annotoolbox.islands import islandedits
    from annotoolbox.paths import REPO_ROOT

    guarded, save = (REPO_ROOT / "island-edits").resolve(), islandedits.save

    def guarded_save(path, edits):
        assert guarded not in Path(path).resolve().parents, f"a test would write {path} into the repo's island-edits/"
        return save(path, edits)

    monkeypatch.setattr(islandedits, "save", guarded_save)


@pytest.fixture(autouse=True)
def _no_game_search(monkeypatch):
    """A suite window in a test does not look for a running game (the live link's retry thread)."""
    monkeypatch.setenv("ANNO_LIVE_LINK", "0")


@pytest.fixture(autouse=True)
def _no_process_list(monkeypatch):
    """No test runs ``tasklist`` (half a second each, a Mods window asks every 5 s): the mod manager's process lister
    sees no game unless the test hands it a lister of its own."""
    from annotoolbox.modmgr import gameguard

    monkeypatch.setattr(gameguard, "system_lister", set)


@pytest.fixture(autouse=True)
def _private_build_cache(tmp_path_factory, monkeypatch):
    """A test never reads or fills the user's build cache (buildcache.py), and starts with an empty one."""
    monkeypatch.setenv("ANNO2070_CACHE", str(tmp_path_factory.mktemp("build-cache")))
    monkeypatch.delenv("ANNO2070_NO_CACHE", raising=False)


@pytest.fixture(autouse=True)
def _private_user_folders(tmp_path_factory, monkeypatch):
    """On Linux the tool's data, settings and cache folders are the XDG ones (paths.py): a test never reads or writes
    the user's own. (Windows tests that need the folders set ``LOCALAPPDATA`` themselves.)"""
    for variable in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
        monkeypatch.setenv(variable, str(tmp_path_factory.mktemp(variable.lower())))


@pytest.fixture(autouse=True)
def _no_gpu_encoder(monkeypatch):
    """No test probes this machine's graphics card: BC7 is the CPU encoder's unless a test hands ``bc7gpu`` one."""
    monkeypatch.setenv("ANNO_BC7_GPU", "none")


@pytest.fixture(autouse=True)
def _private_game_backups(tmp_path_factory, monkeypatch):
    """The copies of a game's own files an install keeps (othergames/gamebackups.py) go to a folder of the test's."""
    monkeypatch.setenv("ANNO_GAME_BACKUPS", str(tmp_path_factory.mktemp("game-backups")))


@pytest.fixture(autouse=True)
def _no_language_override(tmp_path_factory, monkeypatch):
    """The user's own language files (``i18n.override_dir``) are not read: a test sees the shipped languages only."""
    monkeypatch.setenv("ANNO_TOOLBOX_LANG_DIR", str(tmp_path_factory.mktemp("lang-override")))


@pytest.fixture(autouse=True)
def _no_game_detection(monkeypatch):
    """The editor's settings look for installed games on the machine (registry, Steam, the drives) when a game
    folder is unset: a test finds none, whatever this computer has."""
    from annotoolbox.suite import settings

    monkeypatch.setattr(settings, "candidates", list)


@pytest.fixture(autouse=True)
def _english_interface(monkeypatch):
    """Every test starts and ends in English: the language is process-wide state, and loading settings selects it."""
    from annotoolbox.infra import i18n

    monkeypatch.delenv(i18n.ENV_VAR, raising=False)
    i18n.use("en")
    yield
    monkeypatch.undo()  # a test's patched os.name would make the override folder a PosixPath on Windows
    i18n.use("en")


@pytest.fixture
def bc7_dds():
    return make_bc7_dds


@pytest.fixture
def fake_bc7(monkeypatch):
    """``textures.png_to_bc7`` without texconv: a BC7 header for the PNG's size with the format and mip count asked
    for, the calls recorded as ``(is_srgb, mip_count)``."""
    from annotoolbox.models import textures

    calls: list[tuple[bool, int]] = []

    def encode(
        src: Path,
        dst: Path,
        is_srgb: bool = False,
        use_cache: bool = True,
        mip_count: int = 0,
        is_srgb_input: bool = False,
    ) -> Path:
        from PIL import Image

        with Image.open(src) as img:
            width, height = img.size
        calls.append((is_srgb, mip_count))
        assert is_srgb_input == is_srgb  # the PNG holds the stored values: no second gamma step
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(make_bc7_dds(width, height, mip_count or max(width, height).bit_length(), is_srgb))
        return dst

    def encode_pixels(px, is_srgb: bool = False, is_srgb_input: bool = False, mip_count: int = 0) -> bytes:
        height, width = px.shape[:2]
        calls.append((is_srgb, mip_count))
        assert is_srgb_input == is_srgb  # the pixels hold the stored values: no second gamma step
        return make_bc7_dds(width, height, mip_count or max(width, height).bit_length(), is_srgb)

    monkeypatch.setattr(textures, "png_to_bc7", encode)
    monkeypatch.setattr(textures, "rgba_to_bc7", encode_pixels)
    monkeypatch.setattr(
        textures,
        "_encode",
        lambda px, is_srgb, is_srgb_input, mip_count: (encode_pixels(px, is_srgb, is_srgb_input, mip_count), True),
    )
    return calls


ASSETS_XML = """<AssetList>
  <ToolOneVersion>8</ToolOneVersion>
  <Groups>
    <Group>
      <Name>Buildings</Name>
      <Groups>
        <Group>
          <Name>Production</Name>
          <Assets>
            <Asset>
              <Template>FactoryBuilding</Template>
              <Values>
                <Object>
                  <Variations>
                    <Item>
                      <Filename>data\\graphics\\buildings\\bakery\\bakery.cfg</Filename>
                    </Item>
                  </Variations>
                </Object>
                <Standard>
                  <Name>bakery</Name>
                  <GUID>10001</GUID>
                </Standard>
                <Hitpoints>
                  <MaxHitpoints>450</MaxHitpoints>
                </Hitpoints>
                <WareProduction>
                  <Product>Bread</Product>
                </WareProduction>
              </Values>
            </Asset>
            <Asset>
              <Template>FactoryBuilding</Template>
              <Values>
                <Object>
                  <Variations>
                    <Item><Filename>data\\graphics\\buildings\\a\\a.cfg</Filename></Item>
                    <Item><Filename>data\\graphics\\buildings\\b\\b.cfg</Filename></Item>
                  </Variations>
                </Object>
                <Standard>
                  <Name>two variations</Name>
                  <GUID>10002</GUID>
                </Standard>
              </Values>
            </Asset>
            <Asset>
              <BaseAssetGUID>10001</BaseAssetGUID>
              <Values>
                <Object />
                <Standard>
                  <Name>bakery_variant</Name>
                  <GUID>10003</GUID>
                </Standard>
                <WareProduction>
                  <Product>Cake</Product>
                </WareProduction>
              </Values>
            </Asset>
          </Assets>
        </Group>
      </Groups>
    </Group>
  </Groups>
</AssetList>
"""

ADDON_ASSETS_XML = """<Group>
  <ToolOneVersion>8</ToolOneVersion>
  <Groups>
    <Group>
      <Name>Addon</Name>
      <Assets>
        <Asset>
          <Template>OrnamentBuilding</Template>
          <Values>
            <Standard><Name>ornament</Name><GUID>20001</GUID></Standard>
          </Values>
        </Asset>
      </Assets>
    </Group>
  </Groups>
</Group>
"""

TEMPLATES_XML = """<Templates>
  <ToolOneVersion>8</ToolOneVersion>
  <Group>
    <Name>Objects</Name>
    <Template>
      <Name>FactoryBuilding</Name>
      <Properties>
        <Building />
        <Hitpoints><MaxHitpoints>300</MaxHitpoints><StartFireValue>200</StartFireValue></Hitpoints>
        <WareProduction />
      </Properties>
    </Template>
    <Group>
      <Name>Nested</Name>
      <Template>
        <Name>OrnamentBuilding</Name>
        <Properties><Building /></Properties>
      </Template>
    </Group>
  </Group>
</Templates>
"""

PROPERTIES_XML = """<Properties>
  <ToolOneVersion>8</ToolOneVersion>
  <Groups>
    <Group>
      <Name>Building</Name>
      <Property>
        <Name>WareProduction</Name>
        <HasImplementation>1</HasImplementation>
        <ValueDefinition>
          <DataType>Choice</DataType>
          <Name>Product</Name>
          <DataSet>Product</DataSet>
        </ValueDefinition>
      </Property>
    </Group>
  </Groups>
</Properties>
"""


def _write(base: Path, rel: str, content: str | bytes) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    root = tmp_path / "extracted"
    # Base data: old assets.xml that patch1 overrides, plus graphics.
    _write(root, "Data1/data/config/game/assets.xml", "<AssetList><Groups/></AssetList>")
    _write(root, "Data1/data/config/game/templates.xml", TEMPLATES_XML)
    _write(root, "Data1/data/config/game/properties.xml", PROPERTIES_XML)
    _write(root, "Data2/data/graphics/buildings/bakery/bakery.cfg", BAKERY_CFG)
    _write(root, "Data2/data/graphics/buildings/bakery/bakery.ifo", "<Info/>")
    _write(root, "Data2/data/graphics/buildings/bakery/rdm/bakery_lod0.rdm", b"RDM\x01")
    _write(root, "Data2/data/graphics/buildings/bakery/maps/bakery_diff_0.dds", b"DDS ")
    _write(root, "Data2/data/graphics/buildings/bakery/maps/bakery_norm_0.dds", b"DDS ")
    _write(root, "Data2/data/graphics/buildings/bakery/maps/bakery_ground_0.dds", b"DDS ")
    _write(root, "Data2/data/graphics/effects/particles/fire_01.efc", "<x/>")
    # Nested prop config (with a cycle back to the bakery) and its mesh.
    _write(root, "Data2/data/graphics/props/tree/tree.cfg", TREE_CFG)
    _write(root, "Data2/data/graphics/props/tree/rdm/tree.rdm", b"RDM\x01")
    _write(root, "Data2/data/graphics/marker.txt", "data2")
    # Patch overrides assets.xml and the marker.
    _write(root, "patch1/data/config/game/assets.xml", ASSETS_XML)
    _write(root, "patch1/data/graphics/marker.txt", "patch1")
    # Addon assets.
    _write(root, "addon0/addondata/config/balancing/addon_01_assets.xml", ADDON_ASSETS_XML)
    # Language packs: eng has texts, ger is another language and must be ignored.
    eng = "# comment\n\n10001=Bakery\n10002=Two variations\n20001=Ornament\n"
    _write(root, "eng0/data/loca/eng/txt/guids.txt", b"\xff\xfe" + eng.encode("utf-16-le"))
    # Same path in a later pack replaces the whole file, so it must carry every entry.
    _write(
        root, "eng1/data/loca/eng/txt/guids.txt", b"\xff\xfe" + "10001=Bakery v2\n20001=Ornament\n".encode("utf-16-le")
    )
    _write(root, "ger0/data/loca/ger/txt/guids.txt", b"\xff\xfe" + "10001=Baeckerei\n".encode("utf-16-le"))
    # A folder that is not an archive.
    _write(root, "notes/readme.txt", "ignored")
    return root


@pytest.fixture
def plenty_of_memory(monkeypatch):
    """The scheduler's memory gate sees 16 TB free, so what it admits does not depend on the machine running the test."""
    from annotoolbox.infra import tools

    monkeypatch.setattr(tools, "free_memory_gb", lambda: 16384.0)


OPTIONAL_PACKAGES = ("PySide6", "moderngl", "numpy", "scipy", "PIL", "miniaudio", "etcpak", "lupa", "texture2ddecoder")


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(collector):
    """A test file that cannot be imported because an optional package (the GUI libraries, numpy) is not installed
    is skipped with that reason, so the light install runs the tests it can."""
    report = yield
    if report.failed and isinstance(collector, pytest.Module):
        text = str(report.longrepr)
        missing = next((p for p in OPTIONAL_PACKAGES if f"No module named '{p}" in text), None)
        if missing:
            report.outcome = "skipped"
            report.longrepr = (str(collector.path), 0, f"Skipped: needs {missing} (an optional install)")
    return report


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """A test that needs an optional package (imported where the code uses it) that is not installed is skipped with
    that reason, as a test file that cannot import one is."""
    report = yield
    error = call.excinfo.value if call.excinfo is not None else None
    if isinstance(error, ModuleNotFoundError) and (error.name or "").split(".")[0] in OPTIONAL_PACKAGES:
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] or 0, f"Skipped: needs {error.name} (an optional install)")
    return report


@pytest.fixture(scope="module")
def qt():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def fake_textures(monkeypatch):
    def write(src, dst, is_srgb=False):
        assert not is_srgb  # BC7_UNORM, as 117's own particle maps (tests/models/test_texture_formats.py)
        dst.parent.mkdir(parents=True, exist_ok=True)  # a model map's folder is not made by its caller
        dst.write_bytes(b"DDS ")

    monkeypatch.setattr(textures, "convert_dds", write)


@pytest.fixture
def sound_window(monkeypatch):
    """The Sound window offscreen with nothing open (a test sets its ``doc`` or opens a bank); deleted afterwards. The other
    sound tests import it (with ``bank_window``): one setup for every test of the window."""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.soundedit.soundwindow import CSoundWindow
    from annotoolbox.suite.settings import CSettings

    QApplication.instance() or QApplication([])
    window = CSoundWindow(CSettings())
    window.m_is_synchronous = True  # the reads on the test's thread; the off-thread test turns it off
    yield window
    window.deleteLater()


@pytest.fixture
def ui_tree(tmp_path: Path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    (gen / WIDGET).write_bytes(widget_file())
    (gen / IMAGE).write_bytes(image_file())
    (gen / "testscene").write_bytes(scene_file())
    texts = tmp_path / "game" / "data/base/config/gui/texts_english.xml"
    texts.parent.mkdir(parents=True)
    line = int("A010D06813984305", 16) - (1 << 64)
    texts.write_text(
        f"<TextExport><Texts><Text><LineId>{line}</LineId><Text>Tom &amp; Jerry</Text></Text></Texts></TextExport>"
    )
    pwl = tmp_path / "game" / "data/ui/pwl/components/pivot_c7b1e237.json"
    pwl.parent.mkdir(parents=True)
    pwl.write_text(json.dumps({"PhoenixAssetData": {"Object": {
        "ObjectID": "C7B1E237-0000-0000-0000-000000000000", "Name": "ProportionalPivotPoint", "FolderPath": "Layouts",
        "Description": "The pivot as a <Bold>fraction</Bold>.", "CustomProperties": [{"Object": {
            "UniqueId": layout_uiedit.PPP_VALUE, "PropertyName": "ProportionalPivotPoint", "PropertyType": "CustomPropertyType_Vector3",
            "Description": "x and y fractions"}}]}}}))  # fmt: skip
    return gametree.loose_tree(tmp_path / "game")


@pytest.fixture
def composed(tmp_path):
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    for key, data in ((INNER, inner_file()), (OUTER, outer_file()), (LIST, list_file()), (IMAGE, image_file()),
                      ("composed", composed_scene_file())):  # fmt: skip
        (gen / key).write_bytes(data)
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))


@pytest.fixture
def scene_files(request):
    """A scene composed of widget files (the fixture ``composed`` of ``test_uiedit_compose``)."""
    return request.getfixturevalue("composed")


@pytest.fixture
def window(scene_files, tmp_path):
    """The UI editor's window on the composed scene, its first layout done."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.suite.settings import CSettings
    from annotoolbox.uiedit.uiwindow import CUiWindow, _CGame

    qt = QApplication.instance() or QApplication([])
    w = CUiWindow(CSettings(workspace=str(tmp_path / "ws"), project=str(tmp_path / "proj")))
    w._opened("anno117", "", _CGame("anno117", scene_files.m_tree, None))
    w.qt = qt
    yield w
    settle_window(w)
    for doc in w.m_docs.values():
        doc.m_undo.mark_clean()  # closing asks about unsaved edits otherwise
    w.close()
    qt.processEvents()
