# The Flash UI editor for Anno 2205

The Flash UI editor is the **Anno 2205 page** of the suite's UI app ([`../../ui-editor.md`](../../ui-editor.md)). It is
a PC application of its own, and a set of commands. It opens Anno 2205's UI movies (`data/ui/*.feu`) from an installed
game, and shows their elements and a preview. It edits texts, pictures, icons, fonts, positions and the code's
strings. It builds the result into the game's archive layout.

The format it works on is described in [`../../formats/anno2205-ui-format.md`](../../formats/anno2205-ui-format.md).
**Not tried in the game yet** (see the list at the end).

## Why it exists

No public tool edits 2205's UI fully (the format doc, "Tools in public"). A Flash decompiler reads the movies' code
once they are re-signed. It knows nothing, however, of the game's own tags, its pictures, atlases, fonts and size
table, or of how a changed file gets into the game. The editor does that part. It hands the code to a Flash tool when
the code itself has to change.

## Start

The editor needs Python 3.12+ and the `desktop` extra (PySide6) for the window, and Pillow for pictures and icons.

```
python -m annotoolbox.flashui editor [edits.json] --game "<Anno 2205 folder>"
python -m annotoolbox.flashui <command> ...          # the commands below; --game or ANNO2205_DIR names the game
```

## The window

- **Left**: the install's movies (with a filter) and, below them, the open movie as a tree: *Stage* (the first frame's
  placements, with named instances in quotes and nested sprites under them), *Images* (pictures and atlas icons),
  *Fonts*, *Texts*, *Classes* (the sprites the code knows by class name) and *Code*.
- **Middle**: the preview of the stage or of the selected element (the zoom is in the status bar). Pictures come from
  the game, or from the edit file when they were replaced. The format doc, "The preview", says what the preview leaves
  out.
- **Right**: the selection's properties, and what can be changed:
  - a text field's text;
  - a picture (*Replace picture...*, any size);
  - an atlas icon (*Replace icon...*, the same size);
  - a font reference (another font file of the game);
  - a placement's position;
  - a string constant of the code.
- **File > Export images...**: tick what to write as PNG files, and choose a folder. The choices are:
  - the selected element's picture
  - every picture of the open movie
  - every picture of all 89 movies (a sub-folder per movie)
  - the shared pictures (`shared/`: the 1,673 PNGs of `data/ui` except the 6 atlas pictures)
  - the 6 atlases cut into their icons (`atlases/<atlas>/<entry name>.png`)

  An `index.csv` and an `index.json` in that folder list each picture's name, source game path, kind, size, the movies
  that use it and its status (`ok`, `missing` when the game ships no file, e.g. `color_grey_light_3.png`, or
  `failed: why`). A picture is the game's PNG where there is one, and its `_0.dds` otherwise.

  **Right click** an image or icon in the tree, or the preview with one selected, for *Export this picture...* (one
  file). Exported pictures come from the game, not from the edit file.
- **File**: open the game folder; open and save the edit file; *Export movie for a Flash tool* (a re-signed `.swf`)
  and *Replace movie from a Flash tool*; *Build mod*, *Install into the game* and *Uninstall from the game*. The build
  folder is asked for once and kept, and *Choose the build folder...* changes it. The editor asks about unsaved edits
  before another edit file opens or the window closes.
- **Edit > Undo** and **Redo** (Ctrl+Z, Ctrl+Y) take back and make again the edits of the open movie, including the
  ones loaded from the edit file. A new edit ends the redo, and opening another movie starts its list from the edit
  file.
- A *Tools* menu lists what other code registered (`flashui/uiseam.py`).

Every change goes into the **edit file** (JSON: the edits per movie, the replacing files by game path, the icons),
never into the game's files. A build applies it to the game's originals each time.

## The commands

| Command | Does |
|---|---|
| `list` | the movies, with their texts, images and fonts counted |
| `info <movie> [--strings]` | a movie's stage, fonts, images, texts, code blocks (and their string constants) |
| `extract <movie> --to <folder>` | the movie, its re-signed copy for a Flash tool (`.swf`) and the pictures it names |
| `export-images --to <folder> [--movie <m>]... [--all-movies] [--pictures] [--atlases] [--shared] [--picture <ref>]...` | the window's picture export, for batch runs (see the note below the table) |
| `to-swf <in> <out>`, `from-swf <in> <out>` | re-sign for a Flash tool and back (`from-swf` also unpacks a zlib-compressed `CWS` save and checks the result reads) |
| `set <edits.json> text <movie> <text> --id <n>` | a text field's starting text into the edit file (`font <movie> <file> --id <n>` likewise) |
| `set <edits.json> picture <game path> <png>` | a picture replaced (its texture and size follow in the build) |
| `set <edits.json> icon <atlas name> <png>` | an atlas icon replaced, same size (`.\PNGs\icons\icon_zoom.png`) |
| `set <edits.json> movie <movie> <file>` | a whole movie replaced by one saved from a Flash tool |
| `build <edits.json> --out <folder> [--loose]` | the changed files into `<folder>/anno2205-flash-ui/` in the game's layout (`maindata/flash-ui.rda` and `maindata/file.db`), or as loose files |
| `install --out <folder>`, `uninstall --out <folder>` | put the build into the game (the replaced `file.db` kept in `<folder>/anno2205-flash-ui-kept/`) and take it out |

`<movie>` is `title_screen`, `title_screen.feu` or `data/ui/title_screen.feu`.

`export-images` exports a movie's pictures, every movie's, the shared pictures, the atlas icons (`--shared` = both) or
one picture by game path or name. It writes PNG files and `index.csv` / `index.json`.

## What the preview cannot draw

The preview draws what the movie itself fixes. Some pictures get their look from the game at run time, and the
preview shows them as a marked stand-in, never as a plain white box. The reason is in the status line and in the
preview's tooltip whenever one is on screen. Counted on the Anno 2205 install, 89 movies, first frames:

| Shown as | Why | Draws | Movies |
|---|---|---|---|
| tinted 2205's panel blue-grey (its own shading kept; the tooltip says it is a placeholder) | a white or light-grey base (`color_base_light`, `color_gradient`, `Tile_white`: the `color_*` bases of the colour scheme) that the code colours at run time (`ColorManager`, `SetTint`) | 2,303 | 63 |
| grey square with hatching and a cross | `rtt_*` pictures, which the game renders live from its 3D scene (portraits, the minimap) | 28 | 18 |
| hatched grey box with a red cross | a picture the install has no file for (the status line names the first) | 2 | 2 |

The preview applies the whole colour transform (multiplier and offset, through nested sprites). It clips masked depths
and composes the blend modes Qt has. It draws a clipped bitmap once with its edge pixels beyond it. What remains white
is a real white picture: 25 draws in two movies (`minimap`, `economic_espionage`: the minimap's dots and a filled
circle, which the code also colours).

Still not drawn the way the game does:
- filters (a placement's filter list needs the GPU; the reason appears if one is used, none is in the 2205 movies);
- blend modes Qt lacks (named in the status line);
- the colours the code sets.

The preview cannot know those, so a swatch has no colour of its own here.

Note: counted on 2026-11-16. Before the preview applied the whole transform, 2,246 draws in 63 movies came out as
plain white or near-white squares. Nearly all came from the bases above (the rest from a few more white pictures),
because the preview applied a colour transform's alpha only.

## Changing the code with a Flash tool

1. `extract <movie> --to work` (or the window's export) gives `work/<movie>.swf`.
2. Open it in a Flash decompiler that edits ActionScript 3 (JPEXS Free Flash Decompiler does), change the code and
   save. The game's own tags show as unknown tags there: leave them in place.
3. Run `set edits.json movie <movie> work/<movie>.swf` (or *Replace movie from a Flash tool*), then build. The build
   re-signs the movie. The window drops the edits that were recorded for that movie before, since they were made on
   the old one.

## How the game gets the files, and what it must confirm

The build appends our archive to the game's archive index and points the changed files' entries at it, in the same way
the island editor does for 2205's maps (format doc, "Getting a changed file into the game"). Install keeps the replaced
index, and uninstall gives it back. If the island editor's map edits are installed too, take them out in the reverse
order.

These points are not confirmed in the game yet:

- the game starts with the changed index and draws a changed movie, picture or icon;
- a picture of another size is drawn at its new size (the movies and `pngsizes.dat` are updated);
- a movie saved from a Flash tool runs;
- the launcher's file check may put the original `file.db` back: install again after a repair.

Note: the editor was written on 2026-11-07, and the list above is as of that date.

## Language

The Flash UI editor's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"). It applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](../../suite.md#language).
