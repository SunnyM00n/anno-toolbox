"""Stand-ins for an installed game as the sources read it: the asset database files, a game over them, a mod and an
old-engine game, and the ambient map's text."""

from __future__ import annotations

import json
from pathlib import Path

from tests.fakes import asset_xml, value_definition

ASSET_SCHEMA = (  # the property definitions of ``asset_game`` (117's properties-meta.xml shape)
    "<Properties><Groups><Group><Name />"
    "<Property><Name>Standard</Name>" + value_definition("GUID", "Integer") + value_definition("Name", "String")
    + value_definition("IconFilename", "FileName") + value_definition("InfoDescription", "Text") + "</Property>"
    "<Property><Name>Building</Name>"
    + value_definition("BuildCost", "Vector", items=value_definition("Product", "Asset", "<NeededProperty>Product</NeededProperty>")
          + value_definition("Amount", "Integer", "<Min>0</Min><Max>100</Max>"))
    + value_definition("Level", "Choice", "<DataSet>Level</DataSet>") + value_definition("Kinds", "Flags", "<DataSet>ObjectType</DataSet>")
    + value_definition("Upkeep", "Float", "<Min>0</Min>") + value_definition("Workers", "Array", "<DataSet>Level</DataSet>",
                                                   value_definition("Count", "Integer"))
    + value_definition("Model", "Struct", items=value_definition("Scale", "Float") + value_definition("Visible", "Boolean")) + "</Property>"
    "<Property><Name>Product</Name>" + value_definition("Price", "Integer") + "</Property>"
    "<Property><Name>Quest</Name>" + value_definition("Reward", "Asset") + "</Property>"
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


def _vec(tag: str, **v: float) -> str:
    return f"<{tag}>" + "".join(f"<{k}>{x}</{k}>" for k, x in v.items()) + f"</{tag}>"


def _setting(diffuse_power: float = 2.0, wind_z: float = 0.17) -> str:
    return (
        _vec("DiffuseColor", r=0.5, g=0.25, b=1, a=0)
        + f"<DiffuseColorPower>{diffuse_power}</DiffuseColorPower>"
        + _vec("AmbientColor", r=0.5, g=0.5, b=0.5, a=0)
        + "<AmbientColorPower>1.5</AmbientColorPower>"
        + _vec("MoodColor", r=0.2, g=0.2, b=0.2, a=0.2)
        + "<MoodColorPower>2</MoodColorPower><InvCloudShadowFac>0.6</InvCloudShadowFac><SunColorPower>0.7</SunColorPower>"
        + "<HorizonColorPower>2</HorizonColorPower><ZenithColorPower>1</ZenithColorPower><CloudColorPower>1</CloudColorPower>"
        + "<Water>"
        + _vec("WaveFrequency", x=1, y=2)
        + _vec("WaveAmplitude", x=0.3, y=0.4)
        + _vec("WaveVelocity0", x=0.01, y=0.02)
        + _vec("WaveVelocity1", x=0.03, y=0.04)
        + "<Choppiness>3.5</Choppiness>"
        + _vec("AboveWaterLightAbsorptionExponents", x=1, y=2, z=3)
        + "<AboveWaterLightDepth>30</AboveWaterLightDepth><AboveWaterViewDepth>10</AboveWaterViewDepth>"
        + "<AboveWaterViewDepthExponent>0.35</AboveWaterViewDepthExponent>"
        + _vec("AboveWaterScattering", x=0.1, y=0.2, z=0.3)
        + _vec("UnderWaterLightAbsorptionExponents", x=4, y=5, z=6)
        + "<UnderWaterLightDepth>36</UnderWaterLightDepth><UnderWaterViewDepth>58.6</UnderWaterViewDepth>"
        + "<UnderWaterViewDepthExponent>1.84</UnderWaterViewDepthExponent>"
        + _vec("UnderWaterScattering", x=0.03, y=0.05, z=0.1)
        + "<UnderWaterDOFFocusDistance>26.8</UnderWaterDOFFocusDistance></Water>"
        + "<Atmosphere><ScatterStart>0.095</ScatterStart><SunInnerAngle>0</SunInnerAngle><SunOuterAngle>0.84</SunOuterAngle>"
        + "<SunFallOff>8.5</SunFallOff><HorizonFallOff>9.8</HorizonFallOff><DistanceFallOff>1.5</DistanceFallOff>"
        + "<CloudDensity>0.08</CloudDensity>"
        + _vec("SunColor", r=0.1, g=0.2, b=0.3, a=1)
        + _vec("HorizonColor", r=0.2, g=0.4, b=0.5, a=1)
        + _vec("ZenithColor", r=0.3, g=0.5, b=0.6, a=1)
        + _vec("CloudColor", r=0.9, g=0.9, b=0.9, a=1)
        + "</Atmosphere>"
        + _vec("Wind", x=0.2, y=0.98, z=wind_z, w=0)
        + _vec("Current", x=1, y=0, z=0.1, w=0.2)
        + "<BlurStrength>0.3</BlurStrength>"
        + _vec("LightDirection", x=-1, y=-1, z=0)
        + "<CamYRotOffset>2.6</CamYRotOffset>"
    )


AMBIENT = (
    "<AmbientMap><GlobalAreas><i><Name>North</Name><Position><x>0.25</x><y>0.75</y></Position>"
    "<Setting>North</Setting></i></GlobalAreas><Settings>"
    f"<North><Setting0>{_setting()}</Setting0><Setting1>{_setting(1.0, 0.5)}</Setting1></North>"
    f"<Night><Setting0>{_setting(0.5)}</Setting0></Night></Settings></AmbientMap>"
)


LINES = "100=Old name\r\n101=Other\r\n"


def old_game(root: Path) -> Path:
    files = {
        "data/config/game/properties.xml": ASSET_SCHEMA,
        "data/config/game/templates.xml": ASSET_TEMPLATES,
        "data/config/game/datasets.xml": ASSET_DATA_SETS,
        "data/config/game/assets.xml": asset_list(),
    }
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8", newline="\n")
    texts = root / "data/loca/eng/txt/buildings.txt"
    texts.parent.mkdir(parents=True, exist_ok=True)
    texts.write_bytes(b"\xff\xfe" + LINES.encode("utf-16-le"))
    return root
