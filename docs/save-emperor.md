# Anno 117 saves: the emperor and the reputation with him

Where a 117 save (`.a8s`) keeps the emperor who reigns and every participant's standing with him, what depends on them,
and what the Saves app may change in a copy.

It was read from three sources:

- the author's own saves (30 saves of two profiles, a campaign and an endless game, game build 3.0 of November 2026;
  only read)
- the game's asset list
- for two rules, the game's behaviour

The container, the parts and the splice are in [`formats/save-format.md`](formats/save-format.md).

Note: read on 2026-11-15; the completed campaign and the zone change on 2026-11-16.

## Where it lives

Everything is in `data.a7s`, under `MetaGameManager`, except the start emperor of the setup.

| What | Where | Layout |
|---|---|---|
| The emperor who reigns | `ParticipantManager/ActiveEmperor` | u32, the emperor's participant GUID. Left out when no emperor reigns (a game set up without one) |
| The start emperor | `header.a7s` `CorporationProfile/SetupStartEmperor` and `gamesetup.a7s` `GameSetupManager/SetupStartEmperor` | u32; the setup's choice, used when the game starts; left out for "no emperor" |
| Each emperor | a `MetaGameObjectManager/MetaObjectMap/#1` entry whose `guid` is the emperor and which holds a `MetaEmperor` tag | the entry also holds `MetaParticipant`, `MetaDiplomacy`, `IslandOwner` and the like, as every participant's |
| The reputation book | `MetaEmperor/ReputationMap` of each emperor | pairs: a u32 key (a participant's GUID), then a `#1` record (below). Only the emperor who reigns keeps entries; the others' books were empty in every sample |
| The emperor's own state | `MetaEmperor`: `PendingConsulDecisions`, `PendingRebellionDecisions`, `PendingEmperorChangeDecision`, `PendingRebellionCapitulationStoryline`, `PendingZoneChangedStoryline`, `PossibleConsuls`, `LastRebellionLoyalists`, `LastRebellionRebels`, `LastRebellionEndTime`; `RebellionWaveHandler` while a rebellion runs | empty in every sample but one, a save during a rebellion |
| The relation | `DiplomacyManager/Relations`: a record with `FirstParticipant` (the emperor), `SecondParticipant`, `State/id`, `SubStates` | `State/id` is a `DiplomacyState` data id ([`formats/enums-117.md`](formats/enums-117.md)): Peace (1) between every emperor and the player and the rivals in all samples. The game offers no diplomatic action with the emperor |
| Conditions | `ConditionManager/Conditions`: conditions of type `ActiveEmperor` (188) with `EmperorParticipant` | the emperor the condition asks for (from its asset: about 200 triggers of the game name Calidus). They name an emperor, not the one who reigns |
| The campaign | `QuestManager/CampaignHandler`: `ActiveChapter`, `CampaignProgress`, the component lists, `DeadEmperors` | every child empty outside the campaign; how a completed one is marked: "The campaign" below |

A record of the book:

| Field | Layout | Holds |
|---|---|---|
| `CurrentReputation` | i32 | the reputation, -100 to 100; left out at 0 |
| `ActiveZone/id` | u16 | the zone (`ReputationZones`: Hostile 0, Unruly 1, Casual 2, Effort 3, Challenge 4); left out for Casual |
| `SpecialState/id` | u16 | `ReputationSpecialState` data id: Rebellion 1, ProConsul 2, Consul 3, RebellionPending 4, ConsulPending 5, ProConsulPending 6, EmperorChangePending 7; empty for none |
| `StartedEffects` | 8 bytes per effect | the effects the zone or state started (instances, not GUIDs) |
| `UnlockedAssets` | u32 GUIDs | what the zone or state unlocked: the zone's `AssetUnlocks` of the emperor's asset |
| `AmountOfCompletedEmperorContracts` | i32 | the contracts done for the emperor |
| `ActiveEmperorQuest`, `LastFinishedEmperorQuest/value` | a quest, a GUID | the emperor's request under way, the last one |
| `NextEmperorQuestTimestamp`, `NextPassiveReputationGainTimestamp` | u64, ms of game time | when the next request and the next passive change come |

The book holds the human players and the rivals.

The emperor's name and portrait are not in the save. They are the emperor asset's (`Text/OasisId`,
`Participant/Portrait`), so they follow the GUID.

What the samples show:

- The campaign (`Marcia`, 27 saves):
  - The start emperor is 31721 (Lucius). He reigns in act 1 (`ActiveEmperor` 31721) and is in `DeadEmperors` from act 2
    on. From then on `ActiveEmperor` is 31637 (Calidus).
  - There are three emperor entries (31637, 31721, 31722). Calidus' book holds the player (41) and the rivals.
  - The player went from 11 to -100. At -100, in one autosave, the record was in the Rebellion state, its unlocks the
    Rebellion state's, and Calidus had a `RebellionWaveHandler`. The later saves have neither.
- The endless game (`Anti Cithara`, 3 saves, set up with no emperor): no `ActiveEmperor` and no `SetupStartEmperor`;
  two emperor entries (31637, 31722) with empty books.
- The zone's unlocks are in the record. Calidus' Hostile zone unlocks 87339, 91169 and 180396, and a record in the
  Hostile zone holds exactly those. One in the Casual zone holds 82876.

Note: read on 2026-11-15. The autosave with the rebellion is of 2026-11-15.

## The campaign

A campaign save is one whose `CampaignHandler` holds anything; outside a campaign every child is empty. Its stage is
`ActiveChapter/id`, a `QuestCampaignChapter` data id ([`formats/enums-117.md`](formats/enums-117.md)): Act 1
chapters 1 to 3 are 0, 8, 9; Act 2 chapters 4 to 6 are 1, 10, 11; Act 3 chapters 7 to 9 are 2, 12, 13; and
**`CampaignCompleted` is 7**. The header's `CorporationProfile/CampaignChapter` holds the same id (for the load
list).

A completed campaign goes on as free play in the same session. Nothing else marks it: there is no flag of its own, and
`CampaignProgress` stays where the story left it.

The test campaign over its saves:

| Saves | `ActiveChapter` | `CampaignProgress` | Story parts solved / under way (`ActiveComponentGUIDs`) / still to come | `DeadEmperors` | Reigns |
|---|---|---|---|---|---|
| Autosave 0 (2026-09-12) | 0 (Act 1, chapter 1) | 0.005 | 4 / 21 / 816 | - | Lucius |
| Autosaves 24, 48 | 8 (Act 1, chapter 2) | 0.459 | 386 / 16 / 439 | - | Lucius |
| Autosave 72 | 1 (Act 2, chapter 4) | 0.697 | 586 / 0 / 255 | Lucius | Calidus |
| Campaign Backup | 11 (Act 2, chapter 6) | 0.697 | 586 / 0 / 255 | Lucius | Calidus |
| Autosave 94 to the newest, `Marcia.a8s` (2026-11-15) | 7 (completed) | 0.697 | 586 / 0 / 255 | Lucius | Calidus |

So the app takes a campaign as **completed** when `ActiveChapter` is 7 and no story part is under way
(`ActiveComponentGUIDs` empty).

The 255 parts "still to come" in the completed saves were never started (no Act 3 chapter appears in any save). They do
not run. The campaign's other records (`PowerStruggleReasons`, the act 2 snapshots) are its history and stay as they
are.

Note: the table was read on 2026-11-16.

## The game's assets

- **Emperors**: the assets of template `Participant 3rdParty Emperor`: 31637 Calidus, 31722 Julia, 31721 Lucius (the
  campaign's first act). `DifficultyBalancing/AvailableEmperors` lists them for the setup (31637 60 points, 31722 40,
  31721 none).
- **Zones per emperor**: `Emperor/ZoneConfiguration` holds, per zone, the effects it starts (`ActiveEffects`) and the
  assets it unlocks (`AssetUnlocks`). `Emperor/SpecialStateConfiguration` holds the same per special state. The three
  emperors have the same zone configuration. Calidus and Julia also have the same special states, and Lucius' differ.
- **The zones' bounds**: `ReputationFeature` 38180 (`ReputationZones`):

| Zone | Lower bound | Lower inner bound | Upper inner bound | Upper bound |
|---|---:|---:|---:|---:|
| Hostile (0) | -100 | -100 | -65 | -65 |
| Unruly (1) | -64 | -50 | -30 | -30 |
| Casual (2) | -29 | -15 | 25 | 35 |
| Effort (3) | 36 | 36 | 70 | 84 |
| Challenge (4) | 85 | 85 | 100 | 100 |

- **Passive change**: each rival's `PassiveReputationGeneration/ReputationConfigByEmperor` has steps for
  -100 to 100 per emperor.

## The game's rules (read from how the game behaves)

- **The zone of a reputation** depends on the zone the record is in. The zones are tried from Hostile up, and the
  first that takes the value wins. When none does, the record keeps its zone.

  A zone takes the value when the value is at most its upper bound and at least its lower bound. There are two
  exceptions for the zones next to the current one:
  - Coming from above (the current zone is the next one up), the value must be at most the zone's **upper inner**
    bound.
  - Coming from below (the current zone is the next one down), the value must be at least its **lower inner** bound.

  So a record in the Unruly zone stays there at -27 (the Casual zone takes it only from -15 up), and one in the Casual
  zone stays there at -23. The samples agree: every record left out of the zone (Casual) is between -29 and 35, and
  the Unruly records at -27 and -23 had risen there from lower values in the same zone.
- **A new record** (the emperor's first look at a participant) starts at reputation 0 in the Casual zone. The zone's
  effects and unlocks are applied at once, and the timers of the next request and the next passive change are set.
- **An emperor change** in the game goes through the participant manager. It does four things:
  - It ends the old emperor's reign (this part was not read).
  - It sets the new emperor.
  - It creates his participant if needed.
  - It opens a record for each human and rival in his book.

  In the game it happens after a won rebellion (the special state EmperorChangePending and the `ReputationFeature`
  decision 62102). The reputation after it is `ReputationAfterEmperorChange` (former rebel 36, former loyalist -65).

## What changes together, and what the Saves app changes

In the Edit tab the emperor is a list of the emperors the save holds, and each reputation row names its participant.
Both show the installed game's names once they are read (the emperor asset's `Text/OasisId`), with the GUID beside
them. Before that, or without an installed game, they show the numbers alone.

Note: the names were added on 2026-11-17.

**The reputation** of a participant is the `CurrentReputation` of his record in the book of the emperor who reigns.
The zone, its effects and its unlocks follow from it, and the save holds them as instances (effects started,
assets unlocked). The app changes the reputation when two conditions hold:

- The record stores its reputation (a record that leaves it out, at 0, is not offered), and it is in no special state
  (a rebellion or a consul title has its own rules).
- The new value is between -100 and 100 (the zones' outer bounds).

When the game's own rule above keeps the record in its zone, the change is a same-size splice of four bytes and
nothing else changes. When the value crosses a zone border, the zone changes with it as the game changes it ("A zone
change" below).

**The emperor** is `ParticipantManager/ActiveEmperor`. What depends on it is the book. Every record, with the effects
its zone started and the assets it unlocked, belongs to the emperor who reigns. A book left with an emperor who no
longer reigns would keep those effects with nobody to end them.

The app therefore changes the emperor and **moves the book** to the new emperor in one write, and only in the plain
case:

- The save stores `ActiveEmperor` (a game without an emperor is refused: the value would have to be added).
- It is not a campaign that is still running. While it runs, its story picks the emperors (the refusal names the
  chapter and the story parts under way). After a **completed** campaign ("The campaign" above) the game is free play
  and the change is offered as in an endless game.
- The new emperor is an emperor of the game (template `Participant 3rdParty Emperor`) and has his entry in the save,
  not deactivated, not one who died in the campaign (`DeadEmperors`), with an empty book.
- Both emperors hold nothing but the book (no decision pending, no rebellion, no consul in view), and no record is in
  a special state or has a request under way.
- Both emperors have the same zone configuration in the game's assets, so the effects and unlocks the moved book holds
  are exactly the ones the new emperor's zones would have started.

The move is a byte move. The book's records leave the old `ReputationMap` and become the new one's, and
`ActiveEmperor` gets the new GUID. FileDB tags carry no size, so the document keeps its size and nothing else moves.
The written save is read back before it is kept.

What the app leaves as it is:

- the start emperor of the setup (a setup value, read when the game starts)
- the `ActiveEmperor` conditions (each names the emperor it asks for)
- the relations (Peace, and the game offers no action with the emperor)
- the emperor's history (`LastRebellion...`)
- the zones and the special states

**Refused**, because the save does not show what the game would do:

- a special state
- an emperor for a game without one
- an emperor change while a campaign runs or with anything under way
- an emperor who died in the campaign

The reputation edits do not depend on the campaign. They are offered in every save, running campaigns included, with the
checks above.

On the completed test campaign (`Marcia.a8s` and Autosave 448) every other check passes for Julia:
`ActiveEmperor` 31637 is stored, Julia's entry is there with an empty book, no emperor holds anything but his book,
no record is in a special state or has a request, and the installed game gives Calidus and Julia the same zones.
Lucius is refused (he died in the campaign). A write to a copy (Calidus to Julia, the player's reputation -89 to -60
in the Hostile zone) reads back with Julia reigning and Calidus' book empty.

Note: the completed campaign was checked on 2026-11-16.

## A zone change

Read from how the game behaves and from the test save where a zone changed between two autosaves:

- the player from Unruly to Hostile (Autosave 426 to 430)
- a rival from Unruly to Casual (341 to 367)
- a rival from Casual to Unruly (391 to 415)

Note: read on 2026-11-16.

**What the game does.** When a reputation leaves its zone by the rule above, the game:

1. It relocks every asset in the record's `UnlockedAssets` and empties the list.
2. It ends the effects in `StartedEffects` and empties the list.
3. It starts the new zone's effects (`SZoneEffects/ActiveEffects` of the emperor's asset) and unlocks its assets
   (`AssetUnlocks`). It keeps their ids and GUIDs in the two lists, in the asset's order. A special state comes first
   and, with `DeactivateZoneEffects`, keeps the zone's effects off (special states stay refused here).
4. It tells the trade of the participant when one of the two trade-block assets (`ActiveTradeFeature` 140115) changed.
5. For a human only, and only during play, it does two things:
   - It shows the zone's notification (`NotificationFromLowerZone` or `...FromUpperZone` of the `Emperor` template).
   - It starts the zone-entered story line of `ReputationFeature` (`ZoneEnteredFromBelowStoryline` /
     `...FromAboveStoryline`: "Zone Change ... Upwards/Downwards", a decision screen that says the zone changed). Its
     precondition asks for a running campaign.

**What the save shows** (every point seen in all three transitions):

| Where | Before (Unruly, player) | After (Hostile) |
|---|---|---|
| the record's `ActiveZone/id` | 1 | 0 (the Casual zone leaves it out) |
| `StartedEffects` | `0x80000000000007d4` | `0x8000000000000811` (none in the Casual zone: an empty attribute) |
| `UnlockedAssets` | 87340, 91168 | 87339, 91169, 180396 |
| `MetaEffectManager/Effects` | an entry `{Guid 82843, Id 0x8...7d4, TargetParticipant 41, EndTimestamp empty, Scope/value/id 12}` | that entry gone, a new one `{Guid 82889, Id 0x8...811, ...}` at the end (the list is sorted by id) |
| `MetaEffectManager/NextEffectIndex` | 0x811 | counted on: the new effect's id is the index with bit 63 set |
| `ParticipantUnlockManager/UnlockData/Data/<participant>/LockState` | 87339, 91169, 180396 hold 0 (locked); 87340, 91168 have no entry | 87340, 91168 hold 0; 87339, 91169, 180396 have no entry |

A `LockState` entry is the asset's GUID, then `#1/value/value/value` (u32). As the game does it, a relock writes 0.
An unlock removes the entry (or writes 2 for an asset whose `Locked` property keeps it; no zone asset of the game is
one).

The zone effects are all of scope Meta (`EffectScope`, `Scope` data id 12; "settling cost" buffs). They live only in the
effect manager: no session document of the two saves searched (Autosaves 426 and 430) holds their ids or GUIDs.

**What the app does** for a value across a zone border, all in one write:

- It writes the zone by the game's rule (`ActiveZone/id` written, or left out for Casual).
- It removes the old zone's effect entries from `Effects`. It appends the new zone's, with the ids from
  `NextEffectIndex` (bit 63 set), counts the index on, and makes `StartedEffects` the new ids.
- It makes `UnlockedAssets` the new zone's. In the participant's `LockState`, the old zone's assets get their entry with
  0 back (those the new zone also unlocks stay without), and the new zone's lose theirs.
- It writes the reputation.
- It reads the result back: the record, the effects and the unlocks must be what the new zone leaves.

It checks before (and refuses with the reason) that the save holds what the old zone left:

- The started effects are the zone's in the effect manager, aimed at the participant.
- The unlocked assets are the zone's and have no entry in the unlock state.
- No asset the new zone unlocks has a state other than 0.
- The next effect index lies above every effect id.
- Every effect is of scope Meta.
- The installed game has the emperor's zone configuration.

The zones' effects, unlocks and scopes come from the installed game's assets (`Emperor/ZoneConfiguration`, the
effects' `EffectScope`).

**Not done** (the game does it only during play): the notification, the zone-entered story line (a decision screen in
a campaign) and the trade refresh. The trade blocks are read from the unlock state, which the app writes.

On the test save, the three transitions made by the app give the game's own record, effect and unlock state. The
effect ids differ by the effects the game started in between.

## What only the game can confirm

- [ ] A changed reputation within the zone loads, the reputation screen shows the new value and the same zone, and the
  next change moves on from it.
- [ ] A changed emperor (endless game, Calidus to Julia or back) loads: Julia reigns (her name and portrait), the
  player keeps his reputation and zone, and the zone's effects and unlocks stay. A change of zone afterwards ends the
  old effects and starts Julia's.
- [ ] After the emperor change, the next request comes from the new emperor, and no trigger of the old one fires.
- [ ] The same after a completed campaign (`Marcia`): the free play goes on under Julia, and no campaign story
  starts again.
- [ ] A zone change made by the app (the player from Hostile -89 to Casual 0) loads: the reputation screen shows the
  Casual zone, the settling cost is the Casual one (the Hostile surcharge gone), and the Hostile blocks (trade with all
  traders, the two biggest monument events, visitors) are lifted, the Casual item reroll is offered.
- [ ] Up a zone (Casual to Effort): the settling discount applies and the Effort unlocks are there; the next zone change
  in play ends the app's effect as if the game had started it.

## Status

- Built:
  - the reading, both edits and their checks (`saveedit/emperor117.py`)
  - the rows of the Saves app's Edit tab and the check against the installed game's assets (`saveedit/emperoredit.py`,
    taken by `saveedit/edits.py` as its own rows)
  - the zone change (`saveedit/emperorzones.py`)
  - tests on synthetic saves: `tests/saveedit/test_save_emperor.py`, `tests/saveedit/test_save_emperor_zones.py`
- In the game: nothing yet (the list above).
- Not built: a special state, an emperor for a game set up without one, an emperor change while a campaign runs, the
  notification and story line of a zone change.
