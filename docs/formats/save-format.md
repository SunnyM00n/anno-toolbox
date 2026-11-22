# Anno 117 savegames (`.a8s`), and how to edit the player without loading one

This page is hand-written. It was read from the author's own saves (seven profiles of four kinds: manual, autosave,
session backup and campaign backup; game build of 17 Sep 2026). Every sample reads, and an unchanged save repacks
byte for byte. The **game accepts an edited save** when every part keeps its 12-byte tail ("Container"; the in-game
results are in `../engine-notes.md`, "Saves"). The reader and editor is
`src/annotoolbox/formats/savegame117.py` ("The tool").

Note: read on 2026-10-24.

## Where saves are

`<Documents>/Anno 117 - Pax Romana/accounts/<account id>/`

- `<profile>/` one folder per player profile (the name chosen when a game is started, e.g. `Marcia`). In it:
  `<name>.a8s` (manual saves), `Autosave <n>.a8s`, `Session Backup.a8s`, `Campaign Backup.a8s`. All have the same
  format.
- `accountdata.a8s` beside the profile folders is **not** a savegame. It is a plain FileDB v3 document (no archive,
  no compression) of account-wide state, with the root `GameManager`: `FirstPartyUnlockManager` (unlock states),
  `AccountManager` (settings, `AssetWatcher`, scenario data, counters), language, key bindings, `HallOfFameManager`,
  `PlaytimeManager` (play times, login count) and `Achievements`. It holds **no list of saves**: no file names,
  sizes, times or hashes. The game rewrites it on every start and exit, which is why its time stamp moves.
- In every sample the folder name, the profile name inside the save and the company name of the human player are the
  same string. Only `Session Backup.a8s` and `accountdata.a8s` sync to the cloud by default (a manual save can be
  switched to the cloud in the load list, below).

## The load list

There is no index: the list is built from the folders each time it is refreshed.

- Every `*.a8s` (and `*.a7s`) under the account folder is found. The folder it lies in is its profile group. The
  file's size and modification time come from the file system (the time sorts the list).
- The game then reads the save's `meta.a7s` and `header.a7s` (inflated as below). A save whose parts do not read is
  **dropped from the list without a message**. So is a save whose `meta.a7s` version is 1 or less, or whose version
  name is wrong.
- The entry's name is taken from the file name. The `CorporationSaveGameName` in `meta.a7s` is not shown: an
  autosave stores `Autosave ` there, without its number. The group's profile name is the
  `SavegameProfileDisplayName` of its newest readable save.
- Nothing in the list is compared with stored values: no size, time or checksum. A save copied into a profile folder
  under a new file name appears as a new entry once the list is refreshed (the game started, or the load screen
  opened again).

## Container

A save is an RDA V2.2 archive ([`../rda-format.md`](../rda-format.md), `rda.py` reads it) with four files, one block
each, in the order below. Nothing is compressed or encrypted at the archive level (block flags 0, stored size = size,
timestamps 0). Each file is a **zlib stream** (header `78 DA`, level 9) of a **FileDB v3** document
([`filedb.md`](filedb.md); `filedb.py` reads and writes it), followed by a **12-byte tail**:

| Bytes | Holds |
|---|---|
| zlib stream | the document, ending in its Adler-32 as usual |
| 8 | an empty zlib stream: `78 DA 03 00 00 00 00 01` |
| 4 | u32, little-endian: the size of the inflated document |

The game takes the **last four bytes as the inflated size** and inflates only the bytes before them. A part that
ends with the zlib stream (as `zlib.compress` writes it) loses its Adler-32 to the size field. The inflate then never
finishes, and the save is dropped from the load list. `zlib.decompress` ignores the tail, so a Python reader does not
notice it, but a writer must append it. All seven samples have it on all four parts.

| Part | Holds |
|---|---|
| `meta.a7s` (about 250 bytes) | what the load list needs first: `CorporationFileVersion` (u32, 75 in this build), `CorporationVersionName` (UTF-16, `Anno 117 - Pax Romana`), `LastModTime` (u64, Unix seconds), `CorporationSaveGameName` (UTF-16, the save's own name) |
| `header.a7s` | root `CorporationProfile`: the whole game setup (below) plus what the load list shows: `ID`, `PeerData`, `LastActiveSession`, `CorporationTime`, `SavedActivePlaytime`, `RequiredAccountUnlocks`, `SavegameHistory` (timestamp and build per game version that saved it), `TotalMoney` / `TotalPopulation` (history graphs), `WallTimeOnSave`, `CampaignChapter` |
| `gamesetup.a7s` | root `GameSetupManager`: the game setup alone |
| `data.a7s` (5 to 70 MB) | root `MetaGameManager`: the whole world state; its last child `GameSessions` holds the provinces |

The game setup (`header` and `gamesetup` carry the same fields): difficulty and every `Setup...` option,
`SavegameProfileDisplayName` (UTF-16), `ActiveDLCs`, `Pirates`, `ModActivationInfo` (`Mods`, `ModOptions`) and
`SetupSlots` with one `Slot` per player.

Nothing in the file is a checksum, hash or signature (beyond zlib's own Adler-32). The only size-like fields are the
RDA sizes, the inflated size at the end of each part and the FileDB tail. The FileDB tail's node count is only a
capacity hint to the reader (`filedb.write` counts 2 more in the sample `data.a7s` than the game did), so a patch
keeps the original value. The multiplayer checksums of a save are computed for a session, not stored in the file.

### Reading and writing

- Read: `rda.CRdaArchive`, inflate each file with `zlib.decompress`, parse with `filedb.read`. To read as the game
  does, check the tail: the bytes before the last four inflate completely, and the last four equal the inflated size.
- Write: each part is `zlib.compress(doc, 9) + 78 DA 03 00 00 00 00 01 + u32 len(doc)` (`savegame117.pack_part`). The
  archive is written with flags 0 (`rda.write_v22_stored`; `rda.write_v22` is the island layout, which compresses the
  files). Per file it holds the file data, then its 560-byte file header (UTF-16 path padded to 520 bytes, then
  offset, size, size, 0, 0 as u64), then the 32-byte block header. The first block's offset comes after the 792-byte
  preamble, `next` points at the next block, and the last `next` points at the end of the file. A repack of the
  unchanged parts (stream and tail) reproduces the file exactly. Fresh streams only differ in their bytes.
- The order of the parts, the file names and the preamble are all the reader needs. There is no index to update,
  neither in the save nor in `accountdata.a8s`.

## The human player

The player's look and name live in **three places** (all consistent in the seven samples). Values are u32 GUIDs of
assets in `assets.xml` unless noted.

| What | `header.a7s` / `gamesetup.a7s`: `SetupSlots/Slot[0]` | `data.a7s`: participant 41's `MetaParticipant` |
|---|---|---|
| Company name | `CompanyName` (**UTF-8**, no terminator) | `CustomCompanyName` = `[flag u8 = 1, name UTF-16]` |
| Profile name | `SavegameProfileDisplayName` (UTF-16, one level above the slots) | - |
| Colour | `ColorGuid` (template `ParticipantColor`) | `CustomCompanyColorGuids` = `[flag, GUID]` |
| Logo | `LogoGuid` (`PlayerLogo`) | `CustomCompanyLogo` = `[flag, GUID]` |
| Portrait | `PortraitGuid` (`Portrait`) | `CustomAvatarPortrait` = `[flag, GUID]` |
| Banner | `Banner/{LogoColor, BannerShape, BannerPattern, BannerBackgroundPattern, BannerStripes}` | - (not in the participant) |

- `Slot[0]` (`ID` 0) is the human. The other slots are rivals (`SecondPartyProfile` = their participant's GUID, no
  company name). Participant **41** (`Profile_Human_00`) is the human of slot 0 in a single-player save. A second
  human in co-op is `Profile_Human_01` (600069), and so on. The `MetaParticipant` sits in
  `MetaGameManager/MetaGameObjectManager/MetaObjectMap/#1` entries whose `guid` is the participant GUID.
- The `flag` byte says "use this custom value" (1) or "use the asset's default" (0). Rivals have all four flags 0.
- Choosable player colours are `ParticipantColor` assets with `IsPlayerColor` 1: 91304 to 91308, 91312, and the
  Hall-of-Fame colours 99365, 99366, 120095. Logos are `PlayerLogo` assets (92, 500487 and the `PlayerLogo` series,
  Hall-of-Fame, Twitch, DLC and Connect logos). Portraits are `Portrait` assets (the campaign, resident and character
  portraits, and the DLC portraits). Whether an asset is unlocked for the account is account state
  (`accountdata.a8s`, `FirstPartyUnlockManager/LockState`; an asset absent there has its default).
- Values seen: colours 91304, 91306, 91312, 120095; logos 106874, 106910, 106911, 110059, 121969, 125990, 172561;
  portraits 33070, 38959, 38962, 40396, 172559, 172560.
- The player name occurs nowhere else in a save (the save's own name in `meta.a7s` is a different string).

**Money.** A participant's balance is the **last i32 of `StrgLrg`** in its `MetaGameManager/EconomyManager/Pair`
(`ParticipantGUID`, then `MetaEconomy/StrgLrg`). That is the participant's meta storage (the layout of the other
words: "Storages" below). `MetaEconomy/Money` and `MetaMoneyManager` are income figures, not the balance. Between two
autosaves of one game, only that last word changed for every participant. With the balance of participant 41 raised
by 15,000,000 through a same-size patch, the save loaded with the new balance.

Note: the money edit was loaded in the game on 2026-11-06 (update 3.0).

## Storages (`StrgSmll`, `StrgLrg`)

Every store of goods in a save (a participant's meta storage and each island area's) is one attribute, `StrgLrg`
(amounts as u32) or `StrgSmll` (amounts as bytes). It holds a **sorted map from a good's GUID to its amount**. The
layout was decoded from the author's saves (read only: 31 saves, 117 build of 2026-11) by comparing the storages of
one game over time. `formats/storage117.py` reads it, and [`../save-editor.md`](../save-editor.md) shows it.

| Attribute | Size | Layout (little endian), N entries |
|---|---|---|
| `StrgLrg` | 8 N | N u32 **keys**, then N u32 **amounts** |
| `StrgSmll` | 2 N + 3 | the u32 first key, N-1 bytes of **gaps**, N bytes of amounts |

The keys are strictly ascending GUIDs. In `StrgLrg`, the first key is stored as it is, and each later one as the
previous key plus the stored word plus 1 (a stored 0 is the next GUID). `StrgSmll` does the same with one byte per
gap. The bytes never hold more than 255, so a gap that large would use `StrgLrg` (inferred, no sample has one). N
follows from the size. Examples (the money good is 1010017):

- The participant's meta storage of an early save, `[1010017, 20000]`: N 1, key 1010017, amount 20,000.
- Later: keys `[93770, 916246]`, amounts `[1, 66177]`: 93770, then 93770 + 916246 + 1 = 1,010,017. So the money is
  the amount of the **highest key**, which is why "the last i32 is the money" held. It stays the last as long as no
  key above 1010017 exists: a mod's goods in a higher GUID range would be behind it. The editor therefore reads the
  money by its key.
- An island's `AreaEconomy/StrgSmll` `7d 08 00 00 00 14 19`: first key 0x087D = 2173, one gap byte 0, amounts 20 and
  25 for goods 2173 and 2174.

Evidence, from 3,938 entries decoded over five saves:

- 3,860 keys are the GUIDs of `Product` assets of the game. The 78 others are the helper mod's own goods (GUID range
  2070001xxx) in the saves that had it.
- None was out of order.
- Every key of an island's storage is one the area's `StorageTrends` tracks.
- The amounts agree with the trend rings. `StorageTrends` points hold the stock as i32: the newest points of goods
  2132, 2068 and 2137 of one island were 147, 186 and 249, and the stock decoded 147, 186 and 249.

Where they sit: `AreaEconomy/StrgLrg` or `StrgSmll` is the **island's stock** (confirmed by the trends above).
`AreaEconomy/TradeStorage/StrgLrg|StrgSmll`, present in some areas, holds a second small map of goods with amounts
(32 bytes: four goods in a player's area; ten goods all at 250 in a rival's). That it is the goods held for trade is
**inferred from its name**, not shown. Amounts are whole units. Whether a unit is a tonne, in the game's terms, is
not checked. The meta storage also holds permits (goods such as 91372, "Guesthouse Permit") besides the money.

Not decoded: whether the game needs the two halves of `StrgLrg` to agree with anything else (the trend rings, the
areas' capacities). An edit of a stock is therefore not offered.

Note: decoded on 2026-11-14.

## Editing a save without loading it

1. Inflate the four parts, change the attributes in all three places, deflate **with the 12-byte tail** and repack
   (above). The edit is a splice: replace the attribute's bytes (a FileDB v3 attribute is `i32 size, u32 id, content
   padded to 8`), write the new size, and add the length change (a multiple of 8) to the two name-table offsets in the
   FileDB's 16-byte tail. Nothing else moves, and no checksum follows. Same-size values (GUIDs) need no more than
   the bytes. Unchanged parts can keep their original bytes.
2. **Edit in place:** write the result over the save under the same file name. Nothing else is updated: not
   `accountdata.a8s`, not a time stamp, not the save's own name. The file's new modification time moves it to the top
   of the list (setting it back keeps the order).
3. **Add a save:** write it into the profile's folder under a new file name ending in `.a8s`. The file name is the
   name in the list. Keep its `SavegameProfileDisplayName` the same as the folder's (the newest readable save names
   the group).
4. A rename of the **player profile** is more than the strings: the folder carries the name too, and the cloud slot of
   `Session Backup.a8s` is keyed by the CRC32 of it.
5. The game must not be running (it would overwrite the file with its own state on the next save). Cloud: the launcher
   may replace `Session Backup.a8s`, and any save switched to the cloud in the load list, with the cloud copy. Edit
   autosaves and manual saves that are not in the cloud, or switch the save's cloud option off first.

What the game was seen to check when it reads a save (code reading):

- the archive opens;
- each part inflates by the size in its tail;
- each part parses as FileDB;
- `CorporationFileVersion` is not above 75;
- the version name is `Anno 117 - Pax Romana`;
- the mods recorded in `ModActivationInfo` match the active ones (the "Conflicting Mods" question).

None of it involves the player fields.

## Verified

- Read side: pinned against the seven saves; `tests/formats/test_savegame117.py` builds its own saves with the synthetic
  FileDB documents.
- Write side: every sample repacks byte for byte, and the three edited saves the game accepted are reproduced byte
  for byte.
- In the game (`../engine-notes.md`, "Saves"): a part without its tail drops the save from the load list. With the
  tails, the name, colour, logo and portrait edited in all three places show in the list and in the loaded session.
  A profile name that differs from the folder is shown per save, and a new file in a profile folder is listed.
  Loaded, the session's player takes the profile side's values (header / game setup) where `data.a7s` differs. The
  editor writes all three places, so nothing depends on which one wins.

Note: the write side was verified on 2026-10-24.

## The tool

`src/annotoolbox/formats/savegame117.py` does the following:

- It reads a save with every part checked the way the game does (the tail, the inflated size). A part without its
  tail is an error, not a quiet repack.
- It edits the player in all three places, the save's name and a participant's money ("Money" above; a same-size
  patch, for which the scan of `data.a7s` ends behind the economy manager).
- It writes with the tails. A part that did not change keeps its stored bytes.

It patches the FileDB in place (`filedb.scan` / `filedb.patch`: new sizes, the two name-table offsets of the tail
moved by the change; tags carry no size). So a longer name works, and the rest of the document is untouched. A
changed value in `data.a7s` also sets the custom flag of its field. The scan of `data.a7s` ends at participant 41, so
even a 70 MB save is read in a moment.

```bash
anno-toolbox save show <file.a8s>
anno-toolbox save edit <file.a8s> -o <out file or folder> [--name N] [--profile-name N] [--color G] [--logo G] [--portrait G] [--save-name N]
anno-toolbox save edit <file.a8s> --in-place ...   # first copies the original to <file>.orig, unless that exists
anno-toolbox save edit <file.a8s> -o <out> --add-money 15000000   # or --money N; --participant G (default 41)
anno-toolbox save new --template <save> --out <.a8s file or profile folder> --save-name N [--name ...] [--overwrite]
```

`--name` is the company name and, unless `--profile-name` says otherwise, the profile name of header and game setup.
Never run it on a save the game has open, and never on `accountdata.a8s` (not a save).

The suite's Saves app ([`../save-editor.md`](../save-editor.md)) makes the same edits from a window. It reads the
save once from its bytes (`savegame117.read_bytes`) and writes the result as a new file beside the original.
