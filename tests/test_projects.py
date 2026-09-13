"""Tests for the project store: kind detection, import, list/get/delete."""

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

    saved = tmp_projects_dir / rec["id"] / "Deck One.pptx"
    assert saved.is_file() and saved.read_bytes() == b"1234567890"


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
    a = projects.import_upload("a.pptx", b"a")
    b = projects.import_upload("b.mp4", b"bb")

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
