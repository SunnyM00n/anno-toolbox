"""How the game picks a scene's data from its assets, per scene: which assets a list of the scene's data object
holds (the quick tools' pinned buttons, the religion scene's patrons, the options pages), which asset a child object
stands for (the patron shown), and the asset fields some data fields are named differently from.

``plan_for(scene)`` is a scene's ``SPlan``; ``gamedata`` walks a path with it. Each rule and where it was read:
docs/ui/117/game-data.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

REGIONS = ("Roman", "Celtic", "Egyptian")
Node = tuple[int, str]  # an asset and a path inside its values ("" for the asset itself), what a path stands on


@dataclass(frozen=True)
class SPlan:
    """One scene. ``config``: its config asset's template (empty: the one named like the scene); ``extra``: more
    config templates whose fields answer by name; ``lists``: an array field -> its elements; ``objects``: a child
    object field -> the node it stands for; ``aliases``: a data field -> the asset field (a path) that holds it."""

    config: str = ""
    extra: tuple[str, ...] = ()
    lists: dict[str, Callable] = field(default_factory=dict)
    objects: dict[str, Callable] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    main: Callable | None = None  # the object the whole scene shows (an object menu's building)
    classes: dict[str, Callable] = field(default_factory=dict)  # a child object field -> its data class's name


def config_template(scene: str) -> str:
    """The template of the config asset a scene file reads (``quicktoolsscene`` -> ``QuickToolsScene``,
    ``omproductionscene`` -> ``ObjectmenuProductionScene``), compared without case."""
    stem = scene.lower()
    if stem.startswith("om") and not stem.startswith("omni"):
        stem = "objectmenu" + stem[2:]
    return stem


def region_of(game) -> str:
    name = getattr(game.m_defaults, "region_name", None)
    found = name() if callable(name) else "Roman"
    return found if found in REGIONS else "Roman"


def _guids(values) -> list[Node]:
    return [(int(value), "") for value in values if value and value.strip().isdigit()]


def _first_of(template: str, path: str) -> Callable:
    """The GUIDs a template's first asset names under ``path`` (an ElementTree path below the asset)."""

    def query(game, _ctx) -> list[Node]:
        found = game.m_assets.by_template(template)
        element = game.m_assets.element(found[0]) if found else None
        return _guids(x.text for x in element.findall(path)) if element is not None else []

    return query


def _sub(path: str) -> Callable:
    """The items under ``path`` of the node the path stands on (a patron's effect milestones)."""

    def query(game, ctx) -> list[Node]:
        guid, base = ctx.node or (None, "")
        element = game.m_assets.element(guid)
        if element is None:
            return []
        at = f"{base}/{path}" if base else f"Values/*/{path}"
        return [(guid, f"{at}[{i + 1}]") for i in range(len(element.findall(at)))]

    return query


def _pinned(game, ctx) -> list[Node]:
    """The quick tools wheel: the config asset's pinned buttons of the player's region."""
    element = game.m_assets.element(ctx.config)
    found = element.find(f"Values/*/PinsPerRegion/{region_of(game)}") if element is not None else None
    return _guids(x.text for x in found.iter("Asset")) if found is not None else []


def _options_pages(game, ctx) -> list[Node]:
    """The options pages the player's input shows (``VisibleWithDevice``: the gamepad's controls page is not shown
    with mouse and keyboard, so the game's tab bar holds 7)."""
    element = game.m_assets.element(ctx.config)
    device = "MouseKeyboard" if getattr(game.m_defaults, "input", "mouse") == "mouse" else "Gamepad"
    pages = _guids(x.text for x in element.iter("SettingsMenu")) if element is not None else []
    return [page for page in pages if device in (_page_devices(game, page[0]) or device).split(";")]


def _open_page(game, ctx) -> list[Node]:
    """The options page shown: the one Play mode selected (``TabsData.SelectedTabID``, an index into the tabs), else
    the first, as the game opens the scene."""
    pages = _options_pages(game, ctx)
    chosen = chosen_value(game, "TabsData.SelectedTabID")
    i = chosen if isinstance(chosen, int) and not isinstance(chosen, bool) and 0 <= chosen < len(pages) else 0
    return pages[i : i + 1]


def _page_class(game, ctx) -> str:
    """The data class of the options page shown: ``OptionsData`` is typed generic page or controls page, and the
    one page asset with a ``Type`` (``ControlsMK``, no entries of its own) is the controls page [I]."""
    page = game.m_assets.element(ctx.node[0]) if ctx.node else None
    kind = page.findtext("Values/SettingsMenu/Type") if page is not None else None
    return "rdgs.CControlOptionsPageData" if (kind or "").startswith("Controls") else "rdgs.CGenericOptionsPageData"


def chosen_value(game, field: str):
    """What Play mode chose for a field of the open scene's data object (named by its last fields), None if nothing;
    what asked is dropped when such a choice is made (``playstate.choice_tokens``)."""
    note = getattr(game, "m_note", None)
    if note is not None:
        from .playstate import TOKEN

        note((TOKEN, f"tail:{field.rpartition('.')[2]}"))
    values = getattr(getattr(game, "m_play", None), "values", None) or {}
    return next(
        (value for k, value in values.items() if k.startswith(f"{game.m_scene}.") and k.endswith(f".{field}")), None
    )


def _page_devices(game, guid: int) -> str:
    page = game.m_assets.element(guid)
    return (page.findtext("Values/SettingsMenu/VisibleWithDevice") or "") if page is not None else ""


def _option_entries(game, ctx) -> list[Node]:
    """An options page's entries as the PC shows them: its ``Elements`` without those hidden on the PC."""
    guid, _ = ctx.node or (None, "")
    element = game.m_assets.element(guid)
    items = element.findall("Values/SettingsMenu/Elements/Item") if element is not None else []
    at = "Values/SettingsMenu/Elements/Item"
    return [(guid, f"{at}[{i + 1}]") for i, it in enumerate(items) if "PC" not in (it.findtext("HideOnPlatform") or "")]


def _first_category(game, ctx) -> list[Node]:
    """The construction menu's buildings: the first need category of the player's region (``LinearBuildingsMenu``),
    its ``BuildingList``."""
    element = game.m_assets.element(ctx.config)
    item = (
        element.find(f"Values/*/LinearBuildingsMenu/{region_of(game)}/NeedCategories/Item/TierCategory")
        if element
        else None
    )
    category = game.m_assets.element(int(item.text)) if item is not None and (item.text or "").isdigit() else None
    found = category.findall("Values/ConstructionCategory/BuildingList/Item/Building") if category is not None else []
    return _guids(x.text for x in found)


def _first(query: Callable) -> Callable:
    def one(game, ctx) -> list[Node]:
        return query(game, ctx)[:1]

    return one


PATRONS = _first_of("ReligionBalancing", "Values/ReligionBalancing/Patrons/Item/PatronAsset")
PATRON_FIELDS = {
    "DeityName": "PatronName", "SelectedDeityName": "PatronName", "EquippedDeityName": "PatronName",
    "DeityDescription": "PatronDescription", "DeityArtwork": "PortraitBig", "DeityArtworkOLD": "PortraitBig",
    "DeityBackground": "PortraitBackground", "DeityBackgroundOLD": "PortraitBackground",
    "DeityPortrait": "PortraitSmall", "DominantPatronPortrait": "PortraitBig",
    "DominantPatronBackground": "PortraitBackground", "DominantPatronHeader": "PatronName",
    "FirstEffectName": "LocalEffects/Item[1]/Title", "SecondEffectName": "LocalEffects/Item[2]/Title",
    "FirstEffectDescription": "LocalEffects/Item[1]/Description",
    "SecondEffectDescription": "LocalEffects/Item[2]/Description",
    "GlobalEffectTitle": "DominantEffects/Item[1]/Title",
    "GlobalEffectDescription": "DominantEffects/Item[1]/Description",
    "DominanceThreshold": "DominantPatronThreshold", "ShrineThreshold": "ShrineTreshold",
    "UnlockDevotion": "Devotion",
    # run-time values: the first milestone's devotion stands for a patron's belief [I]
    "GlobalDeityBelief": "LocalEffects/Item[1]/Milestones/Item[1]/Devotion",
    "GlobalDeityBeliefString": "LocalEffects/Item[1]/Milestones/Item[1]/Devotion",
    "CurrentBelief": "LocalEffects/Item[1]/Milestones/Item[1]/Devotion",
    "CurrentDevotion": "LocalEffects/Item[1]/Milestones/Item[1]/Devotion",
}  # fmt: skip

_HEADLINES = ("Headline", "ComboboxHeadline", "ToggleHeadline", "SliderHeadline", "ButtonHeadline", "TextInputHeadline")
OPTION_FIELDS = {
    "Text": "|".join(f"Element/Values/*/{headline}" for headline in _HEADLINES),
    "Icon": "Element/Values/*/Icon",
    "ButtonText": "Element/Values/*/ButtonText",
    "CurrentValue": "Element/Values/*/Entries/Item[1]/Text|Element/Values/*/ActivatedText",
    "Header": "InfoPanelHeadline", "SubHeader": "InfoPanelDescription",
}  # fmt: skip

PLANS: dict[str, SPlan] = {
    "quicktoolsscene": SPlan(lists={"QuicktoolsArray": _pinned}),
    "religionscene": SPlan(
        extra=("ReligionBalancing",),
        lists={
            "DeityWheelArray": PATRONS,
            "ArrayRanking": PATRONS,
            "ArrayPins": _sub("LocalEffects/Item[1]/Milestones/Item"),
        },  # fmt: skip
        objects={
            "LocalOverview": _first(PATRONS),
            "DeitySelection": _first(PATRONS),
            "GlobalOverview": _first(PATRONS),
        },  # fmt: skip
        aliases=PATRON_FIELDS,
    ),
    "optionsscene": SPlan(
        lists={"TabsBtnsList": _options_pages, "OptionsData.Entries": _option_entries},
        objects={"OptionsData": _open_page},
        aliases=OPTION_FIELDS,
        classes={"OptionsData": _page_class},
    ),
}
PLANS["constructionmenuscene"] = SPlan(
    config="ConstructionMenu", lists={"ConstructionData.CategoryBuildingsData": _first_category}
)
EMPTY = SPlan()


def _selected(game, _ctx) -> list[Node]:
    """An object menu's object: the residence or the production building the preview assumes
    (``previewdefaults.SDefaults``)."""
    defaults = game.m_defaults
    guid = getattr(defaults, "residence" if "residence" in game.m_scene.lower() else "production", None)
    return [(int(guid), "")] if guid else []


OBJECT_MENU = SPlan(main=_selected)


def plan_for(scene: str) -> SPlan:
    """A scene's plan; an object menu (``om...scene``) shows the preview's selected object."""
    low = scene.lower()
    found = PLANS.get(low)
    if found is None and low.startswith("om") and low.endswith("scene"):
        return OBJECT_MENU
    return found or EMPTY


def list_query(plan: SPlan, names: list[str]) -> Callable | None:
    """The query of the array a path ends at: a plan names it by its field, or by the field with the ones above it
    (``OptionsData.Entries``), the longest that matches."""
    for count in range(len(names), 0, -1):
        found = plan.lists.get(".".join(names[-count:]))
        if found is not None:
            return found
    return None
