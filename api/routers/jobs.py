"""Background job status, polled by the client until done / error."""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import JOB_CANCEL, audit
from api.deps import current_user, may_access_project
from services import jobs as job_store
from services import projects as project_store

router = APIRouter(prefix="/jobs", tags=["jobs"])

# What a poll of an id the server no longer holds answers. Jobs live in
# memory, so after a restart every id a page was polling ends here; the page
# reads the 404 as "the job was lost" (frontend/src/lib/jobs.ts).
JOB_NOT_FOUND = "Job not found."
# Who may read a job (``_may_read``) and who may cancel it (``_may_cancel``).
READ_FORBIDDEN = "Only the user who started this job, the owner of its project, or an admin can see it."
CANCEL_FORBIDDEN = "Only the user who started this job, or an admin, can cancel it."


def _may_cancel(job: dict, user: dict) -> bool:
    """The cancel rule: the user who started the job, or an admin. A job that
    records no starter - an update - is an admin's to cancel: its id is
    discoverable by every signed-in user (``GET /api/system/update/job``), and
    a cancel from anyone else would write a ``job.cancel`` audit row for a
    request that stops nothing."""
    if user.get("role") == "admin":
        return True
    return bool(job.get("user_id")) and job["user_id"] == user["id"]


def _may_read(job: dict, user: dict) -> bool:
    """The read rule: the user who started the job or an admin; a job that
    records no starter (an update) is anyone's to read, so every signed-in user
    can follow the update on the Settings page; and anyone who may act on the
    project the job works on (``api.deps.may_access_project``: its owner or an
    admin).

    The project page follows the project's running job whoever started it
    (``GET /api/projects/{pid}/job``), so the owner of a project must be able
    to watch the job an administrator started on it - that is their own work,
    not somebody else's. Nobody else gains anything: another editor still may
    not read a job on a project that is not theirs, which was the cross-tenant
    leak this guard was written for."""
    if not job.get("user_id") or _may_cancel(job, user):
        return True
    pid = job.get("project_id")
    record = project_store.get_project(pid) if pid else None
    return bool(record) and may_access_project(record, user)


@router.get("/{job_id}")
def get_job(job_id: str, user: dict = Depends(current_user)):
    """A job's state: for the user who started it, an admin, or anyone who may
    act on the job's project (its owner).

    A job carries its kind, its project id, its progress messages and, when it
    finishes, the names of the files it produced. Once projects have owners that
    is somebody else's work, so reading one was the last cross-tenant leak: any
    signed-in editor holding an id could watch it. A job that carries no
    ``user_id`` - the update - is readable by anyone, so every signed-in user
    can follow it on the Settings page. An id the server does
    not hold - a restart forgets every job - is a 404 with ``JOB_NOT_FOUND``.
    """
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND)
    if not _may_read(job, user):
        raise HTTPException(status_code=403, detail=READ_FORBIDDEN)
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user: dict = Depends(current_user)):
    """Ask a job to stop. Cooperative: it raises the job's flag and the job's
    own work decides when to look at it. The ``ai-*`` jobs check it between
    slides (the QA review between passes) and a re-voice between its stages
    (the translation, the picture cut, each sentence it synthesises, the
    music mix); each then finishes as ``done`` with ``result.cancelled`` true,
    the per-slide AI jobs keeping what they had written and a re-voice leaving
    the files and the record as its closing message says. The Q&A document,
    a transcription, a render, the slide previews and an update never look
    and run to their end. Returns the job's state (a finished job is returned
    as it is); keep polling GET. Only the user who started the job, or an
    admin, may cancel it - the project's owner may watch a job an admin
    started on it, but not stop it, and a job that records no starter (an
    update) is an admin's to cancel."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=JOB_NOT_FOUND)
    if not _may_cancel(job, user):
        raise HTTPException(status_code=403, detail=CANCEL_FORBIDDEN)
    cancelled = job_store.cancel(job_id)
    audit(JOB_CANCEL, user=user, entity="job", entity_id=job_id, detail=job.get("kind"))
    return cancelled
