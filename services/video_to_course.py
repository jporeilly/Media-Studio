"""Video to course (R1-R4) and the Workshop video canvas's facts (C2, C5): the pure half of the ONE job that renders a
walkthrough's video, publishes it to Vimeo and places the link in the course workshop's guide - the phases' progress
mapping, the lines, which walkthrough a workshop has and which project a walkthrough has, where the link would go,
and the place itself with the guide's sha256 read just before the write. The job's ``work`` and the routes are
``api.routers.video_to_course``; the render is ``api.routers.projects.prepare_generate``'s, the publish
``services.vimeo.publish``, the write ``services.course_video.write`` - nothing of the three is changed here, only
called.

**The one job**, ``video_to_course`` (:data:`JOB_KIND`), runs on the walkthrough's PROJECT - made as Make a video makes
it, or REUSED (R3: the newest project made from the walkthrough, found by its ``walkthrough_id``; its snapshot and
stills refreshed from the walkthrough as it is saved now, ``services.projects.refresh_walkthrough_project``) - so the
video stays under Projects and the project page's Vimeo panel shows the result. Its phases and their share of the
progress bar: the render 0-0.55 (the render's own messages after "Rendering the video…", :func:`render_progress`),
the upload 0.55-0.85 ("Uploading to Vimeo… 150 of 400 MB"), Vimeo's processing 0.85-0.95, the place 0.95-1.0
(:func:`publish_progress`), then the Done line. Each phase's failure is that service's own message after the phase's
name (:func:`failed`: "Uploading to Vimeo failed: …"); a failure after the render keeps the rendered video on the
project, one after the publish keeps the Vimeo record, so the project page's own buttons take it from there.
"""

from __future__ import annotations

from services import course_export, course_video, walkthroughs, workshops

#: The job's kind: the builder and the canvas follow it; the project page shows it on the Generate card.
JOB_KIND = "video_to_course"

#: Where each phase ends on the progress bar.
RENDER_TO = 0.55
UPLOAD_TO = 0.85
#: Where "Vimeo is processing the video…" is reported (inside its 0.85-0.95 band), and where the place begins.
PROCESS_AT = 0.90
PLACE_AT = 0.95

RENDER_PHASE = "Rendering the video"
UPLOAD_PHASE = "Uploading to Vimeo"
PLACE_PHASE = "Placing the link in the course"
RENDER_PREFIX = "Rendering the video…"
UPLOAD_PREFIX = "Uploading to Vimeo…"
#: ``services.vimeo.publish``'s own first words, kept as they are (nothing is uploading yet).
CREATING_LINE = "Creating the video on Vimeo…"
PROCESSING_LINE = "Vimeo is processing the video…"
PLACING_LINE = "Placing the link in the course…"

#: The Done lines.
DONE_PLACED = "Done: {link} placed in {guide}. Review and publish it in the Content Editor."
DONE_ALREADY_PLACED = "Done: {link} is already in {guide}. Review and publish it in the Content Editor."
DONE_PUBLISHED = "Done: {link} is on Vimeo."
DONE_NOT_PLACED = ("Done: {link} is on Vimeo. Allow export into the Courses folder is off, so the link was not placed: "
                   "copy the lines into the guide.")
CANCELLED_PLACE_MESSAGE = "Cancelled before the link was placed; the video is on Vimeo ({link})."
RENDER_CANCELLED_FALLBACK = "Render cancelled; the project is as it was."

#: The dialog's lines.
NO_COURSE_LINE = ("This walkthrough came from no course workshop: the video is rendered and published to Vimeo; copy its "
                  "link where you need it.")
TOKEN_SET_LINE = "A Vimeo token is set."
NO_TOKEN_LINE = "No Vimeo token: set one in Settings › Studio first."
EXPORT_ON_LINE = "Allow export into the Courses folder is on."
EXPORT_OFF_LINE = ("Allow export into the Courses folder is off: the video is rendered and published, and the lines to paste "
                   "are shown at the end.")

#: The refusals of a "just place again".
PLACE_NEEDS_VIDEO_MESSAGE = "Just place the existing video again needs a video on Vimeo: render, publish and place first."
#: A stand-in link for the plan computed before any video exists (where the link WOULD go).
STAND_IN_LINK = "https://vimeo.com/0"


def failed(phase: str, reason: str) -> str:
    """"<phase> failed: <the service's own message>"."""
    return f"{phase} failed: {reason}"


# -- the progress --------------------------------------------------------------------------------------------------

def render_progress(fraction: float, message: str = "") -> tuple[float, str]:
    """The render's own progress mapped into 0-0.55 after "Rendering the video…" (its message kept word for word)."""
    share = max(0.0, min(1.0, float(fraction)))
    text = f"{RENDER_PREFIX} {message.strip()}" if (message or "").strip() else RENDER_PREFIX
    return RENDER_TO * share, text


def publish_progress(fraction: float, message: str = "") -> tuple[float, str]:
    """``services.vimeo.publish``'s progress mapped into the job's: its create line at 0.55 as it is, its "Uploading…
    150 of 400 MB" (0.02-0.90) as "Uploading to Vimeo… 150 of 400 MB" over 0.55-0.85, its "Vimeo is processing the
    video…" (0.92) at 0.90, and its "Published: <link>" (1.0) at 0.95, where the place begins."""
    f = max(0.0, min(1.0, float(fraction)))
    text = (message or "").strip()
    if f >= 1.0:
        return PLACE_AT, text or PROCESSING_LINE
    if f >= 0.92:
        return PROCESS_AT, PROCESSING_LINE
    if f <= 0.0:
        return RENDER_TO, text or CREATING_LINE
    share = min(f, 0.90) / 0.90
    if text.startswith("Uploading…"):
        text = UPLOAD_PREFIX + text[len("Uploading…"):]
    elif text:
        text = f"{UPLOAD_PREFIX} {text}"
    else:
        text = UPLOAD_PREFIX
    return RENDER_TO + (UPLOAD_TO - RENDER_TO) * share, text


# -- the records -------------------------------------------------------------------------------------------------------

def walkthrough_for(records: list[dict], course: str, slug: str) -> dict | None:
    """The walkthrough imported from the workshop ``course``/``slug`` (its record's ``source.course`` and
    ``source.slug`` - of several workshops, the FIRST's), the most recently made when there are several; None for
    none. One walkthrough per workshop for the canvas: this is the one it opens."""
    hits = [
        r for r in records
        if walkthroughs.is_workshop_source(r.get("source"))
        and str(r["source"].get("course")) == course and str(r["source"].get("slug")) == slug
    ]
    return max(hits, key=lambda r: str(r.get("created_at") or "")) if hits else None


def project_for(projects: list[dict], wid: str) -> dict | None:
    """The project this flow reuses for the walkthrough ``wid``: the newest project of kind ``walkthrough`` made from
    it (``walkthrough_id``), whichever button made it - Make a video's or this one's; None for none."""
    hits = [p for p in projects if p.get("kind") == "walkthrough" and str(p.get("walkthrough_id") or "") == wid]
    return max(hits, key=lambda p: str(p.get("created_at") or "")) if hits else None


def vimeo_view(record: dict) -> dict | None:
    """What the dialog and the canvas say of the project's video on Vimeo: the id, the link, the privacy, when, and
    how many videos were published before; None without one."""
    vimeo = record.get("vimeo") or None
    if not vimeo or not vimeo.get("id"):
        return None
    return {
        "id": str(vimeo.get("id")), "link": course_video.video_link(vimeo), "privacy": vimeo.get("privacy") or "",
        "published_at": vimeo.get("published_at"), "previous": [str(x) for x in (vimeo.get("previous") or [])],
    }


def project_view(record: dict, place: dict | None) -> dict:
    """The project as the GET and the canvas answer it, with where its link stands in the guide (``place``,
    :func:`place_facts`, when the project has a video on Vimeo and a course)."""
    return {
        "id": record["id"], "name": record.get("name") or "", "created_at": record.get("created_at"),
        "rendered_at": record.get("rendered_at"), "has_video": bool(record.get("output_video")),
        "vimeo": vimeo_view(record),
        "placed": bool(place and place.get("placed")),
        "guide": place.get("guide") if place else None,
    }


# -- the place -----------------------------------------------------------------------------------------------------------

def place_facts(source: dict, name: str, link: str | None, caption: str = "") -> dict:
    """Where the link goes in the workshop's guide, from the plan computed now and nothing written (the dialog's
    read-only line): ``how`` (placeholder / figure / insert; the insertion is what the one button does when the
    guide has neither), the 1-based ``line``, the ``heading`` an insertion goes after, the ``guide``, the figure's
    ``existing_caption``, the ``caption`` in force, and ``placed`` - whether the guide already holds THIS link
    (false with no link). A course that cannot be read answers ``error`` with the reason and ``how`` None."""
    try:
        plan = course_video.compute_for_source(source, name, link or STAND_IN_LINK, caption=caption, welcome=False, insert=True)
    except (workshops.NoCoursesFolder, workshops.UnreadableCourse, course_export.ExportRefused, LookupError) as exc:
        return {"how": None, "line": 0, "heading": "", "guide": f"{source.get('course')}/{course_export.source_slugs(source)[0]}/{workshops.GUIDE_FILE}",
                "existing_caption": None, "caption": caption or name, "placed": False, "error": str(exc)}
    return {
        "how": plan.edit.how, "line": plan.edit.line, "heading": plan.edit.heading, "guide": plan.where(plan.guide),
        "existing_caption": plan.edit.existing_caption, "caption": plan.caption,
        "placed": bool(link) and course_video.is_placed(plan), "error": None,
    }


def place(record: dict, *, caption: str, welcome: bool, cancel_check=None) -> dict | None:
    """The place phase: the plan computed NOW (the dialog's was advisory - a guide changed since is no failure here,
    the author pressed the one button for exactly this), then ``course_video.write`` with the sha256 of each file as
    just read; the insertion after the first section heading when the guide has neither placeholder nor figure, so
    the link always has somewhere to go. A file changed between the two reads is read again, once. None when
    ``cancel_check`` says stop between the plan and the write: nothing is written."""
    for attempt in (1, 2):
        plan = course_video.compute(record, caption=caption, welcome=welcome, insert=True)
        view = course_video.plan_view(plan)
        if cancel_check and cancel_check():
            return None
        try:
            return course_video.write(record, caption=caption, welcome=welcome, insert=True, guide_sha256=view["guide_sha256"],
                                      manifest_sha256=view["manifest_sha256"], course_sha256=view["course_sha256"])
        except course_export.GuideChanged:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def done_line(link: str, *, course: bool, allowed: bool, guide: str | None, written: bool) -> str:
    """The Done line: placed, already there, not placed with the setting off, or published alone (no course)."""
    if not course:
        return DONE_PUBLISHED.format(link=link)
    if not allowed:
        return DONE_NOT_PLACED.format(link=link)
    if written:
        return DONE_PLACED.format(link=link, guide=guide)
    return DONE_ALREADY_PLACED.format(link=link, guide=guide)


# -- the canvas's workshop status (C2) -------------------------------------------------------------------------------

def pictured(record: dict) -> int:
    return sum(1 for s in record.get("steps") or [] if walkthroughs.has_picture(s))


def workshop_status(course: dict, course_id: str, mine: list[dict], projects: list[dict]) -> dict[str, dict]:
    """Per workshop slug of ``course``: the walkthrough the canvas would open (:func:`walkthrough_for`) with its step
    and picture counts, the project it has (:func:`project_for`) with its video and its place in the guide, computed
    from the records and the course's files - nothing stored, nothing written."""
    out: dict[str, dict] = {}
    for section in course.get("sections") or []:
        for w in section.get("workshops") or []:
            slug = w["slug"]
            rec = walkthrough_for(mine, course_id, slug)
            entry: dict = {"walkthrough": None, "project": None}
            if rec is not None:
                steps = rec.get("steps") or []
                entry["walkthrough"] = {
                    "id": rec["id"], "name": rec.get("name") or "", "step_count": len(steps), "pictured": pictured(rec),
                    "updated_at": rec.get("updated_at"), "created_at": rec.get("created_at"),
                }
                project = project_for(projects, rec["id"])
                if project is not None:
                    facts = None
                    vimeo = vimeo_view(project)
                    if vimeo:
                        facts = place_facts(rec["source"], rec.get("name") or "", vimeo["link"])
                    entry["project"] = project_view(project, facts)
            out[slug] = entry
    return out
