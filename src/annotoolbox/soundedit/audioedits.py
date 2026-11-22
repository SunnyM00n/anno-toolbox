"""The edits that change a bank's audio: a Sound's clip replaced, a new Sound made from a clip, a music track's source.

A clip is a ``.wem`` (``clipcodec.encode`` makes one from a WAV). Where it goes follows how the Sound stores its
audio: in the bank's own media when it does (embedded), else as a loose streamed ``.wem`` beside the banks (the
shipped streamed sources; ``docs/sound-editor.md`` "Audio"). ``plan_media`` picks the media id an edit gives its clip,
so a list of edits made again on another copy of the bank (``CBankDoc.replay``) uses the same ids.
"""

from __future__ import annotations

import struct
from dataclasses import replace
from typing import TYPE_CHECKING

from ..exporting.banks import wave_info
from ..formats import bnk, bnkgraph, bnkmusic, bnksfx
from ..formats.bnk import SHircObject
from ..infra.i18n import N_, tr
from . import edits, musicedits
from .clipcodec import ADPCM_PLUGIN
from .registry import CEditError, check_index, edit

if TYPE_CHECKING:
    from .bankdoc import CBankDoc

LANGUAGE, HAS_SOURCE = 1, 8  # bits of a source's flag byte: a copy per language folder; the start is in the bank
PLUGIN_OF_FORMAT = {"PCM": bnk.PCM_PLUGIN, "ADPCM": ADPCM_PLUGIN, "Wwise Vorbis": bnk.VORBIS_PLUGIN}


def wem_plugin(wem: bytes) -> int:
    """The codec plugin a ``.wem`` file needs (PCM, Wwise ADPCM, Wwise Vorbis)."""
    try:
        kind = wave_info(wem)["format"]
    except (ValueError, struct.error) as exc:
        raise CEditError(tr("sound.not_wem_file", exc=exc)) from exc
    if kind not in PLUGIN_OF_FORMAT:
        raise CEditError(tr("sound.wem_format_not_one_pcm", kind=kind))
    return PLUGIN_OF_FORMAT[kind]


def plan_media(doc: CBankDoc, sound: int, name: str = "") -> int:
    """The media id a Sound's new clip gets: the one it plays now when no other Sound shares it, else a new one."""
    node = doc.graph.get(sound)
    if node is None or node.source is None:
        raise CEditError(tr("sound.object_not_sound", sound=sound))
    media = node.source.media
    others = [user for user in doc.media_users(media) if user != sound]
    return doc.new_id(name or f"sound-{sound}-media") if others else media


def _source_end(hirc_object: SHircObject, fam: str) -> tuple[bnkgraph.SSource, int]:
    reader = bnkmusic.CReader(hirc_object.payload)
    source = bnkgraph.read_source(reader, fam)
    return source, reader.m_pos


def _store(doc: CBankDoc, source: bnkgraph.SSource, wem: bytes, media: int, owner: int) -> bnkgraph.SSource:
    """The source record that plays ``wem`` as media ``media``; the media's bytes go where the old source kept its own
    (in the bank, or loose and streamed)."""
    new = replace(source, plugin=wem_plugin(wem), media=media)
    rec = bnkgraph.layout(doc.m_version)
    embedded = source.stream == 0 and rec.stream0_embedded
    if embedded:
        doc.put_embedded(media, wem)
        new.size = len(wem)
        new.high = media if rec.media_high_word else 0  # the bank's own media are found by it
        return new
    doc.put_loose(media, wem, bool(source.flags & LANGUAGE))
    if media != source.media and not [user for user in doc.media_users(source.media) if user != owner]:
        doc.drop_embedded(source.media)  # a prefetched start of a clip nobody plays any more
    new.stream, new.offset, new.flags = 2, 0, source.flags & ~HAS_SOURCE
    new.size = min(len(wem), bnksfx.PREFETCH) if rec.prefetch else len(wem)
    new.high = owner if rec.media_high_word else 0
    return new


@edit(N_("sound.replace_audio"))
def replace_audio(doc: CBankDoc, sound: int, wem: bytes, media: int) -> None:
    """A Sound plays another clip (``wem``), kept as media ``media`` (``plan_media``)."""
    hirc_object = doc.hirc_object(sound)
    if hirc_object.type != bnkgraph.SOUND:
        raise CEditError(tr("sound.type_name_sound_not", type_name=hirc_object.type_name, sound=sound))
    source, end = _source_end(hirc_object, doc.m_family)
    if source.plugin & 0xF == bnkgraph.SOURCE_PLUGIN:
        raise CEditError(tr("sound.sound_silence_source_no_clip"))
    new = _store(doc, source, wem, media, sound)
    doc.put(
        SHircObject(
            hirc_object.type, hirc_object.id, bnkgraph.render_source(new, doc.m_family) + hirc_object.payload[end:]
        )
    )


@edit(N_("sound.add_sound"))
def add_sound(
    doc: CBankDoc, sound: int, template: int, parent: int, wem: bytes, media: int, weight: int = edits.DEFAULT_WEIGHT,
    value: int | None = None,
) -> None:  # fmt: skip
    """A new Sound that plays ``wem``: its settings are a copy of the Sound ``template``'s, it hangs under ``parent``
    (0: under none; a Random/Sequence or Actor-Mixer container, or a Switch container's branch ``value``)."""
    tpl = doc.hirc_object(template)
    if tpl.type != bnkgraph.SOUND:
        raise CEditError(tr("sound.not_sound_copy", type_name=tpl.type_name, template=template))
    source, end = _source_end(tpl, doc.m_family)
    if source.plugin & 0xF == bnkgraph.SOURCE_PLUGIN:
        raise CEditError(tr("sound.template_silence_source_pick_sound"))
    fresh = replace(source, stream=source.stream if source.stream == 0 else 2)
    new = _store(doc, fresh, wem, media, sound)
    made = SHircObject(bnkgraph.SOUND, sound, bnkgraph.render_source(new, doc.m_family) + tpl.payload[end:])
    doc.add(bnkgraph.with_parent(made, doc.m_version, 0))
    if parent:
        edits.add_child(doc, parent, sound, weight, value)


@edit(N_("sound.replace_tracks_audio"))
def replace_track_audio(
    doc: CBankDoc, track: int, index: int, wem: bytes, media: int, markers: str = musicedits.SCALE
) -> str:
    """A music track (117's banks) plays another clip: its source ``index`` becomes ``wem``. The track's playlist items
    take its length, and the segment and its markers go with it by ``markers`` (``musicedits.fit_segment``: ``scale``
    moves everything with the clip's length, ``keep`` keeps the trims and moves the exit cue to the new end). Returns
    the sentence about the segment for the status line."""
    hirc_object = doc.hirc_object(track)
    if not bnkgraph.layout(doc.m_version).music_readable or hirc_object.type != bnkmusic.MUSIC_TRACK:
        raise CEditError(tr("sound.music_tracks_edited_anno_117s"))
    parsed = bnkmusic.parse_track(hirc_object)
    check_index(parsed.m_sources, index, tr("sound.sources"))
    old = parsed.m_sources[index]
    source = bnkgraph.SSource(old.plugin_id, old.stream_type, old.source_id & 0xFFFFFFFF, size=old.in_memory_size)
    source.flags, source.high = old.source_bits, old.source_id >> 32
    new = _store(doc, source, wem, media, track)
    parsed.m_sources[index] = bnkmusic.STrackSource(
        new.plugin, new.stream, new.high << 32 | new.media, new.size, new.flags
    )
    ms = float(wave_info(wem).get("seconds", 0.0)) * 1000.0
    items = [i for i in parsed.m_playlist if i.source_id & 0xFFFFFFFF == old.source_id & 0xFFFFFFFF]
    before = musicedits.visible(items[0]) if items else 0.0
    for item in items:
        item.source_id = new.high << 32 | new.media
        if ms:
            musicedits.retime(item, ms, markers)
    doc.put(bnkmusic.render_track(parsed))
    if not (ms and items):
        return ""
    return musicedits.fit_segment(doc, track, before, musicedits.visible(items[0]), markers)
