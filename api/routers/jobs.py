"""Background job status, polled by the client until done / error."""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import current_user
from services import jobs as job_store

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str, user: dict = Depends(current_user)):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
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
    return job_store.cancel(job_id)
