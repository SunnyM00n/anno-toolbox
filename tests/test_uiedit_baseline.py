"""The preview's baseline of the values the game showed in many sessions (uiedit/baseline): it replaces what the
preview made up, live values win over it, and a value the install cannot resolve is skipped. On synthetic files."""

from __future__ import annotations

import os
from types import SimpleNamespace

from tests.test_uiedit_compose import composed, placeholders  # noqa: F401  # the fixture and the filler words

from annotoolbox.uiedit import baseline, elements, layout, viewmodels

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
LINE = "A010D06813984305"
OTHER = "A010D06813984306"
SCENE = "composed"


def _setup(composed, entry: dict):  # noqa: F811
    """The scene's label ``Title`` and a shipped file with ``entry`` for it (file GUID and name CRC read from the
    files); the install's English table holds one line."""
    top = elements.tree(composed.root(SCENE))
    title = next(e for e in top.walk() if e.name == "Title")
    composed._texts["english"] = {int(LINE, 16) - (1 << 64): "Hello"}
    return title, {"format": 1, "files": {top.guid: {elements.crc_key(title)[4:]: entry}}}


def _shown(composed, base, **ctx_values) -> list[str]:  # noqa: F811
    ctx = layout.Context(composed, viewmodels.Samples())
    ctx.baseline = base.for_install(composed) if base is not None else None
    for name, value in ctx_values.items():
        setattr(ctx, name, value)
    frame = layout.place(SCENE, composed.root(SCENE), ctx)
    return [i.text for i in frame.items if i.kind == "text"]


def test_a_baseline_text_takes_the_place_of_a_filler(composed):  # noqa: F811
    _, data = _setup(composed, {"t": [LINE, 100]})
    assert _shown(composed, None) == [placeholders.text_for("")]  # without it: the preview's filler
    assert _shown(composed, baseline.Baseline(data)) == ["Hello"]


def test_a_live_value_wins_over_the_baseline(composed):  # noqa: F811
    title, data = _setup(composed, {"t": [LINE, 100]})
    live = {title.guid: "From the running game"}
    assert _shown(composed, baseline.Baseline(data), live_text=live) == ["From the running game"]


def test_a_baseline_text_the_install_has_no_line_for_is_skipped(composed):  # noqa: F811
    _, data = _setup(composed, {"t": [OTHER, 100]})
    assert _shown(composed, baseline.Baseline(data)) == [placeholders.text_for("")]


def test_a_plain_baseline_text_and_an_empty_one_are_shown_as_they_are(composed):  # noqa: F811
    _, data = _setup(composed, {"n": ["0 %", 90]})
    assert _shown(composed, baseline.Baseline(data)) == ["0 %"]
    _, data = _setup(composed, {"n": ["", 100]})
    assert not [t for t in _shown(composed, baseline.Baseline(data)) if t]  # the game showed nothing there


def test_the_baseline_fills_only_what_the_preview_made_up():
    base = baseline.Baseline({"format": 1, "files": {"f": {"c": {"t": [LINE, 100], "v": [False, 100]}}}})
    base = base.for_install(SimpleNamespace(text_of=lambda ident, language: "Hello", tree=None))
    ctx = SimpleNamespace(language="english")
    text = baseline.TEXT
    given = base.apply(ctx, "f", "c", "", "label", {text: "Marcia"}, {})
    assert baseline.BASELINE_TEXT not in given  # a value the game's data gave stays
    assert (
        base.apply(ctx, "f", "c", "", "label", {text: placeholders.TEXTS["short"]}, {})[baseline.BASELINE_TEXT]
        == "Hello"
    )
    assert base.apply(ctx, "f", "c", "", "label", {}, {})[baseline.BASELINE_TEXT] == "Hello"
    visible = baseline.VISIBLE
    assert base.apply(ctx, "f", "c", "", "label", {visible: True}, {visible: True})[visible] is False  # bound
    assert visible not in base.apply(ctx, "f", "c", "", "label", {visible: True}, {})  # a stored flag stays
    assert base.apply(ctx, "f", "other", "", "label", {}, {}) == {}


def test_a_stand_in_picture_gives_way_to_the_baseline_picture_the_install_has():
    path = "data/ui/fhd/base/icon.png"
    files = SimpleNamespace(tree=SimpleNamespace(exists=lambda p: p == "data/ui/fhd/base/icon.dds"))
    entry = {"i": [path, 100]}
    base = baseline.Baseline({"format": 1, "files": {"f": {"c": entry, "d": {"i": ["data/ui/missing.png", 100]},
                                                           "e": {"i": ["", 100]}}}}).for_install(files)  # fmt: skip
    ctx = SimpleNamespace(language="english")
    stand_in = {baseline.IMAGE_ID: placeholders.PICTURE + "Icon"}
    assert base.apply(ctx, "f", "c", "", "image", stand_in, {})[baseline.IMAGE_ID] == path
    assert base.apply(ctx, "f", "d", "", "image", stand_in, {}) == {}  # no file on this install: skipped
    assert base.apply(ctx, "f", "e", "", "image", stand_in, {})[baseline.IMAGE_ID] == ""  # the game showed none
    assert base.apply(ctx, "f", "c", "", "image", {baseline.IMAGE_ID: path}, {}) == {}  # a real picture stays


def test_a_character_the_game_showed_comes_from_the_preview_settings_cast():
    from annotoolbox.uiedit import previewdefaults

    entry = {"r": [["rival", 1], 100], "p": [["player", 0, "portrait"], 100]}
    base = baseline.Baseline({"format": 1, "files": {"f": {"c": entry}}}).for_install(SimpleNamespace(tree=None))
    ctx = SimpleNamespace(language="english", defaults=previewdefaults.CAMPAIGN_DEFAULTS)
    assert base.text(ctx, entry) == previewdefaults.RIVALS[1].name  # the second rival of the campaign: Athr
    assert base.picture(ctx, entry) == previewdefaults.PLAYER.portrait_icon
    mine = previewdefaults.Defaults(player_name="Livia")
    ctx = SimpleNamespace(language="english", defaults=mine)
    assert base.text(ctx, {"r": [["player", 0], 100]}) == "Livia"  # the user's own settings win over the campaign's


def test_a_placing_context_changes_the_value_and_the_file_level_is_the_default():
    base = baseline.Baseline({"format": 1, "files": {"f": {"c": {"n": ["a", 100]}}},
                              "contexts": {"f": {"parent/x": {"c": {"n": ["b", 100]}}}}})  # fmt: skip
    assert base.entry("f", "c")["n"][0] == "a" and base.entry("f", "c", "parent/x")["n"][0] == "b"
    assert base.entry("f", "c", "parent/y")["n"][0] == "a" and base.entry("g", "c") == {}


def test_the_shipped_baseline_reads_and_holds_references_only():
    shipped = baseline.shipped()
    assert shipped is not None and shipped.build > 0 and shipped.counts()["values"] > 0
    entries = [e for f in shipped.entries.values() for e in f.values()]
    assert all(set(e) <= {"t", "n", "r", "i", "p", "v"} for e in entries)
    assert all(len(e["t"][0]) == 16 for e in entries if "t" in e)  # a line id, never a text
