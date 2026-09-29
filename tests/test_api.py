# tests/test_api.py
from __future__ import annotations

import io
import json
import threading
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scrollstrip.app.library import chapter_dir
from scrollstrip.app.server import create_app
from scrollstrip.config import load_yaml
from scrollstrip.project import merged_config


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "lib"
    root.mkdir()
    app = create_app(root=root)
    with TestClient(app) as c:
        c.root = root
        yield c
    app.state.jobs.shutdown()


def make_cbz(path, pages=3):
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, pages + 1):
            buf = io.BytesIO()
            Image.fromarray(np.full((400, 300, 3), 40 * i % 255, np.uint8)).save(buf, "PNG")
            zf.writestr(f"{i:03d}.png", buf.getvalue())
    return path


def wait_for_job(client, job_id, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs").json()
        match = next((j for j in job if j["id"] == job_id), None)
        if match and match["state"] in {"done", "failed", "cancelled"}:
            return match
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_empty_library_returns_empty_list(client):
    assert client.get("/api/library").json() == []


def test_import_returns_immediately_with_job_and_project_id(client, tmp_path):
    cbz = make_cbz(tmp_path / "book.cbz")
    started = time.time()
    r = client.post("/api/library/import", json={"source": str(cbz), "name": "Book"})
    assert r.status_code == 200
    assert time.time() - started < 2.0, "import must not block on extraction"
    body = r.json()
    assert body["job_id"] and body["project_id"]


def test_importing_chapter_is_visible_in_library_while_still_ingesting(client, tmp_path):
    cbz = make_cbz(tmp_path / "book.cbz")
    body = client.post("/api/library/import", json={"source": str(cbz), "name": "Book"}).json()
    listed = client.get("/api/library").json()
    assert any(c["id"] == body["project_id"] for c in listed)
    assert listed[0]["status"] == "processing"


def test_unsupported_source_returns_structured_error(client, tmp_path):
    bad = tmp_path / "book.cbr"
    bad.write_bytes(b"Rar!")
    r = client.post("/api/library/import", json={"source": str(bad), "name": "X"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "IngestError"
    assert "cbz" in body["hint"].lower()


def test_missing_source_returns_404_not_500(client, tmp_path):
    r = client.post("/api/library/import", json={"source": str(tmp_path / "nope.cbz"), "name": "X"})
    assert r.status_code == 404
    assert r.json()["message"]


def test_media_rejects_traversal(client, tmp_path):
    (client.root / "ch").mkdir()
    (client.root / "ch" / "project.json").write_text('{"name":"c","pages":[]}', encoding="utf-8")
    r = client.get("/media/ch/../../../etc/passwd")
    assert r.status_code in (400, 404)


def test_jobs_endpoint_lists_and_cancels(client, tmp_path):
    cbz = make_cbz(tmp_path / "book.cbz", pages=8)
    job_id = client.post("/api/library/import", json={"source": str(cbz), "name": "B"}).json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    final = wait_for_job(client, job_id)
    assert final["state"] in {"cancelled", "done"}


def test_same_name_imports_reserve_distinct_directories(client, tmp_path):
    release = threading.Event()
    client.app.state.jobs.submit("clean", "blocker", lambda p, c: release.wait(5))
    first_src = make_cbz(tmp_path / "one.cbz")
    second_src = make_cbz(tmp_path / "two.cbz")
    first = client.post("/api/library/import", json={"source": str(first_src), "name": "Book"}).json()
    second = client.post("/api/library/import", json={"source": str(second_src), "name": "Book"}).json()
    try:
        assert first["project_id"] != second["project_id"]
        assert second["project_id"] == f"{first['project_id']}-2"
    finally:
        release.set()


def test_inflight_library_row_uses_requested_name(client, tmp_path):
    started = threading.Event()
    pause = threading.Event()
    real_submit = client.app.state.jobs.submit

    def submit(kind, project_id, fn):
        def wrapped(progress, should_cancel):
            def gated(done, total, message=""):
                progress(done, total, message)
                if message:
                    started.set()
                    pause.wait(5)
            fn(gated, should_cancel)
        return real_submit(kind, project_id, wrapped)

    client.app.state.jobs.submit = submit
    cbz = make_cbz(tmp_path / "book.cbz")
    try:
        body = client.post("/api/library/import", json={"source": str(cbz), "name": "Book"}).json()
        assert started.wait(5.0), "import never started"
        listed = client.get("/api/library").json()
        row = next(c for c in listed if c["id"] == body["project_id"])
        assert row["status"] == "processing"
        assert row["name"] == "Book"
        assert row["name"] != "Importing Book"
        assert row["name"] != body["project_id"] or row["name"] == "Book"
    finally:
        pause.set()


def test_import_persists_width_and_engine_in_chapter_config(client, tmp_path):
    cbz = make_cbz(tmp_path / "book.cbz")
    body = client.post(
        "/api/library/import",
        json={"source": str(cbz), "name": "Sized", "width": 800, "engine": "cv"},
    ).json()
    final = wait_for_job(client, body["job_id"])
    assert final["state"] == "done", final.get("error")
    project = chapter_dir(client.root, body["project_id"])
    cfg = merged_config(project)
    assert cfg["canvas_width"] == 800
    assert cfg["detect"]["engine"] == "cv"
    on_disk = load_yaml(project / "config.yaml")
    assert on_disk.get("canvas_width") == 800
    assert (on_disk.get("detect") or {}).get("engine") == "cv"


def test_overlapping_reservations_do_not_share_a_directory(client, tmp_path, monkeypatch):
    release = threading.Event()
    client.app.state.jobs.submit("clean", "blocker", lambda p, c: release.wait(5))
    import scrollstrip.app.server as server_mod

    real = server_mod.unique_chapter_dir
    calls = {"n": 0}

    def steal(root, name):
        calls["n"] += 1
        path = real(root, name)
        if calls["n"] == 1:
            path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(server_mod, "unique_chapter_dir", steal)
    cbz = make_cbz(tmp_path / "book.cbz")
    try:
        body = client.post("/api/library/import", json={"source": str(cbz), "name": "Book"}).json()
        assert body["project_id"] != "Book"
        assert body["project_id"] == "Book-2"
        assert calls["n"] >= 2
    finally:
        release.set()


