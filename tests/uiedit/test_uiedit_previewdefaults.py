"""What the UI preview assumes of the player's game (``uiedit.previewdefaults``): the campaign's characters, the
values the game gives the components it fills from the player's state, the lock a game object's asset shows, the
pictures a run-time slot names, the store's write API, reset and persistence, and the choices read from the assets
(``uiedit.previewcatalogue``) - all on synthetic data."""

from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from tests.ui.uifakes import SCHEMA
from tests.uiedit.uieditfakes import GEN, IMAGE, image_file, scene_instance
from tests.uiedit.uieditfakes import UIEDIT_VERSIONS as VERSIONS
from tests.uiedit.uieditfakes import ui_file_bytes as _write

from annotoolbox.formats import phoenix, phoenixedit
from annotoolbox.sources import gametree
from annotoolbox.uiedit import layout, previewcatalogue, previewdefaults, uifiles, viewmodels
from annotoolbox.uiedit.pictures import CPictures

PD = previewdefaults


def test_the_campaign_casts_marcia_lucius_and_the_rivals_and_pirates_of_its_setup():
    d = PD.SDefaults()
    assert d.participant().name == "Marcia" and d.participant().portrait == 38962 and d.participant().colour == 91304
    assert d.cast("emperor")[0].participant == 31721  # Lucius, act 1 of the campaign; Calidus (31637) reigns from act 2
    assert [c.name for c in d.cast("rival")] == ["Tarragon", "Athr Iorgwyn", "Concordia"]
    assert [c.participant for c in d.cast("pirate")] == [37495, 37494, 153069]  # Latium's Caeso first, Albion's, Nile's
    assert PD.SDefaults(region=PD.AEGYPTUS).cast("pirate")[0].name == "Kandis Of Kashta"  # the Nile region's pirate
    assert PD.SDefaults(region=PD.ALBION).cast("pirate")[0].name.startswith("Voada")
    assert d.region_name() == "Roman" and d.input == "mouse" and d.session == "single" and not d.is_locked()
    assert d.option("setup.SetupTaxes") == 2 and d.option("banner.LogoColor") == 91307
    assert d.researched(1234, tier=9) == 1.0 and PD.SDefaults(research="tier:3").researched(5, tier=4) == 0.0


def test_names_of_slots_and_properties_take_the_characters_values():
    d = PD.SDefaults()
    assert PD.picture_for("PlayerPortrait", d).endswith("portrait_marcia.png")
    assert PD.picture_for("EmperorPortrait", d).endswith("portrait_emperor_lucius.png")
    assert PD.picture_for("Portrait_Encounter", d).endswith("portrait_rival_tarragon.png")  # another participant
    assert PD.picture_for("logo", d).endswith("icon_2d_logo_campaign_marcia_128.png")
    assert PD.picture_for("2dPortrait", d) is None and PD.picture_for("Icon", d) is None
    assert PD.text_for("PlayerName", d) == "Marcia" and PD.text_for("EmperorName", d) == "Emperor Lucius Augustus"
    assert PD.bool_for("IsGamepadModeActive", d) is False and PD.bool_for("IsMultiplayer", d) is False
    assert PD.bool_for("IsGamepadModeActive", PD.SDefaults(input="xbox")) is True
    assert PD.number_for("Money", d) == d.money
    assert PD.text_for("Text_Input", d) == "Marcia" and PD.text_for("CharacterName", d) == "Marcia"
    assert (
        PD.text_for("SessionName", d) == "Latium" and PD.text_for("IslandName", d) is None
    )  # a place is the session's
    assert PD.number_for("PlayerCount", d) == 1 and PD.number_for("PlayerCount", PD.SDefaults(session="multi")) == 2


def test_the_components_the_game_fills_take_the_players_region_and_input():
    comps = {"30427a23": 7, "a7cc0860": 8, "cd9c2b59": 9}
    values = PD.component_values(comps, PD.SDefaults())
    assert values[(7, PD.REGION_INFO[1])] == PD.PROVINCE["Roman"]  # the HUD's Roman mosaic, not the stored Egyptian
    assert values[(8, PD.GAMEPAD[1])] is False and values[(8, PD.PLATFORM_ID[1])] == PD.PLATFORM["mouse"]
    albion = PD.component_values(comps, PD.SDefaults(region=PD.ALBION, input="playstation"))
    assert albion[(7, PD.REGION_INFO[1])] == PD.PROVINCE["Celtic"] and albion[(8, PD.GAMEPAD[1])] is True
    out = {(9, PD.PORTRAIT_GUID[1]): 31127}
    PD.resolve_portrait(out, comps, PD.SDefaults())
    assert out[(9, PD.PORTRAIT_IMAGE[1])].endswith("portrait_rival_concordia.png")


def _scene_with_lock(tmp_path) -> uifiles.CUiFiles:
    """A scene with a game object whose lock flag decides a lock picture's visibility, and a region component."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    lock = scene_instance("c19f99dd", "IconLock")
    phoenixedit.set_raw(lock, "Image", phoenix.ref_bytes(phoenixedit.IMAGE_ASSET, IMAGE))
    phoenixedit.set_objects(lock, "Components", [phoenixedit.component(
        SCHEMA, "30427a23-7a8d-45a9-b659-58fff382c12e", "PlayerRegionInfo", 77, VERSIONS)])  # fmt: skip
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(lock)
    wf.add_property("Building", "gameobject")
    wf.add_property("-IsLocked", "bool", False)
    wf.bind(wf.end(None, [wf.m_unique_ids["Building"], 0x20000002]), wf.own("-IsLocked"))
    wf.bind(wf.own("-IsLocked"), wf.end(lock, [phoenixedit.BUILTIN["Visible"]]))
    wf.finish("scene")
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    (gen / "lockscene").write_bytes(_write("SC", root))
    (gen / IMAGE).write_bytes(image_file())
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))


def _locks(files, defaults, guid: int | None = None) -> int:
    samples = viewmodels.CSamples()
    building = viewmodels.CSamples.key("lockscene", [phoenixedit.unique_id(0x10000000, "Building")])
    if guid is not None:
        samples.set(f"{building}/20000001", guid)  # the asset the game object stands for, as the game gives it
    ctx = layout.CContext(files, samples, m_defaults=defaults)
    frame = layout.place("lockscene", files.root("lockscene"), ctx)
    return len([i for i in frame.items if i.kind == "image"])


def test_locking_one_building_shows_its_lock_and_all_unlocked_hides_every_lock(tmp_path):
    files = _scene_with_lock(tmp_path)
    assert _locks(files, PD.SDefaults(), 3145) == 0  # all unlocked: the lock is hidden
    assert _locks(files, PD.SDefaults(locked=[3145]), 3145) == 1  # the Patrician Residence locked shows its lock
    assert _locks(files, PD.SDefaults(locked=[3145]), 3142) == 0  # another building stays unlocked
    assert _locks(files, PD.SDefaults(progression="start")) == 1  # below the late game a lock of any asset shows


def test_the_preview_reads_the_region_component_from_the_defaults(tmp_path):
    files = _scene_with_lock(tmp_path)
    lock = next(n for n in phoenixedit.walk(files.root("lockscene")) if phoenixedit.object_name(n) == "IconLock")
    assert layout.CContext(files).values(lock)[(77, PD.REGION_INFO[1])] == PD.PROVINCE["Roman"]
    ctx = layout.CContext(files, m_defaults=PD.SDefaults(region=PD.AEGYPTUS))
    assert ctx.values(lock)[(77, PD.REGION_INFO[1])] == PD.PROVINCE["Egyptian"]


def _region_on_root(tmp_path) -> uifiles.CUiFiles:
    """A file whose own root holds the region component, its region written into a label (the hairlines' files)."""
    root = phoenixedit.new_object(SCHEMA, "1795cb2b", guid=uuid.uuid4().bytes_le, root=True, versions=VERSIONS)
    phoenixedit.set_field(root, "ReferenceSize", (1920.0, 1080.0))
    phoenixedit.set_objects(root, "Components", [phoenixedit.component(
        SCHEMA, "30427a23-7a8d-45a9-b659-58fff382c12e", "PlayerRegionInfo", 78, VERSIONS)])  # fmt: skip
    label = scene_instance("41a404e5", "Region")
    wf = phoenixedit.CWidgetFile(SCHEMA, root, versions=VERSIONS)
    wf.add_instance(label)
    wf.bind(wf.end(None, [78, PD.REGION_INFO[1]]), wf.end(label, [phoenixedit.BUILTIN["Text"]]))
    wf.finish("scene")
    gen = tmp_path / "game" / GEN
    gen.mkdir(parents=True)
    (gen / "regionscene").write_bytes(_write("SC", root))
    return uifiles.CUiFiles(gametree.loose_tree(tmp_path / "game"))


def test_a_files_own_region_component_reads_the_players_region(tmp_path):
    files = _region_on_root(tmp_path)

    def shown(defaults) -> set[str]:
        frame = layout.place("regionscene", files.root("regionscene"), layout.CContext(files, m_defaults=defaults))
        return {item.text for item in frame.items if item.kind == "text"}

    assert shown(PD.SDefaults()) == {str(PD.PROVINCE["Roman"])}  # was the component's declared default
    assert shown(PD.SDefaults(region=PD.AEGYPTUS)) == {str(PD.PROVINCE["Egyptian"])}


def test_a_run_time_slot_shows_the_named_characters_picture_else_an_icon_from_the_games_folders(monkeypatch):
    paths = ["data/ui/4k/dlc02/icon_content/hippodrome/icon_3d_hippodrome_phase_05_0.dds",
             "data/ui/4k/base/icon_content/attributes/icon_2d_income_0.dds",
             "data/ui/4k/base/icon_content/portraits/portrait_marcia_0.dds"]  # fmt: skip
    pictures = CPictures(SimpleNamespace(m_tree=SimpleNamespace(m_paths=paths)))
    asked = []
    monkeypatch.setattr(pictures, "get", lambda asset=None, image_id=None, size_px=None: asked.append(image_id) or 1)
    pictures.sample("PlayerPortrait")
    assert asked[-1].endswith("portrait_marcia.png")
    asked.clear()
    pictures._m_samples.clear()
    monkeypatch.setattr(PD, "picture_for", lambda slot, d: None)
    pictures.sample("Icon")
    assert asked[-1].endswith("icon_2d_income_0.dds")  # the folders the game draws an "Icon" from, not the hippodrome


def test_the_store_writes_resets_tells_and_keeps_its_values_per_game_and_per_project(tmp_path):
    apps: dict = {}
    store = PD.CStore(apps, "anno117", tmp_path / "anno117-preview.json")
    told = []
    store.m_listeners.append(told.append)
    store.set("input", "xbox")
    store.set("options.setup.SetupTaxes", 0, play=True)
    assert told and told[-1].input == "xbox" and told[-1].option("setup.SetupTaxes") == 0
    assert apps["ui"]["preview"]["anno117"] == {"input": "xbox", "options": {"setup.SetupTaxes": 0}}
    assert PD.CStore(apps, "anno117").m_defaults.input == "xbox"  # read back from the Toolbox settings
    store.reset(played=True)  # the play mode's changes go, the user's stay
    assert store.m_defaults.option("setup.SetupTaxes") == 2 and store.m_defaults.input == "xbox"
    store.keep_in_project(True)
    store.set("participant_name", "Livia")
    kept = json.loads((tmp_path / "anno117-preview.json").read_text(encoding="utf-8"))
    assert kept["values"]["participant_name"] == "Livia"
    again = PD.CStore({}, "anno117", tmp_path / "anno117-preview.json")  # the project's file wins
    assert again.m_defaults.participant().name == "Livia" and again.m_defaults.input == "xbox"
    store.reset("input")
    assert store.m_defaults.input == "mouse"
    store.reset()
    assert PD.changes(store.m_defaults) == {} and store.m_defaults.participant().name == "Marcia"
    store.keep_in_project(False)
    assert not (tmp_path / "anno117-preview.json").exists()


def test_the_values_kept_in_a_project_go_back_to_the_settings_when_it_stops_keeping_them(tmp_path):
    apps: dict = {}
    store = PD.CStore(apps, "anno117", tmp_path / "anno117-preview.json")
    store.keep_in_project(True)
    store.set("participant_name", "Livia")
    store.keep_in_project(False)
    assert not (tmp_path / "anno117-preview.json").exists()
    assert (
        PD.CStore(apps, "anno117").m_defaults.participant().name == "Livia"
    )  # not the settings from before the project


def _asset(guid: int, template: str, name: str, values: str) -> SimpleNamespace:
    xml = f"<Asset><Template>{template}</Template><Values><Standard><GUID>{guid}</GUID><Name>{name}</Name>"
    return SimpleNamespace(template=template, name=name, element=ET.fromstring(xml + values + "</Values></Asset>"))


def _game() -> SimpleNamespace:
    """A stand-in of an opened 117 asset game: a rival, a customization scene, tiers, a tech and a setup option."""
    assets = [
        _asset(1, "Participant 2ndParty (Rival)", "Rival_02 (Tarragon)", "</Standard><Text><OasisId>10</OasisId></Text>"
               "<Participant><Portrait>2</Portrait><CompanyIcon>3</CompanyIcon><DefaultColor>4</DefaultColor>"
               "</Participant>"),
        _asset(2, "Portrait", "Tarragon Portrait", "<IconFilename>p.png</IconFilename></Standard>"),
        _asset(3, "PlayerLogo", "Logo", "<IconFilename>l.png</IconFilename></Standard>"),
        _asset(4, "ParticipantColor", "Blue", "</Standard><ParticipantColor><ParticipantUIColor><None>-14261860</None>"
               "</ParticipantUIColor></ParticipantColor>"),
        _asset(500769, "ProfileCustomizationScene", "Customization", "</Standard><ProfileCustomizationScene>"
               "<Portraits><Item><Portrait>2</Portrait></Item></Portraits><Logos><Item><Logo>3</Logo></Item></Logos>"
               "<BannerColors><Colors>4</Colors></BannerColors></ProfileCustomizationScene>"),
        _asset(1494, "PopulationGroup7", "Romans", "</Standard><PopulationGroup7><PopulationLevels><Item><Level>1499"
               "</Level></Item></PopulationLevels><Regional>Roman</Regional></PopulationGroup7>"),
        _asset(1499, "PopulationLevel", "Liberti", "</Standard>"),
        _asset(24446, "Tech", "Tech Economy -1 7 Warehouse", "</Standard><Tech><TechName>11</TechName></Tech>"),
        _asset(3245, "SessionModerate", "Latium", "</Standard><Session><Region>3225</Region>"
               "<IsVisibleInGameSetup>1</IsVisibleInGameSetup></Session><Locked><DefaultLockedState>1"
               "</DefaultLockedState></Locked>"),
    ]  # fmt: skip
    by_guid = {int(a.element.findtext("Values/Standard/GUID")): a for a in assets}
    index = SimpleNamespace(m_by_guid=by_guid, element=lambda g: by_guid[g].element)
    spec = SimpleNamespace(name="SetupTaxes", data_set="DCTaxes")
    schema = SimpleNamespace(properties={"DifficultySetup": SimpleNamespace(values=[spec])},
                             data_sets={"DCTaxes": SimpleNamespace(items=[("Spare", "2"), ("Plenty", "0")])})  # fmt: skip
    texts = {"10": "Tarragon", "11": "Warehouse Organisation"}
    return SimpleNamespace(m_index=index, m_schema=schema, texts=lambda language="english": texts)


def test_the_catalogue_reads_each_settings_choices_from_the_assets():
    cat = previewcatalogue.read(_game())
    (rival,) = cat.characters["rival"]
    assert (rival.name, rival.portrait_icon, rival.emblem_icon, rival.rgb) == (
        "Tarragon",
        "p.png",
        "l.png",
        (38, 97, 156),
    )
    assert [o.icon for o in cat.portraits] == ["p.png"] and [o.rgb for o in cat.colours] == [(38, 97, 156)]
    assert [(o.guid, o.group, o.tier) for o in cat.tiers] == [(1499, "Roman", 1)]
    assert [(o.name, o.tier) for o in cat.research] == [("Warehouse Organisation", 7)]
    assert [(o.guid, o.name) for o in cat.setup["SetupTaxes"]] == [(2, "Spare"), (0, "Plenty")]
    assert [o.guid for o in cat.unlocks] == [3245] and [o.guid for o in cat.regions] == [3245]
    d = cat.defaults()
    d.participant_portrait, d.characters = 2, {"rival": [1]}
    assert d.participant().portrait_icon == "p.png" and d.cast("rival")[0].name == "Tarragon"


def test_stored_values_that_do_not_read_are_left_at_the_campaigns():
    odd = {"tiers": {"one": 2}, "locked": ["x"], "characters": {"rival": 7}, "dlcs": 5, "money": 4321, "unknown": 1}
    d = PD.with_changes(odd)
    base = PD.CAMPAIGN_DEFAULTS
    assert (d.tiers, d.locked, d.characters, d.dlcs) == (base.tiers, base.locked, base.characters, base.dlcs)
    assert d.money == 4321  # what reads is taken
    kept = PD.with_changes(json.loads(json.dumps(PD.changes(PD.with_changes({"tiers": {"1": 3}, "locked": [77]})))))
    assert kept.tiers == {1: 3} and kept.locked == [77]  # and what the store wrote reads back
