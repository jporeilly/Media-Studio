"""The narration editor: per-sentence adjustments to a transcribed video.

One route so far - ``PATCH /api/projects/{pid}/transcript/{index}`` - which
nudges when a single sentence is spoken, mutes it, or gives it its own voice or
speed. Thin over ``services.narration`` (which owns the locking and the
validation): the route checks the project exists and is the caller's, that it is
a video, refuses a write while a job is attached to the project
(``services.jobs.require_idle``, a 409 - every slide write takes it and this one
must too, since a re-voice job reads the transcript it is adjusting), and maps
the service's errors to HTTP answers (``ProjectNotFound`` 404, ``ValueError``
400).

Its own router rather than another route on ``api.routers.projects``, following
``api.routers.slides``: the transcript editor is a feature area, and the
waveform and per-sentence preview routes of the later phases belong beside this
one rather than in the projects module.

The WHOLE-LIST ``PATCH /{pid}/transcript`` stays where it is
(``api.routers.projects``) and stays a text editor. One writer per concern.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import PROJECT_TRANSCRIPT_TIMING, audit
from api.deps import current_user, require_project
from api.schemas import SegmentOverride
from services import jobs, narration

router = APIRouter(prefix="/projects", tags=["narration"])


def writable(pid: str, user: dict) -> dict:
    """The record of a video project this user may adjust: 404 when missing,
    403 when it is someone else's, 400 for a deck or PDF, 409 while a job is
    attached to the project (``ProjectBusy``, answered by the app-wide handler)."""
    record = require_project(pid, user)
    if record.get("kind") not in narration.NARRATION_KINDS:
        raise HTTPException(status_code=400, detail="Only video projects have a transcript.")
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Field names only, never the sentence - the same rule the settings audits
    # follow (api/audit.py). This is the route that decides where a sentence
    # lands in the narration, so "who moved this" has to be answerable.
    audit(PROJECT_TRANSCRIPT_TIMING, user=user, entity="project", entity_id=pid,
          detail=f"segment {index}: {', '.join(sorted(changes)) or '(nothing)'}")
    return segment
