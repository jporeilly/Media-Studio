"""In-process background jobs with progress, polled by the client.

Mirrors OpenSight's no-websocket model: a POST starts a job and returns its id;
the client polls ``GET /api/jobs/{id}`` until ``status`` is ``done`` or ``error``.
Jobs run in a small thread pool and their state lives in memory — fine for the
single-process edition; a durable queue is a later concern (see the plan).

A job may be attached to a project (``project_id``): the generate, re-voice,
transcribe, render-slides and ``ai-*`` jobs are. ONE job per project at a time:
every such job holds its own copy of the project state (the generate job's
``ProjectManager`` would ``save()`` stale notes over what an AI job wrote,
undo history included), so a project with a job queued or running refuses
another job and every slide write. ``require_idle`` is that check (a
``ProjectBusy`` the API answers with 409) and ``start`` is check-and-submit
under one lock, so two requests cannot both pass the check and both start.

Cancellation is cooperative: ``cancel(job_id)`` only raises a flag. A job whose
work loops over slides (the ``ai-*`` kinds, ``services.ai_slides``) checks
``cancel_requested_here()`` between slides, stops, and finishes as ``done`` with
``result["cancelled"] = True`` and what it had written so far kept; a re-voice
(``services.revoice``) checks it between its stages and between the sentences
it synthesises, and finishes the same way; a job that never looks at the flag
simply runs to its end. A cancelled job's closing message is the line it
reported at 1.0, when it reported one (the re-voice says what it left on
disk), else "Cancelled". A job remembers the user who started it
(``user_id``): only that user, or an admin, may cancel it.

**Jobs live in memory**, so a restart of the process forgets every one of
them: a page still polling an id then gets ``None`` here (404 at the route)
and has to treat the job as lost. ``active_for`` is how a page that did not
start a project's job - after a reload, in another tab, or when someone else
started it - finds the one to follow.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from utils.logger import get_logger

logger = get_logger("JOBS")

# Transcription is heavy (CPU/GPU); keep concurrency low.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()
# Check-and-submit of a project job is one step (``start``).
_start_lock = threading.Lock()
# The id of the job the current worker thread is running (``current_job_id``).
_current = threading.local()

# Progress callback the work function receives: progress(fraction, message).
ProgressFn = Callable[[float, str], None]

ACTIVE_STATUSES = ("queued", "running")


class KindBusy(RuntimeError):
    """A job of this kind that belongs to no project - an update - is already
    queued or running (``job`` is its state); ``start_single`` refuses a
    second. The route answers it with 409."""

    def __init__(self, job: dict):
        self.job = job
        super().__init__(f"A job of this kind ({job['kind']}) is already running. Wait for it to finish.")


class ProjectBusy(RuntimeError):
    """The project already has a job queued or running (``job`` is its state).
    The API answers it with 409 (``api.app``)."""

    def __init__(self, job: dict):
        self.job = job
        super().__init__(f"A job is running for this project ({job['kind']}). Wait for it to finish, then try again.")


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
    cancel_requested: bool = False  # set by ``cancel``; honoured by jobs that poll it
    user_id: str | None = None  # who started it; only they (or an admin) may cancel it

    def to_dict(self) -> dict:
        return asdict(self)


def submit(kind: str, work: Callable[[ProgressFn], Any], project_id: str | None = None, user_id: str | None = None) -> str:
    """Start a job. ``work(progress)`` runs in a worker thread and may call
    ``progress(fraction, message)``; its return value becomes ``job.result``.
    ``project_id`` attaches the job to a project (see ``active_for``) - use
    ``start`` for that, which refuses a second job on a busy project.
    Returns the new job id.
    """
    job = Job(id=uuid.uuid4().hex[:12], kind=kind, project_id=project_id, user_id=user_id)
    with _lock:
        _jobs[job.id] = job

    final = {"message": ""}  # what the work reported at fraction 1.0, if anything

    def _progress(fraction: float, message: str = "") -> None:
        with _lock:
            job.progress = max(0.0, min(1.0, float(fraction)))
            if message:
                job.message = message
                final["message"] = message if job.progress >= 1.0 else ""
            if job.status == "queued":
                job.status = "running"

    def _run() -> None:
        with _lock:
            job.status = "running"
        _current.job_id = job.id
        try:
            result = work(_progress)
            with _lock:
                job.result = result
                job.progress = 1.0
                job.status = "done"
                # A job that stopped on its cancel flag says so (its result
                # carries the tally) - in its own words when it summed itself
                # up at 1.0 (the re-voice says what it left on disk), else
                # "Cancelled"; one that ran to its end and summed itself up
                # keeps that line ("Enhance: 38 of 40 slides, 2 failed");
                # else "Complete".
                if isinstance(result, dict) and result.get("cancelled"):
                    job.message = final["message"] or "Cancelled"
                else:
                    job.message = final["message"] or "Complete"
        except Exception as exc:  # noqa: BLE001 — surface any failure to the poller
            with _lock:
                job.status = "error"
                job.error = str(exc)
                job.message = str(exc)
            # The traceback into app.log (and the console) once, through the
            # app's logger - ``traceback.print_exc()`` reached the console only,
            # so a failed job left nothing in the file a support request reads.
            logger.exception("Job %s (%s) failed: %s", job.id, job.kind, exc)
        finally:
            _current.job_id = None  # the worker thread is reused by the next job

    _executor.submit(_run)
    return job.id


def get(job_id: str) -> dict | None:
    """The job's current state as a dict, or None if unknown."""
    with _lock:
        job = _jobs.get(job_id)
        return job.to_dict() if job else None


def cancel(job_id: str) -> dict | None:
    """Ask a job to stop: raises its ``cancel_requested`` flag while it is
    queued or running (a finished job is left as it is) and returns its state,
    or None for an unknown id. Whether the job stops is up to its work loop
    (see the module docstring); the caller keeps polling ``get`` as usual."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if job.status in ACTIVE_STATUSES:
            job.cancel_requested = True
        return job.to_dict()


def cancel_requested(job_id: str) -> bool:
    with _lock:
        job = _jobs.get(job_id)
        return bool(job and job.cancel_requested)


def current_job_id() -> str | None:
    """The id of the job the calling thread is running, or None outside a job."""
    return getattr(_current, "job_id", None)


def cancel_requested_here() -> bool:
    """True when the calling thread runs a job whose cancel was requested
    (False outside a job, so a service loop can be called directly as well)."""
    job_id = current_job_id()
    return bool(job_id) and cancel_requested(job_id)


def active_for(project_id: str) -> dict | None:
    """The queued or running job attached to ``project_id`` (the newest, if
    several), as a dict, or None when the project has no job in flight."""
    with _lock:
        active = [job for job in _jobs.values() if job.project_id == project_id and job.status in ACTIVE_STATUSES]
        if not active:
            return None
        return max(active, key=lambda job: job.created_at).to_dict()


def require_idle(project_id: str) -> None:
    """Raise ``ProjectBusy`` while a job is attached to ``project_id``."""
    active = active_for(project_id)
    if active:
        raise ProjectBusy(active)


def start(
    kind: str, work: Callable[[ProgressFn], Any], project_id: str, user_id: str | None = None, *, reuse: bool = False,
) -> str:
    """Attach a job to a project: ``require_idle`` and ``submit`` as one step
    under the start lock, so two requests cannot both start one. With
    ``reuse`` an active job of the SAME kind is returned instead of refused
    (a second render request joins the render in flight). Raises
    ``ProjectBusy`` otherwise."""
    with _start_lock:
        active = active_for(project_id)
        if active:
            if reuse and active["kind"] == kind:
                return active["id"]
            raise ProjectBusy(active)
        return submit(kind, work, project_id=project_id, user_id=user_id)


def active_of_kind(kind: str) -> dict | None:
    """The queued or running job of ``kind`` that belongs to NO project (the
    newest, if several) - the update - as a dict, or None. How the Settings
    page finds an update already in flight after a reload."""
    with _lock:
        active = [
            job for job in _jobs.values()
            if job.kind == kind and job.project_id is None and job.status in ACTIVE_STATUSES
        ]
        if not active:
            return None
        return max(active, key=lambda job: job.created_at).to_dict()


def start_single(kind: str, work: Callable[[ProgressFn], Any], user_id: str | None = None) -> str:
    """Submit a job that belongs to no project, one of its kind at a time:
    the check and the submit are one step under the start lock, and a second
    while one is queued or running raises ``KindBusy``. An update is such a
    job - two ``git pull``/``pip install`` runs at once over the same install
    are never wanted."""
    with _start_lock:
        active = active_of_kind(kind)
        if active:
            raise KindBusy(active)
        return submit(kind, work, user_id=user_id)
