from __future__ import annotations

from scrollstrip.assemble import iter_panel_placements
from scrollstrip.config import DEFAULTS


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
