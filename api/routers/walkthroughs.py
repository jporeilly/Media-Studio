"""Walkthroughs (W1): the list, one record, the builder's whole-document save,
a step from a still, a step's picture replaced, the player's pictures, a copy
and a delete. W2 adds the import of a course workshop (``POST /import``: a
new walkthrough built from the guide's parse, its pictures copied from the
course) and the guided capture's audit row (``POST /{wid}/guided-capture``).

Every route takes ``current_user`` and answers the owner or an administrator,
as a project's do (``api.deps.may_access_project`` on the record). Nothing
here touches the screen, so the browser edition has all of it: only taking a
still needs the desktop app. A step's picture is copied from the capture by
``services.walkthroughs`` through the one oracle (``services.captures
.image_path``); the capture must be the caller's, which the Captures router's
own ``_owned`` decides (404 / 403 as there).
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from api.audit import (
    WALKTHROUGH_ADD_STEP, WALKTHROUGH_CREATE, WALKTHROUGH_DELETE, WALKTHROUGH_DUPLICATE, WALKTHROUGH_GUIDED_CAPTURE,
    WALKTHROUGH_IMPORT, WALKTHROUGH_REPLACE_PICTURE, WALKTHROUGH_UPDATE, audit,
)
from api.body import capped_body
from api.deps import current_user, may_access_project
from api.routers.captures import _owned as owned_capture
from api.schemas import HotspotIn, WalkthroughIn, WalkthroughName, plain_validation_error
from api.store import display_name_of
from services import walkthroughs, workshops
from services.recordings import safe_stem
from utils.helpers import is_hex12

router = APIRouter(prefix="/walkthroughs", tags=["walkthroughs"])

NO_STORE = {"Cache-Control": "no-store"}
TOO_LARGE = f"A walkthrough's document may not exceed {walkthroughs.MAX_PUT_BYTES // 1024 ** 2} MB."
NOT_YOURS = "This walkthrough is not yours."


class NewWalkthroughIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: WalkthroughName


class AddStepIn(BaseModel):
    """The still whose picture becomes the step, and the step to insert after (else appended)."""

    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(min_length=12, max_length=12)
    after: str | None = Field(None, min_length=12, max_length=12)


class ReplacePictureIn(BaseModel):
    """The still whose picture the step takes; ``hotspot`` left out keeps the step's own (clamped into
    the new picture), a rectangle sets it (clamped), null removes it - the guided capture's pointer rule."""

    model_config = ConfigDict(extra="forbid")

    capture_id: str = Field(min_length=12, max_length=12)
    hotspot: HotspotIn | None = None


class ImportIn(BaseModel):
    """A course workshop to import (W2): the course's folder name and the workshop's slug, as
    ``GET /api/courses`` lists them, and the walkthrough's name (the workshop's title when left out)."""

    model_config = ConfigDict(extra="forbid")

    course: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100)
    name: WalkthroughName | None = None


class GuidedCaptureIn(BaseModel):
    """The guided capture's two events (W2): its start (the kind, with or without the pointer) and its
    end (how many steps were captured and skipped). The stills themselves are ``capture.still`` rows and
    each picture attached a ``walkthrough.replace_picture`` row; this is the row that frames them."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["start", "done"]
    kind: Literal["region", "window", "screen"] | None = None
    cursor: bool = False
    captured: int = Field(0, ge=0, le=10_000)
    skipped: int = Field(0, ge=0, le=10_000)


def _owned(wid: str, user: dict) -> dict:
    rec = walkthroughs.get_walkthrough(wid)
    if rec is None:
        raise HTTPException(status_code=404, detail="No such walkthrough.")
    if not may_access_project(rec, user):
        raise HTTPException(status_code=403, detail=NOT_YOURS)
    return rec


def _counts(rec: dict) -> str:
    """The audit detail of a save: how many steps and chapters, never a step's text."""
    steps = rec.get("steps") or []
    n, c = len(steps), walkthroughs.chapter_count(steps)
    return f"{n} step{'' if n == 1 else 's'}, {c} chapter{'' if c == 1 else 's'}"


@router.get("")
def list_walkthroughs(user: dict = Depends(current_user)):
    """The caller's walkthroughs, newest first (an administrator's: everyone's):
    the name, the counts and the first step's id for a thumbnail - no steps."""
    return {"walkthroughs": [walkthroughs.summary(r) for r in walkthroughs.list_walkthroughs() if may_access_project(r, user)]}


@router.post("")
def create(body: NewWalkthroughIn, user: dict = Depends(current_user)):
    """A new, empty walkthrough; the record."""
    rec = walkthroughs.create(body.name, owner_id=user["id"], owner_name=display_name_of(user))
    audit(WALKTHROUGH_CREATE, user=user, entity="walkthrough", entity_id=rec["id"], detail=rec["name"])
    return rec


@router.post("/import")
def import_workshop(body: ImportIn, user: dict = Depends(current_user)):
    """A new walkthrough built from a course workshop's guide (W2): the chapters and steps of the parse
    (``services.workshops``), each step's instruction, text and note, the guide's pictures copied in
    through the one copy routine, the course kept as the record's ``source``. Owned by the caller. 400 when
    the courses folder is not set, the course or workshop was left out of the list (a file over 2 MB, not
    UTF-8 or not JSON), the workshop has more steps than a walkthrough holds, or the planned record would not
    pass the save's own validation; 404 for a course or workshop that is not there; a workshop with no steps
    makes an empty walkthrough. Answers the record."""
    try:
        root = workshops.courses_root()
        loaded, workshop, text, folder = workshops.load_workshop(root, body.course, body.slug)
    except workshops.NoCoursesFolder as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except workshops.UnreadableCourse as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    parsed = workshops.parse_guide(text, loaded["launchers"])
    source, steps = workshops.import_plan(loaded, workshop, parsed, folder)
    name = body.name or (workshop.get("title") or parsed.title or workshop["slug"])
    try:
        rec = walkthroughs.create_from_workshop(name, owner_id=user["id"], owner_name=display_name_of(user), source=source, steps=steps)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    pictures = sum(1 for s in rec["steps"] if walkthroughs.has_picture(s))
    audit(WALKTHROUGH_IMPORT, user=user, entity="walkthrough", entity_id=rec["id"],
          detail=f"{body.course}/{body.slug}: {_counts(rec)}, {pictures} picture{'' if pictures == 1 else 's'}")
    return rec


@router.get("/{wid}")
def get_walkthrough(wid: str, user: dict = Depends(current_user)):
    """The record: what the builder and the player open with."""
    return _owned(wid, user)


@router.put("/{wid}")
async def put_walkthrough(wid: str, request: Request, user: dict = Depends(current_user)):
    """Save the whole walkthrough: the name and the steps, with the revision
    the builder loaded. 400 with a plain line for a document that does not
    validate against the pictures the walkthrough holds, 409 when the
    revision is not the record's (somebody saved in between), 413 as the body
    passes its cap; nothing is written in any of those. The pictures no step
    names any more are removed after the record is written."""
    _owned(wid, user)
    body = await capped_body(request, walkthroughs.MAX_PUT_BYTES, TOO_LARGE)
    try:
        data = json.loads(body)
    except ValueError as exc:
        # JSONDecodeError and UnicodeDecodeError are ValueErrors; so is Python's refusal of an integer of more
        # than 4,300 digits - every way the bytes fail to be a document gets the route's one line, never
        # Python's own text.
        reason = exc.msg if isinstance(exc, json.JSONDecodeError) else "it could not be read as a document"
        raise HTTPException(status_code=400, detail=f"The walkthrough is not valid JSON: {reason}.") from None
    try:
        document = WalkthroughIn.model_validate(data, context={"pictures": walkthroughs.held_pictures(wid)})
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"The walkthrough is not valid: {plain_validation_error(exc)}") from None
    try:
        rec = walkthroughs.save(wid, document.model_dump(), document.revision)
    except walkthroughs.StaleRevision as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except LookupError:
        raise HTTPException(status_code=404, detail="No such walkthrough.") from None
    audit(WALKTHROUGH_UPDATE, user=user, entity="walkthrough", entity_id=wid, detail=_counts(rec))
    return rec


@router.delete("/{wid}", status_code=204)
def delete(wid: str, user: dict = Depends(current_user)):
    rec = _owned(wid, user)
    if not walkthroughs.delete_walkthrough(wid):
        raise HTTPException(status_code=404, detail="No such walkthrough.")
    audit(WALKTHROUGH_DELETE, user=user, entity="walkthrough", entity_id=wid, detail=rec.get("name"))


@router.post("/{wid}/steps")
def add_step(wid: str, body: AddStepIn, user: dict = Depends(current_user)):
    """A new step from the capture's CURRENT picture (the annotated one when
    there is one), copied into the walkthrough; inserted after ``after`` or
    appended. The capture must be the caller's (404 / 403 as the Captures
    routes answer). Answers the record."""
    _owned(wid, user)
    owned_capture(body.capture_id, user)
    try:
        rec, position = walkthroughs.add_step(wid, body.capture_id, body.after)
    except walkthroughs.NoSuchStep as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    audit(WALKTHROUGH_ADD_STEP, user=user, entity="walkthrough", entity_id=wid,
          detail=f"capture {body.capture_id} -> step {position + 1} of {len(rec['steps'])}")
    return rec


@router.post("/{wid}/steps/{sid}/picture")
def replace_picture(wid: str, sid: str, body: ReplacePictureIn, user: dict = Depends(current_user)):
    """Replace the step's picture with the capture's current one - the first
    picture of a step that had none; the hotspot is kept, clamped to the new
    size, unless the body names one (set, clamped) or nulls it (removed).
    Answers the record."""
    _owned(wid, user)
    owned_capture(body.capture_id, user)
    if not is_hex12(sid):
        raise HTTPException(status_code=404, detail="No such step.")
    hotspot = walkthroughs.KEEP_HOTSPOT if "hotspot" not in body.model_fields_set else (body.hotspot.model_dump() if body.hotspot else None)
    try:
        rec = walkthroughs.replace_picture(wid, sid, body.capture_id, hotspot=hotspot)
    except walkthroughs.NoSuchStep as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    spot = "" if hotspot is walkthroughs.KEEP_HOTSPOT else (", no hotspot" if hotspot is None else ", hotspot set")
    audit(WALKTHROUGH_REPLACE_PICTURE, user=user, entity="walkthrough", entity_id=wid, detail=f"step {sid}: capture {body.capture_id}{spot}")
    return rec


@router.post("/{wid}/guided-capture", status_code=204)
def guided_capture(wid: str, body: GuidedCaptureIn, user: dict = Depends(current_user)):
    """The guided capture's audit row (W2): "start" with the kind (and whether the pointer is drawn),
    "done" with how many steps were captured and skipped. The desktop app sends both; nothing is
    written to the walkthrough here (each picture attached is a replace_picture of its own)."""
    _owned(wid, user)
    if body.event == "start":
        detail = f"start: {body.kind or 'unknown'}{' with pointer' if body.cursor else ''}"
    else:
        detail = f"done: {body.captured} captured, {body.skipped} skipped"
    audit(WALKTHROUGH_GUIDED_CAPTURE, user=user, entity="walkthrough", entity_id=wid, detail=detail)


@router.get("/{wid}/steps/{sid}/image")
def step_image(wid: str, sid: str, user: dict = Depends(current_user)):
    """The step's picture, inline, never cached (the builder busts with the revision)."""
    _owned(wid, user)
    path = walkthroughs.step_image_path(wid, sid)
    if path is None:
        raise HTTPException(status_code=404, detail="The step's picture is missing.")
    return FileResponse(str(path), media_type="image/png", headers=NO_STORE)


@router.get("/{wid}/transcript")
def transcript(wid: str, download: bool = Query(default=False), user: dict = Depends(current_user)):
    """The walkthrough as a transcript - the Markdown the builder's panel and the player's drawer show
    (``services.walkthroughs.transcript_markdown``, byte for byte what the page writes), as
    ``text/markdown``; ``?download=1`` makes it a download named after the walkthrough. A read: no audit row."""
    rec = _owned(wid, user)
    headers = dict(NO_STORE)
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{safe_stem(rec.get("name") or "", "walkthrough")}.md"'
    return Response(content=walkthroughs.transcript_markdown(rec), media_type="text/markdown; charset=utf-8", headers=headers)


@router.post("/{wid}/duplicate")
def duplicate(wid: str, user: dict = Depends(current_user)):
    """A copy named "<name> (copy)", pictures copied, owned by the caller."""
    rec = _owned(wid, user)
    copy = walkthroughs.duplicate(wid, owner_id=user["id"], owner_name=display_name_of(user))
    if copy is None:
        raise HTTPException(status_code=404, detail="No such walkthrough.")
    audit(WALKTHROUGH_DUPLICATE, user=user, entity="walkthrough", entity_id=copy["id"], detail=f"{wid} -> {copy['id']}: {rec.get('name')}")
    return copy
