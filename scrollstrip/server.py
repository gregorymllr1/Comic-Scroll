from __future__ import annotations

import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .assemble import assemble_project
from .detect import detect_page, detections_to_panels, draw_preview
from .clean import _read, _write_jpeg
from .project import load_project, merged_config, save_project

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class Handler(SimpleHTTPRequestHandler):
    project_dir: Path

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def _json(self, payload, status=HTTPStatus.OK):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        return json.loads(body.decode("utf-8") or "{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/project":
            self._json(load_project(self.project_dir))
            return
        if parsed.path == "/api/config":
            self._json(merged_config(self.project_dir))
            return
        if parsed.path.startswith("/files/"):
            rel = parsed.path[len("/files/") :]
            path = (self.project_dir / rel).resolve()
            if self.project_dir.resolve() not in path.parents and path != self.project_dir.resolve():
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = path.read_bytes()
            ctype = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "application/octet-stream"
            if path.suffix.lower() == ".png":
                ctype = "image/png"
            if path.suffix.lower() == ".json":
                ctype = "application/json"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path in {"/", "/index.html"}:
            self.path = "/index.html"
        return super().do_GET()

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/project":
            data = self._read_json()
            save_project(self.project_dir, data)
            self._json({"ok": True})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/redetect":
            body = self._read_json()
            page_id = body.get("page_id")
            project = load_project(self.project_dir)
            cfg = merged_config(self.project_dir, project)
            page = next((p for p in project["pages"] if p["id"] == page_id), None)
            if not page:
                self._json({"error": "unknown page"}, HTTPStatus.BAD_REQUEST)
                return
            image = _read(self.project_dir / page["cleaned"])
            h, w = image.shape[:2]
            detections = detect_page(image, self.project_dir, cfg)
            page["panels"] = detections_to_panels(detections, w, h, cfg, page["id"])
            page["status"] = "detected"
            _write_jpeg(
                self.project_dir / page["preview"],
                draw_preview(image, page["panels"]),
                int(cfg.get("jpeg_quality", 92)),
            )
            save_project(self.project_dir, project)
            self._json(page)
            return
        if parsed.path == "/api/assemble":
            cfg = merged_config(self.project_dir)
            project = assemble_project(self.project_dir, cfg)
            self._json(project.get("export", {}))
            return
        self.send_error(HTTPStatus.NOT_FOUND)


def serve(project_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    Handler.project_dir = project_dir.resolve()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Review UI: http://{host}:{port}/")
    print("Edit boxes, scales, and gutters, then click Assemble chapter.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
