# Scrollstrip

First-pass pipeline that turns scanned comic pages - a folder of images, a CBZ, or a PDF - into a phone-width vertical CBZ. Detection is automatic (YOLO, with an OpenCV fallback). Cleanup, scale, gutters, and bad boxes are meant to be corrected by a person before export.

Use this only on comics you own, for personal reading.

## What it does

0. **Ingest** - read pages from a folder, a `.cbz`/`.zip`, or a `.pdf`. Scanned PDF pages are lifted out at native resolution rather than re-rendered; other PDFs rasterize at `pdf_dpi`.
1. **Clean** — crop scanner border, small deskew, even out lighting, light denoise, luminance sharpen. Colors stay close to the scan. Lettering is not redrawn.
2. **Detect** — YOLOv12 comic-panel model (`mosesb/best-comic-panel-detection`) plus a contour fallback. Writes boxes into `project.json`.
3. **Review** — local browser UI to move/resize/add/delete boxes, set scale and gutter, lock boxes, mark splash/spread/skip pages.
4. **Assemble** — scale each panel to 1080px (or your width), stack with gutters, slice between panels, zip one CBZ per chapter.

## Install

```bash
cd scrollstrip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`ultralytics` needs a working PyTorch. CPU is fine. The first `detect` run downloads `best.pt` (~119 MB) into `models/`.

If YOLO cannot load, use `--engine cv`.

## Chapter workflow

Put one chapter in one folder of scans, already in reading order (`001.jpg`, `002.jpg`, …).

```bash
python -m scrollstrip run \
  --project ./chapter-01 \
  --pages ./scans/chapter-01 \
  --name "Watchmen-01"

python -m scrollstrip.shell
# fix boxes, scales, gutters; click Save; click Assemble chapter

# or from the shell after review:
python -m scrollstrip assemble --project ./chapter-01
```

Output:

- `chapter-01/export/001.jpg`, `002.jpg`, …
- `chapter-01/export/Watchmen-01.cbz`
- optional `chapter-01/export/long-strip.jpg` if the chapter is not enormous

Open the CBZ in a reader with **webtoon / continuous vertical / fit-width** mode.

## Commands

| Command | Purpose |
|---|---|
| `init --pages SOURCE` | Create project from a folder, `.cbz`/`.zip`, or `.pdf` |
| `clean` | Write `work/cleaned/*.jpg` |
| `detect [--engine auto\|yolo\|cv]` | Fill `panels[]` on each page |
| `review` | Open the Scrollstrip desktop app |
| `assemble [--width 1080]` | Slices + CBZ |
| `run` | init (optional) + clean + detect |

`--link` on `init`/`run` records original paths instead of copying files. It applies
to folder sources only; archives and PDFs must be extracted, so `--link` is ignored
with a warning.

CBR and CB7 are not supported - convert to CBZ first. Entries that would escape the
project directory, plus `__MACOSX/`, `.DS_Store` and `Thumbs.db`, are skipped.

## Review UI (this is the design step)

Review happens in the Scrollstrip desktop app (`python -m scrollstrip.shell`), not a per-project browser server at `http://127.0.0.1:8765/`. Open a chapter from the library, then fix boxes, scales, and gutters before assembling.

Automatic boxes fail on the usual suspects: insets, overlaps, borderless panels, balloons in the gutter, SFX used as borders, spreads, full-bleed splash pages, yellowed paper, screentone, faint gutters.

For each bad page:

- Drag a box, drag the white corner to resize
- **Add box** for a missed panel
- **Delete** a false positive
- **Re-detect page** after you lock the boxes you already fixed
- Set **role**
  - `normal` — full content width
  - `reaction` — default scale 0.78 (staccato / small insert)
  - `splash` — full width, large gutter after
- Set **gutter after** (this is the only remaining “page turn”)
  - `tight` — 32px default, action and banter
  - `medium` — 80px default, ordinary beat
  - `large` — 280px default, reveal, punchline, scene or page change
- **Lock** so a later detect pass will not overwrite that box
- Page **kind**: `splash` / `spread` / `skip`

First-pass heuristics already guess some of this (tiny panels → reaction + tight gutter; huge/tall panels and last panel on a page → large gutter). They are starting points, not layout.

Assembly never cuts through a panel. If one splash is taller than `slice_max_height` (2000px), that panel becomes its own file.

## Config

Edit `chapter-01/config.yaml`. Unknown keys are ignored; defaults live in `scrollstrip/config.py`.

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
  flatten_strength: 0.35   # 0 = leave scan lighting alone
  sharpen_amount: 0.28
  max_deskew_degrees: 6.0
  max_illum_side: 512      # lighting is estimated at this size, then scaled up
  crop_min_fill: 0.08      # column/row counts as content above this
  crop_min_area_kept: 0.30 # refuse crops that drop more than this much
detect:
  engine: auto
  conf: 0.25
  model_revision: null     # pin a commit sha for reproducible detection
  min_coverage: 0.35       # below this, the first pass is treated as failed
heuristics:
  reaction_scale: 0.78
```

`project.json` is the source of truth for boxes after review. Keep it.

## Where to start reviewing

`detect` prints the pages whose first pass looks doubtful and marks them
`needs_review` in `project.json`. The test is page coverage and box confidence,
not box count, so a genuine one-panel splash is not flagged.

## Performance note

Cleaning downscales to `working_max_side` before the expensive filters, and
estimates page lighting on a small copy. Doing that work at full scan resolution
first - and then throwing 84% of the pixels away - cost about 128s per page
against 1.6s now, with no visible difference in the output.

## Cleaning philosophy

The cleaner is conservative on purpose. It is closer to “make this scan even and square” than to a generative “redraw this panel.”

- Deskew only when long panel-border lines agree on a small tilt
- Lighting flatten is blended, not a full divide-by-background
- Sharpen is luminance-only so color plates do not fringe
- Working copies are capped at `working_max_side` so the editor stays usable; always start from 300 dpi scans

If you later want a heavier “Grok/Claude cleanup” look, run that on individual problem pages and drop the results into `work/cleaned/` under the same filename. Detection and assembly will use them.

## Layout of a project

```
chapter-01/
  config.yaml
  project.json
  pages/                 original scans
  work/cleaned/          cleaned working copies
  work/previews/         annotated detect previews
  models/best.pt         downloaded YOLO weights
  export/slices/         001.jpg …
  export/*.cbz
```

One project folder = one CBZ. Start a new folder for the next chapter.
