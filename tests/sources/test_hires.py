"""The higher-resolution source folder: a key listed by ``keys`` is also found by ``find`` (any suffix casing)."""

from __future__ import annotations

import pytest

from annotoolbox.sources import hires


def test_upper_case_suffix_is_listed_and_found(tmp_path):
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "A.PNG").write_bytes(b"x")
    source = hires.CHiresSource(tmp_path)
    assert source.keys("icons") == ["A"]
    assert source.find("icons", "A") == tmp_path / "icons" / "A.PNG"


def test_first_suffix_of_the_list_wins(tmp_path):
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "7.TGA").write_bytes(b"x")
    (tmp_path / "icons" / "7.png").write_bytes(b"x")
    assert hires.CHiresSource(tmp_path).find("icons", 7).name == "7.png"


def test_missing_key_and_folder_give_none(tmp_path):
    assert hires.CHiresSource(tmp_path).find("icons", "x") is None


def test_option_must_be_a_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        hires.from_option(tmp_path / "missing")
