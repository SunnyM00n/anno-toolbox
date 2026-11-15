# The UI editor: Anno 117's and Anno 1800's interface files

The suite's **UI** app opens the Phoenix files that Anno 117 and Anno 1800 build their interface from (scenes and
widget files in `data/ui/studio/generated/`). It previews them with sample values in place of the game's, shows their
bindings as a node graph, and edits them with undo. It keeps the changed and new files in the open project, whose
build puts them into the mod. The design and its status are in `tool-suite.md` section 20. The file
format is described in `formats/phoenix-scene-format.md`. The UI's own rules
(layout, widget types, components, masks) are in the UI reference. Anno 2205's Flash interface has
its own app (UI 2205, [`ui/2205/flash-ui-editor.md`](ui/2205/flash-ui-editor.md)).

**Nothing the UI editor writes has been loaded by a game yet.** The codec's output is known to load (changed files and
new widget files). The editor's edits on top of it are currently unverified.

Note: the codec's output was loaded in the game on 2026-09-21; the editor's status is as of 2026-11-09.

## Starting it

- `anno-toolbox --app ui` starts the suite's window on it. In the suite it is **UI** in the app switcher (Ctrl+5).
- File > Open game takes the game's folder from the suite's settings (the island editor's File > Games and folders).
  The first opening indexes the game's UI files (117: about 6 seconds). The index is kept in the workspace's
  `game-index/<game>/ui/`, the one index folder that every app of the suite uses per game. The editor then reads in the
  background which instance names each widget file, so the file list can show widget files by those names.
- File > Open a folder of loose files reads an unpacked game or a mod folder instead.

## The window

| Where | What |
|---|---|
| Left, **Files** | the scenes by their name (a scene is named after the UI state that shows it), the widget files by the names their instances give them (or their GUID), the other kinds (images, text styles, components ...) when the filter finds them; a `*` marks a file the project holds |
| Left, **Elements** | the open file's instances and groups (a group's instances under it); a container names the widget file it places; a hidden element is greyed |
| Left, **Palette** | the Phoenix Widget Library the game ships (`data/ui/pwl/`): new images, labels, containers and videos, then the components, widget types, effects, enums and game objects by folder, each with its description; a double-click adds one |
| Middle, **preview** | the file drawn at its reference size (1920 x 1080 for every scene); wheel zoom, middle drag (or Alt drag) pans, F fits |
| Right, **Properties** | the selected element's fields (every stored field by its name, or by its hash where the format has no name; absent fields greyed), its values of its widget file's properties, its components with their properties, its effects, and the built-in properties a binding can reach; a value is edited in place |
| Right, **View models** | what the file can bind to (below) |
| Right, **Sample values** | the values the preview read where the game would give one (below) |
| Bottom, **Bindings** | the bindings as a node graph (below) |
| Bottom, **States and events** | the timeline of the file's animations (and of its tweens), the design view switch, the events and actions |
| Bottom, **Sequence** | the sequence editor: the open animation's keys on a timeline, the file's tweens (below) |
| Bottom, **Checks** | what the preview could not resolve: a widget file or picture the game does not have; how many values are placeholders |
| Bottom, **Game dump** | a layout dump of the running game held against the preview (below, "Comparing with the game") |

## The preview

The preview composes a file as the game assembles it (`ui/117/layout.md`): the reference space,
anchors (nine points of the parent's computed size), pivots (also as a fraction of the size through the
`ProportionalPivotPoint` component), scale as a multiplier of the natural size (a picture's size, a label's text, a
container's content; a size a binding writes is the size itself), the modes that cancel the ancestors' scale and the
aspect ratio, the containers' sizes (their fixed size, `SizeOverride`, else the union of the visible children that
count for it; a group is as large as what it holds), stacks (`StackLayout` with its spacing, margin and inverted
order, the children's `PaddingUtility`), nine-slice pictures, colour tints with palette entries, alpha down the tree,
the blend modes normal, add and multiply, masks (below), labels in the game's fonts, and the widget files a container
places, as deep as they go.

**Composition**: a placed widget file is evaluated with what its instance passes in (its `PropertyValues` and what the
placing file's bindings write into it), and the placing file reads back what the placed file computes: a binding such
as `Popup_Base.Content_PositionCenter-.x` gets the value the popup's own bindings produced. A value a file only
declares (its default) counts as unknown, so the reader keeps its own stored value: an animation's start state does not
hide a whole scene.

**Bindings run in the preview**: each template's bindings are evaluated with its operators, so a label shows the text a
custom property gives it, a size follows a hub property, a value flows from a scene down into its widgets. Where the
game would fill a value (a scene's data object, a list's elements), the **sample values** stand in, and where none is
set a **placeholder** is made up (below).

**Lists**: a widget file whose widget type is a list or grid (`List`, `Grid` and the game's scrollable grid, the
dynamic widget) places its one item child once per element of its data source: the files' own preview elements, the
elements the sample values hold, else three placeholder elements. A list lays the copies along its axis with its
spacing and visible count; a grid in rows of its visible column count, capped at its visible cells; each copy is bound
to its element (`DataMode` 4) and is its own item (`DataMode` 3).

**Masks and effects** (the game's UI shader, `rendering/ui-117.md`): a mask covers the later
siblings that receive it and their whole subtrees, up to the first sibling that does not; a hard mask clips to its
rectangle; a **soft mask** multiplies the covered pixels by a smooth step of its picture's alpha (from its
`AlphaThreshold` over its `AlphaGradient`; outside its rectangle nothing shows unless `ExclusiveMask`). A picture's
**effects** are applied in the shader's order: whiten, glow (inner and outer, from the 5 x 5 texels round each texel,
three blend modes), drop shadow (the alpha moved by `Strength` texels in `Direction`), alpha test, colorize (contrast,
brightness, grey scale, a multiply by a mix of two colours, alpha gamma), and the described brightness, saturation
and colour templates mapped onto colorize. The picture effects of a blur (nine taps), a distance field (alpha as a distance: soft edge, inner colour, outer glow),
a blink and a texture scroll follow the shader's formulas too.

**The 3D scene behind the UI** (View > Show the 3D scene behind the UI; off by default, the choice is kept): a still of
a 3D scene is laid behind the preview, and the effects that read the world draw from it. A widget with the
**background blur** shows the scene averaged over 5 x 5 pixels, opaque wherever its picture has alpha; with the
**world darken** the scene multiplied by the effect's colour in linear light by its intensity (alone: the unblurred
scene, darkened; both: blurred and darkened); the **island shading** turns an island picture's blue channel (the
distance to the coast) into hatching, a border and a contrast in the effect's colours. The scene is a made-up
**stand-in** (sky, sea, an island with roofs), **a picture you pick** (View > Pick a picture for the scene behind the
UI: a game screenshot, the viewer's "Save the picture", the island editor's view) or **a model of the game the
renderer draws** (View > Draw the scene behind the UI from a model of the game...: a building's or a ship's config,
drawn offscreen by the game's own shaders as the viewer draws it, no file in between; it needs a Direct3D or OpenGL
device) or **an island of the game the island editor's renderer draws** (View > Draw the scene behind the UI from an
island of the game...: an island file opened read only as the island editor opens it, drawn by its view in the game's
look; the first picture of an island takes minutes); View > Use the stand-in scene goes back. Save the preview as a picture includes it. With the switch off a widget with such an effect draws as its picture
does. The data, the shader's rule and what differs from the game (the scene is already tone-mapped, the hatch pictures
are a stand-in) are in `ui/117/world-effects.md`.

**Text** is drawn in the game's own fonts: a text style's localized font is followed to its font file
(`data/fonts/...`, the language's exception where one is set) and loaded; where the chain cannot be read the first font
of the game stands in. A label wraps at its text plane's width, cuts by character or with an ellipsis as its overflow
modes say, cuts whole lines or ends with an ellipsis vertically, spaces lines by the style's leading plus the label's
line spacing and its letters by the style's and the label's letter spacing; outlines are drawn under the glyphs and
shadows below them. Its size follows its sizing mode (the plane, the shown text, the larger or the smaller).

**Group rules and loaders** (inferred from the editor's descriptions, docs/ui/117/layout.md): a stack puts its
children one after another and aligns each across its axis (the group's `ChildrenAlignment`: begin, middle, end;
a child's `AlignmentUtility` overrides it); a background layout stretches the group's first layer round the others
plus its margin; an enum loader places the children whose `EnumEntry` is its current value (and those with none), a
game object loader its first child with a game object template; a radial list or wheel puts its items on an arc.
A layer the author hid in the editor (`IsHidden`) starts hidden unless a binding shows it; View > Show layers hidden
in the editor draws it. A chain of bindings runs until no value changes, so a value several links away from its source
arrives. A picture a binding gives a size is scaled to it with its pivot.

**The design view** (on by default, States and events): elements an animation fades in (stored alpha below 0.01) are
shown, a scale or size a binding sets to zero is left as stored, and a wrap width below two font sizes (the game binds
the real one at run time) does not wrap. Switch it off to see the file as it starts in the game.

**The timeline**: pick one of the open file's animations, or "the file's tweens, started together", scrub or play it;
its tracks write their values as the game's animator would, each key easing to the next by its interpolator.

**Pictures**: an image asset's `ImageId` names a `.png`; the game stores it as `<name>_0.dds` beside it, and the
`data/ui/fhd/` pictures under `data/ui/4k/`. A picture the game does not have is drawn as a flat grey rectangle and
listed in Checks; a picture the game names at run time with no sample value is a light placeholder tile with the slot's
name.

What differs from the game (open, `tool-suite.md` section 20):

- **Text**: the alignment within a plane's height (`AlignmentY`, stored as the bit pattern of a float) and the fifth
  sizing mode are not known (top, the shown text); justify is drawn left; the font data files' own outlines are not
  read (the font files are the same faces).
- **Effects**: the screen offset is not drawn; the background blur, world darken and island shading are drawn when
  View > Show the 3D scene behind the UI is on, over a stand-in scene or a picture (below; the rule and what differs:
  `ui/117/world-effects.md`); a texture scroll and a blink run over time (View > Play the effects over time, on by
  default; off freezes them at time zero; the clock runs only while the open file has such an effect); how the drop shadow's, the soft
  mask's and a container's effects reach the shader is inferred (a container's drawn effects apply to each picture
  below it).
- **Composition**: a stack's cross-axis alignment, a background layout, loaders (one child), radial lists (zero
  degrees up, clockwise) and the layers hidden in the editor are inferred rules (below); a container whose children
  reach into negative coordinates reports the largest right and bottom edge, which can be half the width the game
  gives it (the romanization centre piece), and a mask that comes after the widgets it should cut covers nothing.
- **Animations**: the numbering of a key's interpolator is inferred (0 holds, 1 linear, then the Widget Library's ease
  types from `Sine_In` on); events on a track are not fired.

## Bindings

A binding reads one or more **sources**, runs them through an **operator** and writes its **targets**. The graph draws
each binding as a row: the sources on the left, the operator in the middle (coloured by family; a `*` marks one whose
behaviour is known from its stored fields only), the targets on the right. An end is written as the element it starts
at, then its path: `Caption.Text`, `self.GameContext.<2b2357a9>`, `own.Title`, `constant 0.5`; `.x` / `.y` / `.z` is one
axis of a vector. By default the graph shows the bindings of the selected element; View > Show all bindings shows the
file's.

- **Right-click an operator**: change the operator (the families: Assign, Numerical, Maths, Casts and text, Comparison,
  Logical, Events, Enum, Arrays; 63 operators, each with what it computes), or remove the binding (an end that no other
  binding uses goes with it).
- **Edit > New binding** (Ctrl+B): a source (one of the file's own custom properties, a built-in property or event of an
  element, or a constant), an operator, a target.
- The ends of a template are one index space in the file: the property paths, then the custom properties' containers,
  then the constants. The editor keeps it whole when ends are added and removed.

## View models

- **This scene's data**: the scene's data class as the game's script type files describe it
  (`data/script/types/generated/rdui/gameobjects/*.lua`), guessed from the scene's name (`maintitlescene` ->
  `CMainTitleSceneData`); its fields, child objects and arrays open as a tree.
- **Field ids this file reads**: the paths through game objects that the file's bindings read. The ids are the game's;
  the type files give the names but not the ids. Right-click a class's field > **Name an id** gives an id a field's name;
  the names are kept in the project (`ui-edits/<game>-names.json`) and every path then shows them. Right-click a read
  path > **Bind it to the selected element's property** makes a binding from it.
- **UI data classes**: every class of the type files, found by the filter.
- **Script readouts**: **Load a script list** reads a `script-api.json` (a script library's functions); a readout can be
  added to the file as a custom property of its type, which the library can then fill.

## Sample values and placeholders

Every value the preview read where the game would give one is listed with its path; write a value and the preview shows
it at once. The files' own preview data for lists (`DataSource` values) seed it. The values are kept in the project
(`ui-edits/<game>-samples.json`; the keys start with the file they belong to).

**Placeholders** (View > Fill unbound data with placeholders, on by default): where a binding reads a game value that
has no sample value, a value of the kind the binding needs is made up and kept with the samples, marked as generated
(grey and italic in the panel): a text whose length suits the target (a header short, a description long), a plausible
number (a percentage 75, a count 12, money 1500), `true` for whatever decides a visibility or feeds a logical operator,
a light picture tile named after its slot for a picture named at run time, three elements for a list. Each binding
gets the placeholder of the kind it needs, also when another binding reads the same game value as another kind (a
label's text and a picture's visibility from one value: a text for the one, `true` for the other), so what the
preview shows does not depend on which part of the scene was placed first. The panel lists the value the first one
made. Write a value to keep your own; right-click > Reset to a placeholder drops it again. With the fill
off the scene shows only the values set by hand.

## Editing

Every edit goes on the undo stack (Edit > Undo, Ctrl+Z; Redo, Ctrl+Y); the window's title shows `*` while the file
has edits the project does not hold.

- **Fields**: double-click a value in Properties and type: numbers, `x, y, z` vectors, `true` / `false`, an enum value's
  name or number, a GUID for a reference (empty: none), `r, g, b` for a colour.
- **Values of a widget file's or a component's properties**: the same, under "Its widget file's properties" and
  "Components"; a value the instance does not set yet is added.
- **Move and size** in the preview: drag the selected element to move it (its `Position`, in its parent's units), drag
  the square handle at its corner to size it (its `Scale`).
- **Add**: from the palette, an image, a label, a container (it asks for the widget file it places, from a list of the
  game's and the project's widget files) or a video, as a copy of one the game ships (a label keeps its text style
  that way); a component or an effect onto the selected element. **Drag** an entry from the palette into the preview
  to place it where it is dropped.
- **References**: right-click a reference field in Properties (`Image`, `TextStyle`, `Template`, `LocalizedFont`, a
  palette, an effect, a component or a widget type) > Choose: the files of that kind by name, with a filter, instead
  of typing a GUID (the first match is selected: Enter takes it). **Duplicate** (Ctrl+D), **Remove** (Del: the bindings that name the element go with it). Edit > **New custom
  property**.
- **New widget file** (File, Ctrl+N): an empty widget file whose GUID follows from the project's id and the name given;
  place it with a container's `Template`.

New objects are written in the layout version the file already uses for their type, else the newest the game's files
use.

## The sequence editor

The Sequence dock shows the animation picked in States and events: one row per track (named after the binding end it
drives), its keys as diamonds, the play head at the timeline's time.

- **Pick** a key: its time, value and interpolator show below; change them and Apply.
- **Drag** a key along its row to move it in time; the keys stay in time order.
- **Double-click** a track's row where it has no key to add one there, with the value the track has at that time.
- **Del** removes the picked key.
- **Interpolators**: Constant (hold), Linear and the Widget Library's ease types (`Sine_In` to `Bounce_InOut`); the
  preview eases between keys with them.
- **Tweens**: the table below lists the tween components of the file's elements (Animator, AlphaTween, ScaleTween,
  PositionTween, RotationTween) with their start and end values, duration, delay, ease, loops and yoyo; write a value
  to change the instance's own value. "The file's tweens, started together" in the timeline plays them from 0.

Every change is an edit on the undo stack and is written with the file.

## Saving and the mod

- **File > Save into the project** (Ctrl+S) keeps the open file whole under `<project>/ui-edits/<game>/<game path>`. A
  file without edits is written as its original bytes; an edited one through the codec, which writes every shipped 117
  file byte for byte (and 93 % of 1800's: the status line says when the open file is one of the others).
- **File > Revert the file to the game's** deletes the project's copy.
- **File > Check the project's references**: every file the project's UI files name must be in the project or the game
  (the game fails a whole scene over one missing file).
- **The mod**: the suite's Project window builds every app's share into `build/<mod id>/`; the UI files go in as loose
  files at their game paths, and the loader lint repeats the reference check. Install as for any project.

## Comparing with the game

What the preview infers (a container's size when its content reaches into negative coordinates, which siblings a mask
covers, the designer helpers that `IsHidden` marks, a label's vertical alignment) is settled best by the game's own
numbers. The helper library's **UI dump** (built, **never run in a game yet**) writes them: on a key, every open
scene's live widgets as the game laid them out, one JSON file per scene. **File > Compare with a game dump...** holds
such a file against the preview.

With the live link connected (`live_link=1`, [`live-link.md`](live-link.md) section 10.2), **File > Take the live
values from the running game** asks the game for the dump itself: of the open file's scene, or of the scenes that place
the open widget file. It holds the file it wrote in the same dock. After a change to a scene or widget file, the link's
scene reload (section 10.1) shows it in the game without a restart.

While a dump is held, **View > Show the held game dump's texts in place of sample values** (on by default) changes the
labels that the dump holds a text for. Each such label shows the game's own text in the preview, instead of what the
bindings and sample values give (U8). This is the text the game currently shows, not the data behind it, so the sample
values stay as they are.

Note: the live values and the dump's texts are built but were not tried in a game.

**Getting the dumps** (Anno 117):

1. Switch it on: `ui_dump=1` in the helper library's settings (`<Documents>/Anno 117 - Pax Romana/log/helper-library.ini`;
   for this project's played mod in `data/helper-settings.ini`, then `install`). It is off by default and also works in a
   game started without the Anno 2070 mod. `ui_dump_scenes=romanizationchoicescene,maintitlescene` dumps those scenes
   by themselves once they have been open `ui_dump_delay_ms` (1500 ms, their fade-in done).
2. With the game closed, put the helper library in place next to `Anno117.exe` (the `install` command does it).
3. Start the game. At the title (`maintitlescene`) press **Ctrl+Shift+U** (`ui_dump_key` changes the letter); start a
   new game up to the culture choice (`romanizationchoicescene`) and press it again. Each press writes
   `<Documents>/Anno 117 - Pax Romana/log/ui-dump/<date>-<time>-<scene>.json` for every scene that is open, and a line
   per scene into `log/helper-library.log`.
4. Send the files (and the log) to whoever calibrates the preview.

**What a dump holds** (`"probe": "ui_dump"`, format 1): the scene and its UI state, the window and screen size, the
output and reference size of the scene (`ui_scale` is their ratio), and `widgets`, in drawing order (a parent before its
children). Per widget:

| Key | What |
|---|---|
| `id`, `parent`, `depth`, `owner` | its place in the tree; `owner` is the widget that places the widget file it belongs to |
| `name`, `name_crc`, `guid` | the instance's name, the CRC32 of the name (the files' `NameCrc`), the instance GUID as the files store it |
| `in`, `template` | the widget file it is an instance of (the scene for the scene's own), the widget file it places (a container, the scene) |
| `type`, `class`, `container` | the file instance's type hash, the built-in class (`LabelWidget`, `ImageWidget` ...), whether it holds children |
| `screen`, `pivot_screen` | its bounding box and its pivot in the window's pixels, after the layout |
| `world`, `local` | its world and local matrix, 4 x 4, row vectors (a point is `p M`, the translation in the last row) |
| `layout` | the values in effect: position, pivot, scale, rotation, the cancel modes' compensation, the bounding box in the widget's own units, its size, its own alpha, ZIndex, its flags and mask mode |
| `visible`, `drawable`, `shown`, `alpha_product` | its own `Visible`; whether the game draws it (visible, not transparent, not scaled to zero); whether it shows (drawable, every parent shown, no hidden intersect mask over it); the alphas multiplied down the tree |
| `mask` | the `id` of the mask that covers it, `null` for none |
| `props` | every built-in property as the game reports it: `Position`, `Size`, `BoundingBoxSize`, `Visible`, `AlphaTint` ...; a label's `Text`, `DisplayedText`, `TextPlane`, `DisplayedOffset` (where its text box starts), `DisplayedSize` (the box), `BaselineY`, `AlignmentY` ... |
| `props_read` | whether `props` could be read for this widget (false after a reader fault: the dump then skips the built-in properties; the file's footer counts them in `props_faults` and says whether the readers ran in `getters`) |
| `truncated` | the widget's children were cut short (the widget cap or an unreadable child list) |
| `custom` | a container's custom property values as its bindings left them: value index, `uid` (the UniqueId, where the game knows it), type, value |
| `unnamed`, `unknown_types` | the stored fields whose names the format does not know yet (by name CRC, with the type they belong to, their code, offset and value) on the instance, its widget file and its custom properties, and the unnamed types met; `ui_dump_unnamed=0` leaves them out |

Not in a dump: a label's line breaks (the game's text engine keeps them to itself; `DisplayedSize` and `BaselineY` are
what it gives back).

**The comparison** finds the widget that places the open file (its `template`; for a scene, the scene's root), puts
the game's rectangles of that file's widgets into the file's reference space and draws them over the preview (green:
agrees, red: differs, blue: a widget the file does not have, such as a list's items). The **Game dump** dock lists every
widget whose rectangle differs by more than the tolerance (2 pixels; change it there) or whose visibility differs, and
those only one side has; a click selects the widget. The comparison follows the preview: an edit compares again.
**File > Save the held dump's label table (CSV)...** writes one row per label of the dump with what the preview's text
placement is calibrated against: the stored `AlignmentY` and `AlignmentX`, the text plane, where the shown text starts
(`DisplayedOffset`), its box (`DisplayedSize`), the baseline (`BaselineY`) and the widget's own box (what a label's
vertical alignment and its fifth sizing mode are read from, once a dump exists).

### What a dump must show for labels

Two label facts are not in any file the repository can read: what 117's stored `AlignmentY` (the float 1.0 of 1,085 of the
1,100 shipped labels, 1.1 to 1.5 in 15) puts where, and what sizing mode 4 (937 labels' `HorizontalSizing`) gives the
box (`ui/117/layout.md`, "Alignment" and "Sizing"). A dump of a scene with many labels settles both
(`scripts/label_fit.py <dump.json>`, `uiedit/labelfit`); for each stored value it counts the labels against each reading:

- **Vertical:** the text's height `DisplayedSize.y` and its offset `DisplayedOffset.y` in a box `bbox` taller than the
  text by more than a pixel: top (offset 0), centre (half the free height), bottom (all of it), baseline (offset plus
  `BaselineY` the box's height). A row where the box is one line high tells nothing. If the 1.0 labels agree with one
  reading in the tall boxes and the 15 others with the same one at their own values, the number is a fraction or a
  scale of that reading; if none agrees, the offset is not relative to the box and the CSV (File > Save the held dump's
  label table) has what is needed to fit another.
- **Sizing:** the box's width (`bbox` width) against the plane times the scale (`TextPlane.x` times `Scale.x`), the shown
  text (`DisplayedSize.x`), the larger and the smaller of them, for each stored `HorizontalSizing`; the same for
  `VerticalSizing` and the heights. Mode 4 is the interesting one: if its labels agree with one of the four readings the
  mode is a duplicate of it for those labels; if none, the report lists the labels and the CSV's numbers say what the
  box is.
- **What the dump must hold for this** (all in `props` of each label, which the helper library writes): `AlignmentY`,
  `HorizontalSizing`, `VerticalSizing`, `TextPlane`, `Scale`, `DisplayedOffset`, `DisplayedSize`, `BaselineY`, and the
  widget's `layout.bbox_size`. Pick scenes with labels in several overflow modes (the construction menu, the
  statistics, a tooltip's text) besides `maintitlescene` and `romanizationchoicescene`; one label with an `AlignmentY`
  of 1.4 or 1.5 is worth a screenshot beside its dump (the preview's label draws the text at the top). Not
  settled by a dump: the units of `DisplayedOffset` and `DisplayedSize` (the report assumes the box's own units; the
  ratio `DisplayedSize.x` to the text's width in pixels at the dump's scale shows it).

## Anno 1800

1800's files are read with its own layout versions (the format note, "Layout versions"). All 6,870 of its files open,
the earliest editors' among them ("Read from the bytes" there). 1800 keeps a widget file's custom properties in
another field than 117 (`CustomProperties`). They are read and written there, so its 515 bindings of an own property
run in the preview. 1800 ships no Lua type files, so the view models are empty there. 1800's scenes are drawn in 1800's
own font files. Its text styles' font chain does not always decode; the first font file of the game stands in then.
1800 ships no Widget Library (`data/ui/pwl/` is empty), so its palette lists only the new widgets, and its components
are named from the files.

Seen offscreen: 1800's scenes open and are drawn (its achievement scene with its designers' placeholder texts). Nothing
of it was edited or loaded in the game.

## The History Editions (1404 and 1701), 2026-11-13

Anno 1404 and Anno 1701 as re-released (the History Editions) ship a Phoenix interface of their own beside the old
one. `data/ui/studio/gamedata/` holds 732 files named by GUID (294 widget files, 271 images, 41 text styles, 30 widget
types, 28 enums, 18 scenes, components, effects, fonts, a palette ...). The scenes are the files with a `.scene`
suffix: `mainmenu`, `credits`, `settings`, `infotip` and so on. The files are delivered in `ahe/a4he0.rda` (1404) and
`ahe/a3he0.rda` (1701). `data/ui/studio/` holds the editor's own JSON documents of the same objects (807 files:
scenes, widgets, images, enums, game objects), with every class and field by name. The two games' files are identical.

File > Open game lists both games. The Files list shows the scenes by name (the `.scene` suffix dropped). The Widget
Library comes from `data/ui/pwl/data/` and `data/ui/studio/` (22 components, 32 widget types, 31 enums). Pictures are
the plain `.png` files at the image asset's name. A project keeps changed files under `ui-edits/anno1404/` (or
`anno1701`) at their game path, as for 117.

**What opens: all 732 files, the 18 scenes among them, each re-encoding byte for byte.** The files
are the same container (`PHX`, serializer `0x108`, flags `0x18f0`, editor version 1.16) in an older layout of nearly
every type than 117's or 1800's: 43 type records carry layout GUIDs of their own, two types (the text style and the font
asset) are not in 117's schema, and a container instance carries a list of typed values (`DynamicProperties`). The codec
reads them from the History Editions' own layouts, each one proven by re-encoding every file that uses it
(`formats/phoenix-scene-format.md`, "The History Editions' files"). The preview places
a widget by the older anchor constraints (left, right or centre per axis, read as 117's anchor point and resizing modes)
and a label's alignment stored as 0 to 3.

The binary files carry no instance names (only their CRC32). The element list, the bindings and the paths take each
element's name from the editor's JSON document of the open file (`data/ui/studio/<folder>/<name>_<guid>.json`, every
object by its `ObjectID` and `Name`). This names 1,809 of the 1,847 instances of the scenes and widget files, and every
name matches the file's `NameCrc`. Five widget files (35 instances) ship no document. An element that the document does
not name, or one that was renamed afterwards (its `NameCrc` is not the name's), shows "(unnamed)".

The bindings (7,093) are read from these games' own lists. The preview evaluates them, the graph names their ends, and
a new custom property or binding is written where these files keep them. The values that a container gives the file it
places in `DynamicProperties` are passed in (`layout.Context.values`). Each id is the `AtomPropertyId` of a custom
property of the placed file, or of its widget type and that type's parents. Counted on 1404's install: 1,916 of the
7,103 values are the placed file's own, 548 its widget type's, 2,721 the parent types', and 2 are not found. The value
goes to that property's UniqueId. A value that the instance's own `PropertyValues` give wins. Nothing of it was loaded
by a game.

**A widget's properties are its file's and its widget type's** (all games). A widget file's `WidgetType` and that
type's `ParentWidgetType` chain declare custom properties that every widget of the type has. The bindings read and
write them as the widget's own, by UniqueId (117: 902 binding ends, the History Editions: 1,137). The preview gives the
widget those properties with their declared defaults (`scope.widget_types`; the file's own declaration of a UniqueId
wins). Such a binding therefore reads the type's default, or the value passed in, instead of nothing.

Note: this section is as of 2026-11-13 and 2026-11-14.

## The old interface

For 2070, 1404 and 1701 **File > Open the old interface of** opens a read-only page: the dialogs, templates and
backgrounds of the game, a dialog drawn with its pictures and texts, and an inspector for the widget picked. What it
draws and leaves out, and why the compiled pools cannot be written, are in `ui/old-gui.md`.

## Speed

A file is opened off the window's thread (decoded and checked for a byte-for-byte re-encoding: "opening ..."; the
one asked for last is shown), and a scene is placed off it: opening one shows "laying out the preview ..." in the
status bar while the rest of the window answers. An edit, an undo or redo, a sample value or a revert made meanwhile waits and is made, in
the order it was asked, when the layout is done; a file opened after a waiting edit opens after it, so the edit is made
in the file it was made in, and an edit asked for while the open waits is made in the file shown then; an edit reads
what it changes when it is made, so two moves that waited add up; a view switch (the design view, the hidden layers,
the timeline) is taken by the next layout. What the layout computed is kept between edits, with the files and sample values it read, so an edit places
the edited file again and takes the widget files it places from what was kept: 0.1 to 0.2 s for the largest scenes
(the construction menu, 84 s before). The elements of a list that hold no given value share their work, so the sample
panel lists the first element's values; a value set under an element's key separates that element. A sample value set by hand places again only what read it; a changed view switch
starts over. The numbers and what is left are in `tool-suite.md` section 20, "Speed".

## What a person should try

1. Open Anno 117, open `maintitlescene`, `loadingscreenscene`, `romanizationchoicescene` and a widget file of the
   construction menu; compare the preview with the game's screens (the design view on and off, View > Fill unbound
   data with placeholders on and off); look at Checks for what did not resolve.
2. Select a label, change its `Text`, Save into the project, Build mod in the Project window, start the game with the
   mod: the text must show (and `mods/mod-loader.log` stay clean).
3. Move an element with the mouse, Ctrl+Z, Ctrl+Y; add an image from the palette and point its `Image` at another
   image asset's GUID.
4. In Bindings, change an operator of a binding that feeds a visible value and watch the preview; New binding from a
   constant to an element's `AlphaTint`.
5. Name an id in View models and see it in the graph; write a sample value and see the preview follow.
6. Play an animation on the timeline (the title's `Anim_In`); in Sequence move a key, add one by a double-click,
   change its interpolator, Ctrl+Z; play "the file's tweens" of a file with an Animator and change a tween's end
   value.
7. Open Anno 1800 and a scene of it.
8. Drag an image from the palette into the preview; right-click its `Image` field > Choose and pick a picture; set a
   label's `TextStyle` the same way.
9. Take a UI dump at the title and at the culture choice (above, "Comparing with the game"), open the two scenes,
   File > Compare with a game dump... each, and look at what the Game dump dock lists.
10. View > Show the 3D scene behind the UI, then View > Draw the scene behind the UI from a model of the game... and
    pick a residence; open `statisticsscene` or `blurscene` and compare the blurred glass with the game's over a city.
11. View > Draw the scene behind the UI from an island of the game... with a pool island; compare the blur and the
    darken over it with the game's over the same island.
12. Open `statisticsscene` (its files nest 24 deep) and a scene with a list fed by its file's preview values; compare
    the list's size and the deepest parts with the game's screen.

## Language

The UI editor's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
