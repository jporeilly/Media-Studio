"""Background job status, polled by the client until done / error."""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import JOB_CANCEL, audit
from api.deps import current_user
from services import jobs as job_store

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, user: dict = Depends(current_user)):
    """A job's state. Only the user who started it, or an admin, may read it.

    A job carries its kind, its project id, its progress messages and, when it
    finishes, the names of the files it produced. Once projects have owners that
    is somebody else's work, so reading one was the last cross-tenant leak: any
    signed-in editor holding an id could watch it. Guarded exactly as ``cancel``
    below is, including the older jobs that carry no ``user_id`` at all - those
    predate the field and refusing them would break a poll already in flight.
    """
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("user_id") and job["user_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the user who started this job, or an admin, can see it.")
    return job


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, user: dict = Depends(current_user)):
    """Ask a job to stop. Cooperative: the ``ai-*`` jobs check the flag
    between slides (the QA review between passes) and finish as ``done``
    with ``result.cancelled`` true and what they had written kept; other
    kinds run to their end. Returns the job's state (a finished job is
    returned as it is); keep polling GET. Only the user who started the job,
    or an admin, may cancel it."""
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("user_id") and job["user_id"] != user["id"] and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only the user who started this job, or an admin, can cancel it.")
    cancelled = job_store.cancel(job_id)
    audit(JOB_CANCEL, user=user, entity="job", entity_id=job_id, detail=job.get("kind"))
    return cancelled
