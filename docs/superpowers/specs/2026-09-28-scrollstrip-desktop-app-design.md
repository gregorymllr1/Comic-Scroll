# Scrollstrip desktop app — design

**Date:** 2026-09-28
**Status:** approved in conversation, pending written review
**Audience:** whoever implements this, including future maintainers

## Purpose

Scrollstrip converts comics the reader owns — a folder of scans, a CBZ, or a PDF —
into a phone-native continuous vertical scroll, in the style of DC Universe
Infinite. Today it is a CLI plus a single-page browser box editor. Every chapter
requires typing three commands and hand-editing `config.yaml`, and judging whether
the pacing works means exporting a CBZ and copying it to a phone.

This design replaces that with a desktop application, without rewriting the
imaging pipeline that already works.

### Success criteria

- Converting a chapter never requires the terminal.
- Panel review is fast enough that correcting a page feels cheaper than avoiding it.
- Pacing can be judged inside the app, before exporting anything.
- A friend can unzip the app and run it without installing Python.

### Non-goals

Auto-update, user accounts, cloud sync, a plugin system, a mobile app, and public
distribution (code signing, store presence). With roughly three users none of these
pay for themselves. Public release would also require a legal position on
converting copyrighted comics that this project does not currently take; the tool
is for comics the reader already owns.

## Constraints

- **Windows first.** The developer runs Windows 11. macOS should not be designed
  against, but nothing should be gratuitously Windows-only.
- **The pipeline is Python and stays Python.** torch, OpenCV and PyMuPDF are not
  portable to another runtime, so the shell is chosen to avoid a process boundary.
- **The existing core is trusted.** As of 2026-09-27 the pipeline has been run end
  to end on real 600 DPI scans (24 pages → 85 slices → CBZ) with 32 passing tests.
  This design reuses it rather than revisiting it.

## Architecture

Four layers with one-way dependencies. Nothing below knows about anything above.

```
pywebview shell      native window, native file dialogs
        ↓
FastAPI server       REST + SSE, serves the built frontend and media
        ↓
library + jobs       new: chapter discovery, background work queue
        ↓
scrollstrip core     existing: ingest, clean, detect, assemble, project, config
```

The CLI is retained and drives the same core. It stays valuable for scripting and
for debugging the GUI, and it means the GUI cannot become the only way to reach a
capability.

### Repository layout

```
scrollstrip/
  scrollstrip/          existing core package (unchanged except where noted)
    app/                NEW backend
      __init__.py
      server.py         FastAPI app factory, routes
      library.py        chapter discovery and metadata
      jobs.py           worker thread, queue, job state
      media.py          on-demand image resize + cache
      errors.py         typed error → HTTP payload mapping
    shell.py            NEW pywebview entry point
  web/                  EXISTING single-page editor; retired after phase 2
  ui/                   NEW Svelte + Vite frontend
    src/routes/         Library, Import, Editor, Reader
    src/lib/            api client, stores, canvas helpers
  tests/                existing + new backend tests
  docs/superpowers/specs/
```

### The one change to the core

`clean_project`, `detect_project` and `assemble_project` each gain two optional
keyword arguments:

```python
def clean_project(project_dir, cfg, *, progress=None, should_cancel=None) -> dict
```

- `progress(done: int, total: int, message: str) -> None`
- `should_cancel() -> bool`, polled once per page; returning True raises `JobCancelled`

All three already loop per page, so this is a few lines each and yields per-page
progress and sub-2-second cancellation. Both default to `None`, so the CLI and the
existing tests are unaffected.

No other core change is in scope. In particular this design does not revisit panel
detection quality, reading-order heuristics, or the known full-bleed crop gap.

## Library

### Discovery, not an index

The library root defaults to `~/Documents/Scrollstrip/` and is configurable. A
chapter is any immediate subdirectory containing `project.json`. Listing the
library means globbing `*/project.json` and reading each one, unioned with any
in-flight import jobs (see "Jobs"), so a chapter is visible while it is still
being ingested.

**There is deliberately no `library.json`.** A separate index drifts the moment a
folder is moved, renamed, or deleted outside the app, and reconciling it is a
recurring source of bugs. Each chapter's `project.json` stays its own source of
truth, so moving a chapter folder simply works. Chapter counts here are in the
tens or hundreds and the files are small, so scanning is fast enough.

### Chapter metadata

Derived per chapter, cached in memory keyed by `project.json` mtime:

| Field | Source |
|---|---|
| `id` | directory name |
| `name` | `project.json` → `name` |
| `page_count` | length of `pages` |
| `panel_count` | sum of `panels` |
| `needs_review` | count of pages with `needs_review: true` |
| `status` | derived, see below |
| `cover` | thumbnail of page 1, cached at `work/thumbs/cover.jpg` |
| `updated` | `project.json` mtime |

`status` is derived, never stored: `processing` if a job for this chapter is
active; `new` if no page has `status: detected`; `needs_review` if any page is
flagged; `ready` if an `export/*.cbz` exists and is newer than `project.json`;
otherwise `reviewed`.

A directory whose `project.json` fails to parse is listed with status `damaged`
and its error message, not silently skipped — a chapter disappearing from the
library with no explanation is worse than an ugly card.

## Jobs

### Model

A single worker thread drains a FIFO queue. One worker, not a pool: the work is
CPU-bound through OpenCV and torch, which already use multiple cores internally,
so concurrent jobs would contend rather than help. This also makes progress
reporting and cancellation simple to reason about.

```python
@dataclass
class Job:
    id: str
    kind: Literal["import", "clean", "detect", "assemble"]
    project_id: str
    state: Literal["queued", "running", "done", "failed", "cancelled"]
    done: int
    total: int
    message: str
    error: str | None
    created: float
    finished: float | None
```

Jobs are in-memory only. A crash loses the queue, which is acceptable: every stage
is idempotent and re-runnable, and the on-disk `project.json` is always consistent
because it is written atomically via the existing temp-file rename.

**Batch processing is the queue.** Importing five chapters enqueues five
import jobs; no separate batch subsystem exists.

`POST /api/library/import` must not block. Extracting a 300 MB CBZ or rasterizing
a long PDF takes appreciable time, so the endpoint allocates a project id, enqueues
a single `import` job covering ingest → clean → detect, and returns immediately.

This creates a window where the project directory exists but `project.json` does
not, because `init_project` writes it only after ingest completes. A chapter that
is being imported would therefore be invisible in the library — the worst possible
moment for it to be missing. `GET /api/library` therefore returns the union of
on-disk chapters and in-flight import jobs, with the latter shown as `processing`
using the requested name until their `project.json` lands.

Cancellation is cooperative. `POST /api/jobs/{id}/cancel` sets a flag; a queued job
transitions straight to `cancelled`, a running one stops at its next page boundary.

## API

All JSON. Errors follow the shape in "Error handling".

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/library` | chapter list with metadata |
| `POST` | `/api/library/import` | `{source, name, engine?, width?}` → returns `{job_id, project_id}` immediately |
| `POST` | `/api/library/rescan` | drop the metadata cache |
| `GET` | `/api/project/{id}` | full `project.json` |
| `PUT` | `/api/project/{id}/page/{page_id}` | replace that page's `panels`, `kind`; returns the saved page |
| `POST` | `/api/project/{id}/detect` | re-run detection, `{keep_edits: bool}` |
| `POST` | `/api/project/{id}/assemble` | enqueue assembly |
| `GET` | `/api/project/{id}/preview` | ordered panel manifest for the reader, see below |
| `GET` | `/api/project/{id}/config` · `PUT` same | per-chapter `config.yaml` |
| `GET` | `/api/jobs` | all jobs |
| `POST` | `/api/jobs/{id}/cancel` | request cancellation |
| `GET` | `/api/events` | SSE stream of job state changes |
| `GET` | `/media/{id}/{path}?w=` | page or slice image, resized and cached |
| `GET` | `/` | the built frontend |

### Media serving

Cleaned pages are up to 2800px and roughly 2 MB. Serving originals to the editor
would make every page change feel broken, so `/media` resizes on demand to the
requested width and caches the result under `<project>/work/cache/{w}/`. Requests
without `?w=` serve the original. The editor requests ~1200px, the page rail ~200px,
the reader the canvas width.

`{path}` is resolved against the project directory and rejected if it escapes,
using the same guard as archive extraction.

### Live preview

`GET /api/project/{id}/preview` returns the panel sequence the reader should
display — for each panel, its source page, crop box, target scale and the gutter
that follows — **without writing a CBZ**. The reader composes the scroll from
cropped page images client-side.

This is the feature the app exists for. Judging pacing today means assembling,
copying a CBZ to a phone, reading it, and coming back; making that loop instant is
what turns gutter tuning from a chore into something you actually do.

Composition rules must match `assemble.py` exactly, so the preview and the export
agree. Any divergence is a bug in the preview.

## Screens

### Library

Card grid: cover, name, page count, status badge, and a progress bar for chapters
with an active job. Actions: Import, Rescan. Clicking a card opens the Editor.
Empty state explains what to import and that the tool is for comics you own.

### Import

Opens a **native** file dialog through pywebview — a browser file input cannot give
a real folder picker, and folders are a first-class source. Accepts a folder, a
`.cbz`/`.zip`, or a `.pdf`. Fields: name (defaulted from the source), detection
engine, canvas width. Several can be added before starting; they queue.

`.cbr`/`.cb7` are rejected with the core's existing message telling the user to
convert to CBZ first.

### Editor

The screen that decides whether the app is worth using.

- **Left rail:** page thumbnails in order, badged where `needs_review` is set, so
  review starts where detection is least confident rather than at page 1.
- **Centre:** canvas showing the cleaned page with panel boxes. Drag to move,
  corner handles to resize, drag on empty space to add, Delete to remove. Boxes
  render in reading order with their index shown, because wrong order is the
  failure the reader notices most.
- **Right inspector:** for the selected panel — role, scale, gutter after, lock.
  For the page — kind (`normal`/`splash`/`spread`/`skip`) and a re-detect action.

Keyboard-first: arrow keys nudge, Tab cycles panels, `[`/`]` change gutter,
`L` locks, Delete removes, `J`/`K` change page. This is where all the time goes.

Edits `PUT` per page, debounced. `project.json` remains the source of truth.

### Reader

A phone-width column composed live from `/preview`. Scrolls the chapter as it will
actually read. A toggle outlines gutters and labels their size, so pacing problems
are visible rather than felt. Clicking a panel jumps to it in the Editor — that
round trip is the point.

## Error handling

Core raises typed exceptions (`IngestError` today, plus `JobCancelled`). The API
maps them to a consistent payload:

```json
{ "error": "IngestError", "message": "...", "hint": "Convert to .cbz first." }
```

The UI surfaces `message` as a toast and `hint` as its secondary line. Unexpected
exceptions become a generic message plus a full traceback written to
`~/Documents/Scrollstrip/logs/scrollstrip-YYYY-MM-DD.log`, with the log path shown
in the toast.

Specific cases that get their own message rather than a stack trace: missing
WebView2 runtime; library root missing or unwritable; damaged `project.json`;
source file disappeared between import and processing; disk full during assemble.

## Testing

The 32 existing tests must stay green; the core changes are additive and must not
alter CLI behaviour.

New coverage:

- **Core callbacks** — progress fires once per page; `should_cancel` stops work and
  raises `JobCancelled`; both default to no-ops.
- **Library** — discovery finds chapters and ignores non-chapter directories;
  status derivation for each state; damaged `project.json` surfaces as `damaged`;
  moving a folder is picked up on rescan.
- **Jobs** — FIFO order; cancel while queued and while running; failure captures
  the error; state transitions are legal.
- **API** — `TestClient` per endpoint, including media path-traversal rejection and
  `?w=` caching.
- **Preview/assemble parity** — the preview manifest and a real assemble produce the
  same panel order, scales and gutters. This guards the one invariant that would
  otherwise rot silently.

Frontend testing stays deliberately thin. The one piece worth isolating is the
editor's coordinate math — image space to canvas space and back, under zoom and
pan — which is pure and where off-by-one bugs actually live.

## Phasing

Each phase is independently usable and independently shippable.

**Phase 1 — backend and library.** Core callbacks, job runner, FastAPI server,
media cache, pywebview shell, Library and Import screens. At the end of this phase
the app replaces the CLI for the normal path, and batch works because the queue
exists.
*Done when:* a chapter can be imported from a CBZ, processed with visible progress,
and appears in the library with correct status — without the terminal.

**Phase 2 — Editor.** The canvas, rail, inspector and keyboard model.
*Done when:* every correction the current web UI supports is available and faster,
and the old `web/index.html` can be deleted.

**Phase 3 — Reader.** Live preview composition and editor round-trip.
*Done when:* pacing can be judged and fixed without exporting.

**Phase 4 — Packaging.** PyInstaller one-folder build, zipped, plus a short README
for non-technical users.
*Done when:* a friend unzips it on a clean Windows machine and converts a chapter.

Phase 2 is expected to take longer than the other three combined.

## Risks

- **Bundle size.** PyInstaller with torch lands around 300 MB. Acceptable for a zip
  handed to friends. If it becomes a problem, make the YOLO model an optional
  first-run download and ship the OpenCV detector as the default — the fallback
  already exists.
- **Preview/export divergence.** Two implementations of panel composition will drift.
  Mitigated by the parity test, and by having the preview endpoint derive its
  manifest from the same functions `assemble.py` uses rather than reimplementing them.
- **WebView2 absence.** Present by default on Windows 11 but not guaranteed on
  Windows 10. Detect at startup and link the Microsoft installer.
- **Editor performance.** Large pages plus many boxes can make canvas interaction
  sluggish. The media cache addresses load time; if interaction itself lags, render
  the page to a bitmap layer and draw only boxes on each frame.
