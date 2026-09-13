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
