from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .clean import _read, _write_jpeg
from .errors import JobCancelled
from .project import load_project, save_project

_YOLO_MODEL = None
_YOLO_PATH = None


def nms_boxes(boxes: list[list[int]], scores: list[float], iou_thresh: float) -> list[int]:
    if not boxes:
        return []
    arr = np.array(boxes, dtype=np.float32)
    x1, y1 = arr[:, 0], arr[:, 1]
    x2, y2 = arr[:, 0] + arr[:, 2], arr[:, 1] + arr[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = np.argsort(np.array(scores))[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[1:][iou <= iou_thresh]
    return keep


def box_iou(a: list[int], b: list[int]) -> float:
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


def _coverage(detections: list[tuple[list[int], float]], image: np.ndarray) -> float:
    """Fraction of the page covered by boxes, counting overlaps once."""
    if not detections:
        return 0.0
    h, w = image.shape[:2]
    # A coarse mask is plenty for a go/no-go signal and costs nothing.
    gh, gw = 128, max(1, int(round(128 * w / max(h, 1))))
    mask = np.zeros((gh, gw), dtype=bool)
    for box, _ in detections:
        x0 = max(0, int(box[0] * gw / w))
        y0 = max(0, int(box[1] * gh / h))
        x1 = min(gw, int(round((box[0] + box[2]) * gw / w)))
        y1 = min(gh, int(round((box[1] + box[3]) * gh / h)))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return float(mask.mean())


def clip_box(box: list[int], w: int, h: int) -> list[int] | None:
    x, y, bw, bh = [int(round(v)) for v in box]
    x = max(0, x)
    y = max(0, y)
    bw = min(bw, w - x)
    bh = min(bh, h - y)
    if bw < 8 or bh < 8:
        return None
    return [x, y, bw, bh]


def reading_order(boxes: list[list[int]]) -> list[int]:
    """Western comics: top-to-bottom rows, left-to-right within a row."""
    if not boxes:
        return []
    heights = [b[3] for b in boxes]
    row_tol = max(24.0, float(np.median(heights)) * 0.55)
    items = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0]))
    rows: list[list[int]] = []
    for idx in items:
        cy = boxes[idx][1] + boxes[idx][3] / 2.0
        placed = False
        for row in rows:
            row_cy = np.mean([boxes[j][1] + boxes[j][3] / 2.0 for j in row])
            if abs(cy - row_cy) <= row_tol:
                row.append(idx)
                placed = True
                break
        if not placed:
            rows.append([idx])
    ordered: list[int] = []
    for row in rows:
        row.sort(key=lambda i: boxes[i][0])
        ordered.extend(row)
    return ordered


def ensure_yolo_weights(project_dir: Path, cfg: dict) -> Path:
    detect_cfg = cfg.get("detect", {})
    dest = project_dir / "models" / detect_cfg.get("model_filename", "best.pt")
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    model_id = detect_cfg.get("model_id", "mosesb/best-comic-panel-detection")
    filename = detect_cfg.get("model_filename", "best.pt")
    try:
        from huggingface_hub import hf_hub_download

        downloaded = hf_hub_download(
            repo_id=model_id,
            filename=filename,
            revision=detect_cfg.get("model_revision") or None,
        )
        import shutil

        shutil.copy2(downloaded, dest)
        return dest
    except Exception:
        url = f"https://huggingface.co/{model_id}/resolve/main/{filename}"
        import urllib.request

        print(f"Downloading YOLO weights from {url}")
        urllib.request.urlretrieve(url, dest)
        return dest


def load_yolo(project_dir: Path, cfg: dict):
    global _YOLO_MODEL, _YOLO_PATH
    weights = ensure_yolo_weights(project_dir, cfg)
    if _YOLO_MODEL is not None and _YOLO_PATH == str(weights):
        return _YOLO_MODEL
    from ultralytics import YOLO

    _YOLO_MODEL = YOLO(str(weights))
    _YOLO_PATH = str(weights)
    return _YOLO_MODEL


def detect_yolo(image: np.ndarray, project_dir: Path, cfg: dict) -> list[tuple[list[int], float]]:
    model = load_yolo(project_dir, cfg)
    detect_cfg = cfg.get("detect", {})
    results = model.predict(
        image,
        conf=float(detect_cfg.get("conf", 0.25)),
        iou=float(detect_cfg.get("iou", 0.45)),
        verbose=False,
    )
    out: list[tuple[list[int], float]] = []
    if not results:
        return out
    boxes = results[0].boxes
    if boxes is None:
        return out
    xyxy = boxes.xyxy.cpu().numpy()
    confs = boxes.conf.cpu().numpy()
    h, w = image.shape[:2]
    for row, score in zip(xyxy, confs):
        x1, y1, x2, y2 = row.tolist()
        box = clip_box([x1, y1, x2 - x1, y2 - y1], w, h)
        if box:
            out.append((box, float(score)))
    return out


def detect_cv(image: np.ndarray, cfg: dict) -> list[tuple[list[int], float]]:
    """Contour fallback when YOLO is unavailable or finds nothing."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    # Dark gutters / borders vs paper. Try both polarities and keep stronger set.
    candidates: list[tuple[list[int], float]] = []
    h, w = image.shape[:2]
    page_area = float(h * w)
    min_frac = float(cfg.get("detect", {}).get("min_area_frac", 0.012))
    max_frac = float(cfg.get("detect", {}).get("max_area_frac", 0.96))

    for invert in (True, False):
        if invert:
            thresh = cv2.adaptiveThreshold(
                blur, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 35, 8
            )
        else:
            _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            thresh = 255 - thresh
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = bw * bh
            frac = area / page_area
            if frac < min_frac or frac > max_frac:
                continue
            if bw < 40 or bh < 40:
                continue
            aspect = bw / float(bh)
            if aspect > 12 or aspect < 0.08:
                continue
            box = clip_box([x, y, bw, bh], w, h)
            if box:
                candidates.append((box, min(0.9, 0.35 + frac)))

    if not candidates:
        return []
    boxes = [c[0] for c in candidates]
    scores = [c[1] for c in candidates]
    keep = nms_boxes(boxes, scores, float(cfg.get("detect", {}).get("nms_iou", 0.55)))
    return [candidates[i] for i in keep]


def filter_boxes(
    detections: list[tuple[list[int], float]],
    w: int,
    h: int,
    cfg: dict,
) -> list[tuple[list[int], float]]:
    min_frac = float(cfg.get("detect", {}).get("min_area_frac", 0.012))
    max_frac = float(cfg.get("detect", {}).get("max_area_frac", 0.96))
    page_area = float(w * h)
    kept: list[tuple[list[int], float]] = []
    oversized: list[tuple[list[int], float]] = []
    for box, score in detections:
        frac = (box[2] * box[3]) / page_area
        if frac > max_frac:
            oversized.append((box, score))
            continue
        if frac < min_frac:
            continue
        kept.append((box, score))

    # A full-bleed splash is one box covering the whole page, which trips the
    # max_area filter. Keep the largest such box rather than dropping the page.
    # (This used to return every rejected detection, unfiltered and without NMS.)
    if not kept and oversized:
        kept = [max(oversized, key=lambda item: item[0][2] * item[0][3])]

    if not kept:
        return []
    boxes = [k[0] for k in kept]
    scores = [k[1] for k in kept]
    idx = nms_boxes(boxes, scores, float(cfg.get("detect", {}).get("nms_iou", 0.55)))
    return [kept[i] for i in idx]


def infer_kind(boxes: list[list[int]], w: int, h: int) -> str:
    if not boxes:
        return "splash"
    page_area = float(w * h)
    if len(boxes) == 1:
        frac = (boxes[0][2] * boxes[0][3]) / page_area
        if w > h * 1.15 and frac > 0.55:
            return "spread"
        if frac > 0.62:
            return "splash"
    return "normal"


def infer_panel_fields(box: list[int], w: int, h: int, is_last: bool, cfg: dict) -> dict:
    heur = cfg.get("heuristics", {})
    area_frac = (box[2] * box[3]) / float(w * h)
    aspect = box[3] / float(max(box[2], 1))
    role = "normal"
    scale = 1.0
    gutter = cfg.get("default_gutter", "medium")
    if area_frac <= float(heur.get("reaction_area_frac", 0.08)) and aspect < 1.25:
        role = "reaction"
        scale = float(heur.get("reaction_scale", 0.78))
        gutter = "tight"
    if area_frac >= float(heur.get("splash_area_frac", 0.55)) or aspect > 1.7:
        role = "splash"
        gutter = "large"
    if is_last:
        gutter = heur.get("page_break_gutter", "large")
    return {"role": role, "scale": scale, "gutter_after": gutter}


def detections_to_panels(
    detections: list[tuple[list[int], float]],
    w: int,
    h: int,
    cfg: dict,
    page_id: str,
) -> list[dict]:
    boxes = [d[0] for d in detections]
    order = reading_order(boxes)
    panels = []
    for seq, idx in enumerate(order):
        box, score = detections[idx]
        fields = infer_panel_fields(box, w, h, is_last=(seq == len(order) - 1), cfg=cfg)
        panels.append(
            {
                "id": f"{page_id}-p{seq + 1:02d}",
                "bbox": box,
                "order": seq,
                "score": round(score, 3),
                "role": fields["role"],
                "scale": fields["scale"],
                "gutter_after": fields["gutter_after"],
                "locked": False,
            }
        )
    return panels


def draw_preview(image: np.ndarray, panels: list[dict]) -> np.ndarray:
    vis = image.copy()
    for panel in panels:
        x, y, w, h = panel["bbox"]
        color = {
            "reaction": (80, 180, 255),
            "splash": (80, 80, 255),
            "normal": (60, 220, 90),
        }.get(panel.get("role", "normal"), (60, 220, 90))
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 3)
        label = f"{panel['order'] + 1} {panel.get('role', '')}"
        cv2.putText(
            vis,
            label,
            (x + 6, y + 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            color,
            2,
            cv2.LINE_AA,
        )
    return vis


def detect_page(image: np.ndarray, project_dir: Path, cfg: dict) -> list[tuple[list[int], float]]:
    engine = str(cfg.get("detect", {}).get("engine", "auto")).lower()
    detections: list[tuple[list[int], float]] = []
    yolo_error = None
    if engine in {"auto", "yolo"}:
        try:
            detections = detect_yolo(image, project_dir, cfg)
        except Exception as exc:  # noqa: BLE001 — first-pass tool should keep going
            yolo_error = exc
            if engine == "yolo":
                raise
    # Judge the first pass by how much of the page it accounts for, not by box
    # count: a splash page legitimately has one box, while a failed page has one
    # stray box. Comic pages are mostly panel, so low coverage means failure.
    min_coverage = float(cfg.get("detect", {}).get("min_coverage", 0.35))
    if engine == "cv" or (engine == "auto" and _coverage(detections, image) < min_coverage):
        cv_det = detect_cv(image, cfg)
        if _coverage(cv_det, image) > _coverage(detections, image):
            detections = cv_det
        if yolo_error and not detections:
            print(f"YOLO failed ({yolo_error}); CV detector also found nothing")
    h, w = image.shape[:2]
    return filter_boxes(detections, w, h, cfg)


def detect_project(
    project_dir: Path,
    cfg: dict,
    overwrite_unlocked: bool = True,
    *,
    progress=None,
    should_cancel=None,
) -> dict:
    project = load_project(project_dir)
    quality = int(cfg.get("jpeg_quality", 92))
    total = len(project["pages"])
    for index, page in enumerate(project["pages"], start=1):
        if should_cancel is not None and should_cancel():
            raise JobCancelled(f"Cancelled after {index - 1} of {total} pages")
        cleaned = project_dir / page["cleaned"]
        if not cleaned.exists():
            raise FileNotFoundError(f"Cleaned page missing: {cleaned}. Run clean first.")
        image = _read(cleaned)
        h, w = image.shape[:2]
        page["width"] = w
        page["height"] = h
        locked = [p for p in page.get("panels", []) if p.get("locked")]
        # --keep-edits leaves any already-detected page untouched. It used to skip
        # only pages with NO locked boxes, so locking one box discarded every
        # other manual edit on that page.
        if page.get("panels") and not overwrite_unlocked:
            continue
        detections = detect_page(image, project_dir, cfg)
        auto_panels = detections_to_panels(detections, w, h, cfg, page["id"])
        if locked:
            # Keep locked boxes; replace the rest.
            auto_panels = locked + [p for p in auto_panels if not _overlaps_any(p, locked)]
            for i, panel in enumerate(auto_panels):
                panel["order"] = i
                panel["id"] = f"{page['id']}-p{i + 1:02d}"
        page["panels"] = auto_panels
        page["kind"] = infer_kind([p["bbox"] for p in auto_panels], w, h)
        page["status"] = "detected"
        page["needs_review"] = _looks_doubtful(auto_panels, detections, image, cfg)
        preview = draw_preview(image, auto_panels)
        _write_jpeg(project_dir / page["preview"], preview, quality)
        if progress is not None:
            progress(index, total, f"Detected {len(auto_panels)} panels on {page['id']}")
    flagged = [p["id"] for p in project["pages"] if p.get("needs_review")]
    if flagged:
        print(f"{len(flagged)} page(s) to check first: {', '.join(flagged)}")
    save_project(project_dir, project)
    return project


def _looks_doubtful(panels, detections, image, cfg) -> bool:
    """True when detection probably failed, so review can start where it matters.

    A one-panel page is normal (a splash), so the test is how much of the page the
    boxes account for and how confident they are - not how many there are.
    """
    if not panels:
        return True
    if _coverage(detections, image) < float(cfg.get("detect", {}).get("min_coverage", 0.35)):
        return True
    return min(p.get("score", 1.0) for p in panels) < float(
        cfg.get("detect", {}).get("low_score", 0.35)
    )


def _overlaps_any(panel: dict, locked: list[dict], thresh: float = 0.45) -> bool:
    return any(box_iou(panel["bbox"], other["bbox"]) >= thresh for other in locked)
