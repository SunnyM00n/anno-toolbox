# Exporting game files

Any file of an installed game can be exported. It is always exported **as stored** (the bytes the game holds, under
the same path). Where one exists, it is also exported in a **made form** that other tools read. The viewer's
**File > Export** works on the open file, the file browser's selection, a folder or a filter. The other apps reach
the same export for what they show (an asset's icon, a model, a UI file's pictures, an island's maps). The code is
the `exporting` package, with no window in it.

```python
from pathlib import Path
from annotoolbox.exporting import run

source = run.FolderSource(Path("unpacked/game"))          # or a game tree opened by the suite
entries = run.export_many(source, ["data/ui/a.dds", "data/graphics/house.rdm"], {"png", "glb"}, Path("out"))
```

`export_path` exports one file. `export_many` exports a list and writes the index. A file that cannot be exported is
an entry with status `failed` and the reason. The stored file is still written, and the batch goes on. Files that
exist are left alone unless `Options.force` is set (the entry says so).

**Safety of the output folder.** Every file is written by name below the output folder:

- A name that would land outside it (`..`, a drive prefix; an island archive member or a config's mesh reference may
  carry one) is not written, and the entry is `failed` with the name.
- A write error (a directory in the way, a name Windows refuses) is the entry's failure, not the batch's. The index
  is written even when a run stops part way.
- A file is written whole or not at all.
- Two sources that make one output name in a batch (`a.tga` and `a.png` both give `a.png`; `x.xml` and `x.ifo` both
  give `x.json`) do not overwrite each other. The later one is `failed`, naming the source that made the name first.
- In a bank export, two media or two events of one name get their id added to the name. Device names (`CON`, `NUL`)
  get a leading underscore.

A model config exports the meshes its `MODEL` and `CLOTH` parts name as the game loads them (a 117 config names the
source `.fbx`, the game ships the `.rdm`).

## What each kind exports

The keys are the same for every kind, so one choice applies to a whole selection and each file takes the keys its kind
knows. A key a kind does not know is ignored.

| Kind (suffixes) | Forms | Notes |
|---|---|---|
| Texture (`.dds`) | `png`, `png16`, `exr`, `hdr` | BC1 to BC7, BC6H, the plain and float layouts, cubes and arrays (numpy). A float texture's 8-bit PNG is sRGB at `exposure` stops; the 16-bit PNG holds the linear values clipped to 0..1; `exr` keeps half or float values; `hdr` is Radiance RGBE. Level 0 by default, `all_mips` writes every level (`_mipN`), cube faces and array slices are `_faceN` / `_sliceN` (`all_faces`, `all_slices`). With both `exr` and `hdr` asked, `hdr_form` picks one. A volume texture is exported as stored only. |
| Picture (`.png .tga .jpg .bmp .gif`) | `png` | A PNG is already one. TGA is read here (no library); JPEG, GIF and BMP need Pillow. |
| Movie (`.bik .bk2 .webm .mp4 ...`) | `mp4`, `webm` | Through ffmpeg. ffmpeg reads Bink 1 (`.bik`); Bink 2 (`.bk2`) it does not, so a `.bk2` is decoded by a game's `bink2w64.dll` (the opened game's install, or `ANNO_BINK_DLLS`; a folder of loose files is never searched for one) and ffmpeg only encodes it; without that DLL the note names RAD Video Tools, with which a plain video has to be made first. |
| Sound (`.wem .wav .mp3 .ogg`) | `wav`, `ogg` | A PCM or Wwise ADPCM `.wem` becomes a WAV with no tool. Wwise Vorbis becomes Ogg Vorbis with the codebook file of the ww2ogg tool; its WAV, any Ogg made from PCM, and `.mp3` / `.ogg` into WAV need ffmpeg as well. |
| Sound bank (`.bnk`) | `wav`, `ogg`, `json` | Every embedded sound (and the streamed `.wem` files beside the bank) under `<bank>/media/`, named from the bank's info file, with the `.wem` as stored; each event's sounds under `<bank>/events/<event>/` where the bank's containers are read exactly (the note counts the events that were walked by a guess); `json` is the listing: objects by kind, sounds, events with their media, music tracks. |
| Model (`.rdm .gr2 .cfg`) | `glb`, `gltf` | An `.rdm` goes through the RDM toolchain ([`rdm-gltf.md`](rdm-gltf.md)): nothing added, nothing lost. Options `axes`, `strict`, `lods` (the `_lodN` files beside it), `with_textures` (PNG copies of the materials' DDS textures that the files hold), `anims_folder` (the animation files of a folder that move the mesh). A Granny `.gr2` goes the same way once it is RDM records (meshes, skin, skeletons, its animations and the `.gr2` animations of `anims_folder`; 1701 divided by 32: [`rdm-gltf.md`](rdm-gltf.md), "Granny models"). A config gives every `.rdm` it names as a glTF of its own; its parts, joints and sequences are not in the glTF. |
| Data (`.xml .ifo .fc .ctt ...`) | `json`, `csv` | FileDB documents as JSON: structure and small values, a binary attribute as its size and first bytes (a reading aid, not a byte-exact dump). XML as JSON (tag, attributes, text, children), and `csv` as the file's biggest table of repeated elements; an `Asset` row starts with its GUID, template and name, the other columns are the element's leaf paths. Text, Lua, JSON and CSV files have no made form. |
| Language text (`.txt` under `loca/`) | `csv` | `guid,text` rows; `Options.language` skips the files of other languages in a batch. |
| UI file (`.ui`, Phoenix) | `json` | The decoded object tree: type names, GUIDs, the fields as the reader decodes them (binary values by size and first bytes). |
| Effect (`.rdp`, `.efc`) | `json` | A baked particle file as its header, materials and, per particle, its frames and first and last keys (quantised values as stored). `.efc` is text: as stored. |
| Island (`.isd`, `.a7m`, `.a7t`) | `json`, `csv`, `png16`, `exr` | Anno 2070 `.isd`: `_objects.csv` and the JSON (size, sea level, objects), `_height.png` (16 bit, scaled between the lowest and highest corner, the note gives both) and `_height.exr` (the heights themselves). Anno 117 (and 1800) `.a7m` archives: `<name>/members.json` and each FileDB member as JSON; the island's own `<name>_height.png` / `_height.exr` (the coarse height map of `rd3d.data`, `2 * size + 1` samples across, world units, rows by z) and `<name>_objects.csv` / `_objects.json` (group, GUID, variation, world position, rotation as a quaternion, scale, the object's own config), read from the archive alone (`formats/a7mread`). The stored height map is the render map; the game's own gameplay heights in `gamedata.data` are not exported. |
| Font, anything else | none | As stored. |

## Options

`Options` (`exporting.run`): `png_depth` (8 or 16: 16 writes the PNG as the 16-bit form), `all_mips`, `all_faces`,
`all_slices`, `hdr_form` (`exr` or `hdr`), `exposure` (stops, for the 8-bit PNG of a float texture), `axes`
(`gltf` mirrors on x, `anno` keeps the stored values), `strict`, `lods`, `with_textures`, `anims_folder`, `language`,
`force`.

## Optional tools

The tools are optional and never shipped. The stored file is always exported. A form that needs a missing tool is
not made: the entry's status is `tool-missing`, and its note says which tool it is and where it goes (the tools
folder, the path, or the variable `ANNO_FFMPEG` / `ANNO_WW_CODEBOOKS`). `run.missing_tools(forms)` returns the
message before a run.

| Tool | Needed for |
|---|---|
| ffmpeg | `mp4`, `webm`; sound forms that are not already the form (Ogg from PCM, WAV from Ogg or MP3, WAV of Wwise Vorbis) |
| the ww2ogg codebook file (`packed_codebooks_aoTuV_603.bin`) | Ogg and WAV of Wwise Vorbis sounds |
| a game's `bink2w64.dll` | Bink 2 movies (decoded from the user's own install, never copied) |
| RAD Video Tools | Bink 2 movies when no such DLL is found: a plain video is made with it first (not driven by the export) |
| numpy | textures (every decoded form), PNG copies of model textures |
| Pillow | JPEG, GIF and BMP pictures as PNG |

## The index

Every `export_many` writes `index.json` and `index.csv` into the output folder. They hold, per source file: its path,
its kind, the archive it was read from, its size, the files written (relative to the folder), the status (`written`,
`skipped`, `failed`, `tool-missing`) and the notes (what was decoded, scaled or left out).

A game file that is itself called `index.json` or `index.csv` at the output root is kept: the index is then written as
`export-index.json` and `export-index.csv`.

## Open items

| Item | Estimate |
|---|---|
| OBJ as a second model form (only if asked; glTF stays the default) | 2 hours |
| A config's parts placed in one glTF (the meshes at their transforms, the joints a part hangs on) instead of one glTF per mesh | 1 day |
| Bink 2 when no tool reads it: the note names RAD Video Tools; a driven run of it is not part of the export | open |

Checked (`tests/test_export_tools.py`, `tests/test_export_data.py`):

- with the real ffmpeg: a WAV to Ogg Vorbis and back, an Ogg to WAV, and a movie made by ffmpeg to MP4 and WebM;
- on an install: a real Wwise Vorbis sound (117, the codebook file) to Ogg and WAV, and a real Bink 1 movie (2070)
  to MP4 (`ANNO117_DIR`, `ANNO2070_DIR`, read only).

Each test skips without its tool or install. A Granny file is tested from the synthetic writer of
`tests/test_granny.py` (plain and Oodle-compressed), and a damaged one is a failed entry. The island forms are
tested on a synthetic archive.

Note: checked on 2026-11-09.
