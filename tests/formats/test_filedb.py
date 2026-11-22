"""FileDB: round trips in all three layouts, in-place patching, early-stopping scan, malformed sizes."""

import struct

import pytest

from annotoolbox.formats import filedb
from annotoolbox.formats.filedb import SAttrib, STag


def _doc() -> STag:
    root = STag("")
    a = STag("Alpha", [SAttrib("Size", struct.pack("<I", 7)), SAttrib("Blob", b"abcdefghij")])
    a.children.append(STag("Inner", [SAttrib("Name", b"x")]))
    root.children += [a, STag("Beta", [SAttrib("Size", b"\x01\x02\x03")])]
    return root


@pytest.mark.parametrize("ver", [1, 2, 3])
def test_round_trip_is_byte_exact(ver):
    b = filedb.write(_doc(), version=ver)
    assert filedb.version(b) == ver
    tags, attrs = filedb.name_ids(b)
    assert filedb.write(filedb.read(b), tags, attrs, version=ver) == b


@pytest.mark.parametrize("new", [b"", b"short", b"x" * 40])
def test_patch_grow_and_shrink(new):
    b = filedb.write(_doc())
    blob = filedb.scan(b).path("Alpha/Blob")
    patched = filedb.patch(b, [(blob, new)])
    assert filedb.read(patched).path("Alpha/Blob").data == new
    assert filedb.read(patched).path("Beta/Size").data == b"\x01\x02\x03"
    assert filedb.scan(patched).path("Alpha/Blob").value(patched) == new


def test_scan_stop_gives_partial_tree():
    b = filedb.write(_doc())
    root = filedb.scan(b, stop=lambda n: n.m_name == "Size")
    assert root.path("Alpha/Size") is not None
    assert root.path("Alpha/Blob") is None


@pytest.mark.parametrize("size", [-8, 1 << 20])
def test_bad_attribute_size_raises(size):
    b = bytearray(filedb.write(_doc()))
    # the first attribute's head: Alpha opens at 0, so its size field sits at byte 8
    struct.pack_into("<i", b, 8, size)
    with pytest.raises(ValueError):
        filedb.read(bytes(b))
    with pytest.raises(ValueError):
        filedb.scan(bytes(b))


@pytest.mark.parametrize("ver", [1, 2, 3])
def test_every_failure_is_the_readers_own_error(ver):
    b = filedb.write(_doc(), version=ver)
    cut = b[: len(b) // 2]
    for call in (filedb.read, filedb.name_ids, filedb.scan):
        with pytest.raises(filedb.CFileDBError):
            call(cut)
        with pytest.raises(filedb.CFileDBError):
            call(b"")


def test_a_short_attribute_and_a_bad_edit_raise_the_readers_error():
    with pytest.raises(filedb.CFileDBError):
        SAttrib("Size", b"\x01").u32()
    with pytest.raises(filedb.CFileDBError):
        filedb.write(_doc(), version=4)
    b = filedb.write(_doc())
    with pytest.raises(filedb.CFileDBError):
        filedb.patch(b, [(filedb.scan(b).path("Alpha"), b"")])


def test_scan_of_a_cut_document_raises_value_error_like_read():
    b = bytearray(filedb.write(_doc()))
    struct.pack_into("<I", b, len(b) - 16, len(b) + 100)  # the name tables lie past the end
    with pytest.raises(ValueError):
        filedb.read(bytes(b))
    with pytest.raises(ValueError):
        filedb.scan(bytes(b))
