from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from PIL import Image

from scrollstrip.clean import clean_project
from scrollstrip.config import DEFAULTS, deep_merge
from scrollstrip.detect import detect_project
from scrollstrip.errors import JobCancelled
from scrollstrip.project import init_project, load_project


def make_pages(tmp_path, count=3):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(count):
        arr = np.random.randint(0, 255, (300, 220, 3), dtype=np.uint8)
        Image.fromarray(arr).save(src / f"{i + 1:03d}.png")
    project = tmp_path / "proj"
    init_project(project, pages_dir=src, name="T")
    return project


def test_progress_is_called_once_per_page(tmp_path):
    project = make_pages(tmp_path, 3)
    seen = []
    clean_project(project, DEFAULTS, progress=lambda d, t, m: seen.append((d, t)))
    assert [d for d, _ in seen] == [1, 2, 3]
    assert {t for _, t in seen} == {3}


def test_should_cancel_stops_work(tmp_path):
    project = make_pages(tmp_path, 3)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 1

    with pytest.raises(JobCancelled):
        clean_project(project, DEFAULTS, should_cancel=cancel)


def test_callbacks_default_to_none(tmp_path):
    project = make_pages(tmp_path, 2)
    result = clean_project(project, DEFAULTS)
    assert len(result["pages"]) == 2


def _panel(pid, bbox, locked=False):
    return {
        "id": pid, "bbox": list(bbox), "order": 0, "role": "normal",
        "scale": 1.0, "gutter_after": "medium", "locked": locked, "score": 0.5,
    }


def _two_page_project(tmp_path):
    project_dir = tmp_path / "chapter"
    (project_dir / "work" / "cleaned").mkdir(parents=True)
    for pid in ("p1", "p2"):
        Image.fromarray(np.full((400, 400, 3), 240, np.uint8)).save(
            project_dir / "work" / "cleaned" / f"{pid}.jpg"
        )
    pages = [
        {
            "id": "p1", "cleaned": "work/cleaned/p1.jpg", "preview": "work/previews/p1.jpg",
            "kind": "normal", "status": "detected", "width": 400, "height": 400,
            "needs_review": False,
            "panels": [
                _panel("locked-1", [0, 0, 100, 100], locked=True),
                _panel("stale", [300, 300, 40, 40]),
            ],
        },
        {
            "id": "p2", "cleaned": "work/cleaned/p2.jpg", "preview": "work/previews/p2.jpg",
            "kind": "normal", "status": "detected", "width": 400, "height": 400,
            "needs_review": False,
            "panels": [_panel("keep-me", [1, 2, 3, 4])],
        },
    ]
    (project_dir / "project.json").write_text(json.dumps({
        "name": "T", "version": 1, "config": {}, "pages": pages,
    }), encoding="utf-8")
    return project_dir


def _cv_cfg():
    return deep_merge(DEFAULTS, {"detect": {"engine": "cv"}})


def test_keep_edits_without_only_page_id_skips_pages_that_already_have_panels(tmp_path, monkeypatch):
    project_dir = _two_page_project(tmp_path)
    calls = {"n": 0}

    def fake(image, project_dir, cfg):
        calls["n"] += 1
        return [([10, 10, 80, 80], 0.9)]

    monkeypatch.setattr("scrollstrip.detect.detect_page", fake)
    before = [page["panels"] for page in load_project(project_dir)["pages"]]
    detect_project(project_dir, _cv_cfg(), overwrite_unlocked=False)
    assert calls["n"] == 0
    after = [page["panels"] for page in load_project(project_dir)["pages"]]
    assert after == before


def test_only_page_id_redetects_that_page_and_leaves_the_other_alone(tmp_path, monkeypatch):
    project_dir = _two_page_project(tmp_path)
    cfg = _cv_cfg()
    p2_before = copy.deepcopy(load_project(project_dir)["pages"][1])

    with pytest.raises(FileNotFoundError, match="missing-id"):
        detect_project(project_dir, cfg, overwrite_unlocked=False, only_page_id="missing-id")
    assert load_project(project_dir)["pages"][1] == p2_before

    def fake(image, project_dir, cfg):
        return [([5, 5, 90, 90], 0.9), ([200, 200, 80, 80], 0.95)]

    monkeypatch.setattr("scrollstrip.detect.detect_page", fake)
    result = detect_project(project_dir, cfg, overwrite_unlocked=False, only_page_id="p1")

    assert result["pages"][1] == p2_before
    assert load_project(project_dir)["pages"][1] == p2_before
    panels = result["pages"][0]["panels"]
    boxes = [panel["bbox"] for panel in panels]
    assert [0, 0, 100, 100] in boxes
    assert [200, 200, 80, 80] in boxes
    assert [5, 5, 90, 90] not in boxes
    assert [300, 300, 40, 40] not in boxes
    locked = next(panel for panel in panels if panel["bbox"] == [0, 0, 100, 100])
    assert locked["locked"] is True
    assert all(panel["id"] != "stale" for panel in panels)
