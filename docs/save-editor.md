# The Saves app: a savegame viewer and editor

The suite's **Saves** app finds the savegames of every Anno game on the computer. It shows what a save holds: the
game's version, the mods it was saved with, the players, the sessions and their islands, money, population and quests.
It shows this as a summary, as a tree of every part, and as forms. It lets you change the values whose layout is fully
known, and writes the result as a **new file next to the original**. It never writes over a save. The app is based on
the author's own saves (read-only copies) and the formats this repository already reads. The design and the checklist
are in `tool-suite.md` section 29.

**Nothing the app writes has been loaded by a game yet.** The 117 edits are the ones the `save` command makes, and
those were tried in the game (name, colour, logo and portrait on 2026-10-24, money on 2026-11-06,
[`formats/save-format.md`](formats/save-format.md)). A save written by the app itself was not loaded. The 2070 edit
was never tried. The list of what to check is at the end.

## Where each game keeps its saves

All saves are under the user's Documents folder (a moved Documents folder is followed). "Seen" means that saves of
that kind were on the author's machine on 2026-11-13 and the reader was run over them.

| Game | Folder under Documents | Files | Seen |
|---|---|---|---|
| Anno 117 | `Anno 117 - Pax Romana/accounts/<account id>/<profile>/` | `*.a8s` (manual saves, `Autosave <n>`, `Session Backup`, `Campaign Backup`); `accountdata.a8s` beside the profile folders is account state, not a save, and is not listed | yes: 33 saves of two profiles |
| Anno 1800 | `Anno 1800/accounts/<account id>/<profile>/` | `*.a7s` | no |
| Anno 2205 | `Anno 2205/accounts/...` | `*.a6s` | no (the folder holds only `config`, `fx`, `log`) |
| Anno 2070 | `ANNO 2070/Accounts/<account>/Savegames/` | `*.sww` | yes: one save |
| Anno 1404 | `Anno 1404 History Edition/`, `Anno 1404 HE/`, `Anno 1404/` (each searched, below them any `Savegames` folder) | `*.sww` | no |
| Anno 1701 | `Anno 1701 History Edition/`, `Anno 1701 HE/`, `Anno 1701/` (as 1404; also `Savegames_mp`) | `*.sww` | no |

The 1800, 2205, 1404 and 1701 rows are where the games are expected to write. The app searches those folders, and the
**Open a file...** button takes a save from anywhere. A 117 save group's profile is its folder. The load list of 117 is
built from these folders each time, with no index ([`formats/save-format.md`](formats/save-format.md), "The load
list"), so a new file in a profile folder is a new entry. The app writes next to the original, so a written 117 save
appears in the game's list under its new file name.

Two places keep copies that the app does not touch: Ubisoft Connect's cloud slots (117 syncs `Session Backup.a8s` and
`accountdata.a8s`; `engine-notes.md`, "Saves") and 2070's `AppData/Roaming/Ubisoft/ANNO 2070/Config`
(`SavegamePreview.jpg`, the last save's picture).

## The containers

Every save of these games is an RDA archive ([`rda-format.md`](rda-format.md)) of a few parts. What differs is how a
part is packed and which tree format it holds.

| Game | Archive | Parts | A part is | The document is |
|---|---|---|---|---|
| 117 | V2.2, nothing compressed at the archive level | `meta.a7s`, `header.a7s`, `gamesetup.a7s`, `data.a7s` | a zlib stream with the 12-byte tail (empty stream, inflated size) | FileDB v3 ([`formats/filedb.md`](formats/filedb.md)) |
| 1800 | V2.2 expected | the same four names expected | zlib; the tail is not checked (no sample) | FileDB, the version told by its tail |
| 2205 | an RDA archive expected | not known | zlib expected | FileDB expected |
| 2070 | V2.0, nothing compressed at the archive level | `game2.xml`, `save.sww`, `stats.xml`, `preview.jpg`, `minimap.jpg` | `ZZZZ`, u32 inflated size, a zlib stream that ends in a sync flush (no final block) | the binary tag tree (below) |
| 1404 | V2.0 expected (the same engine as 2070) | as 2070 expected | as 2070 expected | the binary tag tree expected |
| 1701 | a save may be an archive or a bare tree | - | `ZZZZ` packed or plain | the tag tree of the islands (`isd2070.parse`) or the binary one |

The app's reader does not trust the expectations. It opens any RDA archive and unpacks each part by what its first
bytes say (the 117 tail, a bare zlib stream, `ZZZZ`, stored). It reads a document by what it is (a FileDB tail, `BIN `,
a `<` tag tree, a JPEG). A part it cannot read is shown as bytes. A 1800, 2205, 1404 or 1701 save therefore opens as a
tree even though no sample pinned its layout. Only the games whose layout is pinned get a summary and forms.

### Anno 117 in detail

The container, the parts, the player's three places and the money are described in
[`formats/save-format.md`](formats/save-format.md). The list below is what the summary reads beyond that. It was read
from the author's saves (game build of 2026-11). All of it is read-only unless the next section lists it.

- **Version**: `meta.a7s` `CorporationFileVersion` (80 in the November build; the game refuses a version above its
  own), `CorporationVersionName`, `LastModTime`; `header.a7s` `SavegameHistory` (one entry per game build that saved
  this game: `Timestamp`, `Changelist`), `CreationTimeStamp`, `WallTimeOnSave`, `SavedActivePlaytime` and
  `CorporationTime` (milliseconds).
- **Mods**: `header.a7s` `CorporationProfile/ModActivationInfo`: `Mods` holds one `#1` per mod with `ModID` and
  `ModVersion` (UTF-8), `Options` one `#1` per option with `Option` and `Value`; `WasModded` is 1 once a mod was ever on.
  On load the game compares the list with the active mods and asks ("Conflicting Mods") when they differ.
- **DLCs**: `ActiveDLCs` (`count`, then `DLC` GUIDs), `RequiredAccountUnlocks`.
- **Game setup**: `DifficultyPreset`, `GameSeed`, `DefaultStartProvince`, and one `Setup...` tag per option holding
  `value/id` (an enum index, u16); shown as numbers, the enum names are not resolved.
- **Players**: `SetupSlots/Slot` (slot 0 the human: company name, colour, logo, portrait, banner, start session; the
  rivals by their `SecondPartyProfile`), and in `data.a7s` every `MetaObjectMap` entry with a `MetaParticipant` (its
  `guid` is the participant's GUID). Money per participant from `EconomyManager/Pair` (the last word of `StrgLrg`).
- **Load-list numbers**: `header.a7s` `TotalMoney` and `TotalPopulation` (20 bytes: the first i32 is the value the list
  shows; the four others were -1 in every sample and are not decoded).
- **Population**: `PopulationManager/MaxReachedPopulationCount/Data`: per participant (its GUID, then a `#1`) pairs
  of a population level's GUID (u32) and the highest count reached (f32); the summary shows their sum.
- **Quests**: `QuestManager`: per participant `QuestEntryHandler/QuestEntries` (the quest log), `StoryLines`
  (`CreatedComponents`, `EndedComponents`), `CampaignHandler` (`ActiveChapter`, `CampaignProgress` as a float 0..1,
  `SolvedComponentGUIDs`).
- **Sessions**: `data.a7s` `MetaGameManager/GameSessions`: per session a GUID and a `#1` with `SessionDesc`
  (`SessionGUID`, `MapGeneratorSettings/MapTemplateType`, `RandomSeed`) and `SessionData/BinaryData`, **a FileDB v3
  document of its own** (`SessionFileVersion`, `GameSessionManager`; 33 MB for the largest province of a 97 MB
  `data.a7s`). In it: `MapTemplate` (`Size`, `PlayableArea`, `Filename` of the template, one `TemplateElement` per
  island), `AreaInfo` (per island area, keyed by its area id: `CityNameGuid`, `OwnerProfile`, `WasEverOwned`,
  `AreaEconomy/StorageTrends` per good GUID, `TradeStorage`), `IslandManager` (`Island<n>` per island). A session's
  document is read when its node is opened in the tree or the summary asks for it (a whole read of the largest takes
  about two seconds, off the window's thread).
- **Goods per island**: `AreaEconomy/StorageTrends` lists each good the island tracks, with a ring of 120 points. The
  **stock** is `AreaEconomy/StrgLrg` or `StrgSmll`, a sorted map from a good's GUID to its amount. Some areas also hold
  a second one in `AreaEconomy/TradeStorage` ("trade storage"; the role is inferred from the name). The layout, the
  examples and the evidence are in [`formats/save-format.md`](formats/save-format.md), "Storages". The summary's
  Islands table has a **Stock** and a **Trade storage** column (the goods with an amount, named). A participant's meta
  storage holds the money and other goods (permits), shown as **Other goods held**. (Decoded on 2026-11-14.)

### Anno 2070 in detail

This was read from the author's one save (`Spielstand.sww`, 332 KB, a new endless game, 2026-11-01). The archive is a
V2.0 RDA with one file per block, nothing compressed or encrypted at the archive level, and every time stamp 0. The
archive's preamble (the 1,008 bytes after the magic that RDA readers skip) is not empty. At offset 520 it holds a u32
20 and 20 bytes of a value that is **not** SHA-1, SHA-256 or MD5 of the file, of any part, or of any part unpacked.
Apart from those 24 bytes, a repack of the five parts with `rda.write_v20` (flags 0) reproduces the file byte for
byte. The app copies the 24 bytes from the original into what it writes. Whether the game checks them is the first
thing the game must answer (see below).

A packed part is `ZZZZ`, the inflated size as u32, then a zlib stream (header `78 5E`) flushed with a sync flush. It
ends in `00 00 FF FF` and has no final block, so a reader must inflate without expecting the stream's end. Python's
zlib does not reproduce the game's bytes at any level. An unchanged part therefore keeps its stored bytes, and a
changed part is a fresh stream of the same shape.

The **binary tag tree** (`BIN `) is laid out as follows:

- After the magic come the nodes. A node is an int32 tag number (from 1) and an int32 length: -1 means a
  NUL-terminated text follows, a positive length means that many bytes, and 0 means no value. Its children follow, and
  every node is closed by an int32 -1 (a leaf too).
- After the last node come `MAP `, the tag count, and per tag a uint8 length (with the NUL), the name with its NUL and
  the int32 number. `END ` and the u32 offset of `MAP ` close the file.
- Values are the tag tree's text values
  ([`formats/anno1404-world-format.md`](formats/anno1404-world-format.md), "Container"): integers in decimal text, wide
  strings and lists as bytes.

`formats/bintree.py` reads and writes it byte for byte.

| Part | Root | What the summary reads |
|---|---|---|
| `game2.xml` | first the game's setup node, then the network fields | `FactionInfos/i` per player: `PlayerId`, `Type/PlayerType` (Human, Computer), `Profile` GUID, `Difficulty`, `Name` (UTF-16), `Team`, `Color` (ARGB as a signed int), `PortraitGUID`, `ArkGUID`; the game setup (`Seed`, `WorldSize`, `IslandSize`, `Fertilities`, `Resources`, `StartCredit`, `DifficultyPoints`, the victory conditions `VC...`), `BuildCounter` |
| `save.sww` | `SaveGame` | `WorldConfig` (`Width`, `Height`, `Islands`: per island `IslandConfig` with `IslandID`, `Name`, `Filename`, position and size); `m_GameEngine/m_Players/i` (`m_PlayerID`, `m_Name`, `m_ProfileGUID`, `m_Resource/Credits`); per island `ResidentLevels` (`m_ResidentCountPerLevel<faction><level>`: eight int32, one per player) and `Warehouse<player>` (`m_storage`, `m_productsMap`); `m_GameEngine/m_GameSpeed`, `m_CurPlayerID`, `m_QuestLog`; `m_GOPManager/m_GameTickCounter` |
| `stats.xml` | `GameStats` | `Version` (UTF-16, `1.00.00`), `PlayingTimeMilliSec`, `GameType`, `Inhabitants`, `SaveGameType`, `GoldAmount`, `SyncCheckSum` (not understood) |
| `preview.jpg`, `minimap.jpg` | pictures | shown |

**Money** is `m_Resource/Credits` of the player, in fixed point with ten fraction bits: the sample's `81913862` is
79,994.006 credits, and `stats.xml` `GoldAmount` holds the whole credits (79994) for the load list.

## Names for GUIDs and options

Where the save has a number (a GUID, a `Setup...` option's value, a city's text id), the summary stores it as a
`records.Ref` cell. The cell reads as the number, and `names.apply` gives it a label once the installed game's data is
open. The labels come from what the asset editor already reads (`assetedit.assetgame`: the asset index, the schema and
the texts). The index is the asset editor's own cache, `<workspace>/asset-cache` (`assetgame.open_installed`), so a
game that was indexed there opens at once here. The first time it takes about 15 s for 117, off the window's thread.
The table shows the reading with the author's 117 install (2026-11-14):

| Cell | Rule | Example (117) |
|---|---|---|
| a GUID | the asset's name from its text id (first of `Text/OasisId`, `Text/LineID`, `Portrait/PortraitName`, `MapTemplateType/Name`, then the asset's text under its own GUID, which is how 2070 keeps it), else the asset's internal name; shown as `name (guid)`, the bare number when the game has no such asset | 91304: "Lapiz Lazuli" style names where the asset has a text, else the internal name ("ParticipantColor01 Lapiz Lazuli (BLUE)"); 38962: "Marcia (38962)"; 41: "Profile_Human_00 (41)" |
| a setup option | the option's value is an id of the data set the schema gives the `DifficultySetup` property of that name (`SetupTaxes` -> `DCTaxes`: Spare 2, Medium 1, Plenty 0); shown as `item (id)` | `SetupTaxes` 1: "Medium (1)" |
| a text id | the line of the game's texts in the interface language (`assetgame.interface_language`: German for a German interface, else English; a line the German table lacks in English); the asset names above too | a city's `CityNameGuid` (an 8-byte text id, read as an i64) |
| a stock | each good named, with its amount | "Good Oats 186, Good Lavender 5" |

The numbers named are the DLCs, the difficulty preset, the start province, the players' colour, logo, portrait and rival
profile, the participants, the quests' participants, the sessions and their map template type, the islands' owner, city
name and goods, and 2070's players' profiles. Without an install folder in the settings, or for a game the asset editor
does not open, the numbers stay as they are. The window opens the names after the summary is shown (a job off its
thread) and then fills in the labels. It fills them in again when the sessions' islands arrive. Two things are **not**
named: a population level or a quest's GUID inside the tree (the tree shows bytes), and 2070's colours (stored as
colour values, not GUIDs).

## The other games

This part is built from documented formats and **synthetic samples**. No save of Anno 1800, 2205, 1404 or 1701 is on
the author's machine, so none of this was run over a real file. Every row below is a theory until one is.

| Game | What is read | From |
|---|---|---|
| 1404, 1701 (`.sww`: the plain tag tree or the `ZZZZ` packed one) | `WorldConfig`: the world's name and size, each island (name, file, position, size), `GameData/PlayerInfos` (type, profile, colour); both games' tag names (`Width`, `m_Width`, `Name`, `m_Name`, `Filename`, `m_GOPFilename`, ...) | [`formats/anno1404-world-format.md`](formats/anno1404-world-format.md), [`formats/anno1701-world-format.md`](formats/anno1701-world-format.md): a save is a world file with `m_IsSaveGame` set (the two shipped developer saves) |
| 1800 (`.a7s` archive of FileDB parts) | an outline of every FileDB document (version, the tags at its top and what they hold); when `header.a7s` has a `CorporationProfile`, 117's load-list rows (version, mods, setup) | [`formats/filedb.md`](formats/filedb.md); the layout is **assumed** to be 117's (117 descends from it: same part names, same load list) |
| 2205 (`.a6s`) | the outline only | [`formats/filedb.md`](formats/filedb.md); nothing is assumed of the layout |

All four stay read-only (no editable value), and the tree shows everything else. What a real save would add (the
economy of 1404 and 1701, the sessions of 1800) is exactly what these summaries cannot say.

Note: built on 2026-11-14.

## What can be written, and how

A value is offered for editing only when its bytes and every place it lives are known, and the write changes those
bytes and nothing else. Everything else is shown and stays read-only.

| Game | Editable | Written as | Tried in the game |
|---|---|---|---|
| 117 | the save's own name; the player's company name, profile name, colour, logo and portrait (all three places at once); the money of any participant; the emperor who reigns and each reputation with him ([`save-emperor.md`](save-emperor.md): `saveedit/emperoredit.py`, `emperor117.py`, `emperorzones.py`) | `formats/savegame117.py` (`set_save_name`, `set_player`, `set_money`): a splice of the FileDB attribute, the tail offsets moved, every part with its 12-byte tail, unchanged parts' stored bytes kept, the written file read back before it is renamed into place | with the `save` command: yes (2026-10-24, 2026-11-06); a save written by the app: no |
| 2070 | the human player's money | `formats/savegame2070.py`: the text value of `Credits` (credits times 1,024) and `stats.xml` `GoldAmount` re-encoded in the binary tag tree, the two parts packed afresh, the other three parts' bytes and the preamble's 24 bytes kept | no |
| 1800, 2205, 1404, 1701 | nothing | - | - |

- **Never over the original.** The app writes `<name> (edited).<ext>` beside the save it opened (`(edited 2)` and so
  on when that exists), and never replaces a file. The original is read once into memory when it is opened, so nothing
  holds it open while the game runs. A 117 file written into a profile folder is listed by the game as a new save.
- **Unchanged is byte for byte.** A save that is opened and written without a change, or with an edit to the value it
  already has, is the original's bytes. 117 keeps the original file, because the game leaves stale blocks in its
  archives: the author's 3.4 MB `Session Backup` holds 2.9 MB of them, and a rebuilt archive differs. 2070 keeps every
  part whose tree writes the document that was read. A **changed** 117 save is rebuilt compact: the stale blocks are
  dropped, and the archive's directory points only at the four parts. A changed 2070 part is packed anew. It is about
  14 % larger than the game's own zlib output (no zlib setting reproduces it), which is valid for a reader.
- **The name of the new file** is `<name> (edited).<ext>`, then `(edited 2)`. Editing an edited save counts on from its
  own number instead of nesting (`x (edited 2)`, not `x (edited) (edited)`). The file is reserved with an exclusive
  create before it is written, and is then written whole or not at all (`buildcache.write_atomic`, after the bytes were
  read back in memory). Two writes therefore never meet, and a failed one leaves nothing.
- **A value the form does not have is refused**, so a mistyped key does not write an unchanged copy. Money and GUIDs in
  the form are read in decimal (leading zeros and `,` are allowed; a GUID may be `0x...`), not by Python's base-0 rule,
  which rejects `0100`.
- **The thousands separator is the interface language's** (`edits.parse_value`). In English `1,000` is a thousand and
  a point is refused. In German `1.000` is a thousand: a point must have exactly three digits after it (`1.5` and
  `1.0000` are refused), and a comma is refused with a sentence that says it is the decimal sign. So the same text is
  never read as two different numbers without the form saying so.
- **A read can be stopped and replaced.** While a save is read, **Stop** at the right of the status line gives the read
  up: the save shown before stays the open one and stays writable. A save picked meanwhile takes the place of the one
  being read, and the status line says so ("reading B instead of A"). The read that was given up ends at its next
  step (the file, the summary, the values) and its result is dropped. A save picked while a file is being written is
  not opened: the status line says to open it when the write is done.
- **The game must not be running** when you load the written file. It saves over its own files only, but a running
  117 rescans the folder only when the load screen opens.

### What stays read-only, and why

- **Anything the game derives from something else.** Examples are a good in a 117 storage without the economy that
  feeds it, a population count without the houses, or an owner without the buildings. After such a change the stored
  state would disagree with itself. The storage layouts (`StrgSmll`, `StrgLrg` beyond the balance's last word) are not
  decoded anyway.
- **Game setup and difficulty.** They are copied into the sessions when the game starts. A changed `Setup...` value in
  the header alone would only change the load list.
- **The mod list.** Removing a mod from `ModActivationInfo` does not remove the mod's objects from the sessions. The
  game's own question on load ("Conflicting Mods") is the place to change it.
- **The other games' saves.** No sample pinned their layout, so they open as trees.
- **The tree.** It shows every value with its bytes and readings (integer, float, text) but edits none. A raw edit of a
  value whose meaning is unknown is exactly what this app does not offer.

## The window

- **Left: the saves.** A game box (every game) and the saves found in its folders, grouped by profile (117, 1800) or
  account, newest first, with size and time. **Open a file...** takes any other file; it is read as a save of the
  game shown in the box where that game writes the file's extension (1404 and 1701 write `.sww` as 2070 does), else
  as the game the extension belongs to. Opening reads the file once into memory off the window's thread, and the
  summary is then made, also off it. A save whose editable values do not read (a damaged `data.a7s`, a layout the
  reader does not know) still opens: its summary says why, the tree shows it, and the Edit tab is read-only.
- **Summary** tab: version and file version, the save's name and profile, times, the mods and DLCs, the players with
  their money, the sessions with their islands (owner, goods tracked), the game setup, population and quests, and what
  the reader could not read.
- **Tree** tab: every part of the archive and, inside, its document as a tree. The tree is made a level at a time as
  you open it (a 117 session is read when its `BinaryData` is opened). A value shows its size, its bytes and its
  readings. A filter finds nodes by name in what is read.
- **Edit** tab: the editable values of the game in a form, with the original next to each field. **Write a new save**
  writes the file next to the original (see above) and says where.
- **Mods** tab: the mods this save needs (117, 1800). It shows each mod's ID and version, and whether the installed
  mods have it: installed in the same version, in another version, or missing. The installed mods are the mod loader's
  folders of the game, as the Mods app finds them. **Show in the Mods app** opens the Mods app on that game. The app
  only reads the mods; it never switches a mod on or off.

Every read and write runs off the window's thread (`suite.background`). The window shows "reading..." and stays usable.

## What to check in the game (the owner's list)

- [ ] 117: a save written by the app without a change loads (it is the original's bytes, so this checks only the
  listing under the new name).
- [ ] 117: a save with a changed company name, colour, logo, portrait and save name, written by the app, is listed
  and loads with them (the `save` command's result, now from the app).
- [ ] 117: a save with changed money for the human and for a rival loads with both balances.
- [ ] 2070: a save written without a change: the file is the original's bytes, so it must load (a control).
- [ ] 2070: a save with changed money is listed with the new money in the load list (`stats.xml`), loads, and the
  player has the new balance. If it is refused or the list shows it as damaged, the 20-byte value in the archive's
  preamble is a check over the content, and 2070 edits stay off until it is understood.
- [ ] 1800, 2205, 1404, 1701: a save of each opens as a tree, its parts are read, and the summary says what its note
  says. There is no sample on the author's machine. A wrong summary here is a wrong guess about the layout, so please
  report it.
- [ ] 117: the Islands table's **Stock** of an island you know (your capital's goods) matches the in-game stock. If it
  does not, "AreaEconomy/StrgLrg is the stock" is wrong for that save.
- [ ] 117: the **Trade storage** column: what are those goods in the game (the role is only inferred)?

## Status

- Built: the finder, the reader of every container above, the 117 and 2070 summaries, the 117 and 2070 edits, the mods
  list and the window. Also built: names for GUIDs, options and text ids ("Names for GUIDs and options"), in the
  interface language; the decoded 117 storages and the goods of every island (`formats/storage117.py`,
  `tests/test_saveedit_names.py`); and the summaries of the other games ("The other games", synthetic only). The three
  archive readers are one read (`RdaArchive.read_all`), each with its own checks. The fixes of an audit are recorded in
  `audit-2026-11.md`, "The Saves app".
- Tests: synthetic saves (`tests/test_saveedit.py`), the window offscreen (`tests/test_saveedit_window.py`), and
  read-only over the saves found on the machine when the game's variable is set (`tests/test_saveedit_installed.py`:
  the originals are only read; the unchanged write goes from a copy into the test's own folder).
- Run on the author's machine: 117 (31 saves found; the newest, oldest and largest read, the islands of a 97 MB
  `data.a7s`) and 2070 (one save) pass. 1800, 2205, 1404 and 1701 skip (no saves). `tests/test_saveedit_installed.py`
  also decodes and names the stocks of the newest save against the installed 117 (the storage evidence is in
  `formats/save-format.md`).
- In the game: nothing yet (see the list above).
- Not built: editing a stock (the trend rings must agree, and they are not decoded), and anything read from a real
  1800, 2205, 1404 or 1701 save.

Note: built on 2026-11-13 and 2026-11-14; the run on the author's machine was on 2026-11-13.
