"""PyInstaller entry: import the package so relative imports in shell.py work."""

from __future__ import annotations

import multiprocessing
import os
import sys
import traceback
from pathlib import Path


def _ensure_stdio() -> None:
    """Windowed frozen apps set stdin/stdout/stderr to None; logging then crashes."""
    if sys.stdout is None or sys.stderr is None:
        sink = open(os.devnull, "w", encoding="utf-8", errors="replace")
        if sys.stdout is None:
            sys.stdout = sink
        if sys.stderr is None:
            sys.stderr = sink
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8", errors="replace")


def _crash_log() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return base / "scrollstrip-error.log"


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _ensure_stdio()
    try:
        from scrollstrip.shell import main

        main()
    except Exception:
        _crash_log().write_text(traceback.format_exc(), encoding="utf-8")
        raise
