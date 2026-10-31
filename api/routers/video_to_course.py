"""Video to course (R1-R4): one button, one dialog, one job. ``GET /api/walkthroughs/{wid}/video-to-course`` answers what
the dialog opens with - the privacy, the privacies, whether a token is set, whether the studio allows the course
write, the course workshop (or none), where the link would go in its guide, and the project the walkthrough already
has with its video, its Vimeo record and whether the guide holds its link (R3). ``POST …/video-to-course`` starts the
ONE job ``video_to_course`` (``services.video_to_course.JOB_KIND``) on the walkthrough's project: the project is made
as Make a video makes it (``services.projects.create_walkthrough_project``) or REUSED and refreshed from the
walkthrough as it is saved now (``refresh_walkthrough_project``), and the job renders (``api.routers.projects
.prepare_generate``'s work, run inside this job), publishes (``services.vimeo.publish``) and places
(``services.course_video.write``, the plan computed again right before it) in turn, with one progress line and the
existing audit rows - ``walkthrough.make_video`` (the route, as Make a video writes it), ``project.vimeo_publish`` and
``walkthrough.place_video`` (the job, as their routes write them) - and no new action. ``mode: "place"`` is **Just place
the existing video again**: the place phase alone, for a project already on Vimeo.

Cancel works in every phase: the render's own (nothing written before the video, the finished sidecars kept after it),
the upload's (the unfinished video removed from Vimeo), and before the place (nothing half-written). Every route takes
``current_user`` and answers the walkthrough's owner or an administrator (``_owned``, the walkthroughs router's rule).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from api.audit import PROJECT_VIMEO_PUBLISH, WALKTHROUGH_MAKE_VIDEO, WALKTHROUGH_PLACE_VIDEO, audit
from api.deps import current_user
from api.routers.projects import prepare_generate
from api.routers.vimeo import default_description
from api.routers.walkthroughs import _owned
from api.schemas import GenerateRequest, WalkthroughName
from api.store import display_name_of
from services import course_export, course_video, jobs, studio_settings, video_to_course, vimeo, walkthroughs, workshops
from services import projects as store
from utils.logger import get_logger

log = get_logger("VIDEO_TO_COURSE")

router = APIRouter(prefix="/walkthroughs/{wid}/video-to-course", tags=["video-to-course"])

Privacy = Literal["unlisted", "private", "anybody"]
Mode = Literal["full", "place"]


class VideoToCourseIn(BaseModel):
    """What the one dialog sends: the video's name (the walkthrough's), Make a video's two ticks, the Vimeo privacy
    (the studio's default when left out), the course caption ("" for the walkthrough's name), the Welcome Tour tick,
    and the ``mode`` - "full" renders, publishes and places (again); "place" only places the existing video again."""

    model_config = ConfigDict(extra="forbid")

    name: WalkthroughName
    notes: StrictBool = False
    zoom: StrictBool = True
    privacy: Privacy | None = None
    caption: str = Field("", max_length=200)
    welcome: StrictBool = False
    mode: Mode = "full"


def _source_of(rec: dict) -> dict | None:
    """The walkthrough's course workshop ``source`` when it was imported from one, else None."""
    source = rec.get("source")
    return source if walkthroughs.is_workshop_source(source) else None


def _course_of(source: dict) -> dict:
    return {"course": str(source["course"]), "slug": course_export.source_slugs(source)[0], "title": source.get("title") or str(source["course"])}


def _project_of(wid: str) -> dict | None:
    return video_to_course.project_for(store.list_projects(), wid)


@router.get("")
def video_to_course_defaults(wid: str, user: dict = Depends(current_user)):
    """What the dialog opens with (R1, R3): the walkthrough's name, the studio's privacy and the privacies, whether a
    Vimeo token is set, whether the studio allows the course write, the course workshop the link goes into (None for
    a walkthrough from no course workshop), where the link would go in its guide now (the plan computed with the
    project's link, else a stand-in; nothing written) and the project the walkthrough already has - with its video,
    its Vimeo record and whether the guide holds its link. A read: no audit row."""
    rec = _owned(wid, user)
    source = _source_of(rec)
    project = _project_of(wid)
    link = (video_to_course.vimeo_view(project) or {}).get("link") if project else None
    place = video_to_course.place_facts(source, rec.get("name") or "", link) if source else None
    return {
        "name": rec.get("name") or "",
        "privacy": studio_settings.vimeo_privacy(),
        "privacies": [{"value": key, **meta} for key, meta in vimeo.PRIVACIES.items()],
        "token_set": bool(studio_settings.vimeo_token()),
        "allowed": studio_settings.courses_export_allowed(),
        "course": _course_of(source) if source else None,
        "place": place,
        "project": video_to_course.project_view(project, place if link else None) if project else None,
        "job_kind": video_to_course.JOB_KIND,
    }


@router.post("")
def video_to_course_start(wid: str, body: VideoToCourseIn, user: dict = Depends(current_user)):
    """Start the one job (R2). ``mode: "full"``: 400 without a Vimeo token BEFORE anything is created, 400 for a
    walkthrough with no steps; the walkthrough's project is made (as Make a video makes it) or reused and refreshed
    from the walkthrough as it is saved now; the ``video_to_course`` job renders, publishes and places; the
    ``walkthrough.make_video`` row is written here as Make a video writes it, the other two by the job's phases. A
    refusal of the render's start (an unknown provider) removes a project made for it, as Make a video does. ``mode:
    "place"``: the existing video placed again - 400 without a project on Vimeo or without a course. Answers
    ``{project_id, job_id, created}``; 409 while a job holds the project (``ProjectBusy``, the app's handler)."""
    rec = _owned(wid, user)
    source = _source_of(rec)
    caption, welcome = body.caption, body.welcome
    existing = _project_of(wid)
    if body.mode == "place":
        if existing is None or not video_to_course.vimeo_view(existing):
            raise HTTPException(status_code=400, detail=video_to_course.PLACE_NEEDS_VIDEO_MESSAGE)
        if source is None:
            raise HTTPException(status_code=400, detail=course_video.NOT_FROM_COURSE_MESSAGE)
        record = existing
        prepared = None
        created = False
        token = ""
    else:
        if not rec.get("steps"):
            raise HTTPException(status_code=400, detail=store.NO_STEPS_MESSAGE)
        token = studio_settings.vimeo_token()
        if not token:
            raise HTTPException(status_code=400, detail=vimeo.NO_TOKEN_MESSAGE)
        try:
            if existing is not None:
                jobs.require_idle(existing["id"])
                record = store.refresh_walkthrough_project(existing, name=body.name, notes=body.notes, zoom=body.zoom)
                created = False
            else:
                record = store.create_walkthrough_project(
                    wid, name=body.name, notes=body.notes, zoom=body.zoom, owner_id=user["id"], owner_name=display_name_of(user),
                )
                created = True
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except LookupError:
            raise HTTPException(status_code=404, detail="No such walkthrough.") from None
        try:
            prepared = prepare_generate(record, GenerateRequest())
        except BaseException:
            # As Make a video: a project made for this job alone goes with a start that is refused.
            if created:
                try:
                    store.delete_project(record["id"])
                except store.ProjectDeleteError as exc:  # pragma: no cover - a handle held for an instant
                    log.warning("The project %s made for a video that could not start was not removed: %s", record["id"], exc)
            raise
    pid = record["id"]
    name = record.get("name") or body.name
    privacy = body.privacy or studio_settings.vimeo_privacy()
    mode = body.mode

    def work(progress):
        cancel_check = jobs.cancel_check_here()
        if mode == "full":
            # -- the render (0-0.55): the Generate card's own job, run here, its progress mapped in.
            closing = {"line": ""}

            def render_sub(fraction: float, message: str = "") -> None:
                if fraction >= 1.0 and message:
                    closing["line"] = message
                progress(*video_to_course.render_progress(fraction, message))

            try:
                rendered = prepared.work(render_sub)
            except Exception as exc:  # noqa: BLE001 - the render's own words, after the phase's name
                raise RuntimeError(video_to_course.failed(video_to_course.RENDER_PHASE, str(exc))) from None
            if isinstance(rendered, dict) and rendered.get("cancelled"):
                progress(1.0, closing["line"] or video_to_course.RENDER_CANCELLED_FALLBACK)
                return {"cancelled": True, "phase": "render", "project_id": pid}
            # -- the upload (0.55-0.85) and Vimeo's processing (0.85-0.95).
            current = store.get_project(pid) or record
            if not current.get("output_video"):
                raise RuntimeError(video_to_course.failed(video_to_course.RENDER_PHASE, vimeo.NO_VIDEO_MESSAGE))
            path = store.PROJECTS_DIR / pid / current["output_video"]
            try:
                published = vimeo.publish(
                    path, token=token, title=name, description=default_description(current), privacy=privacy,
                    progress=lambda f, m="": progress(*video_to_course.publish_progress(f, m)), cancel_check=cancel_check,
                )
            except vimeo.VimeoError as exc:
                raise RuntimeError(video_to_course.failed(video_to_course.UPLOAD_PHASE, str(exc))) from None
            if published is None:
                progress(1.0, vimeo.CANCELLED_MESSAGE)
                return {"cancelled": True, "phase": "publish", "project_id": pid}
            with store.project_lock(pid):
                current = store.get_project(pid) or current
                old = current.get("vimeo") or {}
                previous = ([str(old["id"])] + [str(x) for x in (old.get("previous") or [])]) if old.get("id") else []
                current["vimeo"] = {**published, "previous": previous}
                store.save_project(current)
            audit(PROJECT_VIMEO_PUBLISH, user=user, entity="project", entity_id=pid,
                  detail=f"{current.get('name') or name} -> vimeo {published['id']} ({published['privacy']})")
        else:
            current = store.get_project(pid) or record
        vimeo_now = video_to_course.vimeo_view(current)
        link = vimeo_now["link"] if vimeo_now else ""
        result = {"project_id": pid, "vimeo": current.get("vimeo"), "link": link, "course": _course_of(source) if source else None,
                  "placed": False, "guide": None, "written": [], "lines": [], "allowed": studio_settings.courses_export_allowed()}
        if source is None:
            progress(1.0, video_to_course.done_line(link, course=False, allowed=True, guide=None, written=False))
            return result
        # -- the place (0.95-1.0): the plan computed again right before the write; nothing half-written on a cancel.
        progress(video_to_course.PLACE_AT, video_to_course.PLACING_LINE)
        if cancel_check():
            progress(1.0, video_to_course.CANCELLED_PLACE_MESSAGE.format(link=link))
            return {**result, "cancelled": True, "phase": "place"}
        result["lines"] = course_video.figure_block(link, caption or (current.get("walkthrough_name") or name))
        if not result["allowed"]:
            progress(1.0, video_to_course.done_line(link, course=True, allowed=False, guide=None, written=False))
            return result
        try:
            placed = video_to_course.place(current, caption=caption, welcome=welcome, cancel_check=cancel_check)
        except (course_video.NotPublished, course_video.NotFromCourse, course_video.NoPlaceholder, workshops.NoCoursesFolder,
                workshops.UnreadableCourse, course_export.ExportRefused, course_export.GuideChanged, LookupError) as exc:
            raise RuntimeError(video_to_course.failed(video_to_course.PLACE_PHASE, str(exc))) from None
        if placed is None:
            progress(1.0, video_to_course.CANCELLED_PLACE_MESSAGE.format(link=link))
            return {**result, "cancelled": True, "phase": "place"}
        audit(WALKTHROUGH_PLACE_VIDEO, user=user, entity="walkthrough", entity_id=wid,
              detail=course_video.audit_detail(placed, str(vimeo_now["id"] if vimeo_now else "")))
        result.update({"placed": True, "guide": placed["guide"], "written": placed["written"], "lines": []})
        progress(1.0, video_to_course.done_line(link, course=True, allowed=True, guide=placed["guide"], written=bool(placed["written"])))
        return result

    try:
        job_id = jobs.start(video_to_course.JOB_KIND, work, project_id=pid, user_id=user["id"])
    except BaseException:
        if created:
            try:
                store.delete_project(pid)
            except store.ProjectDeleteError as exc:  # pragma: no cover - a handle held for an instant
                log.warning("The project %s made for a video that could not start was not removed: %s", pid, exc)
        raise
    if mode == "full":
        steps = record.get("slide_count") or 0
        audit(WALKTHROUGH_MAKE_VIDEO, user=user, entity="walkthrough", entity_id=wid,
              detail=f"{record['name']} -> {pid}: {steps} step{'' if steps == 1 else 's'}")
    return {"project_id": pid, "job_id": job_id, "created": created}
