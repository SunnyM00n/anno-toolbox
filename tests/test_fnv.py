"""``formats.fnv``: the three hashes the games name things by, against known vectors."""

import pytest

from annotoolbox.formats import bnk, fnv


def test_fnv_1a_32_matches_the_published_vectors():
    assert fnv.fnv(b"") == 0x811C9DC5
    assert fnv.fnv(b"a") == 0xE40C292C and fnv.fnv(b"foobar") == 0xBF9CF968


def test_fnv_1_32_multiplies_before_it_xors():
    assert fnv.fnv(b"a", xor_first=False) == 0x050C5D7E and fnv.fnv(b"foobar", xor_first=False) == 0x31F0B262


def test_fnv_1a_64_matches_the_published_vectors():
    assert fnv.fnv(b"", 64) == 0xCBF29CE484222325
    assert fnv.fnv(b"a", 64) == 0xAF63DC4C8601EC8C and fnv.fnv(b"foobar", 64) == 0x85944171F73967E8


def test_a_width_other_than_32_or_64_is_refused():
    with pytest.raises(KeyError):
        fnv.fnv(b"a", 16)


def test_wwise_ids_hash_the_lower_cased_name_with_fnv_1():
    assert bnk.fnv1_32("Play_Fire") == bnk.fnv1_32("play_fire") == fnv.fnv(b"play_fire", xor_first=False)
