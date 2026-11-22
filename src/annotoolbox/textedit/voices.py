"""Which texts a game speaks, by whom, and the clip itself (docs/text-editor.md, "Speakers and voices"). No Qt.

117 names a spoken line in ``audio_generated.xml``: an ``Audio`` asset whose ``OasisScriptId`` is the text's id, whose
``CharacterId`` is the speaker (the ``OasisCharacterId`` of a ``Portrait`` asset) and whose ``WwiseID`` is an event of the
voice bank of the audio language. The older games keep ``speech/<speaker>/<text id>.mp3`` per language, 1800 and 2205 an ``AudioText`` asset per
line (``voicedata``). ``CVoices(files, layout)``;
``voice(key)``, ``speaker_label``, ``clip`` (the bytes a player plays, through ``exporting.banks``), ``clip_file``
(the clip as a file of the cache folder).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from ..assetedit.gamefiles import CGameFiles, SLayout
from ..exporting import banks
from ..infra.buildcache import write_atomic
from ..infra.i18n import tr
from . import lipsync, voicedata
from .voicedata import SVoice

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
CLIPS = "sounds"  # the decoded clips of every app that plays one, in the cache folder
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


def character_labels(files: CGameFiles) -> dict[str, str]:
    """117's characters by id, as the ``Portrait`` assets name them (``Advisor Character (Ben Baalion) Portrait``: the
    name in the brackets, else the asset's name). Players' portraits carry the id too, a man's and a woman's."""
    raw = files.read(ASSETS_117).decode("utf-8", "replace")
    out: dict[str, str] = {}
    start = raw.find("<OasisCharacterId>")
    while start >= 0:
        end = raw.find("</OasisCharacterId>", start)
        character = raw[start + 18 : end].strip()
        name = _NAME.search(raw, end, raw.find("</Asset>", end))
        if name is not None and character not in out:
            label = name.group(1).strip()
            paren = _PAREN.search(label)
            out[character] = paren.group(1) if paren else label.removesuffix("Portrait").strip()
        start = raw.find("<OasisCharacterId>", end)
    return out


class CVoices:
    """The spoken lines of one game, read on first use (``load`` runs off the window's thread)."""

    def __init__(self, files: CGameFiles, layout: SLayout) -> None:
        self.m_files, self.m_layout, self.m_game = files, layout, layout.key
        self.m_characters: dict[str, str] = {}
        self._m_lines: dict[str, dict[str, SVoice]] = {}
        self._m_banks: dict[str, banks.SBankListing] = {}

    @property
    def available(self) -> bool:
        return self.m_game in ("anno117", "anno1800", "anno2205", *voicedata.SPEECH_FILES)

    def load(self, language: str) -> dict[str, SVoice]:
        """Text id -> its voice, for the texts of ``language`` (the games that name a line by an asset have the same
        lines in every language, the speech-file games a folder per language)."""
        key = language if self.m_game in voicedata.SPEECH_FILES else "*"
        if key not in self._m_lines:
            self._m_lines[key] = self._read(language)
        return self._m_lines[key]

    def _read(self, language: str) -> dict[str, SVoice]:
        if self.m_game == "anno117":
            return self._read_117()
        if self.m_game in voicedata.SPEECH_FILES:
            return voicedata.read_speech_files(self.m_files, self.m_game, self.m_layout.language_code(language))
        return voicedata.read_audio_texts(self.m_files, self.m_layout)

    def _read_117(self) -> dict[str, SVoice]:
        if not self.m_files.exists(AUDIO_117):
            return {}
        self.m_characters = character_labels(self.m_files)
        raw = self.m_files.read(AUDIO_117).decode("utf-8", "replace")
        out: dict[str, SVoice] = {}
        for match in _SCRIPT.finditer(raw):
            block = raw[raw.rfind("<Asset>", 0, match.start()) : match.start()]
            found = {
                k: (pattern.search(block).group(1) if pattern.search(block) else "") for k, pattern in _FIELDS.items()
            }
            out[match.group(1)] = SVoice(
                self.speaker_label(found["character"]), found["mood"], int(found["event"] or 0)
            )
        return out

    def speaker_label(self, character: str) -> str:
        """A speaker by id: the portrait's name, else the id's last digits."""
        if not character:
            return ""
        return self.m_characters.get(character) or tr("texts.character_id", id=tail(character))

    def voice(self, key: str, language: str) -> SVoice | None:
        return self.load(language).get(key) if self.available else None

    def can_play(self, key: str, language: str) -> bool:
        """Whether the line has a clip to try: a speech file, or a voice bank of the language (1800's install has none)."""
        voice = self.voice(key, language)
        return voice is not None and bool(voice.path or (voice.event and self._bank_path(language)))

    def clip(self, key: str, language: str) -> tuple[bytes, str]:
        """The line spoken as a file a player plays and its suffix; ``banks.CUnplayable`` or ``LookupError`` says why not."""
        voice = self.voice(key, language)
        if voice is None:
            raise LookupError(tr("texts.no_voice_line", id=key))
        if voice.path:
            return banks.playable(self.m_files.read(voice.path), voice.path)
        return self._clip_event(voice, language)

    def clip_file(self, key: str, language: str) -> Path:
        """The line's clip as a file a media player opens: written once into the cache folder's ``sounds``, named by
        its content (the temp folder would keep one file per line played, for good). ``OSError`` when it cannot be
        written."""
        data, suffix = self.clip(key, language)
        target = self.m_files.m_cache / CLIPS / (hashlib.sha1(data).hexdigest()[:16] + suffix)
        if not target.is_file():
            write_atomic(target, data, is_synchronous=False)
        return target

    def lip_line(self, key: str, language: str) -> str:
        """The lip file beside a speech file (1404, 1701) as one line: its frames as bars, how many, over how long. Empty
        where the line has no such file."""
        voice = self.voice(key, language)
        lip_path = voice.path.removesuffix(".mp3") + ".sp" if voice is not None and voice.path else ""
        if not lip_path or not self.m_files.exists(lip_path):
            return ""
        lips = lipsync.SLips.read(self.m_files.read(lip_path))
        if lips is None:
            return ""
        seconds = lipsync.mp3_seconds(self.m_files.read(voice.path))
        length = f"{seconds:.1f} s" if seconds else "?"
        return tr("texts.lips_bars_frames_length", bars=lips.bars(48), frames=len(lips.levels), length=length)

    def _bank_path(self, language: str) -> str:
        """The voice bank of an audio language: 117's one file per folder, 2205's only bank of its language folder (a
        language the game has no voice for plays the first one), '' where the install has none (1800)."""
        if self.m_game == "anno117":
            folder = AUDIO_FOLDERS.get(language, FALLBACK_FOLDER)
            if not self.m_files.exists(VOICE_BANK.format(folder=folder)):
                folder = FALLBACK_FOLDER
            return VOICE_BANK.format(folder=folder)
        folders = VOICE_FOLDERS.get(self.m_game, {})
        for folder in (folders.get(language), *folders.values()):
            found = self.m_files.glob(f"data/sound/generatedsoundbanks/windows/{folder}/*.bnk") if folder else []
            if found:
                return found[0]
        return ""

    def _clip_event(self, voice: SVoice, language: str) -> tuple[bytes, str]:
        path = self._bank_path(language)
        if not path or not voice.event:
            raise LookupError(tr("texts.install_holds_no_voice_bank"))
        if path not in self._m_banks:
            self._m_banks[path] = banks.bank_listing(self.m_files.read(path))
        listing = self._m_banks[path]
        sounds = banks.walk_event(listing, voice.event).sounds
        sound = next((candidate for candidate in sounds if isinstance(candidate, banks.SBankSound)), None)
        if sound is None:
            raise LookupError(tr("texts.bank_no_sound_event", event=voice.event))
        bank_info = banks.read_info(
            path, self.m_files.exists, lambda bank_path: self.m_files.read(bank_path).decode("utf-8", "replace")
        )
        loose = banks.media_file(path, sound.media, bank_info, self.m_files.exists)
        if not loose and sound.media not in listing.media:
            name = bank_info.media.get(sound.media, ("", ""))[1] or f"{sound.media}.wem"
            raise LookupError(tr("texts.install_not_hold_voice_file", name=name))
        data = self.m_files.read(loose) if loose else listing.embedded(sound.media)
        return banks.playable(data, "x.wem")
