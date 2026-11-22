"""An island's pool registration: read from the assets, checked, written."""

from annotoolbox.editor import islandmeta

ASSETS = """<Assets>
<Asset><Template>RandomIsland</Template><Values><Standard><GUID>1</GUID><Name>a</Name></Standard><RandomIsland>
<FilePath>data/x/a.a7m</FilePath>
<IslandRegion>Roman</IslandRegion>
<IslandDifficulty>Normal;Hard</IslandDifficulty>
<IslandType>Normal;Starter</IslandType>
</RandomIsland></Values></Asset>
<Asset><Template>RandomIsland</Template><Values><Standard><GUID>2</GUID><Name>b</Name></Standard><RandomIsland>
<FilePath>data/x/b.a7m</FilePath>
<IslandRegion>Celtic</IslandRegion>
<IslandType>Decoration</IslandType>
<IslandPlaceholderSize>Medium</IslandPlaceholderSize>
</RandomIsland></Values></Asset>
<Asset><Template>Other</Template><Values><Standard><GUID>3</GUID></Standard></Values></Asset>
</Assets>"""


def test_registrations_and_the_values_the_game_uses():
    (a,) = islandmeta.registrations(ASSETS, "data/x/a.a7m")
    assert a.guid == 1 and a.values["IslandType"] == "Normal;Starter" and a.values["IslandPlaceholderSize"] == ""
    known = islandmeta.known_values(ASSETS)
    assert known["IslandType"] == ["Decoration", "Normal", "Starter"]
    assert known["IslandRegion"] == ["Celtic", "Roman"]


def test_a_change_the_game_would_not_take_is_named():
    known = islandmeta.known_values(ASSETS)
    assert not islandmeta.check({"IslandType": "Normal;Decoration"}, known)
    problems = islandmeta.check({"IslandType": "Volcano", "Colour": "red", "IslandPlaceholderSize": ""}, known, 320)
    assert any("Volcano" in p for p in problems) and any("Colour" in p for p in problems)
    assert any("Medium slots" in p for p in problems)
    assert islandmeta.slot_size(192) == "" and islandmeta.slot_size(384) == "Large"


def test_changes_are_written_into_the_asset():
    block = islandmeta.assets(ASSETS)[0]
    out = islandmeta.apply(
        block, {"IslandType": "Decoration", "IslandDifficulty": "", "IslandPlaceholderSize": "Medium"}
    )
    reg = islandmeta.registrations(out, "data/x/a.a7m")[0]
    assert reg.values == {
        "IslandRegion": "Roman",
        "IslandDifficulty": "",
        "IslandType": "Decoration",
        "IslandPlaceholderSize": "Medium",
    }
