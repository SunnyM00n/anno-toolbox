"""Which texts each game speaks, and by whom: the readers behind ``voices.Voices`` (docs/text-editor.md, "Speakers and
voices"). No Qt.

Two shapes. The older games keep a speech file per text (``speech/<speaker folder>/<text id>.mp3``: 2070, 1404, 1701,
``read_speech_files``). 1800 and 2205 name a spoken text with an ``AudioText`` asset that points at an ``Audio`` asset,
whose Wwise id is the event of a voice bank (``read_audio_texts``). 117's lines are read by ``voices`` itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..assetedit.gamefiles import GameFiles, Layout, asset_files

SPEECH_FILES = {
    "anno2070": ("data/loca/{code}/speech/*.mp3",),
    "anno1404": ("data/loca/{code}/speech/*.mp3", "addondata/loca/{code}/speech/*.mp3"),
    "anno1701": ("data/loca/{code}/speech/*.mp3",),
}
OWNER_FILES = {  # 1701: the files that say which text a speech file belongs to
    "anno1701": ("data/config/game/messages.xml", "data/ai/*.xml", "data/ai/addon01/*.xml"),
}
GROUPS_2205 = "data/config/game/asset/audio_texts.xml"
_PREFIX = re.compile(r"(?:ADDON\d+|DLC\d+|GGJ)$", re.I)
_GROUP = re.compile(r"<Group>\s*<Name>([^<]*)</Name>(?:(?!<Group>).)*?<FileName>([^<]*)</FileName>", re.S)
_AUDIO_TEXT, _AUDIO = "<Template>AudioText</Template>", "<Template>Audio</Template>"
_WWISE = re.compile(r"<Wwise(?:Sound)?ID>(\d+)</Wwise(?:Sound)?ID>")
_GUID = re.compile(r"<GUID>(\d+)</GUID>")
_NAME = re.compile(r"<Name>([^<]*)</Name>")
_MESSAGE = re.compile(r"<Message>\s*<ID>(\d+)</ID>(.*?)</Message>", re.S)
_AI_MESSAGE = re.compile(r"<MESSAGE>(.*?)</MESSAGE>", re.S)
_AI_TEXT = re.compile(r"<Text>(\d+)</Text>")
_SPEECH_GUIDS = re.compile(r"<SpeachGuid>(\d+)</SpeachGuid>")
_SOUND_GUIDS = re.compile(r"<GUID>(\d+)</GUID>")
_VARIANT = re.compile(r"(\d+)_\d+$")
_ASSET = re.compile(r"<AudioAsset>(\d+)</AudioAsset>")


@dataclass(frozen=True)
class Voice:
    """One spoken line: who speaks it (the label of the speaker), the mood the game gives it, and where it is (117,
    1800, 2205: the voice bank's event; the older games: the speech file)."""

    speaker: str
    mood: str = ""
    event: int = 0
    path: str = ""
    variants: int = 1  # how many speech files the text has (1701: an AI message has several of one text)


def read_speech_files(files: GameFiles, game: str, code: str) -> dict[str, Voice]:
    """Text id -> its speech file, for a language code; the speaker is the folder the file is in."""
    out: dict[str, Voice] = {}
    for pattern in SPEECH_FILES.get(game, ()):
        for path in files.glob(pattern.format(code=code)):
            folder, name = path.split("/speech/", 1)[1].rsplit("/", 1)
            out[name.removesuffix(".mp3")] = Voice(_folder_label(folder), path=path)
    owners = read_speech_owners(files, game)
    groups: dict[str, list[str]] = {}
    for speech in sorted(out, key=_numbered):
        owner = owners.get(speech) or (m.group(1) if (m := _VARIANT.match(speech)) else "")
        if owner and owner not in out:
            groups.setdefault(owner, []).append(speech)
    for owner, speeches in groups.items():  # a text with no file of its own: its first speech file, the others counted
        out[owner] = replace(out[speeches[0]], variants=len(speeches))
    return out


def _numbered(speech: str) -> tuple[int, str]:
    digits = re.match(r"\d+", speech)
    return (int(digits.group()) if digits else -1, speech)


def read_speech_owners(files: GameFiles, game: str) -> dict[str, str]:
    """Speech file id -> the id of the text it speaks, for the files that no text names itself (1701: ``messages.xml``
    lists a message's speech files, an AI profile's messages their sounds)."""
    out: dict[str, str] = {}
    for pattern in OWNER_FILES.get(game, ()):
        for path in files.glob(pattern):
            text = files.read(path).decode("utf-8", "replace")
            if path.endswith("messages.xml"):
                for message, body in _MESSAGE.findall(text):
                    for speech in _SPEECH_GUIDS.findall(body):
                        out.setdefault(speech, message)
                continue
            for body in _AI_MESSAGE.findall(text):
                owner = _AI_TEXT.search(body)
                for speech in _SOUND_GUIDS.findall(body) if owner else ():
                    out.setdefault(speech, owner.group(1))
    return out


def _folder_label(folder: str) -> str:
    """A speaker folder as a name: the add-on's folder in front of it dropped (2070, 1701), underscores as blanks."""
    return folder.removeprefix("addon/").removeprefix("addon01/").replace("_", " ")


def read_audio_texts(files: GameFiles, layout: Layout) -> dict[str, Voice]:
    """Text key -> its voice, from the ``AudioText`` assets of 1800 and 2205: the text of an asset is the one whose key
    is the asset's own GUID (the Wwise id is 0 where the asset's audio is missing)."""
    game = layout.key
    events: dict[str, int] = {}
    pending: list[tuple[str, str, str, str]] = []  # key, audio asset, speaker, file
    groups = _groups_2205(files) if game == "anno2205" else {}
    for path in asset_files(layout, files):
        raw = files.read(path)
        if b"AudioText" not in raw and b"<Template>Audio<" not in raw:
            continue
        text = raw.decode("utf-8", "replace")
        events |= _audio_events(text)
        for block in _blocks(text, _AUDIO_TEXT):
            key, asset = _field(block, "GUID"), _ASSET.search(block)
            if key and asset:
                name = _NAME.search(block)
                speaker = groups.get(path) or (_speaker_1800(name.group(1)) if name else "")
                pending.append((key, asset.group(1), speaker, path))
    return {key: Voice(speaker, event=events.get(asset, 0)) for key, asset, speaker, _ in pending}


def _blocks(text: str, marker: str):
    """The assets of a template, each from its template line to its end."""
    at = text.find(marker)
    while at >= 0:
        end = text.find("</Asset>", at)
        yield text[at:end]
        at = text.find(marker, end)


def _field(block: str, name: str) -> str:
    """The value of ``<name>`` in a block (an AudioText's ``GUID``)."""
    m = re.search(rf"<{name}>(-?\d+)</{name}>", block)
    return m.group(1) if m else ""


def _audio_events(text: str) -> dict[str, int]:
    """Audio asset GUID -> Wwise event id."""
    out: dict[str, int] = {}
    for block in _blocks(text, _AUDIO):
        guid, event = _GUID.search(block), _WWISE.search(block)
        if guid and event:
            out[guid.group(1)] = int(event.group(1))
    return out


def _speaker_1800(name: str) -> str:
    """1800 names no speaker: the asset's name (``AT_DLC03_OldNate_ArcticQ201_01``) starts with the add-on and the
    one that talks, so the first part after those is what is shown."""
    parts = [p for p in name.split("_") if p and p != "AT"]
    while len(parts) > 1 and _PREFIX.match(parts[0]):
        parts.pop(0)
    return parts[0] if parts else ""


def _groups_2205(files: GameFiles) -> dict[str, str]:
    """2205's text file -> the name of the character group that owns it (``audio_texts.xml``)."""
    if not files.exists(GROUPS_2205):
        return {}
    text = files.read(GROUPS_2205).decode("utf-8", "replace")
    return {path.strip().lower(): name.strip().replace("_", " ") for name, path in _GROUP.findall(text)}
