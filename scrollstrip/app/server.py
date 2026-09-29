# scrollstrip/app/server.py
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..assemble import assemble_project, iter_panel_placements
from ..clean import clean_project
from ..config import load_yaml, write_yaml
from ..detect import detect_project
from ..ingest import describe_source
from ..project import init_project, load_project, merged_config, save_project, unique_chapter_dir
from . import errors as app_errors
from .errors import ProjectBusy
from .jobs import JobQueue
from .library import chapter_dir, library_root, list_chapters
from .media import cached_resize


class ImportRequest(BaseModel):
    source: str
    name: str | None = None
    engine: str | None = None
    width: int | None = None


def reserve_chapter_dir(root: Path, name: str) -> Path:
    """Pick a unique chapter path and create it, or try again if someone else won."""
    while True:
        target = unique_chapter_dir(root, name)
        try:
            target.mkdir(parents=True, exist_ok=False)
            return target
        except FileExistsError:
            continue


def create_app(root: Path | None = None, jobs: JobQueue | None = None) -> FastAPI:
    app = FastAPI(title="Scrollstrip")
    app.state.root = Path(root) if root else library_root()
    app.state.jobs = jobs or JobQueue()
    app.state.import_names: dict[str, str] = {}
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
                "id": j["project_id"],
                "name": app.state.import_names.get(j["project_id"]) or j["project_id"],
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
        target = reserve_chapter_dir(app.state.root, name)
        project_id = target.name
        app.state.import_names[project_id] = name

        def work(progress, should_cancel):
            progress(0, 3, f"Importing {name}")
            init_project(target, pages_dir=source, name=name)
            cfg = merged_config(target)
            disk = load_yaml(target / "config.yaml")
            if req.width:
                width = int(req.width)
                cfg["canvas_width"] = width
                disk["canvas_width"] = width
            if req.engine:
                cfg.setdefault("detect", {})["engine"] = req.engine
                disk.setdefault("detect", {})["engine"] = req.engine
            if req.width or req.engine:
                write_yaml(target / "config.yaml", disk)
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

    return app
