"""Which texts a game speaks, by whom, and the clip itself (docs/text-editor.md, "Speakers and voices"). No Qt.

117 names a spoken line in ``audio_generated.xml``: an ``Audio`` asset whose ``OasisScriptId`` is the text's id, whose
``CharacterId`` is the speaker (the ``OasisCharacterId`` of a ``Portrait`` asset) and whose ``WwiseID`` is an event of the
voice bank of the audio language. The older games keep ``speech/<speaker>/<text id>.mp3`` per language, 1800 and 2205 an ``AudioText`` asset per
line (``voicedata``). ``Voices(files, layout)``;
``voice(key)``, ``speaker_label``, ``clip`` (the bytes a player plays, through ``exporting.banks``).
"""

from __future__ import annotations

import re

from ..assetedit.gamefiles import GameFiles, Layout
from ..exporting import banks
from ..infra.i18n import tr
from . import lipsync, voicedata
from .voicedata import Voice

AUDIO_117 = "data/base/config/export/audio_generated.xml"
ASSETS_117 = "data/base/config/export/assets.xml"
VOICE_BANK = "data/sound/generatedsoundbanks/windows/{folder}/1648518890.bnk"  # BNK_VO_MAIN, one per audio language
AUDIO_FOLDERS = {
    "english": "en_us",
    "german": "de_de",
    "french": "fr_fr",
    "russian": "ru_ru",
    "simplified_chinese": "zh_cn",
}
FALLBACK_FOLDER = "en_us"
VOICE_FOLDERS = {  # the audio language folders of the games that name a voice bank by a folder
    "anno2205": {"english": "english(us)", "german": "german", "french": "french(france)", "russian": "russian"},
    "anno1800": {},
}
_SCRIPT = re.compile(r"<OasisScriptId>(-?\d+)</OasisScriptId>")
_FIELDS = {
    "event": re.compile(r"<WwiseID>(\d+)</WwiseID>"),
    "character": re.compile(r"<CharacterId>(-?\d+)</CharacterId>"),
    "mood": re.compile(r"<VoiceMood>(\w+)</VoiceMood>"),
}
_NAME = re.compile(r"<Name>([^<]*)</Name>")
_PAREN = re.compile(r"\(([^()]+)\)")


def tail(number: str) -> str:
    return number[-6:]


def character_labels(files: GameFiles) -> dict[str, str]:
    """117's characters by id, as the ``Portrait`` assets name them (``Advisor Character (Ben Baalion) Portrait``: the
    name in the brackets, else the asset's name). Players' portraits carry the id too, a man's and a woman's."""
    raw = files.read(ASSETS_117).decode("utf-8", "replace")
    out: dict[str, str] = {}
    at = raw.find("<OasisCharacterId>")
    while at >= 0:
        end = raw.find("</OasisCharacterId>", at)
        character = raw[at + 18 : end].strip()
        name = _NAME.search(raw, end, raw.find("</Asset>", end))
        if name is not None and character not in out:
            label = name.group(1).strip()
            paren = _PAREN.search(label)
            out[character] = paren.group(1) if paren else label.removesuffix("Portrait").strip()
        at = raw.find("<OasisCharacterId>", end)
    return out


class Voices:
    """The spoken lines of one game, read on first use (``load`` runs off the window's thread)."""

    def __init__(self, files: GameFiles, layout: Layout) -> None:
        self.files, self.layout, self.game = files, layout, layout.key
        self.characters: dict[str, str] = {}
        self._lines: dict[str, dict[str, Voice]] = {}
        self._banks: dict[str, banks.BankListing] = {}

    @property
    def available(self) -> bool:
        return self.game in ("anno117", "anno1800", "anno2205", *voicedata.SPEECH_FILES)

    def load(self, language: str) -> dict[str, Voice]:
        """Text id -> its voice, for the texts of ``language`` (the games that name a line by an asset have the same
        lines in every language, the speech-file games a folder per language)."""
        key = language if self.game in voicedata.SPEECH_FILES else "*"
        if key not in self._lines:
            self._lines[key] = self._read(language)
        return self._lines[key]

    def _read(self, language: str) -> dict[str, Voice]:
        if self.game == "anno117":
            return self._read_117()
        if self.game in voicedata.SPEECH_FILES:
            return voicedata.read_speech_files(self.files, self.game, self.layout.language_code(language))
        return voicedata.read_audio_texts(self.files, self.layout)

    def _read_117(self) -> dict[str, Voice]:
        if not self.files.exists(AUDIO_117):
            return {}
        self.characters = character_labels(self.files)
        raw = self.files.read(AUDIO_117).decode("utf-8", "replace")
        out: dict[str, Voice] = {}
        for m in _SCRIPT.finditer(raw):
            block = raw[raw.rfind("<Asset>", 0, m.start()) : m.start()]
            found = {k: (rx.search(block).group(1) if rx.search(block) else "") for k, rx in _FIELDS.items()}
            out[m.group(1)] = Voice(self.speaker_label(found["character"]), found["mood"], int(found["event"] or 0))
        return out

    def speaker_label(self, character: str) -> str:
        """A speaker by id: the portrait's name, else the id's last digits."""
        if not character:
            return ""
        return self.characters.get(character) or tr("character {id}", id=tail(character))

    def voice(self, key: str, language: str) -> Voice | None:
        return self.load(language).get(key) if self.available else None

    def can_play(self, key: str, language: str) -> bool:
        """Whether the line has a clip to try: a speech file, or a voice bank of the language (1800's install has none)."""
        voice = self.voice(key, language)
        return voice is not None and bool(voice.path or (voice.event and self._bank_path(language)))

    def clip(self, key: str, language: str) -> tuple[bytes, str]:
        """The line spoken as a file a player plays and its suffix; ``banks.Unplayable`` or ``LookupError`` says why not."""
        voice = self.voice(key, language)
        if voice is None:
            raise LookupError(tr("{id} has no voice line", id=key))
        if voice.path:
            return banks.playable(self.files.read(voice.path), voice.path)
        return self._clip_event(voice, language)

    def lip_line(self, key: str, language: str) -> str:
        """The lip file beside a speech file (1404, 1701) as one line: its frames as bars, how many, over how long. Empty
        where the line has no such file."""
        voice = self.voice(key, language)
        sp = voice.path.removesuffix(".mp3") + ".sp" if voice is not None and voice.path else ""
        if not sp or not self.files.exists(sp):
            return ""
        lips = lipsync.Lips.read(self.files.read(sp))
        if lips is None:
            return ""
        seconds = lipsync.mp3_seconds(self.files.read(voice.path))
        length = f"{seconds:.1f} s" if seconds else "?"
        return tr(
            "Lips: {bars}  {frames} frames in {length}", bars=lips.bars(48), frames=len(lips.levels), length=length
        )

    def _bank_path(self, language: str) -> str:
        """The voice bank of an audio language: 117's one file per folder, 2205's only bank of its language folder (a
        language the game has no voice for plays the first one), '' where the install has none (1800)."""
        if self.game == "anno117":
            folder = AUDIO_FOLDERS.get(language, FALLBACK_FOLDER)
            if not self.files.exists(VOICE_BANK.format(folder=folder)):
                folder = FALLBACK_FOLDER
            return VOICE_BANK.format(folder=folder)
        folders = VOICE_FOLDERS.get(self.game, {})
        for folder in (folders.get(language), *folders.values()):
            found = self.files.glob(f"data/sound/generatedsoundbanks/windows/{folder}/*.bnk") if folder else []
            if found:
                return found[0]
        return ""

    def _clip_event(self, voice: Voice, language: str) -> tuple[bytes, str]:
        path = self._bank_path(language)
        if not path or not voice.event:
            raise LookupError(tr("this install holds no voice bank with the event of this line"))
        if path not in self._banks:
            self._banks[path] = banks.bank_listing(self.files.read(path))
        listing = self._banks[path]
        sounds = banks.walk_event(listing, voice.event).sounds
        sound = next((s for s in sounds if isinstance(s, banks.BankSound)), None)
        if sound is None:
            raise LookupError(tr("the bank has no sound for the event {event}", event=voice.event))
        info = banks.read_info(path, self.files.exists, lambda p: self.files.read(p).decode("utf-8", "replace"))
        loose = banks.media_file(path, sound.media, info, self.files.exists)
        if not loose and sound.media not in listing.media:
            name = info.media.get(sound.media, ("", ""))[1] or f"{sound.media}.wem"
            raise LookupError(tr("the install does not hold the voice file {name}", name=name))
        data = self.files.read(loose) if loose else listing.embedded(sound.media)
        return banks.playable(data, "x.wem")
