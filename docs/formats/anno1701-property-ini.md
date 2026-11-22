# Anno 1701's asset data: `property.ini`, the balancing table, `gamevalues.xml`, `icons.xml`

This page describes what Anno 1701 keeps where 117 and 1800 have an asset file. The files are read and written by
`src/annotoolbox/sources/props1701.py` (`parse_props`/`render_props`, `parse_table`/`render_table`,
`game_values`/`set_game_values`). The asset editor shows them as assets (`assetedit/game1701.py`,
[the guide](../asset-editor.md#anno-1701)). Each reader writes its file back byte for byte
(`tests/assetedit/test_game1701.py`, on the install).

There is no schema file: the names of the values are not in the data. The editor names a value by its number
(`V100`) and a property by its number (`P7`). What a number means is open work, kept in the table at the end.

Note: read from the History Edition on 2026-11-13. Every number below was counted over the shipped files.

## `data/config/game/property.ini`

Text, Latin-1, CRLF lines, 241,084 bytes, 497 objects, 730 property blocks. Four kinds of line:

| Line | Meaning |
|---|---|
| `[OBJECT]` | starts an object; always followed by `GUID <n>` and `NAME <text>` (GUIDs 1055 to 40380, each once) |
| `[PROPERTY]` | starts a block of the object; always followed by `PROPID <n>` |
| `PROPID <n>` | the property: 0 (187 blocks), 1 (10), 3 (76), 7 (255), 9 (5), 10 (34), 11 (38), 12 (57), 13 (68); no object has one twice |
| `VALUEID <id> <type> <count> <text>` | a value of the object (before its first `[PROPERTY]`) or of the block above it; `<text>` is the rest of the line (empty after a trailing space) |

`<count>` is 1 in all 9,071 lines. `<type>` is 6 (1,092 lines, always a whole number: ids 100, 80001, 100001, 10002,
110001, 110008 and 40001) or 1 (7,979 lines, text, including numbers). Each of the 55 value ids has one type. The
object's own values are ids 100 to 104 (100: a number such as 512 or 98304; 101 and 104: a call such as `Twister()`
or empty; 102 and 103: 0). The value ids of a block are numbered after its property (property 1: 20001, 20002; 3:
40001 to 40008; 7: 80001 to 80009; 9: 100001; 10: 110001 to 110007; 11: 110006 to 110011; 12: 120010; 13: 130001,
130002; 0: 10002 to 10017). Where a value names a path (`data/graphics/objects/special/twister.xml`), it is a game
file.

## `data/project/balancing/balancingtabelle.txt`

Tab separated, Latin-1, CRLF, 448 lines: a header of 73 German column names (`GUID`, `Name`, `Trefferpunkte`,
`Einflußradius`, `Betriebskosten (Geld)`, `Baukosten Holz`, `Produktionsmenge (pro Minute in kg)`, ...) and 447 rows
by GUID. 353 of the GUIDs are objects of `property.ini`. The table has 94 others, among them the streets 0 to 9. A
row may stop before the last column (the editor fills it up). Numbers use a decimal comma. A `.xls` of the same table
sits beside it. The game's own use of this file was not found. The editor writes it back as the designers' table.

## `data/config/game/gamevalues.xml`

77,261 bytes, Latin-1 with CRLF, 858 settings below `<GameValues>` (sections such as `NeedLevels/Pioneer/...`), many
with a German comment above. The file has 368 comments. Four of them end in `--->`, which is not XML, so a strict
parser refuses the file. The reader takes the comments out first. The editor changes the text of a setting in place
and leaves the rest as it is.

## `data/config/game/icons.xml` and `data/config/gui/iconfilemap.xml`

This is the scheme that 2070 and 1404 share (`sources/icons2070.py`). `icons.xml` maps a GUID (1,904 entries; 495 are
objects of `property.ini`) to `(IconFileID, IconIndex)`. `iconfilemap.xml` maps a file id to the atlas picture (a
`.tga` in 1701, `.png` named and `_0.dds` shipped in 2070) and its tile size. The tiles run row by row. 1404 adds
`addondata/config/game/icons.xml`.

## Texts

`data/loca/<language>/txt/*.txt` (and `addon01/`) are UTF-16 files of `id=text` lines, the same as in 2070 and 1404.
`guids.txt` holds the names of 1,435 GUIDs. The install has the English folder only.

## What is not known

| Item | State |
|---|---|
| names of the property and value numbers | not in any file; derive them by comparing objects with the game's behaviour. Compared with the balancing table's 73 named columns (353 objects that have a row, 2,782 value-and-column pairs): no value id equals one column over its objects (the best, 4 of 9 and 43 of 121, are the same small numbers by chance). `guids.txt` names GUIDs, not values. No name was taken |
| what 100's bits mean | 512, 98304, 114688 ... are sets of flags; not decoded |
| whether the game reads `balancingtabelle.txt` | not found (the numbers probably come from it into `property.ini` or the scripts) |
| an object's template | none in the data; the editor groups objects by the properties they have (`Object P1`, `Object P7 P3`) |
| a new GUID's allowed range | the editor takes the change set's range; the ranges the game accepts are open |
| written files in the game | not tried: a new archive over `data5.rda` (`docs/island-editor.md`) is how 1701 is delivered |

Note: the comparison with the balancing table was made on 2026-11-13.
