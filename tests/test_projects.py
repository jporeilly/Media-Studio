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
    updated = projects.set_transcript(rec["id"], [{"start": 0.0, "end": 1.0, "text": "hi"}])
    assert updated["transcript"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert projects.get_project(rec["id"])["transcript"][0]["text"] == "hi"
    assert projects.set_transcript("aabbccddeeff", []) is None  # valid shape, absent
