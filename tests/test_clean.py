from __future__ import annotations

import cv2
import numpy as np

from scrollstrip.clean import (
    crop_scanner_border,
    _downscale_max_side,
    _illumination_field,
    clean_image,
    flatten_lighting,
)
from scrollstrip.config import DEFAULTS, deep_merge


def comic_page(w: int = 600, h: int = 800) -> np.ndarray:
    """Saturated printed art, which is what the content mask looks for.

    A grey gradient is deliberately NOT this: unsaturated light pixels are how
    blank paper and the scanner bed read, so the crop must ignore them.
    """
    page = np.full((h, w, 3), 245, dtype=np.uint8)
    colors = [(40, 180, 60), (200, 60, 40), (40, 90, 210), (30, 200, 210)]
    rows, cols = 3, 2
    for r in range(rows):
        for c in range(cols):
            y0, y1 = int(h * r / rows) + 6, int(h * (r + 1) / rows) - 6
            x0, x1 = int(w * c / cols) + 6, int(w * (c + 1) / cols) - 6
            page[y0:y1, x0:x1] = colors[(r * cols + c) % len(colors)]
            cv2.rectangle(page, (x0, y0), (x1, y1), (15, 15, 15), 4)
    return page


def gradient_page(w: int = 900, h: int = 1200) -> np.ndarray:
    """A page with uneven lighting: bright top-left falling off to the corner."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    illum = 120 + 100 * (1.0 - (xx / w) * 0.6 - (yy / h) * 0.6)
    page = np.dstack([illum, illum, illum]).clip(0, 255).astype(np.uint8)
    # some panel-ish content so deskew/crop have edges to find
    cv2.rectangle(page, (80, 100), (w - 80, h // 2 - 40), (20, 20, 20), 6)
    cv2.rectangle(page, (80, h // 2 + 40), (w - 80, h - 100), (20, 20, 20), 6)
    return page


def test_illumination_field_approximation_matches_exact_blur():
    """The downscaled field must track the full-resolution blur closely."""
    page = gradient_page()
    sigma = max(25.0, min(page.shape[:2]) / 18.0)
    exact = _illumination_field(page, sigma, max_side=10_000)  # forces the true blur
    approx = _illumination_field(page, sigma, max_side=512)
    diff = np.abs(exact.astype(np.float32) - approx.astype(np.float32))
    assert diff.mean() < 1.5, f"mean illumination error too high: {diff.mean()}"
    assert np.percentile(diff, 99) < 5.0


def test_flatten_lighting_evens_out_a_gradient():
    page = gradient_page()
    before = float(page[:, :, 0].std())
    after = float(flatten_lighting(page, 1.0)[:, :, 0].std())
    assert after < before


def test_flatten_lighting_zero_strength_is_identity():
    page = gradient_page()
    assert np.array_equal(flatten_lighting(page, 0.0), page)


def test_flatten_lighting_handles_image_smaller_than_max_side():
    small = gradient_page(120, 160)
    out = flatten_lighting(small, 0.35, max_illum_side=512)
    assert out.shape == small.shape


def test_clean_image_respects_working_max_side():
    page = gradient_page(3000, 4000)
    cfg = deep_merge(DEFAULTS, {"working_max_side": 800})
    out = clean_image(page, cfg)
    assert max(out.shape[:2]) <= 800


def test_clean_image_crops_border_before_downscaling():
    """Content resolution must survive a big scanner border.

    Cropping after the downscale would leave the comic itself far below
    working_max_side; cropping first keeps it at the cap.
    """
    content = gradient_page(1000, 1400)
    page = np.full((2800, 2000, 3), 255, dtype=np.uint8)  # wide white scanner bed
    page[700:2100, 500:1500] = content
    cfg = deep_merge(DEFAULTS, {"working_max_side": 1400, "clean": {"deskew": False}})
    out = clean_image(page, cfg)
    # the 1400-tall content should still be near the cap, not scaled down twice
    assert max(out.shape[:2]) >= 1300


def test_clean_image_does_not_upscale_small_pages():
    page = gradient_page(400, 600)
    out = clean_image(page, deep_merge(DEFAULTS, {"working_max_side": 2800}))
    assert max(out.shape[:2]) <= 620  # deskew may pad slightly, never 2800


def test_crop_removes_plain_scanner_margin():
    page = np.full((1000, 900, 3), 250, dtype=np.uint8)
    page[100:900, 150:750] = comic_page(600, 800)
    out = crop_scanner_border(page, padding=4)
    assert out.shape[0] < 900 and out.shape[1] < 800


def test_crop_drops_facing_page_bleed_across_the_binding_gap():
    """A flatbed scan often catches a strip of the opposite page.

    It is separated from the real page by the blank binding gap, so the crop
    should keep the larger block and discard the strip. Taking the outermost
    content pixel instead would hold the crop open across the whole scan.
    """
    page = np.full((1000, 1000, 3), 250, dtype=np.uint8)
    page[100:900, 60:200] = comic_page(140, 800)  # bleed strip, left
    page[100:900, 380:950] = comic_page(570, 800)  # the actual page
    out = crop_scanner_border(page, padding=4)
    assert out.shape[1] < 700, f"bleed strip not dropped, width {out.shape[1]}"
    assert out.shape[1] > 450, f"cropped into the page, width {out.shape[1]}"


def test_crop_returns_original_when_page_fills_the_scan():
    page = comic_page(800, 1100)
    out = crop_scanner_border(page, padding=4)
    assert out.shape[0] >= 1000 and out.shape[1] >= 700


def test_downscale_is_a_noop_under_the_cap():
    page = gradient_page(300, 400)
    assert _downscale_max_side(page, 2800) is page
