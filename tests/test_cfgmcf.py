"""1701's mesh configs named by their class types (``models.cfgmcf``) in the object tree and the property editor, on a
file built like the shipped ones (their classes and fields read over all of the install's 1,381 files)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from tests.test_objbuild import qt  # noqa: F401  (the fixture)

from annotoolbox.models import cfgfields, cfgmcf
from annotoolbox.models.cfgdoc import Doc
from annotoolbox.sources import gametree

NL = "\r\n"


def _node(tag: str, *fields: str, children: str = "") -> str:
    inner = "".join(f"<m_{k}>{v}</m_{k}>{NL}" for k, v in (f.split("=", 1) for f in fields))
    return f"<m_{tag}>{inner}{children}</m_{tag}>{NL}"


def _wrap(config: str, *transforms: str) -> str:
    return _node("MCConfig", children=config + "".join(transforms))


def _childs(*items: str) -> str:
    return f"<m_MCChilds>{NL}{''.join(items)}</m_MCChilds>{NL}"


MCF = _node(
    "MeshConfig", "MCClassType=0", "Description=Dummies",
    children=_childs(
        _wrap(_node("MeshConfig", "MCClassType=1", r"MeshFilename=data\graphics\objects\house\house_lod0.gr2"),
              _node("MeshTransform", "MCClassType=101", "OffsetPosition.x=0"),
              _node("MeshTransform", "MCClassType=105", "LODMin=61", "LODMax=100")),
        _wrap(_node("MeshConfig", "MCClassType=3", r"EffectFilename=data\graphics\effects\efc\smoke.efc"),
              _node("MeshTransform", "MCClassType=109", "ConditionTrigger=ISLANDEDITOR")),
        _wrap(_node("MeshConfig", "MCClassType=6", "Dummyname=fire09"), _node("MeshTransform", "MCClassType=103", "DummyName=torch")),
        _wrap(_node("MeshConfig", "MCClassType=0", "Description=Doors", children=_childs()), _node("MeshTransform", "MCClassType=107")),
        _node("Material", "MCClassType=51", "Name=01 - Default", r"DiffuseTexture=data\graphics\wall.tga"),
        _node("Animation", "MCClassType=50", r"Filename=data\graphics\anim\door_open.gr2"),
        _wrap(_node("MeshConfig", "MCClassType=42"), _node("MeshTransform", "MCClassType=99")),
    ),
)  # fmt: skip
PATH = "data/graphics/objects/house/house.mcf"


def _labels(doc: Doc) -> dict[str, str]:
    return {p.path: cfgmcf.label(p) for p in doc.parts().walk()}


def test_a_node_is_named_by_its_class_and_what_it_points_at():
    labels = _labels(Doc.read(MCF.encode(), PATH))
    assert labels[""] == "Group  Dummies"
    assert labels["MCChilds#0"] == "Children (7)"
    assert labels["MCChilds#0/MCConfig#0"] == "Mesh  house_lod0.gr2"
    assert labels["MCChilds#0/MCConfig#0/MeshConfig#0"] == "Mesh settings"
    assert labels["MCChilds#0/MCConfig#0/MeshTransform#0"] == "Offset"
    assert labels["MCChilds#0/MCConfig#0/MeshTransform#1"] == "Level of detail  61-100"
    assert labels["MCChilds#0/MCConfig#1"] == "Effect  smoke.efc"
    assert labels["MCChilds#0/MCConfig#1/MeshTransform#0"] == "Shown on a condition  ISLANDEDITOR"
    assert labels["MCChilds#0/MCConfig#2"] == "Dummy  fire09"
    assert labels["MCChilds#0/MCConfig#2/MeshTransform#0"] == "Link to a dummy  torch"
    assert (
        labels["MCChilds#0/MCConfig#3"] == "Group  Doors" and labels["MCChilds#0/MCConfig#3/MeshTransform#0"] == "Scale"
    )
    assert labels["MCChilds#0/Material#0"] == "Material  01 - Default"
    assert labels["MCChilds#0/Animation#0"] == "Animation  door_open.gr2"
    assert labels["MCChilds#0/MCConfig#4"] == "MeshConfig 42"  # a class nobody documented keeps its number
    assert labels["MCChilds#0/MCConfig#4/MeshTransform#0"] == "MeshTransform 99"


def test_every_class_of_the_install_has_a_name():
    assert set(cfgmcf.NODE_CLASSES) == {0, 1, 2, 3, 4, 6, 7, 8, 9}
    assert set(cfgmcf.TRANSFORM_CLASSES) == {101, 102, 103, 104, 105, 107, 109, 110, 111}


def test_other_files_are_none_of_its_nodes():
    new = Doc.read(b"<Config><ConfigType>MAIN</ConfigType><Models></Models></Config>", "a.cfg")
    assert all(cfgmcf.label(p) is None for p in new.parts().walk())


def test_the_files_a_1701_field_names_are_the_kind_it_says_and_keep_their_backslashes():
    assert cfgfields.file_suffixes("MeshFilename", "data\\a\\b.gr2", "MeshConfig 1") == (".gr2",)
    assert cfgfields.file_suffixes("EffectFilename", "", "MeshConfig 3") == (".efc",)
    assert cfgfields.file_suffixes("MeshConfigFilename", "", "MeshConfig 2") == (".mcf",)
    assert cfgfields.file_value("data/a/new.gr2", "data\\a\\old.gr2") == "data\\a\\new.gr2"
    assert cfgfields.file_value("data/a/new.gr2", "data/a/old.gr2") == "data/a/new.gr2"


@pytest.fixture
def game(tmp_path):
    path = tmp_path / "1701" / PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(MCF.encode())
    tree = gametree.loose_tree(tmp_path / "1701", "Anno 1701")
    tree.game = "anno1701"
    return tree


def test_the_object_tree_and_the_properties_show_the_names(qt, game, tmp_path):  # noqa: F811
    from annotoolbox.objbuild.objwindow import ObjectWindow
    from annotoolbox.suite.settings import Settings

    window = ObjectWindow(Settings(workspace=str(tmp_path / "ws")), None)
    window.set_tree(game)
    assert window.open_path(PATH)
    top = window.objects.topLevelItem(0)
    texts = []

    def walk(item):
        texts.append(item.text(0))
        for k in range(item.childCount()):
            walk(item.child(k))

    walk(top)
    assert "Mesh  house_lod0.gr2" in texts and "Effect  smoke.efc" in texts and "Level of detail  61-100" in texts
    window.objects.select("cfg", "MCChilds#0/MCConfig#0/MeshConfig#0")
    rows = {
        window.props.topLevelItem(k).text(0): window.props.topLevelItem(k)
        for k in range(window.props.topLevelItemCount())
    }
    assert rows["m_MeshFilename"].foreground(1).color().name() == "#4f9fd9"  # a file field: shown as one
    assert "m_MCClassType" in rows and window.props.part_kind == "MeshConfig 1"
    window.obj.undo.mark_clean()
    window.close()
