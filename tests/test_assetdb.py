"""``sources.assetdb``: the number readers every module shares."""

from annotoolbox.sources.assetdb import decimal, number


def test_number_is_whole_and_falls_back():
    assert number("4096") == 4096 and number("1.9") == 1 and number(None, 7) == 7
    assert number("", 3) == 3 and number("x", 2) == 2


def test_decimal_keeps_the_fraction_and_the_callers_default():
    assert decimal("0.5") == 0.5 and decimal(2) == 2.0
    assert decimal(None, 1.0) == 1.0 and decimal("", 1.0) == 1.0 and decimal("x") == 0.0
