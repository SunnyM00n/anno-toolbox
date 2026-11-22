"""Stand-ins for the exporter's sources: a folder source, documents, media and a bank with media."""

from __future__ import annotations

import array
import struct
from pathlib import Path

from annotoolbox.exporting import run
from annotoolbox.formats import bnk, filedb
from annotoolbox.formats.filedb import SAttrib
from annotoolbox.formats.filedb import STag as STag_filedb


def folder(tmp_path: Path, files: dict[str, bytes]) -> run.CFolderSource:
    for name, body in files.items():
        (tmp_path / "game" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "game" / name).write_bytes(body)
    return run.CFolderSource(tmp_path / "game")


def doc_bytes() -> bytes:
    root = STag_filedb(
        "",
        [
            STag_filedb(
                "Island",
                [SAttrib("Name", b"Moon"), SAttrib("Count", struct.pack("<I", 7)), SAttrib("Blob", bytes(range(200)))],
            )
        ],
    )
    return filedb.write(root)


def wem_pcm(samples: list[int]) -> bytes:
    return bnk.pcm_wem(array.array("h", samples), 22050, 1)


# an empty node base of version 172: no effects, metadata, properties, positioning, aux, states or RTPCs
NODE_BASE = bytes(4) + struct.pack("<II", 0, 0) + bytes(3) + bytes(2) + struct.pack("<I", 0) + bytes(6) + bytes(4)


def media_sound_object(oid: int, media: int) -> bnk.SHircObject:
    return bnk.SHircObject(2, oid, struct.pack("<IBI", bnk.PCM_PLUGIN, 0, media) + bytes(9) + NODE_BASE)


def bank_with_media() -> bytes:
    from annotoolbox.formats import bnksfx

    clips = [wem_pcm([1, 2, 3, 4]), wem_pcm([9, 8, 7, 6])]
    data = b"".join(clips)
    bank = bnk.SSoundBank(
        [
            bnk.SChunk(b"BKHD", struct.pack("<III", 172, 9, 0)),
            bnk.SChunk(b"DIDX", b""),
            bnk.SChunk(b"DATA", data),
            bnk.SChunk(b"HIRC", b""),
        ]
    )
    bank.media = [bnk.SMediaEntry(55, 0, len(clips[0])), bnk.SMediaEntry(66, len(clips[0]), len(clips[1]))]
    bank.hirc = [
        media_sound_object(11, 55),
        media_sound_object(12, 66),
        bnksfx.play_action(21, 11, 9),
        bnksfx.event(1, [21]),
    ]
    return bnk.write(bank)


def adpcm_wem(nibbles: bytes, start: int = 1000) -> bytes:
    """A mono Wwise ADPCM clip of one block: the start value, step index 0, then the nibble bytes (32 of them)."""
    block = struct.pack("<hBB", start, 0, 0) + nibbles.ljust(32, b"\0")
    fmt = struct.pack("<HHIIHHH", 2, 1, 48000, 48000 * 36 // 64, 36, 4, 0)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(block)) + block
    return b"RIFF" + struct.pack("<I", len(body)) + body
