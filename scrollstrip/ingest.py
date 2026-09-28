"""Turn whatever the reader owns — a folder, a CBZ, a PDF — into ordered page images.

Everything downstream (clean, detect, review, assemble) only ever sees a list of
image files, so this is the one module that knows about container formats.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from .config import IMAGE_EXTS

ARCHIVE_EXTS = {".cbz", ".zip"}
PDF_EXTS = {".pdf"}

# Archive members that are never comic pages.
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
JUNK_DIR_PARTS = {"__macosx"}


class IngestError(Exception):
    """Raised when a source cannot be turned into a list of pages."""


def natural_key_str(name: str) -> tuple:
    """Sort key that orders p9 before p10 and never compares int to str.

    Each element is (type_rank, number, text) so a numeric chunk and a text
    chunk are always comparable. The older key mixed bare ints and strs in one
    tuple, which raised TypeError on the very common `cover.jpg` + `001.jpg`
    archive layout.
    """
    name = name.replace("\\", "/").lower()
    parts: list[tuple[int, int, str]] = []
    buf = ""
    for ch in name:
        if ch.isdigit():
            buf += ch
        else:
            if buf:
                parts.append((0, int(buf), ""))
                buf = ""
            parts.append((1, 0, ch))
    if buf:
        parts.append((0, int(buf), ""))
    return tuple(parts)


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


def _is_junk(name: str) -> bool:
    norm = name.replace("\\", "/")
    lower = norm.lower()
    if any(part in JUNK_DIR_PARTS for part in lower.split("/")):
        return True
    base = lower.rsplit("/", 1)[-1]
    return base in JUNK_NAMES or base.startswith("._") or base.startswith(".")


def _safe_member_path(name: str, staging: Path) -> Path | None:
    """Resolve an archive entry under `staging`, or None if it escapes."""
    norm = name.replace("\\", "/")
    # absolute posix path, or a Windows drive letter like "C:/..."
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":"):
        return None
    candidate = (staging / norm).resolve()
    try:
        candidate.relative_to(staging.resolve())
    except ValueError:
        return None
    return candidate


def _fresh(staging: Path) -> Path:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    return staging


# ------------------------------------------------------------------ sources


def _from_folder(folder: Path) -> list[Path]:
    files = [
        p
        for p in folder.rglob("*")
        if p.is_file() and _is_image(p.name) and not _is_junk(str(p.relative_to(folder)))
    ]
    if not files:
        raise IngestError(f"No images found in folder: {folder}")
    return sorted(files, key=lambda p: natural_key_str(str(p.relative_to(folder)).replace("\\", "/")))


def _from_archive(archive: Path, staging: Path) -> list[Path]:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise IngestError(f"{archive.name} is not a readable zip/CBZ: {exc}") from exc

    with zf:
        members = [
            info
            for info in zf.infolist()
            if not info.is_dir() and _is_image(info.filename) and not _is_junk(info.filename)
        ]
        if not members:
            raise IngestError(f"No image entries inside {archive.name}")
        members.sort(key=lambda i: natural_key_str(i.filename))

        _fresh(staging)
        out: list[Path] = []
        for index, info in enumerate(members, start=1):
            dest = _safe_member_path(info.filename, staging)
            if dest is None:
                print(f"  skipping unsafe archive entry: {info.filename}")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dest.open("wb") as handle:
                shutil.copyfileobj(src, handle)
            out.append(dest)

    if not out:
        raise IngestError(f"Every entry in {archive.name} was rejected as unsafe")
    return out


def _page_is_one_full_image(page, doc) -> tuple[bytes, str] | None:
    """Return the embedded raster if this page is a single full-bleed image.

    Scanned comics are almost always one image per page. Pulling that image out
    byte-for-byte avoids a resample that rasterizing would cost us.
    """
    images = page.get_images(full=True)
    if len(images) != 1:
        return None
    xref = images[0][0]
    try:
        rects = page.get_image_rects(xref)
    except Exception:  # noqa: BLE001 - fall back to rasterizing
        return None
    if len(rects) != 1:
        return None
    page_area = abs(page.rect.get_area())
    if page_area <= 0 or abs(rects[0].get_area()) / page_area < 0.9:
        return None
    try:
        extracted = doc.extract_image(xref)
    except Exception:  # noqa: BLE001 - fall back to rasterizing
        return None
    ext = extracted.get("ext", "png").lower()
    if f".{ext}" not in IMAGE_EXTS:
        return None
    return extracted["image"], ext


def _from_pdf(pdf: Path, staging: Path, cfg: dict) -> list[Path]:
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise IngestError(
            "Reading PDFs needs PyMuPDF. Install it with:  pip install pymupdf"
        ) from exc

    ingest_cfg = cfg.get("ingest", {})
    dpi = int(ingest_cfg.get("pdf_dpi", 300))
    prefer_embedded = bool(ingest_cfg.get("pdf_extract_embedded", True))

    try:
        doc = pymupdf.open(pdf)
    except Exception as exc:  # noqa: BLE001 - surface any reader failure uniformly
        raise IngestError(f"Could not open PDF {pdf.name}: {exc}") from exc

    _fresh(staging)
    out: list[Path] = []
    with doc:
        if doc.page_count == 0:
            raise IngestError(f"{pdf.name} has no pages")
        for index, page in enumerate(doc, start=1):
            stem = f"{index:04d}"
            embedded = _page_is_one_full_image(page, doc) if prefer_embedded else None
            if embedded is not None:
                data, ext = embedded
                dest = staging / f"{stem}.{ext}"
                dest.write_bytes(data)
            else:
                dest = staging / f"{stem}.png"
                page.get_pixmap(dpi=dpi).save(dest)
            out.append(dest)

    if not out:
        raise IngestError(f"No pages extracted from {pdf.name}")
    return out


# ------------------------------------------------------------------ entry point


def describe_source(source: Path) -> str:
    if source.is_dir():
        return "folder"
    suffix = source.suffix.lower()
    if suffix in ARCHIVE_EXTS:
        return "archive"
    if suffix in PDF_EXTS:
        return "pdf"
    return "unsupported"


def collect_pages(source: Path, staging: Path, cfg: dict) -> list[Path]:
    """Return page images from `source`, in reading order.

    Folders are read in place. Archives and PDFs are unpacked into `staging`,
    which is wiped first and is the caller's to clean up.
    """
    source = Path(source)
    staging = Path(staging)

    if source.is_dir():
        return _from_folder(source)

    if not source.exists():
        raise IngestError(f"Source does not exist: {source}")

    suffix = source.suffix.lower()
    if suffix in ARCHIVE_EXTS:
        return _from_archive(source, staging)
    if suffix in PDF_EXTS:
        return _from_pdf(source, staging, cfg)

    if suffix in {".cbr", ".rar", ".cb7", ".7z"}:
        raise IngestError(
            f"{suffix.lstrip('.')} archives are not supported. "
            f"Convert {source.name} to .cbz first (most comic readers can), then re-run."
        )
    raise IngestError(
        f"Unsupported source '{source.name}'. Use a folder of images, a .cbz/.zip, or a .pdf."
    )
