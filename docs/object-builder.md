# The object builder: a user's guide

The suite's **Objects** app edits a model config in the same way the studio's own object builder did. It shows these
things:

- the config's parts as a tree;
- the selected part's fields;
- the model in 3D, following every edit;
- the sequences on a timeline;
- the object's boxes, footprint and feedback dummies, drawn over the model.

It only reads your own installs of Anno 117, 1800, 2205, 2070, 1404 and 1701, and never writes into a game. It saves
into your project, from which the suite builds the mod. The design and what is still open are in
`tool-suite.md` section 23.

## Start it

```bash
anno-toolbox --app objects      # the suite's window on the object builder, beside the other apps
```

The game folders are the suite's (File > Games and folders in the island editor). **File > Open game** picks a game.

A double-click on a model config in **Files** (or File > Open a file by its path, Ctrl+O) opens its object. The object
is the `.cfg` (1701: `.mcf`, 1800 and 2205 props: `.prp`) with the `.ifo` and `.fc` of the same name beside it. Opening
any of the three opens all of them.

Anno 1701 has an `.info` beside each `.mcf` and no feedback file. An object config (`.ocf`) opens its first `.mcf` with
its `.info`. The info tree is shown and edited as text.

Its grid cells are drawn over the 3D view as rectangles on the ground. They are the footprint and the other blocks,
moved by the `.ocf`'s offset when the object was opened by it. The bounding box and the dummies' boxes are drawn with
them. See [`formats/granny.md`](formats/granny.md), "1701's info files".

Note: as of 2026-11-13, the code draws the 1701 info, but the drawing was not yet looked at in the window.

## The window

| Part | What it does |
|---|---|
| **Files** (left) | the game's folders, searched as in the viewer |
| **Object** (left) | the config's lists and parts with the studio's titles (Files, Models, Materials, Cloths, Effects, Lights, Terrain Decals, Billboard Instancing, Sequences, Transformers), then the info file's boxes and areas, then the feedback file's dummies and feedback; a list a part may hold but has not yet shows greyed, "(empty)" |
| **3D view** (middle) | the viewer's view (left drag turns, right drag moves, wheel zooms, F frames, a click selects the part under the mouse); a moment after each edit it is drawn again, the camera stays |
| **Properties** (right) | the selected part's fields in file order, grouped by a 117 material's headings (Common, Normal, DyeMask ...); double-click a value to change it, tick a switch, pick from a drop-down (117's `ShaderID`, a track element's `Type`), tick the bits of a `Conditions` number, pick a colour for a `.r .g .b .a` group, pick a file among the game's; a blue file name opens the file in the viewer on a double-click of its name |
| **Sequences** (bottom) | the config's sequences: Play, speed, the clock; each track a row of bars (blue: an animation, orange: an effect, grey: a state) |
| **Feedback loops** (bottom, a tab beside Sequences) | the feedback file's people and vehicles: an actor (an item of the file), a state (Not working, Working) and a loop (Enter, Repeat, Leave); the loop's elements as bars as long as one play gives them (blue: a walk, orange: a play, grey: a wait, purple: a show or hide, red: a barrier; pale: not reached by the play) |

Three kinds of marks are drawn over the 3D view:

- the info file's **boxes**: bounding, mesh, hit and intersect boxes in cyan, dummies in green
- the info file's **areas**, in red: the build blocker, which is the footprint, and the other blockers
- the feedback's **dummies**, as small crosses with their names

The selected one is yellow. Each kind is a switch in View.

**View > Simulate cloth** moves the flags, banners and sails by the game's own cloth rules while the clock runs (Play
in Sequences). It works in every look, the games' own shaders included. The cloth is finer near the camera, as the game
makes it. **Torn cloth** shows the cloth of a damaged object where it may tear.

**View > Walking figures** puts the people and vehicles of the feedback file into the view. They walk their loops for
the state picked in **Figures' state** (Not working, Working). An actor whose unit is not found walks as an orange
card. `engine-cloth.md` says what is the game's and what is chosen.

In Anno 1701's mesh configs (`.mcf`) every part is named by its class type and what it points at: `Mesh  house.gr2`,
`Effect  smoke.efc`, `Group  Dummies`, `Dummy  fire09`, `Level of detail  61-100`, `Offset`, `Rotation`, `Scale` and
so on. A file field lists the kind of file it names (a mesh field the `.gr2` files, an effect field the `.efc`).

## Editing

- **A value**: select the part, double-click the value in Properties, type, and press Enter. A number that is not a
  number is refused (the status line says why). A binary value (a feedback file's vectors) shows its bytes; short ones
  are edited as hex.
- **A drop-down, bits, a colour, a file**:
  - A field with a closed list of values is a drop-down in its row. The current value is kept when the list does not
    know it. Such fields are `ShaderID` (Anno 117's 25 shaders, and for 1800 and 2205 the ones their install's own
    shader sources name) and a track element's `Type` (animation, effect, state, and in the newer games sound).
  - A double-click on the value of 117's `Conditions` ticks its bits by name (`Selected`, `Common01` ...).
  - A double-click on the `.r` value of a colour group opens the colour dialog and changes the group's fields as one
    step. A colour above 1 (a light's intensity) is edited as numbers.
  - A double-click on a file field's value lists the game's files of its kind, with a search box. A newer game's
    textures are listed by the source name the config holds, never the lower quality levels. The first match is
    selected, and the value line below the list takes a pick or what is typed. F2 types any value.
  - Every other field of the other games stays a number, because their files give no names for it (tool-suite
    section 23, "Choice lists for the other games").
- **Handles** (Edit > Handles: Move W, Rotate E, Scale R, Off; the keys work in the view): the view shows handles
  over the selected part (a model, a nested file, an effect, a light ... or one of its orientation transformers).
  - Move shows three arrows and a square on the ground, Rotate shows three rings, and Scale shows a circle.
  - Drag one, and the part follows the mouse.
  - The fields of its first orientation transformer (`Position`, `Rotation`, `Scale`) are written when you let go, as
    one undo step.
  - Shift snaps a turn to 5 degrees.
  - A part with no orientation transformer gets one (and its list).
  - A press away from a handle turns the camera as before.
- **The footprint and the other areas** of the info file: select an area (the build blocker ...), and a square marks
  each point.
  - Drag a point over the ground. It snaps to half units, the grid the game's own footprints lie on (hold Alt to place
    it freely).
  - Double-click a side to put a point there, and right-click a point to take it out (a polygon keeps three).
  - **Edit > Draw a footprint** replaces the selected area by clicks on the ground (Enter or a double-click finishes,
    Esc cancels). With nothing selected, it makes the `BuildBlocker` that a file lacks.
  - An `.ifo` is saved whole.
- **Edit > Add a part** (Ins): on a list (or a part in one), this adds a new part of the kind the list takes. When the
  list takes several kinds (a Transformers list), you choose.
  - The new part is a copy of the first part of that kind in the file, so it has every field the game expects (a 117
    material has more than a hundred).
  - Where the file has none, the new part is a small part of the file's own form (a file, an orientation, a light, a
    sequence, a track and its element).
  - On a greyed "(empty)" list, the list is made too.
- **Edit > Add a part from another file**: type a game path, then pick the part of the kind the list takes (a material
  of another building, say).
- **Duplicate** (Ctrl+D), **Remove** (Del) and **Move up / down** (Alt+Up / Alt+Down) act on the selected part.
- **Sequences**: drag a bar to move it in time, and drag its ends to change its start or end. A double-click selects
  the element, so its type, model, animation or effect can be changed in Properties. **Add track** and **Add element**
  add to the chosen sequence. Play shows the animations the sequence plays.
- **Feedback loops**: drag a bar's right end to change its length. The length is a play's time, a wait's two times or
  a walk's speed. A walk at its sequence's own speed and the instant elements have none of their own.
  - Drag the bar or use **Earlier** / **Later** to move it in the loop.
  - **Add** puts an element of the chosen kind after the chosen bar (a copy of the file's first of that kind), and
    **Remove** takes the chosen one out.
  - A double-click shows its values in Properties.
  - The walking figures follow the edited file.
- **Undo and redo** (Ctrl+Z, Ctrl+Y) take back every edit, in every file of the object. The title shows `*` while
  there are edits not yet saved.

A file that does not read back to its own bytes opens read only and says so. Of all the games' files, this is one 1800
feedback file.

## Saving

**File > Save into the project** (Ctrl+S) shows the ways to save, each with the reason it is open or not, and what it
writes:

1. **A new file** at a path you name (`data/my-mod/.../name.cfg`): the whole object, with its `.ifo` and `.fc` renamed
   with it. Nothing of the game changes. An asset (the asset editor) or another config must name the new file before
   the game draws it.
2. **Replace the game's file**: the changed files whole, at their own paths.
3. **A patch** (Anno 117, and 1800 by the same rule, unchecked): a `<name>.cfg.xml` (and `.fc.xml`) of ModOps that
   turns the game's file into yours, so a later game update of the rest of the file is kept. An `.ifo` cannot be
   patched; it is written whole. A patch cannot carry a 117 material's heading tags or a binary value. The dialog says
   so, and a whole file is then the way to save.

The files go into the open project, under `object-edits/<game>/`, and the project's build puts them into the mod at
their game paths. When a game is linked to the suite, it is told which files changed. The status line says what to do
to see them (for a model config: restart the game).

A file is written whole or not at all. A half-written `.part` file is never part of the mod. A path that leaves the
game's folders (`..`, a drive letter) is refused.

A second save replaces what the project holds of the object, and the dialog lists it. A file that an earlier save made
in the other way (`x.cfg` after a patch, `x.cfg.xml` after a replacement) is removed, so the mod never carries both.

When you open an object that the project already holds files of, they are laid over the game's. A whole file replaces
the tree, and a patch is applied to the game's file by the mod loader's rules. The window therefore shows your earlier
edits, and a second save keeps them. The status line says so. It also says what of a patch no longer fits the game's
file.

A typed value that would break the config (`<`, `>`, a bare `&`, `CDATA[`, `nan`, `inf`) is refused. The game opens in
the background, so the window stays usable while 117 is indexed.

**Anno 117's `shared/` copies.** About half of 117's model configs have a baked copy under `shared/`, which the game
reads in place of the `data/` one. The builder opens that copy, as the game does (the title names it). A replacement or
a patch goes to the `shared/` path.

With View > Read the baked shared/ copy switched off, you edit the `data/` file. Replacing or patching it is then
refused, because the game would never read it.

The baked copy's `.bfg` (baked data matched to the parts by their place in the lists) is still read on top of a
replacement. When you add, remove or move parts of a `shared/` config, the save therefore also writes a `.bfg` that
follows. It holds the entries of the parts that stayed, none for new ones, and no far-view model (the notes of the
dialog say so). A part you move counts as taken out and put in.

The baked content itself (the voxels, the triangle groups, the average colours) comes from Ubisoft's build and cannot
be made here. A new file has no `shared/` copy and is read as written.

Note: the `shared/` saving was never tried in the game.

## What a person should try

1. Anno 117: open `residence_tier01_estate06.cfg` (its `shared/` copy opens). Select a FILE (a tree) and change its
   `Position.y`: it moves in the view. Undo. Look at the red footprint and the cyan boxes, and switch them off in View.
2. Select a material and change `cModelDiffTex` to another texture. Double-click the name to see the texture in the
   viewer (in the suite's window).
3. On a unit (`priest_cernunnos_lean_m_01.cfg`): play a sequence, drag its bar's end, and play again.
4. Add a light on the house's "(empty)" Lights and save as a patch. Read the `.cfg.xml` in the dialog. Build the
   project and start the game with it (the in-game check this guide cannot make).
5. Do the same in Anno 2070 (`bakery_ecos.cfg`: new file or replacement only), 1800 (a patch, unchecked in the game),
   2205, 1404 and 1701 (open an `.mcf`: its parts carry their class names).
6. Select a model of the house and choose Edit > Handles > Move. Drag an arrow, drag the ground square, and undo. Do
   the same with Rotate and Scale. Select the footprint in the info file: drag a point, double-click a side,
   right-click a point, then choose Edit > Draw a footprint.
7. Duplicate a model of a `shared/` config and save as a patch: the dialog lists the `.cfg.xml` and the `.bfg` that
   follows. Save, close, and open the object again: your edits are there. Edit more and save: both sessions' edits are
   kept.
8. Select a light and double-click its `Diffuse.r` for the colour dialog. Select a material, and change its `ShaderID`
   in the drop-down and `cModelDiffTex` by the file list.

## Language

The object builder's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
