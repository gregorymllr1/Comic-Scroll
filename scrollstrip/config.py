from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "canvas_width": 1080,
    "working_max_side": 2800,
    "jpeg_quality": 92,
    "background": [18, 18, 18],
    "gutter_presets": {
        "tight": 32,
        "medium": 80,
        "large": 280,
    },
    "default_gutter": "medium",
    "slice_max_height": 2000,
    "slice_gap": 0,
    "clean": {
        "deskew": True,
        "max_deskew_degrees": 6.0,
        "min_deskew_degrees": 0.35,
        "crop_border": True,
        "crop_padding": 8,
        "crop_min_fill": 0.08,
        "crop_min_area_kept": 0.30,
        "flatten_lighting": True,
        "flatten_strength": 0.35,
        "max_illum_side": 512,
        "sharpen": True,
        "sharpen_amount": 0.28,
        "denoise": True,
    },
    "detect": {
        "engine": "auto",  # auto | yolo | cv
        "model_id": "mosesb/best-comic-panel-detection",
        "model_filename": "best.pt",
        "model_revision": None,  # pin a commit sha for reproducible detection
        "conf": 0.25,
        "iou": 0.45,
        "min_area_frac": 0.012,
        "max_area_frac": 0.96,
        "nms_iou": 0.55,
        "min_coverage": 0.35,
        "low_score": 0.35,
    },
    "ingest": {
        "pdf_dpi": 300,
        # Scanned PDFs are one full-page image per page; lift it out byte-for-byte
        # instead of rasterizing, which would cost a resample.
        "pdf_extract_embedded": True,
    },
    "heuristics": {
        "reaction_area_frac": 0.08,
        "reaction_scale": 0.78,
        "splash_area_frac": 0.55,
        "page_break_gutter": "large",
    },
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


def deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config at {path} must be a mapping")
    return data


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=True)


def resolve_config(project_dir: Path, cli_overrides: dict | None = None) -> dict:
    cfg = deepcopy(DEFAULTS)
    cfg = deep_merge(cfg, load_yaml(project_dir / "config.yaml"))
    if cli_overrides:
        cfg = deep_merge(cfg, cli_overrides)
    return cfg
