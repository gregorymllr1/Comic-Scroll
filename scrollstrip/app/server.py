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
        # Reserve the folder before returning. unique_chapter_dir only checks
        # exists(); without mkdir, a second import of the same name can pick
        # the same path and later overwrite project.json.
        target.mkdir(parents=True, exist_ok=True)
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
