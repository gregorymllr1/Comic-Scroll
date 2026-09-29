<div align="center">
  <!-- You can add a logo image here if you have one -->
  <h1>📜 Scrollstrip</h1>
  <p><strong>Turn scanned comic pages into phone-ready vertical scrolls.</strong></p>
</div>

Scrollstrip is a first-pass pipeline that takes a folder of scanned comic images, a CBZ, or a PDF, and transforms them into a phone-width vertical CBZ (Webtoon format). 

Detection is fully automatic using a YOLOv12 model (with an OpenCV fallback). Because automation isn't perfect, the tool includes a local desktop app to easily clean up, scale, adjust gutters, and fix bad bounding boxes before final export.

> ⚠️ **Note:** Please use this tool only on comics you legally own, strictly for personal reading.

---

## ✨ How It Works

The pipeline is broken down into four automated and semi-automated steps:

1. 📥 **Ingest** — Reads pages from a folder, `.cbz`/`.zip`, or `.pdf`. Scanned PDF pages are lifted out at native resolution losslessly; other PDFs are rasterized at your defined `pdf_dpi`.
2. 🧼 **Clean** — Automatically crops scanner borders, applies small deskew corrections, evens out lighting, applies light denoising, and sharpens luminance. Colors remain true to the original scan, and lettering is not redrawn.
3. 🎯 **Detect** — Uses a YOLOv12 comic-panel model (`mosesb/best-comic-panel-detection`) alongside a contour fallback to find panels. Box coordinates are saved to `project.json`.
4. 🖌️️ **Review (Manual)** — Opens a local desktop UI to review the automated detection. Move, resize, add, or delete boxes, set scales/gutters, lock boxes, and mark pages as splash/spread/skip.
5. 🏗️ **Assemble** — Scales each panel (default 1080px width), stacks them with vertical gutters, slices the final strip into optimized segments, and zips them into a single CBZ per chapter.

---

## 🚀 Installation

Requires Python 3. `ultralytics` requires a working PyTorch installation (CPU is perfectly fine). 

```bash
# Clone the repository and enter the directory
cd scrollstrip

# Set up a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

*Note: The first time you run the `detect` command, the script will automatically download the YOLO weights (`best.pt`, ~119 MB) into the `models/` directory. If YOLO cannot load on your system, you can use `--engine cv`.*

---

## 📖 Quick Start: Chapter Workflow

Organize your source files so that one chapter is in one folder of scans, already in reading order (e.g., `001.jpg`, `002.jpg`, …).

**1. Run the initial pipeline:**
```bash
python -m scrollstrip run \
  --project ./chapter-01 \
  --pages ./scans/chapter-01 \
  --name "Watchmen-01"
```

**2. Review and fix in the UI:**
```bash
python -m scrollstrip.shell
```
*In the desktop app: Fix boxes, adjust scales/gutters, click **Save**, and then click **Assemble chapter**.*

*(Alternatively, assemble from the CLI after reviewing)*
```bash
python -m scrollstrip assemble --project ./chapter-01
```

**Output files generated:**
- `chapter-01/export/001.jpg`, `002.jpg`, …
- `chapter-01/export/Watchmen-01.cbz` *(Open this in a comic reader using **webtoon / continuous vertical / fit-width** mode)*
- `chapter-01/export/long-strip.jpg` *(Optional, generated if the chapter is not enormous)*

---

## 🖥️ Commands Reference

| Command | Purpose |
|---|---|
| `init --pages SOURCE` | Create project from a folder, `.cbz`/`.zip`, or `.pdf`. |
| `clean` | Process and write cleaned images to `work/cleaned/*.jpg`. |
| `detect` | Fill `panels[]` on each page. *(Optional: `--engine auto\|yolo\|cv`)* |
| `review` | Open the Scrollstrip desktop app. |
| `assemble` | Slice panels and generate the final CBZ. *(Optional: `--width 1080`)* |
| `run` | Runs `init` (optional) + `clean` + `detect` in sequence. |

**Important notes on initialization:**
* `--link` on `init`/`run` records original paths instead of copying files to save space (folder sources only). Archives and PDFs must be extracted, so `--link` is ignored with a warning.
* `CBR` and `CB7` are not supported — convert them to `CBZ` first. 
* Entries that attempt to escape the project directory, as well as `__MACOSX/`, `.DS_Store`, and `Thumbs.db`, are automatically skipped.

---

## 🎨 The Review UI (Design Step)

> **Pro-Tip:** If you have a GIF or screenshot of the UI in action, place it here!

Reviewing is done via the Scrollstrip desktop app (`python -m scrollstrip.shell`). Open a chapter from your library, fix the layout, and assemble.

Automatic boxes often struggle with insets, overlaps, borderless panels, speech balloons in the gutter, full-bleed splash pages, yellowed paper, and faint gutters. 

For pages that need correction, you can:
* **Adjust:** Drag a box or its white corners to resize.
* **Add/Delete:** Add missing panels or delete false positives.
* **Lock & Re-detect:** Lock the boxes you've manually fixed, then re-detect the rest of the page.
* **Set Page Kind:** Mark a page as `splash`, `spread`, or `skip`.

### Panel Roles & Gutters
Scrollstrip uses panel roles and gutter sizing to control the pacing of the vertical scroll. First-pass heuristics will attempt to guess these (e.g., tiny panels → reaction; huge panels → large gutter), but you can manually tune them:

**Roles:**
* `normal` — Full content width.
* `reaction` — Default scale 0.78 (Used for staccato moments or small inserts).
* `splash` — Full width, forces a large gutter afterward.

**Gutter After (The Vertical "Page Turn"):**
* `tight` (32px) — Fast pacing, action, and banter.
* `medium` (80px) — Ordinary beat, standard spacing.
* `large` (280px) — Major reveal, punchline, or scene/page change.

*Note: Assembly never cuts through a single panel. If a splash panel is taller than `slice_max_height` (2000px), that panel becomes its own dedicated image file.*

---

## ⚙️ Configuration

Project settings are stored in `chapter-01/config.yaml`. Default fallback values live in `scrollstrip/config.py`. 

```yaml
canvas_width: 1080
ingest:
  pdf_dpi: 300
  pdf_extract_embedded: true   # lift scanned pages out losslessly
working_max_side: 2800
slice_max_height: 2000
background: [18, 18, 18]
gutter_presets:
  tight: 32
  medium: 80
  large: 280
default_gutter: medium
clean:
  flatten_strength: 0.35       # 0 = leave scan lighting alone
  sharpen_amount: 0.28
  max_deskew_degrees: 6.0
  max_illum_side: 512          # lighting is estimated at this size, then scaled up
  crop_min_fill: 0.08          # column/row counts as content above this
  crop_min_area_kept: 0.30     # refuse crops that drop more than this much
detect:
  engine: auto
  conf: 0.25
  model_revision: null         # pin a commit sha for reproducible detection
  min_coverage: 0.35           # below this, the first pass is treated as failed
heuristics:
  reaction_scale: 0.78
```
> ⚠️ **Keep your `project.json` safe!** It acts as the ultimate source of truth for your layout and bounding boxes after you complete your review.

---

## 🧠 Under the Hood

### Where to start reviewing?
The `detect` command automatically prints out pages whose first pass looks doubtful and marks them as `needs_review` in your `project.json`. This heuristic is based on page coverage and box confidence, *not* box count—so a clean, genuine one-panel splash page won't be falsely flagged.

### Performance Note
The cleaning process downscales images to `working_max_side` before applying expensive filters. By estimating page lighting on a small copy rather than at full scan resolution, processing time drops drastically from ~128s per page to just **~1.6s per page**, with no visible loss in output quality.

### Cleaning Philosophy
The built-in cleaner is intentionally conservative. Its goal is to "make this scan even and square," not to act generatively and "redraw this panel."
* **Deskew** applies only when long panel-border lines confidently agree on a small tilt.
* **Lighting flatten** uses blending rather than a harsh divide-by-background method.
* **Sharpen** applies strictly to luminance, preventing color plates from fringing.
* **Working copies** are capped so the editor remains highly responsive, even when starting from 300+ DPI raw scans.

*If you prefer a heavier, AI-upscaled or generative cleanup look (e.g., Topaz, Magnific), run that on your problem pages externally and drop the results directly into `work/cleaned/` with the same filename. The detection and assembly pipeline will use them automatically.*

---

## 📂 Project Structure

A typical Scrollstrip project looks like this. **One project folder = one final CBZ.** Start a new folder for each chapter.

```text
chapter-01/
  ├── config.yaml
  ├── project.json
  ├── pages/                 # Original, untouched scans
  ├── work/
  │   ├── cleaned/           # Cleaned working copies
  │   └── previews/          # Annotated detect previews
  ├── models/
  │   └── best.pt            # Downloaded YOLO weights
  └── export/
      ├── slices/            # 001.jpg, 002.jpg ...
      └── Watchmen-01.cbz    # The final vertical scroll
```
