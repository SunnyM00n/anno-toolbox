from tests.fakes import FakeTree

from annotoolbox.editor import editorapp
from annotoolbox.editor.formats.isd import islandlist as isd_islandlist

MISSIONS = """<Asset><Template>ScenarioMission</Template><Values><Standard><Name>Scenario07</Name></Standard>
<GameSettings><LevelFile>data\\levels\\Scenarios\\Singleplayer\\07.www</LevelFile></GameSettings></Values></Asset>
<Asset><Template>ScenarioMission</Template><Values><Standard><Name>ulc01 Scenario02</Name></Standard>
<GameSettings><LevelFile>data\\levels\\dlc_01\\dlc01_scenario02.www</LevelFile></GameSettings></Values></Asset>
<Asset><Template>CampaignMission</Template><Values><Standard><Name>Mission 1.1</Name></Standard>
<GameSettings><LevelFile>data\\levels\\campaign\\Chapter01.www</LevelFile></GameSettings></Values></Asset>"""
ADDON = """<Asset><Template>ScenarioMission</Template><Values><Standard><Name>Mission 01 - Tsunami Disaster</Name>
</Standard><GameSettings><LevelFile>addondata\\levels\\Scenarios\\Mission01\\AddOnScenario01.www</LevelFile>
</GameSettings></Values></Asset>"""


def world(*islands: str) -> str:
    return "<WorldConfig>" + "".join(f"<IslandConfig><Filename>{i}</Filename></IslandConfig>" for i in islands)


def tree() -> FakeTree:
    files = {
        "data/config/game/assets.xml": MISSIONS,
        "addondata/config/balancing/addon_01_static.xml": ADDON,
        "data/levels/scenarios/singleplayer/07.www": world("N_D14.isd", "data/levels/Islands/scenarios/sp07/n_d18.isd"),
        "data/levels/dlc_01/dlc01_scenario02.www": world("dlc01_scenario02_n_l17.isd"),
        "addondata/levels/scenarios/mission01/addonscenario01.www": world(
            "addondata/levels/islands/scenarios/mission01/u_a.isd"
        ),
    }
    for p in (
        "data/levels/islands/normal/n_d14.isd",
        "data/levels/islands/normal/ecomod/n_d14n.isd",
        "data/levels/islands/underwater/u_d04.isd",
        "data/levels/islands/campaign/chapter 01/n_s01.isd",
        "data/levels/islands/scenarios/sp07/n_d18.isd",
        "data/levels/islands/scenarios/sp07/n_spare.isd",
        "data/levels/dlc_01/dlc01_scenario02_n_l17.isd",
        "data/levels/islands/world events/world_event01/we1.isd",
        "data/levels/testworlds/merged_press/m.isd",
        "addondata/levels/islands/underwater/u_new.isd",
        "addondata/levels/islands/scenarios/mission01/u_a.isd",
        "addondata/levels/islands/third parties/t.isd",
    ):
        files[p] = b""
    return FakeTree(files)


def test_every_island_is_found_through_the_tree_and_grouped_by_folder_and_scenario():
    t = tree()
    paths = isd_islandlist.island_paths(t)
    assert len(paths) == 12 and any(p.startswith("addondata/") for p in paths)
    groups = isd_islandlist.island_groups(t, paths)
    by_stem = {p.rsplit("/", 1)[-1].removesuffix(".isd"): g for p, g in groups.items()}
    assert by_stem == {
        "n_d14": "Normal",
        "n_d14n": "Normal · Ecomod",
        "u_d04": "Underwater",
        "n_s01": "Campaign · Chapter 01",
        "n_d18": "Scenarios · Scenario07",  # the scenario's world places it
        "n_spare": "Scenarios · Scenario07",  # no world names it: its folder's neighbour does
        "dlc01_scenario02_n_l17": "Scenarios · DLC 01 Scenario02",
        "we1": "World events · World Event 01",
        "m": "Test worlds · Merged Press",
        "u_new": "Deep Ocean · Underwater",
        "u_a": "Deep Ocean · Scenarios · Mission 01 - Tsunami Disaster",
        "t": "Deep Ocean · Third Parties",
    }
    order = isd_islandlist.group_order(groups.values())
    assert order[:3] == ["Normal", "Normal · Ecomod", "Underwater"] and order[-1].startswith("Test worlds")


def test_only_the_pools_islands_have_a_conversion_and_the_rest_say_why():
    paths = isd_islandlist.island_paths(tree())
    dirs = ("data/levels/islands/normal/", "data/levels/islands/underwater/", "addondata/levels/islands/underwater/")
    why = isd_islandlist.convertible(paths, dirs)
    convertible = {p.rsplit("/", 1)[-1] for p, reason in why.items() if reason is None}
    assert convertible == {"n_d14.isd", "n_d14n.isd", "u_d04.isd", "u_new.isd"}
    assert "level's own island" in why["data/levels/islands/campaign/chapter 01/n_s01.isd"]
    twin = [*paths, "data/levels/islands/underwater/n_d14.isd"]  # a second island of a name the pool already holds
    assert isd_islandlist.convertible(twin, dirs)["data/levels/islands/underwater/n_d14.isd"]


def test_two_islands_of_one_name_never_share_an_edit_file():
    pool = "data/levels/islands/normal/n_s01.isd"
    level = "data/levels/islands/campaign/chapter 01/n_s01.isd"
    assert editorapp.edit_file2070(pool) == "2070_n_s01.json"  # as it always was
    assert editorapp.edit_file2070(level) == "2070_data-islands-campaign-chapter-01-n_s01.json"
    assert editorapp.edit_file2070("addondata/levels/islands/underwater/u_a.isd") != editorapp.edit_file2070(
        "data/levels/islands/underwater/u_a.isd"
    )
