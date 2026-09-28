from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scrollstrip.config import DEFAULTS, deep_merge
from scrollstrip.ingest import IngestError, collect_pages, natural_key_str

CFG = deep_merge(DEFAULTS, {})


def make_png(path: Path, w: int = 60, h: int = 90, color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path)
    return path


def png_bytes(w: int = 60, h: int = 90) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 90, 200)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- sort key


def test_natural_sort_orders_9_before_10():
    names = ["p10.jpg", "p9.jpg", "p1.jpg"]
    assert sorted(names, key=natural_key_str) == ["p1.jpg", "p9.jpg", "p10.jpg"]


def test_natural_sort_mixes_alpha_and_numeric_without_crashing():
    """A CBZ with cover.jpg next to 001.jpg must not raise TypeError."""
    names = ["001.jpg", "cover.jpg", "002.jpg"]
    assert sorted(names, key=natural_key_str) == ["001.jpg", "002.jpg", "cover.jpg"]


def test_natural_sort_uses_full_path_not_just_stem():
    names = ["b/01.jpg", "a/01.jpg", "a/02.jpg"]
    assert sorted(names, key=natural_key_str) == ["a/01.jpg", "a/02.jpg", "b/01.jpg"]


# ---------------------------------------------------------------- folders


def test_folder_flat(tmp_path):
    src = tmp_path / "src"
    for n in ["002.png", "001.png", "010.png"]:
        make_png(src / n)
    got = collect_pages(src, tmp_path / "stage", CFG)
    assert [p.name for p in got] == ["001.png", "002.png", "010.png"]


def test_folder_recurses_into_subdirectories(tmp_path):
    src = tmp_path / "src"
    make_png(src / "ch01" / "002.png")
    make_png(src / "ch01" / "001.png")
    got = collect_pages(src, tmp_path / "stage", CFG)
    assert [p.name for p in got] == ["001.png", "002.png"]


def test_folder_with_no_images_raises(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "notes.txt").write_text("hi")
    with pytest.raises(IngestError):
        collect_pages(src, tmp_path / "stage", CFG)


# ---------------------------------------------------------------- cbz / zip


def test_cbz_extracts_in_order(tmp_path):
    cbz = tmp_path / "book.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        for n in ["p10.png", "p2.png", "p1.png"]:
            zf.writestr(n, png_bytes())
    got = collect_pages(cbz, tmp_path / "stage", CFG)
    assert [p.name for p in got] == ["p1.png", "p2.png", "p10.png"]


def test_cbz_with_cover_and_numbered_pages(tmp_path):
    """The layout that crashes the old sort key."""
    cbz = tmp_path / "book.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        for n in ["001.png", "cover.png", "002.png"]:
            zf.writestr(n, png_bytes())
    got = collect_pages(cbz, tmp_path / "stage", CFG)
    assert [p.name for p in got] == ["001.png", "002.png", "cover.png"]


def test_cbz_skips_junk_entries(tmp_path):
    cbz = tmp_path / "book.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("001.png", png_bytes())
        zf.writestr("__MACOSX/._001.png", b"junk")
        zf.writestr(".DS_Store", b"junk")
        zf.writestr("Thumbs.db", b"junk")
        zf.writestr("ComicInfo.xml", b"<xml/>")
        zf.writestr("002.png", png_bytes())
    got = collect_pages(cbz, tmp_path / "stage", CFG)
    assert [p.name for p in got] == ["001.png", "002.png"]


def test_cbz_nested_directory(tmp_path):
    cbz = tmp_path / "book.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("Book Name/001.png", png_bytes())
        zf.writestr("Book Name/002.png", png_bytes())
    got = collect_pages(cbz, tmp_path / "stage", CFG)
    assert [p.name for p in got] == ["001.png", "002.png"]


def test_cbz_rejects_path_traversal(tmp_path):
    cbz = tmp_path / "evil.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("../escaped.png", png_bytes())
        zf.writestr("001.png", png_bytes())
    stage = tmp_path / "stage"
    got = collect_pages(cbz, stage, CFG)
    assert [p.name for p in got] == ["001.png"]
    assert not (tmp_path / "escaped.png").exists()


def test_plain_zip_is_accepted(tmp_path):
    z = tmp_path / "pages.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("001.png", png_bytes())
    assert len(collect_pages(z, tmp_path / "stage", CFG)) == 1


def test_empty_archive_raises(tmp_path):
    cbz = tmp_path / "empty.cbz"
    with zipfile.ZipFile(cbz, "w") as zf:
        zf.writestr("readme.txt", b"nothing here")
    with pytest.raises(IngestError):
        collect_pages(cbz, tmp_path / "stage", CFG)


def test_corrupt_archive_raises_ingest_error(tmp_path):
    bad = tmp_path / "broken.cbz"
    bad.write_bytes(b"this is not a zip file")
    with pytest.raises(IngestError):
        collect_pages(bad, tmp_path / "stage", CFG)


# ---------------------------------------------------------------- pdf


def make_pdf_vector(path: Path, pages: int = 3) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        page.insert_text((50, 100), f"page {i + 1}", fontsize=30)
    doc.save(path)
    doc.close()
    return path


def make_pdf_scanned(path: Path, pages: int = 2, w: int = 400, h: int = 600) -> Path:
    """Each page is one full-page embedded raster, like a real scan."""
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        buf = io.BytesIO()
        arr = np.full((h, w, 3), (30 + i * 40) % 255, dtype=np.uint8)
        Image.fromarray(arr).save(buf, format="PNG")
        page = doc.new_page(width=w, height=h)
        page.insert_image(pymupdf.Rect(0, 0, w, h), stream=buf.getvalue())
    doc.save(path)
    doc.close()
    return path


def test_pdf_rasterizes_vector_pages(tmp_path):
    pdf = make_pdf_vector(tmp_path / "book.pdf", pages=3)
    got = collect_pages(pdf, tmp_path / "stage", CFG)
    assert len(got) == 3
    assert [p.stem for p in got] == ["0001", "0002", "0003"]
    for p in got:
        assert Image.open(p).size[0] > 300  # rendered above 72dpi


def test_pdf_dpi_is_configurable(tmp_path):
    pdf = make_pdf_vector(tmp_path / "book.pdf", pages=1)
    lo = collect_pages(pdf, tmp_path / "s1", deep_merge(CFG, {"ingest": {"pdf_dpi": 72}}))
    hi = collect_pages(pdf, tmp_path / "s2", deep_merge(CFG, {"ingest": {"pdf_dpi": 300}}))
    assert Image.open(hi[0]).size[0] > Image.open(lo[0]).size[0] * 3


def test_pdf_extracts_embedded_scan_at_native_resolution(tmp_path):
    pdf = make_pdf_scanned(tmp_path / "scan.pdf", pages=2, w=400, h=600)
    cfg = deep_merge(CFG, {"ingest": {"pdf_extract_embedded": True, "pdf_dpi": 72}})
    got = collect_pages(pdf, tmp_path / "stage", cfg)
    assert len(got) == 2
    assert Image.open(got[0]).size == (400, 600)


def test_pdf_embedded_extraction_can_be_disabled(tmp_path):
    pdf = make_pdf_scanned(tmp_path / "scan.pdf", pages=1, w=400, h=600)
    cfg = deep_merge(CFG, {"ingest": {"pdf_extract_embedded": False, "pdf_dpi": 144}})
    got = collect_pages(pdf, tmp_path / "stage", cfg)
    # 144dpi render of a 400pt-wide page = 800px, not the native 400
    assert Image.open(got[0]).size[0] == 800


def test_pdf_single_blank_page_does_not_crash(tmp_path):
    import pymupdf

    pdf = tmp_path / "blank.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    assert len(collect_pages(pdf, tmp_path / "stage", CFG)) == 1


# ---------------------------------------------------------------- dispatch


def test_unsupported_extension_raises(tmp_path):
    bad = tmp_path / "book.cbr"
    bad.write_bytes(b"rar!")
    with pytest.raises(IngestError, match="cbr"):
        collect_pages(bad, tmp_path / "stage", CFG)


def test_missing_source_raises(tmp_path):
    with pytest.raises(IngestError):
        collect_pages(tmp_path / "nope.cbz", tmp_path / "stage", CFG)
