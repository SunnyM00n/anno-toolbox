"""What the island editor reads of a config (``editor.modelparts``): the props of 1800's and 2205's containers."""

from __future__ import annotations

from annotoolbox.editor.modelparts import model_parts

PROP = (
    "<Config><ConfigType>PROP</ConfigType><FileName>data\\graphics\\props\\{name}.prp</FileName>"
    "<Position.x>{x}</Position.x><Position.y>0</Position.y><Position.z>0.5</Position.z>"
    "<Rotation.x>0</Rotation.x><Rotation.y>0</Rotation.y><Rotation.z>0</Rotation.z><Rotation.w>1</Rotation.w>"
    "<Scale.x>2</Scale.x><Scale.y>2</Scale.y><Scale.z>2</Scale.z></Config>"
)


def _container(*props: str, varies: bool = False) -> str:
    return (
        "<Config><ConfigType>PROPCONTAINER</ConfigType>"
        f"<VariationEnabled>{int(varies)}</VariationEnabled><Props>{''.join(props)}</Props></Config>"
    )


def _config(*containers: str) -> str:
    return f"<Config><ConfigType>MAIN</ConfigType><PropContainers>{''.join(containers)}</PropContainers></Config>"


def test_a_container_without_models_still_draws_its_props_at_their_place():
    text = _config(_container(PROP.format(name="storage\\barrel", x=1.5), PROP.format(name="storage\\box", x=-1)))
    files = model_parts(text)["files"]
    assert [(f["cfg"], f["position"], f["scale"]) for f in files] == [
        ("data/graphics/props/storage/barrel.prp", [1.5, 0.0, 0.5], [2.0, 2.0, 2.0]),
        ("data/graphics/props/storage/box.prp", [-1.0, 0.0, 0.5], [2.0, 2.0, 2.0]),
    ]
    assert files[0]["rotation"] == [0.0, 0.0, 0.0, 1.0]


def test_of_the_varying_containers_only_the_first_is_drawn():
    text = _config(
        _container(PROP.format(name="a", x=0)),
        _container(PROP.format(name="b", x=0), varies=True),
        _container(PROP.format(name="c", x=0), varies=True),
    )
    assert [f["cfg"][-5:] for f in model_parts(text)["files"]] == ["a.prp", "b.prp"]
