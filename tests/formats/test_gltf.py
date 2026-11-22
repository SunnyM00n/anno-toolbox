"""The glTF reader's error and URI handling (formats/gltf) on synthetic files."""

import pytest

from annotoolbox.formats.gltf import CBuilder, CGltfError, parse_glb, read_gltf


def _builder():
    b = CBuilder()
    b.view(b"\1\2\3\4")
    return b


def test_a_buffer_named_with_a_space_and_an_umlaut_is_found(tmp_path):
    files = _builder().files(tmp_path / "my model ä.gltf", "gltf")
    for path, data in files.items():
        path.write_bytes(data)
    assert " " not in files[tmp_path / "my model ä.gltf"].decode().split('"uri"')[1].split('"')[1]
    assert read_gltf(tmp_path / "my model ä.gltf").buffers[0].startswith(b"\1\2\3\4")


def test_a_buffer_named_as_it_is_still_reads(tmp_path):
    (tmp_path / "a b.bin").write_bytes(b"\1\2\3\4")
    (tmp_path / "a.gltf").write_text('{"asset":{"version":"2.0"},"buffers":[{"byteLength":4,"uri":"a b.bin"}]}')
    assert read_gltf(tmp_path / "a.gltf").buffers == [b"\1\2\3\4"]


def test_a_cut_short_glb_is_a_gltf_error():
    with pytest.raises(CGltfError):
        parse_glb(b"glTF\2\0\0\0")


def test_a_glb_with_broken_json_is_a_gltf_error():
    body = b"{nope}   "
    import struct

    chunk = struct.pack("<I4s", len(body), b"JSON") + body
    data = struct.pack("<4sII", b"glTF", 2, 12 + len(chunk)) + chunk
    with pytest.raises(CGltfError):
        parse_glb(data)
