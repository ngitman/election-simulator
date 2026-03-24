"""
Lightweight runtime logging for diagnosing memory pressure (especially on Render).

Uses /proc/self/status VmRSS on Linux when available; falls back to ru_maxrss peak only
otherwise. Does not add third-party dependencies.
"""
from __future__ import annotations

import logging
import os
import resource
import sys
from typing import Any

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    kwargs: dict[str, Any] = {
        "level": level,
        "format": _LOG_FORMAT,
    }
    if sys.version_info >= (3, 8):
        kwargs["force"] = True
    logging.basicConfig(**kwargs)


def current_rss_mb() -> float | None:
    """Best-effort current resident set size (MB). Linux /proc only."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    kb = int(line.split()[1])
                    return kb / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def peak_rss_mb() -> float | None:
    """Peak RSS from getrusage (mac: bytes, linux: KB)."""
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return float(ru) / (1024 * 1024)
        return float(ru) / 1024.0
    except (OSError, ValueError):
        return None


def log_stage(logger: logging.Logger, label: str, **extra: Any) -> None:
    parts = [f"stage={label}"]
    rss = current_rss_mb()
    peak = peak_rss_mb()
    if rss is not None:
        parts.append(f"rss_mb≈{rss:.1f}")
    if peak is not None:
        parts.append(f"peak_rss_mb≈{peak:.1f}")
    if extra:
        parts.append(" ".join(f"{k}={v!r}" for k, v in extra.items()))
    logger.info(" | ".join(parts))
