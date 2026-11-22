"""Stand-ins for the sound editor: banks, objects and a window over a bank."""

from __future__ import annotations

import array
import struct

import pytest
from tests.fakes import installed
from tests.formats.formatfakes import OLD_BANK, action, container, event, sound_object, switch_container

from annotoolbox.formats import bnk, bnkgraph
from annotoolbox.soundedit.bankdoc import CBankDoc
from annotoolbox.sources import gametree

BANK_VERSIONS = (112, 135, 172)


def wem(seed: int = 1, n: int = 64) -> bytes:
    return bnk.pcm_wem(array.array("h", [seed * 100 + i for i in range(n)]), 48000, 1)


def snd(version: int, oid: int, media: int, parent: int, streamed: bool = False):
    """A sound whose audio is in the bank (stream 0) where the layout keeps it there, else streamed."""
    return sound_object(version, oid, media, parent, stream=2 if streamed or version == OLD_BANK else 0)


def ranseq(version: int, oid: int, parent: int, children: list[int], playlist: list[tuple[int, int]]):
    obj = container(version, bnkgraph.RANSEQ, oid, parent, children, bytes(24))
    tail = struct.pack("<H", len(playlist)) + b"".join(struct.pack("<II", c, w) for c, w in playlist)
    return bnk.SHircObject(obj.type, oid, obj.payload + tail)


def objects(version: int, streamed: bool = False) -> list[bnk.SHircObject]:
    """Random container 20 (sounds 11, 12; playlist 11, 12, 11), switch container 30 (group 900: value 1 plays 13, value
    2 plays 14), actor-mixer 40 over sound 15; events 1 (plays 20), 2 (plays 30), 3 (sets switch 900 to 2)."""
    return [
        snd(version, 11, 1100, 20, streamed), snd(version, 12, 1200, 20, streamed), snd(version, 13, 1300, 30, streamed), snd(version, 14, 1400, 30, streamed),
        snd(version, 15, 1500, 40, streamed),
        ranseq(version, 20, 0, [11, 12], [(11, 50), (12, 70), (11, 10)]),
        switch_container(version, 30, 900, 1, {1: [13], 2: [14]}),
        container(version, bnkgraph.MIXER, 40, 0, [15]),
        action(21, 0x0403, 20), action(22, 0x0403, 30), action(23, 0x1901, 2, 900, 2),
        event(version, 1, [21]), event(version, 2, [22]), event(version, 3, [23]),
    ]  # fmt: skip


def bank_bytes(version: int, bank_id: int = 9, streamed: bool = False) -> bytes:
    have = version != OLD_BANK and not streamed
    chunks = [bnk.SChunk(b"BKHD", struct.pack("<III", version, bank_id, 0))]
    entries = []
    blob = bytearray()
    if have:
        for mid in (1100, 1200, 1300, 1400, 1500):
            blob += bytes(-len(blob) % 16)
            data = wem(mid // 100)
            entries.append(bnk.SMediaEntry(mid, len(blob), len(data)))
            blob += data
        chunks += [bnk.SChunk(b"DIDX", b""), bnk.SChunk(b"DATA", bytes(blob))]
    chunks.append(bnk.SChunk(b"HIRC", b""))
    return bnk.write(bnk.SSoundBank(chunks, objects(version, streamed), entries))


def doc_of(version: int, **kw) -> CBankDoc:
    return CBankDoc(bank_bytes(version, **kw), f"data/sound/generatedsoundbanks/windows/{version}.bnk")


BANK_PATH = "data/sound/generatedsoundbanks/windows/9.bnk"


def bank_window(window, monkeypatch, version: int = 172, project_folder=None):
    """The fixture's sound window with the test bank of ``version`` open (and a project in ``project_folder``)."""
    if project_folder is not None:
        from annotoolbox.suite import project as suite_project

        project = suite_project.CProject(project_folder, mod_id="m")
        monkeypatch.setattr(window, "project", lambda: project)
    files = {BANK_PATH: bank_bytes(version)}
    window.m_tree, window.m_key = gametree.CGameTree("t", lambda p: files[p], files, lambda p: "t"), "anno117"
    window._fill_banks()
    window.open_bank(BANK_PATH)
    return window


LANGUAGES = ("/de_de/", "/en_us/", "/fr_fr/", "/ru_ru/", "/zh_cn/", "/es_es/")


def installed_banks(game: str, limit: int = 30_000_000, english: bool = False):
    """(path, bytes) of the bank files of a game's install that are not copies in a language folder (the English ones
    too with ``english``: 117 keeps its prepared and voice banks only there)."""
    files = installed(game)
    if files is None:
        pytest.skip(f"needs {game} (its INSTALL_VARS variable)")
    read = getattr(files, "read_bytes", None) or files.read
    for path in files.iter_paths("data/sound/"):
        skipped = LANGUAGES[:1] + LANGUAGES[2:] if english else LANGUAGES
        if path.endswith(".bnk") and not any(lang in path for lang in skipped):
            data = read(path)
            if len(data) <= limit:
                yield path, data
