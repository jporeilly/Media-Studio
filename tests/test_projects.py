"""Tests for the project store: kind detection, import, list/get/delete, and
the owner recorded on the record.

The store only RECORDS the owner; who may see or touch a project is decided in
``api.deps`` and tested in ``test_project_ownership.py``.
"""

import json

import pytest

from services import projects


@pytest.fixture(autouse=True)
def tmp_projects_dir(tmp_path, monkeypatch):
    """Isolate every test to a throwaway PROJECTS_DIR."""
    d = tmp_path / "projects"
    monkeypatch.setattr(projects, "PROJECTS_DIR", d)
    return d


def test_kind_for_suffix():
    assert projects.kind_for_suffix(".pptx") == "deck"
    assert projects.kind_for_suffix(".PPTX") == "deck"  # case-insensitive
    assert projects.kind_for_suffix(".pdf") == "pdf"
    assert projects.kind_for_suffix(".mp4") == "video"
    assert projects.kind_for_suffix(".mov") == "video"
    assert projects.kind_for_suffix(".xyz") is None
    assert projects.kind_for_suffix("") is None


def test_import_upload_creates_record_and_saves_file(tmp_projects_dir):
    rec = projects.import_upload("Deck One.pptx", b"1234567890")

    assert rec["kind"] == "deck"
    assert rec["name"] == "Deck One"
    assert rec["source_filename"] == "Deck One.pptx"
    assert rec["size_bytes"] == 10
    assert rec["slide_count"] is None  # not a real pptx -> reader fails -> None
    assert rec["created_at"]
    # No owner named: the record says so rather than leaving the key out, so a
    # reader never has to guess whether the field is missing or empty.
    assert rec["owner_id"] is None and rec["owner_name"] is None

    saved = tmp_projects_dir / rec["id"] / "Deck One.pptx"
    assert saved.is_file() and saved.read_bytes() == b"1234567890"


def test_import_upload_records_the_owner_on_the_record_not_in_a_database(tmp_projects_dir):
    """Ownership lives in project.json so a project stays a directory you can
    zip, and the display name is denormalised so it still reads after the
    account is gone."""
    rec = projects.import_upload("Deck.pptx", b"x", owner_id="u-123", owner_name="Olive Owner")
    assert (rec["owner_id"], rec["owner_name"]) == ("u-123", "Olive Owner")

    meta = json.loads((tmp_projects_dir / rec["id"] / "project.json").read_text(encoding="utf-8"))
    assert meta["owner_id"] == "u-123" and meta["owner_name"] == "Olive Owner"
    assert projects.get_project(rec["id"])["owner_name"] == "Olive Owner"


def test_import_upload_detects_video():
    rec = projects.import_upload("clip.mov", b"abc")
    assert rec["kind"] == "video"
    assert rec["slide_count"] is None


def test_import_upload_rejects_unsupported():
    with pytest.raises(ValueError):
        projects.import_upload("notes.txt", b"hello")


def test_import_upload_strips_directory_from_filename(tmp_projects_dir):
    # a crafted filename must not escape the project directory
    rec = projects.import_upload("../../evil.pptx", b"x")
    assert rec["source_filename"] == "evil.pptx"
    assert (tmp_projects_dir / rec["id"] / "evil.pptx").is_file()


def test_list_get_delete_cycle():
    a = projects.import_upload("a.pptx", b"a", owner_id="u-1", owner_name="One")
    b = projects.import_upload("b.mp4", b"bb", owner_id="u-2", owner_name="Two")

    # The store lists EVERY owner's projects; the API filters (api/deps.py).
    listed = projects.list_projects()
    assert {p["id"] for p in listed} == {a["id"], b["id"]}
    assert listed[0]["created_at"] >= listed[1]["created_at"]  # newest first

    assert projects.get_project(a["id"])["name"] == "a"
    assert projects.get_project("does-not-exist") is None

    assert projects.delete_project(a["id"]) is True
    assert projects.get_project(a["id"]) is None
    assert projects.delete_project(a["id"]) is False  # already gone
    assert {p["id"] for p in projects.list_projects()} == {b["id"]}


def test_list_projects_empty_when_dir_absent():
    assert projects.list_projects() == []


def test_invalid_pid_cannot_escape_the_store(tmp_projects_dir):
    # A crafted id (".." / traversal) must be rejected before any path join,
    # so get/delete can never read or rmtree outside PROJECTS_DIR.
    assert projects._valid_pid("..") is False
    assert projects._valid_pid("../../etc") is False
    assert projects._valid_pid("deadbeef") is False  # not 12 hex chars
    assert projects.get_project("..") is None
    assert projects.get_project("../../secrets") is None
    assert projects.delete_project("..") is False
    assert projects.set_transcript("..", []) is None

    real = projects.import_upload("a.pptx", b"x")
    assert projects._valid_pid(real["id"]) is True


def test_set_transcript_saves_and_missing_returns_none():
    rec = projects.import_upload("clip.mp4", b"v")
    updated, dropped = projects.set_transcript(rec["id"], [{"start": 0.0, "end": 1.0, "text": "hi"}])
    assert updated["transcript"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert dropped == 0, "nothing to drop: the project had no adjustments"
    assert projects.get_project(rec["id"])["transcript"][0]["text"] == "hi"
    assert projects.set_transcript("aabbccddeeff", []) is None  # valid shape, absent


def test_a_delete_that_cannot_remove_a_file_leaves_the_project_whole(monkeypatch):
    """Windows will not unlink a file a player or an encoder still has open.

    The old code called ``rmtree`` with ``ignore_errors=True`` and returned True
    regardless, and the tree walk reached project.json before the video: the
    project fell out of the list - the list is built from project.json - while
    its largest file stayed on disk forever, invisible and unreferenced. One
    real case left a 76 MB orphan behind.

    The fix after that removed every file it COULD before finding the locked
    one, then raised a 409 promising nothing was half-removed - and a real
    project lost its extracted audio, its re-voiced video and its waveform
    cache to exactly that promise. So the gate is now the RENAME of the whole
    directory, one call that moves everything or nothing: this test refuses
    the rename (the portable way to stand in for a file held open, which only
    Windows enforces) and checks that a refused delete has touched nothing,
    is still listed, and names the file in use.
    """
    from pathlib import Path

    pid = projects.import_upload("clip.mp4", b"video-bytes")["id"]
    pdir = projects.PROJECTS_DIR / pid
    locked = pdir / "clip.mp4"
    (pdir / "audio.wav").write_bytes(b"RIFF")  # a second file the old code would have removed first
    real_rename = Path.rename

    def _rename(self, target):
        if self == pdir:
            raise OSError(5, "Access is denied")  # what Windows says for a directory with an open file in it
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _rename)
    # The probe that names the culprit is Windows-only; stand in for it here so
    # the message contract is tested on every platform.
    monkeypatch.setattr(projects, "_files_in_use", lambda _pdir: ["clip.mp4"])

    with pytest.raises(projects.ProjectDeleteError) as caught:
        projects.delete_project(pid)

    assert "clip.mp4" in str(caught.value)
    assert "try again" in str(caught.value)
    assert projects.get_project(pid) is not None, "still listed, so it can be retried"
    assert [p["id"] for p in projects.list_projects()] == [pid]
    assert locked.is_file() and (pdir / "audio.wav").is_file(), "NOTHING was removed - not even the files that were not locked"
    assert not [d for d in projects.PROJECTS_DIR.iterdir() if projects.DELETING_SUFFIX in d.name], "no renamed directory was left behind"


def test_a_delete_with_a_file_really_held_open_is_refused_whole(tmp_projects_dir):
    """The real thing, on the platform that has it: a file held open by another
    handle stops the directory rename, and the probe names it. Windows only -
    POSIX lets a directory with open files be renamed, so there is nothing to
    refuse there."""
    import os

    if os.name != "nt":
        pytest.skip("only Windows refuses to rename a directory with an open file in it")

    pid = projects.import_upload("clip.mp4", b"video-bytes")["id"]
    pdir = projects.PROJECTS_DIR / pid
    (pdir / "audio.wav").write_bytes(b"RIFF")

    with open(pdir / "clip.mp4", "rb"):
        with pytest.raises(projects.ProjectDeleteError) as caught:
            projects.delete_project(pid)

    assert "clip.mp4" in str(caught.value), "the message names the file that is in use"
    assert "audio.wav" not in str(caught.value), "and only that one"
    assert (pdir / "clip.mp4").is_file() and (pdir / "audio.wav").is_file() and (pdir / "project.json").is_file()
    assert [p["id"] for p in projects.list_projects()] == [pid]

    # Handle released: the same delete now succeeds and takes everything.
    assert projects.delete_project(pid) is True
    assert not pdir.exists()
    assert projects.list_projects() == []


def test_an_interrupted_removal_is_invisible_and_swept_by_the_next_delete(tmp_projects_dir, monkeypatch):
    """Once the directory is renamed the project is gone as far as any user can
    see; if removing the renamed directory then fails, the delete still
    succeeds, the leftover is not listed, and the next delete finishes the job."""
    pid = projects.import_upload("clip.mp4", b"video-bytes")["id"]
    real_rmtree = projects.shutil.rmtree
    refused = {"once": True}

    def _rmtree(path, *args, **kwargs):
        if refused["once"] and projects.DELETING_SUFFIX in Path(path).name:
            refused["once"] = False
            raise OSError(32, "The process cannot access the file because it is being used")
        return real_rmtree(path, *args, **kwargs)

    from pathlib import Path

    monkeypatch.setattr(projects.shutil, "rmtree", _rmtree)

    assert projects.delete_project(pid) is True, "the project is gone from the store either way"
    assert projects.get_project(pid) is None
    assert projects.list_projects() == [], "the renamed directory is not a listed project"
    leftovers = [d for d in projects.PROJECTS_DIR.iterdir() if projects.DELETING_SUFFIX in d.name]
    assert len(leftovers) == 1, "its files are still on disk, invisible, waiting for the sweep"

    other = projects.import_upload("other.mp4", b"video-bytes")["id"]
    assert projects.delete_project(other) is True
    assert not [d for d in projects.PROJECTS_DIR.iterdir() if projects.DELETING_SUFFIX in d.name], "the next delete swept the leftover"


def test_a_successful_delete_takes_the_whole_directory(tmp_projects_dir):
    """Including sub-directories the engine made, and leaving no empty shell -
    a directory with no project.json is invisible to the list forever."""
    pid = projects.import_upload("clip.mp4", b"video-bytes")["id"]
    inner = projects.PROJECTS_DIR / pid / "revoice"
    inner.mkdir()
    (inner / "state.json").write_text("{}", encoding="utf-8")

    assert projects.delete_project(pid) is True
    assert not (projects.PROJECTS_DIR / pid).exists()
    assert projects.get_project(pid) is None
    assert projects.list_projects() == []
