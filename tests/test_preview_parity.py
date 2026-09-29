# tests/test_preview_parity.py
from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scrollstrip.assemble import iter_panel_placements
from scrollstrip.app.server import create_app
from scrollstrip.config import DEFAULTS


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "lib"
    d = root / "ch1"
    (d / "work" / "cleaned").mkdir(parents=True)
    for pid in ("p1", "p2"):
        Image.fromarray(np.full((400, 300, 3), 90, np.uint8)).save(d / "work" / "cleaned" / f"{pid}.jpg")
    (d / "project.json").write_text(json.dumps({
        "name": "Ch1", "version": 1, "config": {},
        "pages": [
            {"id": "p1", "cleaned": "work/cleaned/p1.jpg", "kind": "normal", "status": "detected",
             "panels": [
                 {"id": "a", "bbox": [0, 0, 300, 200], "order": 0, "scale": 1.0, "gutter_after": "tight"},
                 {"id": "b", "bbox": [0, 200, 300, 200], "order": 1, "scale": 0.8, "gutter_after": "large"},
             ]},
            {"id": "p2", "cleaned": "work/cleaned/p2.jpg", "kind": "splash", "status": "detected", "panels": []},
        ],
    }), encoding="utf-8")
    app = create_app(root=root)
    with TestClient(app) as c:
        yield c
    app.state.jobs.shutdown()


def test_preview_matches_the_placements_the_export_uses(client):
    body = client.get("/api/project/ch1/preview").json()
    project = client.get("/api/project/ch1").json()
    expected = list(iter_panel_placements(project, DEFAULTS))

    assert len(body["panels"]) == len(expected)
    for got, want in zip(body["panels"], expected):
        assert got["page_id"] == want["page_id"]
        assert got["bbox"] == want["bbox"]
        assert got["scale"] == want["scale"]
        assert got["gutter_after"] == want["gutter_after"]


def test_preview_resolves_media_urls(client):
    body = client.get("/api/project/ch1/preview").json()
    assert body["panels"][0]["src"].startswith("/media/ch1/")
    assert client.get(body["panels"][0]["src"]).status_code == 200


def test_splash_page_appears_as_a_full_page_panel(client):
    body = client.get("/api/project/ch1/preview").json()
    last = body["panels"][-1]
    assert last["page_id"] == "p2"
    assert last["bbox"] is None


def test_preview_carries_canvas_width_and_background(client):
    body = client.get("/api/project/ch1/preview").json()
    assert body["canvas_width"] == 1080
    assert body["background"] == [18, 18, 18]
