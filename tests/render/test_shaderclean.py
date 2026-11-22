"""The removal of the QRhi path's compiled game shaders a phase no longer needs (render/shaderclean): the manifest per
game build and phase, which entries an offer covers (a native phase's, an earlier game build's, none a needed phase
shares), that nothing else is touched, and the pop-up's choices as remembered."""

from __future__ import annotations

import pytest

from annotoolbox.infra import buildcache
from annotoolbox.render import shaderclean

OLD, NEW = "a" * 64, "b" * 64


def _key(n: int) -> str:
    return f"{n:064x}"


def _entry(namespace: str, key: str, size: int = 10) -> None:
    buildcache.put(namespace, key, bytes(size))


def _cache() -> dict[str, list[str]]:
    """Each phase's entries of the current build, an earlier build's, a native fallback compile."""
    keys = {"terrain": [_key(1), _key(2)], "sky": [_key(2), _key(3)], "models": [_key(4)], "old": [_key(5), _key(3)]}
    for phase in ("terrain", "sky", "models"):
        shaderclean.record(NEW, phase, keys[phase])
    shaderclean.record(OLD, "models", keys["old"])
    for key in {k for ks in keys.values() for k in ks}:
        _entry(shaderclean.SHADERS, key)
    _entry("game-shaders-native", _key(1))  # the native backend's fallback compile under the same key: kept
    return keys


def test_the_manifest_lists_each_phases_entries_per_game_build_once():
    shaderclean.record(NEW, "terrain", [_key(1), _key(2)])
    shaderclean.record(NEW, "terrain", [_key(2)])
    assert shaderclean.manifests() == {NEW: {"terrain": {_key(1), _key(2)}}}
    path = buildcache.root() / shaderclean.MANIFESTS / NEW / "terrain.txt"
    assert path.read_text(encoding="ascii").count(_key(2)) == 1  # once a session


def test_an_offer_covers_a_native_phase_and_an_earlier_build_but_no_shared_entry():
    _cache()
    got = shaderclean.offer(NEW, {"terrain"})
    assert got.phases == ["terrain"] and got.builds == [OLD]
    # terrain's 1 (its 2 is the sky's too), the old build's 5 (its 3 is the sky's now)
    assert got.keys == [_key(1), _key(5)] and got.size == 20
    assert shaderclean.offer(NEW, set()).keys == [_key(5)]  # a game update alone


def test_removing_touches_only_the_offers_entries():
    _cache()
    got = shaderclean.offer(NEW, {"terrain"})
    done = shaderclean.remove(got)
    assert done.entries == 2 and done.size == 20 and not done.failed
    for key, kept in ((_key(1), False), (_key(2), True), (_key(3), True), (_key(4), True), (_key(5), False)):
        assert (buildcache.get(shaderclean.SHADERS, key) is not None) == kept, key
    assert buildcache.get("game-shaders-native", _key(1)) is not None
    assert set(shaderclean.manifests()) == {NEW} and "terrain" not in shaderclean.manifests()[NEW]
    assert not shaderclean.offer(NEW, {"terrain"})  # nothing left to offer


def test_the_pop_ups_choices_are_remembered():
    _cache()
    got = shaderclean.offer(NEW, {"terrain"})
    assert shaderclean.should_ask({}, got, "0.2.0")
    later = shaderclean.answered({}, got, "0.2.0", "later")
    assert not shaderclean.should_ask(later, got, "0.2.0")  # not now: not again in this version
    assert shaderclean.should_ask(later, got, "0.2.1")  # asked again after the next update
    never = shaderclean.answered({}, got, "0.2.0", "never")
    assert not shaderclean.should_ask(never, got, "0.2.1")
    more = shaderclean.SOffer(NEW, ["terrain"], [OLD, "c" * 64], got.keys, got.size)
    assert shaderclean.should_ask(never, more, "0.2.1")  # a game update adds to it: asked about that
    assert not shaderclean.should_ask({}, shaderclean.SOffer(NEW), "0.2.0")  # nothing to remove


def test_a_compile_is_filed_under_its_phase():
    pytest.importorskip("PySide6")
    from annotoolbox.render import terraindraw

    path, tech, function = terraindraw.STAGES["vs"]
    assert shaderclean.phase_of(path, f"{tech}/{function}") == "terrain"
    assert shaderclean.phase_of("data/shaders/fx/model_pbr.fx", "gbuffer") == shaderclean.MODELS
    assert shaderclean.phase_of("data/shaders/fx/terrain.fx", "Unknown/vsUnknown") == shaderclean.OTHER


def test_the_help_menu_offers_the_cleanup(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMainWindow

    from annotoolbox.infra.i18n import tr
    from annotoolbox.suite import shadercleanup

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.m_settings = SimpleNamespace(apps={})
    cleanup = shadercleanup.attach(window, "", ask=True)  # no install: nothing searched at start
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == tr("islands.help"))
    assert tr("common.shader_cleanup_menu") in [a.text() for a in menu.actions()]
    assert not cleanup.m_busy and "looked" not in window.m_settings.apps[shadercleanup.SETTING]
    window.deleteLater()
    app.processEvents()


def test_the_question_has_its_three_choices_and_closing_it_is_not_now(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

    from annotoolbox.infra.i18n import tr
    from annotoolbox.suite import shadercleanup

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.m_settings = SimpleNamespace(apps={})
    cleanup = shadercleanup.attach(window, "", ask=False)
    got = shaderclean.SOffer(NEW, keys=[_key(1)], size=10)
    seen, press = [], [None]

    def exec_(box):
        seen.append([b.text().replace("&", "") for b in box.buttons()])
        assert box.defaultButton() is box.escapeButton()  # Enter and Esc both mean "not now"
        for b in box.buttons() if press[0] else ():
            if b.text() == press[0]:
                b.click()

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    assert cleanup._ask(got, menu=False) == "later"  # closed without a button
    press[0] = tr("common.shader_cleanup_never")
    assert cleanup._ask(got, menu=False) == "never"
    press[0] = tr("common.shader_cleanup_remove")
    assert cleanup._ask(got, menu=True) == "remove"
    assert len(seen[0]) == 3 and len(seen[2]) == 2  # the Help menu's question has no "don't ask again"
    window.deleteLater()
    app.processEvents()
