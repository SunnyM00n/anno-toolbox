"""The model an asset names (``editor.assetmodels``): where its blocks stand in the file and why an object has none."""

from annotoolbox.editor import assetmodels
from annotoolbox.editor.assetmodels import CAssetModels, CReason


def _asset(blocks: str, template: str = "Thing") -> str:
    return f"<Asset><Template>{template}</Template><Values>{blocks}</Values></Asset>"


def _standard(guid: int, name: str) -> str:
    return f"<Standard><GUID>{guid}</GUID><Name>{name}</Name></Standard>"


def _variations(*cfgs: str) -> str:
    items = "".join(f"<Item><Filename>{cfg}</Filename></Item>" for cfg in cfgs)
    return f"<Object><Variations>{items}</Variations></Object>"


def _models(*assets: str) -> CAssetModels:
    return CAssetModels(("<Assets>" + "".join(assets) + "</Assets>").encode())


def test_the_blocks_before_the_standard_block_count_as_the_assets_own():
    """2205 writes ``Object`` before ``Standard``: its collectables and piers were drawn as pins for want of this."""
    before = _asset(f"<Gather><Kind>Ore</Kind></Gather>{_variations('data/g/pack.cfg')}{_standard(7, 'Pack')}")
    after = _asset(f"{_standard(8, 'Crate')}{_variations('data/g/crate.cfg')}")
    models = _models(before, after)
    assert models.asset(7).variations == ["data/g/pack.cfg"] and models.asset(7).name == "Pack"
    assert models.asset(8).variations == ["data/g/crate.cfg"]
    assert models.model_for(7).cfg == "data/g/pack.cfg"
    assert models.value_of(7, "Kind") == "Ore"


def test_the_editors_placeholder_file_is_a_placeholder_only_where_the_game_lacks_it():
    holder = "test_data/graphics/placeholder/placeholder.cfg"
    assert assetmodels.is_missing_placeholder("data/dev/graphics/placeholder/placeholder.cfg")
    assert assetmodels.is_missing_placeholder("data/graphics/ui/constraint_dummy.cfg")
    assert not assetmodels.is_missing_placeholder("data/graphics/house/house.cfg")
    assert not assetmodels.is_placeholder(holder)  # where the game ships it, it is drawn as a model
    spawner = _asset(f"{_standard(9, 'Spawner')}{_variations(holder)}")
    assert _models(spawner).model_for(9).cfg == holder


def test_why_a_variation_draws_nothing():
    models = _models(
        _asset(_standard(1, "Bare")),
        _asset(_standard(2, "Two") + _variations("data/g/a.cfg", "data/g/b.cfg")),
        _asset(_standard(3, "Holder") + _variations("data/dev/graphics/placeholder/placeholder.cfg")),
    )
    assert models.why_none(404) == CReason.NO_ASSET
    assert models.why_none(1) == CReason.NO_MODEL
    assert models.why_none(2, 5) == CReason.VARIATION  # past the list: the game's placeholder, not the first item
    assert models.why_none(2, 1) == ""  # it has a model: whether the file is in the game is the caller's to ask
    assert models.why_none(3) == CReason.PLACEHOLDER
    assert CReason.VARIATION in assetmodels.NO_PICTURE
    assert assetmodels.describe(CReason.VARIATION) != CReason.VARIATION  # the reason has its interface text
