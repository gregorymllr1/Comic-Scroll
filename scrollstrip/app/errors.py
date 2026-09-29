# scrollstrip/app/errors.py
"""Map core exceptions onto HTTP responses the UI can show a person."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from ..errors import JobCancelled
from ..ingest import IngestError


class ProjectBusy(Exception):
    """An edit arrived while a job holds that project."""


HINTS = {
    "IngestError": "Check the file is a folder, .cbz/.zip or .pdf that you can open.",
    "FileNotFoundError": "The file may have been moved or deleted since you chose it.",
    "NotADirectoryError": "Set SCROLLSTRIP_LIBRARY to a folder you can write to.",
    "PermissionError": "Close anything using the file, or pick a different folder.",
    "ProjectBusy": "Wait for the running job to finish, or cancel it.",
}

STATUS = {
    "IngestError": 400,
    "ValueError": 400,
    "FileNotFoundError": 404,
    "NotADirectoryError": 500,
    "PermissionError": 500,
    "JobCancelled": 409,
    "ProjectBusy": 409,
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
    @app.exception_handler(ProjectBusy)
    async def handle(request: Request, exc: Exception):  # noqa: ANN001
        status, body = payload(exc)
        return JSONResponse(status_code=status, content=body)
