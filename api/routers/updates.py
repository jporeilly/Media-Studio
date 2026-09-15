"""Self-update from Git: check upstream, apply (as a job), restart the backend."""

from fastapi import APIRouter, Depends

from api.audit import SYSTEM_RESTART, SYSTEM_UPDATE, audit
from api.deps import admin_only, current_user
from services import jobs, updater

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/update")
def check_update(user: dict = Depends(current_user)):
    """Installed commit/branch and whether upstream has newer commits."""
    return updater.check_for_update()


@router.post("/update")
def apply_update(user: dict = Depends(admin_only)):
    """Fast-forward to upstream + reinstall deps, as a job (poll /api/jobs/{id})."""
    job_id = jobs.submit("update", lambda progress: updater.apply_update(progress))
    audit(SYSTEM_UPDATE, user=user, entity="system", detail=f"job {job_id}")
    return {"job_id": job_id}


@router.post("/restart")
def restart(user: dict = Depends(admin_only)):
    """Relaunch the backend in place so freshly pulled code is served."""
    # Recorded BEFORE the restart is scheduled: after it, this process is gone.
    audit(SYSTEM_RESTART, user=user, entity="system")
    updater.schedule_restart()
    return {"restarting": True}
