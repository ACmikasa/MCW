"""Runtime configuration loaded from environment and CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # Optional when environment variables are injected by the host.
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "uploads"
WEB_DIR = BASE_DIR / "web"

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def stream_name() -> str | None:
    return os.environ.get("LSL_STREAM_NAME") or None


def stream_type() -> str:
    return os.environ.get("LSL_STREAM_TYPE", "EEG")
