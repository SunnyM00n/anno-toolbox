"""The loader's changed flag: a window that redraws on it must find the result in."""

from threading import Event

from annotoolbox.render.loader import CLoader


class RecordingEvent(Event):
    """An event that notes whether every future of the loader was done each time it is set."""

    def __init__(self, loader_of) -> None:
        super().__init__()
        self.m_loader_of = loader_of
        self.m_done_when_set: list[bool] = []

    def set(self) -> None:
        self.m_done_when_set.append(all(future.done() for future in self.m_loader_of().m_futures.values()))
        super().set()


def test_the_changed_flag_is_set_only_once_the_result_can_be_read():
    loader = CLoader(1)
    loader.m_changed = RecordingEvent(lambda: loader)
    try:
        gate = Event()
        loader.get("a", lambda: gate.wait(10) and 1)  # the future is registered before the job ends
        gate.set()
        assert loader.m_changed.wait(10)
        assert loader.m_changed.m_done_when_set and all(loader.m_changed.m_done_when_set)
        assert loader.get("a", lambda: 2) == 1
    finally:
        loader.close()


def test_a_job_the_close_cancelled_answers_none_as_a_new_one_does():
    """A view's last frame after its renderer was released asks for what was queued: no ``CancelledError``."""
    loader = CLoader(1)
    gate = Event()
    loader.get("running", lambda: gate.wait(10))
    loader.get("queued", lambda: 2)
    loader.close()
    gate.set()
    assert loader.get("queued", lambda: 3) is None and loader.get("new", lambda: 4) is None


def test_a_failed_job_sets_the_flag_too():
    loader = CLoader(1)
    try:
        loader.get("bad", lambda: 1 / 0)
        assert loader.m_changed.wait(10) and isinstance(loader.get("bad", lambda: 0), ZeroDivisionError)
    finally:
        loader.close()
