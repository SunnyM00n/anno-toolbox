"""JSON in pieces: the same text and data as the json module, made and read a little at a time."""

import json

import pytest

from annotoolbox.infra import piecejson

DOC = {
    "width": 3,
    "heights": "AAAA",
    "objects": [{"key": f"o{k}", "position": [k, 0.5, -k], "tags": {"a": [1, 2]}} for k in range(2500)],
    "forest": {"tiles": [[k, k + 1] for k in range(3000)], "models": ["a.cfg"]},
    "nested": {"deep": {"deeper": [[1, [2, {"x": None}]]]}},
    "empty": {"list": [], "dict": {}},
    "text": 'quotes " and \\ and é',
}


def test_the_pieces_make_the_text_json_makes_and_read_back_the_data():
    text = piecejson.dumps(DOC)
    assert text == json.dumps(DOC)
    assert piecejson.loads(text) == DOC
    spaced = json.dumps(DOC, indent=2)  # any JSON text, not only its own
    assert piecejson.loads(spaced) == DOC
    assert piecejson.loads(" [1, 2] ") == [1, 2] and piecejson.loads("7") == 7


@pytest.mark.parametrize("bad", ['{"a": 1,}', "[1 2]", '{"a" 1}', "[1, 2", '{"a": 1} x', "{1: 2}"])
def test_text_that_is_not_json_is_refused(bad):
    with pytest.raises(ValueError):
        piecejson.loads(bad)
