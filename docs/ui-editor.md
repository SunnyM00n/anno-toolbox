# The UI editor: the interface files of every game

The suite's **UI** app has one page for each kind of interface file, with a switcher on top (**Interface files of:**).
The first page, described here, opens the Phoenix files that Anno 117 and Anno 1800 build their interface from (scenes
and widget files in `data/ui/studio/generated/`).
It previews them with sample values in place of the game's.
It shows their bindings as a node graph, and it edits them with undo.
It keeps the changed and new files in the open project, whose build puts them into the mod.
The design and its status are in `tool-suite.md` section 20.
The file format is described in `formats/phoenix-scene-format.md`.
The UI's own rules (layout, widget types, components, masks) are in the UI reference.
Anno 2205's Flash interface is the app's other page: [The Anno 2205 page](#the-anno-2205-page).

**Nothing the UI editor writes has been loaded by a game yet.**
The codec's output is known to load (changed files and new widget files).
The editor's edits on top of it are currently unverified.

Note: the codec's output was loaded in the game on 2026-09-21; the editor's status is as of 2026-11-09.

## Starting it

- `anno-toolbox --app ui` starts the suite's window on it.
  In the suite it is **UI** in the app switcher (Ctrl+4).
  The switcher on top of the app changes the page, and the app opens on the page it had last.
- File > Open game takes the game's folder from the suite's settings (the island editor's File > Settings).
  The first opening indexes the game's UI files (117: about 6 seconds).
  The index is kept in the workspace's `game-index/<game>/ui/`, the one index folder that every app of the suite uses
  per game.
  The editor then reads in the background which instance names each widget file, so the file list can show widget
  files by those names.
- File > Open a folder of loose files reads an unpacked game or a mod folder instead.

## The Anno 2205 page

The second page, **Anno 2205**, is the Flash UI editor.
It lists Anno 2205's interface movies (`data/ui/*.feu`) and opens them as a tree of their elements with a preview.
It edits texts, pictures, icons, fonts and positions, saves the edit file, and builds or installs a mod.
It opens the 2205 folder of the suite's settings and keeps its edit file in the open project
(`ui-edits/anno2205.json`); the project's build adds the files it changes.
Its window, commands (`python -m annotoolbox.flashui`), edit file and build are in
[`ui/2205/flash-ui-editor.md`](ui/2205/flash-ui-editor.md).
The format is in [`formats/anno2205-ui-format.md`](formats/anno2205-ui-format.md).
The two pages share nothing but the project and the app's place in the switcher, and each editor is as described in its
own guide.

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

## The preview

The preview composes a file as the game assembles it (`ui/117/layout.md`).
It handles these parts:

- the reference space;
- anchors (nine points of the parent's computed size);
- pivots (also as a fraction of the size through the `ProportionalPivotPoint` component);
- scale as a multiplier of the natural size (a picture's size, a label's text, a container's content; a size a binding
  writes is the size itself, and a size a binding reads is the box times the scale);
- the modes that cancel the ancestors' scale and the aspect ratio;
- the containers' sizes (their fixed size, `SizeOverride`, else the union of the children that count for it, hidden
  ones too, from its smallest corner; a group is as large as what it holds, by the same rule);
- stacks (`StackLayout` with its spacing, margin and inverted order, the children's `PaddingUtility`);
- nine-slice pictures;
- colour tints with palette entries;
- alpha down the tree;
- the blend modes normal, add and multiply;
- masks (below);
- labels in the game's fonts;
- the widget files a container places, as deep as they go.

**Composition**: a placed widget file is evaluated with what its instance passes in (its `PropertyValues` and what the
placing file's bindings write into it).
The placing file reads back what the placed file computes.
A binding such as `Popup_Base.Content_PositionCenter-.x` gets the value the popup's own bindings produced.
A value a file only declares (its default) counts as unknown, so the reader keeps its own stored value.
So an animation's start state does not hide a whole scene.

**Bindings run in the preview**: each template's bindings are evaluated with its operators.
A label shows the text a custom property gives it, a size follows a hub property, and a value flows from a scene down
into its widgets.
Where the game would fill a value (a scene's data object, a list's elements), the **sample values** stand in.
Where none is set, a **placeholder** is made up (below).

**Lists**: a widget file whose widget type is a list or grid places its one item child once per element of its data
source.
The list and grid types are `List`, `Grid` and the game's scrollable grid, the dynamic widget.
The elements are the files' own preview elements, the elements the sample values hold, else three placeholder
elements.
A list lays the copies along its axis with its spacing and visible count.
A grid lays them in rows of its visible column count, capped at its visible cells.
Each copy is bound to its element (`DataMode` 4) and is its own item (`DataMode` 3).

**Masks and effects** (the game's UI shader, `rendering/ui-117.md`):

- A mask covers the siblings that receive it and are drawn after it, with their whole subtrees.
  A run of masks is moved in front of the siblings it covers, and `ZIndex` sorts from the first child that has one
  (`ui/117/layout.md` "Draw order", `layout.draw_order`).
  A mask must be a picture or a shape, and it takes no input in the preview's picking.
- A hard mask clips to its rectangle.
- A **soft mask** multiplies the covered pixels by a smooth step of its picture's alpha (from its `AlphaThreshold` over
  its `AlphaGradient`).
  Outside its rectangle nothing shows unless `ExclusiveMask`.
- A picture's **effects** are applied in the shader's order:
  whiten; glow (inner and outer, from the 5 x 5 texels round each texel, three blend modes); drop shadow (the alpha
  moved by `Strength` texels in `Direction`); alpha test; colorize (contrast, brightness, grey scale, a multiply by a
  mix of two colours, alpha gamma).
  The described brightness, saturation and colour templates are mapped onto colorize.
- The picture effects of a blur (nine taps), a distance field (alpha as a distance: soft edge, inner colour, outer
  glow), a blink and a texture scroll follow the shader's formulas too.

**The 3D scene behind the UI** (View > Show the 3D scene behind the UI; off by default, the choice is kept): a still of
a 3D scene is laid behind the preview, and the effects that read the world draw from it.

- A widget with the **background blur** shows the scene averaged over 5 x 5 pixels, opaque wherever its picture has
  alpha.
- The **world darken** shows the scene multiplied by the effect's colour in linear light by its intensity.
  Alone, it shows the unblurred scene, darkened; with the blur, the scene is blurred and darkened.
- The **island shading** turns an island picture's blue channel (the distance to the coast) into hatching, a border and
  a contrast in the effect's colours.

The scene is one of four things:

- a made-up **stand-in** (sky, sea, an island with roofs);
- **a picture you pick** (View > Pick a picture for the scene behind the UI: a game screenshot, the viewer's "Save the
  picture", the island editor's view);
- **a model of the game the renderer draws** (View > Draw the scene behind the UI from a model of the game...).
  This is a building's or a ship's config, drawn offscreen by the game's own shaders as the viewer draws it, with no
  file in between.
  It needs a Direct3D or OpenGL device;
- **an island of the game the island editor's renderer draws** (View > Draw the scene behind the UI from an island of
  the game...).
  The island file is opened read only as the island editor opens it, and drawn by its view in the game's look.
  The first picture of an island takes minutes.

View > Use the stand-in scene goes back.
Save the preview as a picture includes the scene.
With the switch off, a widget with such an effect draws as its picture does.
The data, the shader's rule and what differs from the game are in `ui/117/world-effects.md`.
The scene is already tone-mapped, and the hatch pictures are a stand-in.

**Text** is drawn in the game's own fonts.
A text style's localized font is followed to its font file (`data/fonts/...`, the language's exception where one is
set) and loaded.
Where the chain cannot be read, the first font of the game stands in.
A label wraps at its text plane's width (a word wider than the plane inside the word).
It cuts by character or with an ellipsis as its overflow modes say, and keeps the whole lines that fit vertically (with
an ellipsis in that mode).
Its first line is as high as the font, and every further line adds the style's leading times its size.
A 117 label's line spacing and the letter spacings change nothing, as in the game.
An older game's label keeps its line spacing as a factor, but nothing was measured there.
Outlines are drawn under the glyphs and shadows below them.
Its size follows its sizing mode (the plane, the shown text, the larger or the smaller).
A box of the text's size starts where the text is aligned to.
All of it is the game's own behaviour (`ui/117/layout.md`, "Fonts, as the game measures them").

**Fonts** (the same on every page of the app and in the Texts app).
Every font is read from the install at run time and registered with Qt for the session (`suite/gamefonts.py`).
This happens once per file content, and a font is never copied into a project, the repository or the tests.
Where one cannot be read, the page draws a system font and says why.
The 117 and 1800 pages say it in the Checks list, and the 2205 and old-interface pages in a tooltip on the preview.

| Game | The fonts | How the UI names one | Draws in |
|---|---|---|---|
| 117 | `data/fonts/`: LisboaSans, AlbertusNova, a hieroglyph font, Roboto, DF Chinese and Japanese files, Noto KR and TC, Meta, Salford, Heuristica and Kelvin in four weights each | a label's `TextStyle` -> `LocalizedFont` -> the font asset's `FontId` file, a language's exception and fallbacks | the game's file; all 1,028 labels of the shipped widget files with a style resolve to Lisboa Sans Pro, Albertus Nova or the hieroglyph font |
| 1800 | `data/fonts/`: Meta Offc Pro, Meta Serif Offc Pro, Roboto, Heuristica, Kelvin, the Asian files | the same chain (four of its localized fonts have a script of their own, read by pattern) | the game's file; all 4,717 labels resolve to Meta Offc Pro or Meta Serif Offc Pro |
| 2205 | `data/ui/*.ffd` glyph pictures, plus Roboto Light and Regular as TrueType in `data/ui/chromium/` | a movie's font reference names the `.ffd` and a family; no movie holds `DefineFont` | Roboto Light and Regular in the shipped TrueType files (618 of the 733 text fields); Thin and Medium in the nearest weight; Arial and the Chinese font in system fonts ("Font descriptors" in [`formats/anno2205-ui-format.md`](formats/anno2205-ui-format.md)) |
| 2070 | `data/loca/<language>/fonts/`: DejaVu Sans Condensed and MagistralTT Bold | the font table of `properties.xml`, a widget's `FONT` | the game's file (396 of the 406 widgets that name a font); the console font is a Windows family |
| 1404 | `data/loca/eng/fonts/georgia.ttf` | the `<Font>` entries of `localisation.xml`: a name, a family (Times New Roman, Arial, Courier New), a size and a weight | the table's Windows family from the system; the shipped Georgia is the `GameFont` entry, and which texts it draws is not known |
| 1701 | `georgia.ttf` the same; `data/ui/studio/fonts/` holds more files (Meta, Chinese and Japanese), none of them named by its font table | the `<Font>` entries of `localisation.xml` (per resolution: `Name_1024`, `Height_1024`; the file is not well-formed XML) | the table's Windows family (Times, Arial, Courier New) from the system |

117's text styles carry `Bold` and `Italic` flags.
Of the 35 shipped styles, none is bold and two are italic.
Both italic styles are on Lisboa Sans, which has no italic file.
So the weights are Qt's: italic is slanted by Qt, and bold would be emboldened.
Heuristica and Kelvin ship bold and italic files that no text style names.
The 2070 table's weights (500, 600, 700) are applied as Qt weights.
The Texts app shows a text's source and target boxes in the game's font of the picked text style.
That style is the one of the labels that show the text, else the most used one.
For the games without text styles it is their default font: Roboto Regular, DejaVu Sans Condensed, Georgia.

Note: the fonts were added on 2026-11-17.

**Group rules and loaders** (inferred from the editor's descriptions, docs/ui/117/layout.md):

- A stack puts its children one after another and aligns each across its axis.
  The group's `ChildrenAlignment` gives begin, middle or end, and a child's `AlignmentUtility` overrides it.
- A background layout stretches the group's first layer round the others that count (hidden ones too) plus its
  margin.
- A stack gives a hidden child that counts its room.
- A group's box holds its background, and a pivot fraction is a point of that box from its smallest corner.
- An info tip widget stands at its `ReferenceWidget`'s rectangle, as large as it, and counts for no size.
- A list of tabs opens on its first entry, and in the design view what that starts runs: the construction menu's
  first category shows its building row.
- A video is as large as its video asset's `Resolution` times its scale.
- An enum loader places the children whose `EnumEntry` is its current value (and those with none).
- A game object loader places the child made for its object's data class where the game's data says the class (the
  options scene's page: `ui/117/game-data.md`, "Object loaders"), else its first.
- A radial list or wheel puts its items on an arc.

Note: the group background, info tip and first-entry rules were added on 2026-11-24, checked against the newest dump of
152 scenes (`ui/117/layout.md`, "Round 2").

A layer the author hid in the editor (`IsHidden`) starts hidden unless a binding shows it.
View > Show layers hidden in the editor draws it.
A chain of bindings runs until no value changes, so a value several links away from its source arrives.
A picture a binding gives a size is scaled to it with its pivot.

**The design view** (on by default, States and events): elements an animation fades in (stored alpha below 0.01) are
shown.
A scale or size a binding sets to zero is left as stored.
A wrap width below two font sizes (the game binds the real one at run time) does not wrap.
Switch it off to see the file as it starts in the game.

**The timeline**: pick one of the open file's animations, or "the file's tweens, started together", and scrub or play
it.
Its tracks write their values as the game's animator would, each key easing to the next by its interpolator.

**Pictures**: an image asset's `ImageId` names a `.png`.
The game stores it as `<name>_0.dds` beside it, and the `data/ui/fhd/` pictures under `data/ui/4k/`.
A picture the game does not have is drawn as a flat grey rectangle and listed in Checks.
A picture the game names at run time with no sample value shows a real picture of the game (`Pictures.sample`).
That is, in this order:

- the portrait or emblem of the character its slot names (below, "What the preview assumes about the player");
- else an icon whose file name shares the longest word with the slot, taken from the folders the game draws for a
  widget of that name (an `Icon` from the generic and attribute icons, a `2DHolder` from the buildings and goods, a
  `Portrait` from the portraits);
- else one picked by the slot's name, always the same.

A slot the binding does not name takes the name of the image it fills.
Only a game with no icon files gets the light placeholder tile with the slot's name, and Checks lists the slot ("no
icon in the game's files").

Note: the icon folders were seen in the game on 2026-11-16.

**Wrong pictures** were fixed on 2026-11-17 after a report of "the wrong icons":

- A slot named only `picture` drew any icon of the game, the hippodrome's phase icons in the HUD among them.
  It now uses the image's own name and the game's folders for it.
- The governors' bar drew the Egyptian portrait frame, because its `PlayerRegionInfo` stores Egyptian.
  It now uses the player's region, Latium's mosaic as in the game.
- Every lock icon showed, since a lock flag was made up as `true`.
  Now all are unlocked by default.

The fhd and 4k names resolve as the game's.
117 ships one picture set (`data/ui/4k/`, `data/ui/2kimages/`, `data/ui/4kimages/`; no `data/ui/fhd/` file), and every
`fhd` name is found under `4k`.
Atlas entries were not checked.

What differs from the game (open, `tool-suite.md` section 20):

- **Text**: a label's box and its text's place in the plane follow the game's own behaviour (sizing 0 the plane, 1 the
  shown text, 2 the larger, 3 the smaller; `AlignmentY` top, centre, baseline, bottom).
  In the design view a plane of 0 (set at run time) takes the shown text's size.
  The box always starts at the label's origin.
  Justify is drawn left.
  The font data files' own outlines are not read (the font files are the same faces).
- **Effects**: the screen offset is not drawn.
  The background blur, world darken and island shading are drawn when View > Show the 3D scene behind the UI is on,
  over a stand-in scene or a picture (below; the rule and what differs: `ui/117/world-effects.md`).
  A texture scroll and a blink run over time (View > Play the effects over time, on by default; off freezes them at
  time zero; the clock runs only while the open file has such an effect).
  How the drop shadow's, the soft mask's and a container's effects reach the shader is inferred (a container's drawn
  effects apply to each picture below it).
- **Composition**: a stack's cross-axis alignment, a background layout, loaders (one child), radial lists (zero degrees
  up, clockwise) and the layers hidden in the editor are inferred rules (above).
  A container whose children reach into negative coordinates reports the largest right and bottom edge, which can be
  half the width the game gives it (the romanization centre piece).
  A mask that comes after the widgets it should cut covers nothing.
- **Animations**: the numbering of a key's interpolator is inferred (0 holds, 1 linear, then the Widget Library's ease
  types from `Sine_In` on).
  Events on a track are not fired.

## Bindings

A binding reads one or more **sources**, runs them through an **operator** and writes its **targets**.
The graph draws each binding as a row: the sources on the left, the operator in the middle (coloured by family; a `*`
marks one whose behaviour is known from its stored fields only), the targets on the right.
An end is written as the element it starts at, then its path: `Caption.Text`, `self.GameContext.<2b2357a9>`,
`own.Title`, `constant 0.5`.
`.x` / `.y` / `.z` is one axis of a vector.
By default the graph shows the bindings of the selected element; View > Show all bindings shows the file's.

- **Right-click an operator**: change the operator, or remove the binding (an end that no other binding uses goes with
  it).
  The families are Assign, Numerical, Maths, Casts and text, Comparison, Logical, Events, Enum and Arrays, with 63
  operators and what each computes.
- **Edit > New binding** (Ctrl+B): a source (one of the file's own custom properties, a built-in property or event of an
  element, or a constant), an operator, a target.
- The ends of a template are one index space in the file: the property paths, then the custom properties' containers,
  then the constants.
  The editor keeps it whole when ends are added and removed.

## View models

- **This scene's data**: the scene's data class as the game's script type files describe it
  (`data/script/types/generated/rdui/gameobjects/*.lua`), guessed from the scene's name (`maintitlescene` ->
  `CMainTitleSceneData`).
  Its fields, child objects and arrays open as a tree.
- **Field ids this file reads**: the paths through game objects that the file's bindings read.
  The ids are the game's; the type files give the names but not the ids.
  Right-click a class's field > **Name an id** gives an id a field's name.
  The names are kept in the project (`ui-edits/<game>-names.json`), and every path then shows them.
  Right-click a read path > **Bind it to the selected element's property** makes a binding from it.
- **UI data classes**: every class of the type files, found by the filter.
- **Script readouts**: **Load a script list** reads a `script-api.json` (a script library's functions).
  A readout can be added to the file as a custom property of its type, which the library can then fill.

## Sample values and placeholders

Every value the preview read where the game would give one is listed with its path.
Write a value, and the preview shows it at once.
The files' own preview data for lists (`DataSource` values) seed it.
The values are kept in the project (`ui-edits/<game>-samples.json`; the keys start with the file they belong to).

**Placeholders** (View > Fill unbound data with placeholders, on by default): where a binding reads a game value that
has no sample value, a value of the kind the binding needs is made up.
It is kept with the samples and marked as generated (grey and italic in the panel).
The kinds are:

- a text whose length suits the target (a header short, a description long);
- a plausible number (a percentage 75, a count 12, money 1500);
- `true` for whatever decides a visibility or feeds a logical operator;
- a real icon of the game for a picture named at run time (a light tile named after its slot where the game has no icon
  files);
- three elements for a list.

Each binding gets the placeholder of the kind it needs, also when another binding reads the same game value as another
kind.
For example, a label's text and a picture's visibility from one value get a text for the one and `true` for the other.
So what the preview shows does not depend on which part of the scene was placed first.
The panel lists the value the first one made.
Write a value to keep your own; right-click > Reset to a placeholder drops it again.
With the fill off, the scene shows only the values set by hand.

**The typical state**: the placeholders make one coherent state of the scene, by evaluating the bindings with them,
never by the widgets' names.
A flag takes the state its name says (`state_for`).
A lock is off (unless the preview's settings lock it), and an error, a warning, an invalid or empty value and an edit
mode are off.
Their opposites are on.
Where the flag's own name says nothing, the name of what it feeds says it, through the operator (a flag inverted into
`IsUnlocked` is false).
After that come the other bindings of the file that read the same game value (one value, one state).
Last comes the flag the target stores (a layer the file keeps hidden, such as a drag hint, stays hidden).

The same coherent state follows these rules:

- An enum loader given a value none of its children has shows none of them, as the game does (a lock loader set to
  "unlocked").
  It shows the first entry only while the value is unknown.
- A component's property that the widget does not store reads the default its component file declares (a UI scale of 1,
  no gamepad).
- What a placing file writes into a field of a placed file's object (a text file's text plane, alignment and sizing)
  reaches that file.
- A label's alignment, sizing and overflow follow what its bindings write.
- One entry of a group is selected, the first.
  This is a list's first element, or a menu's first access button (the options scene's first tab, the construction
  menu's buildings).
- A two-way binding between a widget's state and the game's data starts from the data.
- A game object is one object in every file that holds it.
  A field any of them writes reaches the others, down, up and between sibling files (an options tab's text width, an
  object menu's background height).
- A label's shown text and its size are read as laid out.
- A list's `ValueCompare` gives every item the smallest and largest value of all items (the widest tab).
- An enum the game fills reads its first entry (a popup closed).
- An intro animation has run.
  A panel that slides in stands at its end, and an animation the scene's show event or opening starts is run where it
  fires.
- A page an animation fades in counts as shown (`ui/117/layout.md` "Values the game gives at run
  time").

Values held from a running game win over all of it.

Note: the typical state and its rules were added on 2026-11-17.

## What the preview assumes about the player

Where a scene shows the player's game, the preview assumes Anno 117's campaign in its late game.
The player is Marcia, with everything unlocked and researched, mouse and keyboard, every DLC of the install, and single
player in Latium.
The values are `uiedit/previewdefaults.py` (`Defaults`); what each setting can take is read from the game's assets
(`uiedit/previewcatalogue.py`).
**Live values and recordings win over all of them**, and so does a sample value set by hand.

How a value reaches the preview:

- **Components the game fills from the player's state** get the player's value instead of the one stored in the file.
  `PlayerRegionInfo.CurrentRegion` gets the region (Latium: `halo.Province` Roman).
  `Platform.IsGamepadModeActive` and `Platform` get the input (mouse: false, PC).
  `PortraitResolver.PortraitImage` gets the portrait of its `PortraitGuid`, else the player's (the
  UI reference, its components page).
  This holds for a component on a widget file's own root as well: the hairlines and deco borders of 117's panels read
  their region there, so they are Roman in Latium and Egyptian in Aegyptus.
- **Placeholders by name**: a made-up value whose source or target names a value of the player's game takes it.
  This covers a character's name (`PlayerName`, `EmperorName`, `RivalName`), money, a stock, the emperor's reputation,
  and a gamepad, multiplayer or DLC flag.
  A run-time picture whose slot names a character gets its portrait or emblem.
  The slots are `PlayerPortrait`, `EmperorPortrait`, a bare `Portrait` or `Portrait_Encounter` (a rival's, as the game's
  governors' bar and diplomacy show other participants there), and `logo` (the player's emblem).
  `2dPortrait` is an icon, not a face.
- **Locks**: a lock flag is off while everything is unlocked.
  A lock flag under a game object shows when an asset GUID given under that object (a sample value) is in the locked
  list.
  Below the late game (campaign start, a tier), a lock of an asset the preview does not know shows.

| Setting | Default | Source |
|---|---|---|
| Player | Marcia (participant 41 `Profile_Human_00`): portrait 38962 `portrait_marcia.png`, emblem 125990 (Secretive Sphinx), colour 91304 Lapis Lazuli (38, 97, 156), no title | the Portrait and PlayerLogo assets; the colour's `ParticipantUIColor`; confirmed by a campaign save's slot 0 (name Marcia, colour, logo, portrait, start session 3245) |
| Banner | logo colour 91307 Celtic Green, shape 92736, pattern 92738, background 92740, stripes 92742 | the campaign save's slot 0 |
| Emperor | Emperor Lucius Augustus (31721), portrait 33070, emblem 77590, colour 91314 Falernian Red; reputation 0 (Casual), Peace | the campaign's start emperor, act 1 (`setup.SetupStartEmperor`, as a Marcia save has him); chosen 2026-11-17. Gnaeus Firmius Calidus (31637) reigns from act 2 on and in the endless game, Julia Augusta (31722) is the third; all three stay in the catalogue's emperor list |
| Rivals | Tarragon (31100, Latium), Athr Iorgwyn (31102, Albion), Concordia (31104, Latium): portraits, `CompanyIcon` emblems and `DefaultColor`s of their participant assets; Peace [I] | the campaign's quests name them (act 2's Athr side quest, act 3's Saturnalia with Tarragon and Concordia) |
| Pirates | Caeso Regulus Syracus (37495, Latium), Voada of the Scathach (37494, Albion), Kandis of Kashta (153069, the Nile region: portrait 153091, emblem 153101, colour 158239; Aegyptus DLC); War [I]. A scene in a region first shows that region's pirate | `Difficulty Preset Campaign` (41206) lists Caeso and Voada, Kandis is the Aegyptus DLC's pirate (2026-11-17); absent from the catalogue's pirate list when the DLC is not installed |
| Progression | late game, all unlocked; every tier reached with late-game residents [I] (Latium 1,500 / 1,200 / 800 / 400 ...) | chosen; the tiers are the `PopulationGroup7` levels |
| Research | all researched, a repeatable node once | chosen |
| Input | mouse and keyboard | chosen |
| DLCs | every DLC the install has | chosen; the list is the `DlcController`'s |
| Session | single player; the campaign's setup: `Difficulty Preset Campaign`, map type 43182, seed 211, start in Latium (3245), each `Setup...` option as the campaign save holds it (taxes Spare, ...) | chosen; the save's `header.a7s` |
| Region | Latium | the campaign's start province [I] |
| Season and weather | the game's default | [I] |
| Island | a large one of the player's | [I] |
| Object of an object menu | Patrician Residence (3145), Jeweller (3192) | [I] |
| Money and goods | 250,000 and 500 of each good | [I] |

**The store** (`previewdefaults.CStore`, one per open game, `window.preview_store`): nothing of a user's save is needed
or read.
The user's changes are kept per game in the Toolbox settings (`apps["ui"]["preview"][<game>]`), or in the project
(`ui-edits/<game>-preview.json`, `keep_in_project(True)`), which then wins.
Only what differs from the campaign is kept.

- **Read**: `previewdefaults.current(ctx)` gives the `Defaults` of a preview (`layout.Context.defaults`).
  On it are `player()`, `cast(role)` (player, emperor, rival, pirate), `option("setup.<Tag>" | "banner.<Part>" |
  "title.<...>")`, `region_name()`, `reached()` (tiers and residents), `is_locked(guid)`, `researched(node, tier)` and
  `portrait_of(guid)`.
  `placeholders.is_locked(ctx, key)` is the lock rule.
- **Write**: `Store.set(name, value, play=False)` with a field of `Defaults` (`input`, `region`, `progression`,
  `locked`, `tiers`, `characters`, `player_name` ...) or `options.<name>`.
  `play=True` marks a choice made inside a scene, which `reset(played=True)` undoes.
  `reset(name)` puts one value back, and `reset()` puts all of them back.
- **Change signal**: every change calls `Store.listeners` with the new `Defaults`.
  The window's listener gives them to the preview, drops the placeholders made under the old ones and lays the preview
  out again.
- **Choices**: `previewcatalogue.read(game)` reads an opened asset game, off the window's thread (the window keeps it as
  `preview_catalogue`).
  It gives the characters by role, the profile customization's portraits (20), emblems (45, the game shows 41 of them),
  colours (12), banner parts and title words, the provinces, the tiers by region and the DLCs.
  It also gives the assets locked at the start (`DefaultLockedState`: 47), the research nodes by category with their row
  (211, 14 repeatable) and every `Setup...` option with its values.

What the data does not say: which building a tier unlocks.
A `FeatureUnlock` names a population count, but nothing names the `FeatureUnlock`.
So a tier preset sets the tiers and leaves the locked list to the user.

## Game data in the preview

**View > Fill from the game's data** (on by default, the choice is kept): where a scene shows what the game fills at
run time, the preview takes it from the installed game instead of a placeholder.

- The quick tools wheel shows the eight tools and buildings pinned for the player's region, with their icons.
- The religion scene shows the patrons (Ceres, Neptune, Mars ...) with their icons, artwork, devotion effects and the
  "Exalted Patron" texts.
- The options scene shows one tab per options page and the entries of the open page.
- The construction menu shows the region's categories: infrastructure first (the crane), then one per tier with its
  residents' portrait and tier number, each with its buildings.
- The residence menu and the production menu show the name, icon and category of the residence or production building
  the Preview settings name.
  The other object menus name no object in the game's data, so they keep placeholders rather than another building's
  name.

A list shows as many entries as the data holds, up to what the list shows.

- **Which value wins**: live values and recordings, then a value set by hand (or the file's own preview data), then
  the game's data, then the baseline of the values the game showed (below, where the preview would otherwise make a
  value up), then a placeholder.
- **Where a value came from**: Properties lists, on top, what the selected element is given by the open file's
  bindings and where each value came from.
  The sources are live from the game, recorded, the game's data (the asset and its field), a sample value or a
  placeholder.
- **How it works**: the preview follows a binding's path of fields from the scene's data object.
  It names the fields (by the game's field table in the index folder, where there is one, and by the names given in
  View models).
  It reads the asset that path stands for: the scene's own config asset (`QuickToolsScene`, `ReligionScene` ...), the
  element of a list, or the object an object menu shows.
  A run-time value (money, a patron's belief) comes from the Preview settings or from a typical value of the game's
  data.
  The rules per scene and what is left out are in `ui/117/game-data.md`.
- It reads the game's asset index (the asset editor's, built once and kept in the workspace) off the window's thread
  when a game opens.
  The preview lays out again once it is there.
  A scene with game data places more of its parts apart (a quick tools wheel about 5 s, the options scene about 40 s,
  the first time).

## The baseline: what the game showed in many sessions

Without a running game, the preview makes a value up wherever a scene reads one at run time.
The **baseline** (`uiedit/baseline.py`, the shipped file `uiedit/data/ui-baseline-117.json`, **View > Use the game's
typical values**, on by default, 117 only) replaces such a value by the one the game itself held most often.

**Where it comes from.**
The source is recordings of the widgets of the running game: 4,606 of them, mostly in German.
They are per widget file and widget (its name CRC, as live values match).
They are also per placing context (the container that places the file) where the context changes the value.
For each, the value held most often across the recordings is kept with its share.
Several widgets of one file in one recording (a list's items) share that recording's one vote.
So a widget that shows a different text per item is not given one of them.
Two game builds are among the recordings: update 3.0 (3,821 recordings) and update 3.0.1 (785).
A game update changes texts, pictures and visibilities, so where at least two recordings of the newest build hold a
value, that build alone decides it, and the older recordings count only where the newest has too few.

Note: the recordings were made from 2026-11-16 to 23. The newest build was added on 2026-11-23.
Held out against the newest recordings, the build rule raised the texts the baseline gets right from 106 to 156 of 295
and its wrong pictures fell from 619 to 482 of 10,421 (see "Measured on 2026-11-23" below).

**What it holds** (references and plain values, no game content: a text id is not its text, a picture is a path, no
picture is stored).
The file is regenerated from the recordings by a script of the maintainers' tooling.
It names the build, the dates and the counts.

| Field | Value | Kept when its share of the recordings is at least |
|---|---|---|
| `t` | a label's text as the id of its line (the line is read in the language of the preview) | 50 % [I] |
| `n` | a text that is no line and has no letter (`0 %`, `12/20`), and "" for a label the game left empty | 80 % [I] |
| `r` | a label that showed a campaign character's name: the character's role and place in it (the cast of the Preview settings gives the name) | 50 % [I] |
| `i` | an image's `DynamicImageId`: a game path, or "" (the game showed none) | 50 % [I] |
| `p` | an image that showed a character's portrait or logo: role, place, which (the Preview settings give the picture) | 50 % [I] |
| `v` | whether the widget is visible | 60 % [I] |

Left out on purpose:

- a text with letters that is no line of the game (a player's, island's or quest's name, a number with a unit);
- run-time pictures (`rtt_<n>`);
- visibilities from a recording of a scene that was loaded but not shown (under 5 % [I] of its widgets shown), or below
  a hidden widget.

A widget's own property values are not kept: the label or image inside the placed file holds the value the placing file
hands it.
The counts of the shipped file are 1,949 widget files, 11,173 widgets with 15,294 values, and 623 context entries.
The values are 9,652 visibilities, 5,180 pictures, 297 texts, 151 plain texts and 14 characters.

**How the preview uses it.**
The order of sources is:

1. live values and recordings;
2. a value set by hand;
3. the baseline, where the preview would make a value up;
4. the Preview settings' campaign data, for what is per player or per session;
5. the game's data;
6. a placeholder.

The baseline is used in three ways:

- A label's text that would be a filler, or none, shows the baseline's: the line of its id in the preview's language, or
  the plain text.
  A stored designers' text such as `!Reset` counts as a filler here.
- An image that would draw a stand-in for a picture the game names at run time shows the baseline's picture, or none
  where the game showed none.
- A visibility a binding computed from a made-up flag is the baseline's.

A value the game's data or a binding really gave is not replaced.
The game's data and the baseline agree where both exist, and the game's data is exact for the open scene.
A baseline value that does not resolve on the install is skipped (a line or picture another build or DLC set does not
have).
Play mode, live values and recordings win over all of it.
The Properties "where a value came from" list is not changed, because the baseline is the preview's own fill.

**What is per player or per session** comes from the Preview settings' campaign data, not from the recordings.
This is the player's name and portrait, the rivals, the emperor and the pirates (`previewdefaults`).
The baseline keeps only the role of a character a widget showed.
The preview also takes a name field the player types into (`Text_Input`, `NameInput`), a character's, session's or
province's name, and the number of players from there.

**Measured** on 2026-11-19, on 117's 195 scenes, in English, with the default Preview settings and no recording.
The count is the same before and after.
It differs a little from the table below it, which also counts empty texts.

| What the preview draws | before | after |
|---|---|---|
| texts drawn | 1,834 | 1,630 |
| filler texts | 1,057 | 875 |
| picture slots (a stand-in icon) | 1,112 | 811 |
| pictures the game names that this install has | 7,557 | 7,906 |
| pictures the game has no file for | 7 | 11 |
| stored texts of the files (`!Headline`) | 55 | 68 |
| picture items drawn | 37,234 | 36,290 |

The Preview settings' part alone takes 2 more fillers off (1,057 to 1,055).

**Measured on 2026-11-23**, against the newest recordings (update 3.0.1, 21 scenes), with the baseline made from the
recordings before them, so it did not see them.
The table counts the values the recordings hold for a widget and what the baseline holds for it.

| Value | version | right | wrong | no entry |
|---|---|---|---|---|
| texts | all recordings together | 106 | 8 | 181 |
| | newest build decides | 156 | 24 | 115 |
| pictures | all recordings together | 9,477 | 619 | 325 |
| | newest build decides | 9,621 | 482 | 318 |
| visibilities | all recordings together | 5,854 | 100 | 125 |
| | newest build decides | 5,851 | 81 | 147 |

What is still wrong is mostly a widget one placing styles differently: the construction menu's button backgrounds
(a round or a square shape) and the region's hairlines (Roman or Egyptian).
Their placing context does not name the style.
The preview's own bindings give these where the file's own properties are filled.

**What is left.**
875 fillers and 811 slots stay.
They are where no recording holds a value the game showed in at least half of its sessions:

- a script's text (a quest objective, a headline);
- a name or a count that differs per game;
- a list's item icon (the asset the item stands for: a plan per scene would give it);
- 46 scenes that the recordings do not cover.

The fewer texts drawn are labels the game showed empty or hidden in nearly every recording (the construction menu's
nine filler labels).
Two scenes show no picture at all now: the two gamepad prompts, which the game hides on a mouse.
The **stored texts** went up because a label the game showed is now visible.
Not built: the baseline for 1800 (its dumps do not exist).
Also not built: numbers and flags beyond visibility.
What a count or a flag is in a typical state is `placeholders`' and the Preview settings'.

## What the preview fills, and what stays a sample

This was counted offscreen over every scene of the installs (117: 195 scenes; 1800: 303), in English, with the default
preview settings and no recording.
A **filler** is a text the preview makes up ("Lorem ipsum ...").
A **picture slot** is a picture the game names at run time, which the preview stands in for with an icon of the game
picked by the slot's name (not the picture the game shows).

| What the preview draws | 117 before | 117 after | 1800 before | 1800 after |
|---|---|---|---|---|
| texts drawn | 2210 | 2195 | 7616 | 7624 |
| filler texts | 1266 | 1058 | 4538 | 4217 |
| text ids shown raw (`[id]`) | 0 | 0 | 576 | 0 |
| stored texts of the files (the designers' own, `!Headline`, `asdsad`) | 125 | 95 | 1166 | 756 |
| pictures the game has no file for | 737 | 86 | 4072 | 18 |
| of which a filler text taken for a picture name | 651 | 0 | 4054 | 0 |
| picture slots (a stand-in icon) | 414 | 1114 | 237 | 4291 |

What changed, each rule read from the game's files (`labels.py`, `scope.py`, `placeholders.py`, `uifiles.py`):

- **A label with a text id shows its line.**
  The game shows the line a `LocalizationId` names, and the label's stored `Text` is only what the editor showed.
  The preview used to show the stored text first (`!Pyramid Restored` for the line "Pyramid Restored").
  The line wins over any text a binding writes as well, a filler or a designers' text such as `!Game is paused`.
  Every label of the game's recordings that held both a text id and a text showed the line (202 labels).
  A binding that writes an empty text id clears it, and the label shows its text then.
- **1800's text ids resolve.**
  1800 numbers its texts (`<GUID>5998</GUID>` in `data/config/gui/texts_<language>.xml`, the id a decimal number).
  The preview used to read 117's tables only, and drew 576 labels as `[5998]`.
- **A text property that ends in a picture is a picture slot.**
  This covers a string property a file binds into an image's `DynamicImageId`, or hands on to a placed file that does.
  It also covers a string property named like a picture (`Icon`, `Portrait`, `Model2D`, not `IconText`).
  These used to get a filler text, and the picture they fed was a grey box named "Lorem ipsum dolor sit amet" in
  Checks.
  Now it is a picture slot: a game icon (or the game's data where a scene plan gives it).
- **A game field's name names its filler.**
  A filler used to take its kind from the target alone.
  Now the source's game name (`Amount`, `PlayerCount`, `IsLocked`, `Icon`) is read from the field table.
  So a count is a number, an id is a number, a flag has the state its name says (a typical state, see above), and a
  field that is the target's text id stays unfilled.
  About 470 image layers fewer are drawn in 117's scenes than before, because flags that were `true` by default (a
  visibility) take their name's state.
- **A game object is no text.**
  A list's reference reaching a label drew `SGameObjectRef(...)`; it is empty now.

**What stays a placeholder, and why.**
This is about 117.
Every one is a value the game has only while running, or one the files do not map to an asset.

- *Texts, 1058 of 2195.*
  The leaf names, by count, are:
  `Text` of a text object (760: a headline, a description, a button's line, set by the scene's script), `ButtonText`
  (84), `Value` (56), `ButtonAction`, `FeedbackLabel`, `CurrentValue`, `TimerValue`, and a name typed by the player
  (`Text_Input`, `Name`, `IslandName`).
  The values are the running game's state, a script's choice of a text line or a player's input, and no file names
  which line.
  The texts a scene's config asset or the five plans (`gameplans`) hold are filled.
  No other scene has a plan (`ui/117/game-data.md` "Left out").
- *Picture slots, 1114.*
  An `Icon` of a list element (a good, a building, a research node) is the asset that element stands for.
  It is known only with a plan for that scene.
  The stand-in is an icon of the game's own folders for the slot's name, never a blank tile.
- *Stored texts, 95.*
  A designers' text the label keeps and no line replaces (`asdsad`, `!Subline`).
- *Pictures the game has no file for, 86.*
  These are image assets and widget files the shipped game does not carry (the scenes' notes say "not in the game"),
  and the DLC category picture of an uninstalled DLC.
- *Numbers and flags.*
  A count, a progress, a timer or a selection is a typical value by its name (see above).
  Only a recording or the Preview settings give the real one.
- *An object menu's object.*
  A scene of an object menu (`om...scene`) shows its object's name, icon and texts only for a residence and a
  production building (the Preview settings).
  No asset says which building a menu scene belongs to, because the menu is chosen in the game's own logic.
  So the other menus keep fillers.

Open (not built): a plan for the scenes whose lists are assets (the goods, the research nodes, the shop's packages).
Also open: 1800's field table.
Its fields stay unnamed, so a filler there takes its kind from the target alone.

Note: counted on 2026-11-19; "before" is the state of 2026-11-18.
The rule about text ids was seen in the game on 2026-09-21.

**Held against the game, 2026-11-23.**
18 of the most seen scenes were placed in German (the HUD bars, the construction menu, six object menus, the
notifications, the minimap, the info tip, the quick tools, the governors' requests, diplomacy, the trade routes, the
goods transfer and the options).
Each was held against the newest recording of the scene in which it was shown.
Per widget file and widget, the values the recording holds were counted against the values the preview gives the same
widget, every placing of it counted.
These are the texts of a game line and the pictures of the widgets the game showed, and every visibility.
The preview's region was set to the one the recording shows.

| | texts | pictures | visibilities |
|---|---|---|---|
| before | 16 of 125 | 4,090 of 7,314 | 10,463 of 18,367 |
| after | 22 of 125 | 4,140 of 7,314 | 10,464 of 18,367 |

What the preview still gets wrong, by cause (after):

| Cause | texts | pictures | visibilities |
|---|---|---|---|
| a list's items the game fills at run time that the preview does not place (markers, goods, routes, a category's buildings) | 47 | 2,969 | 7,526 |
| a made-up value (a filler, a stand-in picture) | 26 | 99 | - |
| a style one placing gives (a round or a square button) | - | 59 | - |
| a style of the region (Roman or Egyptian hairlines) | - | 0 (42 before) | - |
| a designers' text where the game's line has no id in the files | 13 | - | - |
| another value (the session's state, another item, another building) | 17 | 47 | 377 |

What changed, each rule read from the game's data or its recordings:

- A component on a widget file's own root takes the player's state as one on an instance does: the region's hairlines
  and deco borders.
- A label's text id wins over any text (above).
- The construction menu holds its categories with their own pictures and tier numbers (game-data.md, the plans).
  The infrastructure category has no tier number, and the preview shows none there either.
- A field that names another asset (`InfotipGUID`) is no longer given the node's description.
- The object menus other than the residence and the production menu no longer show the jeweller's name.
  The residence and the production menu show the building's category under its name.

What stays a placeholder, and why:

- *Run-time lists*: the minimap's markers, a warehouse's goods, the trade routes, the diplomacy partners.
  These are the session's state, which no asset lists.
- *A category's buildings*: the game shows the selected category's buttons by an animation that its list selection
  starts.
  The preview's list does not select an item yet, which is the layout's part.
- *The HUD bars' attribute rows*: their files read paths that start at the file, not at the scene's data object, so no
  plan reaches them.
- *The other object menus' objects*: no asset says which building a menu shows.

The measuring driver is scratch and not kept.

## The language of the game's texts

The **Preview settings** panel's **Game language** picks the language of the game texts the preview shows.
The twelve languages 117 ships are English, German, French, Italian, Spanish, Polish, Russian, Brazilian, Japanese,
Korean, Simplified Chinese and Traditional Chinese.
The choice is kept per game with the other preview settings.
The default is English.
The game keeps the player's own choice with the account, which the Toolbox does not read.
It is not the Toolbox's own interface language.

- Every text the preview takes from the game follows it: a label's text id, the game's data (asset names,
  descriptions, effect texts) and the hints a scene names.
- **Fonts**: a label draws with its localized font's face for the language, as the game does.
  Chinese, Japanese and Korean use their own faces (no empty boxes), and the Latin and Cyrillic languages use the
  default face.
  No shipped language is written right to left.
- Wrapping, widths and cut-offs follow the label rules, so a longer language wraps where the game would.
- Live values and recordings keep the language the game had when they were taken.
  Where a recording carries its language and it is not the one shown, the status bar's note says so ("live: German").
  A recording carries it (`language`) when the game's answer names one.
  Today's link helper names none, so the note shows for a recording that was given one by hand or by a later helper.

Note: corrected on 2026-11-18: the `language` field used to be read from nowhere.

## Play mode

The **Design** / **Play** switch sits in the toolbar and the View menu (F5 plays, Shift+F5 goes back to Design).
In Play mode the preview behaves as the game would, and nothing selects, moves or edits the file:

- **Pointing** at a widget gives it the hover the game gives it (a mouse handler's `IsHover` and `OnMouseEnter`, a
  button's `OnMouseEnter`).
  **Pressing** adds the press, and **releasing** over it adds the release.
  What the scene's bindings start with them shows: a hover animation plays over time from the moment of the change (in
  the nested files too), a flag, a toggle.
- **A click** does one of these:
  - selects a tab (its bar's `SelectedTabID`; the pages bound to it switch);
  - flips a checkbox;
  - steps a toggle over its entries (`SwitchChoice`: the left half back, the right half on; the entries from its data,
    else its field's enum);
  - opens a combo box (its list shows under it, its entries from the game's data where the resolver has them; a click
    on one picks it and closes the list);
  - picks a radial wheel's segment.
- A **drag** moves a slider in its steps, a scroll bar's handle (the list bound to its `ScrollInfo` scrolls with it) and
  a draggable button (within its range; `DragStarted` while dragging, `DragStopped` at the end).
- The **wheel** scrolls a list (a grid by a row).
  Ctrl+wheel still zooms, and the middle button or Alt still pans.
- **Resting** on a widget with an info tip shows the tip's text: its text line, or the name and description of the asset
  it is about from the game's data.
- **A button that opens another screen**: where its release goes into a field named `Open<Name>`, the scene
  `<name>scene` opens in the editor (still in Play mode).
  Otherwise the status bar names the field the click fires, or says that the screen it opens is not a scene file.
- **The scene's start**: the open file's animation that its `AddedToScene` event starts plays once and holds its end.
- **The keyboard**: Tab and Shift+Tab move the focus over the widgets that take a press (outlined in green), in drawing
  order.
  The arrow keys move it to the neighbour the widget names (`NeighborUp` and the others), else to the nearest widget
  that way, as a gamepad does.
  Enter or Space presses.
  Left and Right move a focused slider and step a focused toggle.

**Choices the game keeps.**
Some controls write their value back into the game's data through a two-way binding.
These are an option, a difficulty, the profile's customization, a key binding and a tab the game remembers.
Play mode writes such a value into the Preview settings (`Store.set(..., play=True)`).
It is named by the field's path of names (`play.optionsscene.GameContext ...`), never by the widget.
Every open preview and the Preview settings then show the new value, as the game would after the change.
The Preview settings list them under the tabs.
**Reset Play mode choices** (View menu, or the button in the Preview settings) clears them and what the session set.
A value no binding writes back (a tab of a page, a slider of the preview only) stays for the session.
Design mode never writes to the store.

**Smooth.**
Input changes Play mode's values on the window's thread.
The layout runs off it, one at a time, with the newest values winning and the last finished frame shown meanwhile.
A change places again only the lowest placing it reaches, at its place, and puts its items into the shown frame.
The file around it is placed again only where the placing's size or outputs changed (game-data.md, "Speed").
A choice drops only what read that field and places the file once.

The times below were measured on 2026-11-17 with the game's data.
Each run used the same steps: a tab hovered, clicked, left, a second tab hovered and clicked, the first hovered again.
Other agents loaded the machine meanwhile.
A whole Design layout of `optionsscene` took 29 s in the quietest run and 60 s in the busiest.

| Step | `optionsscene` before (round 1) | now, quiet | now, busy | `quicktoolsscene` now |
|---|---|---|---|---|
| first hover | 6.0 s | 0.54-0.63 s | 1.5 s | 0.18-0.22 s |
| click | 5.1 s | 1.8-3.0 s | 3.7 s | no change (the hover set it) |
| leave | 0.6 s | 0.10-0.37 s | 0.7 s | 7-10 ms |
| next hover | | 0.12-0.41 s | 1.1 s | 0.09-0.11 s |
| next click | 0.85 s | 0.20-0.58 s | 1.2 s | |
| hover again | | 0.11-0.28 s | 0.57 s | 10-12 ms |

Round 4 (2026-11-17, a loaded machine) gave these times:

- a hover took 0.30-0.43 s;
- leaving and hovering again took under 10 ms;
- a tab click took 3.8 s with the page it opens.

The click now selects the tab and switches the page (`ui/117/layout.md`, "Values the game gives at
run time"; the Design layout's times are there under "Open").
The rest of a hover is the bindings of the files on the way, evaluated with the new values.
These are a tab's file and its button's, each run again for every step of its size bindings.
A click on an options tab widens the tab bar, so the bar's file is placed again too.
A tab's selected look in the options scene is in game-data.md, "Checked against the game's dumps".
The design of each rule, and what is not built, is in `ui/117/game-data.md`, "Play mode".
The code is `uiedit/playmode.py` (the switch and the window's side), `playinput.py` (what each input does) and
`playstate.py` (the values in the layout, the placing again).

### Preview settings

View > Preview settings opens a dock that shows and changes everything above, on top of the store.
There is no second store: every control writes through `Store.set`, and the preview is laid out again at once.
The choices are read from the game's assets off the window's thread.
Until they are there, the panel says so and offers the campaign's own.
A small reset button beside each value puts that value back to the campaign's (it is off while the value is not
changed).
Every control has a tool tip.
The labels have access keys, and no two of them clash (the tabs and the buttons below take theirs first).
The tabs are:

- **Player**: name, portrait, emblem and colour as grids of the game's own pictures (like the profile customization),
  the banner's parts (logo colour, shape, pattern, background, stripes) and the title's prefix and suffix.
  The emblems are white pictures, so their grid has a dark ground.
- **Characters**: the emperor (one reigns), the rivals and the pirates as lists with portraits and names.
  The ones checked take part.
  With none changed, the campaign's cast stays, a pirate of the region first.
- **Session**: the difficulty preset, the start province, the seed and every `Setup...` option with its values (a search
  line filters them).
  "Campaign setup" puts all of them back.
- **Progression**: the preset (everything unlocked, the campaign's start, up to a tier), and the tiers reached per
  region with their residents.
  A tier set by hand wins over the preset; with none checked, the preset decides.
  It also has a searchable checklist of the assets locked at the start, grouped by their template (the data does not say
  which menu shows them), with "Lock the shown" and "Unlock the shown" for the search's result.
- **Research**: the research tree by category, each node researched or not, a repeatable node with a count.
  The presets are everything (the default, a repeatable node once), nothing, and up to a row.
  A preset drops the nodes set by hand; only what differs from the preset is kept (`options.research.<node>`).
- **Platform**: the input prompts (mouse and keyboard, Xbox, PlayStation), single or multiplayer and the owned DLCs.
- **Values**: season, island, the object of the residence and production menus, money, the stock of each good, and the
  game's language.

Below the tabs are **Keep these settings in the project** (`keep_in_project`; off when there is no project), **Reset to
the campaign defaults** and **Reset Play mode choices** (`reset(played=True)`).
A note shows while live values or a recording are shown for the open file, since they win over these settings.
The code is `uiedit/previewpanel.py` (the panel), `previewsections.py`, `previewsetup.py` and `previewprogress.py` (the
tabs) and `previewwidgets.py` (the picture grids).
`Store.set_many` keeps several values and tells once (a preset that sets a node each).

## Editing

Every edit goes on the undo stack (Edit > Undo, Ctrl+Z; Redo, Ctrl+Y).
The window's title shows `*` while the file has edits the project does not hold.

- **Fields**: double-click a value in Properties and type.
  Numbers, `x, y, z` vectors, `true` / `false`, an enum value's name or number, a GUID for a reference (empty: none)
  and `r, g, b` for a colour are accepted.
- **Values of a widget file's or a component's properties**: the same, under "Its widget file's properties" and
  "Components".
  A value the instance does not set yet is added.
- **Move and size** in the preview: drag the selected element to move it (its `Position`, in its parent's units), and
  drag the square handle at its corner to size it (its `Scale`).
- **Add**: from the palette, an image, a label, a container or a video, as a copy of one the game ships (a label keeps
  its text style that way).
  A container asks for the widget file it places, from a list of the game's and the project's widget files.
  A component or an effect goes onto the selected element.
  **Drag** an entry from the palette into the preview to place it where it is dropped.
- **References**: right-click a reference field in Properties (`Image`, `TextStyle`, `Template`, `LocalizedFont`, a
  palette, an effect, a component or a widget type) > Choose.
  This lists the files of that kind by name, with a filter, instead of typing a GUID (the first match is selected, and
  Enter takes it).
- **Duplicate** (Ctrl+D) copies the element.
  **Remove** (Del) deletes it, and the bindings that name the element go with it.
- Edit > **New custom property** adds a custom property.
- **New widget file** (File, Ctrl+N): an empty widget file whose GUID follows from the project's id and the name given.
  Place it with a container's `Template`.

New objects are written in the layout version the file already uses for their type, else the newest the game's files
use.

## The sequence editor

The Sequence dock shows the animation picked in States and events.
It has one row per track (named after the binding end it drives), its keys as diamonds, and the play head at the
timeline's time.

- **Pick** a key: its time, value and interpolator show below.
  Change them and Apply.
- **Drag** a key along its row to move it in time; the keys stay in time order.
- **Double-click** a track's row where it has no key to add one there, with the value the track has at that time.
- **Del** removes the picked key.
- **Interpolators**: Constant (hold), Linear and the Widget Library's ease types (`Sine_In` to `Bounce_InOut`).
  The preview eases between keys with them.
- **Tweens**: the table below lists the tween components of the file's elements (Animator, AlphaTween, ScaleTween,
  PositionTween, RotationTween).
  It shows their start and end values, duration, delay, ease, loops and yoyo.
  Write a value to change the instance's own value.
  "The file's tweens, started together" in the timeline plays them from 0.

Every change is an edit on the undo stack and is written with the file.

## Saving and the mod

- **File > Save into the project** (Ctrl+S) keeps the open file whole under `<project>/ui-edits/<game>/<game path>`.
  A file without edits is written as its original bytes.
  An edited one goes through the codec, which writes every shipped 117 file byte for byte (and 93 % of 1800's; the
  status line says when the open file is one of the others).
- **File > Revert the file to the game's** deletes the project's copy.
- **File > Check the project's references**: every file the project's UI files name must be in the project or the game
  (the game fails a whole scene over one missing file).
- **The mod**: the suite's Project window builds every app's share into `build/<mod id>/`.
  The UI files go in as loose files at their game paths, and the loader lint repeats the reference check.
  Install as for any project.

**Live reload of a scene.**
With the live link connected ([`live-link.md`](live-link.md) section 10.1), saving a scene or widget file of the project
announces it to the running game (`reload.scenes`).
The screens that show the file are left and entered again, so the edit shows without a restart.

## Live values

With the live link connected and the game showing the open file, **File > Take the live values from the running game**
asks the game what the file's widgets show now ([`live-link.md`](live-link.md) section 17).
For a scene file it asks for its own widgets.
For a widget file it asks for the widgets of its first instance in a scene the game has open.
The preview then shows, matched by the instance's GUID or its name:

- a label's text in place of its sample text;
- an element hidden or shown as the game has it;
- an image's picture as the game names it (an icon, a portrait), read from the installed game;
- a placed file's own property values in place of the values its instance stores.

The game sends only these values.
The sample values stay as they are, and a widget the game does not answer keeps them.
**View > Show the game's values in place of sample values** (on by default) switches them off and on.
The status bar names where the values came from: the game build and the time.

**Recorded for later.**
Every answer taken is recorded on this computer, per game and per file (`ui-values/` in the workspace, nothing in the
project or the mod).
Taking the values again replaces the recording.
With no game linked, opening the file fills the preview from its recording.
**File > Use the recorded values** reads it again.
When the file changed since, the status bar says how many recorded widgets no longer match an element (stale).
Those keep their sample values, and the rest still fill.
**File > Clear the recorded values of this file** and **Clear all recorded values...** delete them.
The recordings stay until then, across restarts.

**What the game assigned.**
**File > List what the game assigned...** lists the assets, icons and portraits the held values name.
A property value that is an asset's GUID is listed with the asset's name and icon from the installed game (the asset
editor's index, built once).
Each picture the widgets draw is listed with the file it is in the install.
A picture with no file in the install is marked so.
It is either one the game makes at run time (an id such as `rtt_166413`, a render target or a composed portrait) or an
id the editor does not resolve.
The preview draws a stand-in for it.
No picture is stored: everything is read from the install when it is drawn.

Note: built on 2026-11-17, not tried with a game.

## The game mirror

**View > Game mirror...** opens a window of its own that shows what the running game shows.
It shows every UI scene the game has open (the HUD's parts, an object menu, the construction menu, a popup), stacked as
the game stacks them, each filled with the game's live values.
It follows the game through the live link ([`live-link.md`](live-link.md) section 18).
When the game opens a scene it appears in the mirror, and when the game closes it, it goes.
The editor's own window stays as it is: following never changes the file open in the editor.

- **The scenes.**
  Each scene is laid out by the preview's own code at the game window's shape (the size the game reports) and drawn
  into a picture of its own, off the window's thread.
  The mirror puts the pictures over each other, the scene the game opened last on top.
  Only a scene whose values or file changed is laid out again.
- **The list of open scenes** beside the picture names each scene with its layer (1 is the bottom) and its parent state.
  Untick a scene to hide it for a look at what lies under it (the game is not told).
  Double-click a scene in the list, or click it in the picture (the topmost scene that draws something there), to open
  its file in the editor.
- **Live values.**
  While it follows, the mirror asks the game for the values of the scenes it shows, one request at a time and at most
  four a second, slower while nothing changes.
  Each answer whose values changed is recorded as **Take the live values** records it, so the editor's preview of that
  scene shows the same values afterwards.
- **Size**: the game's resolution, or scaled to fit the mirror's window (the default).
- **Pause** stops following: the mirror keeps the last picture and asks the game nothing (no subscription, no request).
  **Resume** asks the game for every open scene again.
- **The status line** says `following the game: <scene>` with the scene opened last, or `paused`, or why it cannot
  follow (no game linked, a game that does not send scene events).

**Push edits to the game** (an option of the mirror, off by default) closes the loop of live editing:

1. edit a scene or a widget file in the editor that a scene the game has open shows;
2. after a moment with no further edit (1.5 seconds), or at once with **Push now**, the file is kept in the project as
   **Save** keeps it and written into the installed copy of the project's mod, and the game is asked to reload the
   scenes that show it (`reload.scenes`);
3. the game reloads the scene;
4. the mirror takes that scene's values again and shows the result, in the game's layering.

A push writes only into the project and into the project's own mod folder in the mods folder.
It writes there only when that folder holds the project's mod, so install the project once first.
A scene the game does not have open is not pushed.
A refused or failed reload (the game is busy, the scene is not open, a widget file is missing) is said on the status
line, and the mirror shows the game's scene as it is.
A reload of a scene the player is using resets its state (the scroll, a typed text).

Note: built on 2026-11-17, not tried with a game.

## Anno 1800

1800's files are read with its own layout versions (the format note, "Layout versions").
All 6,870 of its files open, the earliest editors' among them ("Read from the bytes" there).
1800 keeps a widget file's custom properties in another field than 117 (`CustomProperties`).
They are read and written there, so its 515 bindings of an own property run in the preview.
1800 ships no Lua type files, so the view models are empty there.
1800's scenes are drawn in 1800's own font files.
Its text styles' font chain does not always decode, and the first font file of the game stands in then.
1800 ships no Widget Library (`data/ui/pwl/` is empty), so its palette lists only the new widgets, and its components
are named from the files.

Seen offscreen: 1800's scenes open and are drawn (its achievement scene with its designers' placeholder texts).
Nothing of it was edited or loaded in the game.

## The History Editions (1404 and 1701), 2026-11-13

Anno 1404 and Anno 1701 as re-released (the History Editions) ship a Phoenix interface of their own beside the old one.
`data/ui/studio/gamedata/` holds 732 files named by GUID.
They are 294 widget files, 271 images, 41 text styles, 30 widget types, 28 enums, 18 scenes, components, effects,
fonts, a palette and more.
The scenes are the files with a `.scene` suffix: `mainmenu`, `credits`, `settings`, `infotip` and so on.
The files are delivered in `ahe/a4he0.rda` (1404) and `ahe/a3he0.rda` (1701).
`data/ui/studio/` holds the editor's own JSON documents of the same objects (807 files: scenes, widgets, images, enums,
game objects), with every class and field by name.
The two games' files are identical.

File > Open game lists both games.
The Files list shows the scenes by name (the `.scene` suffix dropped).
The Widget Library comes from `data/ui/pwl/data/` and `data/ui/studio/` (22 components, 32 widget types, 31 enums).
Pictures are the plain `.png` files at the image asset's name.
A project keeps changed files under `ui-edits/anno1404/` (or `anno1701`) at their game path, as for 117.

**What opens: all 732 files, the 18 scenes among them, each re-encoding byte for byte.**
The files are the same container (`PHX`, serializer `0x108`, flags `0x18f0`, editor version 1.16).
Nearly every type is in an older layout than 117's or 1800's.
43 type records carry layout GUIDs of their own.
Two types (the text style and the font asset) are not in 117's schema.
A container instance carries a list of typed values (`DynamicProperties`).
The codec reads them from the History Editions' own layouts, each one proven by re-encoding every file that uses it
(`formats/phoenix-scene-format.md`, "The History Editions' files").
The preview places a widget by the older anchor constraints (left, right or centre per axis, read as 117's anchor point
and resizing modes).
It reads a label's alignment as stored as 0 to 3.

The binary files carry no instance names (only their CRC32).
The element list, the bindings and the paths take each element's name from the editor's JSON document of the open file
(`data/ui/studio/<folder>/<name>_<guid>.json`, every object by its `ObjectID` and `Name`).
This names 1,809 of the 1,847 instances of the scenes and widget files, and every name matches the file's `NameCrc`.
Five widget files (35 instances) ship no document.
An element that the document does not name, or one that was renamed afterwards (its `NameCrc` is not the name's), shows
"(unnamed)".

The bindings (7,093) are read from these games' own lists.
The preview evaluates them, the graph names their ends, and a new custom property or binding is written where these
files keep them.
The values that a container gives the file it places in `DynamicProperties` are passed in (`layout.Context.values`).
Each id is the `AtomPropertyId` of a custom property of the placed file, or of its widget type and that type's parents.
Counted on 1404's install, 1,916 of the 7,103 values are the placed file's own, 548 its widget type's, 2,721 the parent
types', and 2 are not found.
The value goes to that property's UniqueId.
A value that the instance's own `PropertyValues` give wins.
Nothing of it was loaded by a game.

**A widget's properties are its file's and its widget type's** (all games).
A widget file's `WidgetType` and that type's `ParentWidgetType` chain declare custom properties that every widget of
the type has.
The bindings read and write them as the widget's own, by UniqueId (117: 902 binding ends, the History Editions:
1,137).
The preview gives the widget those properties with their declared defaults (`scope.widget_types`; the file's own
declaration of a UniqueId wins).
Such a binding therefore reads the type's default, or the value passed in, instead of nothing.

Note: this section is as of 2026-11-13 and 2026-11-14.

## The old interface

For 2070, 1404 and 1701 the third tab, **Old interface**, is a read-only page.
Its game box picks 2070, 1404 or 1701, and follows the suite's game choice where that is one of them.
The page shows the dialogs, templates and backgrounds of the game, a dialog drawn with its pictures and texts, and an
inspector for the widget picked.
What it draws and leaves out, and why the compiled pools cannot be written, are in `ui/old-gui.md`.

## Speed

A file is opened off the window's thread (decoded and checked for a byte-for-byte re-encoding: "opening ...").
The one asked for last is shown.
A scene is placed off the window's thread too: opening one shows "laying out the preview ..." in the status bar while
the rest of the window answers.

An edit, an undo or redo, a sample value or a revert made meanwhile waits.
It is made, in the order it was asked, when the layout is done.
The order has these consequences:

- A file opened after a waiting edit opens after it, so the edit is made in the file it was made in.
- An edit asked for while the open waits is made in the file shown then.
- An edit reads what it changes when it is made, so two moves that waited add up.
- A view switch (the design view, the hidden layers, the timeline) is taken by the next layout.

What the layout computed is kept between edits, with the files and sample values it read.
So an edit places the edited file again and takes the widget files it places from what was kept.
That takes 0.1 to 0.2 s for the largest scenes (the construction menu, 84 s before).
The elements of a list that hold no given value share their work, so the sample panel lists the first element's
values.
A value set under an element's key separates that element.
A sample value set by hand places again only what read it, and a changed view switch starts over.
The numbers and what is left are in `tool-suite.md` section 20, "Speed".

## What a person should try

1. Open Anno 117, and open `maintitlescene`, `loadingscreenscene`, `romanizationchoicescene` and a widget file of the
   construction menu.
   Compare the preview with the game's screens (the design view on and off, View > Fill unbound data with placeholders
   on and off).
   Look at Checks for what did not resolve.
2. Select a label, change its `Text`, Save into the project, Build mod in the Project window, and start the game with
   the mod.
   The text must show (and `mods/mod-loader.log` stay clean).
3. Move an element with the mouse, Ctrl+Z, Ctrl+Y.
   Add an image from the palette and point its `Image` at another image asset's GUID.
4. In Bindings, change an operator of a binding that feeds a visible value and watch the preview.
   Add a New binding from a constant to an element's `AlphaTint`.
5. Name an id in View models and see it in the graph.
   Write a sample value and see the preview follow.
6. Play an animation on the timeline (the title's `Anim_In`).
   In Sequence move a key, add one by a double-click, change its interpolator, and Ctrl+Z.
   Play "the file's tweens" of a file with an Animator and change a tween's end value.
7. Open Anno 1800 and a scene of it.
8. Drag an image from the palette into the preview.
   Right-click its `Image` field > Choose and pick a picture.
   Set a label's `TextStyle` the same way.
9. View > Show the 3D scene behind the UI, then View > Draw the scene behind the UI from a model of the game... and pick
   a residence.
   Open `statisticsscene` or `blurscene` and compare the blurred glass with the game's over a city.
10. View > Draw the scene behind the UI from an island of the game... with a pool island.
    Compare the blur and the darken over it with the game's over the same island.
11. Open `statisticsscene` (its files nest 24 deep) and a scene with a list fed by its file's preview values.
    Compare the list's size and the deepest parts with the game's screen.
12. With the link helper and a session running, open the HUD's scene and take the live values.
    The game's texts and icons show in the preview.
    Close the game and open the file again: the recorded values fill it, and the status bar names the build and the
    time.

## Language

The UI editor's windows are in English or German.
Choose the language in the settings (General page, "Language"); it applies the next time the app starts.
Game data (asset and file names, the games' own texts) is shown as the game has it.
See [the suite's guide](suite.md#language).
