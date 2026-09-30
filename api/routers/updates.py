"""Self-update from Git: check upstream, apply (as a job), restart the backend."""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import SYSTEM_RESTART, SYSTEM_UPDATE, audit
from api.deps import admin_only, current_user
from services import jobs, updater

router = APIRouter(prefix="/system", tags=["system"])

UPDATE_KIND = "update"
UPDATE_RUNNING = "An update is already running. Wait for it to finish."
# The fields of the running update the Settings page needs to follow it, as
# the project page's ``GET /api/projects/{pid}/job`` gives them.
ACTIVE_JOB_FIELDS = ("id", "kind", "status", "user_id")


@router.get("/update")
def check_update(user: dict = Depends(current_user)):
    """Installed commit/branch and whether upstream has newer commits."""
    return updater.check_for_update()


@router.get("/update/job")
def get_update_job(user: dict = Depends(current_user)):
    """The update in flight, if there is one: ``{"active_job": {id, kind,
    status, user_id}}`` or ``{"active_job": null}``. The Settings page asks it
    on load, so a reload finds an update still running, and while it follows
    none, so it can ask after one it gave up on once the server answers
    again. Any signed-in user: an update records no starter, so its job is
    anyone's to read (``api.routers.jobs``)."""
    active = jobs.active_of_kind(UPDATE_KIND)
    return {"active_job": {key: active.get(key) for key in ACTIVE_JOB_FIELDS} if active else None}


@router.post("/update")
def apply_update(user: dict = Depends(admin_only)):
    """Fast-forward to upstream + reinstall deps, as a job (poll /api/jobs/{id}).
    409 while an update is already queued or running: two ``git pull`` and
    ``pip install`` runs over one install at once are never wanted."""
    try:
        job_id = jobs.start_single(UPDATE_KIND, lambda progress: updater.apply_update(progress))
    except jobs.KindBusy:
        raise HTTPException(status_code=409, detail=UPDATE_RUNNING) from None
    audit(SYSTEM_UPDATE, user=user, entity="system", detail=f"job {job_id}")
    return {"job_id": job_id}


@router.post("/restart")
def restart(user: dict = Depends(admin_only)):
    """Relaunch the backend in place so freshly pulled code is served."""
    # Recorded BEFORE the restart is scheduled: after it, this process is gone.
    audit(SYSTEM_RESTART, user=user, entity="system")
    updater.schedule_restart()
    return {"restarting": True}
