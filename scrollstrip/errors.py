"""Error types shared by the core and the app layer."""

from __future__ import annotations


class JobCancelled(Exception):
    """Raised inside a pipeline stage when the caller asked it to stop."""
