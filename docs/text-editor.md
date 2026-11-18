# The text editor (the Texts app)

The suite's Texts app shows a game's texts in two languages side by side. A translation is made and checked there,
and written into a project in the same form that the game reads. This page starts with the user's guide. It then says
what each game's text files are and how a mod adds or overrides a text (the format notes the app stands on). The code
is in `textedit/` (public edition, an app of `APPS`). The design is in `tool-suite.md` section 27, and
the rows of the idea list it answers are in `editor-ideas.md`.

Note: not tried in a game.

## The user's guide

1. **Open a game** (File > Open game; the folders are the suite's settings). The files are read in the background, and
   the window stays usable.
2. **Pick the two languages**: the *source* (the language you read, English by default) and the *target* (the one you
   write). Every text of the source is a row, and the target's text is beside it.
3. **Find the rows** with the filters:
   - the status filter
   - the file filter (games with several text files)
   - the group filter (117: the first word of the line's editor name, `Portrait`, `Ornament`, `WarDeclaration`; the
     games with several files: the file)
   - the search box (the id, the source, the target, 117's editor name, the speaker)
   - "only problems" and "only spoken" (117 and 2070)

   In 117 a line's twins (see below) stand right behind it.
4. **Write the target text** in the lower box and press Ctrl+Enter (or Apply). The row's status changes at once. The
   checks (see below) run on what you wrote and name every problem. An edit is kept in the project, never in the game's
   files. "Revert" takes it back to the game's text, and Ctrl+Z undoes the last edit.
5. **Save to the project** (Ctrl+S): the edits go to `text-edits/<game>.json` of the project, and the project's build
   writes them as the game reads them (see below).

### The status of a row

| Status | Meaning |
|---|---|
| untranslated | The target has no text (a missing or an empty one) or the very same text as the source (117 and 1800 fill the languages a text was not translated into with the English one) |
| translated | The game's own translation, which you have not touched |
| changed | Your edit, written against the source as it is now |
| outdated | Your edit was written against a source text that has changed since (the source was edited in the project, or the game's update changed it); also a game translation whose source you have edited and which you have not edited since; also an imported row a tool marked as needing a review. Apply the text again (the very same one too) to say it is still right: the edit is then kept against the source as it is now |
| new | An id the game does not have: a text of your mod |

With each edit the app stores **the source text it was written against**. That is what "outdated" compares. An imported
file that carries its source column (CSV, XLIFF, PO) hands that source over. A translation that was made outside the
app against an older source is therefore marked outdated, not trusted.

### What is checked

A translation must keep everything that is not words. The checks compare the target with the source:

| Check | Severity | What it finds |
|---|---|---|
| Placeholders | error | A `{expression}` or a bare `{}` hole of 117's texts, a `[Tag]` of 1800's, 2205's and 2070's (`[Conditions QuestCondition X]`, `[CONDITION_..._NAME]`, `[GUIDNAME 123]`), that is missing, extra or changed in the target |
| Markup | error | The tags of the source (`<b>`, `<i>`, `<br/>`, `<font color='...'>`, `<img ...>`, 2070's `<icon ...>`, `<p>`) missing, extra or changed; a tag left open or closed twice |
| Braces | error | A `{` without its `}` |
| Real line break | error | A line break in a text of the games that keep one text per line (2070, 1404, 1701: the break is a tag there) |
| Line breaks | warning | The target has more or fewer breaks (`<br/>` or real ones) than the source |
| Length | warning | The target is much longer than the source (over 1.4 times and 12 characters more), or empty where the source is not |
| Ends | hint | A space or a break at one end of the source and not of the target, or the reverse |
| Glyphs | warning, hint | A character of the target that the game's fonts do not draw: a warning where no font of the game has it (the game shows its default character), a hint where only another file than the language's own faces does ("The glyph check", below) |
| Glossary | hint | The source holds a term of the glossary and the target not its translation |

An error does not stop a save (you decide). The rows with errors have a filter of their own, and the project's build
lists them.

### The glossary

Two sources, per target language:

- **The game's own terms**: the short names of the game (one to three words, a capital letter, no markup), read from
  the game's texts. The source text and the target text of the same id make a pair. A term that the game translates in
  two ways is left out. These terms are offered as hints only, never as errors.
- **Your terms**: a table you keep in the project (`text-edits/<game>.json`, "glossary"), with a source term and its
  translation per language. You write it in the Glossary dialog or import it. Your terms win over the game's.

### Import and export

File > Export translation writes the rows the filters show (or all rows) for the target language. File > Import reads
such a file back. All three formats carry the id, the source and the target:

| Format | For | Notes |
|---|---|---|
| CSV (UTF-8 with a signature, comma, `id,file,group,source,target,status`) | a spreadsheet | The columns may come in any order and in any case; only `id` and `target` are needed. Read: UTF-8 or UTF-16 ("Unicode text"), with the separator the header line holds most of (a comma, a semicolon: German spreadsheets, or a tab). The `status` column is for the reader and is not read back. A spreadsheet turns the 19-digit ids of 117 into numbers it rounds: open the file as text, or use XLIFF or PO |
| XLIFF 1.2 | translation tools | One `trans-unit` per text, its `id` the text's id, `state` from the status. XLIFF 2.x (`unit` with `segment`s) is read too |
| PO | gettext tools | `msgctxt` is the id, `msgid` the source, `msgstr` the target, `#, fuzzy` for outdated rows. Plural entries and entries without a `msgctxt` are not texts of a game and are skipped |

The import follows these rules:

- An id that the game does not have is counted and skipped (a translation file for a text that does not exist), unless
  "add unknown ids as new texts" is chosen. A new text with no source column has no source text of its own.
- A target equal to what is there is not an edit.
- An id that is twice in a file counts once, with its last row.
- At the end the import says what was applied and skipped, and which texts have a warning or an error.
- An import is one undo step.
- **A row that a tool marks as needing a review** (`#, fuzzy`, an XLIFF state `needs-review-*`, `needs-adaptation`,
  `needs-l10n`) is imported as *outdated*. The translation is kept and flagged, as if its source had changed.
- Line ends in a text are the plain break, whatever the file had (a spreadsheet writes CRLF). The games that keep one
  text per line write a break as `<br/>`.

The reading and writing run off the window's thread.

## What each game's text files are

This section was checked on the installs of 2026-11-13 (117 update 3.0, the dated builds of the others).

| Game | Text files | Format | Languages |
|---|---|---|---|
| 117 | `data/base/config/gui/texts_<language>.xml`, and `texts_metadata.xml` (per id its editor name, its group `FolderId`, variations) | `<TextExport><Texts><Text><LineId>` (signed 64-bit) `</LineId><Text>...</Text></Text>`; UTF-8 without a signature, CRLF; 43,567 lines in each of the twelve files, 130 ids twice | english, german, spanish, french, italian, japanese, korean, polish, brazilian, russian, simplified_chinese, traditional_chinese |
| 1800 | `data/config/gui/texts_<language>.xml` | the same with `<GUID>` (small positive numbers), 58,648 lines (English 58,682) | thirteen files: english, german, french, italian, spanish, polish, russian, japanese, korean, chinese, taiwanese, and the partial brazilian (21,185 lines) and portuguese (23,770) |
| 2205 | `data/config/gui/texts_<language>.xml` (`texts_master.xml` is the English master, `texts_frenglisch.xml` a 1,578-line file of French in English letters); the DLCs' `data/<dlc>/config/game/texts/*.xml` are asset files of the template `Text` (a `LineID` per asset) and belong to the asset editor | the same as 1800, 17,383 lines | english, french, german, polish, russian, spanish |
| 2070 | `data/loca/<code>/txt/*.txt` and `.../txt/addon/*.txt` (33 files in the English pack: `interface`, `quests`, `tt_normal` ..., the credits) | UTF-16 with a signature, `id=text` per line, `#` comments, one text per line (a break is `<br/>`); later files and later lines win; the developer file `test.txt` only fills gaps; 28,918 ids in English | eng, ger, fra, ita, esp, pol, rus, cze |
| 1404 | `data/loca/<code>/txt/*.txt` and the add-on's `addondata/loca/<code>/txt/*.txt` (41 files in English) | the same, with symbolic keys too (`ISLAND_EDITOR_2=...`); 29,177 ids in English, 79 twice | eng, ger, fra, ita, esp, pol, rus, cze |
| 1701 | `data/loca/<code>/txt/<folder>/*.txt` (25 files in English, the Annopedia pages among them) | the same | eng (the install holds one pack), ger, fra, ita, spa, pol, cze, hun in the game |

A line of an old game's file that is no `key=text` (the credits' markup, a comment) is no text of the app and stays as it
is in every write. The app lists the languages the install holds; for the three old games a language the game ships but
the install lacks may be added as an empty target. 117's and 1800's languages cannot grow: a file under another name is
never loaded (`engine-texts.md`).

## How a mod adds or overrides a text

| Game | In a mod | What the app writes |
|---|---|---|
| 117, 1800 | A ModOps file of the same path as the language file (`data/base/config/gui/texts_german.xml`, or `data/config/gui/...`): a `replace` of `Text[LineId='...']/Text` for a text that exists, an `add` to `/TextExport/Texts` for a new one (an `add` of an existing id changes nothing) | The ModOps of `assetedit/modops.text_ops`, one file per language; **a new id is written into every language file of the game**, the target's text where it has one and the source's text elsewhere, because a language file lacking an id shows `<id> missing.` and the game never falls back (`engine-texts.md`, "Adding and changing lines") |
| 2205 | A whole `texts_<language>.xml` in the archive that overlays `config` (the game has no ModOps loader) | The game's file with the edited texts replaced in place and the new ones before `</Texts>`; every other byte as the game has it |
| 2070, 1404, 1701 | A whole text file in the archive that overlays the game's: the file of the same path replaces it | Each file that holds an edited id, whole, with those lines changed (the rest byte for byte, the encoding kept); the new ids in `data/loca/<code>/txt/<mod id>.txt` of each installed language |

117's `texts_metadata.xml` (the group of a line, for the lists that take a group) is **not** written: a line that no group
names needs no entry. Texts of a mod's own that a list must reach by group are made in the asset editor.

All of it goes through the project's build (`tool-suite.md` section 18): the Texts part of the project
joins the other parts' files, a ModOps file two parts write becomes one, and for the archive games the delivery packs the
whole files. A text file that the asset editor writes whole too (its text tab) and this part writes whole as well is a
clash the build reports: edit one text in one app.

## Beyond the text: glyphs, groups and twins, voices

This section was read from the installs of 2026-11-13 (117 update 3.0). The user's guide above says what the app does
with it.

### The glyph check

A label draws a text with the font its text style names; a character that font lacks shows as the font's default
character (`engine-texts.md`, "Fonts per language"). The app reads the **character table (`cmap`) of
the font files a game ships** (TrueType and OpenType, a collection too: `fontcover.py`) and sets it against the
characters of a text (markup, `{...}` and `[...]` placeholders, white space and format characters such as the zero-width
space, which 38,000 lines of the Asian texts hold as a break opportunity, are not drawn and not checked).

Which files a language draws with:

| Game | Files | How far it is read |
|---|---|---|
| 117 | `data/fonts/`, read through the game's own font assets (`fontchain.py`): a **text style** names a localized font, the localized font names a face per language (an exception for Japanese, Korean, Simplified and Traditional Chinese, else its default face) and a **fallback list** of font families behind it. Every language ends with five or six files (`AlbertusNovaforAnno117Tab`, `LisboaSans`, `DFPYuan5A`, `NotoSansTC`, `NotoSansKR`, `DFPKingGothicJP1`, `SalfordSans`), in an order that depends on the language: Japanese tries `DFPKingGothicJP1` first, Korean `NotoSansKR`, then the same list | The 34 text styles use two localized fonts, **AlbertusNova** (20 styles) and **LisboaSans** (14); a third, the hieroglyph font of the `Hieroglyphs` exception, is no text of a translation and is left out. A character is drawn when **any file of the style's chain** has it, so the Asian files draw the stray Han letters of a German text. A text is not tied to a style by the data (most labels get their text from code), so the check asks every style: a warning where none draws a character, a hint where only one of the two does; the style can be named (`unsupported(language, text, style)`) |
| 1800 | `data/fonts/` (Heuristica, Kelvinch, Meta, Roboto, `DFHei5A`, `DFPT_B5`, `MD_CGothic_L`, the UD and Kyoukasho collections): the same chain of assets as 117, in the same UI files (`fontchain.py`). Six localized fonts: two decode with the schema and are Latin (`MetaOffcPro`); **four do not** (a layout of their `LanguagesExceptions` record the schema lacks) and are read by the references they hold (`scan_localized`: the reference bytes `01 <type hash> <asset id>` of the file, split at each exception's record, only ids of font, family and fallback-group assets count), which gives Japanese the UD Marugothic collection, Korean `MD_CGothic_L`, Chinese `DFHei5A` then the Meta faces, Taiwanese `DFPT_B5` then those. A scanned font that has no default face gives the languages with no exception of their own the face of its exception `SansSerifLanguage` (Latin and Cyrillic); 117's fonts that have that exception also have a default face, which they use | The text styles name a localized font (116 styles, 23 to 39 of them in each of the four scripted fonts); a character is a warning where no file of any style's chain has it, as in 117. The localized fonts carry no name in 1800: the picker names them by their first file |
| 2205 | `data/ui/chromium/` (Roboto) | As 1800; the interface is drawn by a browser engine, which also falls back to the system's fonts |
| 2070 | `data/loca/<code>/fonts/`: `DejaVuSansCondensed.ttf` (5,633 glyphs), `MGS65__U.ttf` (507; no Greek) | As 1800 |
| 1404, 1701 | `data/loca/<code>/fonts/georgia.ttf` and `data/ui/studio/fonts/` | As 1800; Georgia is the game's own copy |

Measured with `FontCheck.audit` over every text of every language (the number of distinct characters a language's faces
lack; the stray ones are letters of other scripts that a few lines hold):

| Game | Result |
|---|---|
| 117 | Every language lacks 0 to 3 characters in **both** styles' chains, each in one or two texts: `ʹ` (U+02B9, a Greek numeral sign) in German, French, Polish, Brazilian, Korean and Simplified Chinese, `ḫ` and `ἰ` in English, `ނ` in French, `ۮ` in Spanish, a replacement character in Simplified Chinese. Italian, Russian, Japanese and Traditional Chinese: none. (Before the chains were read, a hand-kept list of faces gave 6 to 32 per language, mostly Han letters that the Asian fallbacks do draw.) |
| 1800 | Read through the chains (2026-11-14): every language 0 to 5 characters that no style's chain draws: Japanese `ゔ` and four Han letters (the `・` of 3,759 lines and the half-width letters, which the file-list check missed, are drawn by the UD collection), Chinese four rare Han, Korean three syllables, Brazilian and Portuguese `ǐ` (one text each), French one Telugu letter, Taiwanese none. The Asian languages list thousands more characters as a hint: two Latin-only styles are in their list |
| 2205, 2070, 1404 | 0 |
| 1701 | English: 50 Thai letters in one text |

For 2205, 2070, 1404 and 1701 the check is a *hint of what to look at*: which face a label draws with, and the system
fonts a game may fall back to, are not known there. 1800 reads its chain like 117. In the window the live check of the
target lists the characters. Edit > "Glyphs of the target language" counts them over the whole language (off the
window's thread). The font files of 117 are read in about a tenth of a second.

### Text groups and twin lines (117)

`texts_metadata.xml` gives a line `Name`, `FolderId` and, for 2,189 lines, `MasterId` and `VariationId`.

- **Groups.** `FolderId` (4,678 distinct) is the *text group* a list of the game draws a random line from. **Its data
  has no name for a group**: a group is an id only. The game's asset data reaches a group through a property of the data
  type `TextGroup` (`properties.xml`), and only 30 of the 4,678 ids stand in `assets.xml` that way (as the value of a
  property such as `BlacklistFeature/ProfileNamePool` or `AllianceNames`); the rest are reached by other files. So the
  fallback stays: the group filter uses the first word of the editor name, and the picked line shows its group's id (the
  last eight digits) in its title line, which also finds the lines of one group when typed in the search box.
- **Twins.** A line with a `MasterId` is a *variation* of that main line: 1,464 main lines have one, two or three
  twins (775, 653 and 36 of them). A twin has **the same editor name as its main line** and, as a rule, the same words
  with another gender or button name in it. `VariationId` says what it is for: 1,378 and 650 twins are the portrait
  variations of the two player characters (the id is the `OasisCharacterId` of the `Portrait` asset *Marcia Africanus*,
  a woman, and *Marcus Africanus*), 161 more (three kinds) belong to no asset and are the controller texts. The app
  lists a twin right behind its main line, names the kind (the player character, else `variation <id>`), and the panel
  lists the twins of the picked line with their source texts. **"Fill the twins with this text"** writes the target
  text into those twins whose *source text is the very same* (one undo step); a twin whose words differ is left to the
  translator.

### Speakers and voices

| Game | What speaks a text | Where |
|---|---|---|
| 117 | An `Audio` asset of `data/base/config/export/audio_generated.xml`: `OasisScriptId` the text's id (13,703 lines, each in the texts), `CharacterId` the speaker, `VoiceMood` (Neutral 7,715, Negative 2,883, Positive 2,868, Negative2 237), `WwiseID` the event of the voice bank `BNK_VO_MAIN` of the audio language (`en_us`, `de_de`; every other language plays the English folder, `sounds.md` "Voices of the messages") | The event's sound is read with the Sound app's bank reader (`exporting/banks`: the event walked to its media, the streamed `.wem` under `generatedsoundbanks/windows/media/<folder>/`, Wwise Vorbis made Ogg by `wwvorbis`, which needs the codebook file of the tools folder) |
| 2070 | `speech/<speaker>/<text id>.mp3` of the language pack (6,972 files in English, 6,971 with a text of that id; the German pack has none) | The speaker is the folder (`eve`, `thor strindberg` ...); the file is plain MP3 |
| 1404 | the same: `data/loca/<code>/speech/<speaker>/<text id>.mp3` and the add-on's `addondata/loca/<code>/speech/`: 7,154 files in the English install (42 speakers: `narrator`, `northburgh n`, `marie` ...), 7,147 of them with a text of that id. Each has a `.sp` file beside it (lip movement, not read) | plain MP3 |
| 1701 | the same under `data/loca/<code>/speech/`, folders `sprecher_<n>` and `addon01/addon_sprecher_<n>`: 12,228 files, 33 speakers named by number only (the `sprecher <n>.txt` of a folder lists the ids of a role, no name). **1,712 of the 12,228 ids are texts of the English table**: the rest belong to a text by other files ("1701's speech files", below) | plain MP3 |
| 2205 | an `AudioText` asset (`audio_texts.xml` and the character files `texts/<name>.xml`): its **own GUID is the text's key**, `AudioAsset` an `Audio` asset of `audio_generated.xml` whose `WwiseSoundID` is an event of the one voice bank per language folder (`generatedsoundbanks/windows/english(us)`, `french(france)`, `german`, `russian`; 5,678 events each). 5,974 voiced texts, all in the English table; 5,615 of the 6,145 audio texts name an event the banks hold. The speaker is the character group of `audio_texts.xml` whose `FileName` is the asset's file (96 groups: `ADAM` 552, `GlobalUnionAdvisor SAM` 544, ...) | **The install has no voice files**: the streamed `.wem` are named in the bank's info file, not under `Media` but under **`ReferencedStreamedFiles`** (5,812 per language: `Voices\<language>\<id>@<line>_<hash>.wem`), and that folder is not shipped. The row shows who speaks it and the play button asks the bank; the answer names the missing file (`voices/english(us)/109243@1161_cc1486bc.wem`; `bank_info` reads both lists since 2026-11-14) |
| 1800 | an `AudioText` asset of `assets.xml`: its own GUID is the text's key (15,376 voiced texts, every one in the English table), `AudioAsset` an `Audio` asset with a `WwiseID`. The asset's name carries the speaker, `AT_<speaker>_...` (`AT_DLC03_OldNate_ArcticQ201_01`; the add-on prefix left out; 168 distinct, among them `res` 1,327 residents' lines, `3rd` 1,209, `ai01` 596): a hint, not a character list | **None of the 2,917 events of the installed banks is one of these**: the install holds no voice bank, so the row says who speaks it and has no play button |

The speaker of a 117 line is the `Portrait` asset whose `OasisCharacterId` is the `CharacterId` (its name in brackets:
*Ben Baalion* 1,011 lines, *Marcia Africanus* 717, *Marcus Africanus* 687, *Gnaeus* 569 ...): 36 of the 43 characters are
named so, the other seven (380 lines) show as `character <last digits>`: a search of every text and every UI file of the install for those ids (as text and as 64-bit numbers) finds them in `audio_generated.xml` only, and the lines' editor names give a place (`Credits..`, `Movie1_..`, `Murmur..`, `Dialog..`: the film narrator, the credits' actors, the crowd), **no name**: the data has none, so they stay numbers. The three variation kinds that no `Portrait` asset names (40, 42 and 79 twins, all in `texts_metadata.xml` only) are the same: the app says `variation <digits>`. A voiced row says who speaks it and the mood, has the filter
"only spoken", is found by the speaker's name in the search box, and has two buttons that play the line **in the source
and in the target language** (a language the game has no voice for has its button off); the clip is read and decoded off
the window's thread and played by Qt's media player, as the Sound app plays a sound. The decoded clip is one file of the
cache folder's `sounds`, named by its content and written once (`Voices.clip_file`), never a file of the temp folder.

## One writer for both apps

A project's texts can be changed in two apps: the Texts app (`text-edits/<game>.json`) and the asset editor's text tab
(the change set's `texts`, `asset-edits/<game>.json`). Both concern the same files: the whole text files of 2205, 2070,
1404 and 1701, and the ModOps of the language files of 117 and 1800. To avoid a clash in the build, **the Texts part is
the project's only writer of texts**. `textedit/merged.py` `combine(edits, change set texts)` makes one set of edits:
it adds every text of the change set that the Texts app has no edit for. The change set's texts carry no source text,
so they are never "outdated". `projectpart.render` writes that set with the one writer of `render.py`. The asset part
writes no text (`assetedit/projectpart.py` renders a change set with its texts emptied, and `has_work` ignores them).
The asset editor's own preview and the "write the mod" of its window are not affected.

**A conflict** is a text id that both apps changed to different words in one language. The Texts app's text is written,
and the build's log names the id and the language ("the Texts app and the asset editor changed it differently"). The
same words in both are no conflict.

## Text styles of a text

117 and 1800 draw a text with the text style of the label that shows it. Most labels get their text from code, but
**233 labels of 117's UI files and 798 of 1800's name a text by id** (`LocalizationId` in a label: 117 stores the signed
64-bit id as 16 hex digits, `A018 4AB6 6641 96B8`; 1800 the decimal GUID) and name their `TextStyle`, an asset that names
the localized font. That links 172 texts of 117 and 986 of 1800 to a style (217 of the 233 labels' ids are rows of 117's
English table); every other text (nearly all of the 43,567 and 58,648) has none and stays "every style". `stylelinks.py`
scans the widget files (38 seconds for each game, off the window's thread, once: the result is kept as
`textstyle-links-<stamp>.json` in the game's cache folder) and gives `fonts_of(id)`, the localized fonts of a text.

In the window: the picker under the target box has "the text's own" (the styles of the labels that show the text; every
style where no label does), "every style" and each localized font of the target language by name; the line beside it says
"Drawn in ..." where a label names the text. The glyph check of the box uses the pick. In code `TextDoc.glyph_problems(text,
style, key)`, `style_choices`, `linked_names`.

Both boxes show the text in the game's font of the picked style (`textfont.py`, `TextDoc.face`): the first face of that
style's chain for 117 and 1800, the game's default font for the others (2205 Roboto Regular, 2070 DejaVu Sans Condensed,
1404 and 1701 Georgia). A font that cannot be read leaves the plain font and the boxes' tooltip says why. The fonts are
read from the install and registered with Qt, never copied ([`ui-editor.md`](ui-editor.md), "Fonts").

## Lip files

1404 and 1701 keep a `.sp` file beside each speech file (6,576 and 11,863 files). Read from the installs: **a 32-bit count `n`,
then `n + 8` bytes and four zero bytes** (every file is `n + 12` bytes long: 0 failing of 18,439); the bytes are one
mouth-opening level (0 to 255) per frame, and **the frames are 50 a second** (`n + 4` levels over the clip's length: 50.0
to 53.1 over 400 files of each game, the clip length counted from the MPEG frames, `lipsync.mp3_seconds`). That the game
reads the levels as mouth openings is an inference from the shape (a run of zeros between words, peaks on vowels), not a
seen result. The panel shows the file under the voice: the levels as a line of bars, the frame count, the length.

## 1701's speech files

Of the 12,228 speech ids of 1701, 10,516 are no text id. Two kinds of file of the game say which text they speak
(`voicedata.read_speech_owners`): `data/config/game/messages.xml` (a `Message` with its `SpeachGuid`s: 298 messages) and the AI
profiles `data/ai/*.xml`, `data/ai/addon01/*.xml` (a `MESSAGE` with its `Text` id and a `SOUNDS` list of `GUID`s: one line in
four to eight takes). A speech id of the form `130077_1` is a take of `130077` (the `sprecher N.txt` lists name them). With
these, **9,486 of the 10,516 belong to a text** (8,639 by the files, 847 by the suffix); they are the takes of 345 texts, so
the voiced rows of 1701 go from 1,712 to 2,057: a text with several takes shows the first and says how many. 1,030 speech ids
stay with no text in any file of the install (the campaign's scenario files are binary and not read).

## Where the code is

`textedit/`: `merged.py` (the Texts app's edits and the change set's texts as one), `stylelinks.py` (which text style draws a text), `lipsync.py` (the `.sp` files), `textfiles.py` (the files read as entries with their places, and written back: byte for byte when nothing
changed, a broken byte kept), `texttable.py` (a language's entries by id, the metadata), `textdoc.py` (the open game, the
two languages, the edits and their undo, the status, the filters, the twins), `checks.py` (the placeholder, markup,
length and glossary checks), `fontcover.py` (the glyph check), `voices.py` (clips) and `voicedata.py` (which texts are spoken and by whom, for every game), `fontchain.py` (117's text styles, their faces and fallback lists), `glossary.py`,
`exchange.py` (CSV, XLIFF and PO), `render.py` (what a project's texts become as files), `projectpart.py` (the part of the
project), `rowsmodel.py`, `detailpanel.py` and `textwindow.py` (the window). The tests: `tests/test_textedit.py`,
`tests/test_textedit_fonts.py` and `tests/test_textedit_voices.py` (synthetic, by module), `tests/test_textedit_installed.py` (the installs, gated on `ANNO*_DIR`: every
file of every game read and written back byte for byte, the fonts, the twins, the voices), `tests/test_textedit_window.py`
(the window offscreen).

The asset editor writes its texts through the same writer. `assetedit/native.py` `render_texts` reads a language with
`texttable.read_language` and writes it with `render.whole_files`. This is one pass: an entry written `<Text/>` is
found and replaced (the game keeps the first entry of an id, so a second one must not be appended), and a real line
break is kept off the lines of the games that keep one text per line. The project's build writes the asset editor's
texts through the Texts part ("One writer for both apps").

## Not built, open

- A text group's name, where it is one of the 30 that `assets.xml` names through a property, is not shown (the
  property's path would be the name).
- The seven 117 characters without a portrait asset and the three variation kinds that no asset names stay numbers:
  the data has no name for them (see above).
- A text that no label names has no style (this is true of nearly all texts): the picker's "every style" asks all
  styles. 2205 draws in a browser engine and the old games use one file, so there is no style there.
- 1800's and 2205's voice files are not in the installs. 1701 has 1,030 speech ids that no text file names. The clip
  that is played is not compared with what the game plays. The lip levels are shown, but not tied to a mouth shape.
- A shared glossary between projects and machine translation are both left out on purpose.
- Nothing here has been tried in a game: that a written text file loads, that a new id shows in every language, and
  that the played voice is the one the game plays.
