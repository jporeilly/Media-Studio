"""The Captures list (T3): stills of the screen - take one, list them, serve
and download the image, add one to a deck as a slide, delete one - and the
annotations on a still (T3b): one record, the document and its export saved
in one multipart PUT, reverted with a DELETE, the original on request.

Which picture ``image``, ``download`` and ``add_to_deck`` serve is
``services.captures.image_path``'s to say (the annotated one once there is
one); nothing here chooses. The editor works anywhere a still is listed,
the browser edition included: it touches no screen, so these routes take
``current_user``, not ``screen_user``."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from api.audit import CAPTURE_ADD_TO_DECK, CAPTURE_ANNOTATE, CAPTURE_DELETE, CAPTURE_REVERT, CAPTURE_STILL, audit
from api.body import capped_body
from api.deps import current_user, may_access_project, require_project
from api.routers.capture import screen_user
from api.schemas import AnnotationDocument, plain_validation_error
from api.store import display_name_of
from services import capture, captures, jobs, slides

router = APIRouter(prefix="/captures", tags=["captures"])

NO_STORE = {"Cache-Control": "no-store"}
TOO_LARGE = f"The annotated picture may not exceed {captures.MAX_ANNOTATED_BYTES // 1024 ** 2} MB."


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
    """The caller's captures, newest first (an administrator's: everyone's),
    each with ``annotated`` and the output size, without the documents."""
    return {"captures": [captures.public_record(c) for c in captures.list_captures() if may_access_project(c, user)]}


@router.get("/{cid}")
def get_capture(cid: str, user: dict = Depends(current_user)):
    """One record, with its annotation document (null when not annotated):
    what the editor opens with."""
    return captures.public_record(_owned(cid, user), with_document=True)


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
    return captures.public_record(rec)


@router.get("/{cid}/image")
def image(cid: str, original: bool = Query(default=False), user: dict = Depends(current_user)):
    """The picture, inline: the annotated one once there is one, else the
    still; ``?original=1`` is always the still (the editor's base picture)."""
    _owned(cid, user)
    path = captures.image_path(cid, original=original)
    if path is None:
        raise HTTPException(status_code=404, detail="The capture's image is missing.")
    return FileResponse(str(path), media_type="image/png", headers=NO_STORE)


async def _multipart(request: Request, body: bytes):
    """The multipart form in ``body`` (already read and capped), parsed by
    Starlette from a request that replays those bytes."""
    from starlette.formparsers import MultiPartException

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    try:
        return await Request(request.scope, receive).form(max_part_size=captures.MAX_DOCUMENT_BYTES)
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=f"The upload is not a valid multipart form: {exc}") from None


@router.put("/{cid}/annotations")
async def put_annotations(cid: str, request: Request, user: dict = Depends(current_user)):
    """Save a still's annotations: a multipart body with ``document`` (the
    JSON document, a string field) and ``image`` (the PNG the page's renderer
    exported, at the crop's size or the still's). Both are kept, nothing is
    re-rendered. 400 with a plain line for a document that does not validate
    against this still or a picture that is not a PNG of the right size, and
    nothing is written then; 413 as the body passes the cap. Answers the
    record with its document."""
    rec = _owned(cid, user)
    body = await capped_body(request, captures.MAX_ANNOTATED_BYTES + captures.MAX_DOCUMENT_BYTES, TOO_LARGE)
    form = await _multipart(request, body)
    text, image = form.get("document"), form.get("image")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="The form needs a 'document' field holding the annotations as JSON.")
    if image is None or isinstance(image, str):
        raise HTTPException(status_code=400, detail="The form needs an 'image' file holding the annotated picture as a PNG.")
    try:
        document = AnnotationDocument.model_validate(
            json.loads(text), context={"width": rec["width"], "height": rec["height"]},
        )
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"The annotation document is not valid JSON: {exc.msg}.") from None
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"The annotation document is not valid: {plain_validation_error(exc)}") from None
    data = await image.read()
    try:
        # ``check_png`` holds the picture's own cap (``TooLarge`` -> 413), its magic bytes, the IHDR size
        # against the crop's or the still's, then Pillow's verify and a full decode; nothing is written when it refuses.
        result = captures.save_annotations(cid, document.model_dump(), data)
    except captures.TooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="No such capture.") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    crop = document.crop
    n = len(document.shapes)
    audit(CAPTURE_ANNOTATE, user=user, entity="capture", entity_id=cid,
          detail=f"{n} shape{'' if n == 1 else 's'}, {f'cropped {crop.width}x{crop.height}' if crop else 'not cropped'}")
    return result


@router.delete("/{cid}/annotations", status_code=204)
def revert_annotations(cid: str, user: dict = Depends(current_user)):
    """Revert to the original: the document and the export are deleted, the
    still was never touched. 204 whether or not there was anything to revert."""
    rec = _owned(cid, user)
    had = captures.revert_annotations(cid)
    if had is None:
        raise HTTPException(status_code=404, detail="No such capture.")
    if had:
        audit(CAPTURE_REVERT, user=user, entity="capture", entity_id=cid, detail=rec.get("name"))


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
    except OSError:
        # The consumer's own robustness: a picture on disk that Pillow cannot read (the save checks
        # every upload, but a file can still be damaged afterwards) is a plain 400, never a 500.
        raise HTTPException(status_code=400, detail="The annotated picture cannot be read: revert the annotations and save them again.")
    audit(CAPTURE_ADD_TO_DECK, user=user, entity="project", entity_id=body.project_id,
          detail=f"capture {cid} -> slide {result['index'] + 1}")
    return {"project_id": body.project_id, **result}


@router.delete("/{cid}", status_code=204)
def delete(cid: str, user: dict = Depends(current_user)):
    rec = _owned(cid, user)
    if not captures.delete_capture(cid):
        raise HTTPException(status_code=404, detail="No such capture.")
    audit(CAPTURE_DELETE, user=user, entity="capture", entity_id=cid, detail=rec.get("name"))
