"""The slide editor: per-slide notes and overrides of a deck or PDF project,
their rendered images, and the deck exported with the edited notes.

Thin over ``services.slides`` (which owns the locking and the validation):
the routes check the project exists and is a deck or PDF, refuse a write
while a job is attached to the project (the job holds its own copy of the
state and would overwrite the edit; ``services.jobs.require_idle``, a 409),
map the service's errors to HTTP answers (``ProjectNotFound`` 404,
``ProjectStateError`` 409, ``ValueError`` 400), and serve files by index -
never by a stored path. ``guard`` / ``writable`` / ``call`` are shared with
the AI assistant's routes (``api.routers.ai``).

``guard`` fetches the project through ``api.deps.require_project``, so a
project that is not the caller's is a 403 here exactly as it is on the project
routes - one helper, not a check per route.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.audit import SLIDES_BULK_UPDATE, SLIDES_RENDER, SLIDES_RESET, SLIDES_UNDO, SLIDES_UPDATE, audit
from api.deps import current_user, require_project
from api.schemas import RenderRequest, SlidesBulkUpdate, SlideUpdate
from core.project_manager import ProjectStateError
from services import jobs, slides

router = APIRouter(prefix="/projects", tags=["slides"])

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
RENDER_JOB_KIND = "render-slides"


def guard(pid: str, user: dict) -> dict:
    """The record of a deck or PDF project this user may touch: 404 when
    missing, 403 when it is someone else's, 400 for a video."""
    record = require_project(pid, user)
    if record.get("kind") not in slides.SLIDE_KINDS:
        raise HTTPException(status_code=400, detail="Only deck and PDF projects have slides.")
    return record


def writable(pid: str, user: dict) -> dict:
    """``guard`` plus: 409 while a job is attached to the project (``ProjectBusy``,
    answered by the app-wide handler)."""
    record = guard(pid, user)
    jobs.require_idle(pid)
    return record


def call(func):
    """Run a service call, turning its errors into the HTTP answers."""
    try:
        return func()
    except slides.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ProjectStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{pid}/slides")
def list_slides(pid: str, user: dict = Depends(current_user)):
    """Every slide with its notes, the deck's own notes / title / body text and
    its render state; materialises the engine project on first use."""
    guard(pid, user)
    return call(lambda: slides.slides_payload(pid))


@router.patch("/{pid}/slides")
def bulk_update(pid: str, body: SlidesBulkUpdate, user: dict = Depends(current_user)):
    """Save the notes of several slides at once (a bad index refuses the whole batch)."""
    writable(pid, user)
    items = [item.model_dump() for item in body.slides]
    saved = call(lambda: slides.bulk_update(pid, items))
    audit(SLIDES_BULK_UPDATE, user=user, entity="project", entity_id=pid, detail=f"{len(items)} slides")
    return {"slides": saved}


@router.post("/{pid}/slides/render")
def render_slides(pid: str, body: RenderRequest | None = None, user: dict = Depends(current_user)):
    """Render the slide images (a job of kind ``render-slides``, polled at
    /api/jobs/{id}); ``{"cached": true}`` when every image already exists,
    unless ``force`` asks for a fresh export (previews rendered before their
    source was recorded, which vision cannot use until it is). A render
    already in flight for the project is returned rather than started again;
    any other job attached to the project is a 409."""
    guard(pid, user)
    force = bool(body and body.force)
    active = jobs.active_for(pid)
    if active and active["kind"] == RENDER_JOB_KIND:
        return {"job_id": active["id"]}
    jobs.require_idle(pid)
    if not force and call(lambda: slides.images_ready(pid)):
        return {"cached": True}
    job_id = jobs.start(
        RENDER_JOB_KIND, lambda progress: slides.ensure_images(pid, progress, force=force),
        project_id=pid, user_id=user["id"], reuse=True,
    )
    audit(SLIDES_RENDER, user=user, entity="project", entity_id=pid,
          detail=f"job {job_id}" + (", forced" if force else ""))
    return {"job_id": job_id}


@router.patch("/{pid}/slides/{index}")
def update_slide(pid: str, index: int, body: SlideUpdate, user: dict = Depends(current_user)):
    """Change one slide's notes / voice override / pause / alt text. Returns the slide."""
    writable(pid, user)
    # Only what the request carried: a field left out stays as it is, an
    # explicit null clears the override.
    changes = body.model_dump(exclude_unset=True)
    provider = changes.pop("provider", None)
    slide = call(lambda: slides.update_slide(pid, index, provider=provider, **changes))
    # Audited even though the editor calls this often: this is the route that
    # changes what the narration SAYS, and the field names alone answer "who
    # changed this deck's script". It is a deliberate Save (or a dropdown /
    # blur), never a keystroke, so the volume is one row per edit the user
    # meant to make. See AUDIT_EXEMPT in tests/test_audit.py.
    audit(SLIDES_UPDATE, user=user, entity="project", entity_id=pid,
          detail=f"slide {index}: {', '.join(sorted(changes)) or '(nothing)'}")
    return slide


@router.post("/{pid}/slides/{index}/undo")
def undo_slide(pid: str, index: int, user: dict = Depends(current_user)):
    """Restore the notes before the last edit; 409 when there is nothing to undo."""
    writable(pid, user)
    slide = call(lambda: slides.undo_slide(pid, index))
    if slide is None:
        raise HTTPException(status_code=409, detail="Nothing to undo on this slide.")
    audit(SLIDES_UNDO, user=user, entity="project", entity_id=pid, detail=f"slide {index}")
    return slide


@router.post("/{pid}/slides/{index}/reset")
def reset_slide(pid: str, index: int, user: dict = Depends(current_user)):
    """Back to the deck's own notes (the current text stays in the undo history)."""
    writable(pid, user)
    slide = call(lambda: slides.reset_slide(pid, index))
    audit(SLIDES_RESET, user=user, entity="project", entity_id=pid, detail=f"slide {index}")
    return slide


@router.get("/{pid}/slides/{index}/image")
def slide_image(pid: str, index: int, user: dict = Depends(current_user)):
    """The rendered PNG of one slide, by index; 404 until it is rendered."""
    guard(pid, user)
    path = call(lambda: slides.image_path(pid, index))
    if path is None:
        raise HTTPException(status_code=404, detail="This slide has not been rendered yet.")
    # The file name never changes between renders, so the browser must ask again.
    return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.get("/{pid}/export/pptx")
def export_pptx(pid: str, user: dict = Depends(current_user)):
    """Download the deck with the edited notes written into every slide."""
    guard(pid, user)
    path = call(lambda: slides.export_notes_pptx(pid))
    return FileResponse(str(path), media_type=PPTX_MEDIA_TYPE, filename=path.name, content_disposition_type="attachment")
