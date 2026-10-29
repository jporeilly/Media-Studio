"""Publish to Vimeo (V2) and Place in the course (V3), on a project's rendered video.

``GET /api/projects/{pid}/vimeo`` answers what the Publish dialog opens with (the title, the description - a
walkthrough project's transcript, cut to Vimeo's limit - the privacy from the studio setting, whether a token is set,
the record's ``vimeo`` and the course workshop the link can go into); ``POST …/vimeo/publish`` starts the
``vimeo_publish`` job (one job per project, like generate; ``services.vimeo.publish`` over the token from the studio
settings), which writes ``vimeo`` onto the record when the video is on Vimeo (Publish again makes a NEW video and
keeps the old id in ``vimeo.previous``: its link may be in a course) and leaves the ``project.vimeo_publish`` row;
``POST …/vimeo/place/plan`` previews the link's place in the workshop's guide (``services.course_video``: nothing
written, no row) and ``POST …/vimeo/place`` writes it - behind the studio setting "Allow export into the Courses
folder" (403), with the files' sha256 from the preview (409) - and leaves the ``walkthrough.place_video`` row.

Every route takes ``current_user`` and answers the project's owner or an administrator (``require_project``).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

from api.audit import PROJECT_VIMEO_PUBLISH, WALKTHROUGH_PLACE_VIDEO, audit
from api.deps import current_user, require_project
from api.routers.projects import _project_file
from services import course_export, course_video, jobs, studio_settings, vimeo, walkthroughs, workshops
from services import projects as store
from services.walkthrough_video import load_snapshot
from utils.logger import get_logger

log = get_logger("VIMEO")

router = APIRouter(prefix="/projects/{pid}/vimeo", tags=["vimeo"])

#: The job's kind: the project page follows it as it follows a render.
VIMEO_JOB_KIND = "vimeo_publish"
NO_VIDEO_FILE = "No generated video for this project."

Privacy = Literal["unlisted", "private", "anybody"]


class PublishIn(BaseModel):
    """What the dialog sends: the title (the project's name by default), the description and the privacy."""

    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=vimeo.VIMEO_NAME_MAX)]
    description: str = Field("", max_length=vimeo.VIMEO_DESCRIPTION_MAX)
    privacy: Privacy


class PlacePlanIn(BaseModel):
    """The place's options: the caption ("" for the default), the Welcome tour, and whether the figure is inserted
    after the first section heading when the guide has no placeholder."""

    model_config = ConfigDict(extra="forbid")

    caption: str = Field("", max_length=200)
    welcome: StrictBool = False
    insert: StrictBool = False


class PlaceIn(PlacePlanIn):
    """The write: the options and each file's sha256 as the preview showed it (the manifest's and course.json's
    when the preview had them)."""

    guide_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    course_sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")


def default_description(record: dict) -> str:
    """A walkthrough project's transcript (from its snapshot), cut to Vimeo's limit; "" for any other kind."""
    if record.get("kind") != "walkthrough":
        return ""
    try:
        snapshot = load_snapshot(store.PROJECTS_DIR / record["id"] / record["source_filename"])
    except (ValueError, KeyError, OSError):
        return ""
    return vimeo.cut_description(walkthroughs.transcript_markdown(snapshot["record"]))


@contextmanager
def _place_refusals():
    """Every refusal of a place as its status: 409 a file changed since the preview; 400 not published, not from a
    course, no placeholder, no courses folder, an unreadable course, a link where a folder should be; 404 a course or
    workshop no longer there. Nothing is written in any of them."""
    try:
        yield
    except course_export.GuideChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except (course_video.NotPublished, course_video.NotFromCourse, course_video.NoPlaceholder, workshops.NoCoursesFolder,
            workshops.UnreadableCourse, course_export.ExportRefused) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("")
def vimeo_defaults(pid: str, user: dict = Depends(current_user)):
    """What the Publish dialog opens with, and what the panel shows: the title (the project's name), the description
    (a walkthrough's transcript), the privacy (the studio's), the privacies, whether a token is set, the record's
    ``vimeo`` and the course workshop the link can be placed in (None for a project of another kind)."""
    record = require_project(pid, user)
    return {
        "title": (record.get("name") or "")[:vimeo.VIMEO_NAME_MAX],
        "description": default_description(record),
        "privacy": studio_settings.vimeo_privacy(),
        "privacies": [{"value": key, **meta} for key, meta in vimeo.PRIVACIES.items()],
        "token_set": bool(studio_settings.vimeo_token()),
        "name_max": vimeo.VIMEO_NAME_MAX,
        "description_max": vimeo.VIMEO_DESCRIPTION_MAX,
        "vimeo": record.get("vimeo"),
        "course": course_video.course_of(record),
        "allowed": studio_settings.courses_export_allowed(),
    }


@router.post("/publish")
def publish_video(pid: str, body: PublishIn, user: dict = Depends(current_user)):
    """Publish the project's rendered video to Vimeo as a job (``vimeo_publish``, one job per project: 409 while
    another holds it): the video created with the tus approach, uploaded in chunks, polled until processed; the
    record then gains ``vimeo`` (a video published before goes into ``vimeo.previous``, never deleted) and the
    ``project.vimeo_publish`` row is written. 400 without a rendered video or a token. A cancel between chunks
    removes the unfinished video from Vimeo and the job ends ``done`` with ``cancelled``."""
    record = require_project(pid, user)
    if not record.get("output_video"):
        raise HTTPException(status_code=400, detail=vimeo.NO_VIDEO_MESSAGE)
    token = studio_settings.vimeo_token()
    if not token:
        raise HTTPException(status_code=400, detail=vimeo.NO_TOKEN_MESSAGE)
    path = _project_file(pid, record["output_video"], NO_VIDEO_FILE)
    title, description, privacy = body.title, body.description, body.privacy
    name = record.get("name") or title

    def work(progress):
        result = vimeo.publish(path, token=token, title=title, description=description, privacy=privacy, progress=progress,
                               cancel_check=jobs.cancel_check_here())
        if result is None:
            progress(1.0, vimeo.CANCELLED_MESSAGE)
            return {"cancelled": True}
        with store.project_lock(pid):
            current = store.get_project(pid) or record
            old = current.get("vimeo") or {}
            previous = ([str(old["id"])] + [str(x) for x in (old.get("previous") or [])]) if old.get("id") else []
            current["vimeo"] = {**result, "previous": previous}
            store.save_project(current)
        audit(PROJECT_VIMEO_PUBLISH, user=user, entity="project", entity_id=pid,
              detail=f"{current.get('name') or name} -> vimeo {result['id']} ({result['privacy']})")
        return {"vimeo": current["vimeo"]}

    job_id = jobs.start(VIMEO_JOB_KIND, work, project_id=pid, user_id=user["id"])
    return {"job_id": job_id}


@router.post("/place/plan")
def place_plan(pid: str, body: PlacePlanIn | None = None, user: dict = Depends(current_user)):
    """The dry run of Place in the course: where the link goes in the workshop's guide (the placeholder, a figure of
    ours, or nowhere - with the insertion offered), the caption, the guide's changes as a diff, the manifest's and
    course.json's, each file's sha256 now, and whether the studio allows the write. Nothing is written; no row."""
    record = require_project(pid, user)
    body = body or PlacePlanIn()
    with _place_refusals():
        plan = course_video.compute(record, caption=body.caption, welcome=body.welcome, insert=body.insert)
    return course_video.plan_view(plan)


@router.post("/place")
def place_video(pid: str, body: PlaceIn, user: dict = Depends(current_user)):
    """Write the link into the course: 403 while "Allow export into the Courses folder" is off; 409 when a file's
    sha256 is not the preview's (nothing written); 400 when the guide has nowhere for it and the insertion was not
    asked; else the guide, the manifest and - with the Welcome tour - course.json, under one journal. Audit row
    ``walkthrough.place_video``."""
    record = require_project(pid, user)
    if not studio_settings.courses_export_allowed():
        raise HTTPException(status_code=403, detail=course_video.PLACE_OFF_MESSAGE)
    with _place_refusals():
        result = course_video.write(record, caption=body.caption, welcome=body.welcome, insert=body.insert, guide_sha256=body.guide_sha256,
                                    manifest_sha256=body.manifest_sha256, course_sha256=body.course_sha256)
    audit(WALKTHROUGH_PLACE_VIDEO, user=user, entity="walkthrough", entity_id=str(record.get("walkthrough_id") or pid),
          detail=course_video.audit_detail(result, str((record.get("vimeo") or {}).get("id") or "")))
    return result
