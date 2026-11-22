# Anno 2205's UI movies (`.feu`) and the files around them

This page is hand-written. It was read from the shipped files of an installed Anno 2205 (all patches): the 89
movies of `data/ui/`, their pictures, atlases, fonts and tables. Every movie and every table named here is read and
written back byte for byte by the readers in `src/annotoolbox/formats/` (`feuread.py`, `feuwrite.py`,
`feuedit.py`, `feushape.py`, `feuabc.py`, `feuassets.py`). The test `tests/formats/test_feu.py` runs over the install when
`ANNO2205_DIR` names it. **Nothing here was seen in the game yet**: a passing round trip proves the file format, not
that the game loads a changed file. The guide to the editor built on it is
[`../ui/2205/flash-ui-editor.md`](../ui/2205/flash-ui-editor.md).

Note: read on 2026-11-07.

## The files of the UI

| Files | What |
|---|---|
| `data/ui/*.feu` (89) | the movies, one per screen or popup (`title_screen`, `options_audio`, `construction_menu`, `objectmenus` ...). The game starts with `ui_epilepsy_warning.feu`, then `ui_release.feu`, which load the others |
| `data/ui/extractedfirefiles/**/*.png` + `*_0.dds` (1,673 each) | the pictures the movies name, each with its texture (below) |
| `data/ui/blobs/*.bfd` + `.png` + `_0.dds` (6) | image atlases: the icon pictures packed into one picture, with a table of where each sits |
| `data/ui/*.ffd` + `*_1.png` + `*_1_0.dds` (7) | the fonts: Roboto in five weights, Arial, a Chinese font; a descriptor and its glyph picture |
| `data/ui/pngsizes.dat` | the size of every UI picture, read before a movie draws |
| `data/ui/*.dep.xml` (13) | for some movies, the textures and fonts they load |
| `data/ui/*.tfo` (2) | texture settings (the engine's texture info format: `<TextureInfo><Format>28</>...`, format 28 being 8-bit RGBA) |
| `data/ui/chromium/` | HTML pages for the world market's graph (shown by the game's embedded browser, not Flash) |

**Fire movies.** The `.feu` movies are movies of Ubisoft's in-house Flash-based UI middleware "Fire". Two things
show it: the `UEF` signature below, and the `ExtractedFireFiles` folder that the pictures' source paths start with.
The public source is Ubisoft Montréal's article on moving Rainbow Six Siege from Fire to Phoenix
(`../toolone-public-sources.md`, section 15). It says only that Fire was Flash-based. The file layout here is read
from the files.

Note: the Fire link was found on 2026-11-08.

## The container

A `.feu` is an **uncompressed Flash movie** (SWF) whose signature is `UEF` where Flash has `FWS`:

```
"UEF"  u8 version (12 in 19 movies, 15 in 70)  u32 file length (= the file's size)
RECT stage (1920 x 1080 in 66 movies; popups and bars smaller)  u16 frame rate (8.8)  u16 frame count (1)
tags: u16 (code << 6 | length), length 0x3F -> a u32 length follows; the last tag is End (0)
```

It is not compressed (no `CWS` / `ZWS`) and **not Scaleform GFx**: no `GFX` / `CFX` signature and none of GFx's tags
(1000 and up). The tags are the standard ones plus five of the game's own exporter (next section). These standard
tags were found, with their count over all movies: `FileAttributes` 89 (`0x08`: ActionScript 3), `SetBackgroundColor` 89,
`DefineSceneAndFrameLabelData` 87, `DefineShape` 1,386, `DefineShape3` 5, `DefineShape4` 2, `DefineEditText` 733,
`DefineSprite` 3,607 (with `PlaceObject2`, `PlaceObject3`, `RemoveObject2`, `FrameLabel` and `ShowFrame` inside),
`DefineScalingGrid` 410, `PlaceObject2` / `PlaceObject3`, `FrameLabel` 3, `EnableDebugger2` 3, `ScriptLimits` 2,
`DoABC` 82, `SymbolClass` 82. A movie holds **no picture and no font of its own**: there is no `DefineBits*` and no
`DefineFont*`. Both come from the game's files through the game's tags.

`PlaceObject3` sets its "has image" bit when it places one of the game's images directly. Unlike the Flash
specification's reading of that bit, no class name follows it in these files (the name is there only with "has class
name").

## The game's tags

| Code | Name used here | Layout | Count |
|---|---|---|---|
| 249 | exporter version | u32, always 2 (the second tag of every movie) | 89 |
| 251 | font reference | u16 font id; string font file (`Roboto_Light_Default.ffd`); string family name (`Roboto Light`); u8 flags (0, 128 on two); u16 count; count x (string language, string font file) - all English only | 186 |
| 246 | external image | u16 character id; u16 width; u16 height; string path below `data/ui/` (`ExtractedFireFiles\pngs\UIAssets\x.png`); u16 flags (0, 1 on 16); u8 0 | 1,878 |
| 243 | atlas image | as 246, then a string: the name of the atlas entry (`.\PNGs\icons\icon_zoom.png`, the same as the path); flags 1 | 1,160 |
| 244 | image atlas | string table (`.\blobs\others2.bfd`); string picture (`.\blobs\others2.png`) | 7 |

Strings are NUL-terminated. The width and height of an external image are its picture's size in pixels (all 503
pictures checked). A text field's font id (`DefineEditText`) names a font reference. An external image is used in
two ways: as the picture of a shape's bitmap fill (1,377 fills, all of them clipped, 1,373 unsmoothed) or placed
directly by `PlaceObject3`. Atlas images (243) are only in `icon_library.feu` and `icon_library2.feu`. The code picks
the icons from there by name.

## From a movie's name to a game file

- **Picture**: lower case, `\` to `/`, a leading `.\` and any `PNGs` folder dropped, below `data/ui/`:
  `ExtractedFireFiles\pngs\UIAssets\Button_Up.png` is `data/ui/extractedfirefiles/uiassets/button_up.png`. All but two
  of the 1,878 names resolve (`dev_playground.feu`'s `color_grey_light_3.png` and `menu_popup.feu`'s
  `dark_overlay.png` have no file). For `color_grey_light_3.png` there is no file of that name in any folder or case
  (only `color_grey_dark_1..3` and `color_base_light_3`), no atlas entry and no size-table record. Only
  `dev_playground.feu` names it, so it is shipped missing, and the export index lists it as missing (checked
  2026-11-08).
- **Texture**: beside each picture `<name>_0.dds`, a DDS of 32-bit RGBA pixels (masks R `0xFF`, G `0xFF00`, B
  `0xFF0000`, A `0xFF000000`; header flags `0x2100F`, depth 1, one level, caps `0x1000`). Its pixels are the PNG's: of
  the first 301 pictures, 299 textures are byte for byte what `feuassets.rgba_dds` writes from the PNG (two differ by
  one step of colour). Which of the two the game draws is not known. A replaced picture gets both.
- **Size table** `pngsizes.dat`: 908 records of 268 bytes - char[260] path (NUL-padded, the original case,
  `data/ui/...`), u32 width, u32 height. 878 match their PNG; 30 name pictures the game does not ship.
- **Atlas table** `.bfd`: u8 version (1), u16 count, then per entry: string name, u16 width, u16 height, six float32
  `a, b, c, d, tx, ty` with `a = 20 * W / width`, `d = 20 * H / height` (W x H the atlas picture), `b = c = 0`,
  `tx = -20 * x`, `ty = -20 * y` (x, y the entry's top-left pixel; the packer leaves one pixel between entries). All
  six tables round trip; every entry lies inside its picture.
- **Font**: `data/ui/<file in lower case>`; its glyphs `<name>_1.png` / `_1_0.dds` are a distance-field picture. The
  `.ffd` descriptor is read in part (`formats/ffdread.py`; "Font descriptors" below).
- **Dependency list** `.dep.xml`: `<Dependencies>` with `<Texture FileId="extractedfirefiles\pngs\...png" />` per
  picture and `<Language Name="English"><FontDescriptor FileId="roboto_default.ffd"><Texture FileId="roboto_default_1.png"
  /></FontDescriptor></Language>`. Only 13 movies have one (`ui_release.dep.xml` is empty), so the game does not need
  it to load a movie. It is left as it is.

## Font descriptors

Read from the install on 2026-11-17 (the 2205 install of the owner). The game draws text from a **glyph picture per
font**, not from outlines: no movie holds a `DefineFont` tag, a font reference (tag 251) names a descriptor, and the
descriptor names the picture. Seven descriptors ship in `data/ui/`: `roboto_default`, `roboto_light_default`,
`roboto_medium_default`, `roboto_thin_default`, `roboto_bold_default`, `arial_default` and `dfyuanw3-gb_default` (a
Chinese font). Of the 733 text fields in the 89 movies, 569 use Roboto Light, 63 Roboto Thin, 50 Roboto Medium, 49
Roboto, one Arial and one the Chinese font; none uses Bold (read from the movies).

**A `.ffd`** (10 to 11 KB), little endian, checked against all six regular descriptors:

| Part | Content |
|---|---|
| header | four u16 (the third is a glyph count that the bold stub does not honour, the fourth is 32), then 36 bytes that look like a hash (meaning not known) |
| name | bytes `fd 0f`, two bytes, a length byte, the family name (`Roboto Light`; the reference's name agrees) |
| tables | u16 glyph count again; count + 1 u16 offsets (`2 * count + 2` upward in steps of 2); count u16 of `0x11`; count u16 **Unicode characters**, sorted (291 to 296 of them, Latin and Cyrillic among them) |
| two arrays | count u16 each (the first holds values 5,000 to 21,000, the second mostly 8): not decoded |
| picture | the NUL-terminated file name of the glyph picture (`Roboto_Light_Default_1.png`) |
| glyphs | count records of 27 bytes: u16 character, u8 0, four float32 (the glyph's rectangle in the picture as left, top, right, bottom, 0 to 1), four i16 (for `!`: 1, 123, 128, 422; for `A`: -34, 123, 382, 422) |
| end | a u16 (0 or 1) |

The pictures are 1024 x 1024 (Roboto Thin 1024 x 2048) RGBA files with a signed distance field. `roboto_bold_default`
is a **stub**: its picture is 64 x 64 (487 bytes) and its descriptor does not follow the layout above, so the reader
raises for it (the game draws no Bold of Roboto from this file; what it does for a bold text is not known).

**Not decoded:** the unit of the four i16 numbers (the sizes of `A` and `m` fit a design size of about 460 units per em
plus a margin of about 85), the advance widths and kerning (no field there matches the TrueType advances), the two
arrays and the header's hash. Without the advances nothing can set a line of text from the picture alone, so the preview
does **not** draw from the glyph pictures.

**The TrueType files the install does ship.** `data/ui/chromium/roboto-light.ttf` and `roboto-regular.ttf` (for the
game's embedded browser) carry the same family name as the descriptors (Roboto) and are taken as the faces the pictures were made from
(inferred: the glyphs were not compared). A Roboto reference is drawn with them: Light at weight 300, Regular at 400. Roboto Medium, Thin and Bold have
no file: the preview draws them in the nearest shipped weight (Thin as Light, Medium as Regular, Bold as Regular made
bold by Qt) and says so. Arial and the Chinese font have no file either: the system's font stands in, and says so.

## ActionScript and how a movie talks to the game

The code is ActionScript 3 byte code (`DoABC`, version 46.16) and is most of every file: 65.3 MB of the 66.2 MB the
89 movies weigh, because the whole component library is compiled into each movie. Any Flash decompiler reads it once
the movie is re-signed `FWS` (below). Read that way:

- **Frames**: each screen's class derives from a generated base in the package `toolone.GeneratedFrames`
  (`TitleScreenFrameBase`, `GlobalbarFrameBase` ...) over `menu.BaseMenu`. The frame's name is its class name.
- **Data in**: `ExternalInterface.call(<frame name>, "GetData")` returns the object the movie lays out (82 such
  calls). The game asks a frame to redraw through its `Invalidate` method.
- **Actions out**: `ExternalInterface.call(<frame name>, "<method>", ...)`, such as `OnBackClicked`, `OnTabClicked`,
  `OnSelectSession` and `OnCloseButtonClicked`. Shared services are called by name: `Common` (`playSound`, `ASSERT`,
  `DEBUGMENU`), `CursorManager` (`setCursor`), `TooltipManager` and `Clipboard`. A movie can only call what the game's
  side of that frame answers.
- **Texts**: the text fields' starting texts are placeholders (`Text`, `Lorem ipsum ...`, `100`). What the player
  reads is set by the code from the game's data, so a label is usually changed in the game's text files, not in a
  movie.
- The library: `fire.*` (the Flash player's own components and events), `components.*`, `menu.*`, `utils.*` and
  `toolone.*` (generated enums and frames). `SymbolClass` binds the movie's sprites to these classes.

`feuabc` reads the byte code's constant pool and changes its strings (the code refers to a string by its number, so a
longer or shorter string moves nothing else). Changing the code itself is a Flash tool's work.

## Editing, by the format

`feuedit` changes the following, each in its own tag, and leaves everything else as stored:

- a text field's starting text and colour;
- a placement's position;
- an external image's path or size;
- a font reference's file;
- a string constant of the code.

The edit file of the Flash UI editor (`flashui/uiproject.py`, JSON) keeps those per movie, plus whole files:

- a movie saved by a Flash tool;
- a replaced picture (any size: every movie that names it, and `pngsizes.dat`, get the new size);
- an atlas icon (the same size: pasted into the atlas picture, the table unchanged).

A build applies the edit file to the game's own files every time. Not possible yet: a new picture or file (the
archive index has no entry for it; see below), a new font (the `.ffd` is only partly decoded) and a different atlas layout.

## Getting a changed file into the game

2205 has no mod folder. The game finds every file through its archive index `maindata/file.db`
([`anno2205-map-format.md`](anno2205-map-format.md), "How the game finds a map", has the index and its rules). So a
changed UI file reaches the game the way a changed map does. The files go into an archive of our own,
`maindata/flash-ui.rda` (stored, flags 0, as the game keeps its UI files). The archive is appended to the index's
archive list, and the files' entries point into it (`othergames/ui2205.py`).

The island editor's map archive works the same way, and both go through one delivery
(`othergames/delivery2205.py`). The game's own index is kept once, outside the workspace (and again whenever the one
in the game is not ours). The index in the game is always that one, pointed at every archive of ours that is
installed. So the UI and the maps install and uninstall in any order. The launcher's file check would repair
`file.db`; the next install then keeps the repaired one.

The other way goes by the game's file rules: with `FileSystem.PreferLocalFiles` set in `engine.ini`, a loose file
wins over the archive's (`flashui build --loose` writes the files as a loose tree). Where the game looks for loose
files was not checked.

## Tools in public

- **JPEXS Free Flash Decompiler** (FFDec 26.4.0, tried here): it refuses a `.feu` ("wrong signature"). Re-signed
  `FWS`, it reads every tag, keeps the game's tags as unknown tags, decompiles the ActionScript (the title screen: 379
  classes) and lists the texts. It shows no picture or font (they are not in the file), so shapes draw without their
  pictures. Its XML round trip wrote the title screen back one byte shorter, and what it saves is signed `FWS`. Its
  GFx support (Scaleform's tags) does not apply: the game's tags are other numbers and layouts.
- **Scaleform's own tools**: never public, and the files are not GFx.
- **Anno 2205 community tools**: the RDA Explorer forks extract the archives. No public tool was found for the
  movies, the game's tags, the atlases, the size table or for loading a changed UI file.

So the existing tools edit the code of a re-signed movie, nothing more. The rest is built here (the Flash UI
editor): the game's tags, the pictures, atlases, fonts and size table, re-signing both ways, the edit file and the
build into the game's archive layout.

Note: searched on 2026-11-07.

## The preview

The editor draws a movie's first frame (or one sprite's) with Qt: placements by depth with their matrix and alpha,
shapes as filled outlines (solid, gradients, the game's pictures; a clipped bitmap once, its edge pixels beyond),
directly placed images, colour transforms (multiplier and offset, composed through nested sprites), masks (clip
depths), the blend modes Qt composes (multiply, screen, lighten, darken, difference, add, overlay, hard light), and
text fields in the game's own Roboto files where the install ships them ("Font descriptors"), else the system font of the
family the reference names. These are left out: filters,
`PlaceObject3`'s visibility, scale-9 grids, the `.ffd` glyph pictures, later frames, and everything the code builds at run
time (most lists and values, and the colours it sets on the `color_*` bases). So a screen looks emptier than in the
game; [`../ui/2205/flash-ui-editor.md`](../ui/2205/flash-ui-editor.md#what-the-preview-cannot-draw) says what the
preview shows in place of what it cannot draw.

## Only the game can confirm

- that it loads a movie, picture, texture or table from our archive through the changed index (not run);
- which of PNG and `_0.dds` it draws, and that it takes a picture of another size with the movies' and the size
  table's new size;
- that a re-signed movie saved by a Flash tool (other code, other tags' layout) still runs;
- what the 246 flag 1, the 251 flag 128 and the `.ffd`'s unread parts (units, advances, the hash) mean, and whether the
  game's text looks like the preview's TrueType Roboto;
- where loose files are looked for with `PreferLocalFiles`.
