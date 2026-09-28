from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from scrollstrip.clean import clean_project
from scrollstrip.config import DEFAULTS, deep_merge
from scrollstrip.errors import JobCancelled
from scrollstrip.project import init_project


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
