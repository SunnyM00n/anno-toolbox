"""rdm.batch: a job's files are written all or none."""

from __future__ import annotations

from annotoolbox.rdm import batch


def test_a_failed_write_leaves_no_file_of_the_job_behind(tmp_path):
    (tmp_path / "blocked").write_bytes(b"a file where a folder is needed")
    job = batch.SJob(tmp_path / "in.rdm", tmp_path / "out" / "a.bin")
    files = {tmp_path / "out" / "a.bin": b"1", tmp_path / "blocked" / "b.bin": b"2"}
    results = batch.run([job], lambda _job: (files, []))
    assert results[0].status == "failed"
    assert not (tmp_path / "out").exists()
    assert not results[0].written


def test_a_good_job_writes_every_file(tmp_path):
    job = batch.SJob(tmp_path / "in.rdm", tmp_path / "out" / "a.bin")
    files = {tmp_path / "out" / "a.bin": b"1", tmp_path / "out" / "sub" / "b.bin": b"2"}
    results = batch.run([job], lambda _job: (files, []))
    assert results[0].status == "written"
    assert (tmp_path / "out" / "sub" / "b.bin").read_bytes() == b"2"
