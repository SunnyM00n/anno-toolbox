"""The ships of a synthetic 117 save: every field a row shows, the human as the owner of a ship without one, the table
with and without the game's names, and a session document that does not read."""

from __future__ import annotations

import pytest
from tests.saveedit.saveeditfakes import (
    ITEM_A,
    META_A,
    NAME_A,
    ROUTE,
    SHIP_A,
    SHIP_B,
    SHIP_C,
    fake_save117,
    ship_data,
    ship_session,
    ships_save,
)

from annotoolbox.saveedit import container, ships117


def test_every_ship_of_the_session_is_read_with_what_it_holds(tmp_path):
    ships = ships117.read(container.open_file(ships_save(tmp_path), "anno117"))
    assert [ship.object_id for ship in ships] == [SHIP_A, SHIP_B, SHIP_C]  # the land unit is no ship
    a, b, _c = ships
    assert (a.session, a.meta_id, a.guid, a.owner_profile, b.owner_profile) == (3245, META_A, 37222, 41, 31100)
    assert (a.vehicle_text_guid, a.vehicle_text_iterator, b.vehicle_text_iterator) == (NAME_A, 1, 2)
    assert (round(a.x_m, 1), round(a.z_m, 1)) == (687.8, 641.7)
    assert a.current_hit_points is None and b.current_hit_points == 566
    assert a.ship_modules == (31647, 31648) and b.ship_modules == ()
    assert a.goods == ((2084, 16),) and (a.slots_used, a.slot_count) == (
        2,
        3,
    )  # goods and the item; the unit slot is empty
    assert a.items == (92876,) and a.item_ids == (ITEM_A,) and a.unit_meta_ids == ()
    assert a.trade_route_id == ROUTE and b.trade_route_id is None and not a.is_quest_object


def test_the_rows_name_the_numbers_when_the_games_names_are_open(tmp_path):
    save = container.open_file(ships_save(tmp_path), "anno117")
    ships, routes = ships117.read(save), ships117.route_names(save)
    assert routes == {ROUTE: "Jul - Sep"}
    plain = ships117.rows(ships, routes)
    assert len(plain[0]) == len(ships117.HEADERS)
    assert plain[0][0] == "3245" and plain[0][1] == str(NAME_A) and plain[1][1] == f"{NAME_A} (2)"
    assert plain[0][-1] == "Jul - Sep" and "2084 16" in plain[0][7]

    class FakeNames:
        def asset(self, guid: int) -> str:
            return {37222: "Penteconter", 2084: "Flax", 3245: "Latium"}.get(guid, "")

        def text(self, text_id: str) -> str:
            return "Lavinia" if text_id == str(NAME_A) else ""

    named = ships117.rows(ships, routes, FakeNames())
    assert named[0][:3] == ["Latium (3245)", "Lavinia", "Penteconter (37222)"] and named[1][1] == "Lavinia (2)"
    assert "Flax 16" in named[0][7]


def test_a_save_without_ships_or_sessions_lists_none(tmp_path):
    assert ships117.read(container.open_file(fake_save117(tmp_path / "plain.a8s"), "anno117")) == []


def test_a_session_document_cut_short_is_the_readers_own_error(tmp_path):
    whole = ship_session()
    cut = whole[:64] + whole[-16:]  # its tail says version 3, its name tables lie beyond the end
    save = container.open_file(ships_save(tmp_path, data=ship_data(cut)), "anno117")
    with pytest.raises(ships117.CShipsError, match="session 3245"):
        ships117.read(save)
