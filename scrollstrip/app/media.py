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

    # Raw `rel` can still contain `\` and `..`. pathlib treats those as
    # separators, so the cache name comes from the resolved in-project path.
    resolved_project = Path(project_dir).resolve()
    filename = (
        source.relative_to(resolved_project).as_posix().replace("/", "_").replace("\\", "_")
    )
    width_dir = Path(project_dir) / "work" / "cache" / str(width)
    target = (width_dir / filename).with_suffix(".jpg")
    resolved_target = target.resolve()
    try:
        resolved_target.relative_to(width_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"Cache path escapes the project: {rel}") from exc
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as img:
        img = img.convert("RGB")
        height = max(1, round(img.height * width / img.width))
        img.resize((width, height), Image.LANCZOS).save(target, "JPEG", quality=88)
    return target
