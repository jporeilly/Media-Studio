"""Screen recordings (T3): the recorder streams WebM chunks here while it
records; Finish turns them into a video project (``services.recordings``).

Making a recording - starting it, its chunks, its heartbeat - is the desktop
shell's on this computer (``screen_user``: a request from anywhere else is
refused and audited, like every route that touches the host's screen).
Saving or discarding one is anyone's who owns it, from the Projects page,
but not while it is LIVE (still being made: refused with 409) unless the
recorder itself asks (``from_recorder``, honoured only from this computer)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.audit import RECORDING_DISCARD, RECORDING_FINISH, RECORDING_START, audit
from api.deps import current_user, may_access_project
from api.routers.capture import is_local_client, screen_user
from api.routers.projects import ACTIVE_JOB_FIELDS
from api.store import display_name_of
from services import jobs, recordings

router = APIRouter(prefix="/recordings", tags=["recordings"])


class RegionIn(BaseModel):
    x: int
    y: int
    width: int = Field(ge=1, le=32_000)
    height: int = Field(ge=1, le=32_000)


class SourceIn(BaseModel):
    """What was recorded: a whole screen, a window, or a region of a screen
    (``region`` and ``monitor`` in physical virtual-screen pixels, as the
    shell's overlay reports them; the crop is their difference)."""

    kind: Literal["screen", "window", "region"] = "screen"
    region: RegionIn | None = None
    monitor: RegionIn | None = None
    window_title: str | None = Field(default=None, max_length=recordings.MAX_NAME_CHARS)


class StartIn(BaseModel):
    name: str = Field(default="", max_length=recordings.MAX_NAME_CHARS)
    mime: str = Field(default="video/webm", max_length=80)
    frame_rate: float = Field(default=recordings.DEFAULT_FRAME_RATE, ge=recordings.MIN_FRAME_RATE, le=recordings.MAX_FRAME_RATE)
    width: int = Field(default=0, ge=0, le=32_000)
    height: int = Field(default=0, ge=0, le=32_000)
    source: SourceIn = Field(default_factory=SourceIn)
    #: The recorder's chunk length; the server measures a recording by it.
    timeslice_ms: int = Field(default=recordings.DEFAULT_TIMESLICE_MS, ge=recordings.MIN_TIMESLICE_MS,
                              le=recordings.MAX_TIMESLICE_MS)


class FinishIn(BaseModel):
    """What the saver knows. The recorder, at Stop: how long it recorded and
    how many chunks it sent (every number below it must have arrived). The
    Projects page, re-saving: the chunks to keep (all, or the unbroken part
    before a gap with ``accept_loss``), and no duration (the server measures
    the chunks). ``from_recorder``: the recorder that is making it asks, so a
    LIVE recording is not refused."""

    duration_ms: int = Field(default=0, ge=0, le=(recordings.MAX_RECORDING_SECONDS + 600) * 1000)
    chunks: int | None = Field(default=None, ge=1, le=recordings.MAX_CHUNKS)
    from_recorder: bool = False
    accept_loss: bool = False


def _owned(rid: str, user: dict) -> dict:
    meta = recordings.read_meta(rid)
    if meta is None:
        raise HTTPException(status_code=404, detail="No such recording.")
    if not may_access_project(meta, user):
        raise HTTPException(status_code=403, detail="This recording is not yours.")
    return meta


def _recorder_asks(request: Request, from_recorder: bool) -> bool:
    """``from_recorder`` counts only from this computer: the recorder runs in
    the desktop shell, never anywhere else."""
    return bool(from_recorder) and is_local_client(request)


def _refusal(exc: Exception) -> HTTPException:
    """The status each of the service's refusals is answered with."""
    if isinstance(exc, recordings.NoRoom):
        return HTTPException(status_code=507, detail=str(exc))
    if isinstance(exc, recordings.TooLarge):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (recordings.RecordingError, jobs.KindBusy)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.post("")
def start(body: StartIn, user: dict = Depends(screen_user)):
    """Begin a recording: answers its id. 400 for a region that is not inside
    its screen; 507 with less than 2 GB free."""
    try:
        meta = recordings.start(
            user_id=user["id"], owner_name=display_name_of(user), name=body.name, mime=body.mime,
            frame_rate=body.frame_rate, width=body.width, height=body.height, source=body.source.model_dump(),
            timeslice_ms=body.timeslice_ms,
        )
    except (recordings.NoRoom, ValueError) as exc:
        raise _refusal(exc)
    audit(RECORDING_START, user=user, entity="recording", entity_id=meta["id"],
          detail=f"{body.source.kind}: {meta['name']}")
    return {"id": meta["id"], "name": meta["name"]}


async def _capped_body(request: Request, limit: int) -> bytes:
    """The request body, refused with 413 the moment it passes ``limit`` -
    read in pieces, so an oversized or lying upload is never held whole."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(status_code=413, detail=f"A chunk may not exceed {limit // 1024 ** 2} MB.")
    parts, size = [], 0
    async for piece in request.stream():
        size += len(piece)
        if size > limit:
            raise HTTPException(status_code=413, detail=f"A chunk may not exceed {limit // 1024 ** 2} MB.")
        parts.append(piece)
    return b"".join(parts)


@router.put("/{rid}/chunks/{seq}")
async def put_chunk(rid: str, seq: int, request: Request, user: dict = Depends(screen_user)):
    """One chunk of the WebM, raw in the body. A duplicate replaces the
    earlier copy; out of order is fine. Answers how many have arrived. 507
    under 1 GB free and 413 past the recording's cap - the recorder stops and
    keeps what arrived - and 409 while the recording is being saved."""
    _owned(rid, user)
    data = await _capped_body(request, recordings.MAX_CHUNK_BYTES)
    try:
        return recordings.put_chunk(rid, seq, data)
    except (LookupError, ValueError, recordings.RecordingError) as exc:
        raise _refusal(exc)


@router.post("/{rid}/heartbeat", status_code=204)
def heartbeat(rid: str, user: dict = Depends(screen_user)):
    """The recorder is still making this recording (paused, or between
    chunks): it stays LIVE, so the Projects page cannot save or discard it."""
    _owned(rid, user)
    try:
        recordings.heartbeat(rid)
    except LookupError as exc:
        raise _refusal(exc)


@router.post("/{rid}/release", status_code=204)
def release(rid: str, user: dict = Depends(screen_user)):
    """The recorder lets go of a recording it could not finish (it failed,
    the disk filled, the cap was reached): no longer live, it is offered on
    the Projects page at once, with what arrived."""
    _owned(rid, user)
    try:
        recordings.release(rid)
    except LookupError as exc:
        raise _refusal(exc)


@router.post("/{rid}/finish")
def finish(rid: str, body: FinishIn, request: Request, user: dict = Depends(current_user)):
    """Turn the chunks into a video project, as a job (``kind`` recording):
    answers ``{"job_id"}``; the job's result names the project. 409 while the
    recording is LIVE (unless its recorder asks) or being saved, while a chunk
    below ``chunks`` is missing (its numbers are in the detail; send them
    again), when chunks after a gap would be dropped without ``accept_loss``,
    or while another recording is being saved."""
    meta = _owned(rid, user)
    try:
        started = recordings.finish(
            rid, user=user, duration_ms=body.duration_ms, chunks=body.chunks,
            from_recorder=_recorder_asks(request, body.from_recorder), accept_loss=body.accept_loss,
        )
    except (LookupError, recordings.RecordingError, jobs.KindBusy) as exc:
        raise _refusal(exc)
    # The count the save takes - the caller's, or every chunk present when it sent none.
    audit(RECORDING_FINISH, user=user, entity="recording", entity_id=rid,
          detail=f"job {started['job_id']}, {meta['name']}, {body.duration_ms / 1000:.0f} s, {started['chunks']} chunks"
                 + (", the part before a gap" if body.accept_loss else ""))
    return {"job_id": started["job_id"]}


@router.delete("/{rid}", status_code=204)
def discard(rid: str, request: Request, from_recorder: bool = Query(default=False),
            user: dict = Depends(current_user)):
    """Throw a recording and its chunks away (the recorder's abort, or an
    unfinished one nobody wants). 409 while it is being saved, or while it is
    LIVE unless its recorder asks."""
    meta = _owned(rid, user)
    try:
        found = recordings.discard(rid, from_recorder=_recorder_asks(request, from_recorder))
    except recordings.RecordingError as exc:
        raise _refusal(exc)
    if not found:
        raise HTTPException(status_code=404, detail="No such recording.")
    audit(RECORDING_DISCARD, user=user, entity="recording", entity_id=rid, detail=meta.get("name"))


@router.get("")
def list_unfinished(user: dict = Depends(current_user)):
    """The caller's recordings still on disk - live, being saved, stopped but
    never saved, or whose save failed - so they can be saved or discarded.
    The page leaves out the one its own recorder is making."""
    return {"recordings": [r for r in recordings.list_unfinished() if may_access_project(r, user)]}


@router.get("/job")
def active_job(user: dict = Depends(current_user)):
    """The recording being saved right now, in the shape
    ``GET /api/projects/{pid}/job`` uses, so the Projects page can follow it
    after a reload - when it is the caller's (or the caller is an admin):
    someone else's save is not theirs to follow."""
    active = jobs.active_of_kind(recordings.JOB_KIND)
    if active and not may_access_project({"owner_id": active.get("user_id")}, user):
        active = None
    return {"active_job": {key: active.get(key) for key in ACTIVE_JOB_FIELDS} if active else None}
