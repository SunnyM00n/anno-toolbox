"""The sound database of the old games (2070, 1404, 1701) as an editable document: one ``sounddatabase.xml``, the clips
an edit replaced, an undo stack, and the files to keep (``files``). No Qt.

``OldSoundDoc(tree, path)`` opens a database of a ``sources.gametree.GameTree``. The edits are ``set_fields``,
``set_file``, ``add_sound``, ``set_weight``, ``add_member``, ``remove_member``, ``replace_clip`` (a sound plays another clip) and ``replace_speech`` (a
spoken line and its ``.sp`` envelope); each is one undo step and raises ``OldEditError`` saying why it cannot be made.
docs/sound-editor.md, "The older games".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..exporting import banks
from ..exporting import sounds as export
from ..formats import sndtimeline, sounddb, spenvelope

WAV = ".wav"


class OldEditError(ValueError):
    """An edit that cannot be made; the text says why and the document is as it was."""


@dataclass
class _State:
    data: bytes
    clips: dict[str, bytes] = field(default_factory=dict)  # game path -> the file written there
    label: str = ""


def database_paths(paths) -> list[str]:
    """Every sound database among a game's paths (1404 ships two: the game's and the add-on's)."""
    return [p for p in paths if p.endswith("/sounddatabase.xml")]


def checked_path(path: str) -> str:
    """``path`` as a game path a file can be written to: relative, forward slashes, no ``..`` and no drive."""
    parts = path.replace("\\", "/").split("/")
    if not path.strip() or path.startswith(("/", "\\")) or ".." in parts or ":" in path:
        raise OldEditError(f"{path!r} is not a path inside the game")
    return path.replace("\\", "/")


def suffix_of(path: str) -> str:
    return path[path.rfind(".") :].lower() if "." in path.rsplit("/", 1)[-1] else ""


def convert_clip(target: str, clip: bytes, source_suffix: str) -> bytes:
    """``clip`` (of ``source_suffix``) as the file ``target`` holds it: as it is in the same form, else a PCM WAV. The
    slow part (ffmpeg may run); it reads no document, so it runs on a thread while the window edits."""
    try:
        data = clip if suffix_of(target) == source_suffix else export.to_wav(clip, source_suffix)
        if suffix_of(target) == WAV and banks.wave_info(data)["format"] != "PCM":
            data = export.to_wav(data, WAV)
    except export.ToolMissing as exc:
        raise OldEditError(str(exc)) from exc
    except (banks.Unplayable, ValueError) as exc:
        raise OldEditError(f"the clip does not read: {exc}") from exc
    return data


class OldSoundDoc:
    def __init__(self, tree, path: str) -> None:
        self.tree, self.path = tree, path
        self.original = tree.read(path)
        self._states = [_State(self.original)]
        self._at = 0
        self._saved = self._states[0]  # the state last kept in the project
        self._parsed: tuple[bytes, sounddb.SoundDb] | None = None

    @property
    def data(self) -> bytes:
        return self._states[self._at].data

    @property
    def clips(self) -> dict[str, bytes]:
        return self._states[self._at].clips

    @property
    def db(self) -> sounddb.SoundDb:
        if self._parsed is None or self._parsed[0] is not self.data:
            self._parsed = (self.data, sounddb.read(self.data))
        return self._parsed[1]

    @property
    def changed(self) -> bool:
        return self.data != self.original or bool(self.clips)

    @property
    def unsaved(self) -> bool:
        """Whether the edits differ from the ones last kept (``mark_saved``); an undo back to them is no change."""
        return self._states[self._at] is not self._saved

    def mark_saved(self) -> None:
        self._saved = self._states[self._at]

    @property
    def label(self) -> str:
        return self._states[self._at].label

    def files(self) -> dict[str, bytes]:
        """What the edits made, by game path: the database where it changed and every clip written."""
        out = dict(self.clips)
        if self.data != self.original:
            out[self.path] = self.data
        return out

    def clip(self, sound: int) -> tuple[str, bytes | None]:
        """(path, bytes) of the file a sound plays now: an edit's, else the game's; bytes None when it has none."""
        found = self.db.sounds.get(sound)
        path = found.filename if found else ""
        if path in self.clips:
            return path, self.clips[path]
        return path, self.tree.read(path) if path and self.tree.exists(path) else None

    # edits
    def _push(self, label: str, data: bytes | None = None, clips: dict[str, bytes] | None = None) -> None:
        state = self._states[self._at]
        new = _State(state.data if data is None else data, {**state.clips, **(clips or {})}, label)
        self._states = [*self._states[: self._at + 1], new]
        self._at += 1

    def _edit(self, label: str, make) -> None:
        try:
            data = make()
        except sounddb.SoundDbError as exc:
            raise OldEditError(str(exc)) from exc
        self._push(label, data)

    def set_fields(self, sound: int, **values: object) -> None:
        self._edit("set_fields", lambda: sounddb.set_fields(self.data, sound, **values))

    def set_file(self, sound: int, path: str) -> None:
        self._edit("set_file", lambda: sounddb.set_file(self.data, sound, path))

    def set_weight(self, list_id: int, sound: int, weight: int, event: int = sounddb.NO_EVENT) -> None:
        self._edit("set_weight", lambda: sounddb.set_weight(self.data, list_id, sound, weight, event))

    def add_member(self, list_id: int, sound: int, weight: int, event: int = sounddb.NO_EVENT) -> None:
        """The list draws ``sound`` at ``weight`` (below ``event`` where the game keeps events): an added sound plays
        in the game only once a list draws it."""
        self._edit("add_member", lambda: sounddb.add_member(self.data, list_id, sound, weight, event))

    def remove_member(self, list_id: int, sound: int, event: int = sounddb.NO_EVENT) -> None:
        self._edit("remove_member", lambda: sounddb.remove_member(self.data, list_id, sound, event))

    def add_sound(self, path: str, clip: bytes | None = None, like: int | None = None, **values: object) -> int:
        """A new sound playing ``path`` (its ``clip`` is written there); returns its id."""
        path = checked_path(path)
        try:
            data, new = sounddb.add_sound(self.data, path, like, **values)
        except sounddb.SoundDbError as exc:
            raise OldEditError(str(exc)) from exc
        self._push("add_sound", data, {path: clip} if clip is not None else None)
        return new

    def target_of(self, sound: int, source_suffix: str) -> str:
        """The path a replacing clip of ``source_suffix`` is written to: the sound's own file, or a WAV beside it
        when the sound plays another form that the clip cannot be made into without a tool."""
        found = self.db.sounds.get(sound)
        if found is None:
            raise OldEditError(f"the database has no sound {sound}")
        if not found.filename:
            raise OldEditError(f"sound {sound} names no file")
        path = checked_path(found.filename)
        wanted = suffix_of(path)
        if wanted in (source_suffix, WAV):
            return path
        return path[: path.rfind(".")] + WAV if "." in path.rsplit("/", 1)[-1] else path + WAV

    def prepare_clip(self, sound: int, clip: bytes, source_suffix: str) -> tuple[str, bytes]:
        """(path, bytes) a replacing ``clip`` of ``source_suffix`` is written as: in the sound's own form, else as a PCM
        WAV; ``put_clip`` applies it. A window asks ``target_of`` itself and runs only ``convert_clip`` on a thread."""
        target = self.target_of(sound, source_suffix)
        return target, convert_clip(target, clip, source_suffix)

    def put_clip(self, sound: int, target: str, data: bytes) -> str:
        """The sound plays ``data`` at ``target`` (the database names it when it is not the sound's file); the path."""
        found = self.db.sounds.get(sound)
        if found is None:  # undone while the clip was being converted
            raise OldEditError(f"the database has no sound {sound}")
        current = found.filename
        edited = sounddb.set_file(self.data, sound, target) if target != current else self.data
        self._push("replace_clip", edited, {target: data})
        return target

    def replace_clip(self, sound: int, clip: bytes, source_suffix: str) -> str:
        """The sound plays ``clip`` (a file of ``source_suffix``): as it is in the sound's own form, else as a PCM WAV
        (the database then names the WAV). Returns the path written."""
        return self.put_clip(sound, *self.prepare_clip(sound, clip, source_suffix))

    def prepare_speech(self, path: str, clip: bytes, source_suffix: str) -> dict[str, bytes]:
        """The two files a spoken line at ``path`` (an MP3 under a speech folder) becomes: the MP3 and the ``.sp``
        envelope beside it. Slow like ``prepare_clip``; ``put_files`` applies it."""
        path = checked_path(path)
        if suffix_of(path) != ".mp3":
            raise OldEditError(f"{path} is not a speech line (an MP3)")
        try:
            samples, rate = export.mono_samples(clip, source_suffix)
            mp3 = export.to_mp3(clip, source_suffix)
        except export.ToolMissing as exc:
            raise OldEditError(str(exc)) from exc
        except (banks.Unplayable, ValueError) as exc:
            raise OldEditError(f"the clip does not read: {exc}") from exc
        envelope = spenvelope.Envelope.from_samples(samples, rate).to_bytes()
        return {path: mp3, path[: -len(".mp3")] + ".sp": envelope}

    def put_files(self, label: str, files: dict[str, bytes]) -> list[str]:
        """Files written as they are (one undo step); returns their paths."""
        self._push(label, None, files)
        return list(files)

    def replace_speech(self, path: str, clip: bytes, source_suffix: str) -> list[str]:
        """The spoken line at ``path`` says ``clip`` and its ``.sp`` envelope next to it follows; returns the two
        paths written."""
        return self.put_files("replace_speech", self.prepare_speech(path, clip, source_suffix))

    def timeline(self, path: str) -> sndtimeline.Timeline:
        """A model's ``.snd`` as it is now (an edit's, else the game's); OldEditError when it does not read."""
        try:
            data = self.clips[path] if path in self.clips else self.tree.read(path)
            return sndtimeline.parse(data.decode("utf-8", "replace"))
        except (ValueError, KeyError, OSError) as exc:
            raise OldEditError(f"{path} does not read: {exc}") from exc

    def edit_timeline(self, path: str, edit, *args) -> None:
        """One undo step: ``edit`` (a ``sndtimeline`` function: ``set_length``, ``place``, ``unplace``, ``move``)
        applied with ``args`` to the ``.snd`` at ``path``, written back in the game's own layout."""
        path = checked_path(path)
        try:
            changed = edit(self.timeline(path), *args)
        except sndtimeline.TimelineError as exc:
            raise OldEditError(str(exc)) from exc
        self._push("edit_timeline", None, {path: sndtimeline.render(changed).encode("utf-8")})

    def has_clip(self, sound: int) -> bool:
        """Whether the file a sound plays now exists (an edit's or the game's), without reading it."""
        found = self.db.sounds.get(sound)
        path = found.filename if found else ""
        return bool(path) and (path in self.clips or self.tree.exists(path))

    # the stack
    def undo(self) -> bool:
        if self._at == 0:
            return False
        self._at -= 1
        return True

    def redo(self) -> bool:
        if self._at + 1 >= len(self._states):
            return False
        self._at += 1
        return True
