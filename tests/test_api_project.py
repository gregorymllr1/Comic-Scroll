# tests/test_api_project.py
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from scrollstrip.app.server import create_app


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "lib"
    (root / "ch1").mkdir(parents=True)
    (root / "ch1" / "project.json").write_text(json.dumps({
        "name": "Ch1", "version": 1, "config": {},
        "pages": [{
            "id": "p1", "cleaned": "work/cleaned/p1.jpg", "kind": "normal",
            "status": "detected", "width": 100, "height": 200,
            "panels": [{"id": "p1-p01", "bbox": [0, 0, 50, 50], "order": 0,
                        "role": "normal", "scale": 1.0, "gutter_after": "medium",
                        "locked": False, "score": 0.9}],
        }],
    }), encoding="utf-8")
    app = create_app(root=root)
    with TestClient(app) as c:
        yield c
    app.state.jobs.shutdown()


def test_get_project(client):
    body = client.get("/api/project/ch1").json()
    assert body["name"] == "Ch1"
    assert len(body["pages"]) == 1


def test_get_unknown_project_is_404(client):
    assert client.get("/api/project/nope").status_code == 404


def test_put_page_saves_panels(client):
    page = client.get("/api/project/ch1").json()["pages"][0]
    page["panels"][0]["bbox"] = [10, 10, 60, 60]
    page["panels"].append({"id": "p1-p02", "bbox": [0, 100, 40, 40], "order": 1,
                           "role": "normal", "scale": 0.8, "gutter_after": "tight",
                           "locked": True, "score": 1.0})
    r = client.put("/api/project/ch1/page/p1", json=page)
    assert r.status_code == 200
    saved = client.get("/api/project/ch1").json()["pages"][0]
    assert saved["panels"][0]["bbox"] == [10, 10, 60, 60]
    assert len(saved["panels"]) == 2
    assert saved["panels"][1]["locked"] is True


def test_put_unknown_page_is_404(client):
    assert client.put("/api/project/ch1/page/nope", json={"panels": []}).status_code == 404


# Review Focus #4
def test_editing_is_refused_while_a_job_is_running_on_that_chapter(client):
    import threading
    release = threading.Event()
    client.app.state.jobs.submit("detect", "ch1", lambda p, c: release.wait(3.0))
    import time
    time.sleep(0.2)
    page = {"panels": [], "kind": "normal"}
    r = client.put("/api/project/ch1/page/p1", json=page)
    release.set()
    assert r.status_code == 409
    assert "running" in r.json()["message"].lower()
