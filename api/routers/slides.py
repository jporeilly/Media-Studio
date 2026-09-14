"""The slide editor: per-slide notes and overrides of a deck or PDF project,
their rendered images, and the deck exported with the edited notes.

Thin over ``services.slides`` (which owns the locking and the validation):
the routes check the project exists and is a deck or PDF, refuse a write
while a job is attached to the project (the job holds its own copy of the
state and would overwrite the edit), map the service's errors to HTTP
answers (``ProjectNotFound`` 404, ``ProjectStateError`` 409, ``ValueError``
400), and serve files by index - never by a stored path.
"""

import threading

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from api.deps import current_user
from api.schemas import SlidesBulkUpdate, SlideUpdate
from core.project_manager import ProjectStateError
from services import jobs, projects as store, slides

router = APIRouter(prefix="/projects", tags=["slides"])

PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
RENDER_JOB_KIND = "render-slides"

# Check-and-submit of a render job is one step, so two clicks cannot start
# two exports of the same deck.
_render_lock = threading.Lock()


def _guard(pid: str) -> dict:
    """The record of a deck or PDF project: 404 when missing, 400 for a video."""
    record = store.get_project(pid)
    if not record:
        raise HTTPException(status_code=404, detail="Project not found.")
    if record.get("kind") not in slides.SLIDE_KINDS:
        raise HTTPException(status_code=400, detail="Only deck and PDF projects have slides.")
    return record


def _busy(pid: str) -> dict | None:
    """The job queued or running for the project, if any."""
    return jobs.active_for(pid)


def _writable(pid: str) -> dict:
    """``_guard`` plus: 409 while a job is attached to the project."""
    record = _guard(pid)
    active = _busy(pid)
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"A job is running for this project ({active['kind']}). Wait for it to finish, then try again.",
        )
    return record


def _call(func):
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
    _guard(pid)
    return _call(lambda: slides.slides_payload(pid))


@router.patch("/{pid}/slides")
def bulk_update(pid: str, body: SlidesBulkUpdate, user: dict = Depends(current_user)):
    """Save the notes of several slides at once (a bad index refuses the whole batch)."""
    _writable(pid)
    items = [item.model_dump() for item in body.slides]
    return {"slides": _call(lambda: slides.bulk_update(pid, items))}


@router.post("/{pid}/slides/render")
def render_slides(pid: str, user: dict = Depends(current_user)):
    """Render the slide images (a job of kind ``render-slides``, polled at
    /api/jobs/{id}); ``{"cached": true}`` when every image already exists. A
    render already in flight for the project is returned rather than started
    again; any other job attached to the project is a 409."""
    _guard(pid)
    with _render_lock:
        active = _busy(pid)
        if active and active["kind"] == RENDER_JOB_KIND:
            return {"job_id": active["id"]}
        _writable(pid)
        if _call(lambda: slides.images_ready(pid)):
            return {"cached": True}
        job_id = jobs.submit(RENDER_JOB_KIND, lambda progress: slides.ensure_images(pid, progress), project_id=pid)
    return {"job_id": job_id}


@router.patch("/{pid}/slides/{index}")
def update_slide(pid: str, index: int, body: SlideUpdate, user: dict = Depends(current_user)):
    """Change one slide's notes / voice override / pause / alt text. Returns the slide."""
    _writable(pid)
    # Only what the request carried: a field left out stays as it is, an
    # explicit null clears the override.
    changes = body.model_dump(exclude_unset=True)
    provider = changes.pop("provider", None)
    return _call(lambda: slides.update_slide(pid, index, provider=provider, **changes))


@router.post("/{pid}/slides/{index}/undo")
def undo_slide(pid: str, index: int, user: dict = Depends(current_user)):
    """Restore the notes before the last edit; 409 when there is nothing to undo."""
    _writable(pid)
    slide = _call(lambda: slides.undo_slide(pid, index))
    if slide is None:
        raise HTTPException(status_code=409, detail="Nothing to undo on this slide.")
    return slide


@router.post("/{pid}/slides/{index}/reset")
def reset_slide(pid: str, index: int, user: dict = Depends(current_user)):
    """Back to the deck's own notes (the current text stays in the undo history)."""
    _writable(pid)
    return _call(lambda: slides.reset_slide(pid, index))


@router.get("/{pid}/slides/{index}/image")
def slide_image(pid: str, index: int, user: dict = Depends(current_user)):
    """The rendered PNG of one slide, by index; 404 until it is rendered."""
    _guard(pid)
    path = _call(lambda: slides.image_path(pid, index))
    if path is None:
        raise HTTPException(status_code=404, detail="This slide has not been rendered yet.")
    # The file name never changes between renders, so the browser must ask again.
    return FileResponse(str(path), media_type="image/png", headers={"Cache-Control": "no-cache"})


@router.get("/{pid}/export/pptx")
def export_pptx(pid: str, user: dict = Depends(current_user)):
    """Download the deck with the edited notes written into every slide."""
    _guard(pid)
    path = _call(lambda: slides.export_notes_pptx(pid))
    return FileResponse(str(path), media_type=PPTX_MEDIA_TYPE, filename=path.name, content_disposition_type="attachment")
