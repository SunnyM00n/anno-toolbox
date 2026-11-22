"""The texture pipeline without texconv: numpy DDS decode, our mip chain, the in-process BC7 encoder, the shared
metal map (docs/texture-pipeline.md). Synthetic images only."""

import struct

import pytest

np = pytest.importorskip("numpy")

from annotoolbox.models import bc7encode, ddsdecode, mipchain, textures  # noqa: E402


def legacy_dds(fourcc: bytes, width: int, height: int, payload: bytes) -> bytes:
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x1007, height, width, 0, 0, 1) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x4, fourcc, 0, 0, 0, 0, 0) + struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    return head + payload


def test_dxt1_decodes_the_four_colour_and_the_punch_through_modes():
    block = struct.pack("<HHI", 0xF800, 0x001F, 0b11_10_01_00)  # red, blue; texel 0..3 = red, blue, 2/3 mix, 1/3 mix
    px = ddsdecode.read_dds(legacy_dds(b"DXT1", 4, 4, block))
    assert px[0, 0].tolist() == [255, 0, 0, 255] and px[0, 1].tolist() == [0, 0, 255, 255]
    assert px[0, 2].tolist() == [170, 0, 85, 255] and px[0, 3].tolist() == [85, 0, 170, 255]
    block = struct.pack("<HHI", 0x001F, 0xF800, 0b11_10_01_00)  # c0 < c1: half mix and a transparent black
    px = ddsdecode.read_dds(legacy_dds(b"DXT1", 4, 4, block))
    assert px[0, 2].tolist() == [127, 0, 127, 255] and px[0, 3].tolist() == [0, 0, 0, 0]


def test_dxt5_alpha_has_the_eight_and_the_six_value_ramps():
    colour = struct.pack("<HHI", 0xFFFF, 0xFFFF, 0)
    bits = sum(i << (3 * i) for i in range(8))  # texels 0..7 use palette entries 0..7
    ramp8 = bytes([200, 100]) + bits.to_bytes(6, "little") + colour
    a = ddsdecode.read_dds(legacy_dds(b"DXT5", 4, 4, ramp8))[..., 3].reshape(-1)[:8].tolist()
    assert a == [200, 100, 185, 171, 157, 142, 128, 114]
    ramp6 = bytes([40, 240]) + bits.to_bytes(6, "little") + colour
    a = ddsdecode.read_dds(legacy_dds(b"DXT5", 4, 4, ramp6))[..., 3].reshape(-1)[:8].tolist()
    assert a == [40, 240, 80, 120, 160, 200, 0, 255]


def test_bc5_gives_red_and_green_and_a_formats_it_cannot_read_are_named():
    bc4 = bytes([255, 255]) + bytes(6)
    px = ddsdecode.read_dds(legacy_dds(b"ATI2", 4, 4, bc4 + bytes([0, 0]) + bytes(6)))
    assert px[0, 0].tolist() == [255, 0, 0, 255]
    with pytest.raises(ddsdecode.CUnsupportedDds):
        ddsdecode.read_dds(legacy_dds(b"DX10", 4, 4, bytes(32)))
    with pytest.raises(ddsdecode.CUnsupportedDds):
        ddsdecode.read_dds(legacy_dds(b"DXT1", 8, 8, bytes(8)))  # shorter than its header says


def test_an_uncompressed_bgra_file_is_reordered_into_rgba():
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x100F, 1, 2, 8, 0, 1) + bytes(44)
    head += struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    head += struct.pack("<5I", 0x1000, 0, 0, 0, 0)
    px = ddsdecode.read_dds(head + bytes([30, 20, 10, 255, 3, 2, 1, 7]))  # B G R A
    assert px.tolist() == [[[10, 20, 30, 255], [1, 2, 3, 7]]]


def test_the_alpha_does_not_darken_the_colour_in_the_small_mips():
    px = np.zeros((16, 16, 4), np.uint8)
    px[...] = (200, 60, 0, 3)  # a rock's normal map with a low gloss
    levels = mipchain.mip_chain(px)
    assert len(levels) == 5 and [lv.shape[0] for lv in levels] == [16, 8, 4, 2, 1]
    assert all(lv[..., :3].tolist() == px[: lv.shape[0], : lv.shape[1], :3].tolist() for lv in levels)
    assert all(int(lv[..., 3].max()) == 3 for lv in levels)


def test_alpha_is_averaged_on_its_own_and_colour_ignores_it():
    px = np.zeros((2, 2, 4), np.uint8)
    px[0, 0] = (255, 255, 255, 255)  # one opaque white texel among transparent black ones
    top = mipchain.mip_chain(px)[1]
    assert top[0, 0, 3] == 64 and top[0, 0, 0] == 64  # colour 255/4, not weighted by alpha


def test_srgb_stored_colour_is_averaged_in_linear_light():
    px = np.zeros((2, 2, 4), np.uint8)
    px[0, :, :3] = 255
    px[..., 3] = 255
    plain = mipchain.mip_chain(px)[1][0, 0, 0]
    gamma = mipchain.mip_chain(px, gamma=True)[1][0, 0, 0]
    assert plain == 128 and gamma == 188  # half the light is sRGB 188, not 128
    linear = np.zeros((2, 2, 4), np.uint8)
    linear[..., :3] = 120
    assert (
        mipchain.mip_chain(linear, 1, to_srgb=True)[0][0, 0, 0] == 182
    )  # linear 120 stored as sRGB (texconv: 183): the "too bright" step


def test_odd_sizes_are_area_averaged_and_counts_are_honoured():
    px = np.arange(5 * 3 * 4, dtype=np.uint8).reshape(5, 3, 4)
    levels = mipchain.mip_chain(px, 2)
    assert len(levels) == 2 and levels[1].shape[:2] == (2, 1)
    assert mipchain.mip_chain(px, 1)[0].shape == (5, 3, 4) and len(mipchain.mip_chain(px)) == 3


needs_encoder = pytest.mark.skipif(not bc7encode.available(), reason="the in-process encoder (etcpak) is not installed")


def test_the_dds_header_is_the_one_texconv_writes():
    head = bc7encode.dds_header(512, 512, 10, True)
    assert len(head) == 148 and head[:4] == b"DDS " and head[84:88] == b"DX10"
    assert struct.unpack_from("<IIIIIII", head, 8) == (0xA1007, 512, 512, 0x40000, 1, 10, 0)
    assert struct.unpack_from("<I", head, 128)[0] == 99 and bc7encode.dds_header(8, 8, 1, False)[128] == 98


def pattern(size: int = 64) -> "np.ndarray":
    y, x = np.mgrid[0:size, 0:size]
    px = np.zeros((size, size, 4), np.uint8)
    px[..., 0], px[..., 1] = x * 255 // (size - 1), y * 255 // (size - 1)
    px[..., 2] = ((x // 8 + y // 8) % 2) * 200
    px[..., 3] = 255 - (x + y) * 255 // (2 * size - 2)
    return px


@needs_encoder
def test_the_encoder_is_deterministic_and_sizes_the_file_like_bc7():
    levels = mipchain.mip_chain(pattern(), gamma=True)
    first = bc7encode.encode_dds(levels, True, True)
    assert first == bc7encode.encode_dds(levels, True, True)
    assert len(first) == 148 + sum(max(1, -(-lv.shape[0] // 4)) * max(1, -(-lv.shape[1] // 4)) * 16 for lv in levels)
    assert textures.dds_info(first) == (64, 64, 7, textures.DXGI_BC7_SRGB)


@needs_encoder
def test_two_runs_of_the_whole_path_write_the_same_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    px = pattern(48)
    runs = [textures.rgba_to_bc7(px, is_srgb=True, is_srgb_input=True) for _ in range(2)]
    assert runs[0] == runs[1] and textures.dds_info(runs[0])[:3] == (48, 48, 6)


@needs_encoder
def test_the_encoded_texture_reads_back_close_to_the_image_in_every_channel():
    decoder = pytest.importorskip("texture2ddecoder")
    px = pattern()
    out = bc7encode.encode_dds(mipchain.mip_chain(px, 1), False, False)
    bgra = np.frombuffer(decoder.decode_bc7(out[148:], 64, 64), np.uint8).reshape(64, 64, 4)
    got = bgra[..., [2, 1, 0, 3]].astype(float)
    for channel in range(4):
        mse = ((got[..., channel] - px[..., channel]) ** 2).mean()
        assert mse == 0 or 10 * np.log10(255**2 / mse) > 38, channel


@needs_encoder
def test_an_odd_sized_level_is_padded_to_whole_blocks():
    out = bc7encode.encode_dds(mipchain.mip_chain(pattern(6)[:6, :5]), False, False)
    assert len(out) == 148 + 16 * (4 + 1 + 1)  # 5x6 -> 2x2 blocks, 2x3 -> 1 block, 1x1 -> 1 block (mip 2: 1x1 too)


def test_the_shared_metal_map_is_dielectric_without_occlusion(tmp_path):
    """RGB 0, alpha 255 exactly (117 reads the alpha as ambient occlusion); needs no encoder and no cache."""
    data = textures.solid_bc7(tmp_path / "m_0.dds", (0, 0, 0, 255)).read_bytes()
    assert textures.dds_info(data) == (4, 4, 3, textures.DXGI_BC7) and len(data) == 148 + 3 * 16
    assert len({data[148 + 16 * i : 164 + 16 * i] for i in range(3)}) == 1
    decoder = pytest.importorskip("texture2ddecoder")
    bgra = np.frombuffer(decoder.decode_bc7(data[148:164], 4, 4), np.uint8).reshape(4, 4, 4)
    assert bgra[..., :3].max() == 0 and bgra[..., 3].min() == 255


def test_a_solid_colour_without_a_seven_bit_form_is_left_to_the_encoder():
    assert bc7encode.solid_block((0, 0, 0, 255)) is not None and bc7encode.solid_block((255, 0, 127, 3)) is None
    assert bc7encode.solid_block((255, 129, 0, 77)) is not None


@needs_encoder
def test_convert_dds_goes_from_the_dds_to_bc7_without_a_png(tmp_path, monkeypatch):
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(textures, "dds_to_png", lambda *a: pytest.fail("a PNG was made"))
    monkeypatch.setattr(textures, "run_tool", lambda *a, **k: pytest.fail("texconv was started"))
    block = struct.pack("<HHI", 0xF800, 0x001F, 0b11_10_01_00)
    (tmp_path / "t.dds").write_bytes(legacy_dds(b"DXT1", 4, 4, block))
    textures.convert_dds(tmp_path / "t.dds", tmp_path / "a_0.dds", is_srgb=True)
    textures.convert_dds(tmp_path / "t.dds", tmp_path / "b_0.dds", is_srgb=True)  # from the cache
    assert (tmp_path / "a_0.dds").read_bytes() == (tmp_path / "b_0.dds").read_bytes()
    assert textures.dds_info((tmp_path / "a_0.dds").read_bytes()) == (4, 4, 3, textures.DXGI_BC7_SRGB)
