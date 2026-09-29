"""Desktop entry point: serve the app and open it in a native window."""

from __future__ import annotations

import threading

import uvicorn

from .app.server import create_app

HOST, PORT = "127.0.0.1", 8765


def main() -> None:
    app = create_app()
    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        import webview
    except ImportError:
        print(f"pywebview is not installed. Open http://{HOST}:{PORT}/ in a browser.")
        thread.join()
        app.state.jobs.shutdown()
        return

    webview.create_window("Scrollstrip", f"http://{HOST}:{PORT}/", width=1400, height=900)
    try:
        webview.start()
    except Exception as exc:  # noqa: BLE001 - most often a missing WebView2 runtime
        print(f"Could not open a native window: {exc}")
        print("Install the Microsoft Edge WebView2 runtime, or open "
              f"http://{HOST}:{PORT}/ in a browser.")
    finally:
        app.state.jobs.shutdown()


if __name__ == "__main__":
    main()
