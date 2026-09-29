from __future__ import annotations

import zipfile
from pathlib import Path

import cv2
import numpy as np

from .clean import _read, _write_jpeg
from .errors import JobCancelled
from .project import load_project, save_project


def gutter_px(name_or_number, cfg: dict) -> int:
    presets = cfg.get("gutter_presets", {})
    if isinstance(name_or_number, (int, float)):
        return max(0, int(name_or_number))
    if name_or_number in presets:
        return int(presets[name_or_number])
    return int(presets.get(cfg.get("default_gutter", "medium"), 80))


def sorted_panels(page: dict) -> list[dict]:
    panels = list(page.get("panels") or [])
    panels.sort(key=lambda p: int(p.get("order", 0)))
    return panels


def crop_panel(image: np.ndarray, bbox: list[int]) -> np.ndarray:
    h, w = image.shape[:2]
    x, y, bw, bh = [int(v) for v in bbox]
    x = max(0, x)
    y = max(0, y)
    x2 = min(w, x + bw)
    y2 = min(h, y + bh)
    crop = image[y:y2, x:x2]
    if crop.size == 0:
        raise ValueError(f"Empty crop for bbox {bbox}")
    return crop


def fit_width(panel_img: np.ndarray, canvas_width: int, scale: float) -> np.ndarray:
    target_w = max(16, int(round(canvas_width * float(scale))))
    target_w = min(target_w, canvas_width)
    ph, pw = panel_img.shape[:2]
    target_h = max(16, int(round(ph * (target_w / float(pw)))))
    interp = cv2.INTER_AREA if target_w < pw else cv2.INTER_CUBIC
    return cv2.resize(panel_img, (target_w, target_h), interpolation=interp)


def place_on_canvas(panel_img: np.ndarray, canvas_width: int, background: tuple[int, int, int]) -> np.ndarray:
    ph, pw = panel_img.shape[:2]
    canvas = np.full((ph, canvas_width, 3), background, dtype=np.uint8)
    x = max(0, (canvas_width - pw) // 2)
    canvas[:, x : x + pw] = panel_img
    return canvas


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


def pack_slices(blocks: list[tuple[np.ndarray, int]], cfg: dict) -> list[np.ndarray]:
    max_h = int(cfg.get("slice_max_height", 2000))
    background = tuple(int(c) for c in cfg.get("background", [18, 18, 18]))
    canvas_width = int(cfg.get("canvas_width", 1080))
    slices: list[np.ndarray] = []
    current: list[np.ndarray] = []
    current_h = 0

    def flush() -> None:
        nonlocal current, current_h
        if not current:
            return
        slices.append(np.vstack(current))
        current = []
        current_h = 0

    for block, gap in blocks:
        bh = block.shape[0]
        # Never slice through a panel. A single oversized panel becomes its own file.
        if current and current_h + bh > max_h:
            flush()
        if not current and bh > max_h:
            slices.append(block)
            if gap:
                # Keep page rhythm even after a tall splash.
                gap_img = np.full((gap, canvas_width, 3), background, dtype=np.uint8)
                if gap <= max_h:
                    current = [gap_img]
                    current_h = gap
            continue
        current.append(block)
        current_h += bh
        if gap > 0:
            if current_h + gap > max_h:
                flush()
            else:
                current.append(np.full((gap, canvas_width, 3), background, dtype=np.uint8))
                current_h += gap
    flush()
    return slices


def assemble_project(project_dir: Path, cfg: dict, *, progress=None, should_cancel=None) -> dict:
    project = load_project(project_dir)
    quality = int(cfg.get("jpeg_quality", 92))
    slice_dir = project_dir / "export" / "slices"
    if slice_dir.exists():
        for old in slice_dir.glob("*"):
            if old.is_file():
                old.unlink()
    slice_dir.mkdir(parents=True, exist_ok=True)

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

    long_path = project_dir / "export" / "long-strip.jpg"
    if slices:
        # Optional whole-chapter strip for inspection; skip if enormous.
        total_h = sum(s.shape[0] for s in slices)
        if total_h <= 30000:
            _write_jpeg(long_path, np.vstack(slices), quality)
        elif long_path.exists():
            long_path.unlink()

    # Directory slug is already Windows-safe. The display name may contain ':'.
    cbz_name = f"{project_dir.name}.cbz"
    cbz_path = project_dir / "export" / cbz_name
    with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for name in names:
            zf.write(slice_dir / name, arcname=name)

    project["export"] = {
        "slices": [f"export/slices/{n}" for n in names],
        "cbz": f"export/{cbz_name}",
        "slice_count": len(names),
    }
    save_project(project_dir, project)
    return project
