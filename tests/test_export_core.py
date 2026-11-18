"""The export of game files: kinds, the stored copy, failures as entries, the index, the optional tools."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from annotoolbox.exporting import kinds, movies, run
from annotoolbox.formats import imagewrite
from annotoolbox.infra import tools


def folder(tmp_path: Path, files: dict[str, bytes]) -> run.FolderSource:
    for name, body in files.items():
        (tmp_path / "game" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "game" / name).write_bytes(body)
    return run.FolderSource(tmp_path / "game")


def test_a_path_is_sorted_into_its_kind_and_every_kind_offers_the_stored_file_first():
    expected = {
        "data/a/house.cfg": "model",
        "a/b.rdm": "model",
        "x.dds": "texture",
        "x.tga": "picture",
        "movies/intro.bk2": "movie",
        "a.wem": "sound",
        "a.bnk": "bank",
        "assets.xml": "data",
        "data/loca/english/txt/text.txt": "text",
        "data/other/readme.txt": "data",
        "ui/a.ui": "ui",
        "fx/a.rdp": "effect",
        "maps/a.a7m": "island",
        "maps/a.isd": "island",
        "fonts/a.ttf": "font",
        "x.unknown": "other",
        "noext": "other",
    }
    for path, kind in expected.items():
        assert kinds.kind_of(path) == kind, path
    for kind in kinds.KINDS:
        assert kinds.formats_for(kind)[0] == kinds.Format("raw", "common.as_stored", "", True)
    assert [f.key for f in kinds.formats_for("texture")] == ["raw", "png", "png16", "exr", "hdr"]


def test_the_stored_file_is_written_beside_its_game_path_and_unknown_forms_are_ignored(tmp_path):
    source = folder(tmp_path, {"data/a/note.lua": b"print(1)\n"})
    out = tmp_path / "out"
    (entry,) = run.export_path(source, "data/a/note.lua", {"glb", "mp4"}, out)
    assert entry.status == "written" and entry.files == ["data/a/note.lua"] and entry.size == 9
    assert (out / "data/a/note.lua").read_bytes() == b"print(1)\n"
    (again,) = run.export_path(source, "data/a/note.lua", None, out)
    assert again.status == "skipped" and "exist" in again.notes[-1]
    (forced,) = run.export_path(source, "data/a/note.lua", None, out, run.Options(force=True))
    assert forced.status == "written"


def test_a_missing_or_broken_file_is_an_entry_not_an_exception(tmp_path):
    source = folder(tmp_path, {"a/bad.dds": b"DDS not really"})
    out = tmp_path / "out"
    (missing,) = run.export_path(source, "a/none.dds", {"png"}, out)
    assert missing.status == "failed" and "not read" in missing.notes[0]
    (broken,) = run.export_path(source, "a/bad.dds", {"png"}, out)
    assert broken.status == "failed" and (out / "a/bad.dds").is_file()  # the stored file still goes out


def test_a_batch_writes_an_index_and_can_be_stopped(tmp_path):
    source = folder(tmp_path, {"a/one.lua": b"1", "a/two.lua": b"22", "a/three.lua": b"333"})
    out, seen = tmp_path / "out", []
    paths = ["a/one.lua", "a/two.lua", "a/three.lua"]
    entries = run.export_many(
        source,
        paths,
        {"raw"},
        out,
        progress=lambda n, total, p: seen.append((n, total, p)),
        cancelled=lambda: len(seen) == 2,
    )
    assert [e.source for e in entries] == paths[:2] and seen[0] == (0, 3, "a/one.lua")
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert [r["source"] for r in index] == paths[:2] and index[1]["size"] == 2 and index[0]["kind"] == "data"
    rows = list(csv.reader((out / "index.csv").read_text(encoding="utf-8").splitlines()))
    assert rows[0][:3] == ["source", "kind", "archive"] and rows[1][0] == "a/one.lua"


def test_a_movie_form_without_ffmpeg_says_so_and_the_stored_file_goes_out(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "find_tool", lambda name: None)
    source = folder(tmp_path, {"movies/intro.bik": b"BIKi" + bytes(60)})
    out = tmp_path / "out"
    (entry,) = run.export_path(source, "movies/intro.bik", {"mp4", "webm"}, out)
    assert entry.status == "tool-missing" and "ffmpeg" in entry.notes[0] and "optional" in entry.notes[0]
    assert (out / "movies/intro.bik").is_file()
    assert "ffmpeg" in run.missing_tools({"mp4"}) and run.missing_tools({"png"}) == ""
    assert movies.missing()


def test_a_movie_form_runs_ffmpeg_over_the_movie(tmp_path, monkeypatch):
    calls = []

    def fake_run(exe, args, cwd=None, timeout=None):
        calls.append(args)
        Path(args[-1]).write_bytes(b"VIDEO")
        return tools.ToolResult(0, "", "")

    monkeypatch.setattr(tools, "find_tool", lambda name: Path("ffmpeg") if name == "ffmpeg" else None)
    monkeypatch.setattr(tools, "run_tool", fake_run)
    source = folder(tmp_path, {"movies/intro.bik": b"BIKi" + bytes(60)})
    (entry,) = run.export_path(source, "movies/intro.bik", {"mp4"}, tmp_path / "out")
    assert entry.status == "written" and "movies/intro.mp4" in entry.files
    assert (tmp_path / "out/movies/intro.mp4").read_bytes() == b"VIDEO" and calls[0][0] == "-y"


def test_a_bink_2_movie_that_ffmpeg_cannot_read_points_at_the_vendor_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "find_tool", lambda name: Path("ffmpeg") if name == "ffmpeg" else None)
    monkeypatch.setattr(tools, "run_tool", lambda *a, **k: tools.ToolResult(1, "", "Invalid data found"))
    source = folder(tmp_path, {"m/a.bk2": b"KB2i" + bytes(60)})
    (entry,) = run.export_path(source, "m/a.bk2", {"mp4"}, tmp_path / "out")
    assert entry.status == "failed" and "RAD Video Tools" in entry.notes[0]


def test_the_package_imports_and_runs_the_plain_forms_without_numpy(tmp_path):
    (tmp_path / "game").mkdir()
    pixels = bytes([255, 0, 0, 255] * 4)
    (tmp_path / "game" / "p.tga").write_bytes(
        bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 2, 0, 32, 0x20]) + bytes([0, 0, 255, 255] * 4)
    )
    script = (
        "import sys; sys.modules['numpy'] = None\n"
        "from pathlib import Path\n"
        "from annotoolbox.exporting import run\n"
        "src = run.FolderSource(Path(sys.argv[1]))\n"
        "e = run.export_path(src, 'p.tga', {'png'}, Path(sys.argv[2]))[0]\n"
        "print(e.status, e.files)\n"
        "d = run.export_path(src, 'p.tga', {'png'}, Path(sys.argv[2]) / 'again')[0]\n"
    )
    root = Path(__file__).resolve().parents[1] / "src"
    done = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "game"), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(root), "PATH": ""},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "written" in done.stdout
    png = (tmp_path / "out" / "p.png").read_bytes()
    assert png[:8] == imagewrite.PNG_MAGIC and imagewrite.png_size(png) == (2, 2) and pixels
