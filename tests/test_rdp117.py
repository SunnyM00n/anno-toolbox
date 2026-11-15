"""117's baked particle files: the byte layout the engine reads (engine-particles "Particles")."""

from __future__ import annotations

import math
import re
import struct
from dataclasses import replace

import pytest

from annotoolbox.formats import rdp117
from annotoolbox.formats.rdp117 import (
    BakedParticle,
    RdpMaterial,
    dequantise,
    parse_rdp,
    quantise,
    texture_file,
    write_rdp,
)
from fakes import installed


def _particle(start=0, ticks=4, material=0, x0=-1.0, x1=1.0, color=(1.0, 0.5, 0.25, 1.0)):
    xs = [x0 + (x1 - x0) * k / (ticks - 1) for k in range(ticks)]
    return BakedParticle(
        start=start,
        end=start + ticks - 1,
        material=material,
        pos=[(x, 2 * x, -x) for x in xs],
        size=[(0.5, 0.5, 0.5)] * ticks,
        rot=[(0.0, math.pi / 2, 0.0)] * ticks,
        color=[color] * ticks,
    )


def _file(particles=None, materials=1, frames=8, looped=False):
    return quantise(
        particles or [_particle()], [RdpMaterial(order=i) for i in range(materials)], "tex\\a.psd", frames, looped
    )


def _blob(data: bytes, name: str) -> bytes:
    m = re.search(rb"<" + name.encode() + rb">CDATA\[", data)
    (length,) = struct.unpack_from("<I", data, m.end())
    return data[m.end() + 4 : m.end() + 4 + length]


def test_positions_are_signed_sixteen_bit_fractions_of_the_box():
    r = _file()
    assert min(p[0] for p in r.positions) == -32767 and max(p[0] for p in r.positions) == 32767
    assert min(p[1] for p in r.positions) == -32767 and max(p[2] for p in r.positions) == 32767
    ticks = struct.unpack("<12h", _blob(write_rdp(r), "T_Positions"))
    assert ticks[:3] == (-32767, -32767, 32767)  # a position below the centre is a negative number
    back = parse_rdp(write_rdp(r))
    assert back.positions == r.positions
    first = dequantise(back)[0].pos
    assert first[0] == pytest.approx((-1.0, -2.0, 1.0), abs=1e-3) and first[-1] == pytest.approx(
        (1.0, 2.0, -1.0), abs=1e-3
    )


def test_colours_are_stored_blue_green_red_alpha():
    r = _file([_particle(color=(1.0, 0.5, 0.25, 1.0))])
    middle = r.colors[1]
    assert middle == (64, 128, 255, 255)  # b, g, r, a of (1.0, 0.5, 0.25, 1.0)... r=255, g=128, b=64
    assert dequantise(r)[0].color[1] == pytest.approx((1.0, 0.5, 0.25, 1.0), abs=1 / 255)


def test_first_and_last_tick_of_every_particle_have_alpha_zero():
    r = _file([_particle(ticks=5), _particle(start=1, ticks=3)])
    alphas = [c[3] for c in r.colors]
    assert alphas == [0, 255, 255, 255, 0, 0, 255, 0]
    assert all(c[3] > 0 for c in r.colors[1:4])


def test_particles_are_written_sorted_by_material_with_their_ticks():
    a = _particle(material=1, x0=0.0, x1=0.0, ticks=3)  # at x = 0
    b = _particle(material=0, x0=1.0, x1=1.0, ticks=3)  # at x = 1
    r = _file([a, b, a, b], materials=2)
    assert r.particle_materials == [0, 0, 1, 1]
    seen = [round(p.pos[0][0]) for p in dequantise(r)]
    assert seen == [1, 1, 0, 0]  # the ticks moved with their particles
    back = parse_rdp(write_rdp(r))
    assert back.particle_materials == [0, 0, 1, 1]


def test_an_unsorted_file_is_refused():
    r = _file([_particle(material=0), _particle(material=1)], materials=2)
    with pytest.raises(ValueError, match="sorted"):
        write_rdp(replace(r, particle_materials=[1, 0]))


def test_rotation_bytes_are_a_turn_in_255ths():
    r = _file([_particle()])
    assert r.rotations[0] == (0, round(255 / 4), 0)  # a quarter turn about y
    assert dequantise(r)[0].rot[0][1] == pytest.approx(math.pi / 2, abs=2 * math.pi / 255)


def test_the_header_carries_every_field_the_engine_requires():
    data = write_rdp(_file(looped=True))
    head = data[: data.index(b"<P_Starts>")].decode("latin1")
    for tag in ("FrameCount", "FrameTime", "Looped", "SelfOcclusion", "Lighting", "MaxScale", "TexturePath"):
        assert f"<{tag}>" in head
    assert "<Looped>1</>" in head
    assert "<Center>" in head and "<Extents>" in head
    material = head[head.index("<Material>") :]
    assert "<ViewAligned>1</>" in material and "<Order>0</>" in material
    assert "<cropUVWH>" in material and "<Additive>0</>" in material


def test_byte_fields_of_a_material_are_written_as_plain_integers():
    data = write_rdp(quantise([_particle()], [RdpMaterial(order=35, color_boost=21)], "t.psd", 8, False))
    assert b"<Order>35</>" in data and b"<ColorBoost>21</>" in data


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"frame_count": 257}, "frame count"),
        ({"frame_count": 0}, "frame count"),
        ({"materials": []}, "materials"),
        ({"materials": [RdpMaterial()] * 9}, "materials"),
        ({"materials": [RdpMaterial(order=256)]}, "bytes"),
        ({"materials": [RdpMaterial(view_aligned=3)]}, "ViewAligned"),
        ({"texture": ""}, "texture"),  # with no material naming one either
        ({"starts": [0, 1]}, "differ"),
        ({"ends": [9]}, "outside the frames"),
        ({"particle_materials": [1]}, "material the file does not have"),
        ({"positions": [(0, 0, 0)]}, "tick tracks"),
    ],
)
def test_a_file_the_engine_would_refuse_is_not_written(change, message):
    with pytest.raises(ValueError, match=message):
        write_rdp(replace(_file(), **change))


def test_textures_named_on_the_materials_and_unmodelled_header_tags_are_written_back():
    """289 of 117's files carry no TexturePath and name each material's texture in MovieTexPath; header tags the
    record does not model (NormalMapPath) are kept."""
    movie = RdpMaterial(movie_texture="maps/wind.psd", movie_fps=-1.0)
    r = replace(_file(), texture="", materials=[movie], extra={"NormalMapPath": "maps/n.psd"})
    data = write_rdp(r)
    assert b"<TexturePath>" not in data and b"<NormalMapPath>maps/n.psd</>" in data
    back = parse_rdp(data)
    assert back.materials == [movie] and back.extra == r.extra and back.texture == ""
    assert parse_rdp(write_rdp(_file())).extra == {}  # a vector's x, y, z are not header tags


@pytest.mark.parametrize("game", ["anno117", "anno1800", "anno2205"])
def test_the_games_particle_files_read_and_write_back(game: str):
    """Field for field, floats to the writer's six decimals; the vanilla files the engine misreads (particles not
    sorted by material, more than 8 materials) are refused, not written."""
    files = installed(game)
    if files is None:
        pytest.skip(f"needs the installed {game}")
    paths = [
        p for p in (files.iter_paths("data/") if game == "anno117" else files.iter_paths("")) if p.endswith(".rdp")
    ]
    read = files.read if game == "anno117" else files.read_bytes
    refused = 0
    for path in paths:
        try:
            r = parse_rdp(read(path))
        except ValueError:
            refused += 1  # 2205's one electricity file is another format
            continue
        try:
            back = parse_rdp(write_rdp(r))
        except ValueError as e:
            assert "sorted" in str(e) or "materials" in str(e), path
            refused += 1
            continue
        for name in ("starts", "ends", "particle_materials", "positions", "colors", "materials", "extra", "texture"):
            assert getattr(back, name) == getattr(r, name), (path, name)
        assert back.center == pytest.approx(r.center, abs=1e-5) and back.max_scale == pytest.approx(
            r.max_scale, abs=1e-5
        )
    assert refused <= 12


def test_a_looped_particle_wraps_past_the_last_frame():
    p = _particle(start=6, ticks=5)  # frames 6, 7, 0, 1, 2 of 8
    p.end = 2
    r = _file([p], frames=8, looped=True)
    assert r.ticks_of(0) == 5
    assert parse_rdp(write_rdp(r)).ends == [2]


def test_texture_reference_the_engine_loads():
    assert texture_file("data\\a\\b.dds") == "data\\a\\b.dds"  # a .dds stays as it is
    assert texture_file("data/a/b.psd") == "data/a/b_0.dds"
    assert texture_file("data/a/b.png") == "data/a/b_0.dds"
    assert texture_file("data/a/b") == "data/a/b_0.dds"


def test_a_degenerate_box_stays_readable():
    r = _file([_particle(x0=0.5, x1=0.5)])
    assert all(e >= 1e-6 for e in r.extents)
    assert dequantise(r)[0].pos[0][0] == pytest.approx(0.5)


def test_constants_match_the_engine_limits():
    assert rdp117.MAX_FRAMES == 256 and rdp117.MAX_MATERIALS == 8
