"""The Saves app over the saves found on this computer, read only: every save opens, its summary and fields are read,
and a write without a change is the original's bytes (written into the test's own folder, from a copy). Each game's
test runs only when its install variable is set (``ANNO117_DIR``, ``ANNO2070_DIR``, ...) and its save folder holds
saves; the originals are read once and never written."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.fakes import INSTALL_VARS

from annotoolbox.infra.i18n import tr
from annotoolbox.saveedit import container, edits, locations, nodes, records, summary

LIMIT = 3  # saves per game: the newest, the oldest, the largest


def chosen(game: str) -> list[locations.SSaveRef]:
    if not os.environ.get(INSTALL_VARS[game]):
        pytest.skip(f"needs {INSTALL_VARS[game]}")
    found = locations.find(game)
    if not found:
        pytest.skip(f"no {game} saves on this computer")
    picks = [found[0], found[-1], max(found, key=lambda r: r.size_bytes)]
    return list({r.path: r for r in picks}.values())[:LIMIT]


@pytest.mark.parametrize("game", list(locations.LOCATIONS))
def test_every_save_found_opens_and_is_summarised(game: str):
    for ref in chosen(game):
        save = container.open_file(ref.path, game)
        s = summary.summarize(save)
        assert s.m_sections and tr(s.m_sections[-1].title) == "Parts"
        assert all(not p.m_error for p in save.parts), [p.m_error for p in save.parts]
        assert nodes.roots(save) and nodes.roots(save)[0].children() is not None
        if game in edits.EDITABLE:
            assert edits.fields(save)


@pytest.mark.parametrize("game", list(edits.EDITABLE))
def test_an_unchanged_write_of_a_copy_is_the_original(game: str, tmp_path: Path):
    ref = min(chosen(game), key=lambda r: r.size_bytes)
    copy = tmp_path / ref.path.name
    shutil.copy2(ref.path, copy)
    save = container.open_file(copy, game)
    written = edits.write_beside(save, {})
    assert written.parent == tmp_path and written.read_bytes() == ref.path.read_bytes()


def test_the_117_islands_read_from_the_newest_save():
    ref = chosen("anno117")[0]
    islands = summary.sessions(container.open_file(ref.path, "anno117"))
    assert islands is not None and islands.table.rows


def test_the_117_stocks_decode_to_the_games_goods_and_get_names(tmp_path: Path):
    from annotoolbox.saveedit import names
    from annotoolbox.suite.settings import CSettings

    install = os.environ["ANNO117_DIR"] if os.environ.get("ANNO117_DIR") else pytest.skip("needs ANNO117_DIR")
    ref = chosen("anno117")[0]
    save = container.open_file(ref.path, "anno117")
    s = summary.summarize(save)
    islands = summary.sessions(save)
    stocks = [row[5] for row in islands.table.rows if isinstance(row[5], records.CRef) and row[5].m_keys]
    assert stocks and all(list(r.m_keys) == sorted(set(r.m_keys)) for r in stocks)
    found = names.open_names(CSettings(anno117=install, workspace=str(tmp_path)), "anno117")
    assert found is not None
    names.apply(s, found)
    names.apply(records.CSummary("anno117", [islands]), found)
    assert any(found.asset(g) != "" for g in stocks[0].m_keys)
    good = stocks[0].m_keys[0]
    assert found.asset(good) and found.asset(good) != str(good)
    assert found.option("SetupTaxes", "1") == "Medium"
