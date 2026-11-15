"""The tag tree of the old engine's text files (``isd2070.parse`` / ``serialize``): written-for-the-test trees, and
every tag-text file of the installed games given by ``ANNO1404_DIR``, ``ANNO1701_DIR``, ``ANNO2070_DIR``,
``ANNO2205_DIR``, ``ANNO1800_DIR`` or ``ANNO117_DIR`` (skipped without them)."""

from __future__ import annotations

import pytest

from annotoolbox.formats import isd2070
from fakes import installed


def _round_trip(data: bytes) -> isd2070.Node:
    root = isd2070.parse(data)
    assert isd2070.serialize(root) == data
    return root


def test_dialects_round_trip():
    blob = b"CDATA[" + bytes([2, 0, 0, 0]) + b"ab]"
    _round_trip(b"<Island><Width>64</Width>\r\n<Terrain><h>" + blob + b"</h>\r\n</>\r\n</Island>\r\n")
    _round_trip(b"<Config 0000>\n  <m_Pos.x>1</m_Pos.x>\n</>")  # 1701: spaces and dots, indentation, no CRLF


def test_an_empty_element_stays_in_its_place():
    root = _round_trip(b"<WorldConfig>\n  <Width>512</Width>\n  <Areas/>\n  <Name/>\n</WorldConfig>\n")
    areas = root.find("WorldConfig").find("Areas")
    assert areas is not None and areas.empty and areas.children == [] and root.find("WorldConfig").text is None


def test_white_space_ending_a_value_and_a_leading_byte_order_mark_are_kept():
    root = _round_trip(b"<a><Name>blue light </Name>\r\n<s>x = </></a>\r\n")
    assert root.find("a").value("Name") == "blue light"  # the value reads without it
    _round_trip(bytes([0xEF, 0xBB, 0xBF]) + b"<DummyRoot><hasValue>1</hasValue>\r\n</DummyRoot>\r\n")


# The tag-text kinds each game ships (docs/formats/README.md, "Formats across the games").
TAG_FILES = {
    "anno1404": (".isd", ".www", ".tww", ".ifo", ".cfg"),
    "anno1701": (".isd", ".isc", ".www", ".tww", ".wcf"),
    "anno2070": (".isd", ".www", ".tww", ".fc", ".ifo", ".cfg"),
    "anno2205": (".fc", ".rdp", ".prp", ".tfo", ".ifo", ".cfg"),
    "anno1800": (".fc", ".rdp", ".prp", ".a7me", ".a7te", ".ifo", ".cfg"),
    "anno117": (".fc", ".rdp", ".a7me", ".a7te", ".ifo", ".cfg"),
}
SAMPLE = 200  # files per kind: the big sets are sampled evenly


@pytest.mark.parametrize("game", list(TAG_FILES))
def test_the_games_tag_files_round_trip(game: str):
    files = installed(game)
    if files is None:
        pytest.skip(f"needs the installed {game}")
    paths = list(files.iter_paths("")) if game != "anno117" else files.iter_paths("data/")
    read = files.read if game == "anno117" else files.read_bytes
    for suffix in TAG_FILES[game]:
        found = [p for p in paths if p.lower().endswith(suffix)]
        for path in found[:: max(1, len(found) // SAMPLE)]:
            data = read(path)
            assert isd2070.serialize(isd2070.parse(data)) == data, path
