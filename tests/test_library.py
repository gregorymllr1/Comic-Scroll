# tests/test_library.py
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scrollstrip.app.library import library_root, list_chapters


def write_chapter(root: Path, cid: str, pages, name=None, export=False):
    d = root / cid
    (d / "work").mkdir(parents=True, exist_ok=True)
    data = {"name": name or cid, "version": 1, "config": {}, "pages": pages}
    (d / "project.json").write_text(json.dumps(data), encoding="utf-8")
    if export:
        (d / "export").mkdir(exist_ok=True)
        time.sleep(0.01)
        (d / "export" / f"{cid}.cbz").write_bytes(b"PK")
    return d


def page(pid, status="detected", panels=1, needs_review=False):
    return {
        "id": pid, "status": status, "needs_review": needs_review,
        "panels": [{"id": f"{pid}-p{i}"} for i in range(panels)],
    }


def test_finds_chapters_and_counts(tmp_path):
    write_chapter(tmp_path, "ch1", [page("a", panels=3), page("b", panels=2)])
    got = list_chapters(tmp_path)
    assert len(got) == 1
    assert got[0]["id"] == "ch1"
    assert got[0]["page_count"] == 2
    assert got[0]["panel_count"] == 5


def test_ignores_directories_without_project_json(tmp_path):
    write_chapter(tmp_path, "ch1", [page("a")])
    (tmp_path / "not-a-chapter").mkdir()
    (tmp_path / "logs").mkdir()
    assert [c["id"] for c in list_chapters(tmp_path)] == ["ch1"]


def test_status_new_when_nothing_detected(tmp_path):
    write_chapter(tmp_path, "ch1", [page("a", status="cleaned")])
    assert list_chapters(tmp_path)[0]["status"] == "new"


def test_status_needs_review_when_any_page_flagged(tmp_path):
    write_chapter(tmp_path, "ch1", [page("a"), page("b", needs_review=True)])
    c = list_chapters(tmp_path)[0]
    assert c["status"] == "needs_review"
    assert c["needs_review"] == 1


def test_status_ready_when_cbz_newer_than_project(tmp_path):
    write_chapter(tmp_path, "ch1", [page("a")], export=True)
    assert list_chapters(tmp_path)[0]["status"] == "ready"


def test_status_processing_overrides_everything(tmp_path):
    write_chapter(tmp_path, "ch1", [page("a", needs_review=True)])
    got = list_chapters(tmp_path, active_project_ids={"ch1"})
    assert got[0]["status"] == "processing"


def test_damaged_project_json_is_listed_not_hidden(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "project.json").write_text("{not json", encoding="utf-8")
    got = list_chapters(tmp_path)
    assert len(got) == 1
    assert got[0]["status"] == "damaged"
    assert got[0]["error"]


# Review Focus #5
def test_missing_library_root_returns_empty_not_crash(tmp_path):
    assert list_chapters(tmp_path / "does-not-exist") == []


def test_library_root_that_is_a_file_raises_readable_error(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    with pytest.raises(NotADirectoryError, match="afile"):
        list_chapters(f)


def test_library_root_honours_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("SCROLLSTRIP_LIBRARY", str(tmp_path / "custom"))
    assert library_root() == tmp_path / "custom"
