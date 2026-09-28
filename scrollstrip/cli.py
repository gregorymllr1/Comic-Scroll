from __future__ import annotations

import argparse
import json
from pathlib import Path

from .assemble import assemble_project
from .clean import clean_project
from .config import resolve_config
from .detect import detect_project
from .ingest import IngestError
from .project import init_project, load_project, merged_config


def _project_dir(args) -> Path:
    return Path(args.project).expanduser().resolve()


def cmd_init(args) -> None:
    data = init_project(
        _project_dir(args),
        pages_dir=Path(args.pages).expanduser().resolve() if args.pages else None,
        copy_pages=not args.link,
        name=args.name,
    )
    print(f"Initialized {len(data['pages'])} pages in {_project_dir(args)}")


def cmd_clean(args) -> None:
    project_dir = _project_dir(args)
    cfg = merged_config(project_dir)
    project = clean_project(project_dir, cfg)
    print(f"Cleaned {len(project['pages'])} pages")


def cmd_detect(args) -> None:
    project_dir = _project_dir(args)
    cfg = merged_config(project_dir)
    if args.engine:
        cfg.setdefault("detect", {})
        cfg["detect"]["engine"] = args.engine
    project = detect_project(project_dir, cfg, overwrite_unlocked=not args.keep_edits)
    counts = [len(p.get("panels") or []) for p in project["pages"]]
    print(f"Detected panels on {len(counts)} pages (min {min(counts) if counts else 0}, max {max(counts) if counts else 0})")
    print("Open the review UI before assembling:  python -m scrollstrip review --project", project_dir)


def cmd_assemble(args) -> None:
    project_dir = _project_dir(args)
    cfg = merged_config(project_dir)
    if args.width:
        cfg["canvas_width"] = int(args.width)
    project = assemble_project(project_dir, cfg)
    export = project.get("export", {})
    print(f"Wrote {export.get('slice_count', 0)} slices")
    print(f"CBZ: {project_dir / export.get('cbz', '')}")


def cmd_review(args) -> None:
    from .server import serve

    project_dir = _project_dir(args)
    load_project(project_dir)  # fail early
    serve(project_dir, host=args.host, port=args.port)


def cmd_run(args) -> None:
    project_dir = _project_dir(args)
    if args.pages:
        init_project(
            project_dir,
            pages_dir=Path(args.pages).expanduser().resolve(),
            copy_pages=not args.link,
            name=args.name,
        )
    cfg = merged_config(project_dir)
    if args.engine:
        cfg.setdefault("detect", {})
        cfg["detect"]["engine"] = args.engine
    clean_project(project_dir, cfg)
    detect_project(project_dir, cfg)
    print("First pass ready. Review boxes, then assemble:")
    print(f"  python -m scrollstrip review --project {project_dir}")
    print(f"  python -m scrollstrip assemble --project {project_dir}")
    if args.review:
        from .server import serve

        serve(project_dir, host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrollstrip",
        description="Clean comic scans (folder, CBZ, or PDF), detect panels, and assemble a vertical phone CBZ.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_project(p):
        p.add_argument("--project", default=".", help="Chapter project folder")
        return p

    p = add_project(sub.add_parser("init", help="Create a chapter project from a folder, CBZ, or PDF"))
    p.add_argument("--pages", required=True, metavar="SOURCE",
                   help="Folder of images, a .cbz/.zip, or a .pdf")
    p.add_argument("--name", default=None, help="CBZ / project name")
    p.add_argument("--link", action="store_true",
                   help="Record source paths instead of copying (folder sources only)")
    p.set_defaults(func=cmd_init)

    p = add_project(sub.add_parser("clean", help="Deskew, crop, even lighting, mild sharpen"))
    p.set_defaults(func=cmd_clean)

    p = add_project(sub.add_parser("detect", help="YOLO/CV panel detection first pass"))
    p.add_argument("--engine", choices=["auto", "yolo", "cv"], default=None)
    p.add_argument("--keep-edits", action="store_true", help="Do not replace unlocked boxes")
    p.set_defaults(func=cmd_detect)

    p = add_project(sub.add_parser("review", help="Open the local box-editing UI"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_review)

    p = add_project(sub.add_parser("assemble", help="Build 1080px slices and a chapter CBZ"))
    p.add_argument("--width", type=int, default=None, help="Override canvas width")
    p.set_defaults(func=cmd_assemble)

    p = add_project(sub.add_parser("run", help="init (optional) + clean + detect, then stop for review"))
    p.add_argument("--pages", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--link", action="store_true")
    p.add_argument("--engine", choices=["auto", "yolo", "cv"], default=None)
    p.add_argument("--review", action="store_true", help="Launch the review UI when detection finishes")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_run)

    p = add_project(sub.add_parser("status", help="Print project summary"))
    p.set_defaults(func=lambda a: print(json.dumps(load_project(_project_dir(a)), indent=2)[:4000]))

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (IngestError, FileNotFoundError) as exc:
        raise SystemExit(f"scrollstrip: {exc}")


if __name__ == "__main__":
    main()
