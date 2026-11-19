"""BC7 writing (textures.png_to_bc7): the cache, the encoder in the key, the texconv CPU fallback."""

import struct
from pathlib import Path

import pytest

from annotoolbox.infra.tools import ToolResult
from annotoolbox.models import bc7encode, ddsdecode, textures

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")


def fake_texconv(calls: list):
    def run(exe, args, cwd=None, timeout=None):
        calls.append(list(args))
        out = Path(args[args.index("-o") + 1]) / (Path(args[-1]).stem + ".dds")
        out.write_bytes(b"DDS " + Path(args[-1]).read_bytes())
        return ToolResult(0, "", "")

    return run


def png(tmp_path, colour=(10, 20, 30, 255), name="a.png"):
    path = tmp_path / name
    Image.new("RGBA", (8, 8), colour).save(path)
    return path


@pytest.fixture
def fallback(tmp_path, monkeypatch):
    """texconv as the encoder, a stand-in that records its arguments."""
    exe = tmp_path / "texconv.bin"
    exe.write_bytes(b"x")
    calls: list = []
    monkeypatch.setenv(textures.ENCODER_ENV, "texconv")
    monkeypatch.setattr(textures, "require_tool", lambda name: exe)
    monkeypatch.setattr(textures, "run_tool", fake_texconv(calls))
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    return calls


@pytest.fixture
def cached(tmp_path, monkeypatch):
    """The in-process encoder with a cache of its own, the calls counted."""
    if not bc7encode.available():
        pytest.skip("the in-process encoder (etcpak) is not installed")
    monkeypatch.delenv(textures.ENCODER_ENV, raising=False)
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    calls: list = []
    real = bc7encode.encode_dds
    monkeypatch.setattr(bc7encode, "encode_dds", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    return calls


def test_a_texture_stopped_mid_write_leaves_no_half_file(tmp_path, monkeypatch):
    """Every texture writer skips a file that exists, so a run killed while writing must not leave a truncated
    texture behind for the next run to keep (the game would draw it as noise or not at all)."""
    from annotoolbox.infra import buildcache

    def half_then_fail(path, data):
        with open(path, "wb") as f:
            f.write(data[: len(data) // 2])
        raise OSError("stopped")

    real_fdopen = buildcache.os.fdopen

    def fdopen_half(fd, mode="r", *args, **kwargs):
        inner = real_fdopen(fd, mode, *args, **kwargs)

        class Half:
            def write(self, data):
                inner.write(data[: len(data) // 2])
                raise OSError("stopped")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                inner.close()
                return False

        return Half()

    monkeypatch.setattr(Path, "write_bytes", half_then_fail)
    monkeypatch.setattr(buildcache.os, "fdopen", fdopen_half)
    for write in (
        lambda dst: textures.write_solid_dds(dst, (1, 2, 3, 4)),
        lambda dst: textures.solid_bc7(dst, (0, 0, 0, 255)),
    ):
        dst = tmp_path / "t_0.dds"
        with pytest.raises(OSError):
            write(dst)
        assert not dst.exists() and not list(tmp_path.glob("*.part"))


def test_a_texture_is_compressed_once_and_then_copied_from_the_cache(tmp_path, cached):
    src = png(tmp_path)
    textures.png_to_bc7(src, tmp_path / "out1.dds")
    textures.png_to_bc7(src, tmp_path / "out2.dds")
    assert len(cached) == 1 and (tmp_path / "out2.dds").read_bytes() == (tmp_path / "out1.dds").read_bytes()
    textures.png_to_bc7(src, tmp_path / "out3.dds", srgb=True)  # another format is another entry
    png(tmp_path, (1, 2, 3, 4))
    textures.png_to_bc7(src, tmp_path / "out4.dds")  # an image with the same name but other bytes is another entry
    textures.png_to_bc7(src, tmp_path / "out5.dds", mips=1)  # so is another mip count
    assert len(cached) == 4 and not list((tmp_path / "cache").rglob("*.part"))


def test_the_encoder_is_part_of_the_cache_key(tmp_path, cached, monkeypatch):
    src = png(tmp_path)
    textures.png_to_bc7(src, tmp_path / "a.dds")
    monkeypatch.setattr(bc7encode, "identity", lambda: "etcpak-next")
    textures.png_to_bc7(src, tmp_path / "b.dds")
    assert len(cached) == 2


def test_a_cache_entry_is_written_through_a_temp_name_of_its_own(tmp_path, cached, monkeypatch):
    """The cache file and the texture itself (``buildcache.write_atomic``): a part name of their own per process."""
    import shutil

    from annotoolbox.infra import buildcache

    src = png(tmp_path)
    moved: list = []
    real = buildcache._replace
    monkeypatch.setattr(buildcache, "_replace", lambda a, b: (moved.append((Path(a).name, Path(b))), real(a, b))[1])
    textures.png_to_bc7(src, tmp_path / "out1.dds")
    shutil.rmtree(tmp_path / "cache")  # a second run meets the same cold entry
    textures.png_to_bc7(src, tmp_path / "out2.dds")
    into_cache = [part for part, dest in moved if dest.is_relative_to(tmp_path / "cache")]
    assert len(into_cache) == 2 and into_cache[0] != into_cache[1] and all(m.endswith(".part") for m in into_cache)
    assert all(part.endswith(".part") for part, _ in moved) and len(moved) == 4  # the two textures too
    assert not list(tmp_path.rglob("*.part"))


def test_texconv_is_the_cpu_fallback_and_gets_our_mip_levels(tmp_path, fallback):
    src = png(tmp_path)
    textures.png_to_bc7(src, tmp_path / "o.dds", srgb=True, srgb_in=True)
    textures.png_to_bc7(src, tmp_path / "p.dds", mips=1)
    first, second = fallback
    assert "-nogpu" in first and first[first.index("-f") + 1] == "BC7_UNORM_SRGB"
    assert second[second.index("-f") + 1] == "BC7_UNORM" and "-bc" in second  # uniform weights for the world's maps
    assert "-m" not in first and "-sepalpha" not in first  # the levels are ours: texconv only compresses


def test_the_texconv_fallback_has_cache_entries_of_its_own(tmp_path, fallback):
    src = png(tmp_path)
    textures.png_to_bc7(src, tmp_path / "a.dds")
    textures.png_to_bc7(src, tmp_path / "b.dds")
    assert len(fallback) == 1 and (tmp_path / "a.dds").read_bytes().startswith(b"DDS ")
    assert textures.encoder_id().startswith("texconv-cpu-")


def _dds_rgba_mips(data: bytes):
    """The mip levels of an uncompressed R8G8B8A8 DDS as (width, bytes) pairs."""
    h, w = struct.unpack_from("<II", data, 12)
    count = struct.unpack_from("<I", data, 28)[0] or 1
    off = 128 + (20 if data[84:88] == b"DX10" else 0)
    out = []
    for m in range(count):
        mw, mh = max(1, w >> m), max(1, h >> m)
        out.append((mw, data[off : off + mw * mh * 4]))
        off += mw * mh * 4
    return out


def test_a_low_gloss_normal_map_keeps_its_normals_in_the_small_mips(tmp_path, monkeypatch):
    """A normal map whose alpha is a low gloss, as on 2070's rocks, kept its RG only in mip 0 before ``-sepalpha``
    (read back by the in-process decoder, so it runs with any encoder and on Linux)."""
    Image = pytest.importorskip("PIL.Image")
    src = tmp_path / "rock_norm.png"
    Image.new("RGBA", (64, 64), (200, 60, 0, 3)).save(src)
    bc7 = textures.png_to_bc7(src, tmp_path / "rock_norm_0.dds", cache=False)
    dds = ddsdecode.DdsFile(bc7.read_bytes())
    mips = [(dds.size_of(k)[0], dds.level(k).tobytes()) for k in range(dds.info.mips)]
    assert len(mips) == 7
    for _width, pixels in mips:
        assert abs(sum(pixels[0::4]) / (len(pixels) / 4) - 200) < 6
        assert abs(sum(pixels[1::4]) / (len(pixels) / 4) - 60) < 6


def test_the_dye_mask_doubles_2070s_mask_green_into_grey(tmp_path):
    from PIL import Image

    src = tmp_path / "mask.png"
    Image.frombytes("RGBA", (4, 1), bytes([9, 0, 0, 255, 9, 64, 0, 255, 0, 128, 0, 255, 0, 255, 0, 255])).save(src)
    out = textures.dye_mask_png(src, tmp_path / "dye.png")
    data = Image.open(out).convert("RGBA").tobytes()
    pixels = [tuple(data[i : i + 4]) for i in range(0, len(data), 4)]
    assert pixels == [(0, 0, 0, 255), (128, 128, 128, 255), (255, 255, 255, 255), (255, 255, 255, 255)]
    Image.new("RGBA", (2, 2), (200, 0, 30, 255)).save(src)  # reflection mask only: no player colour
    assert textures.dye_mask_png(src, tmp_path / "none.png") is None


def test_2070s_specular_mask_becomes_the_gloss_that_gives_its_highlight_peak():
    """gloss = 1 - (F Vis N.L / (40 A m))^(1/4) from 2070's 10 x mask x (N.H)^100 (rendering-2070 7.4): weak masks
    stay matte, a mid mask gets a visible glint, the strongest no longer the pin-point 117 drew for gloss = mask."""
    # under 2070's own sun (47.9 degrees) and near camera step (0.95557 rad), stopgaps area 5
    assert textures.GLOSS_P == 0.25 and round(textures.GLOSS_K, 4) == 0.0103
    gloss = textures.gloss_from_spec([0.0, 0.01, 0.045, 0.1, 0.36, 0.8, 1.0])
    assert list(gloss.round(2)) == [0.0, 0.0, 0.31, 0.43, 0.59, 0.66, 0.68]
    assert all(b >= a for a, b in zip(gloss, gloss[1:], strict=False))


def test_the_gloss_map_keeps_the_normal_and_rewrites_only_the_alpha(tmp_path):
    from PIL import Image

    src = tmp_path / "wall_norm.png"
    Image.frombytes("RGBA", (2, 1), bytes([200, 60, 0, 0, 128, 128, 7, 92])).save(src)
    out = textures.spec_to_gloss_png(src, tmp_path / "wall_norm_gloss.png")
    data = Image.open(out).convert("RGBA").tobytes()
    assert data[:3] == bytes([200, 60, 0]) and data[4:7] == bytes([128, 128, 7])  # normal and glow as stored
    assert data[3] == 0 and data[7] == round(float(textures.gloss_from_spec(92 / 255)) * 255)


def _cube(faces) -> bytes:
    """An uncompressed 2x2 R8G8B8A8 cube DDS (legacy header) with one colour per face."""
    header = b"DDS " + struct.pack("<II", 124, 0) + struct.pack("<II", 2, 2) + bytes(124 - 12)
    return header + b"".join(bytes(rgba) * 4 for rgba in faces)


def test_the_environment_cube_mean_averages_all_six_faces():
    faces = [(255, 0, 0, 255), (0, 0, 0, 255), (0, 255, 0, 255), (0, 0, 0, 255), (0, 0, 255, 255), (0, 0, 0, 255)]
    assert textures.cube_mean(_cube(faces)) == pytest.approx((1 / 6, 1 / 6, 1 / 6))
    with pytest.raises(textures.TextureConversionError):
        textures.cube_mean(_cube(faces)[:-8])


def test_the_reflection_is_baked_into_the_diffuse_where_the_mask_says(tmp_path):
    """2070: lerp(albedo, albedo x cube x 1.5, mask red), here with the cube's mean colour."""
    from PIL import Image

    diff, mask = tmp_path / "d.png", tmp_path / "m.png"
    Image.new("RGBA", (2, 2), (200, 100, 50, 255)).save(diff)
    Image.frombytes("RGBA", (2, 1), bytes([0, 0, 0, 255, 255, 0, 0, 255])).resize((2, 2)).save(mask)
    out = textures.reflection_baked_png(diff, mask, (0.4, 0.5, 0.6), tmp_path / "baked.png")
    px = Image.open(out).convert("RGBA").load()
    assert px[0, 0] == (200, 100, 50, 255)  # no reflection: as painted
    assert px[1, 0] == (120, 75, 45, 255)  # 200 x 0.6, 100 x 0.75, 50 x 0.9: darker and cooler, alpha kept


def test_format_problem_names_what_differs(bc7_dds):
    assert textures.format_problem(bc7_dds(8, 8, 4, True), bc7_dds(8, 8, 4, True)) is None
    assert textures.format_problem(bc7_dds(8, 8, 4, True), bc7_dds(8, 8, 1, False)) == (
        "mips 4 -> 1, DXGI format 99 -> 98"
    )
    assert textures.format_problem(bc7_dds(8, 8), bc7_dds(8, 12)) == "height 8 -> 12"


def test_bc7_like_follows_the_original(bc7_dds, fake_bc7):
    np = pytest.importorskip("numpy")
    pytest.importorskip("PIL.Image")
    px = np.zeros((8, 8, 4), np.uint8)
    out = textures.bc7_like(px, bc7_dds(8, 8, 4, True))
    assert textures.dds_info(out) == (8, 8, 4, textures.DXGI_BC7_SRGB) and fake_bc7 == [(True, 4)]
    out = textures.bc7_like(px, bc7_dds(8, 8, 1, False))
    assert textures.dds_info(out) == (8, 8, 1, textures.DXGI_BC7) and fake_bc7[-1] == (False, 1)
    with pytest.raises(textures.TextureConversionError, match="8x8 original"):
        textures.bc7_like(np.zeros((8, 4, 4), np.uint8), bc7_dds(8, 8))


def test_bc7_like_with_the_real_texconv_keeps_format_and_nearly_every_alpha(tmp_path, monkeypatch):
    """The header equals the original's and a soft alpha survives within BC7's error (any encoder)."""
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image")
    monkeypatch.setattr(textures, "CACHE", tmp_path / "cache")
    ramp = np.arange(64, dtype=np.uint8).reshape(8, 8) * 4
    px = np.dstack(
        [np.full((8, 8), 120, np.uint8), np.full((8, 8), 150, np.uint8), np.full((8, 8), 200, np.uint8), ramp]
    )
    src = tmp_path / "a.png"
    Image.fromarray(px, "RGBA").save(src)
    for srgb, dxgi, mips in ((True, textures.DXGI_BC7_SRGB, 4), (False, textures.DXGI_BC7, 1)):
        dds = textures.png_to_bc7(src, tmp_path / f"o{mips}_0.dds", srgb=srgb, srgb_in=srgb, mips=mips, cache=False)
        assert textures.dds_info(dds.read_bytes()) == (8, 8, mips, dxgi)
        back = textures.dds_to_png(dds, tmp_path / f"back{mips}")
        got = np.asarray(Image.open(back).convert("RGBA"))
        assert np.abs(got[..., 3].astype(int) - ramp).max() <= 8
        assert np.abs(got[..., :3].astype(int) - px[..., :3]).max() <= 3
