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
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.deps import current_user
from services import workshops

router = APIRouter(prefix="/courses", tags=["courses"])


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
