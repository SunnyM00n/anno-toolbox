"""Textures without texconv (docs/texture-pipeline.md "The built-in encoders"): the numpy BC7 (mode 6), BC1, BC3, BC4 and
BC5 encoders, the decode of the newer formats, the choice between texconv and the built-in path. Round trips on
synthetic pictures with PSNR bounds; the decoders of ``texture2ddecoder`` are the independent reference where it is
installed. Nothing here needs texconv, so these tests run on Linux as on Windows."""

import struct

import pytest

from annotoolbox.infra import tools
from annotoolbox.models import (
    bc4encode,
    bc7decode,
    bc7encode,
    bc7simple,
    blockencode,
    ddsdecode,
    mipchain,
    texbackend,
    textures,
)

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")


def picture(h: int, w: int, seed: int = 3, alpha: bool = True):
    """A smooth colour field with a hard-edged patch and a little noise: what a texture looks like to an encoder."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w]
    px = np.stack(
        [128 + 100 * np.sin(x / 9.0), 128 + 100 * np.cos(y / 7.0), x * 255 // max(w, 1), 255 - y * 255 // max(h, 1)], -1
    ).astype(np.float64)
    px[: h // 2, : w // 3, :3] = (200, 30, 30)
    px[..., :3] += rng.normal(0, 3, px[..., :3].shape)
    if not alpha:
        px[..., 3] = 255
    return np.clip(px, 0, 255).astype(np.uint8)


def psnr(a, b, channels=(0, 1, 2, 3)) -> float:
    """The lowest PSNR over the channels (99 where one is exact)."""
    worst = 99.0
    for c in channels:
        mse = float(((a[..., c].astype(np.float64) - b[..., c]) ** 2).mean())
        worst = min(worst, 99.0 if mse == 0 else 10 * np.log10(255.0**2 / mse))
    return worst


@pytest.fixture
def no_texconv(monkeypatch):
    """The built-in path alone: texconv forbidden (the setting) and any run of it a failure."""
    monkeypatch.setenv(texbackend.BACKEND_ENV, "builtin")
    monkeypatch.setenv("ANNO_BC7_GPU", "none")
    monkeypatch.setattr(textures, "run_tool", lambda *a, **k: pytest.fail("texconv was started"))


# BC7, mode 6


@pytest.mark.parametrize("size", [(64, 64), (37, 51), (4, 4), (1, 1), (3, 130)])
def test_bc7_mode6_round_trips_a_picture_within_a_bound(size):
    h, w = size
    px = picture(h, w)
    data = bc7simple.encode_level(px)
    assert len(data) == -(-h // 4) * -(-w // 4) * 16
    got = bc7decode.decode_bc7(data, w, h)
    assert psnr(px, got) > (28 if min(size) > 4 else 10)


def test_bc7_mode6_blocks_are_mode_six_and_the_anchor_index_has_its_top_bit_clear():
    data = np.frombuffer(bc7simple.encode_level(picture(32, 32)), np.uint8).reshape(-1, 16)
    assert ((data[:, 0] & 0x7F) == 0x40).all()  # six zero bits, then the one: mode 6
    hi = int.from_bytes(data[0, 8:], "little")
    assert (hi >> 1) & 7 < 8
    assert (np.frombuffer(data.tobytes(), "<u8").reshape(-1, 2)[:, 1] >> np.uint64(4)).shape[0] == len(data)


def test_bc7_mode6_keeps_flat_blocks_within_one_and_opaque_alpha_exact():
    px = np.full((8, 8, 4), (100, 151, 200, 255), np.uint8)
    got = bc7decode.decode_bc7(bc7simple.encode_level(px), 8, 8)
    assert (got[..., 3] == 255).all()
    assert np.abs(got[..., :3].astype(int) - px[..., :3]).max() <= 1
    clear = np.zeros((4, 4, 4), np.uint8)
    assert (bc7decode.decode_bc7(bc7simple.encode_level(clear), 4, 4)[..., 3] == 0).all()


def test_bc7_mode6_is_exact_on_a_gradient_block():
    px = np.zeros((4, 4, 4), np.uint8)
    px[..., 3] = 255
    px[..., 0] = np.linspace(40, 200, 16).reshape(4, 4)  # one line through colour space
    got = bc7decode.decode_bc7(bc7simple.encode_level(px), 4, 4)
    assert np.abs(got.astype(int) - px).max() <= 6  # sixteen levels over 160 steps


def test_bc7_mode6_is_deterministic_and_the_reference_decoder_agrees():
    px = picture(24, 40)
    first = bc7simple.encode_level(px)
    assert first == bc7simple.encode_level(px)
    ref = pytest.importorskip("texture2ddecoder")
    raw = ref.decode_bc7(first, 40, 24)  # BGRA
    got = np.frombuffer(raw, np.uint8).reshape(24, 40, 4)[..., [2, 1, 0, 3]]
    assert (got == bc7decode.decode_bc7(first, 40, 24)).all()


def test_bc7_mode6_is_within_a_few_db_of_etcpak_on_a_picture():
    if not bc7encode.available():
        pytest.skip("etcpak is not installed")
    px = picture(128, 128)
    ours = psnr(px, bc7decode.decode_bc7(bc7simple.encode_level(px), 128, 128))
    theirs = psnr(px, bc7decode.decode_bc7(bc7encode.encode_level(px, False), 128, 128))
    assert ours > theirs - 6


@pytest.mark.parametrize("srgb", [False, True])
def test_the_built_in_path_writes_a_bc7_dds_with_mips_and_the_right_format(no_texconv, monkeypatch, srgb):
    monkeypatch.setenv(textures.ENCODER_ENV, "simple")
    px = picture(40, 24)
    data = textures.rgba_to_bc7(px, srgb=srgb, srgb_in=srgb)
    width, height, mips, dxgi = textures.dds_info(data)
    assert (width, height, mips, dxgi) == (24, 40, 6, 99 if srgb else 98)
    dds = ddsdecode.DdsFile(data)
    top = dds.level(0)
    assert psnr(px, top) > 26
    assert dds.level(mips - 1).shape[:2] == (1, 1)


def test_srgb_without_srgb_in_stores_linear_pixels_gamma_encoded(no_texconv, monkeypatch):
    monkeypatch.setenv(textures.ENCODER_ENV, "simple")
    px = np.full((8, 8, 4), (120, 120, 120, 255), np.uint8)
    top = ddsdecode.DdsFile(textures.rgba_to_bc7(px, srgb=True, srgb_in=False)).level(0)
    assert abs(int(top[0, 0, 0]) - 182) <= 2  # linear 120 as sRGB (mipchain.to_srgb)
    same = ddsdecode.DdsFile(textures.rgba_to_bc7(px, srgb=True, srgb_in=True)).level(0)
    assert abs(int(same[0, 0, 0]) - 120) <= 1


def test_png_to_bc7_without_texconv_or_etcpak_runs_the_numpy_encoder(no_texconv, monkeypatch, tmp_path):
    monkeypatch.setattr(bc7encode, "available", lambda: False)
    Image.fromarray(picture(16, 16), "RGBA").save(tmp_path / "a.png")
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    out = textures.png_to_bc7(tmp_path / "a.png", tmp_path / "a_0.dds", mips=1)
    assert textures.bc7_engine() == "simple" and textures.encoder_id().startswith("bc7simple-")
    assert textures.dds_info(out.read_bytes())[3] == 98


# The choice of path


def test_the_bc7_engine_follows_the_settings(monkeypatch, tmp_path):
    exe = tmp_path / "texconv.bin"
    exe.write_bytes(b"x")
    monkeypatch.setenv("ANNO_TEXCONV", str(exe))
    monkeypatch.delenv(textures.ENCODER_ENV, raising=False)
    monkeypatch.delenv(texbackend.BACKEND_ENV, raising=False)
    monkeypatch.setattr(bc7encode, "available", lambda: True)
    assert textures.bc7_engine() == "etcpak"  # no change where etcpak is installed
    monkeypatch.setattr(bc7encode, "available", lambda: False)
    assert textures.bc7_engine() == "texconv"  # etcpak missing, texconv there: as before
    monkeypatch.setenv(texbackend.BACKEND_ENV, "builtin")
    assert textures.bc7_engine() == "simple"
    monkeypatch.setenv(texbackend.BACKEND_ENV, "auto")
    monkeypatch.setenv("ANNO_TEXCONV", str(tmp_path / "nowhere"))
    monkeypatch.setattr(tools, "REPO_TOOLS_DIR", tmp_path / "no-tools")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert textures.bc7_engine() == "simple"  # neither: the numpy encoder
    monkeypatch.setenv(textures.ENCODER_ENV, "simple")
    monkeypatch.setattr(bc7encode, "available", lambda: True)
    assert textures.bc7_engine() == "simple"  # forced, though etcpak is there


def test_texbackend_modes(monkeypatch, tmp_path):
    exe = tmp_path / "texconv.bin"
    exe.write_bytes(b"x")
    monkeypatch.setenv("ANNO_TEXCONV", str(exe))
    monkeypatch.delenv(texbackend.BACKEND_ENV, raising=False)
    assert texbackend.mode() == "auto" and texbackend.texconv() == exe
    assert texbackend.identity() not in ("", f"builtin-{texbackend.BUILTIN_VERSION}")
    monkeypatch.setenv(texbackend.BACKEND_ENV, "nonsense")
    assert texbackend.mode() == "auto"
    monkeypatch.setenv(texbackend.BACKEND_ENV, "BUILTIN")
    assert texbackend.texconv() is None and texbackend.identity() == f"builtin-{texbackend.BUILTIN_VERSION}"
    monkeypatch.setenv(texbackend.BACKEND_ENV, "texconv")
    monkeypatch.setenv("ANNO_TEXCONV", str(tmp_path / "nowhere"))
    monkeypatch.setattr(tools, "REPO_TOOLS_DIR", tmp_path / "no-tools")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(tools.ToolError, match="texconv not found"):
        texbackend.texconv()


def test_converters_need_pillow_and_numpy_but_not_texconv(monkeypatch, tmp_path):
    monkeypatch.setenv("ANNO_TEXCONV", str(tmp_path / "nowhere"))
    monkeypatch.setattr(tools, "REPO_TOOLS_DIR", tmp_path / "no-tools")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    tools.require_converters()
    monkeypatch.setitem(__import__("sys").modules, "numpy", None)
    with pytest.raises(tools.ToolError, match="numpy is not installed"):
        tools.require_converters()


# Decode


def test_dds_to_png_reads_a_bc7_dds_in_numpy(no_texconv, tmp_path):
    px = picture(20, 28)
    src = tmp_path / "x_0.dds"
    src.write_bytes(bc7simple.encode_dds([px], False))
    png = textures.dds_to_png(src, tmp_path / "png")
    got = np.asarray(Image.open(png).convert("RGBA"))
    assert (got == bc7decode.decode_bc7(src.read_bytes()[148:], 28, 20)).all()


def test_dds_to_png_without_the_decoder_or_texconv_says_so(no_texconv, tmp_path):
    src = tmp_path / "odd.dds"
    head = bytearray(bc4encode.dds_header(4, 4, 1, dx10=True))
    head[128:132] = (999).to_bytes(4, "little")  # a DXGI format nothing reads
    src.write_bytes(bytes(head) + bytes(8))
    with pytest.raises(textures.TextureConversionError, match="built-in decoder"):
        textures.dds_to_png(src, tmp_path / "png")


def cube_dds(faces) -> bytes:
    """An uncompressed R8G8B8A8 cube (the six faces one after the other, one level)."""
    h, w = faces[0].shape[:2]
    head = struct.pack("<4sIIIIIII", b"DDS ", 124, 0x100F, h, w, w * 4, 1, 1) + bytes(44)
    head += struct.pack("<II4s5I", 32, 0x41, b"\0\0\0\0", 32, 0xFF, 0xFF00, 0xFF0000, 0xFF000000)
    head += struct.pack("<5I", 0x1008, 0xFE00, 0, 0, 0)
    return head + b"".join(f.tobytes() for f in faces)


def test_the_cube_mean_without_texconv_is_the_mean_of_the_six_faces(no_texconv, tmp_path):
    colours = [(10, 20, 30), (50, 60, 70), (90, 100, 110), (130, 140, 150), (170, 180, 190), (210, 220, 230)]
    faces = [np.full((4, 4, 4), (*c, 255), np.uint8) for c in colours]
    src = tmp_path / "cube.dds"
    src.write_bytes(cube_dds(faces))
    got = textures.env_cube_mean(src)
    assert got == pytest.approx(tuple(np.mean(colours, axis=0) / 255.0))
    assert got == pytest.approx(textures.cube_mean(src.read_bytes()))


# BC4 height maps


def test_png_to_bc4_without_texconv_writes_the_texconv_header_and_a_full_chain(no_texconv, tmp_path):
    grey = (np.add.outer(np.arange(32), np.arange(48)) * 255 // 78).astype(np.uint8)
    Image.fromarray(np.stack([grey] * 3 + [np.full_like(grey, 255)], -1), "RGBA").save(tmp_path / "h.png")
    out = textures.png_to_bc4(tmp_path / "h.png", tmp_path / "out" / "h_0.dds")
    data = out.read_bytes()
    assert data[84:88] == b"DX10" and struct.unpack_from("<I", data, 128)[0] == 80  # BC4_UNORM
    dds = ddsdecode.DdsFile(data)
    assert (dds.info.width, dds.info.height, dds.info.mips) == (48, 32, 6)
    assert psnr(np.stack([grey] * 4, -1), dds.level(0), channels=(0,)) > 40
    assert dds.level(5).shape[:2] == (1, 1)


def test_bc4_round_trips_odd_sizes_and_flat_blocks():
    for h, w in ((7, 9), (1, 1), (16, 16)):
        grey = picture(h, w)[..., 0]
        data = bc4encode.encode_level(grey)
        got = ddsdecode._tile(ddsdecode._bc4_blocks(np.frombuffer(data, np.uint8).reshape(-1, 8))[:, :, None], w, h)[
            ..., 0
        ]
        assert psnr(grey[..., None], got[..., None], channels=(0,)) > 30
    flat = np.full((4, 4), 77, np.uint8)
    assert (ddsdecode._bc4_blocks(np.frombuffer(bc4encode.encode_level(flat), np.uint8).reshape(-1, 8)) == 77).all()


# BC1, BC3, BC5


@pytest.mark.parametrize("kind", ["BC1", "BC3", "BC5"])
@pytest.mark.parametrize("srgb", [False, True])
@pytest.mark.parametrize("size", [(32, 32), (13, 21)])
def test_block_formats_round_trip_with_mips(kind, srgb, size):
    if kind == "BC5" and srgb:
        pytest.skip("BC5 has no sRGB form")
    h, w = size
    px = picture(h, w)
    levels = mipchain.mip_chain(px)
    data = blockencode.encode_dds(levels, kind, srgb)
    dds = ddsdecode.DdsFile(data)
    assert dds.info.mips == len(levels) and (dds.info.width, dds.info.height) == (w, h)
    assert dds.info.srgb == srgb
    got = dds.level(0)
    channels = {"BC1": (0, 1, 2), "BC3": (0, 1, 2, 3), "BC5": (0, 1)}[kind]
    assert psnr(px, got, channels=channels) > (24 if kind != "BC5" else 36)
    if kind == "BC1":
        assert (got[..., 3] == 255).all()
    assert dds.level(len(levels) - 1).shape[:2] == (1, 1)


def test_block_formats_agree_with_the_reference_decoder():
    ref = pytest.importorskip("texture2ddecoder")
    px = picture(16, 16)
    for kind, decode in (("BC1", ref.decode_bc1), ("BC3", ref.decode_bc3), ("BC5", ref.decode_bc5)):
        data = blockencode.ENCODERS[kind](px)
        raw = np.frombuffer(decode(data, 16, 16), np.uint8).reshape(16, 16, 4)[..., [2, 1, 0, 3]]
        ours = ddsdecode._compressed({"BC1": b"DXT1", "BC3": b"DXT5", "BC5": b"ATI2"}[kind], bc7_pad(data), 16, 16)
        channels = [0, 1, 2] if kind != "BC5" else [0, 1]
        assert np.abs(raw[..., channels].astype(int) - ours[..., channels]).max() <= 1


def bc7_pad(data: bytes) -> bytes:
    """Blocks behind a 128-byte stand-in header, where ``ddsdecode._compressed`` reads them."""
    return bytes(128) + data


def test_an_unknown_block_kind_is_refused():
    with pytest.raises(ValueError, match="no built-in encoder"):
        blockencode.encode_dds([picture(4, 4)], "BC6H")


@pytest.mark.skipif(__import__("os").name == "nt", reason="the rule is for the other systems")
def test_a_windows_program_in_tools_is_not_taken_for_a_converter_off_windows(monkeypatch, tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "texconv.exe").write_bytes(b"MZ")
    monkeypatch.setattr(tools, "REPO_TOOLS_DIR", tmp_path / "tools")
    monkeypatch.delenv("ANNO_TEXCONV", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert tools.find_tool("texconv") is None
    assert textures.bc7_engine() in ("etcpak", "simple")
