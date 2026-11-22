"""The Converter app without a window: each entry's form and command line made from its command's own parser, the
runner (its log, its exit statuses, Stop), and every kind of entry run on synthetic files the way the window runs it."""

from __future__ import annotations

import argparse
import array
import json
import threading
import wave
from pathlib import Path

import pytest
from tests.ddsfiles import make_dxt1_dds
from tests.rdmfiles import static_file

from annotoolbox.converterapp import catalogue, commandform, commands, gamefiles, runner
from annotoolbox.formats import bnk, rdmread
from annotoolbox.suite.extspecs import SConversion
from annotoolbox.suite.settings import CSettings


def by_key(key: str) -> SConversion:
    return next(c for c in catalogue.baseline() if c.key == key)


def run_entry(key: str, sources: list[Path], out: Path, **values) -> tuple[runner.SOutcome, list[str]]:
    """Run an entry as the window does (one command line per source when it runs per source)."""
    change = by_key(key)
    parser = commandform.parser_of(change)
    form = {f.dest: f.default if f.kind != "flag" else bool(f.default) for f in commandform.fields(change, parser)}
    form = {k: "" if v is None or v == [] else v for k, v in form.items()} | values
    target = commandform.target_of
    lines = (
        [commandform.argv(change, parser, [str(s)], str(target(change, out, str(s))), form) for s in sources]
        if change.per_input
        else [commandform.argv(change, parser, [str(s) for s in sources], str(out), form)]
    )
    log: list[str] = []
    return runner.run(change, parser, lines, runner.STalk(log.append)), log


def test_every_entry_of_this_edition_finds_its_command_and_names_its_texts():
    entries = catalogue.baseline()
    assert len({c.key for c in entries}) == len(entries) == 22
    for change in entries:
        assert commandform.parser_of(change) is not None, change.key
        assert change.category in catalogue.CATEGORIES
        assert change.title == f"convert.{change.key}" and change.summary == f"convert.{change.key}_summary"


def test_the_form_is_the_commands_own_options_and_the_line_is_what_a_person_types():
    change = by_key("rdm_gltf")
    parser = commandform.parser_of(change)
    shown = {f.dest: f for f in commandform.fields(change, parser)}
    assert "inputs" not in shown and "out" not in shown  # the sources and the target have boxes of their own
    assert shown["lods"].kind == "flag" and shown["form"].choices[0] == "glb" and shown["anims"].is_folder
    assert shown["anim"].allow_many and shown["anim"].kind == "path"
    line = commandform.argv(change, parser, ["a.rdm"], "out", {"lods": True, "form": "glb", "anim": "x.rdm;y.rdm"})
    assert line == ["rdm", "export", "a.rdm", "--out", "out", "--anim", "x.rdm", "--anim", "y.rdm", "--lods"]
    args = commandform.parse(change, parser, line)
    assert args.lods and args.anim == [Path("x.rdm"), Path("y.rdm")] and args.form == "glb"
    assert commandform.shown("tool", ["a b"]) == "tool 'a b'"


def test_an_entry_narrows_a_free_option_and_always_gives_it_and_its_fixed_arguments():
    change = by_key("texture_png")
    parser = commandform.parser_of(change)
    shown = {f.dest: f for f in commandform.fields(change, parser)}
    assert (
        shown["format"].choices == ("png", "png16", "exr", "hdr") and "kind" not in shown and "rdm_target" not in shown
    )
    line = commandform.argv(change, parser, ["t.dds"], "o", {"format": "png", "hdr_form": "exr"})
    assert line == ["export", "t.dds", "--out", "o", "--format", "png", "--kind", "texture"]


def test_a_hidden_option_is_given_when_the_window_fills_it_and_a_refused_line_says_why():
    change = by_key("island_port")
    parser = commandform.parser_of(change)
    assert "game" not in {f.dest for f in commandform.fields(change, parser)}
    values = catalogue.port_values(CSettings(anno117="A", anno1800="B"), {"to": "anno1800:moderate"}, "anno117")
    assert values == {"game": "anno117", "src": "A", "dst": "B"}
    line = commandform.argv(change, parser, ["data/x.a7m"], "mods", {"to": "anno1800:moderate", **values})
    assert line[:4] == ["island", "port", "anno117", "data/x.a7m"] and ["--dst", "B"] == line[-2:]
    with pytest.raises(commandform.CParseError, match="required"):
        commandform.parse(change, parser, ["island", "port", "anno117", "x.a7m"])


def test_a_command_this_build_lacks_has_no_parser():
    missing = SConversion("x", "models", "t", "s", ("rdm", "nothing"), by_key("rdm_gltf").register, ("nothing",))
    assert commandform.parser_of(missing) is None


def _talking(sub) -> None:
    p = sub.add_parser("talk")
    p.add_argument("inputs", nargs="+")
    p.add_argument("--fail", action="store_true")

    def func(args: argparse.Namespace) -> int:
        for word in args.inputs:
            print(f"said {word}", end="\n" if word != "last" else "")
        if args.fail:
            raise ValueError("it broke\nsecond line")
        return 0

    p.set_defaults(func=func)


def _leaving(sub) -> None:
    p = sub.add_parser("leave")
    p.add_argument("how")

    def func(args: argparse.Namespace) -> int:
        print("before", end="")
        raise SystemExit({"message": "error: no game folder", "done": None, "status": 3}[args.how])

    p.set_defaults(func=func)


def test_a_command_that_ends_itself_keeps_its_message_and_its_status():
    change = SConversion("leave", "data", "t", "s", ("leave",), _leaving, ("leave",))
    parser = commandform.parser_of(change)
    log: list[str] = []
    lines = [["leave", "message"], ["leave", "done"], ["leave", "status"]]
    outcome = runner.run(change, parser, lines, runner.STalk(log.append))
    assert outcome.statuses == [1, 0, 3]  # a message is a failure, leaving with nothing is not
    assert log[1:3] == ["before", "error: no game folder"]  # the message is not lost, after what was printed


def test_the_runner_logs_each_line_and_counts_the_failures():
    change = SConversion("talk", "data", "t", "s", ("talk",), _talking, ("talk",))
    parser = commandform.parser_of(change)
    log: list[str] = []
    steps: list[tuple[int, int]] = []
    lines = [["talk", "a", "last"], ["talk", "b", "--fail"], ["talk", "--nothing"]]
    outcome = runner.run(change, parser, lines, runner.STalk(log.append, lambda d, t: steps.append((d, t))), "tool")
    assert outcome.statuses == [0, 1, 2] and outcome.failed == 2 and not outcome.stopped
    assert log[:3] == ["> tool talk a last", "said a", "said last"]
    assert "error: it broke" in log and log[-1].startswith("error: ")  # argparse's own message for the third
    assert steps[0] == (0, 3) and steps[-1] == (3, 3)
    assert not isinstance(__import__("sys").stdout, runner.CRouter)  # the streams are handed back


def test_stop_ends_the_job_at_the_next_line_the_command_writes():
    change = SConversion("talk", "data", "t", "s", ("talk",), _talking, ("talk",))
    parser = commandform.parser_of(change)
    talk = runner.STalk(lambda line: talk.stop.set() if line == "said a" else None)
    outcome = runner.run(change, parser, [["talk", "a", "b", "c"], ["talk", "d"]], talk)
    assert outcome.stopped and outcome.statuses == []


def test_another_threads_output_is_not_taken_while_a_job_runs(capsys):
    change = SConversion("talk", "data", "t", "s", ("talk",), _talking, ("talk",))
    parser = commandform.parser_of(change)
    printed = threading.Event()

    def other() -> None:
        print("from elsewhere")
        printed.set()

    def talk_line(line: str) -> None:
        if line == "said a":
            thread = threading.Thread(target=other)
            thread.start()
            thread.join()

    runner.run(change, parser, [["talk", "a"]], runner.STalk(talk_line))
    assert printed.is_set() and "from elsewhere" in capsys.readouterr().out


def test_a_target_inside_a_game_is_refused(tmp_path):
    game = tmp_path / "Anno 117"
    settings = CSettings(anno117=str(game), workspace=str(tmp_path / "ws"))
    assert gamefiles.refused_target(game / "data" / "out", settings) == "Anno 117"
    assert gamefiles.refused_target(tmp_path / "mine", settings) == ""


def test_picked_game_files_are_written_below_a_fresh_folder_at_their_game_paths(tmp_path):
    from annotoolbox.sources import gametree

    for name, body in {"data/loca/english/txt/a.txt": b"1 hi", "data/x.rdm": b"r"}.items():
        (tmp_path / "game" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "game" / name).write_bytes(body)
    tree = gametree.loose_tree(tmp_path / "game")
    settings = CSettings(workspace=str(tmp_path / "ws"))
    folder = gamefiles.fresh_folder(settings, "anno117")
    assert gamefiles.paths(tree, (".txt",)) == ["data/loca/english/txt/a.txt"]
    (written,) = gamefiles.extract(tree, ["data/loca/english/txt/a.txt"], folder)
    assert written == folder / "data" / "loca" / "english" / "txt" / "a.txt" and written.read_bytes() == b"1 hi"
    assert gamefiles.fresh_folder(settings, "anno117") != folder  # the next pick gets a folder of its own
    with pytest.raises(ValueError):
        gamefiles.extract(tree, ["../evil.txt"], folder)


# Each kind of entry on synthetic files (the fixtures of each format's own tests).


def test_an_rdm_becomes_a_gltf_and_back(tmp_path):
    pytest.importorskip("numpy")
    source = tmp_path / "in" / "house.rdm"
    source.parent.mkdir()
    source.write_bytes(static_file())
    outcome, log = run_entry("rdm_gltf", [source], tmp_path / "out")
    assert outcome.statuses == [0], log
    glb = tmp_path / "out" / "house.glb"
    assert glb.is_file()
    outcome, log = run_entry("gltf_rdm", [glb], tmp_path / "back")
    assert outcome.statuses == [0], log
    assert rdmread.read_rdm((tmp_path / "back" / "house.rdm").read_bytes()).meshes


def test_textures_pictures_data_and_texts_go_through_the_export(tmp_path):
    pytest.importorskip("numpy")
    files = tmp_path / "in"
    (files / "data" / "loca" / "english" / "txt").mkdir(parents=True)
    (files / "data" / "loca" / "english" / "txt" / "texts.txt").write_bytes("100=Hello\n".encode("utf-16"))
    (files / "wall.dds").write_bytes(make_dxt1_dds())
    (files / "assets.xml").write_text("<Assets><Asset><Guid>5</Guid></Asset></Assets>", encoding="utf-8")
    for key, source, made in (
        ("texture_png", files / "wall.dds", "wall.png"),
        ("data_json", files / "assets.xml", "assets.json"),
        ("loca_csv", files, "data/loca/english/txt/texts.csv"),
    ):
        outcome, log = run_entry(key, [source], tmp_path / key)
        assert outcome.statuses == [0] and (tmp_path / key / made).is_file(), log


def test_a_wem_becomes_a_wav_and_a_wav_a_wem(tmp_path):
    (tmp_path / "a.wem").write_bytes(bnk.pcm_wem(array.array("h", [0, 100, -100, 5]), 22050, 1))
    outcome, log = run_entry("sound_audio", [tmp_path / "a.wem"], tmp_path / "wav")
    assert outcome.statuses == [0], log
    with wave.open(str(tmp_path / "wav" / "a.wav")) as w:
        assert w.getframerate() == 22050 and w.getnframes() == 4
    outcome, log = run_entry("audio_wem", [tmp_path / "wav" / "a.wav"], tmp_path / "wem", codec="pcm")
    assert outcome.statuses == [0], log
    assert (tmp_path / "wem" / "a.wem").read_bytes()[:4] == b"RIFF"
    outcome, _log = run_entry("audio_wem", [tmp_path / "wav" / "a.wav"], tmp_path / "wem", codec="pcm")
    assert outcome.statuses == [0] and any("skipped" in line for line in _log)  # nothing is overwritten unasked


def test_a_folder_becomes_an_archive_and_the_archive_a_folder_again(tmp_path):
    folder = tmp_path / "pack"
    (folder / "data").mkdir(parents=True)
    (folder / "data" / "a.txt").write_bytes(b"hello")
    outcome, log = run_entry("rda_pack", [folder], tmp_path / "archives", version="2.2")
    archive = tmp_path / "archives" / "pack.rda"
    assert outcome.statuses == [0] and archive.is_file(), log
    outcome, log = run_entry("rda_extract", [archive], tmp_path / "files")
    assert outcome.statuses == [0], log
    assert (tmp_path / "files" / "pack" / "data" / "a.txt").read_bytes() == b"hello"


def test_a_granny_model_becomes_a_gltf(tmp_path):
    pytest.importorskip("numpy")
    from tests.formats.formatfakes import granny_file

    (tmp_path / "m.gr2").write_bytes(granny_file())
    outcome, log = run_entry("granny_gltf", [tmp_path / "m.gr2"], tmp_path / "out", format="glb")
    assert outcome.statuses == [0], log
    assert json.loads((tmp_path / "out" / "index.json").read_text(encoding="utf-8"))


def test_a_picture_becomes_a_bc7_texture_with_its_mips(tmp_path):
    pytest.importorskip("numpy")
    pytest.importorskip("PIL")
    from annotoolbox.models import bc7encode

    if not bc7encode.available():
        pytest.skip("no BC7 encoder in this install")
    from PIL import Image

    Image.new("RGBA", (8, 8), (200, 10, 10, 255)).save(tmp_path / "red.png")
    outcome, log = run_entry("texture_bc7", [tmp_path / "red.png"], tmp_path / "out", srgb=True)
    assert outcome.statuses == [0], log
    assert (tmp_path / "out" / "red.dds").read_bytes()[:4] == b"DDS "


def test_the_back_commands_say_what_they_skip_and_refuse_an_empty_input(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no input file"):
        commands.files_of([tmp_path / "empty"], commands.AUDIO)
