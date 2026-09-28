from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .config import DEFAULTS, IMAGE_EXTS, deep_merge, resolve_config, write_yaml
from .ingest import collect_pages, describe_source, natural_key_str


def natural_key(path: Path) -> tuple:
    """Natural sort for a path. Delegates to the shared string key so that
    numeric and alphabetic names stay comparable (see ingest.natural_key_str)."""
    return natural_key_str(path.name)


def list_images(folder: Path) -> list[Path]:
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=natural_key)


def empty_project(name: str, pages: list[dict] | None = None) -> dict:
    return {
        "name": name,
        "version": 1,
        "config": {},
        "pages": pages or [],
    }


def project_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def load_project(project_dir: Path) -> dict:
    path = project_path(project_dir)
    if not path.exists():
        raise FileNotFoundError(f"No project.json in {project_dir}. Run: scrollstrip init")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("pages", [])
    data.setdefault("config", {})
    data.setdefault("name", project_dir.name)
    return data


def save_project(project_dir: Path, data: dict) -> None:
    path = project_path(project_dir)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    tmp.replace(path)


def page_stem(index: int, source_name: str) -> str:
    return f"{index:03d}-{Path(source_name).stem}"


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')

# Device names Windows refuses to create, including with an extension (NUL.txt).
_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
})


def unique_chapter_dir(root: Path, name: str) -> Path:
    """A fresh directory for a new chapter.

    Two imports named the same must not land in the same folder: the second
    init_project would overwrite the first chapter's project.json and destroy
    every panel edit in it.
    """
    slug = _UNSAFE.sub("-", (name or "").strip()).strip(". -")
    slug = slug or "chapter"
    # Rewrite before the collision loop so CON-chapter-2 still works.
    if slug.split(".", 1)[0].upper() in _RESERVED:
        slug = f"{slug}-chapter"
    candidate = Path(root) / slug
    counter = 2
    while candidate.exists():
        candidate = Path(root) / f"{slug}-{counter}"
        counter += 1
    return candidate


def init_project(
    project_dir: Path,
    pages_dir: Path | None = None,
    copy_pages: bool = True,
    name: str | None = None,
) -> dict:
    project_dir = project_dir.resolve()
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "pages").mkdir(exist_ok=True)
    (project_dir / "work" / "cleaned").mkdir(parents=True, exist_ok=True)
    (project_dir / "work" / "previews").mkdir(parents=True, exist_ok=True)
    (project_dir / "work" / "panels").mkdir(parents=True, exist_ok=True)
    (project_dir / "export" / "slices").mkdir(parents=True, exist_ok=True)
    (project_dir / "models").mkdir(exist_ok=True)

    if not (project_dir / "config.yaml").exists():
        write_yaml(project_dir / "config.yaml", {"canvas_width": DEFAULTS["canvas_width"]})

    sources: list[Path] = []
    staging = project_dir / "work" / "ingest"
    source_kind = "folder"
    if pages_dir:
        source = Path(pages_dir)
        source_kind = describe_source(source)
        cfg = resolve_config(project_dir)
        sources = collect_pages(source, staging, cfg)
        if source_kind != "folder" and not copy_pages:
            print(
                f"--link is ignored for {'an' if source_kind == 'archive' else 'a'} {source_kind} source; "
                "extracted pages are copied into the project."
            )
            copy_pages = True
        print(f"Read {len(sources)} pages from {source_kind}: {source.name}")

    pages: list[dict[str, Any]] = []
    dest_dir = project_dir / "pages"
    for i, src in enumerate(sources, start=1):
        stem = page_stem(i, src.name)
        dest = dest_dir / f"{stem}{src.suffix.lower()}"
        if copy_pages:
            if not dest.exists() or dest.resolve() != src.resolve():
                shutil.copy2(src, dest)
            rel = f"pages/{dest.name}"
        else:
            try:
                rel = str(src.resolve().relative_to(project_dir))
            except ValueError:
                rel = str(src.resolve())
        pages.append(
            {
                "id": stem,
                "source": rel,
                "cleaned": f"work/cleaned/{stem}.jpg",
                "preview": f"work/previews/{stem}.jpg",
                "kind": "normal",
                "status": "new",
                "width": None,
                "height": None,
                "panels": [],
            }
        )

    # Extracted pages now live in pages/; drop the staging copy.
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    data = empty_project(name or project_dir.name, pages)
    save_project(project_dir, data)
    return data


def resolve_page_file(project_dir: Path, rel: str) -> Path:
    path = Path(rel)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()


def merged_config(project_dir: Path, project: dict | None = None) -> dict:
    project = project or load_project(project_dir)
    cfg = resolve_config(project_dir)
    return deep_merge(cfg, project.get("config") or {})
