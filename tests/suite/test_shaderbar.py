"""The shader progress bar: the tracker (``infra.shadertrack``), the pause of the start-up stage's job queue
(``render.shaderstart``) and the bar and its button in a window (``suite.shaderbar``)."""

import threading
import time

import pytest

from annotoolbox.infra import shadertrack
from annotoolbox.render import shaderstart


@pytest.fixture(autouse=True)
def _clean():
    shadertrack.batch(0, 0)
    yield
    shadertrack.batch(0, 0)


def test_compiles_in_flight_and_a_part_are_reported_to_listeners():
    seen = []
    remove = shadertrack.on_change(seen.append)
    with shadertrack.compiling("terrain.fx ps"):
        assert shadertrack.snapshot().active == ("terrain.fx ps",) and shadertrack.snapshot().busy
    shadertrack.batch(2, 5)
    assert (shadertrack.snapshot().done, shadertrack.snapshot().total) == (2, 5)
    remove()
    shadertrack.batch(0, 0)
    assert not shadertrack.snapshot().busy
    assert seen[0].active == ("terrain.fx ps",) and seen[1].active == () and (seen[2].done, seen[2].total) == (2, 5)
    assert len(seen) == 3  # nothing after the listener was removed


def test_pause_holds_back_new_jobs_and_resume_goes_on(monkeypatch):
    monkeypatch.setattr(shaderstart, "HOLD_POLL", 0.01)
    ran, release = [], threading.Event()

    def job(n):
        ran.append(n)
        if n == 0:
            release.wait(5)

    jobs = [shaderstart.SJob(f"j{n}", lambda n=n: job(n), "model") for n in range(4)]
    plan = shaderstart.SPlan("build", jobs)

    class FakeShaderView:
        m_compiler = type("C", (), {"version": lambda self: "v"})()
        m_graphics = ()

        def passing(self):
            from contextlib import nullcontext

            return nullcontext()

    box = []
    t = threading.Thread(target=lambda: box.append(shaderstart.run(plan, FakeShaderView(), workers=1)))
    t.start()
    deadline = time.time() + 5
    while not ran and time.time() < deadline:
        time.sleep(0.01)
    shadertrack.pause(True)  # the running job (0) may end, none starts after it
    release.set()
    time.sleep(0.3)
    assert ran == [0] and shadertrack.snapshot().paused and t.is_alive()
    shadertrack.pause(False)
    t.join(5)
    assert ran == [0, 1, 2, 3] and box[0].done == 4  # it went on where it stopped
    assert not shadertrack.paused()


def test_the_bar_follows_the_tracker_from_another_thread_and_its_button_toggles_the_pause(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMainWindow

    from annotoolbox.infra.i18n import tr
    from annotoolbox.suite import shaderbar

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.show()
    shader = shaderbar.attach(window)
    assert not shader.m_widget.isVisible()
    worker = threading.Thread(target=lambda: shadertrack.batch(1, 4))
    worker.start()
    worker.join()
    app.processEvents()
    assert shader.m_widget.isVisible() and (shader.m_bar.value(), shader.m_bar.maximum()) == (1, 4)
    assert shader.m_pause.text() == tr("common.shader_pause")
    shader.m_pause.click()
    app.processEvents()
    assert shadertrack.paused() and shader.m_pause.text() == tr("common.shader_resume")
    shader.m_pause.click()
    app.processEvents()
    assert not shadertrack.paused()
    shadertrack.batch(0, 0)
    app.processEvents()
    assert not shader.m_widget.isVisible()
    shader.close()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_the_island_window_fills_its_lists_off_the_window_thread_and_turns_to_the_asked_game(tmp_path):
    pytest.importorskip("PySide6")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from annotoolbox.editor import editorapp
    from annotoolbox.editor.desktop import workbench
    from annotoolbox.suite.settings import CSettings

    app = QApplication.instance() or QApplication([])
    path = tmp_path / "settings.json"
    CSettings(anno117=str(tmp_path), mods=str(tmp_path / "mods"), workspace=str(tmp_path / "ws")).save(path)
    window = workbench.CMainWindow(editorapp.CApp(path), lists_wait=False)
    assert window.m_tabs.count() == 0  # not read yet: the window did not wait for it
    window.show_game("anno117")
    window.m_background.join()
    deadline = time.time() + 10
    while not window.m_tabs.count() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert window.m_tabs.count() and window.shown_game() == "anno117"
    window.close()
    window.deleteLater()
    app.processEvents()
