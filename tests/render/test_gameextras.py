"""The 117 frame's extra parts (``render.gameextras``): the ocean steps only when its clock moved, and the island
view's Performance switches leave the visible sky, haze and clouds out (island-editor-view "Performance switches")."""

from types import SimpleNamespace

from annotoolbox.render import gameextras


class FakeOceanSimulation:
    """The ocean's simulation: counts its steps, hands out its maps."""

    m_error = ""

    def __init__(self) -> None:
        self.steps: list[float] = []

    def run(self, ctx, seconds: float) -> None:
        self.steps.append(seconds)

    def inputs(self) -> dict:
        return {"Dx": "map"}


def _extras():
    ocean = FakeOceanSimulation()
    clouds = SimpleNamespace(m_error="", m_maps=SimpleNamespace(far=900.0), m_seconds=0.0)
    parts = {
        "water": SimpleNamespace(m_error="", m_native=None),
        "ocean": ocean,
        "atmosphere": SimpleNamespace(m_error=""),
        "clouds": clouds,
    }
    extras = gameextras.CFrameExtras(None, lambda: "source", lambda job, work: work(), print)
    extras.m_made.update(parts)
    return extras, ocean, clouds


def _passes():
    return SimpleNamespace(sea=None, water=None, atmosphere=None, look=None, clouds=None)


def test_the_switched_off_water_motion_steps_the_ocean_once_and_a_set_time_every_frame():
    extras, ocean, _clouds = _extras()
    for seconds in (1.0, 1.0, 1.0, 2.0, 2.0):
        passes = _passes()
        extras.passes(passes, None, seconds, None, True, held=True)
        assert passes.water is not None and passes.water.m_waves == {"Dx": "map"}  # the maps stay handed over
    assert ocean.steps == [1.0, 2.0]
    for _frame in range(3):  # a picture at a set time: a frame recorded while the shaders compile may never run
        extras.passes(_passes(), None, 3.0, None, True)
    assert ocean.steps == [1.0, 2.0, 3.0, 3.0, 3.0]


def test_the_sky_haze_and_clouds_switches_leave_their_passes_out():
    extras, _ocean, clouds = _extras()
    sky = object()
    passes = _passes()
    extras.passes(passes, sky, 1.0, None, False)
    assert passes.atmosphere is not None and passes.clouds is clouds and extras.far() == 900.0
    passes = _passes()
    extras.passes(passes, sky, 1.0, None, False, has_clouds=False)
    assert passes.atmosphere is not None and passes.clouds is None and extras.far() == 0.0
    passes = _passes()
    extras.passes(passes, sky, 1.0, None, False, has_sky=False, has_clouds=False)
    assert passes.atmosphere is None and passes.look is None and passes.clouds is None
