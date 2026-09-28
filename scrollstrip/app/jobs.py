# scrollstrip/app/jobs.py
"""A single-worker job queue.

One worker, not a pool: the imaging stack is CPU-bound and already uses multiple
cores internally, so concurrent jobs would contend rather than help. Jobs are
in-memory only - every stage is idempotent and re-runnable, and project.json is
always written atomically, so losing the queue on a crash costs nothing.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..errors import JobCancelled
from .library import log_dir

VALID_KINDS = {"import", "clean", "detect", "assemble"}


def _write_traceback(job_id: str, exc: Exception) -> Path:
    """Persist the full traceback so a failure can be diagnosed after the fact.

    The UI only shows str(exc); without this the stack is lost to a console the
    packaged app does not have.
    """
    path = log_dir() / f"scrollstrip-{datetime.now():%Y-%m-%d}.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n--- job {job_id} at {datetime.now():%H:%M:%S} ---\n")
        handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    return path


class JobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._pending: queue.Queue[str] = queue.Queue()
        self._subscribers: list[queue.Queue] = []
        self._stop = threading.Event()
        self._fns: dict[str, Callable] = {}
        self._cancelled: set[str] = set()
        self._worker = threading.Thread(target=self._run, daemon=True, name="scrollstrip-worker")
        self._worker.start()

    # ---------------------------------------------------------------- public

    def submit(self, kind: str, project_id: str, fn: Callable) -> str:
        if kind not in VALID_KINDS:
            raise ValueError(f"Unknown job kind: {kind}")
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id, "kind": kind, "project_id": project_id,
                "state": "queued", "done": 0, "total": 0, "message": "",
                "error": None, "log": None, "created": time.time(), "finished": None,
            }
            self._order.append(job_id)
            self._fns[job_id] = fn
        self._pending.put(job_id)
        self._publish(job_id)
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def all(self) -> list[dict]:
        with self._lock:
            return [dict(self._jobs[j]) for j in self._order]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["state"] in {"done", "failed", "cancelled"}:
                return False
            self._cancelled.add(job_id)
            if job["state"] == "queued":
                job["state"] = "cancelled"
                job["finished"] = time.time()
        self._publish(job_id)
        return True

    def active_project_ids(self) -> set[str]:
        with self._lock:
            return {
                j["project_id"] for j in self._jobs.values()
                if j["state"] in {"queued", "running"}
            }

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def shutdown(self) -> None:
        self._stop.set()
        self._pending.put("")

    # ---------------------------------------------------------------- internals

    def _publish(self, job_id: str) -> None:
        snapshot = self.get(job_id)
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub.put_nowait(snapshot)
            except queue.Full:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            job_id = self._pending.get()
            if not job_id or self._stop.is_set():
                continue
            with self._lock:
                job = self._jobs.get(job_id)
                if not job or job["state"] != "queued":
                    continue
                job["state"] = "running"
                fn = self._fns.pop(job_id, None)
            self._publish(job_id)

            def progress(done: int, total: int, message: str = "", _id=job_id) -> None:
                with self._lock:
                    j = self._jobs[_id]
                    j["done"], j["total"], j["message"] = done, total, message
                self._publish(_id)

            def should_cancel(_id=job_id) -> bool:
                return _id in self._cancelled

            try:
                if fn is None:
                    raise RuntimeError("job function missing")
                fn(progress, should_cancel)
                state, error = "done", None
            except JobCancelled as exc:
                state, error = "cancelled", str(exc)
            except Exception as exc:  # noqa: BLE001 - one bad job must not kill the worker
                state = "failed"
                error = f"{exc}"
                log = _write_traceback(job_id, exc)
            with self._lock:
                j = self._jobs[job_id]
                j["state"], j["error"], j["finished"] = state, error, time.time()
                if state == "failed":
                    j["log"] = str(log)
            self._publish(job_id)
