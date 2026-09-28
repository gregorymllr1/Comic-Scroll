# Scrollstrip Desktop App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the scrollstrip CLI with a desktop application that imports comics, runs the pipeline with visible progress, lets the reader correct panel boxes, and previews the finished vertical scroll before export.

**Architecture:** A pywebview native window displays a Svelte single-page app served by a FastAPI process running in the same Python interpreter. FastAPI owns a single background worker thread that drives the existing `scrollstrip` core unchanged except for progress/cancel callbacks. The filesystem stays the source of truth — each chapter's `project.json` is authoritative and there is no separate index.

**Tech Stack:** Python 3.13, FastAPI + uvicorn, pywebview, Svelte 5 + Vite, pytest, PyInstaller. Existing core: OpenCV, numpy, Pillow, PyMuPDF, ultralytics/torch.

**Spec:** `docs/superpowers/specs/2026-09-28-scrollstrip-desktop-app-design.md`

## Global Constraints

- **Windows 11 is the target platform.** Do not write gratuitously Windows-only code; paths use `pathlib`, never string concatenation or hardcoded separators.
- **The existing 32 tests must stay green after every task.** Run `pytest tests/ -q` before each commit.
- **Core changes are additive only.** `progress=` and `should_cancel=` default to `None`; CLI behaviour must not change.
- **Library root:** `~/Documents/Scrollstrip/`, overridable by the `SCROLLSTRIP_LIBRARY` environment variable.
- **There is no `library.json`.** Chapter metadata is derived by reading each `project.json`. Do not add an index file.
- **One worker thread.** The imaging stack is CPU-bound and already multi-core internally; do not add a pool.
- **Out of scope, do not build:** auto-update, accounts, cloud sync, plugins, mobile app, code signing.
- **Media cache path:** `<project>/work/cache/{width}/`. Covered by the existing `.gitignore` `work/` rule.

## Review Focus

Five conditions the spec implies but does not assign tests to. Each has been given a test in the task that owns the code.

1. **The source file disappears between queueing an import and the job running.** The user picks a CBZ, then moves or deletes it while four other imports are ahead of it in the queue. The job must fail with a readable message naming the file, not crash the worker thread and stall every job behind it. — Task 4.
2. **Two chapters import under the same name.** `init_project` derives the directory from the project path; a second "Swamp Thing 01" must not silently overwrite the first chapter's `project.json` and destroy its panel edits. — Task 6.
3. **`?w=` receives a hostile or absurd value.** Negative, zero, non-numeric, or 100000. Must clamp or reject, never allocate a multi-gigabyte image or crash the server. — Task 7.
4. **A page is edited in the editor while detection is running on that same chapter.** The worker holds a `project` dict in memory and calls `save_project` at the end, which would silently discard the user's concurrent edits. Editing must be refused or the job must reload. — Task 11.
5. **The library root is missing, unwritable, or a file rather than a directory.** The app must start and say so, not crash on launch with a traceback before any window appears. — Task 3.

---

## File Structure

**New backend** (`scrollstrip/app/`):
- `errors.py` — typed error → HTTP payload mapping. No dependencies on other app modules.
- `library.py` — chapter discovery, metadata derivation, status. Depends on core only.
- `jobs.py` — job dataclass, queue, single worker thread. Depends on core only.
- `media.py` — on-demand resize + cache, path-traversal guard.
- `server.py` — FastAPI app factory and routes. Depends on all of the above.

**New shell:** `scrollstrip/shell.py` — pywebview entry point.

**New frontend** (`ui/`):
- `src/lib/api.js` — fetch wrapper, one function per endpoint.
- `src/lib/geometry.js` — image↔canvas coordinate math. Pure, tested.
- `src/lib/stores.js` — Svelte stores for library, jobs, active project.
- `src/routes/Library.svelte`, `Import.svelte`, `Editor.svelte`, `Reader.svelte`
- `src/lib/PanelCanvas.svelte` — the box editing surface.

**Modified core:** `clean.py`, `detect.py`, `assemble.py` (callbacks), `assemble.py` (placement split), `project.py` (collision-safe init).

---

## Phase 1 — Backend, library, import

### Task 1: Progress and cancellation callbacks in the core

**Files:**
- Modify: `scrollstrip/clean.py:252` (`clean_project`)
- Modify: `scrollstrip/detect.py:357` (`detect_project`)
- Modify: `scrollstrip/assemble.py:127` (`assemble_project`)
- Create: `scrollstrip/errors.py`
- Test: `tests/test_progress.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class JobCancelled(Exception)` in `scrollstrip/errors.py`. Signatures become `clean_project(project_dir, cfg, *, progress=None, should_cancel=None) -> dict`, identical for `detect_project` (which keeps its existing `overwrite_unlocked` positional) and `assemble_project`. `progress` is `Callable[[int, int, str], None]` called as `progress(done, total, message)`. `should_cancel` is `Callable[[], bool]`, polled once per page.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_progress.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrollstrip.errors'`

- [ ] **Step 3: Create the exception module**

```python
# scrollstrip/errors.py
"""Error types shared by the core and the app layer."""

from __future__ import annotations


class JobCancelled(Exception):
    """Raised inside a pipeline stage when the caller asked it to stop."""
```

- [ ] **Step 4: Add callbacks to `clean_project`**

Replace the body of `clean_project` in `scrollstrip/clean.py`:

```python
def clean_project(project_dir: Path, cfg: dict, *, progress=None, should_cancel=None) -> dict:
    project = load_project(project_dir)
    quality = int(cfg.get("jpeg_quality", 92))
    total = len(project["pages"])
    for index, page in enumerate(project["pages"], start=1):
        if should_cancel is not None and should_cancel():
            raise JobCancelled(f"Cancelled after {index - 1} of {total} pages")
        print(f"  cleaning {index}/{total} {page['id']}", flush=True)
        src = resolve_page_file(project_dir, page["source"])
        if not src.exists():
            raise FileNotFoundError(f"Missing source page: {src}")
        raw = _read(src)
        cleaned = clean_image(raw, cfg)
        dest = project_dir / page["cleaned"]
        _write_jpeg(dest, cleaned, quality)
        page["width"] = int(cleaned.shape[1])
        page["height"] = int(cleaned.shape[0])
        page["status"] = "cleaned"
        if progress is not None:
            progress(index, total, f"Cleaned {page['id']}")
    save_project(project_dir, project)
    return project
```

Add `from .errors import JobCancelled` to the imports at the top of `clean.py`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_progress.py -v`
Expected: PASS, 3 tests

- [ ] **Step 6: Apply the same pattern to `detect_project` and `assemble_project`**

In `detect.py`, change the signature to `detect_project(project_dir, cfg, overwrite_unlocked=True, *, progress=None, should_cancel=None)`. Inside the existing `for page in project["pages"]:` loop, convert it to `for index, page in enumerate(project["pages"], start=1):` and add at the top of the loop body:

```python
        if should_cancel is not None and should_cancel():
            raise JobCancelled(f"Cancelled after {index - 1} of {total} pages")
```

where `total = len(project["pages"])` is computed before the loop, and at the end of the loop body:

```python
        if progress is not None:
            progress(index, total, f"Detected {len(auto_panels)} panels on {page['id']}")
```

In `assemble.py`, change the signature to `assemble_project(project_dir, cfg, *, progress=None, should_cancel=None)`. Assembly has two phases, so report both: wrap the `list(iter_assembled_blocks(...))` call in a loop that counts pages, and report once per written slice in the existing `for i, sl in enumerate(slices, start=1):` loop:

```python
    blocks = []
    pages_total = len(project["pages"])
    for page_index, block in enumerate(iter_assembled_blocks(project_dir, project, cfg), start=1):
        if should_cancel is not None and should_cancel():
            raise JobCancelled("Cancelled during composition")
        blocks.append(block)
    slices = pack_slices(blocks, cfg)
    names = []
    for i, sl in enumerate(slices, start=1):
        name = f"{i:03d}.jpg"
        _write_jpeg(slice_dir / name, sl, quality)
        names.append(name)
        if progress is not None:
            progress(i, len(slices), f"Wrote slice {name}")
```

Add `from .errors import JobCancelled` to both files.

- [ ] **Step 7: Run the whole suite**

Run: `pytest tests/ -q`
Expected: PASS — 32 existing plus 3 new = 35

- [ ] **Step 8: Commit**

```bash
git add scrollstrip/errors.py scrollstrip/clean.py scrollstrip/detect.py scrollstrip/assemble.py tests/test_progress.py
git commit -m "feat: add progress and cancellation callbacks to pipeline stages"
```

---

### Task 2: Split panel placement from image compositing

The spec identifies preview/export divergence as a risk. `iter_assembled_blocks` currently decides panel order and gutter sizes *and* crops and composites images in one pass. The reader needs the former without the latter. Extracting the decision layer means both paths share one implementation, so they cannot drift.

**Files:**
- Modify: `scrollstrip/assemble.py:58-83`
- Test: `tests/test_placements.py`

**Interfaces:**
- Consumes: Task 1's changes to `assemble_project`.
- Produces: `iter_panel_placements(project: dict, cfg: dict) -> Iterator[dict]`. Each dict has keys `page_id: str`, `cleaned: str` (project-relative path), `bbox: list[int] | None` (None means the whole page), `scale: float`, `gutter_after: int` (pixels). `iter_assembled_blocks(project_dir, project, cfg)` keeps its existing signature and return type and is reimplemented on top of it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_placements.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_placements.py -v`
Expected: FAIL — `ImportError: cannot import name 'iter_panel_placements'`

- [ ] **Step 3: Extract the placement decisions**

Add to `scrollstrip/assemble.py`, directly above `iter_assembled_blocks`:

```python
def iter_panel_placements(project: dict, cfg: dict):
    """Decide what is shown, in what order, at what scale, with what gap after.

    This is the single source of reading order and gutter sizing. Both the CBZ
    export and the in-app reader consume it, so the preview cannot drift from
    what actually gets exported.
    """
    for page in project["pages"]:
        if page.get("kind") == "skip":
            continue
        panels = sorted_panels(page)
        if not panels:
            yield {
                "page_id": page["id"],
                "cleaned": page["cleaned"],
                "bbox": None,
                "scale": 1.0,
                "gutter_after": gutter_px(
                    cfg.get("heuristics", {}).get("page_break_gutter", "large"), cfg
                ),
            }
            continue
        for panel in panels:
            yield {
                "page_id": page["id"],
                "cleaned": page["cleaned"],
                "bbox": list(panel["bbox"]),
                "scale": float(panel.get("scale", 1.0)),
                "gutter_after": gutter_px(
                    panel.get("gutter_after", cfg.get("default_gutter", "medium")), cfg
                ),
            }
```

- [ ] **Step 4: Reimplement `iter_assembled_blocks` on top of it**

Replace the existing `iter_assembled_blocks` body:

```python
def iter_assembled_blocks(project_dir: Path, project: dict, cfg: dict):
    canvas_width = int(cfg.get("canvas_width", 1080))
    background = tuple(int(c) for c in cfg.get("background", [18, 18, 18]))
    cache: dict[str, np.ndarray] = {}
    for place in iter_panel_placements(project, cfg):
        cleaned = project_dir / place["cleaned"]
        if not cleaned.exists():
            raise FileNotFoundError(cleaned)
        image = cache.get(place["cleaned"])
        if image is None:
            image = _read(cleaned)
            cache = {place["cleaned"]: image}  # only the current page is kept
        source = image if place["bbox"] is None else crop_panel(image, place["bbox"])
        fitted = fit_width(source, canvas_width, place["scale"])
        yield place_on_canvas(fitted, canvas_width, background), place["gutter_after"]
```

The single-entry cache matters: placements are page-ordered, so this reads each cleaned page once instead of once per panel. On a page with 8 panels that is 8× fewer decodes of a 2800px JPEG.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/ -q`
Expected: PASS — 38 tests. The existing assemble behaviour is unchanged.

- [ ] **Step 6: Verify against the real chapter**

Run:
```bash
python -m scrollstrip assemble --project ../../chapter-swamp01
```
Expected: `Wrote 85 slices` — the same count as before the refactor. If it differs, the extraction changed behaviour and must be fixed before committing.

- [ ] **Step 7: Commit**

```bash
git add scrollstrip/assemble.py tests/test_placements.py
git commit -m "refactor: split panel placement decisions from image compositing"
```

---

### Task 3: Library discovery

**Files:**
- Create: `scrollstrip/app/__init__.py` (empty)
- Create: `scrollstrip/app/library.py`
- Test: `tests/test_library.py`

**Interfaces:**
- Consumes: `scrollstrip.project.load_project`.
- Produces: `library_root() -> Path`; `log_dir() -> Path`; `list_chapters(root: Path, active_project_ids: set[str] | None = None) -> list[dict]`; `chapter_dir(root: Path, chapter_id: str) -> Path`. Each chapter dict has `id`, `name`, `page_count`, `panel_count`, `needs_review`, `status`, `updated`, and `error` (None unless `status == "damaged"`).

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_library.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrollstrip.app'`

- [ ] **Step 3: Implement**

```python
# scrollstrip/app/library.py
"""Chapter discovery.

The filesystem is the source of truth. A chapter is a directory holding a
project.json, and its metadata is read from that file. There is deliberately no
index: an index drifts the moment a folder is moved or deleted outside the app,
and reconciling it is a recurring source of bugs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def library_root() -> Path:
    override = os.environ.get("SCROLLSTRIP_LIBRARY")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Documents" / "Scrollstrip"


def log_dir() -> Path:
    path = library_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chapter_dir(root: Path, chapter_id: str) -> Path:
    resolved = (root / chapter_id).resolve()
    if resolved.parent != Path(root).resolve():
        raise ValueError(f"Invalid chapter id: {chapter_id}")
    return resolved


def _status(data: dict, chapter: Path, flagged: int) -> str:
    pages = data.get("pages") or []
    if not any(p.get("status") == "detected" for p in pages):
        return "new"
    if flagged:
        return "needs_review"
    cbz = sorted((chapter / "export").glob("*.cbz")) if (chapter / "export").is_dir() else []
    if cbz and cbz[0].stat().st_mtime >= (chapter / "project.json").stat().st_mtime:
        return "ready"
    return "reviewed"


def _read_chapter(chapter: Path) -> dict:
    manifest = chapter / "project.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {
            "id": chapter.name, "name": chapter.name, "page_count": 0,
            "panel_count": 0, "needs_review": 0, "status": "damaged",
            "updated": manifest.stat().st_mtime if manifest.exists() else 0,
            "error": f"Could not read project.json: {exc}",
        }
    pages = data.get("pages") or []
    flagged = sum(1 for p in pages if p.get("needs_review"))
    return {
        "id": chapter.name,
        "name": data.get("name") or chapter.name,
        "page_count": len(pages),
        "panel_count": sum(len(p.get("panels") or []) for p in pages),
        "needs_review": flagged,
        "status": _status(data, chapter, flagged),
        "updated": manifest.stat().st_mtime,
        "error": None,
    }


def list_chapters(root: Path, active_project_ids: set[str] | None = None) -> list[dict]:
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"Library root is not a directory: {root}")
    if not root.exists():
        return []
    active = active_project_ids or set()
    out = []
    for manifest in sorted(root.glob("*/project.json")):
        entry = _read_chapter(manifest.parent)
        if entry["id"] in active:
            entry["status"] = "processing"
        out.append(entry)
    return sorted(out, key=lambda c: c["updated"], reverse=True)
```

Create an empty `scrollstrip/app/__init__.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_library.py -v`
Expected: PASS — 10 tests

- [ ] **Step 5: Commit**

```bash
git add scrollstrip/app/__init__.py scrollstrip/app/library.py tests/test_library.py
git commit -m "feat: add library chapter discovery and status derivation"
```

---

### Task 4: Job queue and worker

**Files:**
- Create: `scrollstrip/app/jobs.py`
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `scrollstrip.errors.JobCancelled`.
- Produces: `class JobQueue` with `submit(kind: str, project_id: str, fn: Callable[[Callable, Callable], None]) -> str` returning a job id; `get(job_id) -> dict | None`; `all() -> list[dict]`; `cancel(job_id) -> bool`; `active_project_ids() -> set[str]`; `subscribe() -> queue.Queue` for SSE; `shutdown()`. `fn` receives `(progress, should_cancel)` and is expected to pass them into a core stage.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_jobs.py
from __future__ import annotations

import threading
import time

import pytest

from scrollstrip.app.jobs import JobQueue
from scrollstrip.errors import JobCancelled


@pytest.fixture
def q():
    queue = JobQueue()
    yield queue
    queue.shutdown()


def wait_for(queue, job_id, state, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = queue.get(job_id)
        if job and job["state"] == state:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never reached {state}: {queue.get(job_id)}")


def test_job_runs_and_reports_progress(q):
    def work(progress, should_cancel):
        for i in range(1, 4):
            progress(i, 3, f"step {i}")

    jid = q.submit("clean", "ch1", work)
    job = wait_for(q, jid, "done")
    assert job["done"] == 3 and job["total"] == 3


def test_jobs_run_in_fifo_order(q):
    order = []
    started = threading.Event()

    def first(progress, should_cancel):
        started.wait(2.0)
        order.append("first")

    def second(progress, should_cancel):
        order.append("second")

    a = q.submit("clean", "ch1", first)
    b = q.submit("clean", "ch2", second)
    started.set()
    wait_for(q, b, "done")
    assert order == ["first", "second"]


def test_failure_is_captured_and_does_not_kill_the_worker(q):
    def boom(progress, should_cancel):
        raise RuntimeError("detector exploded")

    failed = q.submit("detect", "ch1", boom)
    job = wait_for(q, failed, "failed")
    assert "detector exploded" in job["error"]

    ok = q.submit("clean", "ch2", lambda p, c: None)
    wait_for(q, ok, "done")


def test_cancel_while_queued_never_runs(q):
    release = threading.Event()
    ran = []
    q.submit("clean", "ch1", lambda p, c: release.wait(2.0))
    second = q.submit("clean", "ch2", lambda p, c: ran.append(1))
    assert q.cancel(second) is True
    release.set()
    wait_for(q, second, "cancelled")
    assert ran == []


def test_cancel_while_running_stops_at_next_check(q):
    def work(progress, should_cancel):
        for i in range(100):
            if should_cancel():
                raise JobCancelled("stopped")
            progress(i, 100, "")
            time.sleep(0.01)

    jid = q.submit("clean", "ch1", work)
    wait_for(q, jid, "running")
    q.cancel(jid)
    job = wait_for(q, jid, "cancelled")
    assert job["done"] < 100


def test_active_project_ids_reflects_running_work(q):
    release = threading.Event()
    jid = q.submit("clean", "ch1", lambda p, c: release.wait(2.0))
    wait_for(q, jid, "running")
    assert q.active_project_ids() == {"ch1"}
    release.set()
    wait_for(q, jid, "done")
    assert q.active_project_ids() == set()


def test_failure_writes_a_traceback_log_and_reports_its_path(q, tmp_path, monkeypatch):
    monkeypatch.setenv("SCROLLSTRIP_LIBRARY", str(tmp_path))

    def boom(progress, should_cancel):
        raise RuntimeError("detector exploded")

    jid = q.submit("detect", "ch1", boom)
    job = wait_for(q, jid, "failed")
    log = tmp_path / "logs"
    written = list(log.glob("scrollstrip-*.log"))
    assert written, "no traceback log written"
    assert "detector exploded" in written[0].read_text(encoding="utf-8")
    assert "Traceback" in written[0].read_text(encoding="utf-8")
    assert job["log"] == str(written[0])


# Review Focus #1
def test_missing_source_file_fails_readably_without_stalling_the_queue(q):
    def work(progress, should_cancel):
        raise FileNotFoundError("C:/gone/book.cbz")

    bad = q.submit("import", "ch1", work)
    job = wait_for(q, bad, "failed")
    assert "book.cbz" in job["error"]

    after = q.submit("clean", "ch2", lambda p, c: None)
    wait_for(q, after, "done")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_jobs.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrollstrip.app.jobs'`

- [ ] **Step 3: Implement**

```python
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

    _fns: dict[str, Callable] = {}
    _cancelled: set[str] = set()

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
```

Note: `_fns` and `_cancelled` must be instance attributes, not class attributes. Move them into `__init__` as `self._fns = {}` and `self._cancelled = set()` and delete the class-level declarations — class-level mutable state would be shared across every `JobQueue` instance and break test isolation.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_jobs.py -v`
Expected: PASS — 8 tests

- [ ] **Step 5: Commit**

```bash
git add scrollstrip/app/jobs.py tests/test_jobs.py
git commit -m "feat: add single-worker job queue with progress and cancellation"
```

---

### Task 5: Collision-safe project creation

**Files:**
- Modify: `scrollstrip/project.py:61` (`init_project`)
- Test: `tests/test_project_collision.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `unique_chapter_dir(root: Path, name: str) -> Path` in `scrollstrip/project.py`, returning a non-existent directory path derived from `name`, suffixed `-2`, `-3`, … on collision.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_project_collision.py
from __future__ import annotations

from scrollstrip.project import unique_chapter_dir


# Review Focus #2
def test_second_chapter_with_the_same_name_gets_its_own_directory(tmp_path):
    first = unique_chapter_dir(tmp_path, "Swamp Thing 01")
    first.mkdir()
    (first / "project.json").write_text("{}", encoding="utf-8")
    second = unique_chapter_dir(tmp_path, "Swamp Thing 01")
    assert second != first
    assert not second.exists()


def test_name_is_slugified_for_the_filesystem(tmp_path):
    got = unique_chapter_dir(tmp_path, "Swamp Thing: Quest / Part 1?")
    assert got.parent == tmp_path
    for ch in ':/?*"<>|':
        assert ch not in got.name


def test_blank_name_still_produces_a_directory(tmp_path):
    got = unique_chapter_dir(tmp_path, "   ")
    assert got.name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_project_collision.py -v`
Expected: FAIL — `ImportError: cannot import name 'unique_chapter_dir'`

- [ ] **Step 3: Implement**

Add to `scrollstrip/project.py`:

```python
import re

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def unique_chapter_dir(root: Path, name: str) -> Path:
    """A fresh directory for a new chapter.

    Two imports named the same must not land in the same folder: the second
    init_project would overwrite the first chapter's project.json and destroy
    every panel edit in it.
    """
    slug = _UNSAFE.sub("-", (name or "").strip()).strip(". -")
    slug = slug or "chapter"
    candidate = Path(root) / slug
    counter = 2
    while candidate.exists():
        candidate = Path(root) / f"{slug}-{counter}"
        counter += 1
    return candidate
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/ -q`
Expected: PASS — all green

- [ ] **Step 5: Commit**

```bash
git add scrollstrip/project.py tests/test_project_collision.py
git commit -m "feat: give each imported chapter a unique directory"
```

---

### Task 6: Media serving with resize cache

**Files:**
- Create: `scrollstrip/app/media.py`
- Test: `tests/test_media.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `resolve_media(project_dir: Path, rel: str) -> Path` (raises `ValueError` on escape); `cached_resize(project_dir: Path, rel: str, width: int | None) -> Path`; `MAX_WIDTH = 4096`, `MIN_WIDTH = 16`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_media.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_media.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrollstrip.app.media'`

- [ ] **Step 3: Implement**

```python
# scrollstrip/app/media.py
"""Serve project images, resized on demand.

Cleaned pages are up to 2800px and around 2MB. Handing originals to the editor
would make every page change feel broken, so requested widths are rendered once
and cached under work/cache/{width}/.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

MIN_WIDTH = 16
MAX_WIDTH = 4096


def resolve_media(project_dir: Path, rel: str) -> Path:
    project_dir = Path(project_dir).resolve()
    candidate = (project_dir / str(rel).replace("\\", "/")).resolve()
    try:
        candidate.relative_to(project_dir)
    except ValueError as exc:
        raise ValueError(f"Path escapes the project: {rel}") from exc
    return candidate


def cached_resize(project_dir: Path, rel: str, width: int | None) -> Path:
    source = resolve_media(project_dir, rel)
    if not source.exists():
        raise FileNotFoundError(source)
    if width is None or width <= 0:
        return source
    width = max(MIN_WIDTH, min(int(width), MAX_WIDTH))

    with Image.open(source) as probe:
        if probe.width <= width:
            return source

    target = Path(project_dir) / "work" / "cache" / str(width) / f"{rel.replace('/', '_')}"
    target = target.with_suffix(".jpg")
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = img.convert("RGB")
        height = max(1, round(img.height * width / img.width))
        img.resize((width, height), Image.LANCZOS).save(target, "JPEG", quality=88)
    return target
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_media.py -v`
Expected: PASS — 11 tests

- [ ] **Step 5: Commit**

```bash
git add scrollstrip/app/media.py tests/test_media.py
git commit -m "feat: add media serving with resize cache and traversal guard"
```

---

### Task 7: FastAPI server — library, import, jobs

**Files:**
- Create: `scrollstrip/app/errors.py`
- Create: `scrollstrip/app/server.py`
- Modify: `requirements.txt`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `list_chapters`, `chapter_dir`, `library_root` (Task 3); `JobQueue` (Task 4); `unique_chapter_dir` (Task 5); `cached_resize`, `resolve_media` (Task 6).
- Produces: `create_app(root: Path | None = None, jobs: JobQueue | None = None) -> FastAPI`. The app exposes `app.state.jobs` and `app.state.root`.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
fastapi>=0.115
uvicorn>=0.32
pywebview>=5.3
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

```python
# tests/test_api.py
from __future__ import annotations

import io
import json
import time
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from scrollstrip.app.server import create_app


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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scrollstrip.app.server'`

- [ ] **Step 4: Implement the error mapping**

```python
# scrollstrip/app/errors.py
"""Map core exceptions onto HTTP responses the UI can show a person."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..errors import JobCancelled
from ..ingest import IngestError

HINTS = {
    "IngestError": "Check the file is a folder, .cbz/.zip or .pdf that you can open.",
    "FileNotFoundError": "The file may have been moved or deleted since you chose it.",
    "NotADirectoryError": "Set SCROLLSTRIP_LIBRARY to a folder you can write to.",
    "PermissionError": "Close anything using the file, or pick a different folder.",
}

STATUS = {
    "IngestError": 400,
    "ValueError": 400,
    "FileNotFoundError": 404,
    "NotADirectoryError": 500,
    "PermissionError": 500,
    "JobCancelled": 409,
}


def payload(exc: Exception) -> tuple[int, dict]:
    name = type(exc).__name__
    hint = HINTS.get(name, "")
    if isinstance(exc, IngestError) and "cbz" in str(exc).lower():
        hint = str(exc)
    return STATUS.get(name, 500), {"error": name, "message": str(exc), "hint": hint}


def install(app) -> None:
    @app.exception_handler(IngestError)
    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(NotADirectoryError)
    @app.exception_handler(PermissionError)
    @app.exception_handler(ValueError)
    @app.exception_handler(JobCancelled)
    async def handle(request: Request, exc: Exception):  # noqa: ANN001
        status, body = payload(exc)
        return JSONResponse(status_code=status, content=body)
```

- [ ] **Step 5: Implement the server**

```python
# scrollstrip/app/server.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..clean import clean_project
from ..detect import detect_project
from ..ingest import describe_source
from ..project import init_project, merged_config, unique_chapter_dir
from . import errors as app_errors
from .jobs import JobQueue
from .library import chapter_dir, library_root, list_chapters
from .media import cached_resize


class ImportRequest(BaseModel):
    source: str
    name: str | None = None
    engine: str | None = None
    width: int | None = None


def create_app(root: Path | None = None, jobs: JobQueue | None = None) -> FastAPI:
    app = FastAPI(title="Scrollstrip")
    app.state.root = Path(root) if root else library_root()
    app.state.jobs = jobs or JobQueue()
    app.state.root.mkdir(parents=True, exist_ok=True)
    app_errors.install(app)

    @app.get("/api/library")
    def get_library():
        active = app.state.jobs.active_project_ids()
        chapters = list_chapters(app.state.root, active_project_ids=active)
        known = {c["id"] for c in chapters}
        # A chapter being ingested has no project.json yet. Showing nothing at the
        # moment the user is watching for it is the worst possible behaviour, so
        # in-flight imports are merged in.
        pending = [
            {
                "id": j["project_id"], "name": j.get("message") or j["project_id"],
                "page_count": 0, "panel_count": 0, "needs_review": 0,
                "status": "processing", "updated": j["created"], "error": None,
            }
            for j in app.state.jobs.all()
            if j["kind"] == "import" and j["state"] in {"queued", "running"}
            and j["project_id"] not in known
        ]
        return pending + chapters

    @app.post("/api/library/import")
    def post_import(req: ImportRequest):
        source = Path(req.source).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"Source does not exist: {source}")
        if describe_source(source) == "unsupported":
            from ..ingest import IngestError
            raise IngestError(
                f"Unsupported source '{source.name}'. Convert it to .cbz first, "
                "or use a folder of images or a .pdf."
            )
        name = req.name or source.stem
        target = unique_chapter_dir(app.state.root, name)
        project_id = target.name

        def work(progress, should_cancel):
            progress(0, 3, f"Importing {name}")
            init_project(target, pages_dir=source, name=name)
            cfg = merged_config(target)
            if req.width:
                cfg["canvas_width"] = int(req.width)
            if req.engine:
                cfg.setdefault("detect", {})["engine"] = req.engine
            clean_project(target, cfg, progress=progress, should_cancel=should_cancel)
            detect_project(target, cfg, progress=progress, should_cancel=should_cancel)

        job_id = app.state.jobs.submit("import", project_id, work)
        return {"job_id": job_id, "project_id": project_id}

    @app.post("/api/library/rescan")
    def rescan():
        return {"ok": True}

    @app.get("/api/jobs")
    def get_jobs():
        return app.state.jobs.all()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return {"cancelled": app.state.jobs.cancel(job_id)}

    @app.get("/api/events")
    async def events():
        sub = app.state.jobs.subscribe()

        async def stream():
            loop = asyncio.get_event_loop()
            while True:
                job = await loop.run_in_executor(None, sub.get)
                yield f"data: {json.dumps(job)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/media/{chapter_id}/{path:path}")
    def media(chapter_id: str, path: str, w: int | None = Query(default=None)):
        project = chapter_dir(app.state.root, chapter_id)
        return FileResponse(cached_resize(project, path, w))

    return app
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_api.py -v`
Expected: PASS — 7 tests

- [ ] **Step 7: Run the whole suite**

Run: `pytest tests/ -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add scrollstrip/app/errors.py scrollstrip/app/server.py requirements.txt tests/test_api.py
git commit -m "feat: add FastAPI server with library, import, jobs and media endpoints"
```

---

### Task 8: Project and page endpoints

**Files:**
- Modify: `scrollstrip/app/server.py`
- Test: `tests/test_api_project.py`

**Interfaces:**
- Consumes: Task 7's `create_app`.
- Produces: `GET /api/project/{id}`, `PUT /api/project/{id}/page/{page_id}`, `POST /api/project/{id}/detect`, `POST /api/project/{id}/assemble`, `GET|PUT /api/project/{id}/config`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_api_project.py -v`
Expected: FAIL — 404 on `/api/project/ch1`

- [ ] **Step 3: Implement**

Add to `create_app` in `scrollstrip/app/server.py`, before `return app`:

```python
    def _require_idle(project_id: str) -> None:
        # A running stage holds the project dict in memory and saves it when it
        # finishes, which would silently discard edits made in the meantime.
        if project_id in app.state.jobs.active_project_ids():
            raise ProjectBusy(f"A job is running on {project_id}; try again when it finishes.")

    @app.get("/api/project/{chapter_id}")
    def get_project(chapter_id: str):
        return load_project(chapter_dir(app.state.root, chapter_id))

    @app.put("/api/project/{chapter_id}/page/{page_id}")
    def put_page(chapter_id: str, page_id: str, body: dict):
        _require_idle(chapter_id)
        project_dir = chapter_dir(app.state.root, chapter_id)
        project = load_project(project_dir)
        for page in project["pages"]:
            if page["id"] == page_id:
                if "panels" in body:
                    page["panels"] = body["panels"]
                if "kind" in body:
                    page["kind"] = body["kind"]
                if "needs_review" in body:
                    page["needs_review"] = bool(body["needs_review"])
                page["status"] = "reviewed"
                save_project(project_dir, project)
                return page
        raise FileNotFoundError(f"No page {page_id} in {chapter_id}")

    @app.post("/api/project/{chapter_id}/detect")
    def post_detect(chapter_id: str, body: dict | None = None):
        _require_idle(chapter_id)
        project_dir = chapter_dir(app.state.root, chapter_id)
        keep_edits = bool((body or {}).get("keep_edits", False))

        def work(progress, should_cancel):
            cfg = merged_config(project_dir)
            detect_project(project_dir, cfg, overwrite_unlocked=not keep_edits,
                           progress=progress, should_cancel=should_cancel)

        return {"job_id": app.state.jobs.submit("detect", chapter_id, work)}

    @app.post("/api/project/{chapter_id}/assemble")
    def post_assemble(chapter_id: str):
        _require_idle(chapter_id)
        project_dir = chapter_dir(app.state.root, chapter_id)

        def work(progress, should_cancel):
            cfg = merged_config(project_dir)
            assemble_project(project_dir, cfg, progress=progress, should_cancel=should_cancel)

        return {"job_id": app.state.jobs.submit("assemble", chapter_id, work)}

    @app.get("/api/project/{chapter_id}/config")
    def get_config(chapter_id: str):
        return merged_config(chapter_dir(app.state.root, chapter_id))

    @app.put("/api/project/{chapter_id}/config")
    def put_config(chapter_id: str, body: dict):
        _require_idle(chapter_id)
        project_dir = chapter_dir(app.state.root, chapter_id)
        write_yaml(project_dir / "config.yaml", body)
        return merged_config(project_dir)
```

Add these imports to the top of `server.py`:

```python
from ..assemble import assemble_project
from ..config import write_yaml
from ..project import load_project, save_project
```

Add to `scrollstrip/app/errors.py`:

```python
class ProjectBusy(Exception):
    """An edit arrived while a job holds that project."""
```

Register it: add `"ProjectBusy": 409` to `STATUS`, `"ProjectBusy": "Wait for the running job to finish, or cancel it."` to `HINTS`, and add `@app.exception_handler(ProjectBusy)` to the stack in `install`. Import `ProjectBusy` into `server.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_api_project.py -v`
Expected: PASS — 5 tests

- [ ] **Step 5: Commit**

```bash
git add scrollstrip/app/server.py scrollstrip/app/errors.py tests/test_api_project.py
git commit -m "feat: add project and page endpoints with busy-project guard"
```

---

### Task 9: Preview manifest with export parity

**Files:**
- Modify: `scrollstrip/app/server.py`
- Test: `tests/test_preview_parity.py`

**Interfaces:**
- Consumes: `iter_panel_placements` (Task 2), `create_app` (Task 7).
- Produces: `GET /api/project/{id}/preview` returning `{canvas_width: int, background: [int,int,int], panels: [{page_id, src, bbox, scale, gutter_after}]}` where `src` is a `/media/...` URL.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preview_parity.py -v`
Expected: FAIL — 404 on `/api/project/ch1/preview`

- [ ] **Step 3: Implement**

Add to `create_app`:

```python
    @app.get("/api/project/{chapter_id}/preview")
    def get_preview(chapter_id: str):
        project_dir = chapter_dir(app.state.root, chapter_id)
        project = load_project(project_dir)
        cfg = merged_config(project_dir, project)
        panels = [
            {
                "page_id": place["page_id"],
                "src": f"/media/{chapter_id}/{place['cleaned']}",
                "bbox": place["bbox"],
                "scale": place["scale"],
                "gutter_after": place["gutter_after"],
            }
            for place in iter_panel_placements(project, cfg)
        ]
        return {
            "canvas_width": int(cfg.get("canvas_width", 1080)),
            "background": [int(c) for c in cfg.get("background", [18, 18, 18])],
            "panels": panels,
        }
```

Add `from ..assemble import iter_panel_placements` to the imports.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_preview_parity.py -v`
Expected: PASS — 4 tests

- [ ] **Step 5: Commit**

```bash
git add scrollstrip/app/server.py tests/test_preview_parity.py
git commit -m "feat: add preview manifest sharing placement logic with export"
```

---

### Task 10: Frontend scaffold and pywebview shell

**Files:**
- Create: `ui/package.json`, `ui/vite.config.js`, `ui/index.html`, `ui/src/main.js`, `ui/src/App.svelte`
- Create: `ui/src/lib/api.js`, `ui/src/lib/stores.js`
- Create: `scrollstrip/shell.py`
- Modify: `scrollstrip/app/server.py` (serve built frontend)
- Modify: `.gitignore` (already covers `node_modules/` and `dist/`)

**Interfaces:**
- Consumes: every endpoint from Tasks 7–9.
- Produces: `ui/src/lib/api.js` exporting `getLibrary()`, `importSource(body)`, `getProject(id)`, `putPage(id, pageId, body)`, `postDetect(id, keepEdits)`, `postAssemble(id)`, `getPreview(id)`, `getJobs()`, `cancelJob(jobId)`, `subscribeJobs(onJob)`. `scrollstrip/shell.py` exposing `main()`.

- [ ] **Step 1: Scaffold the frontend**

```bash
cd ui
npm create vite@latest . -- --template svelte
npm install
```

- [ ] **Step 2: Point Vite at the API and build into the package**

```javascript
// ui/vite.config.js
import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  build: { outDir: '../scrollstrip/web_dist', emptyOutDir: true },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/media': 'http://127.0.0.1:8765',
    },
  },
})
```

Add `scrollstrip/web_dist/` to `.gitignore`.

- [ ] **Step 3: Write the API client**

```javascript
// ui/src/lib/api.js
async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  if (!res.ok) {
    const problem = await res.json().catch(() => ({
      error: 'HTTPError', message: `${res.status} ${res.statusText}`, hint: '',
    }))
    throw Object.assign(new Error(problem.message), problem)
  }
  return res.status === 204 ? null : res.json()
}

export const getLibrary = () => request('/api/library')
export const importSource = (body) => request('/api/library/import', { method: 'POST', body })
export const getProject = (id) => request(`/api/project/${encodeURIComponent(id)}`)
export const putPage = (id, pageId, body) =>
  request(`/api/project/${encodeURIComponent(id)}/page/${encodeURIComponent(pageId)}`,
          { method: 'PUT', body })
export const postDetect = (id, keepEdits = false) =>
  request(`/api/project/${encodeURIComponent(id)}/detect`, { method: 'POST', body: { keep_edits: keepEdits } })
export const postAssemble = (id) =>
  request(`/api/project/${encodeURIComponent(id)}/assemble`, { method: 'POST' })
export const getPreview = (id) => request(`/api/project/${encodeURIComponent(id)}/preview`)
export const getJobs = () => request('/api/jobs')
export const cancelJob = (jobId) => request(`/api/jobs/${jobId}/cancel`, { method: 'POST' })

export function subscribeJobs(onJob) {
  const source = new EventSource('/api/events')
  source.onmessage = (event) => onJob(JSON.parse(event.data))
  return () => source.close()
}
```

- [ ] **Step 4: Write the stores**

```javascript
// ui/src/lib/stores.js
import { writable, derived } from 'svelte/store'
import { getLibrary, getJobs, subscribeJobs } from './api.js'

export const chapters = writable([])
export const jobs = writable([])
export const toast = writable(null)

export async function refreshLibrary() {
  chapters.set(await getLibrary())
}

export function showError(err) {
  toast.set({ message: err.message, hint: err.hint || '' })
  setTimeout(() => toast.set(null), 6000)
}

export async function startJobStream() {
  jobs.set(await getJobs())
  return subscribeJobs((job) => {
    jobs.update((list) => {
      const next = list.filter((j) => j.id !== job.id)
      next.push(job)
      return next
    })
    if (['done', 'failed', 'cancelled'].includes(job.state)) refreshLibrary()
    // A job fails on the worker thread, so nothing else would ever tell the user.
    if (job.state === 'failed') {
      toast.set({
        message: job.error || `${job.kind} failed`,
        hint: job.log ? `Details written to ${job.log}` : '',
      })
      setTimeout(() => toast.set(null), 12000)
    }
  })
}

export const activeJobsByProject = derived(jobs, ($jobs) => {
  const map = {}
  for (const job of $jobs) {
    if (job.state === 'queued' || job.state === 'running') map[job.project_id] = job
  }
  return map
})
```

- [ ] **Step 5: Serve the built frontend from FastAPI**

Add to the end of `create_app`, immediately before `return app`:

```python
    dist = Path(__file__).resolve().parent.parent / "web_dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
```

Add `from fastapi.staticfiles import StaticFiles` to the imports. This mount must come last: mounting `/` first would shadow every API route.

- [ ] **Step 6: Write the shell**

```python
# scrollstrip/shell.py
"""Desktop entry point: serve the app and open it in a native window."""

from __future__ import annotations

import threading

import uvicorn

from .app.server import create_app

HOST, PORT = "127.0.0.1", 8765


def main() -> None:
    app = create_app()
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()

    try:
        import webview
    except ImportError:
        print(f"pywebview is not installed. Open http://{HOST}:{PORT}/ in a browser.")
        server.run()
        return

    webview.create_window("Scrollstrip", f"http://{HOST}:{PORT}/", width=1400, height=900)
    try:
        webview.start()
    except Exception as exc:  # noqa: BLE001 - most often a missing WebView2 runtime
        print(f"Could not open a native window: {exc}")
        print("Install the Microsoft Edge WebView2 runtime, or open "
              f"http://{HOST}:{PORT}/ in a browser.")
    finally:
        app.state.jobs.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: Verify the shell starts**

Run: `python -m scrollstrip.shell`
Expected: a window titled "Scrollstrip" opens. With no frontend built yet it will show a 404 — that is correct at this step.

- [ ] **Step 8: Commit**

```bash
git add ui/ scrollstrip/shell.py scrollstrip/app/server.py .gitignore
git commit -m "feat: add Svelte frontend scaffold and pywebview shell"
```

---

### Task 11: Library and Import screens

**Files:**
- Create: `ui/src/routes/Library.svelte`, `ui/src/routes/Import.svelte`
- Modify: `ui/src/App.svelte`
- Modify: `scrollstrip/app/server.py` (native folder/file dialog endpoint)

**Interfaces:**
- Consumes: `api.js`, `stores.js` (Task 10).
- Produces: `App.svelte` exporting a `view` store with values `"library" | "import" | "editor" | "reader"` and `activeChapterId`.

- [ ] **Step 1: Add the native dialog endpoint**

A browser file input cannot return a folder path, and folders are a first-class source. Add to `create_app`:

```python
    @app.post("/api/dialog/open")
    def open_dialog(body: dict | None = None):
        """Native picker. Returns {path: str | None}."""
        try:
            import webview
        except ImportError:
            return {"path": None, "unavailable": True}
        windows = webview.windows
        if not windows:
            return {"path": None, "unavailable": True}
        folders = bool((body or {}).get("folder"))
        result = windows[0].create_file_dialog(
            webview.FOLDER_DIALOG if folders else webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=() if folders else ("Comics (*.cbz;*.zip;*.pdf)", "All files (*.*)"),
        )
        return {"path": result[0] if result else None}
```

- [ ] **Step 2: Write the Library screen**

```svelte
<!-- ui/src/routes/Library.svelte -->
<script>
  import { onMount } from 'svelte'
  import { chapters, activeJobsByProject, refreshLibrary, showError } from '../lib/stores.js'
  export let onOpen, onImport

  onMount(() => { refreshLibrary().catch(showError) })

  const LABEL = {
    processing: 'Processing', new: 'Not detected', needs_review: 'Needs review',
    reviewed: 'Reviewed', ready: 'Ready', damaged: 'Damaged',
  }
</script>

<header>
  <h1>Library</h1>
  <button on:click={onImport}>Import comic</button>
  <button on:click={() => refreshLibrary().catch(showError)}>Rescan</button>
</header>

{#if $chapters.length === 0}
  <p class="empty">
    No comics yet. Import a folder of scans, a CBZ, or a PDF of a comic you own.
  </p>
{:else}
  <ul class="grid">
    {#each $chapters as chapter (chapter.id)}
      {@const job = $activeJobsByProject[chapter.id]}
      <li class:damaged={chapter.status === 'damaged'}>
        <button on:click={() => chapter.status !== 'damaged' && onOpen(chapter.id)}>
          <img src={`/media/${chapter.id}/work/cleaned/${chapter.id}.jpg?w=200`} alt="" />
          <strong>{chapter.name}</strong>
          <span class="badge">{LABEL[chapter.status] ?? chapter.status}</span>
          {#if chapter.page_count}
            <span>{chapter.page_count} pages · {chapter.panel_count} panels</span>
          {/if}
          {#if chapter.needs_review}
            <span>{chapter.needs_review} to check</span>
          {/if}
          {#if job}
            <progress value={job.done} max={job.total || 1}></progress>
            <span>{job.message}</span>
          {/if}
          {#if chapter.error}<span class="error">{chapter.error}</span>{/if}
        </button>
      </li>
    {/each}
  </ul>
{/if}
```

- [ ] **Step 3: Write the Import screen**

```svelte
<!-- ui/src/routes/Import.svelte -->
<script>
  import { importSource } from '../lib/api.js'
  import { refreshLibrary, showError } from '../lib/stores.js'
  export let onDone

  let queued = []
  let source = '', name = '', engine = 'auto', width = 1080

  async function pick(folder) {
    const res = await fetch('/api/dialog/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    }).then((r) => r.json())
    if (res.path) {
      source = res.path
      if (!name) name = res.path.split(/[\\/]/).pop().replace(/\.[^.]+$/, '')
    }
  }

  function add() {
    if (!source) return
    queued = [...queued, { source, name: name || source, engine, width }]
    source = ''; name = ''
  }

  async function start() {
    const items = source ? [...queued, { source, name: name || source, engine, width }] : queued
    for (const item of items) {
      try { await importSource(item) } catch (err) { showError(err) }
    }
    await refreshLibrary()
    onDone()
  }
</script>

<h1>Import</h1>
<button on:click={() => pick(false)}>Choose CBZ or PDF…</button>
<button on:click={() => pick(true)}>Choose folder…</button>
<input bind:value={source} placeholder="Source path" />
<input bind:value={name} placeholder="Chapter name" />
<select bind:value={engine}>
  <option value="auto">Detection: automatic</option>
  <option value="yolo">YOLO only</option>
  <option value="cv">OpenCV only</option>
</select>
<input type="number" bind:value={width} min="480" max="2160" />

<button on:click={add} disabled={!source}>Add another</button>
<button on:click={start} disabled={!source && queued.length === 0}>Start</button>

{#if queued.length}
  <ol>{#each queued as item}<li>{item.name}</li>{/each}</ol>
  <p>These run one after another.</p>
{/if}
```

- [ ] **Step 4: Wire App.svelte**

```svelte
<!-- ui/src/App.svelte -->
<script>
  import { onMount } from 'svelte'
  import Library from './routes/Library.svelte'
  import Import from './routes/Import.svelte'
  import { startJobStream, toast } from './lib/stores.js'

  let view = 'library'
  let activeChapterId = null
  let unsubscribe

  onMount(async () => { unsubscribe = await startJobStream() })
  $: if (!view) view = 'library'
</script>

{#if view === 'library'}
  <Library onOpen={(id) => { activeChapterId = id; view = 'editor' }}
           onImport={() => (view = 'import')} />
{:else if view === 'import'}
  <Import onDone={() => (view = 'library')} />
{/if}

{#if $toast}
  <div class="toast"><strong>{$toast.message}</strong>{#if $toast.hint}<span>{$toast.hint}</span>{/if}</div>
{/if}
```

- [ ] **Step 5: Verify end to end**

Run `npm run build` in `ui/`, then `python -m scrollstrip.shell`. Import a CBZ. Expected: the chapter appears immediately with "Processing" and a progress bar, then flips to "Needs review" when detection finishes.

- [ ] **Step 6: Commit**

```bash
git add ui/src scrollstrip/app/server.py
git commit -m "feat: add library and import screens"
```

**Phase 1 is complete here.** The app imports, processes with visible progress, and lists chapters without the terminal.

---

## Phase 2 — Editor

### Task 12: Canvas coordinate math

The only frontend logic worth isolating. Image space to canvas space and back, under zoom and pan, is pure and is where off-by-one bugs live.

**Files:**
- Create: `ui/src/lib/geometry.js`
- Create: `ui/src/lib/geometry.test.js`
- Modify: `ui/package.json` (add vitest)

**Interfaces:**
- Produces: `fitScale(imageW, imageH, viewW, viewH) -> number`; `toCanvas(box, view) -> {x,y,w,h}`; `toImage(rect, view) -> [x,y,w,h]`; `hitTest(point, boxes, view) -> {index, handle} | null` where `handle` is `'move' | 'nw' | 'ne' | 'sw' | 'se'`; `clampBox(box, imageW, imageH) -> [x,y,w,h]`. `view` is `{scale, offsetX, offsetY}`.

- [ ] **Step 1: Add vitest**

```bash
cd ui && npm install -D vitest
```

Add to `ui/package.json` scripts: `"test": "vitest run"`.

- [ ] **Step 2: Write the failing test**

```javascript
// ui/src/lib/geometry.test.js
import { describe, it, expect } from 'vitest'
import { fitScale, toCanvas, toImage, hitTest, clampBox } from './geometry.js'

const view = { scale: 0.5, offsetX: 10, offsetY: 20 }

describe('fitScale', () => {
  it('fits by the tighter axis', () => {
    expect(fitScale(1000, 2000, 500, 500)).toBe(0.25)
    expect(fitScale(2000, 1000, 500, 500)).toBe(0.25)
  })
  it('never upscales past 1', () => {
    expect(fitScale(100, 100, 500, 500)).toBe(1)
  })
})

describe('round trip', () => {
  it('toImage undoes toCanvas', () => {
    const box = [100, 200, 300, 400]
    const rect = toCanvas(box, view)
    expect(toImage(rect, view)).toEqual(box)
  })
  it('places the box using scale and offset', () => {
    expect(toCanvas([100, 200, 300, 400], view)).toEqual({ x: 60, y: 120, w: 150, h: 200 })
  })
})

describe('hitTest', () => {
  const boxes = [[0, 0, 200, 200], [400, 400, 200, 200]]
  it('finds a corner handle before the body', () => {
    const corner = toCanvas([0, 0, 200, 200], view)
    expect(hitTest({ x: corner.x + corner.w, y: corner.y + corner.h }, boxes, view))
      .toEqual({ index: 0, handle: 'se' })
  })
  it('finds the body', () => {
    expect(hitTest({ x: 60, y: 70 }, boxes, view)).toEqual({ index: 0, handle: 'move' })
  })
  it('returns null on empty space', () => {
    expect(hitTest({ x: 5, y: 5 }, boxes, view)).toBeNull()
  })
  it('prefers the topmost box when they overlap', () => {
    const stacked = [[0, 0, 400, 400], [0, 0, 200, 200]]
    expect(hitTest({ x: 60, y: 70 }, stacked, view).index).toBe(1)
  })
})

describe('clampBox', () => {
  it('keeps the box inside the image', () => {
    expect(clampBox([-10, -10, 50, 50], 100, 100)).toEqual([0, 0, 40, 40])
    expect(clampBox([80, 80, 50, 50], 100, 100)).toEqual([80, 80, 20, 20])
  })
  it('enforces a minimum size', () => {
    expect(clampBox([10, 10, 1, 1], 100, 100)).toEqual([10, 10, 8, 8])
  })
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ui && npm test`
Expected: FAIL — cannot resolve `./geometry.js`

- [ ] **Step 4: Implement**

```javascript
// ui/src/lib/geometry.js
const HANDLE = 10   // px, corner grab radius in canvas space
const MIN = 8       // px, smallest box in image space

export function fitScale(imageW, imageH, viewW, viewH) {
  return Math.min(1, viewW / imageW, viewH / imageH)
}

export function toCanvas([x, y, w, h], view) {
  return {
    x: x * view.scale + view.offsetX,
    y: y * view.scale + view.offsetY,
    w: w * view.scale,
    h: h * view.scale,
  }
}

export function toImage(rect, view) {
  return [
    Math.round((rect.x - view.offsetX) / view.scale),
    Math.round((rect.y - view.offsetY) / view.scale),
    Math.round(rect.w / view.scale),
    Math.round(rect.h / view.scale),
  ]
}

export function clampBox([x, y, w, h], imageW, imageH) {
  x = Math.max(0, Math.min(Math.round(x), imageW - MIN))
  y = Math.max(0, Math.min(Math.round(y), imageH - MIN))
  w = Math.max(MIN, Math.min(Math.round(w), imageW - x))
  h = Math.max(MIN, Math.min(Math.round(h), imageH - y))
  return [x, y, w, h]
}

export function hitTest(point, boxes, view) {
  // Topmost first, so the box drawn last wins an overlap.
  for (let i = boxes.length - 1; i >= 0; i -= 1) {
    const r = toCanvas(boxes[i], view)
    const corners = {
      nw: [r.x, r.y], ne: [r.x + r.w, r.y],
      sw: [r.x, r.y + r.h], se: [r.x + r.w, r.y + r.h],
    }
    for (const [name, [cx, cy]] of Object.entries(corners)) {
      if (Math.abs(point.x - cx) <= HANDLE && Math.abs(point.y - cy) <= HANDLE) {
        return { index: i, handle: name }
      }
    }
    if (point.x >= r.x && point.x <= r.x + r.w && point.y >= r.y && point.y <= r.y + r.h) {
      return { index: i, handle: 'move' }
    }
  }
  return null
}
```

- [ ] **Step 5: Run tests**

Run: `cd ui && npm test`
Expected: PASS — 10 tests

- [ ] **Step 6: Commit**

```bash
git add ui/src/lib/geometry.js ui/src/lib/geometry.test.js ui/package.json ui/package-lock.json
git commit -m "feat: add canvas coordinate math with tests"
```

---

### Task 13: Panel canvas component

**Files:**
- Create: `ui/src/lib/PanelCanvas.svelte`

**Interfaces:**
- Consumes: `geometry.js` (Task 12).
- Produces: a component with props `src`, `imageW`, `imageH`, `panels`, `selectedIndex`, and events `change` (panels array), `select` (index).

- [ ] **Step 1: Implement the component**

```svelte
<!-- ui/src/lib/PanelCanvas.svelte -->
<script>
  import { createEventDispatcher, onMount } from 'svelte'
  import { fitScale, toCanvas, toImage, hitTest, clampBox } from './geometry.js'

  export let src, imageW, imageH, panels = [], selectedIndex = -1

  const dispatch = createEventDispatcher()
  let canvas, image, box, drag = null
  let view = { scale: 1, offsetX: 0, offsetY: 0 }

  const ROLE_COLOR = { normal: '#3cdc5a', reaction: '#ffb450', splash: '#ff5050' }

  onMount(() => {
    image = new Image()
    image.onload = () => { resize(); draw() }
    image.src = src
  })
  $: if (image && src) { image.src = src }

  function resize() {
    if (!canvas || !box) return
    canvas.width = box.clientWidth
    canvas.height = box.clientHeight
    const scale = fitScale(imageW, imageH, canvas.width, canvas.height)
    view = {
      scale,
      offsetX: (canvas.width - imageW * scale) / 2,
      offsetY: (canvas.height - imageH * scale) / 2,
    }
  }

  function draw() {
    if (!canvas || !image) return
    const ctx = canvas.getContext('2d')
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    const r = toCanvas([0, 0, imageW, imageH], view)
    ctx.drawImage(image, r.x, r.y, r.w, r.h)
    panels.forEach((panel, i) => {
      const box2 = toCanvas(panel.bbox, view)
      ctx.lineWidth = i === selectedIndex ? 4 : 2
      ctx.strokeStyle = ROLE_COLOR[panel.role] ?? ROLE_COLOR.normal
      ctx.strokeRect(box2.x, box2.y, box2.w, box2.h)
      // Reading order is the failure a reader notices first, so always show it.
      ctx.fillStyle = ctx.strokeStyle
      ctx.font = 'bold 18px sans-serif'
      ctx.fillText(String(i + 1), box2.x + 6, box2.y + 22)
      if (i === selectedIndex) {
        for (const [hx, hy] of [[box2.x, box2.y], [box2.x + box2.w, box2.y],
                                [box2.x, box2.y + box2.h], [box2.x + box2.w, box2.y + box2.h]]) {
          ctx.fillStyle = '#fff'
          ctx.fillRect(hx - 5, hy - 5, 10, 10)
        }
      }
    })
  }
  $: panels, selectedIndex, draw()

  function pointerPos(event) {
    const rect = canvas.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  function down(event) {
    const point = pointerPos(event)
    const hit = hitTest(point, panels.map((p) => p.bbox), view)
    if (hit) {
      dispatch('select', hit.index)
      drag = { ...hit, start: point, original: [...panels[hit.index].bbox] }
    } else {
      drag = { index: -1, handle: 'new', start: point, original: null }
    }
  }

  function move(event) {
    if (!drag) return
    const point = pointerPos(event)
    if (drag.handle === 'new') { draw(); return }
    const dx = (point.x - drag.start.x) / view.scale
    const dy = (point.y - drag.start.y) / view.scale
    let [x, y, w, h] = drag.original
    if (drag.handle === 'move') { x += dx; y += dy }
    if (drag.handle === 'se') { w += dx; h += dy }
    if (drag.handle === 'nw') { x += dx; y += dy; w -= dx; h -= dy }
    if (drag.handle === 'ne') { y += dy; w += dx; h -= dy }
    if (drag.handle === 'sw') { x += dx; w -= dx; h += dy }
    const next = [...panels]
    next[drag.index] = { ...next[drag.index], bbox: clampBox([x, y, w, h], imageW, imageH) }
    panels = next
  }

  function up(event) {
    if (!drag) return
    if (drag.handle === 'new') {
      const point = pointerPos(event)
      const rect = {
        x: Math.min(drag.start.x, point.x), y: Math.min(drag.start.y, point.y),
        w: Math.abs(point.x - drag.start.x), h: Math.abs(point.y - drag.start.y),
      }
      if (rect.w > 12 && rect.h > 12) {
        const bbox = clampBox(toImage(rect, view), imageW, imageH)
        panels = [...panels, {
          id: `new-${Date.now()}`, bbox, order: panels.length, role: 'normal',
          scale: 1.0, gutter_after: 'medium', locked: false, score: 1.0,
        }]
        dispatch('select', panels.length - 1)
      }
    }
    drag = null
    dispatch('change', panels)
  }
</script>

<svelte:window on:resize={() => { resize(); draw() }} />
<div bind:this={box} class="canvas-box">
  <canvas bind:this={canvas} on:pointerdown={down} on:pointermove={move} on:pointerup={up}></canvas>
</div>

<style>
  .canvas-box { width: 100%; height: 100%; }
  canvas { display: block; touch-action: none; cursor: crosshair; }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/lib/PanelCanvas.svelte
git commit -m "feat: add panel canvas with drag, resize and create"
```

---

### Task 14: Editor screen

**Files:**
- Create: `ui/src/routes/Editor.svelte`
- Modify: `ui/src/App.svelte`

**Interfaces:**
- Consumes: `PanelCanvas.svelte` (Task 13), `api.js` (Task 10).

- [ ] **Step 1: Implement**

```svelte
<!-- ui/src/routes/Editor.svelte -->
<script>
  import { onMount } from 'svelte'
  import PanelCanvas from '../lib/PanelCanvas.svelte'
  import { getProject, putPage, postDetect, postAssemble } from '../lib/api.js'
  import { showError } from '../lib/stores.js'

  export let chapterId, onBack, onRead

  let project = null, pageIndex = 0, selectedIndex = -1, saveTimer

  $: page = project?.pages?.[pageIndex] ?? null
  $: selected = page?.panels?.[selectedIndex] ?? null

  onMount(async () => {
    try {
      project = await getProject(chapterId)
      // Open on the first page detection was unsure about, not always page 1.
      const flagged = project.pages.findIndex((p) => p.needs_review)
      pageIndex = flagged >= 0 ? flagged : 0
    } catch (err) { showError(err) }
  })

  function queueSave() {
    clearTimeout(saveTimer)
    saveTimer = setTimeout(save, 600)
  }

  async function save() {
    if (!page) return
    try {
      await putPage(chapterId, page.id, {
        panels: page.panels, kind: page.kind, needs_review: false,
      })
      page.needs_review = false
      project = project
    } catch (err) { showError(err) }
  }

  function onChange(event) {
    page.panels = event.detail.map((panel, i) => ({ ...panel, order: i }))
    project = project
    queueSave()
  }

  function updateSelected(patch) {
    if (!selected) return
    page.panels[selectedIndex] = { ...selected, ...patch }
    project = project
    queueSave()
  }

  function deleteSelected() {
    if (selectedIndex < 0) return
    page.panels = page.panels.filter((_, i) => i !== selectedIndex).map((p, i) => ({ ...p, order: i }))
    selectedIndex = -1
    project = project
    queueSave()
  }

  const GUTTERS = ['tight', 'medium', 'large']

  function onKey(event) {
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'SELECT') return
    if (event.key === 'Delete') { deleteSelected(); event.preventDefault() }
    if (event.key === 'j') { pageIndex = Math.min(pageIndex + 1, project.pages.length - 1); selectedIndex = -1 }
    if (event.key === 'k') { pageIndex = Math.max(pageIndex - 1, 0); selectedIndex = -1 }
    if (event.key === 'Tab' && page?.panels?.length) {
      selectedIndex = (selectedIndex + 1) % page.panels.length
      event.preventDefault()
    }
    if (event.key === 'l' && selected) updateSelected({ locked: !selected.locked })
    if ((event.key === '[' || event.key === ']') && selected) {
      const at = GUTTERS.indexOf(selected.gutter_after)
      const next = event.key === '[' ? Math.max(0, at - 1) : Math.min(GUTTERS.length - 1, at + 1)
      updateSelected({ gutter_after: GUTTERS[next] })
    }
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key) && selected) {
      const step = event.shiftKey ? 10 : 1
      const [x, y, w, h] = selected.bbox
      const moved = {
        ArrowLeft: [x - step, y, w, h], ArrowRight: [x + step, y, w, h],
        ArrowUp: [x, y - step, w, h], ArrowDown: [x, y + step, w, h],
      }[event.key]
      updateSelected({ bbox: moved })
      event.preventDefault()
    }
  }
</script>

<svelte:window on:keydown={onKey} />

{#if project && page}
  <div class="editor">
    <nav class="rail">
      <button on:click={onBack}>← Library</button>
      {#each project.pages as p, i}
        <button class:active={i === pageIndex} on:click={() => { pageIndex = i; selectedIndex = -1 }}>
          <img src={`/media/${chapterId}/${p.cleaned}?w=140`} alt="" loading="lazy" />
          <span>{i + 1}</span>
          {#if p.needs_review}<span class="flag" title="Detection was unsure">!</span>{/if}
        </button>
      {/each}
    </nav>

    <main>
      <PanelCanvas
        src={`/media/${chapterId}/${page.cleaned}?w=1200`}
        imageW={page.width} imageH={page.height}
        panels={page.panels} {selectedIndex}
        on:change={onChange} on:select={(e) => (selectedIndex = e.detail)} />
    </main>

    <aside>
      <h2>Page {pageIndex + 1} of {project.pages.length}</h2>
      <label>Kind
        <select bind:value={page.kind} on:change={queueSave}>
          <option value="normal">normal</option><option value="splash">splash</option>
          <option value="spread">spread</option><option value="skip">skip</option>
        </select>
      </label>
      <button on:click={() => postDetect(chapterId, true).catch(showError)}>Re-detect page</button>

      {#if selected}
        <h3>Panel {selectedIndex + 1}</h3>
        <label>Role
          <select value={selected.role} on:change={(e) => updateSelected({ role: e.target.value })}>
            <option value="normal">normal</option><option value="reaction">reaction</option>
            <option value="splash">splash</option>
          </select>
        </label>
        <label>Scale
          <input type="range" min="0.3" max="1" step="0.02" value={selected.scale}
                 on:input={(e) => updateSelected({ scale: Number(e.target.value) })} />
          {selected.scale.toFixed(2)}
        </label>
        <label>Gutter after
          <select value={selected.gutter_after} on:change={(e) => updateSelected({ gutter_after: e.target.value })}>
            {#each GUTTERS as g}<option value={g}>{g}</option>{/each}
          </select>
        </label>
        <label><input type="checkbox" checked={selected.locked}
               on:change={(e) => updateSelected({ locked: e.target.checked })} /> Locked</label>
        <button on:click={deleteSelected}>Delete panel</button>
      {:else}
        <p>Drag on empty space to add a panel.</p>
      {/if}

      <hr />
      <button on:click={onRead}>Preview scroll</button>
      <button on:click={() => postAssemble(chapterId).catch(showError)}>Export CBZ</button>
    </aside>
  </div>
{:else}
  <p>Loading…</p>
{/if}
```

- [ ] **Step 2: Wire it into App.svelte**

Add the import and the branch:

```svelte
  import Editor from './routes/Editor.svelte'
```
```svelte
{:else if view === 'editor'}
  <Editor chapterId={activeChapterId} onBack={() => (view = 'library')}
          onRead={() => (view = 'reader')} />
```

- [ ] **Step 3: Verify against the real chapter**

Copy `chapter-swamp01` into the library root, run the app, open it. Expected: it opens on a flagged page; boxes drag and resize; the copyright bar on page 2 can be deleted; edits survive a reload.

- [ ] **Step 4: Commit**

```bash
git add ui/src/routes/Editor.svelte ui/src/App.svelte
git commit -m "feat: add editor screen with inspector and keyboard shortcuts"
```

---

### Task 15: Retire the old review UI

**Files:**
- Delete: `web/index.html`
- Delete: `scrollstrip/server.py`
- Modify: `scrollstrip/cli.py` (point `review` at the new app)
- Modify: `README.md`

- [ ] **Step 1: Repoint the CLI**

Replace `cmd_review` in `scrollstrip/cli.py`:

```python
def cmd_review(args) -> None:
    """The review UI is now the desktop app."""
    from .shell import main as shell_main

    print("Opening the Scrollstrip app. The old per-project review server has been replaced.")
    shell_main()
```

Delete the `from .server import serve` import and the `serve(...)` call in `cmd_run`; replace the latter with `shell_main()` guarded by `if args.review:`.

- [ ] **Step 2: Remove the old files**

```bash
git rm web/index.html scrollstrip/server.py
```

- [ ] **Step 3: Update the README**

Replace the "Review UI (this is the design step)" section's opening so it describes the app rather than `http://127.0.0.1:8765/`, and replace the chapter workflow commands with `python -m scrollstrip.shell`. Keep the panel role and gutter documentation — it is still accurate and still the part that matters.

- [ ] **Step 4: Run the suite**

Run: `pytest tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: replace the per-project review server with the desktop app"
```

---

## Phase 3 — Reader

### Task 16: Reader screen

**Files:**
- Create: `ui/src/routes/Reader.svelte`
- Modify: `ui/src/App.svelte`

**Interfaces:**
- Consumes: `getPreview` (Task 10 client, Task 9 endpoint).

- [ ] **Step 1: Implement**

```svelte
<!-- ui/src/routes/Reader.svelte -->
<script>
  import { onMount } from 'svelte'
  import { getPreview } from '../lib/api.js'
  import { showError } from '../lib/stores.js'

  export let chapterId, onBack, onEditPage

  let preview = null, showGutters = false, width = 420

  onMount(async () => {
    try { preview = await getPreview(chapterId) } catch (err) { showError(err) }
  })

  // The manifest gives crops in image space; CSS reproduces them without
  // compositing anything server-side.
  function cropStyle(panel) {
    if (!panel.bbox) return `width:${width}px`
    const [, , w] = panel.bbox
    const factor = (width * panel.scale) / w
    return `width:${width * panel.scale}px; height:${panel.bbox[3] * factor}px; overflow:hidden; position:relative`
  }

  function imageStyle(panel) {
    if (!panel.bbox) return `width:${width}px; display:block`
    const [x, y, w] = panel.bbox
    const factor = (width * panel.scale) / w
    return `position:absolute; left:${-x * factor}px; top:${-y * factor}px; width:${factor * 100}%;
            transform-origin: top left; image-rendering:auto`
  }
</script>

<header>
  <button on:click={onBack}>← Editor</button>
  <label><input type="checkbox" bind:checked={showGutters} /> Show gutters</label>
  <label>Width <input type="range" min="320" max="640" bind:value={width} /> {width}px</label>
</header>

{#if preview}
  <div class="scroll" style={`background: rgb(${preview.background.join(',')})`}>
    {#each preview.panels as panel, i}
      <div class="panel" style={cropStyle(panel)} on:click={() => onEditPage(panel.page_id)}>
        <img src={`${panel.src}?w=${Math.round(width * 2)}`} style={imageStyle(panel)} alt="" loading="lazy" />
      </div>
      <div class="gutter" style={`height:${panel.gutter_after * (width / preview.canvas_width)}px`}>
        {#if showGutters}<span>{panel.gutter_after}px</span>{/if}
      </div>
    {/each}
  </div>
{:else}
  <p>Composing…</p>
{/if}

<style>
  .scroll { margin: 0 auto; display: flex; flex-direction: column; align-items: center; }
  .panel { cursor: pointer; }
  .gutter { width: 100%; display: grid; place-items: center; }
  .gutter span { font: 11px monospace; color: #888; }
</style>
```

- [ ] **Step 2: Wire into App.svelte**

```svelte
{:else if view === 'reader'}
  <Reader chapterId={activeChapterId} onBack={() => (view = 'editor')}
          onEditPage={() => (view = 'editor')} />
```

- [ ] **Step 3: Verify parity by eye**

Open a chapter in the reader, then export it and open the CBZ. Expected: the same panels, the same order, the same relative gutters. Any difference is a bug in the preview, not the export.

- [ ] **Step 4: Commit**

```bash
git add ui/src/routes/Reader.svelte ui/src/App.svelte
git commit -m "feat: add live scroll preview reader"
```

---

## Phase 4 — Packaging

### Task 17: PyInstaller build

**Files:**
- Create: `packaging/scrollstrip.spec`
- Create: `packaging/build.ps1`
- Create: `packaging/README-for-users.md`
- Modify: `.gitignore`

- [ ] **Step 1: Add build artefacts to .gitignore**

```
build/
packaging/dist/
*.spec.bak
```

- [ ] **Step 2: Write the build script**

```powershell
# packaging/build.ps1
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot\..

Write-Host "Building frontend..."
Push-Location ui
npm ci
npm run build
Pop-Location

Write-Host "Building app..."
pyinstaller --noconfirm --clean packaging\scrollstrip.spec

Write-Host "Zipping..."
Compress-Archive -Path dist\Scrollstrip\* -DestinationPath dist\Scrollstrip.zip -Force
Pop-Location
Write-Host "Done: dist\Scrollstrip.zip"
```

- [ ] **Step 3: Write the spec file**

```python
# packaging/scrollstrip.spec
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

a = Analysis(
    ['..\\scrollstrip\\shell.py'],
    pathex=['..'],
    binaries=[],
    datas=[('..\\scrollstrip\\web_dist', 'scrollstrip/web_dist')]
          + collect_data_files('ultralytics'),
    hiddenimports=collect_submodules('uvicorn') + ['pymupdf', 'cv2'],
    excludes=['matplotlib', 'tkinter', 'pandas', 'polars', 'IPython', 'notebook'],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Scrollstrip',
          console=False, icon=None)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False,
               upx=False, name='Scrollstrip')
```

The `excludes` list matters: `matplotlib`, `pandas` and `polars` are pulled in transitively by ultralytics but never used here, and each adds tens of megabytes.

- [ ] **Step 4: Write the user README**

```markdown
# Scrollstrip

Turns comics you own into a phone-friendly continuous vertical scroll.

## Running it

Unzip anywhere and run `Scrollstrip.exe`. No installation.

If nothing opens, install the Microsoft Edge WebView2 runtime:
https://developer.microsoft.com/microsoft-edge/webview2/

## Using it

1. **Import** a folder of scans, a `.cbz`, or a `.pdf`.
   CBR files are not supported - convert to CBZ first.
2. Wait for processing. The first run downloads a ~119 MB panel-detection model.
3. **Fix the boxes.** Automatic detection gets most panels and misses some.
   Pages it was unsure about are marked with `!`.
4. **Set gutters.** The gap after each panel is the pacing. `large` for a reveal
   or a scene change, `tight` for fast back-and-forth.
5. **Preview the scroll**, then **Export CBZ**.

Read the exported CBZ in any comic reader set to webtoon / continuous vertical mode.

Your chapters live in `Documents\Scrollstrip\`.

This is for comics you own.
```

- [ ] **Step 5: Build and test on a clean machine**

Run: `powershell -File packaging\build.ps1`
Expected: `dist\Scrollstrip.zip`. Unzip on a machine without Python and confirm a chapter converts end to end.

- [ ] **Step 6: Commit**

```bash
git add packaging/ .gitignore
git commit -m "build: add PyInstaller packaging and user README"
```

---

## Verification

After every task: `pytest tests/ -q` and, from Task 12 onward, `cd ui && npm test`.

Final check — the full loop on real material:

1. Import `chapter-swamp01`'s source scans as a new chapter.
2. Confirm it appears as "Processing" immediately, with progress.
3. Open the editor; confirm it lands on a flagged page.
4. Delete the indicia box on the page that has one; adjust a gutter.
5. Preview the scroll; confirm the edit is reflected.
6. Export; confirm the CBZ panel count matches the preview panel count.
