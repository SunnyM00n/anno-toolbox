"""Stand-ins for the particle editor: effect and config texts, trees of three games and a game over them."""

from __future__ import annotations

import pytest
from tests.objbuild.objbuildfakes import SHARED_HOUSE_CFG

from annotoolbox.formats.rdp117 import SBakedParticle, SRdpMaterial, quantise, write_rdp
from annotoolbox.sources import gametree

RDP_PATH = "data/base/fx/smoke.rdp"


PARTICLE_HOUSE_PATH = "data/base/house/house.cfg"


EFC = "data/graphics/effects/fire.efc"


CFG2070 = "data/graphics/buildings/hut/hut.cfg"


EFC1701 = "data/graphics/effects/efc/splash.efc"


PARTICLE_CFG = (
    "<Config><ConfigType>MAIN</ConfigType>\r\n<Particles><Config><ConfigType>PARTICLE</ConfigType>\r\n"
    "<Name>smoke</Name>\r\n<FileName>{path}</FileName>\r\n<TimeScale>1</TimeScale>\r\n<IsEmitterBound>1</IsEmitterBound>\r\n"
    "</Config>\r\n</Particles>\r\n</Config>\r\n"
)


EFC_TEXT = (
    "<Effect0><Texture0>data/graphics/effects/particles/maps/effects_big.png</Texture0>\r\n<Name>RenameMe0</Name>\r\n"
    "<EffectType>1</EffectType>\r\n<Technique>1</Technique>\r\n<Delay>0</Delay>\r\n<LifeTime>1600</LifeTime>\r\n"
    "<Duration>500</Duration>\r\n<Loop>1</Loop>\r\n<Bound>0</Bound>\r\n<OffsetX>0.000000</OffsetX>\r\n"
    "<PEPValueMin>0.000000</PEPValueMin>\r\n<PEPValueMax>100.000000</PEPValueMax>\r\n<PEPValueDef>50.000000</PEPValueDef>\r\n"
    "<PEPVarianz>0.000000</PEPVarianz>\r\n<PEPSize>1</PEPSize>\r\n<PEPKnotPos_0>1.000000</PEPKnotPos_0>\r\n"
    "<PEPKnotX_0>0.200000</PEPKnotX_0>\r\n<PEPKnotY_0>0.200000</PEPKnotY_0>\r\n<PEPKnotZ_0>0.200000</PEPKnotZ_0>\r\n"
    "<PEPKnotW_0>0.200000</PEPKnotW_0>\r\n<ColorValueMin>0.000000</ColorValueMin>\r\n<ColorValueMax>1.000000</ColorValueMax>\r\n"
    "<ColorValueDef>1.000000</ColorValueDef>\r\n<ColorVarianz>0.000000</ColorVarianz>\r\n<ColorSize>0</ColorSize>\r\n"
    "<Alignment>0</Alignment>\r\n</Effect0>\r\n"
)


CFG2070_TEXT = (
    "<m_Config><m_Effects><m_Config><m_FileName>data\\graphics\\effects\\fire.efc</m_FileName>\r\n"
    "<m_ConfigType>EFFECT</m_ConfigType>\r\n</m_Config>\r\n</m_Effects>\r\n<m_ConfigType>MAIN</m_ConfigType>\r\n"
    "</m_Config>\r\n"
)


BRACE_TEXT = (
    'Effect\r\n{\r\n\tBillboardEffect\r\n\t{\r\n\t\tName "BillboardEffect0000"\r\n\t\tDuration 1500\r\n\t\tLoop 0\r\n'
    '\t\tTexture "data\\graphics\\textures\\effects\\effects_big.tga"\r\n\t\tLoop 1\r\n'
    "\t\tColor\r\n\t\t{\r\n\t\t\tColor\r\n\t\t\t{\r\n\t\t\t\tRGBA 1.000000 1.000000 1.000000 1.000000\r\n"
    "\t\t\t\tTime 0.000000\r\n\t\t\t}\r\n\t\t}\r\n"
    "\t\tAlpha\r\n\t\t{\r\n\t\t\tMinimum 0.000000\r\n\t\t\tMaximum 1.000000\r\n\t\t\tSingle 1\r\n\t\t\tValue 1.000000\r\n"
    "\t\t\tLinear 1\r\n\t\t\tPoint\r\n\t\t\t{\r\n\t\t\t\tp 0.000000 0.000000\r\n\t\t\t\ti -0.166667 0.000000\r\n"
    "\t\t\t\to 0.166667 0.000000\r\n\t\t\t\tudt 0\r\n\t\t\t}\r\n\t\t}\r\n\t}\r\n}\r\n"
)


def rdp_bytes() -> bytes:
    ticks = 4
    parts = [
        SBakedParticle(k, k + ticks - 1, 0, [(0.0, 0.1 * t, 0.0) for t in range(ticks)], [(0.5, 0.5, 0.5)] * ticks,
                      [(0.0, 0.0, 0.0)] * ticks, [(1.0, 0.5, 0.25, 1.0)] * ticks)
        for k in range(3)
    ]  # fmt: skip
    return write_rdp(quantise(parts, [SRdpMaterial()], "data\\base\\fx\\smoke.psd", 8, True))


@pytest.fixture
def particle_game(tmp_path):
    def make(key: str, files: dict[str, bytes | str]):
        root = tmp_path / key
        for rel, data in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data.encode("latin-1") if isinstance(data, str) else data)
        tree = gametree.loose_tree(root, key)
        tree.m_game = key
        return tree

    return make


@pytest.fixture
def tree117(game):
    cfg = PARTICLE_CFG.format(path=RDP_PATH)
    return game(
        "anno117",
        {RDP_PATH: rdp_bytes(), PARTICLE_HOUSE_PATH: cfg, SHARED_HOUSE_CFG: cfg, "data/base/fx/empty.rdp": b""},
    )


def window_on(tree, tmp_path):
    from annotoolbox.particleedit.particlewindow import CParticleWindow
    from annotoolbox.suite.settings import CSettings

    window = CParticleWindow(CSettings(workspace=str(tmp_path / "ws")), None, is_synchronous=True)
    window.set_tree(tree)
    return window
