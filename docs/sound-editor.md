# The sound editor: user's guide

The suite's **Sound** app opens a Wwise bank of Anno 117, and of Anno 1800 and Anno 2205 where the layout allows. It
lists the bank's events, containers, sounds and music. It plays a sound, changes how it plays, replaces its audio from
a WAV or an Ogg file, and adds sounds and events. It keeps the result in the project as the files of a mod. The design
is in `tool-suite.md` section 24, and the formats are in
`sound/bank-objects.md`.

The app is tested on synthetic banks of the three layouts.
It is also tested read-only on the banks of the three installs: every bank is written back byte for byte, and edits
are made and read back.
**Nothing was heard in a running game yet.**
A test proves the file format, not that the engine plays it.

## Using it

1. Choose **File > Open game** and pick a game. Its banks are listed on the left (filter by words of the path).
   Double-click a bank. A bank's info file (`<bank>.json` beside it) gives the events' names.
2. The middle tree has three lists: **Events**, **Sounds and containers** (the hierarchy: containers with their
   children) and **Music** (segments, tracks, switch and playlist containers). Pick an object, and the top right shows
   what it is (kind, parent, children, codec, media, its properties).
3. Edit with the forms on the right (each is one step on the undo stack, Ctrl+Z / Ctrl+Y):
   - **Apply the settings**: volume (dB), pitch (cents) and the random pitch range of the picked Sound or container.
     Only the boxes you changed are written.
     A value the box clamps or rounds stays as the file has it.
   - **Apply the playlist**: a Random/Sequence container's playlist as `child:weight, child:weight` (a child may stand
     twice; the order is the order of a sequence).
   - **Replace the audio...**: a Sound (or a music track) plays another clip. Pick a WAV, Ogg, MP3 or FLAC file and, in
     "encode a clip as", the codec.
   - **Add a sound here...**: a new Sound under the picked container (or next to the picked Sound), with a copy of a
     neighbour's settings and the clip you pick. Under a switch container it joins the first value's branch.
   - **Add an event that plays this...**: a new Event (id = the hash of the name you type, which is how the game posts
     it by name) with its Play action.
   - **Play** plays the picked Sound's clip (Wwise PCM, ADPCM and Vorbis through Qt's player).
4. **File > Save to the project** writes the changed bank and the loose clips under `sound-edits/<game>/` in the
   project (with the game's own paths) and tells the game link. The project's build puts them into the mod, and
   **Install** copies the mod. The status line says what the game needs (a restart for banks, `docs/live-link.md`).

Programmatic use: `BankDoc(bytes).do(name, **args)` with the edits of `soundedit/edits.py` and `audioedits.py`
(`set_props`, `set_weight`, `set_playlist`, `set_ranseq`, `add_child`, `remove_child`, `set_switch`, `set_branch`,
`add_event`, `add_play`, `set_event_actions`, `set_action`, `set_music_node`, `replace_audio`, `add_sound`,
`replace_track_audio`) and those of `musicedits` (`set_markers`, `set_marker`, `add_marker`, `remove_marker`,
`set_transition`, `add_transition`, `remove_transition`), `curveedits` (`set_rtpc`, `add_rtpc`, `remove_rtpc`,
`set_layer`, `set_layer_rtpc`, `set_layer_curve`, `remove_layer_curve`, `set_attenuation_curve`,
`add_attenuation_curve`, `remove_attenuation_curve`, `set_attenuation_slot`, `set_attenuation_rtpc`) and `groupedits`
(`add_state_group`, `set_state_group`, `remove_state_group`, `add_switch_group`, `remove_switch_group`); `undo`,
`redo`, `to_bytes`. Each edit has a label (`registry.label(name)`, `change the settings` for `set_props`) that the
window's status line shows after it. An edit that cannot be made raises `CEditError` with the sentence that says why.

## What can be edited

| | Anno 117 (bank 172) | Anno 1800 (132 to 135) | Anno 2205 (112) |
|---|---|---|---|
| Volume, pitch, random pitch of a Sound or container | yes | yes | yes |
| Random/Sequence playlist (weights, order, repeats), add / remove a child | yes | yes where the tail reads exactly (about 99.8 % of containers) | the same (about 98.5 %) |
| Loop count, avoid-repeat, sequence / shuffle of a Random/Sequence container | yes | no (the block before the child list is not decoded) | no |
| Switch container: group, start value, the nodes of a value | yes | yes | yes |
| Set-switch and set-state actions: group and value | yes | yes | yes |
| New Event with a Play action, more Play actions on an event | yes | yes, from a Play action the bank has | the same |
| Replace a Sound's audio, add a Sound | yes | yes (audio in the bank) | the bank keeps the clip outside: written loose and streamed |
| Music: a playlist entry's weight, loops, repeat; a track's clip; segment markers; transition rules | yes | no | no |
| Attenuation curves (points, scaling, which use takes which curve; a curve added or removed) | yes | yes | yes |
| Layer container: the game parameter of a layer, a layer's curves and RTPC entries | yes | yes | yes |
| RTPC entries of a Sound's or container's node base (curve, game parameter, driven property; added, removed) | yes | yes | yes |
| State groups and switch groups driven by a game parameter, in the Init bank | yes | yes | yes |
| Wwise Vorbis clips (ffmpeg with libvorbis, the codebook file) | yes | yes | yes |

A container cannot be put under itself or under one of its own children (the bank would loop).
The last child of a container can be taken out and others added back in every version.
A Random/Sequence container's emptied list ends in six zero bytes and an Actor-Mixer's in four.
That is how the older banks find it again (`sound/bank-objects.md`).
A failed edit of any kind leaves the bank as it was.

An edit that the layout of the container does not allow says so and leaves the bank as it was ("the playlist does not
end the container", "the child list was not found"). It never guesses.
These are not edited at all: buses, the states of a node, the positioning, effects, an attenuation's cone, the music
switch's decision tree, a layer container's children, layers added or removed, and a transition rule's transition
object.
A bank in which nothing was changed is written back **byte for byte**.

## Audio

A clip is read from a 16-bit PCM WAV (also 8, 24, 32 bit and float WAVs; mono or stereo) directly. Ogg, MP3 and FLAC
go through ffmpeg when it is installed, and through miniaudio otherwise (stereo, 48 kHz). The Sound keeps the way it
stores its audio:

| | |
|---|---|
| Audio in the bank (stream type 0, Anno 117 and 1800) | the new `.wem` replaces the bank's media file (the bank's media are rebuilt, 16-byte aligned) |
| Streamed or prefetched sound (117's music and voices; all of 2205's) | the bank's start of the clip is dropped, the whole `.wem` is written loose beside the banks (`media/<id>.wem` for 117, `<id>.wem` for 1800 and 2205; `media/<language>/<id>.wem` when the sound is language-specific) and the source is marked streamed, with the first 3000 bytes as its size in 117, as the music mod does |
| A media file two Sounds share | the Sound you change gets a media id of its own |
| A silence source | refused: add a Sound instead |

| Codec | Written for | Needs | State |
|---|---|---|---|
| PCM | all three | nothing | PCM in the music mod plays in 117 (verified in game); not tried in 1800 or 2205 |
| Wwise ADPCM (IMA, 36 bytes a channel and block, 64 samples) | all three | nothing | the encoder is the inverse of the decoder the viewer plays 2205's clips with (round trip tested); **not heard in a game** |
| Wwise Vorbis | all three | ffmpeg with libvorbis and `packed_codebooks_aoTuV_603.bin` in `tools/` (the codebook file ships with the installed program) | the writer is `formats/wwvorbis`, the writer of the converted building sounds; 1800's and 2205's Vorbis files are the same layout with 2205's own channel word (the file header written is checked against the games' own files; `sound/bank-objects.md` "Wwise Vorbis files in 1800 and 2205"); **not heard in 1800 or 2205**. ffmpeg is never shipped |

Memory: PCM is large (a 10 s stereo clip is 1.9 MB), ADPCM is a quarter of that, and Vorbis a tenth.
117's sound effects and the music are Vorbis, so put a long clip in as Vorbis where you can.

## Music (Anno 117's banks)

- **A track's new clip** (Replace the audio) sets its length in the track's playlist items and fits the segment to it:
  the trims and the start of the clip, the segment's duration and its markers move with the clip. "Markers follow a new
  clip by" picks how:
  - **scale** (default) moves everything by the ratio of the new length to the old one. A 12 s clip replaced by a 6 s
    one halves the trims, the duration and every marker.
  - **keep** leaves the trims and every marker where it was in time. It moves the exit cue (the marker at the
    segment's end) to the new end, and pulls a marker that would lie beyond it back to it. It is refused when the trims
    would cut away the whole new clip.

  A segment that is not this track's length (one with several tracks of different lengths) keeps its length and
  markers; the status line says so.
- **Markers**: pick a segment and edit "markers (ms)" as `id:ms` (a custom one as `id:ms:name`, or `name:ms`, whose id
  becomes the hash Wwise gives that name), then choose Apply the markers. The entry and exit cues stay, every position
  must lie in 0 to the segment's duration, and ids are unique. The picked track's waveform draws its segment's markers
  as red lines.
- **Transitions**: pick a Music Playlist or Music Switch container, and its rules are listed in the facts. The rule
  number and "fields of the rule" (`sync_type=2, src_fade_time=3000, src=30;31`) change one rule. **Add a rule
  (copy)** makes a new one from the picked rule with the fields changed, and **Remove the rule** deletes it. The fields
  are the source side (`src_fade_time`, `src_fade_curve`, `src_fade_offset`, `sync_type`, `cue_filter`,
  `play_post_exit`), the destination side (`dst_fade_time`, `dst_fade_curve`, `dst_fade_offset`, `dst_cue_filter`,
  `jump_to_id`, `jump_to_type`, `entry_type`, `play_pre_entry`, `dst_match_cue`) and the node id lists `src` and `dst`.
  The values are the file's numbers (`sound/bank-objects.md` "Music"). These are not edited:
  the music switch's decision tree, a rule's transition object, stingers and the meter. The music of 1800's and 2205's
  banks is not read, so none of this is offered there.

## The waveform

A bank picked in the list, the clip of a picked Sound and the clip **Play** plays are read and decoded off the
window's thread.
The bank or the Sound asked for last is the one shown or played, and an older answer is dropped.

Picking a Sound or a music track draws its clip above the facts: the peaks of the samples (the lowest and highest
sample of each pixel column, both channels together), with the clip's length and format under it. The clip is the one
the **Play** button would play: an edit's, or else the bank's, or else the loose file. It is decoded in the same way
the Play button decodes it: PCM and Wwise ADPCM directly, and Wwise Vorbis through the codebook file and ffmpeg or
miniaudio (the same two tools that read an Ogg file; the installed program ships the codebook file). Without them the area says why it is empty.
A clip over 40 MB is not drawn.
A music track's segment draws its markers (entry and exit cues) as lines at their time in the clip.
`soundedit/waveform.py` makes the peaks (no Qt), and `soundedit/wavewidget.py` draws them.

## Files

A localised bank (the voices, `BNK_MUS_MAIN`) lives once in every language folder (`windows/en_us/<id>.bnk`,
`windows/de_de/...`) besides the plain file. The game reads the copy of the audio language.
**Save** therefore makes the same edits to every copy the game has (the music fix of `music.md`).
An object that a copy lacks is reported, and the edit is skipped for that copy. All paths are the game's own
(`data/sound/generatedsoundbanks/windows/...`), so a mod folder or, for 1800 and 2205, the delivery archive has them
where the game looks.

A loose clip goes where the bank's info file says the clip lives (when it names one). Otherwise it goes where the game
has one of that name, or else into the default folder of the bank's version. **Save** after undoing every edit removes
the files that an earlier save of that bank made. A media file that the bank's index lists but whose bytes are not
there is reported on Save and left out of the written bank.

## Curves

Pick an Attenuation, a Layer container, or a Sound or container of Anno 117's banks. The facts list its curves, and
the "curve" form offers them: an attenuation's curves with the uses they serve, a layer's curve for each child and its
RTPC entries, or a node's RTPC entries. Edit the points as `x:y:interpolation, ...` (interpolation 4 is linear, 9 is
constant, 0 to 9 are the shapes Wwise has) and choose **Apply the curve**. A curve needs two points or more with x not
going back.
Otherwise the status line says why, and the bank is unchanged.

**Add an RTPC to this object** takes a game parameter id and the property it drives (volume, pitch, low-pass), with the
points typed. **Remove the RTPC**, **Add a curve to this attenuation** (with the use slot that takes it: 0 volume dry
... 3 low-pass, 4 high-pass, 5 spread, 6 focus) and **Remove the curve** (the slots that used it then use none) do the
rest.

The RTPC entries of a Sound or container are read and edited in all three games' banks. The older banks' node base is
read as far as its RTPC list, and a node that does not end where the object goes on is refused. Attenuations and layers
are edited in all three. The meaning of a game parameter is in the game's data
(`sound/anno117-sound-data.md`).

Note: not heard in a game.

## Groups

A state group, a switch group and each value of one is an id, the hash of its name. A Switch container or a Set-State
or Set-Switch action names one by id, so a **new group needs no definition** for those.
**New state or switch group...** asks for the kind, the name and the values. In the Init bank it adds the group to the state manager: a state
group, or a switch group that a game parameter drives (with the game parameter and the points typed as
`value:switch`). In any other bank it names the group and its values for the lists, and tells you their ids to type
into the forms that take one.

Open the Init bank (`1355168291`) to see the state manager's groups in the **Groups** list. Picking one shows its
default transition time, its custom transitions or the game parameter that drives it. The Init bank is the game's own.
Whether the engine reads an Init bank from a mod folder is a question for the game.
So is whether a state group that only a mod's bank uses works
(`sound/bank-objects.md` "State and switch groups").
The group names are not written to any info file.

## The older games

2070, 1404 and 1701 have no banks: their sounds are loose files (WAV and MP3) named by a **sound database**
(`sounddatabase.xml`, `sounds.md` "The sound database of the old games"). **File > Open game** with one of
them shows a page instead of the bank tree:

- **The table** lists every sound (id, file, kind, volume, the zoom range, group), with a filter over what the rows
  show. The box above picks the database where a game has several (1404: the add-on's is the larger and opens first).
- **The form** shows the picked sound's values (file, kind, volume 0 to 100, most at once, the zoom range of full volume
  and of silence, priority, random pitch, flags, horizontal reach, sound group). **Apply** writes the values you
  changed; a value that was not changed keeps its text. **Play** plays the file (double-click does too).
- **The lists** that draw the picked sound, with their weights. Edit the weight cells and choose **Apply the weights**.
- **Replace the clip...** takes a WAV, MP3, Ogg or FLAC file and writes it where the sound plays. A WAV sound gets a
  WAV. An MP3 sound that is given another form gets a PCM WAV beside it, and the database names that. Ogg, FLAC and MP3
  as input need ffmpeg. **Add a sound...** adds a sound that plays a new file in the picked sound's folder, with its
  values copied.
- **Replace a spoken line...** lists the game's spoken lines (every `.mp3` that has a `.sp` beside it), takes a clip
  and writes it as an MP3 with the `.sp` envelope made from its loudness (`COldSoundDoc.replace_speech`).
- **Add to a list...** puts the picked sound into a list the game draws from, at a weight (below an event where the
  game keeps events). A sound that was added plays in the game only once a list draws it (`sounddb.add_member`,
  `remove_member`; an id that the database lacks or that the list already holds is refused).
- **The Timelines tab** lists the game's `.snd` files (a filter narrows them). It shows the length of each animation
  sequence, and each placed sound with its time and id. Edit the numbers and choose **Apply** (one undo step). **Add a
  sound...** places a sound id on the picked sequence, and **Remove** takes the picked placement away
  (`formats/sndtimeline`: `set_length`, `place`, `unplace`, `move`; the file is written in the shipped layout).
- A clip is made ready on a thread (ffmpeg may run for an Ogg, FLAC or MP3 input), with a line in the status bar.
  The document changes when it is done (`COldSoundDoc.prepare_clip` / `put_clip`, `prepare_speech` / `put_files`).
  A second clip while the first runs is announced, not started.
  Picking a row never reads its clip: it only asks whether the file exists.
- A path that an edit would write to must lie inside the game: `..`, a leading slash or a drive refuses the edit.
- Each change is one undo step (Ctrl+Z, Ctrl+Y). **File > Save to the project** keeps the changed database and the
  clips under `sound-edits/<game>/` in the project's paths, and the project's build packs them with the other parts:
  2070's `patch9.rda`, 1404's last `data` archives, 1701's new `data<n>.rda` (`mod-manager.md`, `island-editor.md`).

The viewer opens the same files read-only (tables of sounds, lists with their sounds' files, ambients). It also opens
a model's `.snd` (the sounds on each animation sequence) and a spoken line's `.sp` (its loudness as bars). A
double-click plays the file. This part is tested on synthetic files.
It is also tested read-only on the three installs: every database, timeline and envelope is written back byte for
byte (`tests/soundedit/test_sounddb.py`).
**Nothing was heard in a game.**
It is currently unknown whether the games take a WAV for an MP3 sound, a clip of another length, or an added sound id.

## What to try, and in the game

1. In Anno 117: open a small bank (a UI sound bank), change a sound's volume by +6 dB, save, build, install, restart
   the game and trigger the sound. Then replace one sound by a short WAV as PCM, then as Vorbis (needs ffmpeg).
2. A streamed sound (a voice) replaced: check that the language folder you play in has the loose file and that the line
   plays past the first 3000 bytes (the streaming step is unverified for Vorbis and for language-specific files).
3. A new event: add one that plays a container, name it, and post it by name from an asset (`Audio` asset's event) in a
   test mod.
4. A music playlist entry's weight (117): the only change that needs a long play to notice.
5. 1800 and 2205: a volume change first; the audio replacement is theory until it plays. Then a Vorbis clip in each
   (1800: a Sound with its audio in the bank; 2205: a streamed one, the loose file beside the banks), then a PCM and an
   ADPCM one.
6. Music (117): replace a track's clip by one of another length (scale, then keep) and start the music. Check that the
   song ends where the new clip does and that the next song follows at the exit cue. Move an exit cue earlier by hand
   and listen for the change. Change a transition rule's sync type (7 exit cue, 0 immediate) and watch a change of
   song.
7. Curves: lengthen the attenuation curve of a building sound (the volume at distance) and walk away from it. Give a
   layered ambience another curve. Add an RTPC that lowers a sound's volume with a game parameter (`Camera_Height`).
8. Groups: add a state group to the Init bank in a mod, and see whether the game starts and whether a mod bank's
   Set-State action on it is followed (the log shows a refusal). This is the open question of "Groups" above.
9. An emptied Random container (1800 or 2205): take the last child out, add another back, and play the event.
10. Older games: in 2070 or 1404 lower a building's loop volume (one sound's `MasterVolume`), build, install, start the
    game and listen. Replace a one-shot by a short PCM WAV. An added sound is in no list: it plays only when an asset
    or the game's data names its id (the page cannot add a list member yet). 1701: the same on a sound of the town
    atmosphere (its archive is a new `data<n>.rda`; **Restore** removes it).

## What remains

Nothing here has been heard in a game. These are not built:

- writing a bank's names (the info file is read, never written)
- a bank made from nothing
- buses, the cone of an attenuation, and the music tails of 1800 and 2205 (their objects are read, not edited)
- adding or removing a layer container's layers
- the live reload of one bank (the link names `banks`; the game currently restarts, `live-link.md`)

## Language

The sound editor's windows are in English or German. Choose the language in the settings (Games and folders,
"Language"); it applies the next time the app starts. Game data (asset and file names, the games' own texts) is shown
as the game has it. See [the suite's guide](suite.md#language).
