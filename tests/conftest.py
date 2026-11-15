"""Synthetic extracted-2070 tree used by the tests."""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

import pytest
from tests import hangguard, memorygate, qtcleanup

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
    monkeypatch.setattr(linkprotocol, "read_session", lambda user_dir=None: read(user_dir) if user_dir else None)


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
def _private_game_backups(tmp_path_factory, monkeypatch):
    """The copies of a game's own files an install keeps (othergames/gamebackups.py) go to a folder of the test's."""
    monkeypatch.setenv("ANNO_GAME_BACKUPS", str(tmp_path_factory.mktemp("game-backups")))


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
    i18n.use("en")


def make_dxt1_dds(width: int = 4, height: int = 4) -> bytes:
    """A legacy DXT1 file (one red block repeated), the kind 2070 ships."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, 8, 0, 1) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DXT1", 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return head + struct.pack("<HHI", 0xF800, 0, 0) * (-(-width // 4) * -(-height // 4))


def make_bc7_dds(width: int, height: int, mips: int = 1, srgb: bool = False) -> bytes:
    """A DX10 BC7 header (UNORM, or UNORM_SRGB) with a stand-in payload: the layout of 117's UI textures."""
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000 | (0x20000 if mips > 1 else 0)
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, flags, height, width, 0, 0, mips) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    head += struct.pack("<5I", 0x1000 | (0x400008 if mips > 1 else 0), 0, 0, 0, 0)
    head += struct.pack("<5I", 99 if srgb else 98, 3, 0, 1, 0)
    return head + bytes(16)


@pytest.fixture
def bc7_dds():
    return make_bc7_dds


@pytest.fixture
def fake_bc7(monkeypatch):
    """``textures.png_to_bc7`` without texconv: a BC7 header for the PNG's size with the format and mips asked
    for, the calls recorded as ``(srgb, mips)``."""
    from annotoolbox.models import textures

    calls: list[tuple[bool, int]] = []

    def encode(
        src: Path, dst: Path, srgb: bool = False, cache: bool = True, mips: int = 0, srgb_in: bool = False
    ) -> Path:
        from PIL import Image

        with Image.open(src) as img:
            width, height = img.size
        calls.append((srgb, mips))
        assert srgb_in == srgb  # the PNG holds the stored values: no second gamma step
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(make_bc7_dds(width, height, mips or max(width, height).bit_length(), srgb))
        return dst

    def encode_pixels(px, srgb: bool = False, srgb_in: bool = False, mips: int = 0) -> bytes:
        height, width = px.shape[:2]
        calls.append((srgb, mips))
        assert srgb_in == srgb  # the pixels hold the stored values: no second gamma step
        return make_bc7_dds(width, height, mips or max(width, height).bit_length(), srgb)

    monkeypatch.setattr(textures, "png_to_bc7", encode)
    monkeypatch.setattr(textures, "rgba_to_bc7", encode_pixels)
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

BAKERY_CFG = """<m_Config><m_Files><m_Config><m_FileName>data\\graphics\\props\\tree\\tree.cfg</m_FileName>
<m_ConfigType>FILE</m_ConfigType>
<m_Transformer><m_Config><m_Position.x>5.000000</m_Position.x>
<m_Position.y>0.000000</m_Position.y>
<m_Position.z>0.000000</m_Position.z>
<m_Scale>0.700000</m_Scale>
<m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType>
</m_Config>
</m_Transformer>
</m_Config>
</m_Files>
<m_Models><m_Config><m_FileName>data\\graphics\\buildings\\bakery\\rdm\\bakery_lod0.rdm</m_FileName>
<m_ConfigType>MODEL</m_ConfigType>
<m_Animations><m_Config><m_FileName>data\\graphics\\buildings\\bakery\\anim\\bakery_work.rdm</m_FileName>
<m_ConfigType>ANIMATION</m_ConfigType>
</m_Config>
</m_Animations>
<m_Materials><m_Config><m_Name>xx</m_Name>
<m_ShaderIndex>10</m_ShaderIndex>
<m_DiffuseColor.r>1.000000</m_DiffuseColor.r>
<m_DiffuseColor.g>0.500000</m_DiffuseColor.g>
<m_DiffuseColor.b>0.250000</m_DiffuseColor.b>
<m_DiffuseColor.a>1.000000</m_DiffuseColor.a>
<m_DiffuseTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_diff.png</m_DiffuseTexture>
<m_NormalTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_norm.png</m_NormalTexture>
<m_EnvironmentTexture></m_EnvironmentTexture>
<m_HeightTexture></m_HeightTexture>
<m_MaskTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_mask.png</m_MaskTexture>
<m_RipplesTexture></m_RipplesTexture>
<m_VertexFormat>P4h_N4b_G4b_B4b_T2h</m_VertexFormat>
<m_DiffuseEnabled>1</m_DiffuseEnabled>
<m_NormalEnabled>1</m_NormalEnabled>
<m_EnvironmentEnabled>0</m_EnvironmentEnabled>
<m_ParallaxEnabled>0</m_ParallaxEnabled>
<m_RipplesEnabled>0</m_RipplesEnabled>
<m_NumBonesPerVertex>0</m_NumBonesPerVertex>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>
</m_Materials>
<m_Transformer><m_Config><m_Position.x>1.000000</m_Position.x>
<m_Position.y>2.000000</m_Position.y>
<m_Position.z>3.000000</m_Position.z>
<m_Rotation.x>0.000000</m_Rotation.x>
<m_Rotation.y>0.000000</m_Rotation.y>
<m_Rotation.z>0.000000</m_Rotation.z>
<m_Rotation.w>1.000000</m_Rotation.w>
<m_Scale>1.000000</m_Scale>
<m_Conditions>0</m_Conditions>
<m_ConfigType>ORIENTATION_TRANSFORM</m_ConfigType>
</m_Config>
</m_Transformer>
</m_Config>
</m_Models>
<m_Effects><m_Config><m_FileName>data\\graphics\\effects\\particles\\fire_01.efc</m_FileName>
<m_ConfigType>EFFECT</m_ConfigType>
</m_Config>
</m_Effects>
<m_TerrainDecals><m_Config><m_Extend.x>2.000000</m_Extend.x>
<m_Extend.y>1.500000</m_Extend.y>
<m_TexCoords.x>0.000000</m_TexCoords.x>
<m_TexCoords.y>0.250000</m_TexCoords.y>
<m_TexCoords.z>1.000000</m_TexCoords.z>
<m_TexCoords.w>1.000000</m_TexCoords.w>
<m_ConfigType>TERRAINDECAL</m_ConfigType>
<m_Materials><m_Config><m_Name></m_Name>
<m_ShaderIndex>0</m_ShaderIndex>
<m_DiffuseTexture>data\\graphics\\buildings\\bakery\\maps\\bakery_ground.png</m_DiffuseTexture>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>
</m_Materials>
</m_Config>
</m_TerrainDecals>
<m_Sequences><m_Config><m_SequenceID>5000</m_SequenceID>
<m_ConfigType>SEQUENCE</m_ConfigType>
</m_Config>
</m_Sequences>
<m_RenderPropertyFlags>131712</m_RenderPropertyFlags>
<m_Center.x>0.000000</m_Center.x>
<m_Center.y>3.355972</m_Center.y>
<m_Center.z>-0.001022</m_Center.z>
<m_Extent.x>2.000000</m_Extent.x>
<m_Extent.y>3.384575</m_Extent.y>
<m_Extent.z>1.572772</m_Extent.z>
<m_Radius>4.234260</m_Radius>
<m_ConfigType>ROOT</m_ConfigType>
</m_Config>"""

TREE_CFG = """<m_Config><m_Models><m_Config><m_FileName>data\\graphics\\props\\tree\\rdm\\tree.rdm</m_FileName>
<m_ConfigType>MODEL</m_ConfigType>
<m_Materials><m_Config><m_Name>leaves</m_Name>
<m_ShaderIndex>4</m_ShaderIndex>
<m_DiffuseTexture>tools\\bobnet\\data\\default_diff.png</m_DiffuseTexture>
<m_VertexFormat>P4h_N4b_T2h</m_VertexFormat>
<m_ConfigType>MATERIAL</m_ConfigType>
</m_Config>
</m_Materials>
</m_Config>
</m_Models>
<m_Files><m_Config><m_FileName>data\\graphics\\buildings\\bakery\\bakery.cfg</m_FileName>
<m_ConfigType>FILE</m_ConfigType>
</m_Config>
</m_Files>
<m_ConfigType>ROOT</m_ConfigType>
</m_Config>"""


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
