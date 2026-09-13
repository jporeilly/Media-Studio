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
