from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from scrollstrip.app.media import MAX_WIDTH, cached_resize, resolve_media


def make_project(tmp_path, w=2000, h=3000):
    d = tmp_path / "ch"
    (d / "work" / "cleaned").mkdir(parents=True)
    Image.fromarray(np.full((h, w, 3), 128, np.uint8)).save(d / "work" / "cleaned" / "p1.jpg")
    return d


def test_serves_original_when_no_width(tmp_path):
    d = make_project(tmp_path)
    got = cached_resize(d, "work/cleaned/p1.jpg", None)
    assert Image.open(got).size == (2000, 3000)


def test_resizes_and_caches(tmp_path):
    d = make_project(tmp_path)
    first = cached_resize(d, "work/cleaned/p1.jpg", 200)
    assert Image.open(first).size[0] == 200
    assert first.parent == d / "work" / "cache" / "200"
    mtime = first.stat().st_mtime_ns
    second = cached_resize(d, "work/cleaned/p1.jpg", 200)
    assert second == first and second.stat().st_mtime_ns == mtime  # served from cache


def test_never_upscales(tmp_path):
    d = make_project(tmp_path, w=300, h=400)
    got = cached_resize(d, "work/cleaned/p1.jpg", 1200)
    assert Image.open(got).size == (300, 400)


@pytest.mark.parametrize("bad", ["../secrets.txt", "/etc/passwd", "..\\..\\x.jpg", "C:/Windows/x.jpg"])
def test_rejects_path_traversal(tmp_path, bad):
    d = make_project(tmp_path)
    with pytest.raises(ValueError):
        resolve_media(d, bad)


# Review Focus #3
@pytest.mark.parametrize("bad", [0, -5, -1])
def test_non_positive_width_serves_original(tmp_path, bad):
    d = make_project(tmp_path)
    got = cached_resize(d, "work/cleaned/p1.jpg", bad)
    assert Image.open(got).size == (2000, 3000)


def test_absurd_width_is_clamped(tmp_path):
    d = make_project(tmp_path)
    got = cached_resize(d, "work/cleaned/p1.jpg", 100_000)
    assert Image.open(got).size[0] <= MAX_WIDTH


def test_backslash_dotdot_cache_stays_in_width_dir(tmp_path):
    d = make_project(tmp_path)
    # Forward-slash pads give resolve_media enough depth to stay in-project.
    # Backslash ".." segments stay separators in the cache join on Windows.
    rel = "a/b/c/d/e\\..\\..\\..\\..\\..\\work\\cleaned\\p1.jpg"
    source = resolve_media(d, rel)
    assert source == (d / "work" / "cleaned" / "p1.jpg").resolve()
    got = cached_resize(d, rel, 200)
    cache_dir = (d / "work" / "cache" / "200").resolve()
    assert cache_dir in got.resolve().parents
    assert Image.open(got).size[0] == 200
