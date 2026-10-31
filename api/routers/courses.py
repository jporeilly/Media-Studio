"""Courses (W2): the courses folder's courses and workshops, and the preview of
what importing a workshop would make. Reads only: the walkthrough itself is
made by ``POST /api/walkthroughs/import`` (``api.routers.walkthroughs``).

Studio-wide like the music library and the docs: the courses are files an
administrator pointed the app at (Settings > Studio, ``courses_folder``), shared
by every account, so these routes take ``current_user`` and no owner. Nothing
outside the folder is ever read (``services.workshops``: names, not paths; no
link followed; every file proven under its folder; a guide over 2 MB refused
before it is read), and nothing here touches the screen, so the browser
edition has it all.

The Workshop video canvas (C2, C6) adds two things. ``GET /{course}/workshop-status``
answers, per workshop, the walkthrough the canvas would open, its project and
where its video stands (``services.video_to_course.workshop_status``; the
caller's own walkthroughs and projects, every account's for an administrator).
And the course is PUBLISHED through the Content Editor's own API, called thinly
(``services.content_editor``; the editor is the one publisher, Media Studio runs
no git on the courses): ``GET /publish/config`` probes the editor at the Studio
setting's address, ``GET /{course}/publish/diff`` asks what would change
upstream, and ``POST /{course}/publish`` asks it to commit and push, answering
its answer as it is - or ONE plain line when it is not running - and leaving the
``course.publish`` row.
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from api.audit import COURSE_PUBLISH, audit
from api.deps import current_user, may_access_project
from services import content_editor, studio_settings, video_to_course, walkthroughs, workshops
from services import projects as store

router = APIRouter(prefix="/courses", tags=["courses"])

#: A commit message is bounded like any other text a user types.
MAX_MESSAGE_CHARS = 500


class PublishCourseIn(BaseModel):
    """The commit message (the editor's own default when left empty); the editor commits the authoring repository
    too (``commit: true``), the canvas's one-button flow."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field("", max_length=MAX_MESSAGE_CHARS)


def _course_name(course: str) -> str:
    """A course id as the list names it (a folder name, never a path): 404 for anything else."""
    if not workshops.safe_name(course):
        raise HTTPException(status_code=404, detail="No such course.")
    return course


@contextmanager
def _editor_refusals():
    """The editor's answers as this route's: not running → 502 with the one plain line; an error the editor
    answered → its status and its own text (502 for a status outside HTTP's error range)."""
    try:
        yield
    except content_editor.ContentEditorDown as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except content_editor.ContentEditorError as exc:
        status = exc.status if 400 <= exc.status <= 599 else 502
        raise HTTPException(status_code=status, detail=str(exc)) from None


def _root():
    """The courses folder, or 400 with where to set it."""
    try:
        return workshops.courses_root()
    except workshops.NoCoursesFolder as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("")
def list_courses(user: dict = Depends(current_user)):
    """The courses: id, title, version, description and the sections with their workshops (slug, title,
    minutes, step count) in SUMMARY.md order. ``configured`` false, with the message, when the courses
    folder is not set - never an error: the page says where to set it."""
    try:
        root = workshops.courses_root()
    except workshops.NoCoursesFolder as exc:
        return {"configured": False, "message": str(exc), "courses": []}
    return {"configured": True, "message": None, "courses": [workshops.public_course(c) for c in workshops.list_courses(root)]}


@router.get("/{course}/workshops/{slug}/preview")
def preview_workshop(course: str, slug: str, user: dict = Depends(current_user)):
    """The parse: the chapters and the steps the import would make, each step's instruction and text,
    whether a picture was found, the launcher if any; the counts; nothing written. 404 for a course or a
    workshop that is not there (a name that is a path is not there), 400 with the reason for one the list
    left out (a file over 2 MB, not UTF-8 or not JSON)."""
    root = _root()
    try:
        loaded, workshop, text, folder = workshops.load_workshop(root, course, slug)
    except workshops.UnreadableCourse as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    parsed = workshops.parse_guide(text, loaded["launchers"])
    return workshops.preview(loaded, workshop, parsed, folder)


# -- the Workshop video canvas (C2) -----------------------------------------------------------------------------------

@router.get("/{course}/workshop-status")
def workshop_status(course: str, user: dict = Depends(current_user)):
    """Per workshop of the course: the walkthrough the canvas would open (the one imported from it, the most recent),
    its step and picture counts, the project made from it with its video, its Vimeo record and whether the guide holds
    the link - the caller's own records (every account's for an administrator), the guide read, nothing written. 404
    for a course that is not there; 400 when the courses folder is not set."""
    root = _root()
    loaded = workshops.load_course(root, _course_name(course))
    if loaded is None:
        raise HTTPException(status_code=404, detail="No such course.")
    mine = [r for r in walkthroughs.list_walkthroughs() if may_access_project(r, user)]
    projects = [p for p in store.list_projects() if may_access_project(p, user)]
    return {"course": course, "workshops": video_to_course.workshop_status(loaded, course, mine, projects)}


# -- the course published through the Content Editor (C6) -------------------------------------------------------------

@router.get("/publish/config")
def publish_config(user: dict = Depends(current_user)):
    """The editor at the Studio setting's address: ``reachable`` with the distribution repository's ``url`` and
    ``ref`` when it answered, else ``reachable`` false with the one plain line (not running) or the editor's own
    error text as ``message``. Never an error: the Publish stage says what it found. A read."""
    address = studio_settings.content_editor_url()
    try:
        cfg = content_editor.config()
    except (content_editor.ContentEditorDown, content_editor.ContentEditorError) as exc:
        return {"address": address, "reachable": False, "url": None, "ref": None, "message": str(exc)}
    return {"address": address, "reachable": True, "url": cfg.get("url"), "ref": cfg.get("ref"), "message": None}


@router.get("/{course}/publish/diff")
def publish_diff(course: str, user: dict = Depends(current_user)):
    """What publishing the course would change upstream, as the editor answers it, with one ``summary`` line ("2
    files change upstream: 01-first-win/guide.md, 01-first-win/manifest.json"). A read: no audit row."""
    with _editor_refusals():
        answer = content_editor.diff(_course_name(course))
    return {**answer, "summary": content_editor.diff_summary(answer)}


@router.post("/{course}/publish")
def publish_course(course: str, body: PublishCourseIn | None = None, user: dict = Depends(current_user)):
    """Publish the course through the editor: ``POST /api/courses/{course}/publish`` with ``{message, commit: true}``
    - the editor copies the course into its clone of the public repository, commits and pushes, and commits the
    authoring repository too; its answer is returned as it is with one ``summary`` line ("committed abc1234 and
    pushed (2 files)"). Audit row ``course.publish`` ("<course>: <message> -> <summary>"). Media Studio runs no git."""
    message = (body.message if body else "").strip()
    with _editor_refusals():
        answer = content_editor.publish(_course_name(course), message, commit=True)
    summary = content_editor.publish_summary(answer)
    said = message or "(the editor's default message)"
    audit(COURSE_PUBLISH, user=user, entity="course", entity_id=course, detail=f"{course}: {said} -> {summary}")
    return {**answer, "summary": summary}
