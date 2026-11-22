"""The particle editor on every effect file the installed games ship (``ANNO117_DIR``, ``ANNO1800_DIR``,
``ANNO2205_DIR``, ``ANNO2070_DIR``, ``ANNO1404_DIR``, ``ANNO1701_DIR``; each game skipped without its variable): each
file opens, writes back byte for byte, makes its tree, and the game's own reader still reads it; an edit and its undo
come back to the same bytes; the curves read as the viewer's simulation reads them (docs/particle-editor.md)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.formats import efc1701
from annotoolbox.formats.rdp117 import parse_rdp
from annotoolbox.models.particles2070 import parse_efc
from annotoolbox.particleedit import outline
from annotoolbox.particleedit.curves import SXmlCurve, xml_curves
from annotoolbox.particleedit.effectdoc import BRACE, EFFECT_ENDINGS, RDP, XML, CEffectDoc
from annotoolbox.sources import gametree

GAMES = ("anno117", "anno1800", "anno2205", "anno2070", "anno1404", "anno1701")
SYNTAX = {"anno117": RDP, "anno1800": RDP, "anno2205": RDP, "anno2070": XML, "anno1404": XML, "anno1701": BRACE}


@pytest.fixture(scope="module", params=GAMES)
def game(request, tmp_path_factory):
    folder = os.environ.get(INSTALL_VARS[request.param])
    if not folder or not Path(folder).is_dir():
        pytest.skip(f"needs {INSTALL_VARS[request.param]}")
    tree = gametree.open_game(request.param, Path(folder), tmp_path_factory.mktemp("index"))
    paths = sorted(p for p in tree.m_paths if p.endswith(EFFECT_ENDINGS))
    assert paths
    return request.param, tree, paths


def _read_back(syntax: str, data: bytes) -> int:
    """How many emitters (or particles) the game's own reader of this syntax finds in the bytes."""
    if syntax == RDP:
        return len(parse_rdp(data).starts)
    if syntax == XML:
        return len(parse_efc(data.decode("utf-8", "replace")))
    return len(efc1701.read_efc(data.decode("latin-1")))


def test_every_shipped_effect_reads_and_writes_back_unchanged(game):
    key, tree, paths = game
    problems = []
    for path in paths:
        data = tree.read(path)
        effect = CEffectDoc.open(tree, key, path)
        if not data:
            assert not effect.editable
            continue
        if effect.m_syntax != SYNTAX[key] or not effect.m_exact or effect.to_bytes() != data:
            problems.append(path)
            continue
        assert outline.outline(effect), path
        _read_back(effect.m_syntax, data)
    assert not problems, f"{len(problems)} of {len(paths)} do not write back: {problems[:5]}"


def test_an_edit_and_its_undo_come_back_to_the_same_bytes(game):
    key, tree, paths = game
    for path in paths[:: max(1, len(paths) // 12)]:
        data = tree.read(path)
        if not data:
            continue
        effect = CEffectDoc.open(tree, key, path)
        row = next((r for item in outline.outline(effect) for r in item.rows
                    if r.field_info.kind == "number" and not r.read_only), None)  # fmt: skip
        if row is None:
            continue
        effect.apply(outline.set_row(effect, row, "1"))
        assert effect.to_bytes() != data or row.value == "1"
        _read_back(effect.m_syntax, effect.to_bytes())
        effect.m_undo.undo()
        assert effect.to_bytes() == data, path


def test_the_curves_read_as_the_viewers_simulation_reads_them(game):
    key, tree, paths = game
    if SYNTAX[key] != XML:
        pytest.skip("curves of 2070 and 1404")
    for path in paths[::7]:
        effect = CEffectDoc.open(tree, key, path)
        for node, read in zip((c for c in effect.m_doc.root.children if c.children),
                              parse_efc(tree.read(path).decode("utf-8", "replace")), strict=False):  # fmt: skip
            for name in xml_curves(node):
                ours, theirs = SXmlCurve(node, name), read.curve(name)
                if theirs is not None and theirs.knots:
                    assert ours.at(0.37) == pytest.approx(theirs.at(0.37), abs=1e-4), (path, name)
