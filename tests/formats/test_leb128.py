"""``formats.leb128``: the one coder of the counts and sizes three file formats share."""

import pytest

from annotoolbox.formats import leb128


@pytest.mark.parametrize(
    ("value", "coded"),
    [(0, b"\x00"), (1, b"\x01"), (127, b"\x7f"), (128, b"\x80\x01"), (300, b"\xac\x02"), (624485, b"\xe5\x8e\x26")],
)
def test_a_number_codes_to_the_known_bytes_and_back(value, coded):
    assert leb128.encode(value) == coded
    assert leb128.decode(coded) == (value, len(coded))


def test_decode_starts_where_it_is_told_and_says_where_it_ended():
    data = b"\xff" + leb128.encode(300) + b"\x05"
    assert leb128.decode(data, 1) == (300, 3)
    assert leb128.decode(data, 3) == (5, 4)


def test_a_limit_ends_a_number_that_never_clears_its_top_bit():
    assert leb128.decode(b"\x81\x82\x83\x84\x85\x86", most=5) == (1 | 2 << 7 | 3 << 14 | 4 << 21 | 5 << 28, 5)
    assert leb128.decode(b"\x81\x02\x83", most=5) == (1 | 2 << 7, 2)


def test_bytes_that_end_inside_a_number_are_an_index_error():
    with pytest.raises(IndexError):
        leb128.decode(b"\x80\x80")


def test_encode_refuses_a_negative_number_instead_of_looping():
    with pytest.raises(ValueError):
        leb128.encode(-1)
