# tests/test_jobs.py
from __future__ import annotations

import threading
import time

import pytest

from scrollstrip.app.jobs import JobQueue
from scrollstrip.errors import JobCancelled


@pytest.fixture
def q():
    queue = JobQueue()
    yield queue
    queue.shutdown()


def wait_for(queue, job_id, state, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = queue.get(job_id)
        if job and job["state"] == state:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {state}: {queue.get(job_id)}")


def test_job_runs_and_reports_progress(q):
    def work(progress, should_cancel):
        for i in range(1, 4):
            progress(i, 3, f"step {i}")

    jid = q.submit("clean", "ch1", work)
    job = wait_for(q, jid, "done")
    assert job["done"] == 3 and job["total"] == 3


def test_jobs_run_in_fifo_order(q):
    order = []
    started = threading.Event()

    def first(progress, should_cancel):
        started.wait(2.0)
        order.append("first")

    def second(progress, should_cancel):
        order.append("second")

    a = q.submit("clean", "ch1", first)
    b = q.submit("clean", "ch2", second)
    started.set()
    wait_for(q, b, "done")
    assert order == ["first", "second"]


def test_failure_is_captured_and_does_not_kill_the_worker(q):
    def boom(progress, should_cancel):
        raise RuntimeError("detector exploded")

    failed = q.submit("detect", "ch1", boom)
    job = wait_for(q, failed, "failed")
    assert "detector exploded" in job["error"]

    ok = q.submit("clean", "ch2", lambda p, c: None)
    wait_for(q, ok, "done")


def test_cancel_while_queued_never_runs(q):
    release = threading.Event()
    ran = []
    q.submit("clean", "ch1", lambda p, c: release.wait(2.0))
    second = q.submit("clean", "ch2", lambda p, c: ran.append(1))
    assert q.cancel(second) is True
    release.set()
    wait_for(q, second, "cancelled")
    assert ran == []


def test_cancel_while_running_stops_at_next_check(q):
    def work(progress, should_cancel):
        for i in range(100):
            if should_cancel():
                raise JobCancelled("stopped")
            progress(i, 100, "")
            time.sleep(0.01)

    jid = q.submit("clean", "ch1", work)
    wait_for(q, jid, "running")
    q.cancel(jid)
    job = wait_for(q, jid, "cancelled")
    assert job["done"] < 100


def test_active_project_ids_reflects_running_work(q):
    release = threading.Event()
    jid = q.submit("clean", "ch1", lambda p, c: release.wait(2.0))
    wait_for(q, jid, "running")
    assert q.active_project_ids() == {"ch1"}
    release.set()
    wait_for(q, jid, "done")
    assert q.active_project_ids() == set()


def test_failure_writes_a_traceback_log_and_reports_its_path(q, tmp_path, monkeypatch):
    monkeypatch.setenv("SCROLLSTRIP_LIBRARY", str(tmp_path))

    def boom(progress, should_cancel):
        raise RuntimeError("detector exploded")

    jid = q.submit("detect", "ch1", boom)
    job = wait_for(q, jid, "failed")
    log = tmp_path / "logs"
    written = list(log.glob("scrollstrip-*.log"))
    assert written, "no traceback log written"
    assert "detector exploded" in written[0].read_text(encoding="utf-8")
    assert "Traceback" in written[0].read_text(encoding="utf-8")
    assert job["log"] == str(written[0])


# Review Focus #1
def test_missing_source_file_fails_readably_without_stalling_the_queue(q):
    def work(progress, should_cancel):
        raise FileNotFoundError("C:/gone/book.cbz")

    bad = q.submit("import", "ch1", work)
    job = wait_for(q, bad, "failed")
    assert "book.cbz" in job["error"]

    after = q.submit("clean", "ch2", lambda p, c: None)
    wait_for(q, after, "done")
