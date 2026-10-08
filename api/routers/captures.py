"""The Captures list (T3): stills of the screen - take one, list them, serve
and download the image, add one to a deck as a slide, delete one."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.audit import CAPTURE_ADD_TO_DECK, CAPTURE_DELETE, CAPTURE_STILL, audit
from api.deps import current_user, may_access_project, require_project
from api.routers.capture import screen_user
from api.store import display_name_of
from services import capture, captures, jobs, slides

router = APIRouter(prefix="/captures", tags=["captures"])

NO_STORE = {"Cache-Control": "no-store"}


class CaptureIn(BaseModel):
    """The rectangle to grab, physical virtual-screen pixels (negative offsets
    are legal), whether to draw the pointer, what it is and what to call it."""

    x: int
    y: int
    width: int = Field(ge=capture.MIN_DIMENSION, le=capture.MAX_DIMENSION)
    height: int = Field(ge=capture.MIN_DIMENSION, le=capture.MAX_DIMENSION)
    cursor: bool = False
    kind: Literal["region", "window", "screen"] = "region"
    name: str = Field(default="", max_length=captures.MAX_NAME_CHARS)


class AddToDeckIn(BaseModel):
    project_id: str = Field(min_length=12, max_length=12)


def _owned(cid: str, user: dict) -> dict:
    rec = captures.get_capture(cid)
    if rec is None:
        raise HTTPException(status_code=404, detail="No such capture.")
    if not may_access_project(rec, user):
        raise HTTPException(status_code=403, detail="This capture is not yours.")
    return rec


@router.get("")
def list_captures(user: dict = Depends(current_user)):
    """The caller's captures, newest first (an administrator's: everyone's)."""
    return {"captures": [c for c in captures.list_captures() if may_access_project(c, user)]}


@router.post("")
def take(body: CaptureIn, user: dict = Depends(screen_user)):
    """Grab the rectangle NOW into a new capture. Any delay is the page's:
    it waits, then asks. 400 for a bad rectangle, 503 when the grab fails."""
    try:
        rec = captures.take(
            x=body.x, y=body.y, width=body.width, height=body.height, cursor=body.cursor, kind=body.kind,
            name=body.name, owner_id=user["id"], owner_name=display_name_of(user),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except capture.CaptureError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    audit(CAPTURE_STILL, user=user, entity="capture", entity_id=rec["id"],
          detail=f"{body.kind} {body.width}x{body.height}{' with pointer' if body.cursor else ''}: {rec['name']}")
    return rec


@router.get("/{cid}/image")
def image(cid: str, user: dict = Depends(current_user)):
    """The still, inline."""
    _owned(cid, user)
    path = captures.image_path(cid)
    if path is None:
        raise HTTPException(status_code=404, detail="The capture's image is missing.")
    return FileResponse(str(path), media_type="image/png", headers=NO_STORE)


@router.get("/{cid}/download")
def download(cid: str, user: dict = Depends(current_user)):
    """The still as a download named after the capture."""
    rec = _owned(cid, user)
    path = captures.image_path(cid)
    if path is None:
        raise HTTPException(status_code=404, detail="The capture's image is missing.")
    from services.recordings import safe_stem

    return FileResponse(str(path), media_type="image/png", filename=f"{safe_stem(rec['name'], 'screen-capture')}.png",
                        content_disposition_type="attachment", headers=NO_STORE)


@router.post("/{cid}/add-to-deck")
def add_to_deck(cid: str, body: AddToDeckIn, user: dict = Depends(current_user)):
    """Make the still the deck project's new last slide (letterboxed to the
    deck's slide image size; the .pptx gains the picture slide). The deck
    must be the caller's and idle (409 while a job holds it); a PDF is
    refused with 400."""
    _owned(cid, user)
    project = require_project(body.project_id, user)
    if project.get("kind") != "deck":
        raise HTTPException(status_code=400, detail="Only a deck can take a new slide; a PDF's pages are fixed.")
    jobs.require_idle(body.project_id)
    path = captures.image_path(cid)
    if path is None:
        raise HTTPException(status_code=404, detail="The capture's image is missing.")
    try:
        result = slides.append_image_slide(body.project_id, path)
    except slides.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(CAPTURE_ADD_TO_DECK, user=user, entity="project", entity_id=body.project_id,
          detail=f"capture {cid} -> slide {result['index'] + 1}")
    return {"project_id": body.project_id, **result}


@router.delete("/{cid}", status_code=204)
def delete(cid: str, user: dict = Depends(current_user)):
    rec = _owned(cid, user)
    if not captures.delete_capture(cid):
        raise HTTPException(status_code=404, detail="No such capture.")
    audit(CAPTURE_DELETE, user=user, entity="capture", entity_id=cid, detail=rec.get("name"))
