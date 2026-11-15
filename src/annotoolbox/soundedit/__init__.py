"""The sound editor: Wwise banks of Anno 117 (and 1800 and 2205 where the layouts allow) opened, their events, containers,
sounds and music edited, the audio replaced, the bank written back into a project as mod files.

Start at ``soundwindow.SoundWindow`` (the suite's Sound app). The parts, each one job:

- ``bankdoc``: the open bank, its undo stack, ``to_bytes``; ``registry``: the edits by name and ``EditError``
- ``nodebase``: a Sound's or container's properties (volume, pitch ...); ``containers``: child lists, playlists, switch tables
- ``edits``: the edits of objects; ``audioedits``: the edits of audio (a clip replaced, a sound added, a music track)
- ``clipcodec``: a WAV or Ogg file read, encoded as PCM, Wwise ADPCM or Wwise Vorbis
- ``outputs``: the files an edit makes in every language folder; ``projectpart``: the project's share of a mod
- ``musicedits``: segment markers, transition rules, a segment fitted to a clip; ``curveedits``: RTPC entries, layer and
  attenuation curves; ``groupedits``: the Init bank's state and switch groups
- ``waveform``: a clip's peaks (no Qt); ``wavewidget``, ``musicpanel``, ``curvepanel``: the window's waveform and forms (Qt)
- ``outline``: what the window lists (no Qt); ``soundwindow``: the window (Qt)

docs/sound-editor.md is the user's guide, docs/tool-suite.md section 24 the design.
"""
