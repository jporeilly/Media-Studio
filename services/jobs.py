"""In-process background jobs with progress, polled by the client.

Mirrors OpenSight's no-websocket model: a POST starts a job and returns its id;
the client polls ``GET /api/jobs/{id}`` until ``status`` is ``done`` or ``error``.
Jobs run in a small thread pool and their state lives in memory — fine for the
single-process edition; a durable queue is a later concern (see the plan).

A job may be attached to a project (``project_id``): the generate, re-voice,
transcribe and render-slides jobs are, so the slide editor can refuse writes
while one of them is queued or running (``active_for``) - a job holds its own
copy of the project state and would overwrite an edit made meanwhile.
"""

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# Transcription is heavy (CPU/GPU); keep concurrency low.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()

# Progress callback the work function receives: progress(fraction, message).
ProgressFn = Callable[[float, str], None]

ACTIVE_STATUSES = ("queued", "running")


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_id: str | None = None  # the project the job works on, when it has one

    def to_dict(self) -> dict:
        return asdict(self)


def submit(kind: str, work: Callable[[ProgressFn], Any], project_id: str | None = None) -> str:
    """Start a job. ``work(progress)`` runs in a worker thread and may call
    ``progress(fraction, message)``; its return value becomes ``job.result``.
    ``project_id`` attaches the job to a project (see ``active_for``).
    Returns the new job id.
    """
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, project_id=project_id)
    with _lock:
        _jobs[job.id] = job

    def _progress(fraction: float, message: str = "") -> None:
        with _lock:
            job.progress = max(0.0, min(1.0, float(fraction)))
            if message:
                job.message = message
            if job.status == "queued":
                job.status = "running"

    def _run() -> None:
        with _lock:
            job.status = "running"
        try:
            result = work(_progress)
            with _lock:
                job.result = result
                job.progress = 1.0
                job.status = "done"
                job.message = "Complete"
        except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
            with _lock:
                job.status = "error"
                job.error = str(exc)
                job.message = str(exc)
            traceback.print_exc()

    _executor.submit(_run)
    return job.id


def get(job_id: str) -> dict | None:
    """The job's current state as a dict, or None if unknown."""
    with _lock:
        job = _jobs.get(job_id)
        return job.to_dict() if job else None


def active_for(project_id: str) -> dict | None:
    """The queued or running job attached to ``project_id`` (the newest, if
    several), as a dict, or None when the project has no job in flight."""
    with _lock:
        active = [job for job in _jobs.values() if job.project_id == project_id and job.status in ACTIVE_STATUSES]
        if not active:
            return None
        return max(active, key=lambda job: job.created_at).to_dict()
