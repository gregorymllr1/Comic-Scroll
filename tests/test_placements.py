from __future__ import annotations

import json

import numpy as np
from PIL import Image

from scrollstrip.assemble import assemble_project, iter_panel_placements
from scrollstrip.config import DEFAULTS
from scrollstrip.project import load_project


def project_with(pages):
    return {"name": "T", "version": 1, "config": {}, "pages": pages}


def test_placements_follow_panel_order():
    project = project_with([{
        "id": "p1", "cleaned": "work/cleaned/p1.jpg", "kind": "normal",
        "panels": [
            {"id": "b", "bbox": [0, 100, 50, 50], "order": 1, "gutter_after": "tight", "scale": 1.0},
            {"id": "a", "bbox": [0, 0, 50, 50], "order": 0, "gutter_after": "medium", "scale": 1.0},
        ],
    }])
    got = list(iter_panel_placements(project, DEFAULTS))
    assert [p["bbox"] for p in got] == [[0, 0, 50, 50], [0, 100, 50, 50]]
    assert [p["gutter_after"] for p in got] == [80, 32]


def test_skip_pages_are_omitted():
    project = project_with([
        {"id": "p1", "cleaned": "c/p1.jpg", "kind": "skip", "panels": []},
        {"id": "p2", "cleaned": "c/p2.jpg", "kind": "normal",
         "panels": [{"id": "a", "bbox": [0, 0, 9, 9], "order": 0, "scale": 1.0}]},
    ])
    assert [p["page_id"] for p in iter_panel_placements(project, DEFAULTS)] == ["p2"]


def test_page_with_no_panels_becomes_one_full_page_placement():
    project = project_with([
        {"id": "p1", "cleaned": "c/p1.jpg", "kind": "splash", "panels": []},
    ])
    got = list(iter_panel_placements(project, DEFAULTS))
    assert len(got) == 1
    assert got[0]["bbox"] is None
    assert got[0]["scale"] == 1.0
    assert got[0]["gutter_after"] == 280  # the large page-break gutter


def test_cbz_filename_is_the_directory_slug_when_the_name_contains_a_colon(tmp_path):
    project_dir = tmp_path / "watchmen-chapter-1"
    cleaned = project_dir / "work" / "cleaned"
    cleaned.mkdir(parents=True)
    Image.fromarray(np.full((200, 150, 3), 40, np.uint8)).save(cleaned / "p1.jpg")
    (project_dir / "project.json").write_text(json.dumps({
        "name": "Watchmen: Chapter 1",
        "version": 1,
        "config": {},
        "pages": [{
            "id": "p1",
            "cleaned": "work/cleaned/p1.jpg",
            "kind": "normal",
            "panels": [{
                "id": "a", "bbox": [0, 0, 150, 200], "order": 0,
                "scale": 1.0, "gutter_after": "tight",
            }],
        }],
    }), encoding="utf-8")

    result = assemble_project(project_dir, DEFAULTS)

    assert result["name"] == "Watchmen: Chapter 1"
    assert result["export"]["cbz"] == "export/watchmen-chapter-1.cbz"
    assert (project_dir / "export" / "watchmen-chapter-1.cbz").is_file()
    assert not (project_dir / "export" / "Watchmen: Chapter 1.cbz").exists()
    assert load_project(project_dir)["export"]["cbz"] == "export/watchmen-chapter-1.cbz"
