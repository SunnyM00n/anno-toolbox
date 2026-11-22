"""How the island lists are grouped (``editor.islandgroups``)."""

from annotoolbox.editor import editlists, islandgroups

ASSETS_1800 = (
    b"<Asset><Template>RandomIsland</Template><Values><RandomIsland>"
    b"<FilePath>data/sessions/islands/pool/moderate/moderate_s_01/moderate_s_01.a7m</FilePath>"
    b"<IslandRegion>Moderate</IslandRegion></RandomIsland></Values></Asset>"
    b"<Asset><Template>RandomIsland</Template><Values><RandomIsland>"
    b"<FilePath>data/dlc03/sessions/islands/pool/colony03_a01_01/colony03_a01_01.a7m</FilePath>"
    b"<IslandRegion>Arctic</IslandRegion></RandomIsland></Values></Asset>"
    b"<Asset><Template>RandomIsland</Template><Values><RandomIsland>"
    b"<FilePath>data/dlc06/sessions/islands/pool/colony02_s_01/colony02_s_01.a7m</FilePath>"
    b"<IslandRegion>Africa</IslandRegion></RandomIsland></Values></Asset>"
)


def test_group_rows_orders_the_groups_and_keeps_the_rows():
    rows = [("c", "x/c"), ("a", "y/a"), ("b", "x/b"), ("z", "")]
    grouped = editlists.group_rows(rows, lambda r: r[1].split("/")[0], order=("y",))
    assert [g for g, _ in grouped] == ["y", "x", "Other"]  # the order first, then alphabetical, Other last
    assert dict(grouped)["x"] == [("c", "x/c"), ("b", "x/b")]  # a row keeps its place in its group


def test_117_islands_group_by_province():
    paths = [
        "data/base/provinces/roman/islands/pool/roman_island_medium_03/roman_island_medium_03.a7m",
        "data/dlc01/provinces/celtic/islands/pool/celtic_island_small_01/celtic_island_small_01.a7m",
        "data/base/other/thing.a7m",
    ]
    groups = islandgroups.island_groups("anno117", paths)
    assert [groups[p] for p in paths] == ["Latium", "Albion", "Other"]


def test_1800_islands_group_by_region_asset_then_by_name_and_folder():
    base = "data/sessions/islands"
    paths = [
        f"{base}/pool/moderate/moderate_s_01/moderate_s_01.a7m",  # its RandomIsland asset says Moderate
        f"{base}/pool/moderate/moderate_s_01/moderate_s_01_river_01.a7m",  # no asset: the name's region
        "data/dlc03/sessions/islands/pool/colony03_a01_01/colony03_a01_01.a7m",  # the asset: Arctic
        "data/dlc06/sessions/islands/pool/colony02_d_09/colony02_d_09.a7m",  # not an asset; colony02 is Africa
        f"{base}/campaign/colony02/chapter03/campaign_colony02_x/campaign_colony02_x.a7m",  # the folder
        "data/dlc11/scenario03/sessions/islands/pool/scenario03_d_01/scenario03_d_01.a7m",
        f"{base}/pool/ggj_l_01/ggj_l_01.a7m",
    ]
    groups = islandgroups.island_groups("anno1800", paths, FakeTree(ASSETS_1800))
    assert [groups[p] for p in paths] == [
        "Old World", "Old World", "Arctic", "Enbesa", "Enbesa", "Scenario 03", "Other"
    ]  # fmt: skip
    assert set(islandgroups.island_groups("anno1800", paths, FakeTree(None)).values()) >= {"Scenario 03", "Other"}


def test_the_old_engine_groups_by_the_folders_under_islands():
    paths = [
        "addondata/levels/islands/north/n_l01.isd",
        "data/levels/islands/south/s_m02.isd",
        "data/levels/islands/campaign/chapter 01/c01.isd",
        "data/levels/islands/addon01/campaign/mis01_aztec_01.isd",
        "data/levels/loose.isd",
    ]
    groups = islandgroups.island_groups("anno1404", paths)
    assert [groups[p] for p in paths] == ["Occident", "Orient", "Campaign · Chapter 01", "Campaign", "Other"]
    assert islandgroups.island_groups("anno1701", paths[:1])[paths[0]] == "North"
    assert islandgroups.island_groups("anno2070", ["data/levels/islands/normal/ecomod/n_l01n.isd"]) == {
        "data/levels/islands/normal/ecomod/n_l01n.isd": "Normal · Ecomod"
    }


def test_2205_sessions_group_by_their_region():
    paths = [
        "data/sessions/session_moon_01/session_moon_01.a6m",
        "data/sessions/event_session_arctic_01/event_session_arctic_01.a6m",
        "data/sessions/meta/portrait_bot/portrait_bot.a6m",
    ]
    groups = islandgroups.island_groups("anno2205", paths)
    assert [groups[p] for p in paths] == ["Moon", "Arctic", "Other"]


class FakeTree:
    def __init__(self, assets: bytes | None) -> None:
        self.assets = assets

    def read_bytes(self, path: str) -> bytes:
        if self.assets is None:
            raise FileNotFoundError(path)
        return self.assets
