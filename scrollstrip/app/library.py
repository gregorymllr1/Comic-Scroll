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
