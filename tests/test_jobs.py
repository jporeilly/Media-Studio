"""Tests for the in-process job runner (threaded; polled for completion)."""

import time

from services import jobs


def _wait(job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.get(job_id)
        if job and job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def test_job_runs_to_done_with_result_and_progress():
    seen = []

    def work(progress):
        progress(0.5, "halfway")
        seen.append("ran")
        return {"answer": 42}

    job = _wait(jobs.submit("test", work))
    assert job["status"] == "done"
    assert job["progress"] == 1.0
    assert job["result"] == {"answer": 42}
    assert seen == ["ran"]


def test_job_captures_error_as_status():
    def work(progress):
        raise RuntimeError("boom")

    job = _wait(jobs.submit("test", work))
    assert job["status"] == "error"
    assert "boom" in (job["error"] or "")


def test_get_unknown_job_is_none():
    assert jobs.get("does-not-exist") is None


def test_final_message_is_the_summary_reported_at_one_else_complete():
    job = _wait(jobs.submit("test", lambda progress: progress(0.5, "halfway")))
    assert job["message"] == "Complete"

    def summing(progress):
        progress(0.5, "halfway")
        progress(1.0, "Enhance: 3 of 3 slides")
        return {"done": 3}

    assert _wait(jobs.submit("test", summing))["message"] == "Enhance: 3 of 3 slides"


def test_cancel_raises_the_flag_the_work_can_read_and_marks_the_result():
    import threading

    started, release = threading.Event(), threading.Event()
    seen = {}

    def work(progress):
        seen["id"] = jobs.current_job_id()
        seen["before"] = jobs.cancel_requested_here()
        started.set()
        release.wait(5)
        seen["after"] = jobs.cancel_requested_here()
        return {"cancelled": seen["after"], "done": 1}

    job_id = jobs.submit("test", work)
    assert started.wait(5)
    assert jobs.cancel(job_id)["cancel_requested"] is True
    release.set()
    job = _wait(job_id)
    assert seen == {"id": job_id, "before": False, "after": True}
    assert job["status"] == "done" and job["message"] == "Cancelled" and job["result"]["cancelled"] is True
    assert job["cancel_requested"] is True
    assert jobs.cancel(job_id)["status"] == "done", "a finished job is returned as it is"
    assert jobs.cancel("does-not-exist") is None
    assert jobs.current_job_id() is None, "the worker thread's job id never leaks into the caller's thread"
