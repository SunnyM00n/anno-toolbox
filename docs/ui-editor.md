# The UI editor: the interface files of every game

The suite's **UI** app has one page for each kind of interface file, with a switcher on top (**Interface files of:**).
The first page, described here, opens the Phoenix files that Anno 117 and Anno 1800 build their interface from (scenes and
widget files in `data/ui/studio/generated/`). It previews them with sample values in place of the game's, shows their
bindings as a node graph, and edits them with undo. It keeps the changed and new files in the open project, whose
build puts them into the mod. The design and its status are in `tool-suite.md` section 20. The file
format is described in `formats/phoenix-scene-format.md`. The UI's own rules
(layout, widget types, components, masks) are in the UI reference. Anno 2205's Flash interface is the app's
other page: [The Anno 2205 page](#the-anno-2205-page).

**Nothing the UI editor writes has been loaded by a game yet.** The codec's output is known to load (changed files and
new widget files). The editor's edits on top of it are currently unverified.

Note: the codec's output was loaded in the game on 2026-09-21; the editor's status is as of 2026-11-09.

## Starting it

- `anno-toolbox --app ui` starts the suite's window on it. In the suite it is **UI** in the app switcher (Ctrl+4). The switcher on top of the app
  changes the page; the app opens on the page it had last.
- File > Open game takes the game's folder from the suite's settings (the island editor's File > Games and folders).
  The first opening indexes the game's UI files (117: about 6 seconds). The index is kept in the workspace's
  `game-index/<game>/ui/`, the one index folder that every app of the suite uses per game. The editor then reads in the
  background which instance names each widget file, so the file list can show widget files by those names.
- File > Open a folder of loose files reads an unpacked game or a mod folder instead.

## The Anno 2205 page

The second page, **Anno 2205**, is the Flash UI editor: Anno 2205's interface movies (`data/ui/*.feu`) listed, opened as
a tree of their elements with a preview, texts, pictures, icons, fonts and positions edited, the edit file saved, a mod
built or installed. It opens the 2205 folder of the suite's settings and keeps its edit file in the open project
(`ui-edits/anno2205.json`); the project's build adds the files it changes. Its window, commands (`python -m annotoolbox.flashui`),
edit file and build are in [`ui/2205/flash-ui-editor.md`](ui/2205/flash-ui-editor.md); the format is in
[`formats/anno2205-ui-format.md`](formats/anno2205-ui-format.md). The two pages share nothing but the project and the
app's place in the switcher: each editor is as described in its own guide.

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

**Masks and effects** (the game's UI shader, `rendering/ui-117.md`): a mask covers the siblings that receive it and
are drawn after it, with their whole subtrees (a run of masks is moved in front of the siblings it covers, and `ZIndex`
sorts from the first child that has one: `ui/117/layout.md` "Draw order", `layout.draw_order`; a mask must be a picture
or a shape, and takes no input in the preview's picking); a hard mask clips to its
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
of the game stands in. A label wraps at its text plane's width (a word wider than the plane inside the word), cuts by
character or with an ellipsis as its overflow modes say, and keeps the whole lines that fit vertically (with an
ellipsis in that mode). Its first line is as high as the font, every further line adds the style's leading times its
size; a 117 label's line spacing and the letter spacings change nothing, as in the game (an older game's label
keeps its line spacing as a factor: nothing was measured there). Outlines are drawn under the
glyphs and shadows below them. Its size follows its sizing mode (the plane, the shown text, the larger or the
smaller), and a box of the text's size starts where the text is aligned to. All of it is the game's own behaviour
(2026-11-17: `ui/117/layout.md`, "Fonts, as the game measures them").

**Fonts** (2026-11-17; the same on every page of the app and in the Texts app). Every font is read from the install at run
time and registered with Qt for the session (`suite/gamefonts.py`: once per file content, never copied into a project,
the repository or the tests); where one cannot be read the page draws a system font and says why (the Checks list on
the 117 and 1800 pages, a tooltip on the preview of the 2205 and old-interface pages).

| Game | The fonts | How the UI names one | Draws in |
|---|---|---|---|
| 117 | `data/fonts/`: LisboaSans, AlbertusNova, a hieroglyph font, Roboto, DF Chinese and Japanese files, Noto KR and TC, Meta, Salford, Heuristica and Kelvin in four weights each | a label's `TextStyle` -> `LocalizedFont` -> the font asset's `FontId` file, a language's exception and fallbacks | the game's file; all 1,028 labels of the shipped widget files with a style resolve to Lisboa Sans Pro, Albertus Nova or the hieroglyph font |
| 1800 | `data/fonts/`: Meta Offc Pro, Meta Serif Offc Pro, Roboto, Heuristica, Kelvin, the Asian files | the same chain (four of its localized fonts have a script of their own, read by pattern) | the game's file; all 4,717 labels resolve to Meta Offc Pro or Meta Serif Offc Pro |
| 2205 | `data/ui/*.ffd` glyph pictures, plus Roboto Light and Regular as TrueType in `data/ui/chromium/` | a movie's font reference names the `.ffd` and a family; no movie holds `DefineFont` | Roboto Light and Regular in the shipped TrueType files (618 of the 733 text fields); Thin and Medium in the nearest weight; Arial and the Chinese font in system fonts ("Font descriptors" in [`formats/anno2205-ui-format.md`](formats/anno2205-ui-format.md)) |
| 2070 | `data/loca/<language>/fonts/`: DejaVu Sans Condensed and MagistralTT Bold | the font table of `properties.xml`, a widget's `FONT` | the game's file (396 of the 406 widgets that name a font); the console font is a Windows family |
| 1404 | `data/loca/eng/fonts/georgia.ttf` | the `<Font>` entries of `localisation.xml`: a name, a family (Times New Roman, Arial, Courier New), a size and a weight | the table's Windows family from the system; the shipped Georgia is the `GameFont` entry, and which texts it draws is not known |
| 1701 | `georgia.ttf` the same; `data/ui/studio/fonts/` holds more files (Meta, Chinese and Japanese), none of them named by its font table | the `<Font>` entries of `localisation.xml` (per resolution: `Name_1024`, `Height_1024`; the file is not well-formed XML) | the table's Windows family (Times, Arial, Courier New) from the system |

117's text styles carry `Bold` and `Italic` flags (35 shipped styles: none bold, two italic, both on Lisboa Sans, which has no
italic file), so the weights are Qt's: italic is slanted by Qt, bold would be emboldened. Heuristica and Kelvin ship
bold and italic files that no text style names. The 2070 table's weights (500, 600, 700) are applied as Qt weights.
The Texts app shows a text's source and target boxes in the game's font of the picked text style (the style of the
labels that show the text, else the most used one; for the games without text styles their default font: Roboto
Regular, DejaVu Sans Condensed, Georgia).

**Group rules and loaders** (inferred from the editor's descriptions, docs/ui/117/layout.md): a stack puts its
children one after another and aligns each across its axis (the group's `ChildrenAlignment`: begin, middle, end;
a child's `AlignmentUtility` overrides it); a background layout stretches the group's first layer round the others
plus its margin; an enum loader places the children whose `EnumEntry` is its current value (and those with none), a
game object loader the child made for its object's data class where the game's data says the class (the options
scene's page: `ui/117/game-data.md`, "Object loaders"), else its first; a radial list or wheel puts its items on an arc.
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
listed in Checks; a picture the game names at run time with no sample value shows a real picture of the game
(`Pictures.sample`): the portrait or emblem of the character its slot names (below, "What the preview assumes about
the player"), else an icon whose file name shares the longest word with the slot, taken from the folders the game
draws for a widget of that name (seen in the game, 2026-11-16: an `Icon` from the generic and attribute icons, a
`2DHolder` from the buildings and goods, a `Portrait` from the portraits), else one picked by the slot's name, always
the same. A slot the binding does not name takes the name of the image it fills. Only a game with no icon files gets
the light placeholder tile with the slot's name, and Checks lists the slot ("no icon in the game's files").

**Wrong pictures fixed on 2026-11-17** (the owner's report: "the wrong icons"): a slot named only `picture` drew any
icon of the game, the hippodrome's phase icons in the HUD among them (now the image's own name and the game's folders
for it); the governors' bar drew the Egyptian portrait frame because its `PlayerRegionInfo` stores Egyptian (now the
player's region, Latium's mosaic as in the game); every lock icon showed, since a lock flag was made up as `true` (now
all unlocked by default). The fhd and 4k names resolve as the game's: 117 ships one picture set (`data/ui/4k/`,
`data/ui/2kimages/`, `data/ui/4kimages/`; no `data/ui/fhd/` file), every `fhd` name found under `4k`. Atlas entries
were not checked.

What differs from the game (open, `tool-suite.md` section 20):

- **Text**: a label's box and its text's place in the plane follow the game's own behaviour (sizing 0 the plane, 1 the shown
  text, 2 the larger, 3 the smaller; `AlignmentY` top, centre, baseline, bottom); in the design view a plane of 0 (set
  at run time) takes the shown text's size; the box always starts at the label's origin; justify is drawn left; the
  font data files' own outlines are not read (the font files are the same faces).
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
a real icon of the game for a picture named at run time (a light tile named after its slot where the game has no icon files), three elements for a list. Each binding
gets the placeholder of the kind it needs, also when another binding reads the same game value as another kind (a
label's text and a picture's visibility from one value: a text for the one, `true` for the other), so what the
preview shows does not depend on which part of the scene was placed first. The panel lists the value the first one
made. Write a value to keep your own; right-click > Reset to a placeholder drops it again. With the fill
off the scene shows only the values set by hand.

**The typical state** (2026-11-17): the placeholders make one coherent state of the scene, by evaluating the bindings
with them, never by the widgets' names. A flag takes the state its name says (`state_for`): a lock off (unless the
preview's settings lock it), an error, a warning, an invalid or empty value, an edit mode off, their opposites on;
where the flag's own name says nothing, the name of what it feeds says it, through the operator (a flag inverted into
`IsUnlocked` is false), and then the other bindings of the file that read the same game value (one value, one
state), and last the flag the target stores (a layer the file keeps hidden, such as a drag hint, stays hidden). An
enum loader given a value none of its children has shows none of them, as the game (a lock loader set to "unlocked"),
and the first entry only while the value is unknown. A component's property that the widget does not store reads the
default its component file declares (a UI scale of 1, no gamepad), and what a placing file writes into a field of a
placed file's object (a text file's text plane, alignment and sizing) reaches that file. A label's alignment, sizing
and overflow follow what its bindings write. One entry of a group is selected, the first (a list's first element, a
menu's first access button: the options scene's first tab, the construction menu's buildings), and a two-way binding
between a widget's state and the game's data starts from the data. A game object is one object in every file that
holds it: a field any of them writes reaches the others, down, up and between sibling files (an options tab's text
width, an object menu's background height); a label's shown text and its size are read as laid out; a list's `ValueCompare` gives every item the smallest and largest value
of all items (the widest tab); an enum the game fills reads its first entry (a popup closed); an intro animation has
run (a panel that slides in stands at its end, an animation the scene's show event or opening starts where it fires),
and a page an animation fades in counts as shown (2026-11-17,
`ui/117/layout.md` "Values the game gives at run time"). Values held from a running game win over
all of it.

## What the preview assumes about the player

Where a scene shows the player's game, the preview assumes Anno 117's campaign in its late game: Marcia as the player,
everything unlocked and researched, mouse and keyboard, every DLC of the install, single player in Latium. The values
are `uiedit/previewdefaults.py` (`Defaults`); what each setting can take is read from the game's assets
(`uiedit/previewcatalogue.py`). **Live values and recordings win over all of them**, a sample value set by hand too.

How a value reaches the preview:

- **Components the game fills from the player's state** get the player's value instead of the one stored in the file:
  `PlayerRegionInfo.CurrentRegion` the region (Latium: `halo.Province` Roman), `Platform.IsGamepadModeActive` and
  `Platform` the input (mouse: false, PC), `PortraitResolver.PortraitImage` the portrait of its `PortraitGuid`, else the
  player's (the UI reference, its components page).
- **Placeholders by name**: a made-up value whose source or target names a value of the player's game takes it: a
  character's name (`PlayerName`, `EmperorName`, `RivalName`), money, a stock, the emperor's reputation, a gamepad,
  multiplayer or DLC flag; a run-time picture whose slot names a character its portrait or emblem (`PlayerPortrait`,
  `EmperorPortrait`, a bare `Portrait` or `Portrait_Encounter` a rival's, as the game's governors' bar and diplomacy
  show other participants there, `logo` the player's emblem). `2dPortrait` is an icon, not a face.
- **Locks**: a lock flag is off while everything is unlocked. A lock flag under a game object shows when an asset GUID
  given under that object (a sample value) is in the locked list; below the late game (campaign start, a tier) a lock of
  an asset the preview does not know shows.

| Setting | Default | Source |
|---|---|---|
| Player | Marcia (participant 41 `Profile_Human_00`): portrait 38962 `portrait_marcia.png`, emblem 125990 (Secretive Sphinx), colour 91304 Lapis Lazuli (38, 97, 156), no title | the Portrait and PlayerLogo assets; the colour's `ParticipantUIColor`; confirmed by a campaign save's slot 0 (name Marcia, colour, logo, portrait, start session 3245) |
| Banner | logo colour 91307 Celtic Green, shape 92736, pattern 92738, background 92740, stripes 92742 | the campaign save's slot 0 |
| Emperor | Emperor Lucius Augustus (31721), portrait 33070, emblem 77590, colour 91314 Falernian Red; reputation 0 (Casual), Peace | the campaign's start emperor, act 1 (`setup.SetupStartEmperor`, as the owner's Marcia save has him); the owner's choice 2026-11-17. Gnaeus Firmius Calidus (31637) reigns from act 2 on and in the endless game, Julia Augusta (31722) is the third; all three stay in the catalogue's emperor list |
| Rivals | Tarragon (31100, Latium), Athr Iorgwyn (31102, Albion), Concordia (31104, Latium): portraits, `CompanyIcon` emblems and `DefaultColor`s of their participant assets; Peace [I] | the campaign's quests name them (act 2's Athr side quest, act 3's Saturnalia with Tarragon and Concordia) |
| Pirates | Caeso Regulus Syracus (37495, Latium), Voada of the Scathach (37494, Albion), Kandis of Kashta (153069, the Nile region: portrait 153091, emblem 153101, colour 158239; Aegyptus DLC); War [I]. A scene in a region first shows that region's pirate | `Difficulty Preset Campaign` (41206) lists Caeso and Voada, Kandis is the Aegyptus DLC's pirate (the owner, 2026-11-17); absent from the catalogue's pirate list when the DLC is not installed |
| Progression | late game, all unlocked; every tier reached with late-game residents [I] (Latium 1,500 / 1,200 / 800 / 400 ...) | the owner; the tiers are the `PopulationGroup7` levels |
| Research | all researched, a repeatable node once | the owner |
| Input | mouse and keyboard | the owner |
| DLCs | every DLC the install has | the owner; the list is the `DlcController`'s |
| Session | single player; the campaign's setup: `Difficulty Preset Campaign`, map type 43182, seed 211, start in Latium (3245), each `Setup...` option as the campaign save holds it (taxes Spare, ...) | the owner; the save's `header.a7s` |
| Region | Latium | the campaign's start province [I] |
| Season and weather | the game's default | [I] |
| Island | a large one of the player's | [I] |
| Object of an object menu | Patrician Residence (3145), Jeweller (3192) | [I] |
| Money and goods | 250,000 and 500 of each good | [I] |

**The store** (`previewdefaults.Store`, one per open game, `window.preview_store`; nothing of a user's save is
needed or read): the user's changes are kept per game in the Toolbox settings (`apps["ui"]["preview"][<game>]`), or
in the project (`ui-edits/<game>-preview.json`, `keep_in_project(True)`), which then wins. Only what differs from the
campaign is kept.

- **Read**: `previewdefaults.current(ctx)` gives the `Defaults` of a preview (`layout.Context.defaults`); on it
  `player()`, `cast(role)` (player, emperor, rival, pirate), `option("setup.<Tag>" | "banner.<Part>" | "title.<...>")`,
  `region_name()`, `reached()` (tiers and residents), `is_locked(guid)`, `researched(node, tier)`, `portrait_of(guid)`;
  `placeholders.is_locked(ctx, key)` is the lock rule.
- **Write**: `Store.set(name, value, play=False)` with a field of `Defaults` (`input`, `region`, `progression`,
  `locked`, `tiers`, `characters`, `player_name` ...) or `options.<name>`; `play=True` marks a choice made inside a
  scene, which `reset(played=True)` undoes. `reset(name)` puts one value back, `reset()` all of them.
- **Change signal**: every change calls `Store.listeners` with the new `Defaults`; the window's listener gives them to
  the preview, drops the placeholders made under the old ones and lays the preview out again.
- **Choices**: `previewcatalogue.read(game)` (an opened asset game, off the window's thread; the window keeps it as
  `preview_catalogue`) gives the characters by role, the profile customization's portraits (20), emblems (45, the game
  shows 41 of them), colours (12), banner parts and title words, the provinces, the tiers by region, the DLCs, the
  assets locked at the start (`DefaultLockedState`: 47), the research nodes by category with their row (211, 14
  repeatable) and every `Setup...` option with its values.

What the data does not say: which building a tier unlocks (a `FeatureUnlock` names a population count, but nothing
names the `FeatureUnlock`), so a tier preset sets the tiers and leaves the locked list to the user.

## Game data in the preview

**View > Fill from the game's data** (on by default, the choice is kept): where a scene shows what the game fills at
run time, the preview takes it from the installed game instead of a placeholder. The quick tools wheel shows the eight
tools and buildings pinned for the player's region, with their icons; the religion scene the patrons (Ceres, Neptune,
Mars ...) with their icons, artwork, devotion effects and the "Exalted Patron" texts; the options scene one tab per
options page and the entries of the open page; the construction menu the buildings of the first category; an object
menu the name and icon of the residence or production building the Preview settings name. A list shows as many
entries as the data holds, up to what the list shows.

- **Which value wins**: live values and recordings, then a value set by hand (or the file's own preview data), then
  the game's data, then a placeholder.
- **Where a value came from**: Properties lists, on top, what the selected element is given by the open file's
  bindings and where each value came from: live from the game, recorded, the game's data (the asset and its field),
  a sample value or a placeholder.
- **How it works**: the preview follows a binding's path of fields from the scene's data object, names the fields
  (by the game's field table in the index folder, where there is one, and by the names given in View models), and reads
  the asset that path stands for: the scene's own config asset (`QuickToolsScene`, `ReligionScene` ...), the element of
  a list, the object an object menu shows. A run-time value (money, a patron's belief) comes from the Preview settings
  or from a typical value of the game's data. The rules per scene and what is left out are in
  `ui/117/game-data.md`.
- It reads the game's asset index (the asset editor's, built once and kept in the workspace) off the window's thread
  when a game opens; the preview lays out again once it is there. A scene with game data places more of its parts
  apart (a quick tools wheel about 5 s, the options scene about 40 s, the first time).

## The language of the game's texts

The **Preview settings** panel's **Game language** picks the language of the game texts the preview shows: the
twelve languages 117 ships (English, German, French, Italian, Spanish, Polish, Russian, Brazilian, Japanese, Korean,
Simplified and Traditional Chinese). It is kept per game with the other preview settings. The default is English (the
game keeps the player's own choice with the account, which the Toolbox does not read). It is not the Toolbox's own
interface language.

- Every text the preview takes from the game follows it: a label's text id, the game's data (asset names,
  descriptions, effect texts), the hints a scene names.
- **Fonts**: a label draws with its localized font's face for the language, as the game does: Chinese, Japanese and
  Korean in their own faces (no empty boxes), the Latin and Cyrillic languages in the default face. No shipped language
  is written right to left.
- Wrapping, widths and cut-offs follow the label rules, so a longer language wraps where the game would.
- Live values and recordings keep the language the game had when they were taken; where a recording carries its
  language and it is not the one shown, the status bar's note says so ("live: German"). A recording carries it
  (`language`) when the game's answer names one; today's link helper names none, so the note shows for a recording
  that was given one by hand or by a later helper (fixed 2026-11-18: the field was read from nowhere before).

## Play mode

The **Design** / **Play** switch sits in the toolbar and the View menu (F5 plays, Shift+F5 goes back to Design). In
Play mode the preview behaves as the game would, and nothing selects, moves or edits the file:

- **Pointing** at a widget gives it the hover the game gives it (a mouse handler's `IsHover` and `OnMouseEnter`, a
  button's `OnMouseEnter`); **pressing** adds the press, **releasing** over it the release. What the scene's bindings
  start with them shows: a hover animation plays over time from the moment of the change (in the nested files too),
  a flag, a toggle.
- **A click** selects a tab (its bar's `SelectedTabID`; the pages bound to it switch), flips a checkbox, steps a toggle
  over its entries (`SwitchChoice`: the left half back, the right half on; the entries from its data, else its field's
  enum), opens a combo box (its list shows under it, its entries from the game's data where the resolver has them; a
  click on one picks it and closes the list), and picks a radial wheel's sector. A **drag** moves a slider in its
  steps, a scroll bar's handle (the list bound to its `ScrollInfo` scrolls with it) and a draggable button (within its
  range; `DragStarted` while dragging, `DragStopped` at the end). The **wheel** scrolls a list (a grid by a row);
  Ctrl+wheel still zooms, the middle button or Alt still pans.
- **Resting** on a widget with an info tip shows the tip's text: its text line, or the name and description of the
  asset it is about from the game's data.
- **A button that opens another screen**: where its release goes into a field named `Open<Name>`, the scene
  `<name>scene` opens in the editor (still in Play mode); otherwise the status bar names the field the click fires,
  or says that the screen it opens is not a scene file.
- **The scene's start**: the open file's animation that its `AddedToScene` event starts plays once and holds its end.
- **The keyboard**: Tab and Shift+Tab move the focus over the widgets that take a press (outlined in green), in
  drawing order; the arrow keys move it to the neighbour the widget names (`NeighborUp` and the others), else to the
  nearest widget that way, as a gamepad does. Enter or Space presses; Left and Right move a focused slider and step a
  focused toggle.

**Choices the game keeps.** Where a control's value goes back into the game's data through a two-way binding (an
option, a difficulty, the profile's customization, a key binding, a tab the game remembers), Play mode writes it into
the Preview settings (`Store.set(..., play=True)`), named by the field's path of names (`play.optionsscene.GameContext
...`), never by the widget. Every open preview and the Preview settings then show the new value, as the game would
after the change; the Preview settings list them under the tabs. **Reset Play mode choices** (View menu, or the button
in the Preview settings) clears them and what the session set. A value no binding writes back (a tab of a page, a
slider of the preview only) stays for the session. Design mode never writes to the store.

**Smooth.** Input changes Play mode's values on the window's thread; the layout runs off it, one at a time, the
newest values winning and the last finished frame shown meanwhile. A change places again only the lowest placing it
reaches, at its place, and puts its items into the shown frame; the file around it is placed again only where the
placing's size or outputs changed (game-data.md, "Speed"). A choice drops only what read that field and places the
file once. Measured 2026-11-17 with the game's data, the same steps in each run (a tab hovered, clicked, left, a
second tab hovered and clicked, the first hovered again), while other agents loaded the machine (a whole Design
layout of `optionsscene` took 29 s in the quietest run, 60 s in the busiest):

| Step | `optionsscene` before (round 1) | now, quiet | now, busy | `quicktoolsscene` now |
|---|---|---|---|---|
| first hover | 6.0 s | 0.54-0.63 s | 1.5 s | 0.18-0.22 s |
| click | 5.1 s | 1.8-3.0 s | 3.7 s | no change (the hover set it) |
| leave | 0.6 s | 0.10-0.37 s | 0.7 s | 7-10 ms |
| next hover | | 0.12-0.41 s | 1.1 s | 0.09-0.11 s |
| next click | 0.85 s | 0.20-0.58 s | 1.2 s | |
| hover again | | 0.11-0.28 s | 0.57 s | 10-12 ms |

Round 4 (2026-11-17, a loaded machine): a hover 0.30-0.43 s, leave and hover again under 10 ms, a tab click 3.8 s
with the page it opens (the click now selects the tab and switches the page: `ui/117/layout.md`,
"Values the game gives at run time"; the Design layout's times are there under "Open").
The rest of a hover is the bindings of the files on the way evaluated with the new values (a tab's file and its
button's, each run again for every step of its size bindings); a click on an options tab widens the tab bar, so the
bar's file is placed again too. A tab's selected look in the options scene: game-data.md, "Checked against the
game's dumps". The design of each rule, and what is not built, is in `ui/117/game-data.md`,
"Play mode". The code is `uiedit/playmode.py` (the switch and the window's side), `playinput.py` (what each input does)
and `playstate.py` (the values in the layout, the placing again).

### Preview settings

View > Preview settings opens a dock that shows and changes everything above, on top of the store (no second store:
every control writes through `Store.set`, and the preview is laid out again at once). The choices are read from the
game's assets off the window's thread; until they are there the panel says so and offers the campaign's own. A small
reset button beside each value puts that value back to the campaign's (it is off while the value is not changed). Every
control has a tool tip and the labels have access keys no two of which clash (the tabs and the buttons below take
theirs first). The tabs:

- **Player**: name, portrait, emblem and colour as grids of the game's own pictures (like the profile customization),
  the banner's parts (logo colour, shape, pattern, background, stripes) and the title's prefix and suffix. The
  emblems are white pictures, so their grid has a dark ground.
- **Characters**: the emperor (one reigns), the rivals and the pirates as lists with portraits and names; the ones
  checked take part. With none changed the campaign's cast stays, a pirate of the region first.
- **Session**: the difficulty preset, the start province, the seed and every `Setup...` option with its values (a
  search line filters them); "Campaign setup" puts all of them back.
- **Progression**: the preset (everything unlocked, the campaign's start, up to a tier), the tiers reached per region
  with their residents (a tier set by hand wins over the preset; none checked: the preset decides), and a searchable
  checklist of the assets locked at the start, grouped by their template (the data does not say which menu shows
  them), with "Lock the shown" and "Unlock the shown" for the search's result.
- **Research**: the research tree by category, each node researched or not, a repeatable node with a count; the
  presets everything (the default, a repeatable node once), nothing, and up to a row. A preset drops the nodes set by
  hand; only what differs from the preset is kept (`options.research.<node>`).
- **Platform**: the input prompts (mouse and keyboard, Xbox, PlayStation), single or multiplayer and the owned DLCs.
- **Values**: season, island, the object of the residence and production menus, money, the stock of each good, and the
  game's language.

Below the tabs: **Keep these settings in the project** (`keep_in_project`; off when there is no project), **Reset to
the campaign defaults** and **Reset Play mode choices** (`reset(played=True)`). A note shows while live values or a
recording are shown for the open file, since they win over these settings. The code is `uiedit/previewpanel.py` (the
panel), `previewsections.py`, `previewsetup.py` and `previewprogress.py` (the tabs) and `previewwidgets.py` (the picture
grids); `Store.set_many` keeps several values and tells once (a preset that sets a node each).

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

**Live reload of a scene.** With the live link connected ([`live-link.md`](live-link.md) section 10.1), saving a scene
or widget file of the project announces it to the running game (`reload.scenes`): the screens that show the file are
left and entered again, so the edit shows without a restart.

## Live values

With the live link connected and the game showing the open file, **File > Take the live values from the running
game** asks the game what the file's widgets show now ([`live-link.md`](live-link.md) section 17): of a scene file its
own widgets, of a widget file the widgets of its first instance in a scene the game has open. The preview then
shows, matched by the instance's GUID or its name:

- a label's text in place of its sample text;
- an element hidden or shown as the game has it;
- an image's picture as the game names it (an icon, a portrait), read from the installed game;
- a placed file's own property values in place of the values its instance stores.

The game sends only these values; the sample values stay as they are, and a widget the game does not answer keeps
them. **View > Show the game's values in place of sample values** (on by default) switches them off and on. The
status bar names where the values came from: the game build and the time.

**Recorded for later.** Every answer taken is recorded on this computer, per game and per file (`ui-values/` in the
workspace, nothing in the project or the mod). Taking the values again replaces the recording. With no game linked,
opening the file fills the preview from its recording; **File > Use the recorded values** reads it again. When the
file changed since, the status bar says how many recorded widgets no longer match an element (stale): those keep
their sample values, the rest still fill. **File > Clear the recorded values of this file** and **Clear all recorded
values...** delete them; the recordings stay until then, across restarts.

**What the game assigned.** **File > List what the game assigned...** lists the assets, icons and portraits the held
values name: a property value that is an asset's GUID with the asset's name and icon from the installed game (the
asset editor's index, built once), and each picture the widgets draw with the file it is in the install. A picture
with no file in the install is marked so: one the game makes at run time (an id such as `rtt_166413`, a render
target or a composed portrait) or an id the editor does not resolve; the preview draws a stand-in for it. No picture
is stored: everything is read from the install when it is drawn.

Note: built on 2026-11-17, not tried with a game.

## The game mirror

**View > Game mirror...** opens a window of its own that shows what the running game shows: every UI scene the game
has open (the HUD's parts, an object menu, the construction menu, a popup), stacked as the game stacks them, each
filled with the game's live values. It follows the game through the live link ([`live-link.md`](live-link.md)
section 18): when the game opens a scene it appears in the mirror, when the game closes it, it goes. The editor's
own window stays as it is: following never changes the file open in the editor.

- **The scenes.** Each scene is laid out by the preview's own code at the game window's shape (the size the game
  reports) and drawn into a picture of its own, off the window's thread; the mirror puts the pictures over each
  other, the scene the game opened last on top. Only a scene whose values or file changed is laid out again.
- **The list of open scenes** beside the picture names each scene with its layer (1 is the bottom) and its parent
  state. Untick a scene to hide it for a look at what lies under it (the game is not told). Double-click a scene in
  the list, or click it in the picture (the topmost scene that draws something there), to open its file in the editor.
- **Live values.** While it follows, the mirror asks the game for the values of the scenes it shows, one request at a
  time and at most four a second, slower while nothing changes. Each answer whose values changed is recorded as
  **Take the live values** records it, so the editor's preview of that scene shows the same values afterwards.
- **Size**: the game's resolution, or scaled to fit the mirror's window (the default).
- **Pause** stops following: the mirror keeps the last picture and asks the game nothing (no subscription, no
  request). **Resume** asks the game for every open scene again.
- **The status line** says `following the game: <scene>` with the scene opened last, `paused`, or why it cannot
  follow (no game linked, a game that does not send scene events).

**Push edits to the game** (an option of the mirror, off by default) closes the loop of live editing:

1. edit a scene or a widget file in the editor that a scene the game has open shows;
2. after a moment with no further edit (1.5 seconds), or at once with **Push now**, the file is kept in the project
   as **Save** keeps it and written into the installed copy of the project's mod, and the game is asked to reload the
   scenes that show it (`reload.scenes`);
3. the game reloads the scene;
4. the mirror takes that scene's values again and shows the result, in the game's layering.

A push writes only into the project and into the project's own mod folder in the mods folder (and only when that
folder holds the project's mod: install the project once first). A scene the game does not have open is not pushed.
A refused or failed reload (the game is busy, the scene is not open, a widget file is missing) is said on the status
line, and the mirror shows the game's scene as it is. A reload of a scene the player is using resets its state (the
scroll, a typed text).

Note: built on 2026-11-17, not tried with a game.

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
9. View > Show the 3D scene behind the UI, then View > Draw the scene behind the UI from a model of the game... and
    pick a residence; open `statisticsscene` or `blurscene` and compare the blurred glass with the game's over a city.
10. View > Draw the scene behind the UI from an island of the game... with a pool island; compare the blur and the
    darken over it with the game's over the same island.
11. Open `statisticsscene` (its files nest 24 deep) and a scene with a list fed by its file's preview values; compare
    the list's size and the deepest parts with the game's screen.
12. With the link helper and a session running, open the HUD's scene and take the live values: the game's texts and
    icons show in the preview. Close the game, open the file again: the recorded values fill it, and the status bar
    names the build and the time.

## Language

The UI editor's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
