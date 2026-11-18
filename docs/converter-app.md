# The Converter app

The Converter app turns single files, assets and islands of the Anno games into other formats, and back where a way back
exists. Every entry is a command of the command line. The app shows that command's own options as a form, runs it on a
list of files or folders and writes into a folder of yours or into the project. The command line stays the reference:
the app shows the exact line it runs, and a line copied from there does the same in a console.

The code is `src/annotoolbox/converterapp/`. The suite opens it as `--app converter`.

## The entries

22 entries, by category. "Games" are the games whose files the entry takes, and "From the game" lists that game's
files to pick from.

| Category | Entry | Command | Games | Notes |
|---|---|---|---|---|
| Models | RDM model to glTF | `rdm export` | 117, 1800, 2205, 2070 | every mesh, material, skeleton and animation ([`rdm-gltf.md`](rdm-gltf.md)) |
| Models | glTF to RDM model | `rdm import` | any glTF | drops what a modelling tool added, says what |
| Models | RDM into 117's vertex layouts | `rdm relayout` | 117, 1800, 2205, 2070 | a model of another game in the layouts 117's files use |
| Models | Granny model to RDM | `rdm from-granny` | 1404, 1701 | for 2070, 2205, 1800 or 117 (`--to`); its animations from `--anims` |
| Models | Granny model to glTF | `export --kind model` | 1404, 1701 | `--format glb` or `gltf` |
| Models | glTF to Granny model | `rdm granny` | any glTF | a `.gr2` per source for 1404 or 1701, with `--place` the config and info file |
| Textures | Texture to PNG, EXR or HDR | `export --kind texture` | every game | BC1 to BC7, BC6H, cubes, arrays; `--all-mips`, `--hdr-form` |
| Textures | Picture to PNG | `export --kind picture` | every game | TGA here, JPEG, BMP and GIF with Pillow |
| Textures | Picture or texture to BC7 DDS | `texture bc7` | any picture, any game's DDS | the full mip chain; `--srgb` for a colour texture |
| Sounds | Wwise sound to WAV or Ogg | `export --kind sound` | every game | Vorbis needs the codebook file ([`exporting.md`](exporting.md)) |
| Sounds | Sound bank to sounds | `export --kind bank` | every game | the bank's sounds, its events' sounds, a JSON listing |
| Sounds | Audio to Wwise sound | `sound wem` | WAV, Ogg, MP3, FLAC | PCM, ADPCM, or Vorbis with ffmpeg; `--version` the bank's |
| Videos | Movie to MP4 or WebM | `export --kind movie` | every game | through ffmpeg; Bink 2 through the game's own Bink library |
| UI | UI file to JSON | `export --kind ui` | 117, 1800 | the decoded object tree |
| UI | 2205 UI movie to SWF and pictures | `flashui extract` | 2205 | a folder per movie ([`ui/2205/flash-ui-editor.md`](ui/2205/flash-ui-editor.md)) |
| Texts | Language text to CSV | `export --kind text` | every game | `guid,text` rows; the files must lie under `loca/` |
| Islands | Island to maps and lists | `export --kind island` | every game | objects as CSV and JSON, the height map as 16-bit PNG and EXR |
| Islands | Island to another region or game | `island port` | 117, 1800 | the sources are game paths; one mod for all of them |
| Archives | RDA archive to files | `rda extract` | any `.rda` | a folder per archive ([`rda-format.md`](rda-format.md)) |
| Archives | Folder to RDA archive | `rda pack` | any folder | `--version` 1.1, 2.0 or 2.2 |
| Data | Data file to JSON or CSV | `export --kind data` | every game | XML and FileDB documents; the biggest table as CSV |
| Data | Particle effect to JSON | `export --kind effect` | 117, 1800, 2205 | a baked `.rdp` |

Not offered here, and why:

| Asked for | Where it is instead |
|---|---|
| a game's texts as CSV, XLIFF or PO and back | the Texts app (two languages of an open game side by side; [`text-editor.md`](text-editor.md)) |
| a Phoenix UI file from its JSON | none yet: the UI app edits the files; JSON is a reading aid |
| a FileDB document from JSON or XML | none yet: the JSON is a reading aid, not byte-exact ([`exporting.md`](exporting.md)) |
| a model config moved to another game | none yet: the Objects app edits a config of each game |
| a savegame into another format | none: the Saves app reads and writes saves of their own game only |

## The window

On the left the entries by category, with a filter. On the right the chosen entry:

- its title, what it does and the command line it runs (the sources and the target as `<source>` and `<target>`);
- **Sources**: the list, with *Add files...* (Ctrl+O), *Add folder...*, *From the game...* (Ctrl+G) and *Type...*. The
  game box above it picks the game that *From the game...* reads. A source is listed once;
- **Target**: the output folder; *Project folder* sets the open project's `converter` folder (the workspace's when no
  project is open);
- **Options**: the command's options, each with the command line's own help under it. A starred one is required;
- *Convert* (F5) and *Stop* (Shift+F5), the progress over the sources and the log.

Every menu entry and every named button has an access key, none given twice (`tests/test_keyboard_access.py`).

## Sources and targets

*From the game...* opens the game's archives once (the suite's index, off the window's thread) and lists its files of
the entry's kinds. The chosen file is copied, as stored, into a new folder of the workspace under its game path
(`<workspace>/converter/game-files/<game>/<time>/data/...`), and that folder becomes the source. The game path is kept,
so the export still knows a language text by its `loca/` folder. An entry whose sources are game paths (the island
port) takes the path itself. An entry with a list of its own (2205's UI movies) offers that list.

The app never writes into a game: a target inside an install or a game's Documents folder (mods, saves, logs) is
refused before anything runs (`gamefiles.refused_target`).

## The form

`commandform` reads the command's argparse parser: a switch is a check box, a choice a list, a number or a text a line,
a path a line with a picker, and an option that takes several values a line with the values separated by `;`. The
sources and the target have boxes of their own, and options the entry fixes (`--kind`) are not shown. A value equal to
the command's default is left out of the line, as a person would leave it out. An entry may narrow a free option to a
list (`--format` of an export), and may fill options from the settings (a game's folder) or from the source game.

## Running

`runner.run` runs the command lines in this process, on a background thread. An entry that takes one source at a
time runs once per source; the others (the island port) run once for the whole list. While a job runs, what the
command prints goes to the log line by line; the other threads' output is left alone. **Stop** ends the job at the
next line the command prints, or before the next source. A command that fails ends its own line with `error:` and the
next source goes on. The status line says how many runs ended with an error.

## The commands

Two commands were added for the way back into the games' formats (`converterapp/commands.py`, in the Toolbox's command
line):

```bash
anno-toolbox texture bc7 wall.png icons/ -o out --srgb      # pictures or DDS files as BC7 DDS with every mip
anno-toolbox sound wem voice.wav -o out --codec adpcm      # audio files as Wwise .wem (PCM, ADPCM, Vorbis)
```

Each takes files and folders (searched recursively), writes into `-o` with the folders' layout kept, and leaves a file
that exists unless `--force`.

## An entry

An entry is an `extspecs.Conversion`, registered at the extension point `features.CONVERSIONS` (an extension's
entry of an existing key takes its place). Its fields:

| Field | What |
|---|---|
| `key`, `category`, `title`, `summary` | the entry; the category is a key of `catalogue.CATEGORIES` |
| `command`, `register`, `words` | the command line's words, the function that adds the command's group to argparse sub-parsers, and the words from there to the command |
| `inputs`, `source` | the argument that takes the sources, and what they are: `files`, `folder`, `game_paths`, `values` |
| `output`, `output_file`, `output_subfolder` | the argument that takes the target; a file of that suffix, or a folder, per source |
| `suffixes`, `games` | the files the entry takes, and the games whose files it lists |
| `fixed`, `choices`, `hidden` | arguments always given, an option narrowed to a list, options not shown |
| `folders`, `complete` | options filled from the settings, and the entry's own filling (the source game, its installs) |
| `per_input` | one run per source |
| `candidates`, `prepare` | the entry's own list of sources, and what the command line does before the command |

`features.CONVERTER_PAGES` takes a page beside the entries (an `extspecs.ConverterPage`: a title and `make(shell)`). The app
then shows its entries and the pages as tabs.

## What to check

- On an install: pick a texture, a sound and a model from each game with *From the game...* and run its entry.
- The island port from the window, into a scratch folder, then the mod in the Mods app.
