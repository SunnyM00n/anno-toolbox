"""Stand-ins for the quest system's source: a small questconfig folder and the files around it."""

from __future__ import annotations


def quest_asset(template: str, guid: int, body: str, base: int | None = None) -> str:
    base_tag = f"<BaseAssetGUID>{base}</BaseAssetGUID>" if base else ""
    return f"<Asset><Template>{template}</Template>{base_tag}<Values><Standard><GUID>{guid}</GUID></Standard>{body}</Values></Asset>"


def ware_quest(guid: int, goods: str, place: str, extra: str = "", base: int | None = None) -> str:
    return quest_asset(
        "WareQuest",
        guid,
        f"<GetWares><WaresToGet>{goods}</WaresToGet></GetWares><Execution><ExecutionPlace>{place}</ExecutionPlace></Execution>"
        "<QuestText><QuestCategoryText>20</QuestCategoryText><QuestStoryText>21</QuestStoryText>"
        f"<QuestInfoText>22</QuestInfoText></QuestText>{extra}",
        base,
    )


TEMPLATES = """<Templates><Group><Name>Quests</Name><Template><Name>WareQuest</Name><Properties>
<Quest><Mission>GetWares</Mission><SolvedCounter>SolvedDeliveryQuests</SolvedCounter></Quest>
<Time><MaxTime>1800000</MaxTime></Time>
<Preconditions><TreatyState>Peace</TreatyState></Preconditions>
</Properties></Template></Group></Templates>"""

SCHEMA = """<Properties><Groups><Group><Name>Default</Name><Property><Name>Balancing</Name>
<ValueDefinition><DataType>Array</DataType><Name>RewardAmountModulator</Name></ValueDefinition></Property>
<Property><Name>QuestText</Name>
<ValueDefinition><DataType>Text</DataType><Name>QuestInfoText</Name></ValueDefinition>
<ValueDefinition><DataType>Text</DataType><Name>QuestStoryText</Name></ValueDefinition>
<ValueDefinition><DataType>Text</DataType><Name>QuestCategoryText</Name></ValueDefinition>
<ValueDefinition><DataType>Text</DataType><Name>GUID</Name></ValueDefinition>
<ValueDefinition><DataType>Text</DataType><Name>Name</Name></ValueDefinition>
<ValueDefinition><DataType>String</DataType><Name>Name</Name></ValueDefinition></Property>
</Group></Groups>
<DefaultValues><Balancing>
<RewardPercentages><A>200</A><B>150</B><C>100</C><D>50</D><E>25</E><F>5</F></RewardPercentages>
<RewardAmountModulator><Normal><MinPercentage>120</MinPercentage><MaxPercentage>140</MaxPercentage></Normal></RewardAmountModulator>
<PlayerCounterNames><Vehicles><Name>30</Name></Vehicles></PlayerCounterNames>
<SpecialQuests><StartTimeMin>3000</StartTimeMin><StartTimeMax>5000</StartTimeMax><IntervalMin>3600</IntervalMin><IntervalMax>6900</IntervalMax></SpecialQuests>
<StandardQuests><StartTimeMin>2400</StartTimeMin><StartTimeMax>3600</StartTimeMax><IntervalMin>2100</IntervalMin><IntervalMax>3600</IntervalMax>
<MaxRunningQuests><EarlyGame>2</EarlyGame><MidGame>2</MidGame><LateGame>3</LateGame><EndGame>4</EndGame></MaxRunningQuests></StandardQuests>
<RewardResourceAmount><Credits><Levels><EarlyGame>1700</EarlyGame><MidGame>1900</MidGame><LateGame>4500</LateGame><EndGame>5500</EndGame></Levels></Credits>
<Licences><Levels><EarlyGame>60</EarlyGame><MidGame>90</MidGame><LateGame>110</LateGame><EndGame>140</EndGame></Levels></Licences></RewardResourceAmount>
</Balancing></DefaultValues></Properties>"""

INDEX = """<Group><Groups><Group><Name>Third Parties</Name><Groups>
<Group><Name>Test</Name><GUID>111</GUID><GroupType>1</GroupType><FileName>data/config/questconfig/TestGiver.xml</FileName></Group>
</Groups></Group></Groups></Group>"""

GIVER_QUESTS = "<Group><Groups><Group><Name>Plain</Name><GUID>222</GUID><GroupType>9</GroupType><Assets>{}</Assets></Group></Groups></Group>".format(
    ware_quest(1001, "<Vegetables>6</Vegetables>", "PlayerHarbour", "<Quest><MaxCallCount>1</MaxCallCount></Quest>")
    + ware_quest(1002, "<Tea>3</Tea>", "ClientArk")
    + quest_asset("WareQuest", 1003, "<Time><MaxTime>600000</MaxTime></Time>", base=1001)
    + ware_quest(1004, "<Unknown>2</Unknown>", "PlayerHarbour")
    + quest_asset("ProtestQuest", 1005, "<Quest><QuestType>ProtestQuest</QuestType><Enabled>0</Enabled></Quest>")
    + quest_asset("Survey", 1006, "<Survey><SurveyID>3</SurveyID></Survey>")
    + ware_quest(1007, "<Wood>9</Wood>", "PlayerHarbour", "<Time><MaxTime>600000</MaxTime></Time>")
)
DAILY = "<Group><Groups><Group><Name>Daily</Name><Assets>{}</Assets></Group></Groups></Group>".format(
    quest_asset("MetaGameWareQuest", 2001, "")
)
REWARDS = "<Group><Groups><Group><Name>R</Name><Assets>{}</Assets></Group></Groups></Group>".format(
    quest_asset(
        "Reward",
        3001,
        "<Reward><RewardVariations><Item><Percentage>30</Percentage><GoldAmount>Normal</GoldAmount></Item>"
        "<Item><Percentage>70</Percentage><Pools><Item><RewardPool>3002</RewardPool></Item></Pools></Item></RewardVariations></Reward>",
    )
    + quest_asset(
        "RewardPool",
        3002,
        "<RewardPool><Products><Wood><ProductData><EarlyGame><Amount>8</Amount></EarlyGame></ProductData></Wood></Products></RewardPool>",
    )
)
GIVERS = """<AssetList><Groups><Group><Assets>
<Asset><Template>Pirate</Template><Values><Standard><GUID>9001</GUID><Name>PiratesMedium</Name></Standard>
<Quest><QuestGroups><Item><Group>111</Group></Item></QuestGroups><StatusQuestInterval>3300000</StatusQuestInterval>
<QuestRewards><Normal><RewardLink>3001</RewardLink></Normal></QuestRewards></Quest></Values></Asset>
<Asset><Template></Template><BaseAssetGUID>9001</BaseAssetGUID><Values><Standard><GUID>9002</GUID><Name>PiratesEasy</Name></Standard>
<Quest><StatusQuestInterval>4800000</StatusQuestInterval></Quest></Values></Asset>
</Assets></Group></Groups></AssetList>"""
GAME = """<AssetList><Groups><Group><Assets>
<Asset><Template>Mail</Template><Values><Standard><GUID>4001</GUID><Name>Welcome</Name></Standard>
<Mail><Icon>5</Icon><Content>23</Content><Sender>2330007</Sender></Mail></Values></Asset>
<Asset><Template>QuestRoamerUnit</Template><Values><Standard><GUID>30049</GUID><Name>Engineer</Name></Standard>
<KIModule><ModuleCall>RandomWalker(10)</ModuleCall></KIModule>
<Object><Variations><Item><Filename>data\\graphics\\units\\engineer.cfg</Filename></Item></Variations></Object></Values></Asset>
</Assets></Group></Groups></AssetList>"""

TEXTS = {
    20: "Delivery",
    21: '"We need seedlings"',
    22: "Deliver<br/>[QUEST_GETWARES]",
    23: "<p>Welcome</p>",
    30: "Ships",
}
GOODS = {"Vegetables": 501, "Wood": 502, "Tea": 503}

FILES = (
    ("questconfig/templates.xml", TEMPLATES),
    ("questconfig/properties.xml", SCHEMA),
    ("questconfig/quests.xml", INDEX),
    ("questconfig/TestGiver.xml", GIVER_QUESTS),
    ("questconfig/MetaGameQuests.xml", DAILY),
    ("questconfig/Rewards.xml", REWARDS),
    ("ai/thirdparties.xml", GIVERS),
    ("game/assets.xml", GAME),
)
