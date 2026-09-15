"""The narration editor: per-sentence adjustments to a transcribed video.

Two routes:

- ``PATCH /api/projects/{pid}/transcript/{index}`` nudges when a single
  sentence is spoken, mutes it, or gives it its own voice or speed;
- ``GET /api/projects/{pid}/transcript/{index}/preview`` speaks that one
  sentence back, so a voice or a speed can be heard before a whole re-voice is
  run for it.

Both are thin over ``services.narration`` (which owns the locking, the
validation and the synthesis): the routes check the project exists and is the
caller's and that it is a video, and map the service's errors to HTTP answers.
The PATCH additionally refuses a write while a job is attached to the project
(``services.jobs.require_idle``, a 409 - every slide write takes it and this one
must too, since a re-voice job reads the transcript it is adjusting). The
preview does NOT: it writes nothing to the project, and refusing to let someone
listen while a job runs would be a 409 on a read.

``SegmentNotFound`` is answered differently by the two on purpose, and both are
right: the PATCH is a write whose index argument is out of range (400, as it has
answered since phase 1), the preview is a GET of something that is not there
(404).

Its own router rather than another route on ``api.routers.projects``, following
``api.routers.slides``: the transcript editor is a feature area, and the
waveform route of phase 3 belongs beside these rather than in the projects
module.

The WHOLE-LIST ``PATCH /{pid}/transcript`` stays where it is
(``api.routers.projects``) and stays a text editor. One writer per concern.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.audit import PROJECT_TRANSCRIPT_TIMING, audit
from api.deps import current_user, require_project
from api.schemas import SegmentOverride
from services import jobs, narration

router = APIRouter(prefix="/projects", tags=["narration"])


def readable(pid: str, user: dict) -> dict:
    """The record of a video project this user may see: 404 when missing, 403
    when it is someone else's, 400 for a deck or PDF."""
    record = require_project(pid, user)
    if record.get("kind") not in narration.NARRATION_KINDS:
        raise HTTPException(status_code=400, detail="Only video projects have a transcript.")
    return record


def writable(pid: str, user: dict) -> dict:
    """``readable`` plus: 409 while a job is attached to the project
    (``ProjectBusy``, answered by the app-wide handler)."""
    record = readable(pid, user)
    jobs.require_idle(pid)
    return record


@router.patch("/{pid}/transcript/{index}")
def update_transcript_segment(
    pid: str, index: int, body: SegmentOverride, user: dict = Depends(current_user),
):
    """Adjust one transcript sentence: its offset, whether it is muted, and its
    own voice / speed. A field left out is left alone; an explicit null clears
    it. Returns the sentence as it now stands."""
    writable(pid, user)
    # Only what the request carried: a field left out stays as it is, an
    # explicit null clears the override.
    changes = body.model_dump(exclude_unset=True)
    provider = changes.pop("provider", None)
    try:
        segment = narration.update_segment(pid, index, provider=provider, **changes)
    except narration.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (narration.SegmentNotFound, ValueError) as exc:
        # An index past the end is a bad argument to a write, not a missing
        # page: 400 with the range named, as phase 1 shipped it.
        raise HTTPException(status_code=400, detail=str(exc))
    # Field names only, never the sentence - the same rule the settings audits
    # follow (api/audit.py). This is the route that decides where a sentence
    # lands in the narration, so "who moved this" has to be answerable.
    audit(PROJECT_TRANSCRIPT_TIMING, user=user, entity="project", entity_id=pid,
          detail=f"segment {index}: {', '.join(sorted(changes)) or '(nothing)'}")
    return segment


@router.get("/{pid}/transcript/{index}/preview")
def preview_transcript_segment(
    pid: str, index: int,
    provider: str | None = Query(None, description="edge_tts | kokoro; omitted = the studio's"),
    voice: str | None = Query(None, max_length=narration.MAX_VOICE_CHARS,
                              description="the job's voice; the sentence's own still wins"),
    speed: float | None = Query(None, ge=narration.MIN_SPEED, le=narration.MAX_SPEED,
                                description="the job's speed; the sentence's own still wins"),
    user: dict = Depends(current_user),
):
    """Hear one transcript sentence, spoken as the render would speak it.

    The words are the STORED ones - the transcript editor has an explicit Save,
    so you can only hear what is saved, which is what lets this be a GET (the
    browser caches it, and it stays out of the audit guard honestly: nothing is
    written). The three query parameters are the JOB's narration - what the
    Re-voice card has selected - and the sentence's own voice and speed still
    win over them, exactly as they do at render time. With none of them, the
    sentence's own overrides are used, then the studio defaults.

    It is deliberately NOT a job: ``services.jobs`` allows one job per project,
    so a preview-as-job would block the re-voice this is auditioning for. The
    first press costs a provider round trip (Edge: about a second warm, ~10 s
    for the first one after the server starts); the second is a file copy,
    because the synthesis cache is keyed on (text, voice, speed) and the render
    reuses the very same entry.

    Answers: 200 the audio, 400 a deck/PDF or an unknown provider or a voice
    from the other provider, 404 no such project / no transcript / no such
    sentence, 409 Kokoro's model is not downloaded yet, 502 the provider
    returned nothing (or nothing in time).
    """
    readable(pid, user)
    try:
        path = narration.preview_segment(pid, index, provider=provider, voice=voice, speed=speed)
    except (narration.ProjectNotFound, narration.SegmentNotFound) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except narration.KokoroModelMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except narration.PreviewUnavailable as exc:
        # Never a 500: the provider failing is an upstream failure, and the
        # message says which provider and what to try.
        raise HTTPException(status_code=502, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Both providers write mp3 (``utils.helpers.get_cache_path``'s default
    # extension); served inline so a plain <audio src="…"> plays it.
    return FileResponse(str(path), media_type="audio/mpeg")
