"""The edit: which ranges of a video's one source are kept (porting vertical
6, phase E1 - the model and the render; the timeline gesture is E2).

Three routes, thin over ``services.edit``, which owns the validation, the
projection and the locking:

- ``GET /api/projects/{pid}/edit`` - the kept ranges (``null`` means
  everything), the source's length and the output's;
- ``PUT /api/projects/{pid}/edit`` - replace the whole list;
- ``DELETE /api/projects/{pid}/edit`` - back to keep-everything.

The GET is a read and is allowed while a job holds the project. The PUT and
the DELETE write the record, so they take ``jobs.require_idle`` (a 409) like
every other write of it: a re-voice job reads the edit it is rendering.

There is no render route of its own. The render IS the re-voice job with the
projection applied (``services.revoice``) - one job kind, one code path - so
``POST /api/projects/{pid}/revoice`` starts it, exactly as before.

Its own router, following ``api.routers.narration``: the edit is a feature
area of its own, and the projects module is a store of source files.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import PROJECT_EDIT, audit
from api.deps import current_user, readable_project, writable_project
from api.schemas import EditIn
from services import edit, narration

router = APIRouter(prefix="/projects", tags=["edit"])

NOT_A_VIDEO = "Only video projects can be cut."


def readable(pid: str, user: dict) -> dict:
    """A video project this user may see (``api.deps.readable_project``):
    404 missing, 403 someone else's, 400 a deck or PDF."""
    return readable_project(pid, user, narration.NARRATION_KINDS, NOT_A_VIDEO)


def writable(pid: str, user: dict) -> dict:
    """``readable`` plus 409 while a job holds the project."""
    return writable_project(pid, user, narration.NARRATION_KINDS, NOT_A_VIDEO)


def _summary(stored: dict) -> str:
    """The audit detail: how many ranges and how much was removed - never the
    times, which would say where every cut is."""
    count = len(stored["keep"])
    removed = max(0.0, (stored["source_duration"] or 0.0) - stored["output_duration"])
    return f"{count} range{'' if count == 1 else 's'} kept, {removed:.1f} s removed"


@router.get("/{pid}/edit")
def get_edit(pid: str, user: dict = Depends(current_user)):
    """The project's edit: ``keep`` is the list of kept ``[start, end]``
    ranges in source seconds, or ``null`` when the whole source is kept;
    ``source_duration`` is the extracted audio's length (``null`` until the
    video has been transcribed) and ``output_duration`` what a render would
    be (``null`` while neither is known).

    Answers: 200, 400 a deck/PDF or an edit this version cannot read, 404 no
    such project.
    """
    record = readable(pid, user)
    try:
        return edit.describe(record)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.put("/{pid}/edit")
def put_edit(pid: str, body: EditIn, user: dict = Depends(current_user)):
    """Replace the edit with ``keep``: ordered, non-overlapping ranges within
    the source, in source seconds. Returns the edit as stored.

    Answers: 200 the stored edit, 400 a bad list (the message names the
    range) or a deck/PDF, 404 no such project, 409 a job holds the project or
    the video has no extracted audio yet (the ranges are checked against its
    length, and transcribing is what extracts it).
    """
    writable(pid, user)
    try:
        stored = edit.set_edit(pid, body.keep)
    except edit.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except edit.SourceLengthUnknown as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(PROJECT_EDIT, user=user, entity="project", entity_id=pid, detail=_summary(stored))
    return stored


@router.delete("/{pid}/edit")
def delete_edit(pid: str, user: dict = Depends(current_user)):
    """Back to keep-everything. Returns the edit as it now stands (``keep``
    null). Idempotent, and a project that had no edit is left as it is and
    records nothing - there was nothing to clear. Answers: 200, 400 a
    deck/PDF, 404 no such project, 409 busy."""
    writable(pid, user)
    try:
        cleared, had_edit = edit.clear_edit(pid)
    except edit.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if had_edit:
        audit(PROJECT_EDIT, user=user, entity="project", entity_id=pid, detail="cleared")
    return cleared
