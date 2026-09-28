from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .errors import JobCancelled
from .project import load_project, resolve_page_file, save_project


def _read(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return image


def _write_jpeg(path: Path, image: np.ndarray, quality: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError(f"Failed to encode {path}")
    buf.tofile(str(path))


def _downscale_max_side(image: np.ndarray, max_side: int) -> np.ndarray:
    h, w = image.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return image
    scale = max_side / float(longest)
    new_size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def estimate_skew_degrees(image: np.ndarray, max_degrees: float) -> float:
    """Estimate small page tilt from long near-axis edges (panel borders)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(80, image.shape[1] // 8),
        minLineLength=min(image.shape[:2]) // 6,
        maxLineGap=20,
    )
    if lines is None:
        return 0.0
    angles = []
    for line in np.asarray(lines).reshape(-1, 4):
        x1, y1, x2, y2 = (int(v) for v in line)
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0:
            continue
        angle = np.degrees(np.arctan2(dy, dx))
        # Snap to nearest horizontal or vertical, keep residual tilt.
        while angle > 45:
            angle -= 90
        while angle < -45:
            angle += 90
        if abs(angle) <= max_degrees:
            angles.append(angle)
    if len(angles) < 6:
        return 0.0
    return float(np.median(angles))


def rotate_bound(image: np.ndarray, angle: float, fill: tuple[int, int, int]) -> np.ndarray:
    h, w = image.shape[:2]
    center = (w / 2.0, h / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    nw = int(h * sin + w * cos)
    nh = int(h * cos + w * sin)
    matrix[0, 2] += nw / 2.0 - center[0]
    matrix[1, 2] += nh / 2.0 - center[1]
    return cv2.warpAffine(
        image,
        matrix,
        (nw, nh),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=fill,
    )


def _longest_run(flags: np.ndarray) -> tuple[int, int] | None:
    """Start/end of the longest True run, or None."""
    if not flags.any():
        return None
    padded = np.concatenate(([False], flags, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    starts, ends = edges[0::2], edges[1::2]
    best = int(np.argmax(ends - starts))
    return int(starts[best]), int(ends[best]) - 1


def content_mask(image: np.ndarray) -> np.ndarray:
    """Printed comic art: saturated colour or dark ink.

    Scanner bed and blank paper margin are neither, which is what separates the
    page from the bed and from the facing page bleeding into the scan.
    """
    hsv = cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0), cv2.COLOR_BGR2HSV)
    sat, val = hsv[:, :, 1], hsv[:, :, 2]
    mask = ((sat > 60) | (val < 140)).astype(np.uint8)
    # Pale colour and speech balloons are holes inside a panel. Close them so a
    # panel reads as one solid block and row/column runs do not fragment.
    k = max(3, (int(round(min(mask.shape[:2]) * 0.02)) | 1))
    return cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    )


def crop_scanner_border(
    image: np.ndarray, padding: int, min_fill: float = 0.08, min_area_kept: float = 0.30
) -> np.ndarray:
    """Trim scanner bed and any facing-page bleed down to the printed page.

    Uses the longest contiguous run of content-bearing rows/columns rather than
    the outermost edge pixel, so a sliver of the opposite page (separated from
    the real page by the binding gap) does not hold the crop open.
    """
    mask = content_mask(image)
    x_run = _longest_run(mask.mean(axis=0) > min_fill)
    if x_run is None:
        return image
    # Measure rows only across the page we just found. Averaging over the blank
    # margin too would dilute every row below the threshold.
    band = mask[:, x_run[0] : x_run[1] + 1]
    y_run = _longest_run(band.mean(axis=1) > min_fill)
    if y_run is None:
        return image
    x0, x1 = x_run
    y0, y1 = y_run
    x0 = max(0, x0 - padding)
    y0 = max(0, y0 - padding)
    x1 = min(image.shape[1] - 1, x1 + padding)
    y1 = min(image.shape[0] - 1, y1 + padding)
    cropped = image[y0 : y1 + 1, x0 : x1 + 1]
    # Refuse pathological crops (lost most of the page). The floor is well below
    # half because a scan holding a facing-page strip plus generous bed margin
    # legitimately drops more than half its area.
    if cropped.size == 0:
        return image
    if cropped.shape[0] * cropped.shape[1] < min_area_kept * image.shape[0] * image.shape[1]:
        return image
    return cropped


def _illumination_field(image: np.ndarray, sigma: float, max_side: int) -> np.ndarray:
    """Large-scale lighting across the page.

    The field is low-frequency by definition, so it is estimated on a small copy
    and scaled back up. Blurring at 1/k scale with sigma/k approximates blurring
    at full scale with sigma, and costs orders of magnitude less: a sigma-283
    blur over a 35MP scan was 96% of the entire clean stage.
    """
    h, w = image.shape[:2]
    scale = min(1.0, max_side / float(max(h, w)))
    if scale >= 1.0:
        return cv2.GaussianBlur(image, (0, 0), sigmaX=sigma)
    small = cv2.resize(
        image,
        (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    blurred = cv2.GaussianBlur(small, (0, 0), sigmaX=max(1.0, sigma * scale))
    return cv2.resize(blurred, (w, h), interpolation=cv2.INTER_LINEAR)


def flatten_lighting(image: np.ndarray, strength: float, max_illum_side: int = 512) -> np.ndarray:
    """Divide out large-scale illumination; blend so colors stay close to the scan."""
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0:
        return image
    sigma = max(25.0, min(image.shape[:2]) / 18.0)
    blur = _illumination_field(image, sigma, max_illum_side)
    blur = np.clip(blur.astype(np.float32), 1.0, 255.0)
    mean = float(np.mean(blur))
    corrected = image.astype(np.float32) / blur * mean
    corrected = np.clip(corrected, 0, 255)
    blended = image.astype(np.float32) * (1.0 - strength) + corrected * strength
    return blended.astype(np.uint8)


def sharpen_luminance(image: np.ndarray, amount: float) -> np.ndarray:
    amount = float(np.clip(amount, 0.0, 1.5))
    if amount <= 0:
        return image
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    blur = cv2.GaussianBlur(l, (0, 0), 1.2)
    sharp = cv2.addWeighted(l, 1.0 + amount, blur, -amount, 0)
    merged = cv2.merge((sharp, a, b))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def light_denoise(image: np.ndarray) -> np.ndarray:
    # Color denoise only; keep edges so lettering stays crisp.
    return cv2.bilateralFilter(image, d=5, sigmaColor=18, sigmaSpace=5)


def clean_image(image: np.ndarray, cfg: dict) -> np.ndarray:
    clean_cfg = cfg.get("clean", {})
    fill = tuple(int(c) for c in cfg.get("background", [18, 18, 18]))

    max_side = int(cfg.get("working_max_side", 2800))

    # Crop at full resolution first: it is cheap, and it keeps the comic content
    # itself at maximum resolution instead of scaling down scanner border too.
    if clean_cfg.get("crop_border", True):
        image = crop_scanner_border(
            image,
            int(clean_cfg.get("crop_padding", 8)),
            float(clean_cfg.get("crop_min_fill", 0.08)),
            float(clean_cfg.get("crop_min_area_kept", 0.30)),
        )

    # Then downscale, before anything expensive. Everything below used to run at
    # full scan resolution only for the result to be downscaled on the last line.
    image = _downscale_max_side(image, max_side)

    if clean_cfg.get("deskew", True):
        max_deg = float(clean_cfg.get("max_deskew_degrees", 6.0))
        min_deg = float(clean_cfg.get("min_deskew_degrees", 0.35))
        angle = estimate_skew_degrees(image, max_deg)
        if abs(angle) >= min_deg:
            image = rotate_bound(image, angle, fill)

    if clean_cfg.get("flatten_lighting", True):
        image = flatten_lighting(
            image,
            float(clean_cfg.get("flatten_strength", 0.35)),
            int(clean_cfg.get("max_illum_side", 512)),
        )

    if clean_cfg.get("denoise", True):
        image = light_denoise(image)

    if clean_cfg.get("sharpen", True):
        image = sharpen_luminance(image, float(clean_cfg.get("sharpen_amount", 0.28)))

    # Deskew expands the canvas slightly; clamp back. Usually a no-op.
    return _downscale_max_side(image, max_side)


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
